#!/usr/bin/env python3
"""The engine bahalafoundation/arcade embeds, and its host editors: the one recipe.

Both release workflows run it (bahala-libgodot.yml: the apple and android parts;
bahala-host-editors.yml: the host part), and so does arcade's `just engine-build`, from the
commit a release's tag points at (bahalafoundation/arcade#436). The pins below are the whole
of what is built: arcade pins a release, by tag and SHA-256, and nothing else.

    plan    --release libgodot|host-editors
        The release this checkout builds, as KEY=VALUE lines: tag, godotjs_rev, the pins.
    source  --part P --tree DIR
        The engine tree at its pin (cloned when absent, moved to the pin when not), with
        this checkout linked in as modules/godotjs.
    build   --part P --tree DIR --stage DIR --out DIR [--symbols DIR] [--jobs N]
            [--scons-cache DIR]
        source, then scons (and gradle), then stage, check and package. --stage gets the
        artifacts unpacked, --out exactly the files the release publishes for this part.
        --symbols builds with debug_symbols=yes and sorts the DWARF into DIR.
    smoke   --stage DIR --project DIR
        The staged host editor's --version, and GodotJS's tests/project run on it.
    symbols --symbols DIR --out DIR
        The apple and android parts' sorted symbols, as libgodot-symbols.zip.

Parts:
    apple    macOS: the iOS xcframework (device and arm64 Simulator slices) and the macOS
             arm64 editor, from the libgodot fork, GodotJS on JavaScriptCore
    android  macOS: the Android .aar, from the same tree, GodotJS on QuickJS-NG
    host     Linux or Windows: this OS's editor, vanilla Godot plus GodotJS on QuickJS-NG.
             Not the engine that ships: it runs arcade's suites and never exports a pack.

Needs git, scons, node and pnpm on PATH; the apple and android parts need Xcode, and the
android part a JDK 17 and an Android SDK with config.gradle's versions (ANDROID_HOME).
Python 3.9 or later, standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

# bahalafoundation/libgodot (arcade#171). Its `godot` gitlink pins bahalafoundation/godot,
# whose `bahala` carries arcade's Godot fixes as commits, so this pins Godot too.
LIBGODOT_REPO = "https://github.com/bahalafoundation/libgodot.git"
LIBGODOT_REV = "01a14a06a9356f50e08838051f56df9cb70cfd54"

# Godot 4.5.1-stable, for the host editors: the libgodot fork does not build for linuxbsd.
GODOT_VANILLA_REPO = "https://github.com/godotengine/godot.git"
GODOT_VANILLA_REV = "f62fdbde15035c5576dad93e586201f4d41ef0cb"

# Each release tag ends in its recipe version, `-r<N>`. Bump it in the commit that changes
# what that release holds without moving a pin or the module commit, or the plan finds the
# old release and builds nothing. A release's tag is also where arcade reads this script.
#   libgodot      1 arcade#170 (no suffix)  2 arcade#177 Simulator slice  3 arcade#171
#                 libgodot fork  4 arcade#261 debug symbols  5 arcade#436 this script
#   host editors  1 arcade#169 (no suffix)  2 arcade#436 this script
LIBGODOT_RECIPE = 5
HOST_EDITORS_RECIPE = 2

# Symbolicator's symsorter, from the release arcade's crash service runs (arcade#260), so
# the layout it writes is the one that service reads.
SYMSORTER_VERSION = "26.9.0"
SYMSORTER_SHA256 = "8d7c591ac1894fbe65f2b22098068b7dedf85b7d39a982ddf58c725c5149486b"

# This checkout. Godot names a module after its directory, and GodotJS the directory it
# loads compiled JavaScript from after that, so it must be called `godotjs`.
GODOTJS = pathlib.Path(__file__).resolve().parents[2]

XCFRAMEWORK_SLICES = ["ios-arm64", "ios-arm64-simulator"]
STAMP = ".build-libgodot-pins.json"
MACOS_EDITOR = "godot-editor-macos-arm64"
AAR = "godot-lib.template_debug.aar"

# Built binary under godot/bin -> staged name. Windows' console wrapper launches the
# executable named like itself minus `.console`, so it is staged under the matching name.
HOSTS = {
    "linuxbsd": {
        "host": "linux-x86_64",
        "bins": {"godot.linuxbsd.editor.x86_64": "godot-editor"},
        "engine": "godot-editor",
        "run": "godot-editor",
    },
    "windows": {
        "host": "windows-x86_64",
        "bins": {
            "godot.windows.editor.x86_64.exe": "godot-editor.exe",
            "godot.windows.editor.x86_64.console.exe": "godot-editor.console.exe",
        },
        # The console wrapper holds no engine, so only the .exe is checked.
        "engine": "godot-editor.exe",
        "run": "godot-editor.console.exe",
    },
}


def fail(message: str) -> None:
    sys.exit(f"build.py: {message}")


def run(cmd: list, cwd: pathlib.Path | None = None, env: dict | None = None) -> None:
    print(f"==> {' '.join(str(c) for c in cmd)}", flush=True)
    result = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env)
    if result.returncode != 0:
        fail(f"{cmd[0]} exited {result.returncode}")


def output(cmd: list, cwd: pathlib.Path | None = None, check: bool = True) -> str:
    result = subprocess.run(
        [str(c) for c in cmd], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and result.returncode != 0:
        fail(f"{' '.join(str(c) for c in cmd)} exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        fail(f"{name} is not on PATH")
    return found


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_text(path: pathlib.Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


# ------------------------------------------------------------------------------- plan ---


def godotjs_rev() -> str:
    """The module commit: the last one outside .github/, which a commit touching only CI
    files leaves alone, so it maps to the release that already exists."""
    if output(["git", "-C", GODOTJS, "rev-parse", "--is-shallow-repository"]) == "true":
        fail(f"{GODOTJS} is a shallow clone; the module commit needs its history (fetch-depth: 0)")
    rev = output(["git", "-C", GODOTJS, "log", "-1", "--format=%H", "HEAD", "--", ".", ":(exclude).github"])
    same = subprocess.run(["git", "-C", str(GODOTJS), "diff", "--quiet", rev, "HEAD", "--", ".", ":(exclude).github"])
    if same.returncode != 0:
        fail(f"HEAD differs from {rev} outside .github/")
    return rev


def release_tag(release: str, rev: str) -> str:
    if release == "libgodot":
        return f"libgodot-{LIBGODOT_REV[:7]}-godotjs-{rev[:7]}-r{LIBGODOT_RECIPE}"
    return f"editor-godot-{GODOT_VANILLA_REV[:7]}-godotjs-{rev[:7]}-r{HOST_EDITORS_RECIPE}"


def plan(args: argparse.Namespace) -> None:
    rev = godotjs_rev()
    lines = {"tag": release_tag(args.release, rev), "godotjs_rev": rev}
    if args.release == "libgodot":
        lines.update(libgodot_rev=LIBGODOT_REV, recipe=LIBGODOT_RECIPE, symsorter_version=SYMSORTER_VERSION)
    else:
        lines.update(godot_vanilla_rev=GODOT_VANILLA_REV, recipe=HOST_EDITORS_RECIPE)
    for key, value in lines.items():
        print(f"{key}={value}")


# ----------------------------------------------------------------------------- source ---


def head(tree: pathlib.Path) -> str:
    return output(["git", "-C", tree, "rev-parse", "--verify", "-q", "HEAD"], check=False)


def ensure_libgodot(tree: pathlib.Path) -> pathlib.Path:
    """The libgodot fork at LIBGODOT_REV with every submodule; returns its godot/."""
    if not (tree / ".git").exists():
        run(["git", "clone", "-q", LIBGODOT_REPO, tree])
    if head(tree) != LIBGODOT_REV:
        if subprocess.run(["git", "-C", str(tree), "cat-file", "-e", f"{LIBGODOT_REV}^{{commit}}"]).returncode != 0:
            run(["git", "-C", tree, "fetch", "-q", "origin"])
        run(["git", "-C", tree, "checkout", "-q", LIBGODOT_REV])
    run(["git", "-C", tree, "submodule", "update", "--init", "--recursive"])
    return tree / "godot"


def ensure_vanilla_godot(tree: pathlib.Path) -> pathlib.Path:
    """Vanilla Godot at GODOT_VANILLA_REV, that commit only; the tree is godot/ itself."""
    if not (tree / ".git").exists():
        tree.mkdir(parents=True, exist_ok=True)
        run(["git", "-C", tree, "init", "-q"])
        run(["git", "-C", tree, "remote", "add", "origin", GODOT_VANILLA_REPO])
    if head(tree) != GODOT_VANILLA_REV:
        run(["git", "-C", tree, "fetch", "-q", "--depth", "1", "origin", GODOT_VANILLA_REV])
        run(["git", "-C", tree, "checkout", "-q", GODOT_VANILLA_REV])
    return tree


def is_link(path: pathlib.Path) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    return path.is_symlink() or (isjunction is not None and isjunction(path))


def link_module(godot: pathlib.Path) -> None:
    """modules/godotjs is this checkout: itself, or a link to it (a junction on Windows,
    which needs no privilege). The build writes its objects into the checkout."""
    if GODOTJS.name != "godotjs":
        fail(f"{GODOTJS}: check GodotJS out into a directory named godotjs; Godot names the module after it")
    module = godot / "modules" / "godotjs"
    if is_link(module):
        if module.resolve() == GODOTJS:
            return
        os.rmdir(module) if os.name == "nt" else module.unlink()
    elif module.exists():
        if module.resolve() == GODOTJS:
            return
        fail(f"{module} is a directory, not {GODOTJS}: remove it, and this links the checkout in its place")
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(GODOTJS), str(module))
    else:
        module.symlink_to(GODOTJS, target_is_directory=True)
    print(f"linked {module} -> {GODOTJS}", flush=True)


def source(part: str, tree: pathlib.Path) -> pathlib.Path:
    """The engine tree for PART with the module in place; returns its godot/ directory."""
    tree = tree.resolve()
    if part == "host":
        godot = ensure_vanilla_godot(tree)
    else:
        godot = ensure_libgodot(tree)
        # Every scons run in the tree reads custom.py, the two inside build_libgodot.sh
        # included: GodotJS on JavaScriptCore, the system framework on Apple platforms
        # (arcade#44). The android part's use_quickjs_ng=yes overrides it.
        write_text(godot / "custom.py", '# Written by .github/bahala/build.py - GodotJS on the JavaScriptCore backend (#44).\nuse_jsc = "yes"\n')
    link_module(godot)
    return godot


def source_command(args: argparse.Namespace) -> None:
    print(source(args.part, pathlib.Path(args.tree)))


# ------------------------------------------------------------------------- the checks ---

# A backend's own source paths survive in the binary (__FILE__), in either slash; the
# others' names wherever they are linked in. The backend is a silent scons option, so a lost
# custom.py or flag would fall back to another engine unnoticed (arcade#44).
BACKENDS = {
    "jsc": ([b"impl/jsc/"], [b"quickjs", b"v8_monolith", b"v8::internal"]),
    "quickjs": ([b"impl/quickjs", b"impl\\quickjs"], [b"v8_monolith", b"v8::internal", b"impl/jsc/", b"impl\\jsc"]),
}
BACKEND_NAMES = {"jsc": "JavaScriptCore", "quickjs": "QuickJS-NG"}


def assert_backend(binary: pathlib.Path, backend: str) -> None:
    """BINARY carries GodotJS on BACKEND and no other JavaScript engine."""
    data = binary.read_bytes()
    present, foreign = BACKENDS[backend]
    name = BACKEND_NAMES[backend]
    if not any(marker in data for marker in present):
        fail(f"{binary}: not built with GodotJS's {name} backend")
    data = data.lower()
    for marker in foreign:
        if marker in data:
            fail(f"{binary}: links a JavaScript engine other than {name} (found {marker.decode()!r})")
    print(f"{binary.name}: {name} backend, no other JavaScript engine", flush=True)


class Elf:
    """The little a 64-bit little-endian ELF needs read for the android checks."""

    def __init__(self, path: pathlib.Path):
        self.data = path.read_bytes()
        d = self.data
        if d[:4] != b"\x7fELF" or d[4] != 2 or d[5] != 1:
            fail(f"{path}: not a 64-bit little-endian ELF")
        (self.phoff, self.shoff) = struct.unpack_from("<QQ", d, 0x20)
        (self.phentsize, self.phnum, self.shentsize, self.shnum, self.shstrndx) = struct.unpack_from("<HHHHH", d, 0x36)

    def segments(self):
        for i in range(self.phnum):
            p_type, _, p_offset, _, _, p_filesz, _, p_align = struct.unpack_from("<IIQQQQQQ", self.data, self.phoff + i * self.phentsize)
            yield p_type, p_offset, p_filesz, p_align

    def section_names(self) -> list:
        headers = [struct.unpack_from("<IIQQQQIIQQ", self.data, self.shoff + i * self.shentsize) for i in range(self.shnum)]
        strtab = headers[self.shstrndx][4]
        names = []
        for h in headers:
            start = strtab + h[0]
            names.append(self.data[start : self.data.index(b"\0", start)].decode())
        return names

    def load_alignments(self) -> set:
        return {align for p_type, _, _, align in self.segments() if p_type == 1}

    def build_id(self) -> str:
        for p_type, offset, size, _ in self.segments():
            if p_type != 4:  # PT_NOTE
                continue
            pos = offset
            while pos < offset + size:
                namesz, descsz, n_type = struct.unpack_from("<III", self.data, pos)
                name = self.data[pos + 12 : pos + 12 + namesz]
                desc = pos + 12 + ((namesz + 3) & ~3)
                if n_type == 3 and name == b"GNU\0":  # NT_GNU_BUILD_ID
                    return self.data[desc : desc + descsz].hex()
                pos = desc + ((descsz + 3) & ~3)
        return ""


def assert_16kb_aligned(so: pathlib.Path) -> None:
    """Play requires 16 KB pages of native code; NDK 28 aligns so by default."""
    aligns = Elf(so).load_alignments()
    if aligns != {0x4000}:
        fail(f"{so.name}: LOAD segments align to {sorted(hex(a) for a in aligns)}, not 0x4000 (16 KB)")
    print(f"{so.name}: all LOAD segments 16 KB (0x4000) aligned", flush=True)


def assert_render_view_lookup(so: pathlib.Path) -> None:
    """bahalafoundation/godot's getRenderView JNI lookup (arcade#72): without it
    get_godot_view() is null and the engine SIGSEGVs on its first frame on every device."""
    if b"getRenderView" not in so.read_bytes():
        fail(f"{so.name}: no getRenderView JNI lookup (arcade#72); is LIBGODOT_REV's godot gitlink a commit with the fix?")
    print(f"{so.name}: getRenderView JNI lookup present", flush=True)


def godot_version(editor: pathlib.Path, cwd: pathlib.Path | None = None) -> str:
    out = output([editor, "--version"], cwd=cwd)
    return out.splitlines()[-1].strip() if out else ""


def assert_godot_version(editor: pathlib.Path, godot_rev: str) -> str:
    """It is the Godot pinned: `4.5.1.stable.custom_build.<first 9 of the commit>`."""
    version = godot_version(editor)
    want = f"4.5.1.stable.custom_build.{godot_rev[:9]}"
    if version != want:
        fail(f"{editor} --version printed {version!r}, expected {want!r}")
    print(f"--version: {version}", flush=True)
    return version


# --------------------------------------------------------------------- staging, notices ---


def run_url() -> str:
    if os.environ.get("GITHUB_RUN_ID"):
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        return f"{server}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    return "a local build, not a published release"


def stamp_json(fields: dict) -> str:
    # Byte for byte what arcade's nushell `{...} | to json` writes: two-space indent, keys
    # in this order, no trailing newline.
    return json.dumps(fields, indent=2)


def copy_licenses(godot: pathlib.Path, dest: pathlib.Path) -> str:
    """The licence texts MIT requires beside the binaries; returns QuickJS-NG's commit."""
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(godot / "LICENSE.txt", dest / "godot-LICENSE.txt")
    shutil.copy2(godot / "COPYRIGHT.txt", dest / "godot-COPYRIGHT.txt")
    shutil.copy2(GODOTJS / "LICENSE", dest / "godotjs-LICENSE.txt")
    shutil.copy2(GODOTJS / "quickjs-ng" / "LICENSE", dest / "quickjs-ng-LICENSE.txt")
    return output(["git", "-C", GODOTJS / "quickjs-ng", "rev-parse", "HEAD"])


