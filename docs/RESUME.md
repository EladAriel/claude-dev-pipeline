# Resume

The orchestrator writes `runs/<id>/state.json` after every completed stage
and after every implement ticket. The write replaces the file in one step, so
a crash cannot leave a half-written checkpoint.

`pipeline resume <run-id>` continues from the last completed stage. Completed
stages and intact tickets are skipped. A ticket worktree whose branch is
missing, whose HEAD does not match the checkpoint, or whose tree is dirty is
discarded and redone. The ledger is append-only, so the budget stays
cumulative and a second resume does not book those stages again. An existing
`pr.json` or status comment is left in place.

Resume refuses when the issue snapshot or config hash no longer matches the
checkpoint, unless you pass `--force`. A budget halt can continue with
`--run-cap-cents` set higher than the cap stored in the checkpoint.

## Verify

```bash
pytest tests/test_resume.py
```

## Rollback

```yaml
stages:
  resume:
    enabled: false
```

That refuses `pipeline resume` and leaves the checkpoint on disk. To remove
the feature:

```bash
git revert <resume-commit>
```
