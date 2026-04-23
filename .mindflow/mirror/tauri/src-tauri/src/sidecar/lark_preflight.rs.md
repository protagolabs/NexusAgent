---
code_file: tauri/src-tauri/src/sidecar/lark_preflight.rs
last_verified: 2026-04-23
---

# lark_preflight.rs — skill-pack (SKILL.md) runtime installer

## Intent

The `claude` and `lark-cli` binaries themselves are bundled inside the dmg
(see scripts/build-desktop.sh step 3.5-3.6), so there is nothing to `npm
install -g` at runtime. What is NOT bundled is the **Lark skill pack**:
the `lark_skill` MCP tool reads SKILL.md knowledge files under
`~/.agents/skills/lark-*/`, which `npx skills add larksuite/cli -y -g`
installs into the user's home directory.

We have to do this at runtime (not build) because the files must live under
$HOME so the user's other tooling (claude-code's skill system in
particular) can discover them too — a build-time copy into the bundle
wouldn't satisfy that.

## Why best-effort + detached

- Install may hang on slow registries → `tokio::time::timeout` with 180s cap.
- Install may outright fail (no network, broken npm registry). The `lark_skill`
  MCP tool degrades to "not found" in that case — the Agent falls back to
  `<domain> +<cmd> --help`, which is worse UX but not broken.
- setup() must not block on this — `tokio::spawn` fire-and-forget.

## Changes vs. run.sh

`scripts/run.sh` still installs both the lark-cli binary (`npm install -g
@larksuite/cli`) and the skill pack (`npx skills add ...`). Bundle mode
does only the skill-pack step, because the binary is shipped inside the dmg.

## Upstream / downstream

- **Called by:** `lib.rs::run()` inside `setup()`
- **Depends on:** bundled `resources/nodejs/bin/npx` — resolved via
  `state::resolve_resource_dir()` and `state::resolve_bundled_node_bins()`
- **Fallback:** dev mode (no bundled npx) → log and skip; `bash run.sh`
  covers that path via npm-global

## Gotchas

- PATH construction in `install_skill_pack`: bundled node must be first on
  PATH so `#!/usr/bin/env node` in the npx shim resolves to OUR node, not
  whatever node the user might have. Same pattern as
  `process_manager::start_service`.
- `lark_skills_present()` checks two locations because `skills add -g`
  installs to `~/.agents/skills/` and creates a `~/.claude/skills/` symlink.
  Either one satisfies the MCP tool's lookup order.
