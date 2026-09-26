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
- **Arcade's two Android fixes are commits, not patches (arcade#171).** They
  used to live here as `.patch` files under `bahala/patches/godot/`, applied
  at build time by `bahala-libgodot.yml` and Arcade's local `just
  engine-build`. Both now live as ordinary commits on
  [`bahalafoundation/godot`](https://github.com/bahalafoundation/godot)'s own
  `bahala` branch (a fork of `migeran/godot`: a GLES3_ENABLED guard, and the
  restored `getRenderView` JNI lookup, #72), reached through
  [`bahalafoundation/libgodot`](https://github.com/bahalafoundation/libgodot)'s
  (a fork of `migeran/libgodot`) `godot` gitlink. `bahala-libgodot.yml` builds
  from `bahalafoundation/libgodot` directly; nothing here applies a patch any
  more, and `bahala/patches/` is gone.
- **Sync upstream by merging** `upstream/main` into `bahala`, never by
  rebasing. Mirror upstream's `main` to this fork's `main` unchanged.
- **Only our two workflows run here.** Upstream's workflows are disabled in
  the repository's Actions settings, not deleted, so their files stay as
  upstream wrote them. After a sync, check that nothing new came in enabled:
  `gh workflow list -R bahalafoundation/GodotJS --all`.
  - `bahala-host-editors.yml` publishes the Linux and Windows host editors
    (vanilla Godot + GodotJS on QuickJS-NG) as `editor-godot-<7>-godotjs-<7>`.
  - `bahala-libgodot.yml` publishes the engine Arcade ships (the
    bahalafoundation/libgodot fork + GodotJS): the iOS xcframework (device and arm64
    Simulator slices) and macOS editor on JavaScriptCore, and the Android
    `.aar` on QuickJS-NG, as `libgodot-<7>-godotjs-<7>-r<recipe>`. It runs on
    the hosted `xcode-27` runner; if that preview label breaks, dispatch it
    with `runner=macos-26`.
- **Releases are never overwritten.** Both workflows name a release after the
  last commit outside `.github/`, and build nothing if it already exists. The
  tag itself points at the commit the run built (identical outside
  `.github/`): `GITHUB_TOKEN` may not create a tag on a tree whose workflow
  files the default branch doesn't have. So a
  commit like this one, which only touches `.github/`, builds nothing.
  `bahala-libgodot.yml`'s tag also ends in `-r<LIBGODOT_RECIPE>`, the version
  of its recipe: a `.github/` commit that changes what that workflow builds
  bumps it (and Arcade's `LIBGODOT_RECIPE` to match), so it gets a new release
  instead of mapping onto the old one. Arcade pins every zip by SHA-256, so
  don't delete a release Arcade has pinned; a release nothing pins can be
  deleted with its tag by hand to rebuild it.
