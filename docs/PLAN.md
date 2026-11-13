# Plan stage

Opus turns `spec.md` and `spec.json` into `plan.json`. The model id and effort
come from `config/models.yaml` (`plan`). The skill `to-tickets` is pinned in
`skills/manifest.yaml` and is not copied here.

Tools in `agents/plan.md` are read-only: `read`, `grep`, and `glob`. The
Atlassian read for this stage stays disabled. The orchestrator resolves it
through the MCP policy and records `contacted: false`.

`plan.json` is a `tasks` list that `pipeline import plan.json` can already
read. Each ticket has an id, title, body, acceptance-criterion ids from the
spec, files, seams, a test plan, `blocked_by` dependencies, an estimate
(`S`, `M`, or `L`), and a suggested implement model. Tickets are ordered so
every dependency appears earlier. Every spec acceptance id is covered. The
graph has no cycles.

Plan does not run when `spec.json` has `needs_human: true`. The plan fixture
is not opened in that case.

## Run

```bash
pipeline run --dry-run --config config/pipeline.config.example.yaml
pipeline run --issue 12 --issue-snapshot issues/12.json \
  --model-fixture triage-response.json \
  --spec-fixture spec-response.json \
  --plan-fixture plan-response.json
```

The dry-run prints the plan stage and does not read a fixture. The second
command books the stages from recorded responses. Neither calls a model API.

## Verify

```bash
pytest tests/test_plan.py
pipeline run --dry-run --config config/pipeline.config.example.yaml
```

## Rollback

Disable the stage:

```yaml
stages:
  plan:
    enabled: false
```

A disabled plan stage writes no `plan.json`. To remove the stage from the tree:

```bash
git revert <plan-commit>
```
