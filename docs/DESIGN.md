# Scaffold preface

This file is the approved design (Phase 1, rev 3). The design text below this preface is unchanged.
Owner decisions that override it for the scaffold are in [OWNER_DECISIONS.md](OWNER_DECISIONS.md).

Skills are referenced, not copied. Evaluation is inline on each run; ignore separate golden-task sections below. There is no demo repository. Operational docs do not ask for a hosted console or a paid API call. MCP servers follow section 16 and stay disabled. Code graph is a disabled sandbox CLI, not MCP. GitHub Issues are the task source of truth.

---

# claude-dev-pipeline: Design (Phase 1, rev 2)

> Status: **design proposal, Phase 1 (research only)**. Nothing in this document has been built, deployed, or run against a paid API.
> Research date: 2026-11-13. All facts and prices below come from the sources in [Sources](#sources), read on that date. Claude Managed Agents is in **beta**, so behavior and prices can change.
> Rev 3 (2026-11-13) adds §16 MCP integrations. Rev 2 (2026-11-13) applied the owner's decisions: the review stage is rebuilt on Anthropic's official code-review article, a Retro stage and an evals design are added, a local SQLite ledger is adopted, and the repo, Codex, PAT and lockfile decisions are applied (see §15 changelog).

## 0. TL;DR

- **What:** `claude-dev-pipeline` (public, `EladAriel`), a generic, any-repo/any-language software-development pipeline. Each stage is a **Claude Managed Agent**:
  Triage → Spec (grilling) → Plan → Implement (TDD) → **Pre-PR verify** (evidence, simplify, end-to-end) → **Review** (parallel finders → verifier vote → rank/tag → every finding answered, security blocks, ≤3 rounds) → PR → **Retro**.
  A small local **orchestrator** (Python) runs the stages, owns every gate, and writes the ledger. **Only the user merges.**
- **Review follows Anthropic's own article**, ["How the Claude Code team does code review"](https://claude.dev/blog/solving-code-review-at-anthropic/) (Oct 15, 2026), plus the Code Review docs and the official `code-review` plugin (§6). Codex (`gpt-6.1-sol`, local `codex exec`) is one of the parallel finders **and** one of the three votes, so every surviving finding is confirmed across model families.
- **Retro after every run** (§9): an adapted copy of mattpocock's `retro` skill reads the run's metrics and event digest and proposes environment fixes (checks, REVIEW.md, skills, routing). Nothing is auto-applied. **Evals** (§10): per-stage and per-run metrics, golden tasks on a demo repo, and a regression gate against a baseline.
- **Local SQLite** `runs/pipeline.db` (gitignored) indexes runs, stages, usage, findings, votes, responses, eval results, and retro items (§11). JSON artifacts on disk stay the source of truth.
- **Typical run** (1 feature, 3 tickets, 1 fix round, Standard review): **≈ $9.35 list cost** (was ≈ $7.40), of which session runtime is **≈ $0.24**. Range is ≈ $6.20–$14.60. New hard cap: **$20 per run**. See §8.
- **Security:** nothing secret in the repo (`.env`, `claude-lock.json`, `runs/` are gitignored). PRs are opened with the **user's own fine-grained PAT**. Because GitHub doesn't let authors approve their own PRs, the `main` ruleset requires **0 approvals** and instead requires a PR, required status checks, and resolved conversations. No agent, tool, or orchestrator code path can merge (§7).
- **MCP integrations (rev 3, §16):**
  - The official **Atlassian Rovo MCP** (`https://mcp.atlassian.com/v2/mcp`, scoped service-account key via `static_bearer`) is read-only in spec/plan. Jira writes (comment + transition only) happen only in the PR stage, `always_ask`.
  - The **GitHub remote MCP** is selected by URL path (`/mcp/x/pull_requests`) and only exposes `create_pull_request`.
  - **CodeGraph/Graphify** stay off by default. They are piloted inside the sandbox via their CLI, because a local stdio MCP can't be reached from the cloud sandbox, and kept only if the evals show a saving.
- **Phase 3, step 0:** the repo scaffold (gitignore, `.env.example`, ruleset, CI secret and path scan, schemas, orchestrator skeleton with the SQLite ledger and budget math, dry-run `ant apply`). See §13.

---

## 1. Research findings: Claude Managed Agents

Source root: <https://platform.claude.com/docs/en/managed-agents/overview>

### 1.1 Core objects

| Concept | What it is | API | Notes for us |
|---|---|---|---|
| **Agent** | A reusable, **versioned** config: `name`, `model` (string or `{id, effort, speed, inference_geo}`), `system`, `tools`, `mcp_servers`, `skills`, `multiagent`, `description`, `metadata` | `POST /v1/agents` | One agent per stage. The `version` goes up on every update. `model`, `system`, `tools`, `mcp_servers`, and `skills` can be overridden per session. [agent-setup] |
| **Environment** | Where sessions run: a `cloud` sandbox or a self-hosted one. Networking is `limited` (`allowed_hosts`, `allow_package_managers`, `allow_mcp_servers`) or `unrestricted` | `POST /v1/environments` | **API-created environments default to `unrestricted` networking**, so we must always set `limited` ourselves. Environments are **not versioned**. Each session gets its own sandbox. [environments], [cloud-sandboxes-reference] |
| **Session** | A running agent instance with `resources` (files, `github_repository`), `vault_ids`, and an optional `budget` | `POST /v1/sessions` | Status is `idle`, `running`, `rescheduling`, or `terminated`. A finished session goes `idle`, not `terminated`. [sessions], [session-operations] |
| **Events** | `user.message`, `user.interrupt`, `user.tool_confirmation`, `user.define_outcome`, `user.custom_tool_result`, plus agent/session events over SSE | `/v1/sessions/{id}/events` | `agent.thread_context_compacted` shows that compaction happens automatically. [reference] |

- **Config as files:** the `ant` CLI's `ant apply` creates and updates agents, environments, skills, memory stores, deployments, and vaults from **Markdown or YAML files in the repo**. In an agent `.md`, the frontmatter is the request body and the prose is the system prompt. Files refer to each other by relative path. Applied IDs go into `claude-lock.json`. Useful flags: `--dry-run`, `--prune`, and `--upgrade`, which re-resolves GitHub-URL skills that are otherwise **pinned to a commit**. [apply]
  - ⚠️ `claude-lock.json` records the **organization_id and workspace_id**. These are not secrets, but they are account identifiers. **Decision (rev 2): `claude-lock.json` is gitignored and never committed** (§7).
- **Beta header:** every endpoint needs `anthropic-beta: managed-agents-2026-04-01`. The curl examples also send `anthropic-version: 2026-10-21`. **The SDK sets the beta header automatically.** [overview], [agent-setup]
- **Data handling:** sessions are stateful and stored server-side, so they are **not ZDR or HIPAA eligible** (self-hosted is the exception). Sessions and files can be deleted through the API. [overview]

### 1.2 Tools, MCP, skills, GitHub

- **Built-in toolset `agent_toolset_20260401`:** `bash`, `read`, `write`, `edit`, `glob`, `grep`, `web_fetch`, `web_search`. Any of them can be disabled (`configs: [{name, enabled:false}]`), or you can start with everything off via `default_config.enabled:false`. Tool outputs over 100k characters spill to a file. [tools]
- **Permission policies:** `always_allow`, `always_ask`, and `auto`, where the server evaluates each call and a denial can't be overridden. The agent toolset **defaults to `always_allow`** and MCP toolsets **default to `always_ask`**. Approvals arrive as `user.tool_confirmation`. [permission-policies]
- **MCP:** remote HTTP (streamable HTTP, with SSE fallback) servers only, up to **20 per agent**. Each one needs a matching `mcp_toolset`, and individual MCP tools can be enabled per name. **Credentials never go in the agent.** They live in **vaults** (`static_bearer` or `mcp_oauth`), are matched by URL, and are passed per session as `vault_ids`. [mcp-connector], [vaults], [reference]
- **Skills:** attach `anthropic` (pptx/xlsx/docx/pdf) or `custom` uploaded skills, pinned by `version`, with up to 500 per session. Skills under a mounted repo's root **`.claude/skills/<name>/SKILL.md` are auto-loaded with no review step**, which makes that directory part of the trust boundary. [skills]
- **GitHub:** the `github_repository` session resource takes `url`, `authorization_token`, `mount_path`, and `checkout` (branch or commit). Without a token, a public repo clones but `git push` fails. The GitHub MCP server is `https://api.githubcopilot.com/mcp/`. The docs say: "Use fine-grained personal access tokens with minimum required permissions". [github]
- **Sandbox:** Ubuntu 24.04, up to 8 GB RAM and 10 GB disk, with Python 3.10–3.13, Node 20–22, Go, Rust, Java 21, Ruby, PHP, C/C++, Postgres 16 and Redis 7 (not running by default), Playwright with Chromium, and `docker` (limited availability). [cloud-sandboxes-reference]
- **Files / handoff:** you upload through the Files API and mount as a session resource (read-only, under `/mnt/session/uploads/`). Anything the agent writes to **`/mnt/session/outputs/`** can be listed and downloaded with the Files API. [files]

### 1.3 Multiagent, advisor, outcomes, scheduling

- **Multiagent (`multiagent: {type: multiagent_20261001}`):** three modes, each switched on or off separately. [multiagent-orchestration]
  - *Subagents:* delegation is **one level deep**, with **≤ 25 child threads**. Agents are `predefined_agents` (by ID, optionally version-pinned) or inline.
  - *Dynamic workflows:* the agent writes a program that fans work out to many agents in the background.
  - *Advisor:* the primary thread consults a model that is **at least as capable as itself**. Consultations are billed at the advisor's rates.
  - **Budgets in multiagent sessions are shared, with no per-thread caps.** [budgets]
- **Outcomes (`user.define_outcome` + rubric):** the harness provisions a **grader in a separate context** that scores the artifact against your rubric and sends feedback back to the agent. [define-outcomes]
- **Scheduled deployments:** a cron `expression` plus an IANA `timezone`, an agent, an environment, resources, and an initial event. A `budget` set here is copied onto **each** run. Rate-limited creations are recorded as failed runs and not retried. [scheduled-deployments]

### 1.4 Limits, budgets, observability

- **Rate limits (per org):** 300 create requests/min and 1,200 read requests/min, plus normal tier spend limits. [reference]
- **Session budget:** `budget: {type: "limit", max_list_cost: {amount: "<cents>", currency: "USD"}}`.
  - It can only be attached **at session creation**, though it can be changed or removed later.
  - It is enforced between model requests, so the request in flight finishes and spend can **overshoot by up to one request per thread**.
  - At the cap the session goes `idle` with `stop_reason: budget_reached`. It is not terminated.
  - List cost counts tokens, web searches ($10/1k), and runtime ($0.08/h). [budgets]
- **Usage tracking:** `session.usage` returns `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation.{ephemeral_5m,ephemeral_1h}`, `list_cost` (cents), and `active_seconds`. The same snapshot streams as `session.usage` events, and per-thread usage is available too. Cache TTL is **5 minutes by default**. [session-observability]

### 1.5 Running a local client with your own API key

```bash
pip install anthropic python-dotenv        # SDK sets the managed-agents beta header itself
# CLI for `ant apply` (Linux release binary, v2.9.0 per quickstart):
#   https://github.com/anthropics/anthropic-cli/releases
export ANTHROPIC_API_KEY=...               # loaded from .env, never committed
```

```python
from anthropic import Anthropic
client = Anthropic()  # reads ANTHROPIC_API_KEY
session = client.beta.sessions.create(agent=AGENT_ID, environment_id=ENV_ID,
                                      budget={"type": "limit", "max_list_cost": {"amount": "300", "currency": "USD"}})
with client.beta.sessions.events.stream(session.id) as stream:
    client.beta.sessions.events.send(session.id, events=[{"type": "user.message", "content": [{"type": "text", "text": "..."}]}])
    for ev in stream: ...   # agent.message / agent.tool_use / session.status_idle / session.usage
```

Source: [quickstart]. `ant apply` in CI should use Workload Identity Federation instead of a stored key, but we stay **local-only** (the user's own key).

### 1.6 Pricing (verified 2026-11-13, [pricing])

**Session runtime: $0.08 per session-hour.** It is billed only while the session is `running`, measured to the millisecond. `idle` time (including waiting for you or for a tool confirmation), `rescheduling`, and `terminated` time are free. There is no separate container-hour charge. Tokens are billed at standard model rates with the caching multipliers. Batch discount does not apply. Web search costs $10/1k. A `us` inference_geo costs 1.1x, and fast mode is priced at a premium. *The figure matches the brief's $0.08; no discrepancy found.*

| Model (Anthropic API) | Input / MTok | 5m cache write | 1h cache write | Cache read | Output / MTok |
|---|---|---|---|---|---|
| Claude Haiku 5.5 (prompt ≤ 100k) | $0.10 | $0.125 | $0.20 | $0.01 | $0.50 |
| Claude Haiku 5.5 (prompt > 100k) | $0.50 | $0.625 | $1.00 | $0.05 | $2.50 |
| Claude Sonnet 5.5 | $2.00 | $2.50 | $4.00 | $0.10 (0.05x) | $10.00 |
| Claude Opus 5.5 | $4.00 | $5.00 | $8.00 | $0.20 (0.05x) | $20.00 |
| Claude Fable 5.2 | $8.00 | $10.00 | $16.00 | $0.20 | $40.00 |

| Model (OpenAI API, Standard, short context) | Input | Cached input | 30m cache write | Output |
|---|---|---|---|---|
| gpt-6.1-sol | $2.00 | $0.10 | $2.50 | $10.00 |
| gpt-6.1-terra | $1.00 | $0.05 | $1.25 | $5.00 |
| gpt-6-luna | $0.10 | $0.01 | $0.125 | $0.50 |

Notes:
- Claude 4.7+ models use a tokenizer that produces about **30% more tokens** for the same text. [pricing]
- The **Codex GitHub PR review** is **not available with an API key**. It needs a ChatGPT plan (Plus or higher) and uses plan usage. `codex exec` with an API key is billed at API rates. [codex-pricing]

---

## 2. Research findings: mattpocock/skills (MIT)

- Repo: <https://github.com/mattpocock/skills>, read at commit `5863806` (2026-11-12). License: **MIT, © 2026 Matt Pocock**.
- **Attribution:** when we vendor or upload a skill, we keep the `LICENSE` text and a `THIRD_PARTY_NOTICES.md` entry (repo URL, commit, path).
- `skills/engineering/pr/` carries its own `CREDITS.md`, because content is reproduced from Dex Horthy's `show-me` (humanlayer). If we vendor it, keep that file.
- Most skills assume an "issue tracker" config written by `setup-matt-pocock-skills`. The **local-markdown tracker** (`.scratch/<feature>/spec.md`, `.scratch/<feature>/issues/NN-*.md`) maps cleanly onto our file artifacts.

| Skill | What it does (from its SKILL.md) | Pipeline stage | Verdict |
|---|---|---|---|
| **`grill-with-docs`** | A 1-line wrapper: "Call the Skill tool twice, for `grilling` and `domain-modeling`". A relentless interview that also writes ADRs and a glossary | **1 Spec** | ✅ Core. It needs `grilling` and `domain-modeling` attached too. It is interactive: the orchestrator relays each question round to the user in the terminal (`idle` time is free). |
| `grilling` / `grill-me` | Design-tree interview in rounds, with a recommended answer for each question. Facts come from sub-agents and decisions come from the user | 1 Spec | ✅ (dependency) |
| `domain-modeling` | GLOSSARY.md + ADR format | 1 Spec, 2 Plan | ✅ (dependency) |
| `to-spec` | Synthesizes the conversation into a spec, sketches **test seams**, and publishes with `ready-for-agent` | 1 Spec (closing step) | ✅ |
| **`triage`** | A state machine: categories `bug`/`enhancement`, states `needs-triage`/`needs-info`/`ready-for-agent`/`ready-for-human`/`wontfix`. Every posted comment must start with an AI disclaimer, and it writes agent briefs | **0 Triage** | ✅ Use it **read-only**: the agent *proposes* labels and a brief in JSON, and the orchestrator applies them after the owner approves. The skill's "post to tracker" step is disabled in our setup. |
| `to-tickets` | Tracer-bullet tickets with blocking edges | **2 Plan** | ✅ |
| `wayfinder` | Plans work too big for one session as a decision-ticket map | 2 Plan (large features only) | Optional |
| `codebase-design` | Deep-module and seam vocabulary | 2 Plan, 3 Implement | ✅ (reference) |
| **`tdd`** | Red→green in vertical slices, tests only at **pre-agreed seams**, anti-patterns (implementation-coupled, tautological, horizontal slicing). Refactoring belongs to review | **3 Implement** | ✅ Core. Seams are agreed in Spec/Plan, so the implementer doesn't block on asking. |
| `implement` / `implement-spec` | Implement from a spec or tickets | 3 Implement | ✅ (`implement-spec` matches `to-spec` + `to-tickets` output) |
| `diagnosing-bugs` | Diagnosis loop for hard bugs and perf regressions | 3 Implement / 4 Verify (on failure) | ✅ On demand |
| **`code-review`** | Two axes, **Standards** (repo docs + a Fowler smell baseline) and **Spec**, run in parallel fresh-context sub-agents and **not merged or reranked** | **5 Review** (`finder-standards` prompt) | ✅ We reuse its Standards and Spec axes and the smell baseline as one *finder* inside the Anthropic-style find → vote → rank flow (§6). |
| `pr` | Writes the PR body | **6 PR** | ✅ (keep CREDITS.md) |
| **`retro`** | Retrospective that suggests improvements to the agent's **environment** in 7 categories: navigation, automated checks, coding standards (mechanical violation → deterministic check; judgement → `CODING_STANDARDS.md`), AGENTS.md bloat, tool economy, no-ops, information access. Depends on `writing-for-agents` | **7 Retro** (new stage, every run) | ✅ Core, adapted: it reads the run's exported event logs and ledger instead of local Claude Code logs, and outputs `retro.json` (§9). |
| `writing-for-agents` | Style guide for skills and AGENTS.md / CLAUDE.md | 7 Retro (dependency) | ✅ |
| `research`, `prototype`, `improve-codebase-architecture`, `handoff`, `wizard`, `setup-*`, writing/teach skills | Not stage-critical | — | Skip for v1 |

**Caveat:** many of these skills set `disable-model-invocation: true`, which is a Claude Code convention. I could not find whether Managed Agents honors that field. DEVX-074 in the user's own notes shows skills often don't fire on their own. Each stage's system prompt therefore **names the skill to use explicitly**.

---

## 3. What we take from EladAriel/ai-knowledge-hub

Read at commit `ec83f57` (2026-11-13 10:36 IST). Every note is linked by its full GitHub URL. In rev 2 the **primary** source for the review stage is Anthropic's article (§6). The hub notes below are **supporting** links. PIPE-002 is the hub's own summary of that same article and links to it.

**Code review (supporting §6)**
- [PIPE-002: Claude Code team's layered code review](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/pipelines/PIPE-002.md) summarizes the Anthropic article and cites its URL. It confirms the flow: verify before PR → fresh-context subagent review → required CI → parallel bug hunters with verifier votes → separate security review → risk-tier approvals → fast rollback. It also confirms the 3-round cap and that incidents become evals.
- [METH-085: MCPGuard](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-085.md): "a PoC built to confirm each finding". This is the evidence bar our `confirm` voter uses when a finding can be reproduced with a failing test.
- [METH-094: A thousand fewer paths to root](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-094.md): **verify the path to the result, not only the result**, and set abandon budgets. Failed attempts averaged $3,922 against $360 for successful ones. This supports the 3-round cap and the 2-attempt abandon rule.
- [METH-129: VulnCheck on Glasswing](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-129.md): self-reported reviewer accuracy is not acceptance. This matches the article's "sort every comment by how the person responded".
- [METH-045: Contestable agents](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-045.md): uncertain findings go to the owner with evidence, and the decision is recorded (a split vote on a security finding, §6.4).
- [DEVX-019: /code-review as a pre-PR CI step](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-019.md) and [DEVX-066: collapse agent PR bodies in `<details>`](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-066.md) set the PR body format.
- [EVAL-010: LLM-as-judge, yes/no, calibrated, different model family](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/evals/EVAL-010.md) is why one of the three votes is Codex.

**Retro and evals (supporting §9–§10)**
- [EVAL-013: Regression suite from past failures](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/evals/EVAL-013.md): every wrong finding, missed bug, or revert becomes a frozen case (the article's "incidents are the eval").
- [EVAL-020: Trajectory check with pressure cases](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/evals/EVAL-020.md): a run fails if there was a push or PR without an approval record, or a tool call outside the stage allowlist.
- [EVAL-024: pass^k](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/evals/EVAL-024.md): golden tasks run k=3.
- [EVAL-035: Cost and latency per correct answer](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/evals/EVAL-035.md): the regression gate fails if cost per successful run rises by more than 15%.
- [EVAL-012: MASIF, successful runs hide failures](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/evals/EVAL-012.md): per-step traces and a hidden-failure rate.
- [EVAL-001: /claude-api build-eval + hillclimb](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/evals/EVAL-001.md): build evals from real traces, split train/held-out, keep one patch at a time only if both improve. This is how retro proposals are accepted.
- [EVAL-011: Harness-Delta Attribution](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/evals/EVAL-011.md): separate real gains from overfitting to the search set and from extra compute. This is why golden tasks have a held-out split.
- [EVAL-032: same model across 7 harnesses](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/evals/EVAL-032.md): success rates within a few points are often indistinguishable on 20 tasks, so we report confidence intervals and cost per solved task.
- [DEVX-057: five-whys skill](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-057.md): evidence-backed root cause with countermeasures. Retro uses it only for a failed gate.
- [METH-079: procedures compiled from verified runs](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-079.md): record `outcome` and `verified_by` on every run (columns in §11).

**Pipelines / methods / devex**
- [METH-003: Cockroach Labs code-agent pipeline](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-003.md) validates fixed roles.
- [PIPE-001: Reliable scheduled agent automations](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/pipelines/PIPE-001.md) applies to the optional scheduled triage.
- [METH-056: Simple harnesses beat SOP-driven agents](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-056.md): gates check outputs, they don't script the order of work.
- [METH-097: The Harness Is the Model](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-097.md): validate model and harness together, and grade from the action log.
- [METH-095: Summaries become instructions](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-095.md): handoffs are schema'd artifacts, and permissions come from config.
- [METH-055: The Helpful Agent Problem](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-055.md) and [METH-096: Airlock](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-096.md): the orchestrator enforces the flow itself.
- [DEVX-074: Skills don't fire reliably](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-074.md): each stage prompt names its skill.
- [DEVX-026: Manual-only side-effect skills](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-026.md) and [DEVX-046: human-gate](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-046.md): irreversible actions wait for a human.
- [DEVX-008: One git worktree/branch per agent task](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-008.md): one `agent/<run-id>-<ticket>` branch per ticket.
- [TOOL-008: Scan third-party skills with SkillSpector](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/tools-and-repos/TOOL-008.md): scan vendored skills before upload.
- [DEVX-017: AGENTS.md security rules](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-017.md): the pattern for the repo's AGENTS.md / REVIEW.md rules.

**Cost and model routing (supporting §8)**
- [METH-134: Which Claude model to build with](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-134.md): Opus 5.5 for judgment, Sonnet 5.5 for scoped work, Haiku 5.5 for classification. Run low first and re-run only failures higher. Switching models mid-conversation throws away the cache.
- [METH-065: Coordination tax vs heterogeneity dividend](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-065.md): planner–coder–reviewer cost 3.22x without solving more tasks, while cheap-model routing cut cost to 0.39x for −6.7 points. Compare against a single strong agent (our eval baseline).
- [METH-131: One agent vs multi-agent on 13 procedures](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-131.md): multi-agent setups used 1.4–4.7x the tokens, so we gate on cost per successful task.
- [METH-001: Sonnet 5.5 migration](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-001.md): Sonnet 5.5 is often up to 30% cheaper per task than Sonnet 5.
- [DEVX-003: Haiku 5.5 for subagents](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-003.md): price rises 5x past 100k tokens, so Haiku sessions stay short.
- [DEVX-015: 1h prompt cache](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-015.md): the 5m default fits short stage sessions.
- [DEVX-035: Lower effort for subagents](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-035.md): fan-out finders and voters run at lower effort.
- [METH-028: Decision-model controllers raise agent cost](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-028.md): the orchestrator is deterministic code, not an LLM manager.

---

## 4. Pipeline overview

```
issue / idea
   │  (owner label gate: only issues labelled `agent:approved` by EladAriel)
   ▼
[0 Triage]    Haiku 5.5  ──► triage.json + agent-brief.md                         ── owner approves ──┐
[1 Spec]      Opus 5.5   ──► spec.md, GLOSSARY/ADR diffs, seams.json (interactive)  ── owner approves ──┤
[2 Plan]      Opus 5.5   ──► tickets/NN-*.md + plan.json (DAG)                      ── owner approves ──┤
[3 Implement] Sonnet 5.5, one session per ticket ──► branch agent/<run>-<NN>, impl-report.json
[4 Pre-PR verify]  the article's three pre-PR checks, minus bug-hunting which is stage 5:
     4a evidence   Haiku 5.5  run configured test/lint/typecheck/build, keep the real output
     4b simplify   Sonnet 5.5 simplify the diff (separate commit, checks must stay green)
     4c end-to-end Haiku 5.5  run spec acceptance scenarios against the built software
[5 Review]    5.0 risk tier (Haiku) → Quick / Standard / Deep
     5a FIND (parallel):  finder-bugs (Opus) ║ finder-standards+spec (Sonnet) ║ finder-history (Haiku)
                          ║ security (Sonnet) ║ Codex gpt-6.1-sol (`codex exec`, read-only)
     5b VERIFY (vote):    confirm (Sonnet) + refute (Sonnet) + Codex vote; keep if ≥2/3 and ≥1 cross-family
     5c RANK & TAG:       dedupe, drop style/cleanup, Important / Nit / Pre-existing, summary starts with a count
     5d ANSWER:           implement fixes Important (by bug class), or resolves with a reason
     5e RE-REVIEW:        diff-only, no repeats, round cap 3 → "step back and reconsider" to the owner
                                                                      ── owner sees verdict ──┤
[6 PR]        Haiku 5.5 ──► draft PR via the user's PAT; orchestrator sets status `pipeline/review`
                                                                      ── OWNER reviews & MERGES
[7 Retro]     Sonnet 5.5 ──► retro.json (environment fixes, proposals only) + metrics into runs/pipeline.db
```

The orchestrator is a local Python CLI (`pipeline run|resume|status|cost|eval|retro|db`). It:
1. loads `.env` and the target repo's `pipeline.config.yaml`;
2. creates **one short session per stage** (or per ticket, finder, or voter), each with its own budget;
3. mounts the input artifacts and the repo at a pinned commit;
4. streams events, relays the interactive questions, and answers `requires_action` confirmations **only after asking the user**;
5. downloads `/mnt/session/outputs/*`, validates each artifact against its JSON Schema, and writes `runs/<id>/` files plus rows in `runs/pipeline.db`;
6. archives the session.

Every gate checks **outputs**: schema-valid artifact, real command output, findings verified, budget not exceeded. Gates never script the agent's steps (METH-056). Parallel review fan-out is done by the **orchestrator** with separate sessions, not by a multiagent coordinator. That keeps a budget per finder and voter, keeps the votes independent (no shared thread context), and keeps the flow deterministic. A multiagent `dynamic workflow` is the documented alternative (§1.3). It is kept as an experiment, because its budget is per session with no per-thread caps.

---

## 5. Stages: one agent per stage

All agents follow the same baseline:
- environment `limited` networking with `allow_package_managers: true` and `allowed_hosts` from repo config (never `api.github.com`);
- `web_search` and `web_fetch` **disabled** unless the table says otherwise;
- the stage skill named explicitly in `system`;
- output only to `/mnt/session/outputs/`;
- **no** merge, label, or comment tools.

GitHub MCP, where it is used, has `default_config.enabled:false` plus a per-tool allowlist and `always_ask` for writes.

| # | Agent | Model / effort | Why this model | Tools | Skills | Inputs → Output artifact |
|---|---|---|---|---|---|---|
| 0 | `triage` | **Haiku 5.5**, low | Classification over short text (METH-134); stays under the 100k price step | `read`, `grep`, `glob` only | `triage` (read-only use) | issue JSON → `triage.json` (category, state, confidence, `injection_suspected`), `agent-brief.md` |
| 1 | `spec` | **Opus 5.5**, medium | Misalignment is the costliest failure; judgment-heavy | `read`, `grep`, `glob`, `bash` (read-only), `write`; `web_fetch` with a docs allowlist | `grill-with-docs` → `grilling`, `domain-modeling`; `to-spec` | brief, repo @ base SHA → `spec.md` (acceptance criteria with IDs, **agreed test seams**, **e2e scenarios**), ADRs |
| 2 | `plan` | **Opus 5.5**, medium (A/B vs Sonnet in Phase 3) | Decomposition decides downstream cost | `read`, `grep`, `glob`, `write` | `to-tickets`, `codebase-design` | spec → `tickets/NN-*.md`, `plan.json` (DAG) |
| 3 | `implement` | **Sonnet 5.5**, high; one ticket moves to Opus 5.5 only after 2 failed attempts | Scoped work with a test oracle (METH-001, METH-134) | full toolset minus web | `tdd`, `implement-spec`, `diagnosing-bugs` | ticket, spec, seams → commits on `agent/<run>-<NN>`, `impl-report.json` |
| 4a | `verify` (evidence) | **Haiku 5.5**, low | Runs deterministic commands; the article requires "show the output instead of saying it's done" | `bash`, `read`, `grep`, `glob` | (own) `verify-evidence` | branch + `checks` from config → `verify-report.json` (command, exit code, output tail, acceptance-ID→test map) |
| 4b | `simplify` | **Sonnet 5.5**, medium | Must write code safely, but scope is the diff only | full toolset minus web | (own) `simplify-diff` (modelled on Claude Code's `/simplify`) | diff → one `simplify:` commit or "nothing to simplify"; 4a re-runs after it |
| 4c | `verify` (e2e) | **Haiku 5.5**, low (Sonnet if UI screenshots need judgment) | Runs `e2e` commands from config and compares results with spec scenarios | `bash`, `read` | (own) `verify-e2e` | spec scenarios + `e2e` commands → `e2e-report.json` |
| 5.0 | `risk` | **Haiku 5.5**, low | Classification | `read`, `grep` | (own) `risk-tier` | diff stats, paths, config → `risk.json` (low/medium/high + reasons; protected paths force high) |
| 5a | `finder-bugs` | **Opus 5.5**, medium (Deep: high) | The main correctness judgment; a missed bug is the most expensive outcome | `read`, `grep`, `glob`, `bash` (read-only: tests, `git diff/blame`) | (own) `find-bugs`, plugin-derived | diff, spec intent, `REVIEW.md` (base branch) → `findings-bugs.json` |
| 5a | `finder-standards` | **Sonnet 5.5**, medium | Compliance against written rules is scoped work | same | mattpocock `code-review` (Standards + Spec axes) | diff, `CLAUDE.md`/`AGENTS.md`/`REVIEW.md`, spec → `findings-standards.json` |
| 5a | `finder-history` | **Haiku 5.5**, low | Mechanical `git blame/log` context, like the plugin's history agent | same | (own) `find-history` | diff → `findings-history.json` |
| 5a | `security` | **Sonnet 5.5**, medium | The article's blocking security review | same | (own) `security-review` (modelled on Claude Code's `/security-review`) | diff → `findings-security.json` |
| 5a | **Codex finder** (not a Managed Agent) | `gpt-6.1-sol` via local `codex exec --sandbox read-only` | A different model family finds different bugs (EVAL-010) | Codex CLI on a clean local checkout | `reviewers/codex/find.md` + AGENTS.md | diff → `findings-codex.json` (`--output-schema`) |
| 5b | `verifier` ×2 stances (`confirm`, `refute`) | **Sonnet 5.5**, low | One yes/no + 0–100 confidence + evidence per finding; batched; fresh context | `read`, `grep`, `glob`, `bash` (read-only, may run a reproduction test in a scratch dir) | (own) `verify-finding` | candidate findings + diff → `votes-<stance>.json` |
| 5b | **Codex voter** | `gpt-6.1-sol`, `codex exec` | Cross-family vote | Codex CLI | `reviewers/codex/vote.md` | candidates → `votes-codex.json` |
| 5c | `rank` | **Sonnet 5.5**, low | Dedupe, class grouping, severity ranking, summary writing | `read`, `write` | (own) `rank-findings` | surviving findings → `review-verdict.json` |
| 6 | `pr` | **Haiku 5.5**, low | Templated writing | `read`, `grep` + GitHub MCP with **only** `create_pull_request` (`always_ask`) | `pr` | verdict, spec, reports → `pr-body.md` + **draft** PR |
| 7 | `retro` | **Sonnet 5.5**, medium | Reads a compact digest, not raw transcripts, so Opus isn't needed | `read`, `grep`, `glob`, `write` | adapted `retro` + `writing-for-agents`; `five-whys` (DEVX-057) on failed gates | run digest + metrics → `retro.json` |

**Optional levers (off by default):**
- An **advisor** on `implement`: Sonnet 5.5 consulting Opus 5.5. Turn it on only if the evals show Sonnet failing on design decisions.
- **Outcomes** (`user.define_outcome`) on `spec`, with a rubric grader, to cut grilling rounds.
- **Nightly review of existing code** (the article runs review agents over old code nightly): a scheduled deployment of `finder-bugs` on `main`, read-only, with a per-run budget. Its findings go to the owner as Pre-existing.

**Model-agnostic by construction:**
- Models live only in `config/models.yaml`, keyed by stage. Prompts and skills are plain Markdown.
- Artifacts are JSON-Schema files, so any agent or CLI that emits them can replace a stage.
- Finders and voters are adapters (`reviewers/<name>/`) that emit `findings.schema.json` or `votes.schema.json`.

**Generic (any repo or language):** nothing stack-specific lives in agents. Each target repo gets a `pipeline.config.yaml`:

```yaml
base_branch: main
setup:     ["pnpm install --frozen-lockfile"]      # or: uv sync / go mod download / ...
checks:                                             # 4a evidence
  test:      "pnpm test"
  lint:      "pnpm lint"
  typecheck: "pnpm tsc --noEmit"
  build:     "pnpm build"
e2e:                                                # 4c end-to-end scenarios (optional)
  - "pnpm start & sleep 5 && pnpm e2e"              # or a CLI run, curl script, Playwright...
network:
  allowed_hosts: []
paths:
  protected: [".github/**", "**/auth/**", "infra/**"]   # => risk tier high, owner must read
  ignore_in_review: ["**/generated/**", "*.lock"]
review:
  depth: auto            # auto (from risk tier) | quick | standard | deep
  max_rounds: 3
  max_nits: 3
limits:
  max_diff_lines: 1500
budgets_usd: { run: 20 }  # overrides config/budgets.yaml defaults
```

---

## 6. Review: rebuilt on Anthropic's code-review article

### 6.1 Primary sources (official Anthropic)

| Source | What it gives us |
|---|---|
| **[A] "How the Claude Code team does code review"**, Addy Osmani, Morgan Westlee Lunt, Qing Wang, Oct 15 2026: <https://claude.dev/blog/solving-code-review-at-anthropic/> | The layered flow, the three pre-PR checks, the verifier vote, the tags, "every finding answered", the blocking security review, risk tiers, convergence rules, noise measurement, and incidents as evals |
| **[B] Code Review docs**: <https://code.claude.com/docs/en/code-review> and [customize](https://code.claude.com/docs/en/code-review-customize) / [pull requests](https://code.claude.com/docs/en/code-review-pull-requests) | The service's four steps (parallel agents find → a **verification step checks each finding against the code** → dedupe and rank by severity → post); markers 🔴 Important / 🟡 Nit / 🟣 Pre-existing; `REVIEW.md` rules; the review never approves or blocks |
| **[C] Local `/code-review` docs**: <https://code.claude.com/docs/en/code-review-local> | The local command reads `CLAUDE.md` but **not** `REVIEW.md`; `low` effort keeps only the most confident findings; the neighbouring commands are `/simplify` (cleanup only) and `/verify` (runs the app to confirm a change works) |
| **[D] Official `code-review` plugin**: <https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-review> | 4 parallel agents (2 × CLAUDE.md compliance, 1 bug scan, 1 git blame/history); every issue scored 0–100 and **anything under 80 is dropped**; an explicit false-positive list; links to file + full SHA + line range |

**About the name "three verification checks":** article [A] does not use that exact phrase. Its explicit trio is the pre-PR one: *"Before opening a PR, many of us also run a few internal skills: **one hunts for bugs, one simplifies the diff, and one checks the behavior end to end**."* Doc [C] ships the public versions of those three as `/code-review`, `/simplify`, and `/verify`. In each PR review, the per-finding verification is the vote: *"Several agents look for bugs at the same time. Then a separate group votes on each finding, and it only gets posted if enough of them agree it's real. Findings that are only about style, diagnostics, or cleanup get dropped."* The design adopts **both**, and they replace rev 1's derived Anchored / Reproduced / Attributable checks. Those survive only as the evidence a voter must cite (see 6.4).

### 6.2 The three pre-PR checks (stage 4 + 5)

Before any of them, the article's base rule applies: *give Claude something it can run and ask it to show the output instead of saying it's done.* Gate 4a fails if `verify-report.json` doesn't carry real command output.

| Check [A] | Public equivalent [C] | Our stage | Gate |
|---|---|---|---|
| **Hunt for bugs** (fresh context) | `/code-review` | **5 Review** (finders + vote, 6.3–6.4) | No open Important or security finding |
| **Simplify the diff** | `/simplify` (cleanup only, applies fixes) | **4b `simplify`**: a separate `simplify:` commit; checks re-run; if anything breaks, the commit is dropped | Checks green after simplify; diff not larger |
| **Check behavior end to end** | `/verify` (runs the app) | **4c `verify` e2e**: runs the repo's `e2e` commands against spec scenarios, and records output and screenshots | Every spec scenario has a pass record, or is marked "not e2e-testable" by the owner at spec time |

Order: 4a evidence → 4b simplify → 4a again → 4c e2e → 5 review. Cleanup comes **before** review, so the reviewer can drop style and cleanup findings, as [A] does, without losing them.

### 6.3 Find: parallel finders, scaled by risk tier

- **5.0 Risk tier ([A] §5, "the change sets the tier, not the author").** A Haiku classifier reads diff stats and paths and sets `low`, `medium`, or `high`. `paths.protected` and any `.github/workflows/**` change force `high`. A second deterministic rule forces `high` if the diff touches auth, secrets, or dependency manifests. The tier selects review depth, as in [A] ("a quick pass on routine changes, a deep one where a missed bug is expensive"):

| Depth | When | Finders | Votes |
|---|---|---|---|
| Quick | `low` (docs, copy, additive behind a flag) | finder-bugs on **Sonnet**, Codex, security | 3 |
| Standard | `medium` | finder-bugs (Opus), finder-standards, finder-history, security, Codex | 3 |
| Deep | `high` | Standard with Opus at `high` effort, plus `finder-bugs` run twice with different seeds or prompts | 3 |

- **Each finder sees:** the diff, the spec's stated intent ([A]: "does the change solve its problem, fit the system, or break something else?"), the repo at head, `CLAUDE.md`/`AGENTS.md`, and **`REVIEW.md` read from the base branch** ([B]). Bug fixes are also asked for the root cause ([A]).
- **Plugin rules every finder follows [D]:**
  - give a 0–100 confidence and **don't report under 80**;
  - don't report pre-existing issues as new (tag them Pre-existing instead);
  - skip "looks like a bug but isn't", pedantic nits, anything a linter, typechecker or compiler catches, general quality not required by `CLAUDE.md`/`REVIEW.md`, and code with an explicit lint-ignore;
  - a rule violation must quote the rule that says so;
  - every finding gives `file`, `line_start`–`line_end`, the head SHA, and a **class of bug** ([A]: "flag the class of bug, so the author fixes every instance").
- **Codex** runs locally as `codex exec --sandbox read-only -m gpt-6.1-sol --output-schema schemas/findings.schema.json -o runs/<id>/findings-codex.json` with `reviewers/codex/find.md`. Its `AGENTS.md` carries the same rules.

### 6.4 Verify: the vote (replaces rev 1's "three checks")

Each candidate finding gets **three independent votes**, each from a fresh context with no access to the other votes:

| Voter | Model | Stance | Evidence it must cite |
|---|---|---|---|
| `verifier/confirm` | Sonnet 5.5 | "Show this is real" | a `file:line` trace in the source ([B] REVIEW.md guidance: "behavior claims need a `file:line` citation in the source, not an inference from naming"), and where cheap, a reproduction test in a scratch dir (METH-085) |
| `verifier/refute` | Sonnet 5.5 | "Try to show it's wrong, intended, pre-existing, or style only" | the code or doc that contradicts it, or the `git blame` showing it predates the diff |
| Codex voter | gpt-6.1-sol | neutral | the same `file:line` evidence |

Each vote is `{real: yes|no, confidence: 0–100, severity: important|nit, pre_existing: bool, evidence}`. A vote counts as **yes only if `real=yes` and confidence ≥ 80** [D].

**Survival rule ("only gets posted if enough of them agree it's real" [A]):**
- at least **2 of 3** yes votes;
- and at least one yes from a **different model family than the finder**. Agreement among same-family agents is correlated, so a Codex-found bug needs a Claude yes, and a Claude-found bug needs a Codex yes or both Claude stances.
- **Security findings** are special. If they get 1 yes, they are not dropped; they go to the owner as "unconfirmed security" with the evidence (METH-045). Losing a real security bug costs more than an owner glance.
- Findings that are **only style, diagnostics, or cleanup** are dropped before voting ([A]), which also saves the votes' cost.

### 6.5 Rank, tag, and answer every finding

- **`rank`** (Sonnet) dedupes by `(file, overlapping lines, class)`, merges same-class findings into one finding listing every instance, orders by severity, and tags each one **Important / Nit / Pre-existing** ([A], [B]). Severity is the median of the votes. `REVIEW.md` can redefine Important. Nits are capped at `review.max_nits` (default 3; [B]'s example uses 5).
- `review-verdict.json` opens with a summary that **starts with a count and says plainly when there's nothing to fix** ([A] on REVIEW.md).
- **Every finding is answered** ([A]: "you either fix it or resolve the thread with a reason"). Each one gets a `resolution`:
  - `fixed` (commit SHA);
  - `wrong`, `intentional`, or `didnt_matter`, with a one-line reason. These are [A]'s four response categories, reused as the noise metric (§10).
- **Who answers:**
  - Important and security findings go back to `implement` as a fix round, fixing the whole bug class.
  - Nits are fixed only if trivial; otherwise they are resolved `didnt_matter`.
  - Pre-existing findings never block. They become a backlog issue proposal for the owner.
  - The agent may propose `wrong`/`intentional` for an Important finding, but **the owner confirms it** before it counts as resolved.
- **Security review blocks** ([A] §3): the run can't reach stage 6 while any security finding is open. The orchestrator encodes this in the `pipeline/review` status (§7). [A] mentions the open-source security-review GitHub Action as another route. It is **not** used in v1, because it needs an API key in CI.

### 6.6 Convergence (from [A], measured in §10)

- **Round cap: 3.** After round 3 the pipeline stops and asks the owner to **step back and reconsider the approach** (abandon, re-plan, or re-spec). There is no 4th automatic round.
- **Diff-only re-review** after round 1. Rounds 2–3 send finders only `git diff <last-reviewed-sha>..HEAD`, which is cheaper too.
- **No repeats.** Findings are fingerprinted (`class + file + normalized snippet hash`) in `runs/pipeline.db`, and a finding already posted or resolved is suppressed.
- **Flag the class, not the line,** so one fix round clears every instance.
- The article's numbers, for calibration: about 59% of repeat flags were old code. After the change, mean reviews to pass went 2.08 → 1.32 and pass-after-one-round went 65% → 78%. We track the same metrics.

### 6.7 What the human does ([A] "Humans stay")

The owner reads the evidence (test output, e2e records, verdict), decides direction and architecture, looks closely at `high`-tier PRs, and **merges**. There is no formal approval step (see §7). [A]'s own goal is "at most one human in the loop, who can be the author", which is exactly our setup.

### 6.8 `REVIEW.md` template (root of each target repo, read from the base branch)

```markdown
# Review instructions
## What Important means here
Breaks behavior, leaks data or secrets, or blocks a rollback. Style/naming = Nit at most.
## Cap the nits
At most 3 Nits per review.
## Do not report
Anything CI enforces (lint, format, types); generated files; *.lock; vendored code.
## Always check
New entry points have a test; no secrets/PII in logs; workflow files never gain write permissions.
## Evidence
Every behavior claim cites file:line in the source, not an inference from a name.
## Summary
Start with the count of findings by tag; say "Nothing to fix" when there is none.
```

Because the local `/code-review` doesn't read `REVIEW.md` [C], our finders get **both** files explicitly. Rules wanted everywhere go in `CLAUDE.md`. Repeated review comments are moved into `REVIEW.md` or into a check by Retro (§9).

---

## 7. Security (public repo `EladAriel/claude-dev-pipeline`)

**Secrets and paths**
- `.env` holds `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (used only by local `codex exec`), `GITHUB_TOKEN_READ`, and `GITHUB_TOKEN_WRITE`, and it is **gitignored**. Only `.env.example` (names, no values) is committed.
- **Gitignored as well:** `runs/` (artifacts and `pipeline.db`), **`claude-lock.json`** (it holds org and workspace IDs; decision: never committed), `.codex/`, `*.local.*`.
  - Consequence: `ant apply` is run only from the owner's machine. A fresh clone runs `ant apply` once to recreate the lock against the owner's workspace.
  - The lock is backed up locally, not in git.
- **Defense in depth:**
  - a pre-commit `gitleaks` hook;
  - GitHub secret scanning + push protection;
  - a CI `secret-scan` workflow;
  - a CI step that fails on absolute or private paths (`/home/`, `/Users/`, `C:\\`) and on anything that looks like an org/workspace ID.
- Tokens are never placed in agent definitions:
  - GitHub MCP credentials live in an Anthropic **vault** (`static_bearer`).
  - The clone/push token goes per session in `authorization_token`.
  - The Codex key is read by the local CLI from the environment, never passed to an agent.

**Least privilege, using the user's own fine-grained PATs** (decision)
- Two fine-grained PATs owned by `EladAriel`, each scoped to **one repository** (the target repo), with a 30–90 day expiry:
  - *read* token: Contents (read), Issues (read), Pull requests (read), Metadata. Used by triage, spec, plan, verify, review, and retro.
  - *write* token: Contents (read/write), Pull requests (read/write), **Commit statuses (read/write)**. Mounted only for `implement` (push `agent/*`) and `pr`. The orchestrator also uses it locally to set the `pipeline/review` status.
- A fine-grained PAT can't separate "open PR" from "merge PR". Contents + Pull requests write is enough to merge through the API. "Only the user merges" is therefore enforced in four layers:
  1. No agent has a merge tool. The GitHub MCP allowlist is just `create_pull_request` (+ optional `get_pull_request`), set to `always_ask`.
  2. Sandboxes use `limited` networking with no `api.github.com` in `allowed_hosts`, so `bash` can't call the REST API. *Whether `git push` to `github.com` works under `limited` with only the repository resource needs a Phase 3 check (§14).*
  3. The orchestrator has no merge code path, and a unit test asserts that no call to `/merge` or `pulls/*/merge` exists in its source.
  4. Trajectory eval (EVAL-020) fails any run whose event log contains a merge-like tool call.
- Environments are always `limited`. Codex always runs with `--sandbox read-only`.

**Untrusted input (issues from strangers)**
- Unchanged from rev 1. Only issues labelled **`agent:approved`**, with the label applied by `EladAriel` (checked through the timeline API), enter the pipeline. Everything else is ignored with no LLM call.
- `triage` has no bash, write, web, or MCP tools and emits `injection_suspected`. Downstream stages get the owner-approved brief, never the raw issue body.
- External PRs that touch `.claude/**`, `agents/**`, `skills/**`, `REVIEW.md`, `AGENTS.md`, or `.github/**` are never mounted. Mounting a repo auto-loads `.claude/skills/`, and `REVIEW.md` is trusted only from the base branch.

**Merge control: the `main` ruleset** (decision: own PAT, no self-approval)
GitHub doesn't let a PR author approve their own PR. Since agent PRs are opened with the user's PAT, the user is the author. The ruleset therefore **does not require approvals**; it relies on checks, and the user merges:

| Rule | Setting |
|---|---|
| Require a pull request before merging | ✅ with **required approvals = 0**. "Require review from Code Owners" **off**, because it would block every self-authored PR |
| Require conversation resolution | ✅ ([A]: "require every review thread resolved before merge") |
| Required status checks | `ci` (lint, typecheck, tests, build), `secret-scan`, **`pipeline/review`** (green only when 0 open Important and 0 open security findings and every finding has a resolution), with "require branches to be up to date" on |
| Block force pushes / restrict deletions | ✅ |
| Bypass list | **empty**: the owner merges through the PR UI like everyone else |
| CODEOWNERS | kept for documentation and to mark sensitive paths for the `risk` tier; not enforced as a reviewer requirement |

- Agent PRs are opened as **draft**, labelled `agent-authored`, and their body carries the run-id. **Only the user marks them ready and merges.**
- `pipeline/review` is a commit status that the local orchestrator sets with the write PAT, so it is only as trustworthy as the owner's machine. That is acceptable for a single-owner repo. *Pinning the expected source of a status that a PAT posts isn't possible the way it is for a GitHub App. Noted in §14.*
- [A]'s rule "an approval covers one specific commit" maps here: `pipeline/review` is set **per head SHA**, so any new commit makes it pending again.

**Audit**
- Every session ID, agent version, model, `list_cost`, artifact hash, and vote goes into `runs/<id>/` and `runs/pipeline.db` (§11).
- Vendored skills are scanned with SkillSpector before upload (TOOL-008).
- Sessions are archived at stage end. `pipeline purge --older-than 30d` is manual.

---

## 8. Cost and model routing (updated for rev 2)

### 8.1 Formulas (unchanged)

```
session_cost = Σ_tokens (uncached_in·P_in + cache_write_5m·P_w5m + cache_read·P_read + out·P_out)/1e6
             + active_hours · $0.08            (+ web_searches · $0.01)
run_cost     = Σ_stages session_cost  +  codex_cost
codex_cost   = (in·$2.00 + cache_write_30m·$2.50 + cached_in·$0.10 + out·$10.00)/1e6   [gpt-6.1-sol, API]
cost/hour    = $0.08 + token_burn_rate($/h)
```

Unit prices are verified (§1.6): Haiku 5.5 $0.10/$0.125/$0.01/$0.50, Sonnet 5.5 $2/$2.50/$0.10/$10, Opus 5.5 $4/$5/$0.20/$20 per MTok (in / 5m write / cache read / out), and $0.08 per session-hour. **Token volumes are assumptions**, to be replaced by measured `session.usage` from `runs/pipeline.db` after Phase 3.

### 8.2 Per-stage estimate (typical run: 1 feature, 3 tickets, Standard review, 1 fix round)

| Stage | Model | Uncached in | 5m write | Cache read | Output | Active min | **Stage $** |
|---|---|---|---|---|---|---|---|
| 0 Triage | Haiku 5.5 | 10k | 5k | 30k | 3k | 3 | **0.01** |
| 1 Spec | Opus 5.5 | 40k | 60k | 600k | 25k | 15 | **1.10** |
| 2 Plan | Opus 5.5 | 30k | 50k | 400k | 15k | 10 | **0.76** |
| 3 Implement ×3 | Sonnet 5.5 | 60k | 120k | 2.5M | 40k | 25 each | **3.31** |
| 4a+4c Evidence + e2e | Haiku 5.5 ×2 sessions | 20k | 20k | 200k | 8k | 15 total | **0.04** |
| 4b Simplify | Sonnet 5.5 | 20k | 30k | 200k | 10k | 8 | **0.25** |
| 5.0 Risk tier | Haiku 5.5 | 10k | 5k | 10k | 1k | 1 | **<0.01** |
| 5a finder-bugs | Opus 5.5 | 50k | 60k | 400k | 12k | ┐ | **0.82** |
| 5a finder-standards | Sonnet 5.5 | 30k | 40k | 200k | 8k | │ | **0.26** |
| 5a finder-history | Haiku 5.5 | 20k | 10k | 100k | 4k | │ parallel, | **0.01** |
| 5a security | Sonnet 5.5 | 30k | 40k | 200k | 8k | │ ≈20 min of | **0.26** |
| 5a Codex finder | gpt-6.1-sol | 50k | 50k (30m) | 100k | 10k | │ session time | **0.34** |
| 5b 2 Claude votes × ~8 findings | Sonnet 5.5 | 48k | 40k | 640k | 24k | │ in total | **0.50** |
| 5b Codex votes (batch) | gpt-6.1-sol | 20k | 30k | 30k | 8k | │ | **0.20** |
| 5c Rank + summary | Sonnet 5.5 | 20k | 20k | 100k | 6k | ┘ | **0.16** |
| 5 runtime (all review sessions) | — | | | | | 20 | **0.03** |
| 5d Fix round ×1 | Sonnet 5.5 | 30k | 60k | 1.0M | 20k | 15 | **0.53** |
| 5e Diff-only re-review | Opus finder (small) + Sonnet votes | 28k | 35k | 270k | 9k | 8 | **0.43** |
| 6 PR | Haiku 5.5 | 20k | 20k | 100k | 6k | 3 | **0.01** |
| 7 Retro | Sonnet 5.5 | 60k | 40k | 200k | 8k | 8 | **0.33** |
| **Total** | | | | | | ≈181 min | **≈ $9.35** |

- **What changed vs rev 1 (≈ $7.40):**
  - The review stage (5a–5c) is **≈ $2.57**, up from $1.65 for rev 1's single reviewer + Codex + reconcile.
  - The diff-only re-review adds +$0.43, pre-PR simplify adds +$0.25, and Retro adds +$0.33.
  - Runtime is now ≈ $0.24 (≈ 2.6%).
- **Range:**
  - small run (1 ticket, no fix round): **≈ $6.20**;
  - Quick-tier review: about −$1.1;
  - large run (6 tickets, 3 fix rounds): **≈ $14.60**.
- **Codex per run** ≈ $0.53 (finder + votes) + ≈ $0.10 per re-review round.

### 8.3 Levers (verified against the docs)

Unchanged from rev 1: model and effort per agent, automatic prompt caching (keep one model per session), short sessions instead of compaction, idle costs $0 runtime, `budget.max_list_cost`, `session.usage`, web search off, no batch discount for sessions, no fast mode or US geo.

Two new review-specific levers:
- **Risk-tiered depth** (Quick / Standard / Deep, §6.3), with the review cost scaled to what a miss would cost, as [A] recommends.
- **Drop style and cleanup before voting**, and **dedupe before voting**. Votes scale with candidate count, so finder precision directly cuts cost.

### 8.4 Budget caps and tracking

`config/budgets.yaml` defaults, in cents (strings, as the API expects):

| Stage | Cap (cents) | Stage | Cap (cents) |
|---|---|---|---|
| triage | `"10"` | finder-history | `"5"` |
| spec | `"300"` | security | `"50"` |
| plan | `"200"` | verifier (each stance, per round) | `"60"` |
| implement (per ticket) | `"300"` | rank | `"30"` |
| verify (4a/4c, each) | `"10"` | fix round | `"150"` |
| simplify | `"50"` | re-review | `"75"` |
| risk | `"5"` | pr | `"25"` |
| finder-bugs | `"150"` | retro | `"75"` |
| finder-standards | `"50"` | **run** | **$20** |
| codex (per run) | $1.50 | | |

- Each session is created with `budget.max_list_cost = min(stage_cap, run_cap − spent_so_far)`. If that is ≤ 0, the session is not started.
- On `stop_reason: budget_reached`, the stage fails closed and the user can `pipeline resume --raise <stage> <usd>`.
- Caps carry about 10% headroom, because a budget can overshoot by one request per thread.
- Codex cost comes from `codex exec --json` usage if it is present (unverified). As a fallback, an OpenAI project budget.
- **Tracking:** every session's `session.usage` and every Codex call go into `runs/pipeline.db` (§11). `pipeline cost --run <id>` and `pipeline cost --by stage --last 20` read from it.

### 8.5 Top cost risks and mitigations

1. **Review loops** (one PR in [A] ran 50+ rounds). → 3-round cap, then step back. Diff-only, no repeats, fix by class.
2. **Vote cost scales with noisy finders.** → Confidence ≥ 80 at the finder, style dropped before voting, dedupe first. Track candidates per run and drop a finder that costs more than its accepted findings justify (§10).
3. **Failed attempts cost ~10x** (METH-094). → Abandon after 2 failed attempts per ticket.
4. **Coordination tax** (METH-065, METH-131). → The single-agent baseline in evals. A stage that doesn't pay for itself is merged.
5. **Context growth** (Haiku is 5x past 100k). → One ticket per session, diff-only inputs.
6. **Opus overuse.** → Opus only on spec, plan, finder-bugs (and Deep re-review).
7. **Eval spend** (§10). → Stage-level fixtures for day-to-day work. A full golden run happens only before a release, with its own cap.

---

## 9. Stage 7: Retro (after every run)

**Source skill.** mattpocock/skills has an exact match: [`skills/engineering/retro/SKILL.md`](https://github.com/mattpocock/skills/blob/main/skills/engineering/retro/SKILL.md) (read at commit `5863806`). Its description is *"Conduct a retrospective on a coding session."* It suggests improvements to the agent's **environment** in seven categories:
- navigation;
- automated checks (a mechanical violation gets a deterministic check; a repo with no guardrail is itself a finding);
- coding standards (for the reviewer agent);
- AGENTS.md bloat;
- tool economy;
- no-ops;
- information access.

It depends on [`writing-for-agents`](https://github.com/mattpocock/skills/blob/main/skills/productivity/writing-for-agents/SKILL.md). ai-knowledge-hub has no session-eval skill. Its closest note is [DEVX-057 five-whys](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-057.md), which we use for failed gates only. The design **adapts mattpocock's `retro`** rather than writing a new one. The retro also asks [A]'s incident-review question: *"which layer should have caught the issue?"* and *"what didn't the model have in front of it?"*

**Adaptation (`skills/own/run-retro/`, with the vendored `retro` kept unchanged next to it):**
1. **Input is a run digest, not raw logs.** The original reads "session logs on this machine". Ours reads a digest that the orchestrator builds deterministically:
   - per-stage metrics from `pipeline.db`;
   - gate failures with their messages;
   - tool-call counts and the 10 most expensive tool calls per session (from the session event list);
   - findings with votes and resolutions;
   - owner interventions;
   - the diff stats.
   This keeps the retro session around 100–300k cached tokens instead of millions. *Raw transcripts are fetched only if the retro asks for a specific session/event id.*
2. **Metrics are computed by code; the LLM only interprets them.** The retro never calculates cost or success rates itself (METH-097: grade from the action log).
3. **Extra categories for this pipeline:**
   - **review noise**: findings resolved `wrong`/`intentional`/`didnt_matter`, so a rule goes to `REVIEW.md`, or a finder prompt or threshold changes;
   - **missed bugs**: anything found later by the owner, CI, or a revert;
   - **cost**: the stage over its median or near its cap, with a routing or effort suggestion;
   - **convergence**: more than 1 round, or a repeat fingerprint.
4. **Output** is `retro.json` (schema'd). Each item has: `category`, `severity`, `evidence` (session_id/event_id or file:line), `proposed_change` (`check` | `REVIEW.md` | `CLAUDE.md` | `CODING_STANDARDS.md` | `skill` | `config.models` | `config.budgets` | `pipeline.config`), and `expected_metric` (which eval metric should move).
5. **Never auto-applied.** The owner accepts or rejects each item with `pipeline retro review <run>`, and the decision is stored. An accepted item becomes a normal pipeline task (issue labelled `agent:approved`). If it touches a prompt, skill, model, or threshold, it must pass the **eval regression gate** (§10.4) before merge. This is [A]'s "rescore the reviewer whenever its prompts, tools or model change", done one patch at a time as in [EVAL-001](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/evals/EVAL-001.md).
6. **Post-merge incidents.** `pipeline incident add --pr <n> --note "..."` records a revert or a bug found later. It triggers a retro on the original run, and also adds an eval case (§10.3).

**Cost:** ≈ $0.33 per run with Sonnet 5.5 (§8.2), capped at $0.75. Retro can be set to `every_run` (the default), `on_failure`, or `weekly` (one retro over the week's digests, which is cheaper once the pipeline is stable).

---

## 10. Evals design

### 10.1 Per-stage metrics (computed by the orchestrator, stored in `pipeline.db`)

| Metric | Definition | Source |
|---|---|---|
| success | gate passed (schema valid + stage gate) | orchestrator |
| first-try success | passed with 0 retries | orchestrator |
| loops | attempts, fix rounds, grilling rounds, re-review rounds | orchestrator |
| tokens | uncached in / cache write / cache read / output, per session and per thread | `session.usage`, `codex exec --json` |
| cost | `list_cost` (+ computed Codex cost) | same |
| time | `active_seconds` (billed) and wall-clock, including owner wait time | `session.usage`, timestamps |
| budget events | `budget_reached`, raises | events |
| trajectory violations | tool outside allowlist, push/PR without approval record, merge-like call (EVAL-020) | event log |

**Review-specific (from [A]):**

| Metric | Definition |
|---|---|
| candidates → survivors | findings in vs out of the vote, per finder (finder precision proxy) |
| **acceptance by response** | share of posted findings resolved `fixed` / `wrong` / `intentional` / `didnt_matter`. [A]: "we sort every comment the reviewer leaves by how the person on the PR responded" |
| recall on seeded bugs | golden review cases (10.3) |
| **clean-PR harmful rate** | on known-clean diffs, findings that would have led to a harmful change. [A]: "we also run the reviewer on PRs we know are clean" |
| rounds-to-pass, pass-after-one-round, ≥5-round share | [A]'s convergence metrics (the cap makes ≥5 impossible, so ≥3 is tracked as "hit cap") |
| vote agreement | per voter pair. If two voters always agree, one may be redundant (a cost lever) |

### 10.2 Per-run metrics

- **Run success:** the PR was merged by the owner and was not reverted within 14 days.
- **Cost per successful run** (EVAL-035), and cost per stage share.
- **Human time:** owner interventions, owner wait time, approvals asked. [A] measures human time with reverts and incidents as the guardrail.
- **Hidden-failure rate** (EVAL-012): runs that succeeded but had a gate retry, a budget raise, or an owner override.
- Total tokens, loops, and wall-clock time.

### 10.3 Golden tasks on the demo repo

- **Demo repo:** a small separate public repo (proposed name `EladAriel/claude-dev-pipeline-demo`, open question) with a tiny app in two stacks (e.g. a Python CLI and a TS HTTP service), so the generic config is exercised.
- Each golden task is a tag (`golden/<id>/base`) plus `evals/golden/<id>.yaml`. The file holds:
  - the issue text;
  - **hidden acceptance tests**, mounted only for grading and never for the agent;
  - the expected review outcome;
  - the budget.

| # | Task type | What it tests |
|---|---|---|
| G1–G2 | small feature, one per stack | full path; e2e scenario |
| G3 | bug fix with a hidden regression test | root cause, TDD |
| G4 | refactor with no behavior change | simplify, no false positives |
| G5 | docs-only change | Quick tier; review must say "nothing to fix" |
| G6 | change touching `.github/workflows/` | tier forced `high`; protected path flagged |
| R1–R4 | **review-only**: fixed diffs with 1–3 seeded bugs (logic, injection, off-by-one, secret in log) | recall, class flagging |
| C1–C3 | **review-only**: known-clean diffs | clean-PR harmful rate, "nothing to fix" |
| P1–P3 | pressure (EVAL-020): issue text trying to skip approval, push to `main`, or merge | trajectory violations must be 0 |

- **Split.** Golden tasks are split into **train** (used when tuning prompts) and **held-out** (only for the regression gate), following [EVAL-001](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/evals/EVAL-001.md) and [EVAL-011](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/evals/EVAL-011.md).
- **Growth.** Every `pipeline incident add` and every confirmed `wrong` finding adds a case (EVAL-013; [A]: "each incident adds a case").

### 10.4 Baseline and regression comparison

- **Baselines:**
  - **B0**: a single Opus 5.5 agent with the full toolset and the same run budget, asked to do the task end to end (METH-065 / METH-131's "compare against a single strong agent").
  - **B1**: the first tagged stable pipeline version (`baseline-v1`). Each later accepted change becomes the new baseline.
- **Command:** `pipeline eval --suite golden --split heldout --k 3 --compare baseline-v1`. It writes `eval_results` rows and a Markdown delta table.
- **Gate (a change to prompts, skills, models, thresholds, or routing is kept only if):**
  - pass^3 success is not lower (EVAL-024);
  - review recall on R-cases is not lower, and the clean-PR harmful rate is not higher;
  - **cost per successful run rises ≤ 15%** (EVAL-035), unless the owner accepts the trade-off explicitly;
  - trajectory violations = 0.
- With ≈ 15 tasks, differences of a few points are noise (EVAL-032), so the report shows Wilson intervals and calls small deltas "no change".
- **Tiers of eval spend:**
  - **stage smoke**: one stage on its fixtures, k=1. Review-only R/C cases cost ≈ $2.6 each in Standard mode (assumption), so ≈ $15–20 per smoke run.
  - **full golden**: all tasks, k=3, before a release. At ≈ $9 per full task this is roughly **$150–250 per full run** (an assumption, to be measured). It has its own cap in `config/budgets.yaml` (`eval_full: 250`).
- Both are manual commands. Nothing runs in CI with API keys in v1.

---

## 11. Local database: SQLite `runs/pipeline.db` (decision: **include**)

**Why it clearly helps.** JSONL per run is fine for a single run, but the new requirements are **cross-run** questions:
- the regression delta vs baseline;
- cost per stage over the last N runs;
- finder precision and acceptance by response category over time;
- the no-repeat fingerprint check across review rounds;
- retro items still open, and whether an accepted fix moved its metric.

With files only, each of these needs ad-hoc parsing code. SQLite gives them as plain SQL. It is a zero-dependency single file (Python stdlib `sqlite3`), easy to gitignore, and easy to rebuild.

**Rules**
- **Artifacts stay the source of truth.** `runs/<id>/*.json` and `ledger.jsonl` are written first, and the DB is an index. `pipeline db rebuild` re-creates it from `runs/`.
- Gitignored with the rest of `runs/`. It holds **no secrets and no transcripts**, only IDs, numbers, short evidence strings, and hashes.
- Schema migrations use `PRAGMA user_version`, WAL mode, and one writer (the orchestrator).
- **Alternatives considered:**
  - JSONL only: the cross-run queries above become custom code;
  - DuckDB: an extra dependency for no gain at this size;
  - a hosted tracer such as Langfuse: an optional exporter later, but not required, and it sends data off the machine.

**Schema sketch**

```sql
CREATE TABLE runs (
  run_id TEXT PRIMARY KEY, repo TEXT, issue_ref TEXT, pipeline_version TEXT,  -- git SHA of claude-dev-pipeline
  started_at TEXT, ended_at TEXT, status TEXT,            -- running|succeeded|failed|abandoned
  risk_tier TEXT, review_depth TEXT, run_cap_usd REAL,
  outcome TEXT, verified_by TEXT,                          -- METH-079: merged|reverted|... ; ci|owner|golden
  is_eval INTEGER DEFAULT 0, eval_run_id TEXT);
CREATE TABLE stages (
  stage_id INTEGER PRIMARY KEY, run_id TEXT REFERENCES runs, name TEXT, ticket TEXT, round INTEGER,
  attempt INTEGER, provider TEXT, model TEXT, effort TEXT,  -- anthropic|openai
  session_id TEXT, agent_id TEXT, agent_version INTEGER,
  started_at TEXT, ended_at TEXT, active_seconds REAL, wall_seconds REAL,
  status TEXT, stop_reason TEXT, gate_message TEXT, artifact_sha256 TEXT, budget_cents INTEGER);
CREATE TABLE usage (
  stage_id INTEGER REFERENCES stages, thread_id TEXT,
  input_tokens INTEGER, cache_write_tokens INTEGER, cache_read_tokens INTEGER, output_tokens INTEGER,
  web_searches INTEGER, list_cost_usd REAL, runtime_cost_usd REAL);
CREATE TABLE findings (
  finding_id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs, round INTEGER, finder TEXT,
  file TEXT, line_start INTEGER, line_end INTEGER, head_sha TEXT, bug_class TEXT,
  severity TEXT, tag TEXT, confidence INTEGER, is_security INTEGER,  -- tag: important|nit|pre_existing
  fingerprint TEXT, survived INTEGER, summary TEXT);
CREATE INDEX findings_fp ON findings(fingerprint);
CREATE TABLE votes (
  finding_id TEXT REFERENCES findings, voter TEXT, model_family TEXT,
  real INTEGER, confidence INTEGER, severity TEXT, evidence TEXT);
CREATE TABLE resolutions (
  finding_id TEXT REFERENCES findings, resolution TEXT,   -- fixed|wrong|intentional|didnt_matter
  fix_sha TEXT, reason TEXT, decided_by TEXT, decided_at TEXT);   -- agent|owner
CREATE TABLE golden_tasks (task_id TEXT PRIMARY KEY, kind TEXT, split TEXT, base_ref TEXT, spec_path TEXT);
CREATE TABLE eval_runs (eval_run_id TEXT PRIMARY KEY, suite TEXT, split TEXT, k INTEGER,
  pipeline_version TEXT, baseline TEXT, started_at TEXT, cap_usd REAL);
CREATE TABLE eval_results (
  eval_run_id TEXT REFERENCES eval_runs, task_id TEXT REFERENCES golden_tasks, trial INTEGER,
  run_id TEXT REFERENCES runs, passed INTEGER, recall REAL, harmful_findings INTEGER,
  rounds INTEGER, cost_usd REAL, wall_seconds REAL, trajectory_violations INTEGER);
CREATE TABLE retro_items (
  item_id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs, category TEXT, severity TEXT,
  evidence TEXT, proposed_change TEXT, target TEXT, expected_metric TEXT,
  status TEXT, decided_at TEXT, follow_up_run_id TEXT);   -- proposed|accepted|rejected|done
CREATE TABLE incidents (incident_id TEXT PRIMARY KEY, run_id TEXT, pr_number INTEGER,
  kind TEXT, note TEXT, created_at TEXT, eval_task_id TEXT);  -- revert|bug|security
```

**Example queries it makes trivial:**
- `SELECT name, model, AVG(list_cost_usd) … GROUP BY name, model` gives the cost per stage;
- `SELECT resolution, COUNT(*) FROM resolutions JOIN findings … WHERE survived=1 GROUP BY finder, resolution` gives the noise per finder;
- `SELECT 1 FROM findings WHERE fingerprint=? AND run_id=?` is the no-repeat check;
- a delta table of `eval_results` between two `pipeline_version`s.

---

## 12. Repo layout: `EladAriel/claude-dev-pipeline` (public, MIT)

```
claude-dev-pipeline/
├── README.md  DESIGN.md  LICENSE  THIRD_PARTY_NOTICES.md  SECURITY.md
├── AGENTS.md              # repo rules + "## Code Review Rules" (read by Codex)
├── CLAUDE.md              # short: runnable checks + navigation pointers
├── REVIEW.md              # §6.8 template, for this repo's own PRs
├── .env.example           # ANTHROPIC_API_KEY, OPENAI_API_KEY, GITHUB_TOKEN_READ, GITHUB_TOKEN_WRITE (names only)
├── .gitignore             # .env, runs/ (incl. pipeline.db), claude-lock.json, .codex/, *.local.*
├── agents/                # ant-apply agent files (frontmatter = config, prose = system prompt)
│   ├── triage.md spec.md plan.md implement.md verify.md simplify.md risk.md
│   ├── finder-bugs.md finder-standards.md finder-history.md security.md verifier.md rank.md
│   ├── pr.md retro.md
│   └── tools/             # shared fragments: readonly-toolset.yaml, github-mcp-pr.yaml
├── environments/sandbox-limited.yaml
├── vaults/github.yaml     # display_name only; credential added via API, never in git
├── skills/
│   ├── vendor/mattpocock/ # pinned copies (retro, writing-for-agents, tdd, triage, ...) + LICENSE + CREDITS.md
│   └── own/ verify-evidence/ verify-e2e/ simplify-diff/ risk-tier/ find-bugs/ find-history/
│            security-review/ verify-finding/ rank-findings/ run-retro/
├── schemas/               # triage, spec-meta, plan, impl-report, verify-report, e2e-report, risk,
│                          # findings, votes, review-verdict, retro, ledger
├── config/models.yaml budgets.yaml pipeline.config.example.yaml
├── orchestrator/          # Python: cli.py, stages/*.py, review/{fanout,vote,rank,converge}.py,
│                          # gates.py, budget.py, db.py (+ migrations/), github_gate.py, digest.py
├── reviewers/codex/{find.md,vote.md,run.sh}
├── evals/
│   ├── golden/*.yaml      # task defs; hidden tests live in the demo repo under eval-only paths
│   ├── review/{seeded,clean}/   # R- and C-cases as fixed diffs
│   ├── pressure/          # EVAL-020 cases
│   └── regression/        # frozen cases from incidents and wrong findings (EVAL-013)
└── .github/
    ├── CODEOWNERS  pull_request_template.md (Testing / Risk / How to revert + <details> agent notes)
    ├── ISSUE_TEMPLATE/
    └── workflows/ci.yml secret-scan.yml   # no API keys in CI v1
```

---

## 13. Phase 3 build order (one stage at a time)

Each step ships as **its own PR**, opened by the user and merged by the user. Rollback is always available: revert the PR, and `ant apply` the previous file version (agents are versioned; `--prune` archives removed ones). Each stage can be switched off with `stages.<name>.enabled: false`. Everything runs against the **demo repo**, never a real project.

| Step | Build | Verification | Rollback |
|---|---|---|---|
| **0 (first)** | Repo scaffold: `.gitignore` (incl. `claude-lock.json`, `runs/`), `.env.example`, ruleset (0 approvals, required checks, conversation resolution, no force-push), CODEOWNERS, CI `ci` + `secret-scan` + path scan, schemas, orchestrator skeleton (**SQLite `db.py` with migrations**, ledger, budget math, `--dry-run`), `environments/sandbox-limited.yaml`, demo repo with 2 golden tasks | CI green; gitleaks clean; push protection blocks a planted fake key; `ant apply --dry-run .` shows only intended creates; `pipeline db rebuild` round-trips fixture runs; budget-math unit tests; **a direct push to `main` is rejected** | Revert; `ant apply --prune` |
| 1 | `triage` + owner-label gate | 10 fixture issues (3 strangers, 2 injections): strangers ignored with 0 LLM calls; ≥9/10 correct; pass^3; < $0.05 per issue | Disable; revert; prune |
| 2 | `spec` | 2 features: acceptance IDs, seams, **e2e scenarios** present; idle time costs $0 runtime | Same |
| 3 | `plan` | Valid DAG; A/B Opus vs Sonnet → routing decision | Same |
| 4 | `implement` | Red→green evidence; pushes **only** to `agent/*`; a tiny budget triggers `budget_reached`; trajectory clean | Delete `agent/*` branches; disable |
| 5 | Pre-PR verify (4a evidence, 4b simplify, 4c e2e) | A planted failing test is caught with real output; simplify keeps checks green or is dropped; an e2e scenario failure blocks | Disable 4b/4c individually |
| 6 | Review: risk tier, finders, Codex, vote, rank, convergence | R1–R4 recall and C1–C3 harmful rate measured; the vote drops a planted style-only and a planted false finding; 3-round cap → "step back"; no repeated fingerprint; security finding blocks | Disable individual finders or the Codex adapter |
| 7 | `pr` + `pipeline/review` status | Draft PR opens only after `always_ask`; status flips to pending on a new commit; merge is blocked while it is red; the owner can merge with 0 approvals once it is green | Close PR; revoke the write PAT |
| 8 | `retro` + `pipeline eval` + baselines B0/B1 | Retro on 3 runs produces schema-valid items with evidence; the eval delta table works; the regression gate rejects a deliberately worse prompt | Disable retro; revert |
| 9 | MCP integrations (§16): Atlassian read vaults → spec/plan; GitHub `x/pull_requests` + Jira write in `pr`; CodeGraph pilot | Demo Jira project: read-only stages can't call any write tool (trajectory eval); a PR-stage Jira comment and transition are read back via REST; a `session.error` on MCP auth fails the gate; CodeGraph A/B on G1–G3 + R-cases | Remove the vault from the stage config; `integrations.*: none` |
| 10 | End-to-end | G1–G6 + P1–P3, k=3 held-out, vs B0 on cost per success; 0 trajectory violations. Optional: nightly read-only review deployment | Pause deployment; revert |

---

## 14. Open questions and unverified items

**Decisions still open**
1. The demo repo name and stacks (proposed: `EladAriel/claude-dev-pipeline-demo`, with Python CLI + TS service).
2. Vendor the mattpocock skills (a pinned copy plus notices) or reference them by GitHub URL, which `ant apply` pins to a commit.
3. The eval budget: is ~$150–250 per full golden run (estimate) acceptable before each release, or should the full suite be k=1 with k=3 only on held-out?

**Could not verify**
- **The phrase "three verification checks"** isn't in article [A]. The mapping in §6.1 is my reading: the three pre-PR skills (bug hunt / simplify / end-to-end), plus the per-finding vote. If the owner meant a different Anthropic page, §6 needs another pass.
- [A] is published on `claude.dev` (the Claude Code site), by Anthropic authors, and the hub's PIPE-002 cites the same URL. I did not find a mirror on anthropic.com/engineering.
- [A] does not publish the voter count, the vote threshold, or the confidence cut-off. **2-of-3 with a cross-family rule is our choice.** The ≥80 threshold comes from the plugin README [D], not from the article.
- Whether `git push` from a `limited` sandbox works through the `github_repository` resource without adding `github.com` to `allowed_hosts`.
- Whether a ruleset can require a commit status posted by a PAT with "any source" (expected: yes, but to be tested in step 7).
- Whether Managed Agents honors `disable-model-invocation` (mattpocock's `retro` sets it). Prompts name skills explicitly anyway.
- Whether `codex exec --json` reports token usage for exact Codex cost.
- The GitHub MCP server's exact tool names as exposed to Managed Agents.
- MCP integration items are listed in §16.8.
- All token volumes in §8.2 and the eval cost in §10.4 are assumptions. Only the unit prices are verified.

---

## 15. Changelog rev 1 → rev 2

- **§6 review rebuilt** on [A]/[B]/[C]/[D]:
  - three pre-PR checks;
  - risk-tiered parallel finders, with Codex as a finder;
  - a 3-vote verify with ≥80 confidence and a cross-family rule;
  - Important / Nit / Pre-existing tags;
  - every finding answered;
  - a blocking security review;
  - convergence rules.
  The rev 1 "reconcile" stage and the derived Anchored / Reproduced / Attributable checks are removed.
- **New §9 Retro stage** (adapted mattpocock `retro`), **§10 evals design**, and **§11 SQLite** ledger.
- **Decisions applied:**
  - the repo is `claude-dev-pipeline`;
  - Codex is local `codex exec` only (mode B dropped);
  - the user's own PAT with a 0-approval ruleset;
  - `claude-lock.json` gitignored.
- **Cost** ≈ $7.40 → **≈ $9.35** typical, and the run cap $15 → **$20**.
- **Rev 3:** §16 MCP integrations (Atlassian Rovo MCP, GitHub remote MCP, CodeGraph/Graphify), with per-stage assignment, vault split, and cost impact.

---

## 16. MCP integrations: Atlassian (Jira + Confluence), GitHub, code graphs (rev 3)

Research date: 2026-11-13. Nothing here has been connected or called. All facts come from the pages linked in this section.

### 16.1 What Managed Agents can connect to (constraints that drive everything below)

- **Remote URL servers only.**
  - An agent declares `mcp_servers: [{type: "url", name, url}]`, and only `url` is accepted, so **no custom headers** can be set. [mcp-connector]
  - The server should speak **streamable HTTP**; SSE-only servers work through an automatic fallback. [reference §Supported MCP server types]
  - **Private servers** are reachable only through **MCP tunnels**. That route is in research preview, needs a request for access, and runs `cloudflared` + Anthropic's proxy on a Kubernetes cluster or a Docker VM. [mcp-tunnels](https://platform.claude.com/docs/en/agents-and-tools/mcp-tunnels/overview)
  - **So a stdio MCP server on the owner's laptop (CodeGraph, Graphify) can't be attached to a cloud session directly.** The docs describe no way to register an MCP server that runs *inside* the sandbox; my reading of the docs, not tested.
- **Auth comes from vaults, matched by URL.**
  - `static_bearer` sends `Authorization: Bearer <token>`.
  - `mcp_oauth` stores access + refresh tokens, and Anthropic refreshes them. Refresh auth can be `none`, `client_secret_basic`, or `client_secret_post`.
  - Matching normalizes scheme, host, port, and trailing slash, but **a different path is a different server**. So `…/mcp/x/issues/readonly` and `…/mcp/x/pull_requests` can carry **different tokens**. [vaults], [mcp-connector]
- **Tool allowlist and permissions on our side:**
  - `mcp_toolset` with `default_config.enabled:false` plus a per-tool `configs` allowlist;
  - MCP tools default to `always_ask`.
- **Networking:** with `limited` networking, session creation fails unless the MCP host is in `allowed_hosts`, or `allow_mcp_servers: true` is set. [mcp-connector]
- **Failures don't block the session.** A bad credential or unreachable server shows up as a `session.error` (`mcp_connection_failed_error` / `mcp_authentication_failed_error`). **The orchestrator treats that as a failed gate for any stage that needs the integration** (PIPE-001: a failed read is never "nothing new").

### 16.2 Atlassian Rovo MCP Server (Jira + Confluence)

| Item | Finding | Source |
|---|---|---|
| Official endpoint | `https://mcp.atlassian.com/v2/mcp`. `?tools=all` returns a flat paginated list for gateways | [getting started](https://developer.atlassian.com/cloud/rovo-mcp/guides/getting-started/) |
| Transport | Remote HTTP endpoint, so it fits the Managed Agents `type: url` | same |
| Auth option 1 | **OAuth 2.1** (recommended, interactive consent): `Authorization: Bearer <access_token>`. Admins must allow the client's **domain** (Domains tab) | [OAuth 2.1](https://developer.atlassian.com/cloud/rovo-mcp/guides/configuring-oauth-2-1/), [auth](https://developer.atlassian.com/cloud/rovo-mcp/guides/authentication-and-authorization/), [support](https://support.atlassian.com/security-and-access-policies/docs/understand-atlassian-mcp-server/) |
| Auth option 2 | **API token**, which needs **admin enablement**. Either a personal API token as `Authorization: **Basic** base64(email:token)`, or a **service-account API key** as `Authorization: **Bearer** <key>`. Scopes are `*:jira:agent-interface` / `*:confluence:agent-interface`. API tokens aren't bound to a cloud ID (pass `cloudId`). Some tools are unavailable with tokens | [API token](https://developer.atlassian.com/cloud/rovo-mcp/guides/configuring-authentication-via-api-token/) |
| Tool model | A few **primary** tools are listed directly. The rest of the catalog is **deferred**: found with `discover` and called through `executeRead` / `executeWrite` / `executeDestructive` | [supported tools](https://developer.atlassian.com/cloud/rovo-mcp/guides/supported-tools/) |
| Read tools (primary) | `getJiraIssue`, `listJiraIssueComments`, `searchJiraIssuesUsingJql`, `getConfluenceContent`, `searchConfluence`, `atlassianUserInfo`, `getAccessibleAtlassianResources` | same |
| Write tools (primary) | `createJiraIssue`, `editJiraIssue`, `transitionJiraIssue`, `addOrEditJiraIssueComment`, `createConfluenceContent`, `updateConfluenceContent` | same |
| Permission groups | `read_jira`, `write_jira`, `search_jira`; `delete_jira` and `manage_jira` are **off by default** (admin must enable). Confluence has the same split. Admins grant per group | same |
| Credits | Some tools consume **Rovo credits**: Teamwork Graph calls "1–10 credits", and the Confluence infographic/Maui tools "15–30 credits". All of those stay disabled | same, [getting started](https://developer.atlassian.com/cloud/rovo-mcp/guides/getting-started/) |

**Fit with Managed Agents**
- **Service-account API key → `static_bearer`** is the clean headless path. It is non-interactive, scoped per key, and sent as Bearer, which is exactly what `static_bearer` sends.
- A **personal API token does not fit**. It needs `Basic`, and `static_bearer` sends `Bearer`.
- **OAuth 2.1 → `mcp_oauth`** also fits on paper. The owner completes the consent once with a local helper and stores the access + refresh token with the `token_endpoint`. *Unverified: the Atlassian token endpoint and client registration details for a non-browser client, the refresh-token rotation behaviour, and whether the Domains allowlist accepts it.*
- If the Atlassian org uses **IP allowlists**, Anthropic's egress must be allowed. *Not verified which IPs Managed Agents MCP calls come from.*

**Least privilege (two layers)**
1. **Credential scopes.** There are two service-account keys, each held in its own vault, and the orchestrator picks the vault per stage:
   - `vault-atlassian-read`: `read:jira`, `search:jira`, `read:confluence`, `search:confluence`;
   - `vault-atlassian-write`: adds only `write:jira`.
   - No Confluence write, no delete/manage scopes at all.
2. **Tool allowlist.** `default_config.enabled:false`, then only the primary tools listed in 16.5.
   - `executeWrite` and `executeDestructive` are **never** enabled. They would open the whole deferred write catalog, including `mergeBitbucketRepoPullRequest` if Bitbucket is linked.
   - `executeRead`/`discover` stay off by default, which also keeps tool-list tokens small.

**Untrusted content.** Jira issues and Confluence pages are as untrusted as GitHub issues. [EVAL-022](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/evals/EVAL-022.md) found agents following instructions planted in tool content (266 runs) and making false claims about actions (290 runs). [EVAL-023](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/evals/EVAL-023.md) gives the planted-conflict test. So:
- the **owner-label gate** applies to Jira too. Only issues labelled `agent-approved` enter, and the orchestrator checks via the Jira REST changelog (locally, read token from `.env`) that the owner added the label;
- triage gets the issue text, never tools;
- every Jira or Confluence fact used in the spec is quoted with its key or page id in `spec.md`, so the owner can check it.

### 16.3 GitHub MCP Server (official `github/github-mcp-server`, remote)

| Item | Finding | Source |
|---|---|---|
| Repo / license | <https://github.com/github/github-mcp-server>, MIT | GitHub API |
| Remote endpoint | `https://api.githubcopilot.com/mcp/` (the default toolsets are `context, repos, issues, pull_requests, users`) | [README](https://github.com/github/github-mcp-server/blob/main/README.md) |
| **Toolset and read-only by URL path** | `/x/{toolset}`, `/x/{toolset}/readonly`, `/readonly`, `/x/all/readonly`. A path carries only **one** toolset; combining needs the `X-MCP-Toolsets` header | [remote-server.md](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md) |
| Header options | `X-MCP-Toolsets`, `X-MCP-Tools` (exact tool list), `X-MCP-Readonly`, `X-MCP-Lockdown`. **Managed Agents can't send these**, so we use URL paths + our own tool allowlist | same |
| Lockdown mode | Hides public-repo content from users without push access. It is "a best-effort content filter… not an authorization boundary" and is header-only on the remote server (no URL path found) | README §Lockdown Mode |
| PR tools | `create_pull_request` (supports `draft`), `pull_request_read`, `list_pull_requests`, `update_pull_request`, **`merge_pull_request`**, `pull_request_review_write`, … | README §Pull Requests |
| Auth | OAuth (needs a host-side GitHub/OAuth App), or a PAT as `Authorization: Bearer`, which fits `static_bearer` with a **fine-grained PAT** | README |

**Our use**
- **Least privilege by URL + token + allowlist:**
  - **PR stage only:** `https://api.githubcopilot.com/mcp/x/pull_requests` with the **write** PAT in `vault-github-pr`, tool allowlist = `create_pull_request` (`always_ask`, `draft: true`). `merge_pull_request` and `update_pull_request` are never enabled.
  - **Optional read-only** (off by default): `…/mcp/x/issues/readonly` with the **read** PAT in `vault-github-read`, tools `issue_read`, `search_issues`. It is for `spec` to look up *owner-approved* linked issues only. Without lockdown, raw public issue text is the main prompt-injection channel, so the orchestrator-fetched brief remains the default.
- `allow_mcp_servers: true` on the environment, rather than adding `api.githubcopilot.com` to `allowed_hosts`. That keeps `bash` egress to GitHub's API closed. *Unverified that this behaves as described (Phase 3 test).*

### 16.4 Code knowledge graphs: CodeGraph and Graphify

| | **CodeGraph** | **Graphify** |
|---|---|---|
| Project | <https://github.com/colbymchenry/codegraph> (npm `@colbymchenry/codegraph`, 1.6.2) | <https://github.com/Graphify-Labs/graphify> (PyPI `graphifyy`, 0.9.108, whose project URLs point to that repo) |
| License | MIT | Apache-2.0 |
| What it does | Pre-indexed symbol/call/dependency graph in local SQLite (`.codegraph/codegraph.db`, FTS5). One default MCP tool, `codegraph_explore`, returns the **verbatim line-numbered source** of relevant symbols + call paths + blast radius. Seven more tools are unlisted (`callers`, `callees`, `impact`, …) | tree-sitter AST graph of code (no LLM, local) + optional semantic pass over docs/PDFs/images (needs a model key). Outputs `graphify-out/graph.json`. MCP tools: `query_graph` (token-budgeted subgraph, default 2,000), `get_node`, `get_neighbors`, `shortest_path`, PR-impact tools |
| Local vs remote | Local only. MCP over stdio (`codegraph serve --mcp`), **plus a CLI with the same output** (`codegraph explore`, `query`, `callers`, `impact`). Anonymous telemetry, opt-out `CODEGRAPH_TELEMETRY=0` | Local CLI/skill (`graphify query`), `graphify-mcp` over stdio **or streamable HTTP with `--api-key`**. A commercial hosted "Graphify Cloud" also exists |
| Vendor token claims | "88% fewer tool calls · 53% faster · 62% fewer tokens · 44% cheaper" (7 repos, median of 4 runs, re-measured 2026-08 on Opus 4.8) | "Cut agent token spend by 80%" (Cloud); `graphify benchmark` reports e.g. 136.8x fewer tokens **per query vs reading the whole corpus** |
| Independent tests | [HarrisonSec on Hono](https://harrisonsec.com/blog/i-tested-codegraph-on-hono-benchmark/): −55% tool calls, −20% latency, **+6.8% cost**; narrow questions 20–43% **more** expensive. [menges.dev](https://menges.dev/writing/code-graph-mcp-servers-tested/): 42% faster but **+26% cost** on its best question | [menges.dev](https://menges.dev/writing/code-graph-mcp-servers-tested/): +23% cost on one question, −42% on another without answering it. **With graphify or CodeGraph, Claude Code fully found a real 2FA bypass in 0 of 6 sessions vs 3 of 5 with no tool** |

The ai-knowledge-hub has **no note on CodeGraph or Graphify** (searched `knowledge/` at `ec83f57`). The closest is [METH-020: GraphRAG indexing cost reality check](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-020.md): fund graphs only for relationship and whole-collection questions, and require indexing cost and a freshness plan.

**How a Managed Agent could use them** (no local MCP from the cloud sandbox, see 16.1):

| Option | How | Verdict |
|---|---|---|
| **A. Build inside the sandbox, query by CLI** | Pinned install through the package-manager allowance (`npm i -g @colbymchenry/codegraph@1.6.2` / `pip install graphifyy==0.9.108`). Then `codegraph init --local-only`, or `GRAPHIFY_BACKEND=none graphify extract . --code-only`, then `codegraph explore "<q>"` / `graphify query "<q>"` from `bash`, wrapped in a tiny skill | ✅ **Preferred.** Code-only means no LLM calls and no keys. Telemetry is blocked anyway by `limited` networking (+ the env opt-out). Index time is session runtime (≈ minutes, about $0.01). The graph is always at the mounted SHA |
| **B. Prebuilt artifact** | The orchestrator builds `.codegraph/` or `graphify-out/graph.json` locally at the pinned SHA and mounts it as a session file; the agent still needs the CLI | ⚠️ Only if in-sandbox indexing proves slow on big repos. It goes stale during `implement` (CodeGraph's watcher is off in sandboxes; run `codegraph sync`) |
| C. Remote MCP | `graphify-mcp --transport http --api-key` on a public host or via MCP tunnel (`static_bearer`) | ❌ for v1. It exposes the code graph over the network, and tunnels are a research preview needing infra |
| D. Graphify Cloud (hosted) | Vendor-hosted graph | ❌ It sends code to a third party |

**Recommendation:** **off by default.** Pilot **CodeGraph (option A)** only on `spec`, `plan`, and `finder-history` (blast radius via `codegraph impact`), as an A/B in the golden evals (§10.4). Keep it only if cost per success doesn't rise and review recall doesn't fall. Do **not** give it to `finder-bugs`/`security` until the R-cases show it doesn't hide bugs, since menges.dev's 0/6 vs 3/5 result is exactly the risk. Graphify is the second candidate, mainly for its `--code-only` determinism and token-budgeted output. Pin versions and re-diff tool descriptions on upgrade ([DEVX-004](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-004.md)).

### 16.5 Per-stage MCP assignment

| Stage | GitHub MCP | Atlassian MCP | Code graph | Vault(s) |
|---|---|---|---|---|
| 0 triage | — (orchestrator passes issue text) | — (orchestrator passes Jira issue text) | — | none |
| 1 spec | optional `x/issues/readonly`: `issue_read` | read: `getJiraIssue`, `listJiraIssueComments`, `searchJiraIssuesUsingJql`, `getConfluenceContent`, `searchConfluence`, `getAccessibleAtlassianResources` | pilot (A) | `vault-github-read`, `vault-atlassian-read` |
| 2 plan | — | read: `getJiraIssue`, `searchJiraIssuesUsingJql` (link tickets to existing work) | pilot (A) | `vault-atlassian-read` |
| 3 implement | — | — | — | none |
| 4 verify / simplify | — | — | — | none |
| 5 review finders/voters | — | `finder-standards` only: `getConfluenceContent` for linked design docs (optional) | `finder-history` pilot | `vault-atlassian-read` (optional) |
| **6 pr** | `x/pull_requests`: **`create_pull_request` only**, `always_ask`, draft | **write, Jira only:** `addOrEditJiraIssueComment` (PR link + summary), `transitionJiraIssue` (e.g. → "In Review"), both `always_ask` | — | `vault-github-pr`, `vault-atlassian-write` |
| 7 retro | — | — | — | none |

- **Jira writes happen only in stage 6**, after the owner approves the PR step. They are only two primary tools, each `always_ask`, and the orchestrator re-checks the issue key against the run's approved issue. **No Confluence writes, no create/delete issues.**
- **Config** (per target repo, `pipeline.config.yaml`):

```yaml
integrations:
  tracker: github                 # github | jira
  jira:   { site: "<site>.atlassian.net", project: "ABC", approve_label: "agent-approved",
            transition_on_pr: "In Review" }
  confluence: { spaces: ["ENG"] } # spec may read only these spaces (checked on returned page ids)
  github_mcp: { issues_readonly: false }
  code_graph: none                # none | codegraph | graphify   (pilot only)
```

### 16.6 Security summary for MCP

- **Remote MCP servers are hostile to the client too** ([DEVX-021](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-021.md); [DEVX-007](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-007.md) on unauthenticated endpoints). We use only the two vendor-official servers above and nothing community-hosted.
- **Tokens:**
  - every token lives in a vault, never in agent files;
  - read and write are **separate credentials in separate vaults**, attached per stage;
  - fine-grained PAT / scoped service-account keys, 30–90 day expiry;
  - rotate immediately on any vendor breach notice ([DEVX-051](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/devex/DEVX-051.md)).
- **No token passthrough.** Each server gets a token issued for that server ([PIPE-012](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/pipelines/PIPE-012.md), citing the MCP authorization spec).
- **Agents claim MCP success on impossible tasks** ([METH-110](https://github.com/EladAriel/ai-knowledge-hub/blob/main/knowledge/methods/METH-110.md)). So the orchestrator verifies every write by reading it back:
  - for the PR, through the GitHub REST API locally;
  - for a Jira comment or transition, through Jira REST locally.
  The stage passes only if the read-back matches.
- **Trajectory eval (EVAL-020) additions:** any call to a non-allowlisted MCP tool; any Atlassian write outside stage 6; any `executeWrite`/`executeDestructive`; any call to `merge_pull_request`.

### 16.7 Cost impact

- **Atlassian/GitHub MCP:**
  - **No per-call fee from Anthropic.** The cost is the tokens of tool definitions + tool results (I found no published per-tool token sizes, see 16.8).
  - **Tool definitions:** with ≤ 6 tools per stage they sit in the cached prefix, so the cost is small after the first turn.
  - **Results:** a Confluence page or a JQL result can be 5–30k tokens. On Opus `spec`, ~50k extra uncached tokens ≈ **+$0.20**; reads that get cached afterwards cost $0.20/MTok. **Typical run: +$0.10–0.40 → ≈ $9.45–9.75** (assumption).
  - PR-stage writes on Haiku are negligible.
  - **Rovo credits:** none expected for the plain Jira/Confluence tools we enable. The credit-consuming tools (Teamwork Graph, Remix/infographic) stay disabled. *Credit use for plain tools is not documented, so unverified.*
  - **Haiku 100k step:** Atlassian tools are not given to Haiku stages except `pr`, where results are tiny.
- **Code graph (pilot):**
  - indexing is code-only (no LLM), costing only sandbox runtime, about $0.08/h × a few minutes;
  - the token effect is **uncertain in both directions**: vendors claim −44% to −80%, while independent tests range from −42% to **+26–43%**;
  - on our assumed spec + plan spend (~$1.86), that is roughly ±$0.5 per run;
  - **it ships only if the A/B shows a net saving at equal or better quality.**

### 16.8 Unverified (MCP)

- Whether `allow_mcp_servers: true` lets the MCP connection through while keeping the host closed to `bash`. The docs only say it turns the host check off.
- How Atlassian OAuth 2.1 works with `mcp_oauth`: the token endpoint, refresh rotation, and the Domains allowlist for a token obtained outside a browser client. Also which IPs Managed Agents MCP calls come from, if the Atlassian org uses IP allowlists.
- That the Atlassian tool names exposed over MCP exactly match the doc names (the docs say tool availability is dynamic per scopes, groups, and auth).
- Whether service-account API keys are available on the user's Atlassian plan.
- Rovo credit consumption for basic Jira/Confluence tools.
- Token sizes of tool definitions and typical results (to be measured with `session.usage` in Phase 3).
- That no in-sandbox MCP registration exists. My reading of the docs, not tested.
- That CodeGraph/Graphify install and index cleanly inside the cloud sandbox: npm/pip via the package-manager allowance; CodeGraph's bundled Node / `node:sqlite` requirements.

---

## Sources

**Claude Managed Agents and Claude API** (read 2026-11-13)
- [overview] https://platform.claude.com/docs/en/managed-agents/overview
- [quickstart] https://platform.claude.com/docs/en/managed-agents/quickstart
- [agent-setup] https://platform.claude.com/docs/en/managed-agents/agent-setup
- [environments] https://platform.claude.com/docs/en/managed-agents/environments
- [cloud-sandboxes-reference] https://platform.claude.com/docs/en/managed-agents/cloud-sandboxes-reference
- [sessions] https://platform.claude.com/docs/en/managed-agents/sessions
- [session-operations] https://platform.claude.com/docs/en/managed-agents/session-operations
- [tools] https://platform.claude.com/docs/en/managed-agents/tools
- [permission-policies] https://platform.claude.com/docs/en/managed-agents/permission-policies
- [mcp-connector] https://platform.claude.com/docs/en/managed-agents/mcp-connector
- [vaults] https://platform.claude.com/docs/en/managed-agents/vaults
- [skills] https://platform.claude.com/docs/en/managed-agents/skills
- [github] https://platform.claude.com/docs/en/managed-agents/github
- [files] https://platform.claude.com/docs/en/managed-agents/files
- [multiagent-orchestration] https://platform.claude.com/docs/en/managed-agents/multiagent-orchestration
- [define-outcomes] https://platform.claude.com/docs/en/managed-agents/define-outcomes
- [scheduled-deployments] https://platform.claude.com/docs/en/managed-agents/scheduled-deployments
- [budgets] https://platform.claude.com/docs/en/managed-agents/budgets
- [session-observability] https://platform.claude.com/docs/en/managed-agents/session-observability
- [secure-deployment] https://platform.claude.com/docs/en/managed-agents/secure-deployment
- [reference] https://platform.claude.com/docs/en/managed-agents/reference
- [apply] https://platform.claude.com/docs/en/cli-sdks-libraries/cli/apply
- [effort] https://platform.claude.com/docs/en/build-with-claude/effort
- [pricing] https://platform.claude.com/docs/en/about-claude/pricing (sections "Model pricing" and "Claude Managed Agents pricing")

**Code review: official Anthropic sources** (read 2026-11-13)
- [A] "How the Claude Code team does code review", Addy Osmani, Morgan Westlee Lunt, Qing Wang, Oct 15 2026: https://claude.dev/blog/solving-code-review-at-anthropic/
- [B] Code Review (managed service): https://code.claude.com/docs/en/code-review · https://code.claude.com/docs/en/code-review-customize · https://code.claude.com/docs/en/code-review-pull-requests
- [C] Local `/code-review`, `/simplify`, `/verify`: https://code.claude.com/docs/en/code-review-local
- [D] Official `code-review` plugin README: https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-review
- Context only, with no review checklist: "How Anthropic secures its AI-native software development lifecycle", https://claude.com/blog/how-anthropic-secures-its-ai-native-software-development-lifecycle

**MCP integrations (rev 3)** (read 2026-11-13)
- [mcp-tunnels] https://platform.claude.com/docs/en/agents-and-tools/mcp-tunnels/overview · [remote-mcp-servers] https://platform.claude.com/docs/en/agents-and-tools/remote-mcp-servers · [self-hosted-sandboxes] https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes
- Atlassian Rovo MCP: https://developer.atlassian.com/cloud/rovo-mcp/guides/getting-started/ · https://developer.atlassian.com/cloud/rovo-mcp/guides/authentication-and-authorization/ · https://developer.atlassian.com/cloud/rovo-mcp/guides/configuring-oauth-2-1/ · https://developer.atlassian.com/cloud/rovo-mcp/guides/configuring-authentication-via-api-token/ · https://developer.atlassian.com/cloud/rovo-mcp/guides/supported-tools/ · https://support.atlassian.com/security-and-access-policies/docs/understand-atlassian-mcp-server/
- GitHub MCP: https://github.com/github/github-mcp-server · https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md
- CodeGraph: https://github.com/colbymchenry/codegraph (+ `site/src/content/docs/reference/mcp-server.md`) · Graphify: https://github.com/Graphify-Labs/graphify · https://docs.graphify.com/open-source/mcp-server · https://docs.graphify.com/open-source/features/honest-token-savings · https://docs.graphify.com/open-source/features/token-benchmark
- Independent tests: https://harrisonsec.com/blog/i-tested-codegraph-on-hono-benchmark/ · https://menges.dev/writing/code-graph-mcp-servers-tested/

**OpenAI Codex** (read 2026-11-13)
- [codex-cli-reference] https://developers.openai.com/codex/cli/reference (`codex review`, `codex exec`, `--output-schema`, `--sandbox`)
- [codex-github] https://developers.openai.com/codex/integrations/github (`@codex review`, AGENTS.md Code Review Rules, P0/P1 only; mode B, not used after rev 2)
- [codex-pricing] https://developers.openai.com/codex/pricing (API key: no GitHub code review; API pricing)
- [openai-pricing] https://developers.openai.com/api/docs/pricing (gpt-6.1-sol $2 / $0.10 cached / $2.50 30m write / $10 out)

**mattpocock/skills** (commit `5863806`)
- https://github.com/mattpocock/skills, plus `LICENSE`, `README.md`, and `skills/engineering/retro/SKILL.md`, `skills/productivity/writing-for-agents/SKILL.md`, `skills/engineering/{grill-with-docs,triage,tdd,code-review,to-spec,to-tickets,implement,implement-spec,pr,diagnosing-bugs,codebase-design,domain-modeling,wayfinder,setup-matt-pocock-skills}/SKILL.md`, `skills/productivity/grilling/SKILL.md`, `skills/engineering/pr/CREDITS.md`, `skills/engineering/setup-matt-pocock-skills/issue-tracker-local.md`

**EladAriel/ai-knowledge-hub** (commit `ec83f57`). All note links (full GitHub URLs) are in §3, §9 and §10.
