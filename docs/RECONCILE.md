# Codex and Claude disagreement

`stages.reconcile.enabled` defaults on. When one model votes yes and the other votes no, both at confidence 80 or higher, the stage runs one tie-break. The verifier is the cheapest priced model that is neither of those two. It has to cite `file:line` plus a failing test or a repro. If that evidence matches the finding, a non-security finding is kept. Otherwise it is dropped and tagged `disputed`.

A security finding that already blocks on one qualifying yes still blocks. The disagreement is labeled disputed for a person to decide. Disputed findings are listed in the PR body. Ones that do not block do not stop the pull request.

The cap is `max_per_round` tie-breaks and `max_cents` for their fixture cost. Extra splits are disputed without another call. Agreement and false-positive rows go to `agreement.json` and the `model_agreement` table. Retro may propose raising the confidence floor. It never applies that change.

## Verify

```bash
python3 -m pytest -q tests/test_reconcile.py
```

## Roll back

Set `stages.reconcile.enabled: false`, then `git revert` the reconcile commit.
