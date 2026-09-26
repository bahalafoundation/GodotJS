# The `bahala` branch

This is Bahala's fork of [GodotJS](https://github.com/godotjs/GodotJS), which
[Arcade](https://github.com/bahalafoundation/arcade) builds from. The full
process (changing GodotJS, bumping Arcade's pin, syncing upstream, sending
fixes upstream) is documented in Arcade's `docs/build.md`, § "Working on the
GodotJS fork". What follows are the rules for working in this repository.

- **`bahala` is append-only.** Never rewrite it or force-push it. Arcade pins
  commits on this branch by SHA and checks them out of a full clone, so every
  commit it has ever pinned must stay reachable.
- **Push to both remotes:** GitHub (`bahalafoundation/GodotJS`) and the
  homelab mirror (`oss/godotjs` on Gitea). They don't sync.
- **Keep concerns in separate commits:** the Godot 4.5.1 compat commit, fixes
  meant for upstream, and our tooling under `.github/`. A fix going upstream
  must not depend on either of the others.
- **Sync upstream by merging** `upstream/main` into `bahala`, never by
  rebasing. Mirror upstream's `main` to this fork's `main` unchanged.
- **Only `bahala-host-editors.yml` runs here.** Upstream's workflows are
  disabled in the repository's Actions settings, not deleted, so their files
  stay as upstream wrote them. After a sync, check that nothing new came in
  enabled:
  `gh workflow list -R bahalafoundation/GodotJS --all`. Gitea Actions is off on
  the mirror.
- **Releases are never overwritten.** `bahala-host-editors.yml` publishes the
  Linux and Windows editors as `editor-godot-<7>-godotjs-<7>`, named after the
  last commit outside `.github/`. So a commit like this one, which only touches
  `.github/`, builds nothing.