LIBGODOT_NOTICE = """Arcade engine artifacts (bahalafoundation/arcade): copyright and licence notices
===============================================================================

This release holds builds of open-source software, published by Bahala from its
fork of GodotJS. Every component below is MIT-licensed, which permits
redistribution in binary form but REQUIRES that this notice and the licence texts
beside it travel with the binaries. Nothing proprietary to Bahala is in them.

Artifacts this notice covers
  libgodot-xcframework       the embeddable engine for iOS (GodotJS on JavaScriptCore),
                             device and Simulator slices
  godot-editor-macos-arm64   the macOS arm64 host editor from the same build
  godot-lib-android-aar      the embeddable engine for Android (GodotJS on QuickJS-NG)
  libgodot-symbols           the debug symbols of the two embeddable engines above

Components, at the exact revisions built
  Godot Engine   https://github.com/bahalafoundation/godot  (branch `bahala`;
                 fork of https://github.com/migeran/godot, carrying two Android
                 fixes as commits — arcade#171)
                 {godot_rev}
                 (the godot submodule of
                 https://github.com/bahalafoundation/libgodot  (branch `bahala`;
                 fork of https://github.com/migeran/libgodot, which carries build
                 scripts only) at {libgodot_rev})
                 MIT, see godot-LICENSE.txt; third-party components bundled by
                 Godot itself: godot-COPYRIGHT.txt
  GodotJS        https://github.com/bahalafoundation/GodotJS  (branch `bahala`)
                 {godotjs_rev}
                 MIT, see godotjs-LICENSE.txt
  QuickJS-NG     https://github.com/quickjs-ng/quickjs  (in the Android .aar only)
                 {quickjs_rev}
                 MIT, see quickjs-ng-LICENSE.txt

JavaScriptCore, GodotJS's engine on iOS and macOS, is linked as a system framework;
no part of it is redistributed here.

Built by {run_url}
"""

