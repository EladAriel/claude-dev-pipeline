# Budget overrun

Before each stage the orchestrator adds the ledger to that stage's estimate
in `config/budget_policy.yaml` and compares it with the remaining run cap and
the tier cap. A fixture that already quotes a higher cost or more output
tokens than the remaining budget counts as the estimate. `max_tokens` is how
many output tokens the remaining cents can buy at list price.

At 80% of the cap, later stages drop to the cheapest model on their allowlist
and review depth drops to Quick. The security block rule stays in place.

If the next stage would pass the cap, the run stops before that stage writes
anything. Worktrees and branches already created are kept. Nothing is pushed,
and a pull request is not opened. Retro still runs from its reserved slice.
`summary.md` says what finished, what is left, and how much budget would be
needed to finish. The halt reason is stored on the metrics row.

## Verify

```bash
pytest tests/test_overrun.py
```

## Rollback

```yaml
stages:
  budget_policy:
    enabled: false
```

Stages then start without a projection. To remove the policy:

```bash
git revert <budget-policy-commit>
```
