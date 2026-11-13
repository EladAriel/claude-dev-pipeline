---
name: pipeline
description: Start the local claude-dev-pipeline CLI, then report status and the PR link. Dry-run only.
---

# Pipeline

Use the local orchestrator. Do not call a model API and do not merge.

1. From the repo root, run one of `pipeline run "<task>"`, `pipeline run --spec <file>`, or `pipeline run --issue <n>`.
2. Run `pipeline status`.
3. Tell the user the run id, status, intake, and PR link. `pr: none` means no pull request has been opened.
4. Herdr is optional. Background panes and a Codex reviewer are documented in `docs/herdr.md` and are not started unless the user asks.
