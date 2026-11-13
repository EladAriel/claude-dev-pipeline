# Owner decisions for the scaffold

These override the design where they differ. The design text itself is unchanged in `DESIGN.md`.

- Skills from mattpocock/skills are not copied. Install the official plugin
  `mattpocock-skills` v1.3.2, or read the commit-pinned URLs in
  `skills/manifest.yaml`. The pin is git tag `v1.3.2`
  (`d1478fcb140db0b93c0c8df17bcfd311844749d0`). The manifest also lists the
  dependencies those skills call (`grilling`, `domain-modeling`,
  `writing-for-agents`). Attribution is in `NOTICE`.
- Evaluation is inline. Each run records metrics in SQLite after retro, inside
  the $20 run cap. There is no separate golden-task suite and no extra eval
  budget. Ignore those parts of the design.
- There is no demo repository.
- Docs describe local checks. They do not ask anyone to open a hosted console
  or to call a paid API.
- The Python package is `src/claude_dev_pipeline` (src layout). The design
  shows a top-level `orchestrator/` directory.
- Stage agent files are not in this scaffold. `agents/` is a placeholder.
- `reviewers/codex/` documents the future read-only command and does not run it.
- The GitHub ruleset is documented in `RULESET.md` and is not applied here.
- MCP matches design §16: remote URL plus vault auth, separate read and write
  vaults, servers disabled. Code graph is a disabled sandbox CLI, not MCP.
- GitHub Issues are the task source of truth. See `GITHUB_ISSUES.md`.
- Herdr is optional. See `herdr.md`.
