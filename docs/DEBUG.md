# Debug mode

`pipeline debug` reproduces a report in a scratch worktree. It does not commit,
push, or open a pull request. The diagnosis is `runs/<id>/diagnosis.md`: how
to reproduce it, hypotheses, the failing test, the root cause, the evidence,
a confidence score, and a suggested fix.

The agent prompt references `diagnosing-bugs` from mattpocock/skills at the
pinned manifest SHA. The file is not copied into this repo. The default model
is Haiku. The stage cap is `debug.budget_cents` (50 when unset).

`--fix` hands that failing test to the fast lane as its acceptance check. The
escalation rules from the fast lane still apply when that lane runs.

## Verify

```bash
pytest tests/test_debug.py
```

## Rollback

```yaml
stages:
  debug:
    enabled: false
```

That skips the command and does not open the fixture. To remove the mode:

```bash
git revert <debug-commit>
```
