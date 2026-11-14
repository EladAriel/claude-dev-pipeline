# claude-dev-pipeline

Status: **offline stages through retro**.

A local, deterministic Python orchestrator for a generic software-dev pipeline.
Each stage has an agent definition. The commands in this repository do not call
Anthropic or OpenAI, and they do not merge pull requests. Only the owner merges.

GitHub Issues are the source of truth for tasks. A dry-run prints the stage
plan and the issue payloads it would create.

## Diagrams

What the owner does. The pipeline does not merge.

```mermaid
flowchart TD
  issue["Write an issue"]
  gate{"agent:approved by EladAriel"}
  ignore["Ignored, no model call"]
  run["pipeline run"]
  dup{"Duplicate or open pipeline PR"}
  dupStop["One comment and pipeline:duplicate"]
  clarity{"Clarity below threshold"}
  ask["Questions inline or one comment"]
  resumeCmd["pipeline resume"]
  lane{"Lane"}
  answer["answer.md and optional comment"]
  wait["Wait for approve-nocode"]
  stages["Stage pipeline"]
  debugCmd["pipeline debug"]
  summary["summary.md and draft PR"]
  reviewPr["Owner reviews the PR"]
  merge["Owner merges when checks are green"]
  retroOut["retro.md, proposals not applied"]
  revertCmd["pipeline revert"]

  issue --> gate
  gate -->|no| ignore
  gate -->|yes| run
  run --> debugCmd
  run --> dup
  dup -->|yes| dupStop
  dup -->|no| clarity
  clarity -->|yes| ask
  ask --> resumeCmd
  resumeCmd --> clarity
  clarity -->|no| lane
  lane -->|no-code| answer
  lane -->|needs code| wait
  wait -->|approved| stages
  lane -->|code| stages
  stages --> summary
  summary --> reviewPr
  reviewPr --> merge
  merge --> retroOut
  merge --> revertCmd
```

Owner path from an approved issue through the local CLI to a merge the owner performs.

How a run moves. Models are the ids in `config/models.yaml`.

```mermaid
flowchart TD
  snap["Approved issue snapshot"]
  budget{"Budget check before the stage"}
  halt["Halt and checkpoint"]
  resume["pipeline resume"]
  triage["Triage, Haiku"]
  dup["Duplicate check"]
  dupStop["Stop, pipeline duplicate"]
  clarity["Clarity gate, Haiku"]
  route{"Tier S M L XL"}
  fast["Fast lane, skip spec and plan"]
  docs["Docs lane, skip spec plan bootstrap"]
  nocode["No-code lane, answer.md"]
  deps["Deps lane, no fast lane"]
  mig["Migrations, tier at least L"]
  mono["Monorepo, affected packages"]
  multi["Multi-repo, one PR per repo"]
  spec["Spec, Opus"]
  plan["Plan, Opus"]
  impl["Implement, Sonnet"]
  verify["Verify commands, Haiku on failure"]
  flaky["Flaky quarantine or no-tests path"]
  review["Review finders, Opus and Sonnet"]
  codex["codex exec read-only, gpt-6.1-sol"]
  tie["Tie-break, Haiku"]
  pr["PR stage, Haiku"]
  conflict{"Base moved with conflicts"}
  rebase["Rebase pipeline branch"]
  retro["Retro, Haiku, and summary.md"]

  snap --> budget
  budget -->|over cap| halt
  halt --> resume
  resume --> budget
  budget -->|ok| triage
  triage --> dup
  dup -->|halt| dupStop
  dup -->|ok| clarity
  clarity -->|needs info| halt
  clarity -->|ok| route
  route -->|S| fast
  route -->|docs| docs
  route -->|no-code| nocode
  nocode --> retro
  route -->|deps| deps
  route -->|migrations| mig
  route -->|monorepo| mono
  route -->|multi-repo| multi
  route -->|standard| spec
  fast --> impl
  docs --> impl
  deps --> spec
  mig --> spec
  mono --> spec
  multi --> plan
  spec --> plan
  plan --> impl
  impl --> verify
  verify --> flaky
  flaky --> review
  review --> codex
  codex --> tie
  tie --> pr
  pr --> conflict
  conflict -->|yes| rebase
  rebase --> verify
  conflict -->|no| retro
```

Stage order, lanes, and the configured model. A budget halt checkpoints the run for resume.

Where the pieces sit, and what they are allowed to see.

```mermaid
flowchart TB
  subgraph local ["Local machine"]
    cli["Python orchestrator and CLI"]
    dotenv[".env gitignored, never committed"]
    state["runs state.json and pipeline.db"]
    codexLocal["Codex CLI, sandbox read-only"]
  end
  subgraph anthropic ["Anthropic Managed Agents"]
    agentFiles["agents markdown, one per stage"]
    envFile["environments sandbox-limited.yaml"]
    sessions["Session per stage, own sandbox"]
    skills["mattpocock skills, pinned, not vendored"]
  end
  subgraph github ["GitHub"]
    repo["Repo, issues, and draft PRs"]
    actions["Actions ci and secret-scan"]
    rules["Ruleset, owner merges, no force-push"]
  end
  subgraph mcp ["MCP, disabled in the example config"]
    ghMcp["GitHub MCP, create_pull_request only"]
    atlas["Atlassian MCP, off by default"]
    vaults["Vault ids, credentials not in agent files"]
  end

  cli --> agentFiles
  cli --> state
  dotenv -.-> sessions
  skills --> agentFiles
  envFile --> sessions
  agentFiles --> sessions
  cli --> repo
  repo --> actions
  actions --> rules
  cli --> codexLocal
  sessions -.-> ghMcp
  ghMcp --> vaults
  atlas --> vaults
```

Trust boundary: API keys stay in gitignored `.env`. MCP credentials stay in vaults. The scaffold CLI does not call the API, and the example MCP servers are off.

## Stage order

Triage (Haiku) → spec (Opus, grill-with-docs / to-spec) → plan (Opus, to-tickets)
→ implement (Sonnet, one session per ticket, tdd) → verify → review (parallel
finders, including local `codex exec --sandbox read-only`, then a verifier vote)
→ draft PR (Haiku) → retro (Haiku). Retro records inline metrics on the same
run, inside that run's budget. There is no separate evaluation budget.

The default run cap is **$20**. Crossing it is a hard stop.

## Quickstart

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Put `ANTHROPIC_API_KEY` in `.env` before a live Claude session, and
`OPENAI_API_KEY` before a real `codex` binary. Export them in the shell you
use for that session. The `pipeline` commands do not read `.env` and do not
call an API. Codex in this repo is `codex exec --sandbox read-only` when
`reviewers.codex.enabled` is true; if the binary is missing, review continues
with `cross_model` false. Herdr is optional (`docs/herdr.md`). From Claude
Code, `/pipeline` runs the same CLI and reports the run id and PR link
(`.claude/commands/pipeline.md`).

```bash
pipeline --dry-run --config config/pipeline.config.example.yaml
pipeline run "describe the change"
pipeline run --spec path/to/spec.md
pipeline run --issue 12 --issue-snapshot issues/12.json --model-fixture triage-response.json
pipeline run --issue 12 --no-push
pipeline status
pipeline report
pipeline import plan.json
```

`--no-push` writes `pr.json` and `pr-body.md` without pushing. Retro still
runs and writes `metrics.json` and `retro.md`. Proposals are not applied.
`pipeline report` prints the cost trend and, per stage, the share of model
calls that used the cheapest model on that stage's allowlist.

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