HOST_NOTICE = """GodotJS host editor ({host}) for bahalafoundation/arcade: copyright and licence notices
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


def ditto_zip(src: pathlib.Path, zip_path: pathlib.Path) -> None:
    """A path's CONTENTS at the zip root (a file's own name for a file), the layout arcade's
    ci/vendor.nu unpacks."""
    zip_path.unlink(missing_ok=True)
    run(["ditto", "-c", "-k", "--sequesterRsrc", src, zip_path])


def write_sha256(zip_path: pathlib.Path) -> None:
    line = f"{sha256(zip_path)}  {zip_path.name}\n"
    write_text(zip_path.with_name(zip_path.name + ".sha256"), line)
    print(line, end="", flush=True)


def fresh(path: pathlib.Path) -> pathlib.Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


# -------------------------------------------------------------------------- symbols ---


def symsorter() -> pathlib.Path:
    path = pathlib.Path(tempfile.mkdtemp()) / "symsorter"
    url = f"https://github.com/getsentry/symbolicator/releases/download/{SYMSORTER_VERSION}/symsorter-Darwin-universal"
    run(["curl", "-fsSL", "-o", path, url])
    if sha256(path) != SYMSORTER_SHA256:
        fail(f"symsorter {SYMSORTER_VERSION}: SHA-256 {sha256(path)}, expected {SYMSORTER_SHA256}")
    path.chmod(0o755)
    return path


def sort_symbols(part: str, symbols_in: pathlib.Path, ids: list, symbols: pathlib.Path) -> None:
    """IDS ((id, what, file) per binary that ships) sorted by `symsorter -z` into Symbolicator's
    unified/<id[0:2]>/<id[2:]>/{debuginfo,executable} under SYMBOLS, with <part>.ids. Every
    id must come out of it with an entry, and nothing else may."""
    sorted_dir = pathlib.Path(tempfile.mkdtemp())
    run([symsorter(), "-z", "-p", "unified", "-o", sorted_dir, symbols_in])
    for symbol_id, what, name in ids:
        entry = sorted_dir / "unified" / symbol_id[:2] / symbol_id[2:]
        if not ((entry / "debuginfo").is_file() or (entry / "executable").is_file()):
            fail(f"nothing sorted for {what}'s {name} ({symbol_id})")
        print(f"{what} {name}: {symbol_id}", flush=True)
    files = [f for f in (sorted_dir / "unified").rglob("*") if f.is_file() and f.suffix != ".meta"]
    if len(files) != len(ids):
        fail(f"{len(files)} files sorted for {len(ids)} ids")
    for f in files:
        print(f"{f.stat().st_size} bytes  {f.relative_to(sorted_dir)}", flush=True)
    symbols.mkdir(parents=True, exist_ok=True)
    shutil.copytree(sorted_dir / "unified", symbols / "unified", dirs_exist_ok=True)
    write_text(symbols / f"{part}.ids", "".join(f"{i} {what} {name}\n" for i, what, name in ids))
    shutil.rmtree(sorted_dir)
    shutil.rmtree(symbols_in)


def symbols_command(args: argparse.Namespace) -> None:
    """Both parts' sorted symbols as one asset, stored (every file in it is already zstd)."""
    symbols = pathlib.Path(args.symbols)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ids = "".join((symbols / f"{part}.ids").read_text(encoding="utf-8") for part in ("apple", "android"))
    if len(ids.splitlines()) != 3:
        fail(f"expected 3 symbol ids (two iOS slices, one Android .so), have:\n{ids}")
    zip_path = out / "libgodot-symbols.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
        for f in sorted((symbols / "unified").rglob("*")):
            z.write(f, f.relative_to(symbols).as_posix())
    write_text(out / "libgodot-symbols.ids", ids)
    size = zip_path.stat().st_size
    # GitHub refuses a release asset of 2 GiB or more.
    if size >= 2 << 30:
        fail(f"libgodot-symbols.zip is {size} bytes, over GitHub's 2 GiB asset limit")
    print(ids, end="")
    write_sha256(zip_path)
    print(f"libgodot-symbols.zip: {size} bytes", flush=True)


