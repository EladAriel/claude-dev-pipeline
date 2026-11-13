# Executive summary

After retro, and at the end of a debug run, the orchestrator writes
`runs/<id>/summary.md` and prints it last. The sections are filled from the
ledger, the metrics, the tier, and the pull-request artifact:

- what was asked
- what was decided, and why
- what was built
- open items and risks
- cost and time
- tier, lane, and any escalation
- what you need to do next, including review and merge

A recorded Haiku fixture may add one narrative paragraph. If the fixture is
missing, or `stages.summary.narrative` is false, the paragraph is the
deterministic fallback. Stopped runs and budget halts get the same template.

When the source issue still passes `agent:approved`, the same text is saved
as `summary-comment.txt`. It is not posted to GitHub.

## Verify

```bash
pytest tests/test_summary.py
```

## Rollback

```yaml
stages:
  summary:
    enabled: false
```

That skips the file and the trailing print. To remove the feature:

```bash
git revert <summary-commit>
```
