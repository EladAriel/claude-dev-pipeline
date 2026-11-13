# Fast lane

An explicit tier `S` uses the fast lane when `stages.fast.enabled` is true.
A fixture that omits `tier` keeps the normal stage order. The lane skips
spec and plan and writes one ticket from the issue. Implement stays on Sonnet.
Verify runs next. Review is Quick and keeps only the bug-hunt check.

Codex stays off unless the diff matches `fast.security_globs`. A security
finding still blocks on one qualifying yes vote.

The lane escalates when the diff is past `fast.max_files` or `fast.max_lines`,
or when verify fails `fast.max_verify_failures` times. Escalation sets
`routing.escalated`, keeps the implement work, and records the reason. A diff
escalation continues with a standard or full review. Repeated verify failures
stop before the pull request.

## Verify

```bash
pytest tests/test_fast.py
```

## Rollback

```yaml
stages:
  fast:
    enabled: false
```

Tier S then follows the normal spec and plan stages. To remove the lane:

```bash
git revert <fast-lane-commit>
```