# ---------------------------------------------------------------------------- apple ---


def build_apple(tree: pathlib.Path, godot: pathlib.Path, jobs: int, symbols_in: pathlib.Path | None) -> pathlib.Path:
    """The macOS editor, then the iOS library for device and Simulator, wrapped as one
    xcframework without its dSYMs; returns the xcframework."""
    env = dict(os.environ)
    # xcodebuild layers this over every target it builds, the one hook onto the framework
    # wrapper's link line: JavaScriptCore, and an arm64-only Simulator slice (arcade#177),
    # or Xcode adds an empty x86_64 stub and names it ios-arm64_x86_64-simulator.
    xcconfig = tree / "godotjs.xcconfig"
    write_text(
        xcconfig,
        "// .github/bahala/build.py - GodotJS JavaScriptCore backend (#44).\n"
        "OTHER_LDFLAGS = $(inherited) -framework JavaScriptCore\n"
        "// #177: the Simulator slice is arm64 only.\n"
        "ARCHS[sdk=iphonesimulator*] = arm64\n",
    )
    env["XCODE_XCCONFIG_FILE"] = str(xcconfig)

    # Metal, not the default Vulkan build, which needs the Vulkan SDK. build_libgodot.sh
    # then finds this editor and skips its own host build.
    run([tool("scons"), "p=macos", "target=editor", "dev_build=yes", "vulkan=no", "metal=yes", f"-j{jobs}"], cwd=godot, env=env)

    # Without --update-api, which would force the default host rebuild: build_libgodot.sh
    # dumps extension_api.json from the editor above instead. Apple's tools first on PATH:
    # these scripts assume macOS's BSD userland. The second pass is the Simulator's
    # library, which build_libgodot_xcframework.sh wraps as ios-arm64-simulator.
    env["PATH"] = os.pathsep.join(["/usr/bin", "/bin", "/usr/sbin", "/sbin", env.get("PATH", "")])
    if symbols_in is not None:
        # SCONSFLAGS is the only way in: build_libgodot.sh passes no flags through, and
        # SConstruct reads debug_symbols from the command line only.
        env["SCONSFLAGS"] = " ".join(filter(None, [env.get("SCONSFLAGS"), "debug_symbols=yes"]))
    run([tree / "build_libgodot.sh", "--target", "ios"], cwd=tree, env=env)
    run([tree / "build_libgodot.sh", "--target", "ios", "--simulator"], cwd=tree, env=env)
    run([tree / "build_libgodot_xcframework.sh", "--target", "template_debug"], cwd=tree, env=env)

    xcf = tree / "build" / "libgodot" / "debug" / "libgodot.xcframework"
    sim = xcf / "ios-arm64-simulator" / "libgodot.framework"
    if not sim.is_dir():
        fail(f"{xcf} has no ios-arm64-simulator slice: found {sorted(p.name for p in xcf.iterdir())}")
    # The dSYMs leave the xcframework (for the symbols, or for good), and `strip -S` drops
    # each binary's debug map, read only by dsymutil, which has run. Neither changes the
    # Mach-O UUID a dSYM matches by. Re-wrapped without -debug-symbols, so Info.plist has no
    # DebugSymbolsPath and an app embedding it archives no libgodot dSYM (arcade#263).
    for slice_id in XCFRAMEWORK_SLICES:
        dsym = xcf / slice_id / "dSYMs" / "libgodot.framework.dSYM"
        if not dsym.is_dir():
            fail(f"no dSYM in {xcf / slice_id}")
        if symbols_in is not None:
            (symbols_in / slice_id).mkdir(parents=True, exist_ok=True)
            shutil.move(str(dsym), str(symbols_in / slice_id / dsym.name))
        binary = xcf / slice_id / "libgodot.framework" / "libgodot"
        before = binary.stat().st_size
        run(["strip", "-S", binary])
        print(f"{slice_id}/libgodot: {before} bytes, {binary.stat().st_size} without its debug map", flush=True)
    rewrapped = tree / "build" / "libgodot.xcframework.rewrap"
    if rewrapped.exists():
        shutil.rmtree(rewrapped)
    run(["xcodebuild", "-create-xcframework", "-framework", xcf / "ios-arm64" / "libgodot.framework", "-framework", sim, "-output", rewrapped])
    shutil.rmtree(xcf)
    shutil.move(str(rewrapped), str(xcf))
    # The Simulator's dyld refuses an unsigned library, and arcade's Simulator builds sign
    # nothing they embed. Signing after assembly is equivalent: Info.plist records none.
    run(["codesign", "--force", "--sign", "-", sim])
    return xcf


