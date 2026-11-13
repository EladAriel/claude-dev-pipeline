# Retro stage

Retro runs at the end of every recorded run: success, stop, or budget halt.
The orchestrator keeps the retro cap inside the run cap, and earlier stages
spend only what is left after that slice. Metrics are computed in code from
`evals/metrics.yaml` inputs: cost by stage and model against the caps, tokens,
review survivors by family, review rounds, verify pass, and the stop reason.
They are written to `metrics.json` and indexed in `runs/pipeline.db`.

Haiku writes `retro.md` from a recorded fixture. Each item is a proposal.
`applied` stays false. The stage does not edit the target repo.

`pipeline report` reads the index and prints the cost trend and the share of
implement rows that used `claude-haiku-5.5`, the cheapest model in the catalog.

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
