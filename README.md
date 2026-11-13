# claude-dev-pipeline

Status: **scaffold**.

A local, deterministic Python orchestrator for a generic software-dev pipeline.
Later stages will be one Claude Managed Agent each. This repository does not
call Anthropic or OpenAI, and it does not merge pull requests. Only the owner
merges.

GitHub Issues are the source of truth for tasks. A dry-run prints the stage
plan and the issue payloads it would create.

## Stage order

Triage (Haiku) → spec (Opus, grill-with-docs / to-spec) → plan (Opus, to-tickets)
→ implement (Sonnet, one session per ticket, tdd) → verify → review (parallel
finders, including local `codex exec --sandbox read-only`, then a verifier vote)
→ draft PR (Haiku) → retro (Sonnet). After retro, the orchestrator records
inline metrics on the same run. There is no separate evaluation budget.

The default run cap is **$20**. Crossing it is a hard stop.

## Quickstart

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Put your own keys in `.env` when a later stage needs them. The scaffold does
not read `.env` and does not call an API.

```bash
pipeline --dry-run --config config/pipeline.config.example.yaml
pipeline run "describe the change"
pipeline run --spec path/to/spec.md
pipeline run --issue 12 --issue-snapshot issues/12.json --model-fixture triage-response.json
pipeline status
pipeline import plan.json
```

`--issue` runs only when the local snapshot shows that `EladAriel` applied
`agent:approved`. The scaffold does not call GitHub to check that. `pipeline
import` prints one issue per task and does not create issues unless you pass
`--apply`, which writes a local outbox and still does not call GitHub.

## Layout

- `src/claude_dev_pipeline/` — orchestrator package
- `config/` — models, budget caps, and the example target-repo config
- `docs/DESIGN.md` — approved design
- `docs/GITHUB_ISSUES.md` — issues, status labels, and the claude.ai Artifact note
- `docs/herdr.md` — optional herdr pane / `hc` usage
- `skills/manifest.yaml` — commit-pinned skill URLs, no copied skills
- `.claude/commands/pipeline.md` — Claude Code slash command

## Verify locally

```bash
ruff check .
ruff format --check .
pytest
pipeline scan-paths
gitleaks detect --source . --redact --config .gitleaks.toml
pipeline --dry-run --config config/pipeline.config.example.yaml
```

No hosted console check is part of this scaffold.