def check_xcframework(xcf: pathlib.Path, symbols_in: pathlib.Path | None) -> list:
    """Exactly the two slices, the Simulator's arm64-only and ad-hoc signed, no dSYMs or
    debug map, and with symbols each slice's dSYM matching what ships. Returns the symbol
    ids."""
    with open(xcf / "Info.plist", "rb") as f:
        info = plistlib.load(f)
    slices = sorted(lib["LibraryIdentifier"] for lib in info["AvailableLibraries"])
    if slices != XCFRAMEWORK_SLICES:
        fail(f"xcframework slices: {slices}, expected {XCFRAMEWORK_SLICES}")
    print(f"libgodot.xcframework: slices {' '.join(slices)}", flush=True)

    sim = xcf / "ios-arm64-simulator" / "libgodot.framework"
    run(["codesign", "--verify", sim])
    signature = subprocess.run(["codesign", "-dv", str(sim)], capture_output=True, text=True)
    if "Signature=adhoc" not in signature.stderr.splitlines():
        fail(f"{sim} is not ad-hoc signed")
    archs = output(["lipo", "-archs", sim / "libgodot"])
    if archs != "arm64":
        fail(f"{sim}/libgodot is not arm64 only: {archs}")
    print("ios-arm64-simulator: arm64 only, ad-hoc signed", flush=True)

    if any(xcf.rglob("*.dSYM")) or b"DebugSymbolsPath" in (xcf / "Info.plist").read_bytes():
        fail(f"{xcf} still carries dSYMs or a DebugSymbolsPath")
    ids = []
    for slice_id in XCFRAMEWORK_SLICES:
        binary = xcf / slice_id / "libgodot.framework" / "libgodot"
        oso = sum(1 for line in output(["xcrun", "nm", "-ap", binary]).splitlines() if " OSO " in line)
        if oso:
            fail(f"{binary} still has a debug map ({oso} object files)")
        assert_backend(binary, "jsc")
        if symbols_in is None:
            continue
        # Without debug_symbols=yes the link still makes a dSYM, with next to no DWARF.
        dwarf = symbols_in / slice_id / "libgodot.framework.dSYM" / "Contents" / "Resources" / "DWARF" / "libgodot"
        uuid = output(["xcrun", "dwarfdump", "--uuid", binary]).split()[1]
        if output(["xcrun", "dwarfdump", "--uuid", dwarf]).split()[1] != uuid:
            fail(f"{slice_id}: the dSYM's UUID is not the binary's ({uuid})")
        info_size = 0
        for line in output(["xcrun", "llvm-objdump", "--section-headers", dwarf]).splitlines():
            fields = line.split()
            if len(fields) > 2 and fields[1] == "__debug_info":
                info_size = int(fields[2], 16)
        if info_size < 64 << 20:
            fail(f"{slice_id}: the dSYM has only {info_size} bytes of __debug_info")
        print(f"{slice_id}: UUID {uuid}, binary {binary.stat().st_size} bytes, dSYM DWARF {dwarf.stat().st_size} bytes ({info_size} of __debug_info)", flush=True)
        ids.append((uuid.replace("-", "").lower(), slice_id, "libgodot.framework"))
    return ids


