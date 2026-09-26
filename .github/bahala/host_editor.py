#!/usr/bin/env python3
"""Stage, smoke-test and package a GodotJS host editor for bahalafoundation/arcade (#169).

Called by .github/workflows/bahala-host-editors.yml, once per platform. Python rather than
bash/pwsh so the Linux and Windows jobs run the same code.

The staged layout mirrors arcade's ci/build-libgodot.nu (stage-linux-artefacts,
stage-windows-artefacts) exactly, because arcade's ci/vendor.nu unpacks this zip into a
directory and dev/editor.nu reads its stamp with the same code it uses for a local build:

    linuxbsd:  godot-editor
    windows:   godot-editor.exe, godot-editor.console.exe
    both:      .build-libgodot-pins.json   {platform, godot_vanilla_rev, godotjs_rev}
               NOTICE.txt, licenses/        (MIT: the notices travel with the binaries)

    stage   --platform P --godot DIR --out DIR     copy, assert QuickJS-NG, stamp, notices
    smoke   --platform P --stage DIR --project DIR  --version + GodotJS's own test project
    package --platform P --stage DIR --out DIR      zip it, print name and sha256
"""

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import zipfile

# name of the built binary under godot/bin -> name it is staged under. The console wrapper
# launches the executable named like itself minus `.console`, so it has to be staged under
# the matching name to find godot-editor.exe.
PLATFORMS = {
    "linuxbsd": {
        "host": "linux-x86_64",
        "bins": {"godot.linuxbsd.editor.x86_64": "godot-editor"},
        # The one carrying the engine, which the backend check reads.
        "engine": "godot-editor",
        # The one to run from a terminal/CI: prints to stdout, returns the exit code.
        "run": "godot-editor",
    },
    "windows": {
        "host": "windows-x86_64",
        "bins": {
            "godot.windows.editor.x86_64.exe": "godot-editor.exe",
            "godot.windows.editor.x86_64.console.exe": "godot-editor.console.exe",
        },
        # The console wrapper is a small launcher with no engine in it, so only the .exe
        # is checked, as arcade's stage-windows-artefacts does.
        "engine": "godot-editor.exe",
        "run": "godot-editor.console.exe",
    },
}

STAMP = ".build-libgodot-pins.json"


def env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        sys.exit(f"{name} is not set; the workflow passes it")
    return value


def stamp_json(platform: str) -> str:
    # Byte-for-byte what arcade's nushell `{...} | to json` writes: two-space indent, keys in
    # this order, no trailing newline.
    return json.dumps(
        {
            "platform": platform,
            "godot_vanilla_rev": env("GODOT_VANILLA_REV"),
            "godotjs_rev": env("GODOTJS_REV"),
        },
        indent=2,
    )


def assert_quickjs_only(binary: pathlib.Path) -> None:
    """arcade's assert-quickjs-only(-windows): the QuickJS-NG backend's source paths are
    baked into the binary (__FILE__), V8's and JavaScriptCore's are not. MSVC writes
    backslashed paths, hence both spellings."""
    data = binary.read_bytes()
    if b"impl/quickjs" not in data and b"impl\\quickjs" not in data:
        sys.exit(f"{binary}: no QuickJS-NG sources baked in; did use_quickjs_ng=yes reach scons?")
    for marker in (b"v8_monolith", b"v8::internal", b"impl/jsc/", b"impl\\jsc"):
        if marker in data:
            sys.exit(f"{binary}: links a JavaScript engine other than QuickJS-NG (found {marker!r})")
    print(f"{binary.name}: QuickJS-NG backend, no other JavaScript engine")


def git_rev(path: pathlib.Path) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def stage(args: argparse.Namespace) -> None:
    spec = PLATFORMS[args.platform]
    godot = pathlib.Path(args.godot)
    module = godot / "modules" / "godotjs"
    out = pathlib.Path(args.out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)

    for built, staged in spec["bins"].items():
        src = godot / "bin" / built
        if not src.exists():
            sys.exit(f"{src} was not built; scons' output naming changed?")
        shutil.copy2(src, out / staged)
    assert_quickjs_only(out / spec["engine"])

    (out / STAMP).write_text(stamp_json(args.platform), encoding="utf-8", newline="\n")

    licenses = out / "licenses"
    licenses.mkdir()
    shutil.copy2(godot / "LICENSE.txt", licenses / "godot-LICENSE.txt")
    shutil.copy2(godot / "COPYRIGHT.txt", licenses / "godot-COPYRIGHT.txt")
    shutil.copy2(module / "LICENSE", licenses / "godotjs-LICENSE.txt")
    shutil.copy2(module / "quickjs-ng" / "LICENSE", licenses / "quickjs-ng-LICENSE.txt")
    quickjs_rev = git_rev(module / "quickjs-ng")
    (out / "NOTICE.txt").write_text(
        NOTICE.format(
            host=spec["host"],
            godot_rev=env("GODOT_VANILLA_REV"),
            godotjs_rev=env("GODOTJS_REV"),
            quickjs_rev=quickjs_rev,
            run_url=env("RUN_URL"),
        ),
        encoding="utf-8",
        newline="\n",
    )

    print(f"staged {args.platform} host editor under {out}:")
    for f in sorted(out.rglob("*")):
        print(f"  {f.relative_to(out)}")
    print(
        "NOT the engine that ships: vanilla Godot + QuickJS-NG, not the bahalafoundation/libgodot "
        "fork's JavaScriptCore build. It never exports a shipping pack."
    )


