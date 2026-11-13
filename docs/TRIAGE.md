# Triage stage

Haiku classifies an issue that already passed the owner label gate. The skill
`triage` is referenced from `skills/manifest.yaml` and is not copied into this
repository. Tools are `read`, `grep`, and `glob` only. The agent file is
`agents/triage.md`.

`triage.json` records `classify`, `category`, `state`, `risk_tier`
(`Quick`, `Standard`, or `Deep`), `decision` (`route` or `reject`),
`needs_human`, `confidence`, `injection_suspected`, and `summary`. A routed
issue also gets `agent-brief.md`. The orchestrator does not post the brief.

## Run

Live model calls are not enabled. A recorded response stands in for the model:

```bash
pipeline run --issue 12 --issue-snapshot issues/12.json --model-fixture triage-response.json
```

`pipeline run --dry-run` prints the triage stage and does not read a fixture.
An issue without `agent:approved` from `EladAriel` stops before the fixture is opened.

The stage books its cost on the JSONL ledger and the SQLite index, inside the
triage cap and the run cap. A charge past the cap is a hard stop.

## Verify

```bash
pytest tests/test_triage.py
pipeline run --dry-run --config config/pipeline.config.example.yaml
```

Both stay offline.

## Rollback

Disable the stage in the target repo config:

```yaml
stages:
  triage:
    enabled: false
```

The next `pipeline run` skips triage and writes no `triage.json`. To remove
the stage from the tree, revert its commit:

```bash
git revert <triage-commit>
```