def stage_apple(tree: pathlib.Path, godot: pathlib.Path, xcf: pathlib.Path, stage: pathlib.Path, out: pathlib.Path, symbols_in: pathlib.Path | None) -> list:
    gdextension = tree / "build" / "gdextension"
    if not (gdextension / "extension_api.json").is_file():
        fail("no extension_api.json: the libgodot build scripts changed layout?")
    rev = godotjs_rev()
    godot_rev = output(["git", "-C", godot, "rev-parse", "HEAD"])

    # What arcade's method-bind hashes and host are generated from, unpublished.
    sdk = fresh(stage / "gdextension")
    shutil.copy2(gdextension / "extension_api.json", sdk)
    shutil.copy2(gdextension / "gdextension_interface.h", sdk)
    shutil.copy2(tree / "libgodot_framework" / "libgodot" / "libgodot.h", sdk)

    staged_xcf = stage / "libgodot.xcframework"
    if staged_xcf.exists():
        shutil.rmtree(staged_xcf)
    shutil.copytree(xcf, staged_xcf, symlinks=True)
    ids = check_xcframework(staged_xcf, symbols_in)

    editor_dir = fresh(stage / MACOS_EDITOR)
    editor = editor_dir / "godot-editor"
    shutil.copy2(godot / "bin" / "godot.macos.editor.dev.arm64", editor)
    assert_backend(editor, "jsc")
    assert_godot_version(editor, godot_rev)
    write_text(editor_dir / STAMP, stamp_json({"rev": LIBGODOT_REV, "godotjs_rev": rev}))

    licenses = editor_dir / "licenses"
    quickjs_rev = copy_licenses(godot, licenses)
    notice = LIBGODOT_NOTICE.format(godot_rev=godot_rev, libgodot_rev=LIBGODOT_REV, godotjs_rev=rev, quickjs_rev=quickjs_rev, run_url=run_url())
    write_text(editor_dir / "NOTICE.txt", notice)

    out.mkdir(parents=True, exist_ok=True)
    write_text(out / "NOTICE.txt", notice)
    for f in licenses.iterdir():
        shutil.copy2(f, out / f.name)
    for name, src in (("libgodot-xcframework.zip", staged_xcf), (f"{MACOS_EDITOR}.zip", editor_dir)):
        ditto_zip(src, out / name)
        write_sha256(out / name)
    shutil.copy2(editor_dir / STAMP, out / f"{MACOS_EDITOR}.stamp.json")
    write_text(out / "xcode-version.txt", output(["xcodebuild", "-version"]) + "\n")
    return ids


# -------------------------------------------------------------------------- android ---


def patch_gradle_scons_args(godot: pathlib.Path) -> None:
    """Gradle's own scons task hardcodes its args, so if it ran (Android Studio,
    -PgenerateNativeLibs; a plain generateGodotTemplates never schedules it) it would drop
    use_quickjs_ng=yes and link V8. What actually keeps V8 out is the byte check."""
    path = godot / "platform" / "android" / "java" / "lib" / "build.gradle"
    text = path.read_text(encoding="utf-8")
    if "use_quickjs_ng=yes" in text:
        return
    anchor = '"arch=${selectedAbi}", "-j"'
    if anchor not in text:
        fail(f"{path}: scons args anchor not found; build.gradle changed shape?")
    write_text(path, text.replace(anchor, '"arch=${selectedAbi}", "use_quickjs_ng=yes", "-j"'))


