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
  meant for upstream, Arcade's Godot patches under `bahala/`, and our tooling
  under `.github/`. A fix going upstream must not depend on any of the others.
- **`bahala/patches/godot/`** holds the two patches Arcade's Android engine
  needs on top of the migeran/libgodot fork's Godot (a GLES3 guard, and the
  `getRenderView` JNI lookup). They sit outside `.github/` on purpose: a
  change to them is a new module commit, so it gets new releases and a new pin
  in Arcade. `bahala-libgodot.yml` and Arcade's local `just engine-build` both
  apply them from the same checkout. There is no second copy in Arcade.
  Nothing under `bahala/` is compiled into the module.
- **Sync upstream by merging** `upstream/main` into `bahala`, never by
  rebasing. Mirror upstream's `main` to this fork's `main` unchanged.
- **Only our two workflows run here.** Upstream's workflows are disabled in
  the repository's Actions settings, not deleted, so their files stay as
  upstream wrote them. After a sync, check that nothing new came in enabled:
  `gh workflow list -R bahalafoundation/GodotJS --all`.
  - `bahala-host-editors.yml` publishes the Linux and Windows host editors
    (vanilla Godot + GodotJS on QuickJS-NG) as `editor-godot-<7>-godotjs-<7>`.
  - `bahala-libgodot.yml` publishes the engine Arcade ships (the
    migeran/libgodot fork + GodotJS): the iOS xcframework and macOS editor on
    JavaScriptCore, and the Android `.aar` on QuickJS-NG, as
    `libgodot-<7>-godotjs-<7>`. It runs on the hosted `xcode-27` runner; if
    that preview label breaks, dispatch it with `runner=macos-26`.
- **Releases are never overwritten.** Both workflows name a release after the
  last commit outside `.github/`, and build nothing if it already exists. The
  tag itself points at the commit the run built (identical outside
  `.github/`): `GITHUB_TOKEN` may not create a tag on a tree whose workflow
  files the default branch doesn't have. So a
  commit like this one, which only touches `.github/`, builds nothing. To
  replace a release, delete it and its tag by hand first; Arcade pins every
  zip by SHA-256.