def run(label: str, cmd: list, cwd: pathlib.Path, timeout: int) -> str:
    print(f"==> {label}: {' '.join(str(c) for c in cmd)}", flush=True)
    result = subprocess.run(
        [str(c) for c in cmd],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    output = result.stdout + result.stderr
    print(output, flush=True)
    if result.returncode != 0:
        sys.exit(f"{label} exited {result.returncode}")
    return output


def smoke(args: argparse.Namespace) -> None:
    spec = PLATFORMS[args.platform]
    editor = pathlib.Path(args.stage).resolve() / spec["run"]
    project = pathlib.Path(args.project).resolve()
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        sys.exit("pnpm not on PATH")

    # 1. It is the Godot we pinned: `4.5.1.stable.custom_build.<first 9 of the commit>`.
    version = run("version", [editor, "--version"], project, 120).strip().splitlines()[-1]
    expected = env("GODOT_VANILLA_REV")[:9]
    if not version.endswith(expected):
        sys.exit(f"--version printed {version!r}, expected a build of {expected}")

    # 2. GodotJS's own integration project (tests/project), as upstream's runtime matrix
    #    runs it for host-qjs: generate typings in the editor, compile, run headless until
    #    the project prints its completion sentinel. It exercises the module end to end on
    #    QuickJS-NG (scripted resources, singletons, class extension, workers).
    #    `--outDir .godot/godotjs`: GodotJS loads compiled JS from `.godot/<module dir>`,
    #    and this module is built as modules/godotjs (arcade's layout, which this editor
    #    must match), not modules/GodotJS as upstream's CI and the project's tsconfig assume.
    out_dir = ".godot/godotjs"
    run("pnpm install", [pnpm, "install", "--ignore-workspace", "--frozen-lockfile"], project, 600)
    run("tsc (emit only)", [pnpm, "exec", "tsc", "--noCheck", "--outDir", out_dir], project, 600)
    run("generate types", [editor, "--headless", "--editor", "--generate-types", "--path", "."], project, 600)
    run("tsc", [pnpm, "exec", "tsc", "--outDir", out_dir], project, 600)
    output = run("run tests/project", [editor, "--audio-driver", "Dummy", "--headless", "--path", "."], project, 600)
    if "GODOTJS_TEST_PROJECT_COMPLETED" not in output:
        sys.exit("tests/project never printed GODOTJS_TEST_PROJECT_COMPLETED")
    for bad in ("GODOTJS_TEST_PROJECT_FAILED:", "exception thrown in function:", "Uncaught Error:"):
        if bad in output:
            sys.exit(f"tests/project output contains {bad!r}")
    print(f"smoke test passed: {version}, GodotJS tests/project completed")


def package(args: argparse.Namespace) -> None:
    spec = PLATFORMS[args.platform]
    src = pathlib.Path(args.stage)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = f"godot-editor-{spec['host']}"
    zip_path = out / f"{name}.zip"
    executables = set(spec["bins"].values())
    # Contents at the zip's root, no wrapping directory: arcade's ci/vendor.nu unpacks it
    # into Vendor/<name>/. Unix mode bits are recorded, though vendor.nu reapplies +x anyway.
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(src.rglob("*")):
            if f.is_dir():
                continue
            rel = f.relative_to(src).as_posix()
            info = zipfile.ZipInfo.from_file(f, rel)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if rel in executables else 0o100644) << 16
            with open(f, "rb") as fh:
                z.writestr(info, fh.read(), compresslevel=9)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (out / f"{name}.zip.sha256").write_text(f"{digest}  {name}.zip\n", encoding="utf-8", newline="\n")
    (out / f"{name}.stamp.json").write_text(stamp_json(args.platform), encoding="utf-8", newline="\n")
    print(f"{zip_path.name}  {digest}  {zip_path.stat().st_size} bytes")


NOTICE = """GodotJS host editor ({host}) for bahalafoundation/arcade: copyright and licence notices
=====================================================================================

This archive holds a build of open-source software, published by Bahala from its
fork of GodotJS. Every component below is MIT-licensed, which permits redistribution
in binary form but REQUIRES that this notice and the licence texts in licenses/
travel with the binaries.

It is a HOST EDITOR for developing Arcade's Godot projects on Linux and Windows:
vanilla Godot plus GodotJS on the QuickJS-NG backend. It is NOT the engine Arcade
ships (that is the bahalafoundation/libgodot fork on JavaScriptCore), and it never exports a
pack that ships.

Components, at the exact revisions built
  Godot Engine   https://github.com/godotengine/godot
                 {godot_rev}  (4.5.1-stable)
                 MIT, see licenses/godot-LICENSE.txt; third-party components
                 bundled by Godot itself: licenses/godot-COPYRIGHT.txt
  GodotJS        https://github.com/bahalafoundation/GodotJS  (branch `bahala`)
                 {godotjs_rev}
                 MIT, see licenses/godotjs-LICENSE.txt
  QuickJS-NG     https://github.com/quickjs-ng/quickjs
                 {quickjs_rev}
                 MIT, see licenses/quickjs-ng-LICENSE.txt

Built by {run_url}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("stage")
    p.add_argument("--platform", choices=PLATFORMS, required=True)
    p.add_argument("--godot", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=stage)
    p = sub.add_parser("smoke")
    p.add_argument("--platform", choices=PLATFORMS, required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--project", required=True)
    p.set_defaults(func=smoke)
    p = sub.add_parser("package")
    p.add_argument("--platform", choices=PLATFORMS, required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=package)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