def build_android(godot: pathlib.Path, jobs: int, symbols_in: pathlib.Path | None) -> pathlib.Path:
    """scons builds libgodot_android.so, gradle's generateGodotTemplates packages it into the
    .aar (gradle finds scons on PATH at configure time, though it never runs it); returns
    the .aar. arm64 only, which Play's 64-bit rule allows. The fork's Android needs
    angle_libs= (its detect.py has no default) and opengl3=no (its GLES3/EGL backend does
    not compile; the app runs Vulkan only)."""
    flags = ["platform=android", "arch=arm64", "target=template_debug", "use_quickjs_ng=yes", "angle_libs=", "opengl3=no"]
    if symbols_in is not None:
        # Adds DWARF and drops the `-s` link flag. Gradle strips the copy it packages
        # (same build id), so the copy saved here is the only one with the DWARF.
        flags.append("debug_symbols=yes")
    run([tool("scons"), *flags, f"-j{jobs}"], cwd=godot)
    if symbols_in is not None:
        (symbols_in / "android").mkdir(parents=True, exist_ok=True)
        shutil.copy2(godot / "platform" / "android" / "java" / "lib" / "libs" / "debug" / "arm64-v8a" / "libgodot_android.so", symbols_in / "android")
    patch_gradle_scons_args(godot)
    run(["./gradlew", "generateGodotTemplates"], cwd=godot / "platform" / "android" / "java")
    return godot / "bin" / AAR


def stage_android(aar: pathlib.Path, stage: pathlib.Path, out: pathlib.Path, symbols_in: pathlib.Path | None) -> list:
    """QuickJS-NG and nothing else, the getRenderView lookup and 16 KB pages, on the .so
    the .aar carries; with symbols, the saved copy matching it by build id."""
    stage.mkdir(parents=True, exist_ok=True)
    staged = stage / AAR
    shutil.copy2(aar, staged)
    with tempfile.TemporaryDirectory() as tmp:
        so = pathlib.Path(tmp) / "libgodot_android.so"
        with zipfile.ZipFile(staged) as z:
            so.write_bytes(z.read("jni/arm64-v8a/libgodot_android.so"))
        assert_backend(so, "quickjs")
        assert_render_view_lookup(so)
        assert_16kb_aligned(so)
        ids = []
        if symbols_in is not None:
            shipped, saved = Elf(so), Elf(symbols_in / "android" / "libgodot_android.so")
            build_id = shipped.build_id()
            if not build_id or saved.build_id() != build_id:
                fail(f"the saved .so's build id is not the shipped one's ({build_id})")
            if ".debug_info" in shipped.section_names():
                fail("the shipped libgodot_android.so still carries DWARF")
            if ".debug_info" not in saved.section_names():
                fail("the saved libgodot_android.so has no DWARF: did debug_symbols reach scons?")
            print(f"libgodot_android.so: build id {build_id}, {so.stat().st_size} bytes shipped, {len(saved.data)} with DWARF", flush=True)
            ids.append((build_id, "android-arm64-v8a", "libgodot_android.so"))
    # The .aar keeps gradle's name, at the zip root (arcade's `unpack: aar`).
    out.mkdir(parents=True, exist_ok=True)
    ditto_zip(staged, out / "godot-lib-android-aar.zip")
    write_sha256(out / "godot-lib-android-aar.zip")
    return ids


# ----------------------------------------------------------------------------- host ---


def host_platform() -> str:
    if sys.platform.startswith("linux"):
        return "linuxbsd"
    if sys.platform == "win32":
        return "windows"
    fail(f"the host part builds on Linux or Windows, not {sys.platform}")
    return ""


def build_host(godot: pathlib.Path, platform: str, jobs: int, scons_cache: str | None) -> None:
    """use_quickjs_ng=yes alone picks the backend: GodotJS reads custom.py's use_jsc only when
    no QuickJS option is set. The cache does not change what is built. SCons finds MSVC."""
    flags = [f"platform={platform}", "target=editor", "use_quickjs_ng=yes", f"-j{jobs}"]
    if scons_cache:
        flags += [f"cache_path={scons_cache}", "cache_limit=6"]
    run([tool("scons"), *flags], cwd=godot)


def stage_host(godot: pathlib.Path, platform: str, stage: pathlib.Path, out: pathlib.Path) -> None:
    spec = HOSTS[platform]
    name = f"godot-editor-{spec['host']}"
    editor_dir = fresh(stage / name)
    for built, staged in spec["bins"].items():
        src = godot / "bin" / built
        if not src.exists():
            fail(f"{src} was not built; scons' output naming changed?")
        shutil.copy2(src, editor_dir / staged)
    assert_backend(editor_dir / spec["engine"], "quickjs")
    rev = godotjs_rev()
    stamp = stamp_json({"platform": platform, "godot_vanilla_rev": GODOT_VANILLA_REV, "godotjs_rev": rev})
    write_text(editor_dir / STAMP, stamp)
    quickjs_rev = copy_licenses(godot, editor_dir / "licenses")
    write_text(
        editor_dir / "NOTICE.txt",
        HOST_NOTICE.format(host=spec["host"], godot_rev=GODOT_VANILLA_REV, godotjs_rev=rev, quickjs_rev=quickjs_rev, run_url=run_url()),
    )

    # Contents at the zip's root, no wrapping directory: arcade's ci/vendor.nu unpacks it
    # into Vendor/<name>/. Unix modes recorded, though vendor.nu reapplies +x anyway.
    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / f"{name}.zip"
    executables = set(spec["bins"].values())
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(editor_dir.rglob("*")):
            if f.is_dir():
                continue
            rel = f.relative_to(editor_dir).as_posix()
            info = zipfile.ZipInfo.from_file(f, rel)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if rel in executables else 0o100644) << 16
            with open(f, "rb") as fh:
                z.writestr(info, fh.read(), compresslevel=9)
    write_sha256(zip_path)
    write_text(out / f"{name}.stamp.json", stamp)
    print(f"{zip_path.name}: {zip_path.stat().st_size} bytes", flush=True)
    print("NOT the engine that ships: vanilla Godot + QuickJS-NG, not the libgodot fork's JavaScriptCore build.", flush=True)


