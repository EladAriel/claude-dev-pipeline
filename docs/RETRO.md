# Retro stage

Retro runs at the end of every recorded run: success, stop, or budget halt.
The orchestrator keeps the retro cap inside the run cap, and earlier stages
spend only what is left after that slice. Metrics are computed in code from
`evals/metrics.yaml` inputs: cost by stage and model against the caps, tokens,
review survivors by family, review rounds, verify pass, and the stop reason.
They are written to `metrics.json` and indexed in `runs/pipeline.db`.

Haiku writes `retro.md` from a recorded fixture. Each item is a proposal.
`applied` stays false. The stage does not edit the target repo.

`pipeline report` reads the index and prints the cost trend. For each stage
it also prints the share of model calls that used the cheapest model on that
stage's allowlist in `config/models.yaml`. A stage with no `allowlist` uses
its configured `model` as the only choice. Cheapest means the lowest input
list price in `budget.py`, then the lowest output price. Rows with
`model_called: false` are left out, including a green verify run. The overall
share uses only stages whose allowlist has a known list price.

## Verify

```bash
pytest tests/test_retro.py tests/test_e2e.py
pipeline report --db runs/pipeline.db
```

## Rollback

```yaml
stages:
  retro:
    enabled: false
```

That skips the stage and does not open the retro fixture. To remove it from
the tree:

```bash
git revert <retro-commit>
```

The design text still names Sonnet for this stage. This implementation uses
Haiku at low effort, which is the default in `config/models.yaml`.
