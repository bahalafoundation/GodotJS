# The `bahala` branch

This is Bahala's fork of [GodotJS](https://github.com/godotjs/GodotJS), which
[Arcade](https://github.com/bahalafoundation/arcade) builds from. The full
process (changing GodotJS, bumping Arcade's pin, syncing upstream, sending
fixes upstream) is documented in Arcade's `docs/build.md`, § "Working on the
GodotJS fork". What follows are the rules for working in this repository.

- **`bahala` is append-only.** Never rewrite it or force-push it. Arcade pins
  commits on this branch by SHA and checks them out of a full clone, so every
  commit it has ever pinned must stay reachable.
- **GitHub is the only remote.** Push to `bahalafoundation/GodotJS` and
  nowhere else; every build and release happens here. (The homelab's Gitea
  copy, `oss/godotjs`, was removed in arcade#170.)
- **Keep concerns in separate commits:** the Godot 4.5.1 compat commit, fixes
  meant for upstream, and our tooling under `.github/`. A fix going upstream
  must not depend on any of the others.
- **The engine build is `build.py`, once (arcade#436).** It holds the pins
  (`LIBGODOT_REV`, `GODOT_VANILLA_REV`), the release tag scheme and its recipe
  versions, every scons and gradle call, the staging, the notices and the
  byte-level checks. Both workflows call it and keep only the orchestration
  (runners, toolchains, artifacts, the release), and Arcade's `just
  engine-build` runs it from the commit a release's tag points at, so Arcade
  pins a release (tag and SHA-256) and knows nothing else about the build.
  `python3 .github/bahala/build.py --help` lists the parts and commands.
  Bumping Godot, libgodot or the recipe is a commit here and a `vendor.yaml`
  re-pin in Arcade, nothing more.
- **Arcade's Godot fixes are commits, not patches (arcade#171).** They live
  on [`bahalafoundation/godot`](https://github.com/bahalafoundation/godot)'s
  own `bahala` branch (a fork of `migeran/godot`), reached through
  [`bahalafoundation/libgodot`](https://github.com/bahalafoundation/libgodot)'s
  (a fork of `migeran/libgodot`) `godot` gitlink, which `LIBGODOT_REV` pins.
  Nothing here applies a patch.
- **Sync upstream by merging** `upstream/main` into `bahala`, never by
  rebasing. Mirror upstream's `main` to this fork's `main` unchanged.
- **Only our two workflows run here.** Upstream's workflows are disabled in
  the repository's Actions settings, not deleted, so their files stay as
  upstream wrote them. After a sync, check that nothing new came in enabled:
  `gh workflow list -R bahalafoundation/GodotJS --all`.
  - `bahala-host-editors.yml` publishes the Linux and Windows host editors
    (vanilla Godot + GodotJS on QuickJS-NG) as
    `editor-godot-<7>-godotjs-<7>-r<recipe>` (build.py's `host` part).
  - `bahala-libgodot.yml` publishes the engine Arcade ships (the
    bahalafoundation/libgodot fork + GodotJS): the iOS xcframework (device and arm64
    Simulator slices) and macOS editor on JavaScriptCore, and the Android
    `.aar` on QuickJS-NG, as `libgodot-<7>-godotjs-<7>-r<recipe>` (build.py's
    `apple` and `android` parts), with both
    engines' debug symbols as `libgodot-symbols.zip` (arcade#261). It runs on
    the hosted `xcode-27` runner; if that preview label breaks, dispatch it
    with `runner=macos-26`.
- **Releases are never overwritten.** Both workflows name a release after the
  last commit outside `.github/`, and build nothing if it already exists. The
  tag itself points at the commit the run built (identical outside
  `.github/`): `GITHUB_TOKEN` may not create a tag on a tree whose workflow
  files the default branch doesn't have. A commit that
  only touches `.github/` builds nothing, unless it moves a pin in `build.py`
  or bumps a recipe version there (`LIBGODOT_RECIPE`, `HOST_EDITORS_RECIPE`:
  the `-r<N>` of that workflow's tags). Bump it in a commit that changes what its release
  holds without moving a pin, so it gets a new release instead of mapping onto
  the old one. Arcade pins every zip by SHA-256, so don't delete a release
  Arcade has pinned; a release nothing pins can be deleted with its tag by
  hand to rebuild it.
- **Try a change on a branch.** Dispatch either workflow on it (`gh workflow
  run bahala-libgodot.yml -R bahalafoundation/GodotJS --ref <branch>`): it
  builds and checks everything and publishes nothing, since only `bahala`
  publishes.