def smoke(args: argparse.Namespace) -> None:
    """--version names the pinned Godot, and GodotJS's own integration project (tests/project)
    runs headless to its completion sentinel, as upstream's runtime matrix runs it for
    host-qjs, exercising the module end to end on QuickJS-NG."""
    spec = HOSTS[host_platform()]
    editor = pathlib.Path(args.stage).resolve() / f"godot-editor-{spec['host']}" / spec["run"]
    project = pathlib.Path(args.project).resolve()
    pnpm = tool("pnpm")

    def step(label: str, cmd: list) -> str:
        print(f"==> {label}: {' '.join(str(c) for c in cmd)}", flush=True)
        result = subprocess.run([str(c) for c in cmd], cwd=project, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        log = result.stdout + result.stderr
        print(log, flush=True)
        if result.returncode != 0:
            fail(f"{label} exited {result.returncode}")
        return log

    version = step("version", [editor, "--version"]).strip().splitlines()[-1]
    if not version.endswith(GODOT_VANILLA_REV[:9]):
        fail(f"--version printed {version!r}, expected a build of {GODOT_VANILLA_REV[:9]}")
    # `--outDir .godot/godotjs`: GodotJS loads compiled JS from `.godot/<module directory>`,
    # which here is godotjs, not the GodotJS the project's tsconfig assumes.
    out_dir = ".godot/godotjs"
    step("pnpm install", [pnpm, "install", "--ignore-workspace", "--frozen-lockfile"])
    step("tsc (emit only)", [pnpm, "exec", "tsc", "--noCheck", "--outDir", out_dir])
    step("generate types", [editor, "--headless", "--editor", "--generate-types", "--path", "."])
    step("tsc", [pnpm, "exec", "tsc", "--outDir", out_dir])
    log = step("run tests/project", [editor, "--audio-driver", "Dummy", "--headless", "--path", "."])
    if "GODOTJS_TEST_PROJECT_COMPLETED" not in log:
        fail("tests/project never printed GODOTJS_TEST_PROJECT_COMPLETED")
    for bad in ("GODOTJS_TEST_PROJECT_FAILED:", "exception thrown in function:", "Uncaught Error:"):
        if bad in log:
            fail(f"tests/project output contains {bad!r}")
    print(f"smoke test passed: {version}, GodotJS tests/project completed", flush=True)


# ---------------------------------------------------------------------------- build ---


def build(args: argparse.Namespace) -> None:
    part = args.part
    tree = pathlib.Path(args.tree).resolve()
    stage = pathlib.Path(args.stage).resolve()
    out = pathlib.Path(args.out).resolve()
    jobs = args.jobs or os.cpu_count() or 1
    # scons and gradle are long and quiet when piped; unbuffered, their progress reaches the
    # log as it happens, which tells a slow stage from a stalled one.
    os.environ["PYTHONUNBUFFERED"] = "1"
    symbols = pathlib.Path(args.symbols).resolve() if args.symbols else None
    if symbols is not None and part == "host":
        fail("only the apple and android parts have symbols to publish")
    symbols_in = pathlib.Path(tempfile.mkdtemp(dir=os.environ.get("RUNNER_TEMP"))) if symbols else None

    godot = source(part, tree)
    if part == "apple":
        xcf = build_apple(tree, godot, jobs, symbols_in)
        ids = stage_apple(tree, godot, xcf, stage, out, symbols_in)
    elif part == "android":
        aar = build_android(godot, jobs, symbols_in)
        ids = stage_android(aar, stage, out, symbols_in)
    else:
        platform = host_platform()
        build_host(godot, platform, jobs, args.scons_cache)
        stage_host(godot, platform, stage, out)
        ids = []
    if symbols is not None:
        sort_symbols(part, symbols_in, ids, symbols)
    print(f"{part}: staged under {stage}, release files under {out}:", flush=True)
    for f in sorted(out.iterdir()):
        print(f"  {f.stat().st_size:>12}  {f.name}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    parts = ["apple", "android", "host"]

    p = sub.add_parser("plan")
    p.add_argument("--release", choices=["libgodot", "host-editors"], required=True)
    p.set_defaults(func=plan)

    p = sub.add_parser("source")
    p.add_argument("--part", choices=parts, required=True)
    p.add_argument("--tree", required=True)
    p.set_defaults(func=source_command)

    p = sub.add_parser("build")
    p.add_argument("--part", choices=parts, required=True)
    p.add_argument("--tree", required=True, help="the engine tree, cloned at its pin when absent")
    p.add_argument("--stage", required=True, help="the artifacts, unpacked")
    p.add_argument("--out", required=True, help="the release files of this part")
    p.add_argument("--symbols", help="build with debug_symbols=yes and sort the DWARF here")
    p.add_argument("--jobs", type=int, help="scons -j (default: every CPU)")
    p.add_argument("--scons-cache", help="a SCons cache directory (host part)")
    p.set_defaults(func=build)

    p = sub.add_parser("smoke")
    p.add_argument("--stage", required=True)
    p.add_argument("--project", required=True)
    p.set_defaults(func=smoke)

    p = sub.add_parser("symbols")
    p.add_argument("--symbols", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=symbols_command)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
