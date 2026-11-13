# Difficulty tiers

Triage records `tier` as `S`, `M`, `L`, or `XL`, with a rationale and signals:
estimated files, cross-module, security-sensitive paths, and ambiguity.
`config/tiers.yaml` maps each tier to a lane (`fast`, `standard`, or `full`),
a review depth (`Quick`, `Standard`, or `Deep`), a Codex policy, model and
effort overrides, and a budget cap. The cap stays inside the $20 run cap.
An override is kept only when the model is on that stage's allowlist.

Signals can raise the tier. A raised tier is stored as `escalated` so retro
can measure misclassification. The review stage reads `routing.review_depth`,
which matches the saved `risk_tier`.

`XL` stops before any later stage unless the run passes `--approve-xl`.

## Verify

```bash
pytest tests/test_tiers.py
```

## Rollback

```yaml
stages:
  tiers:
    enabled: false
```

That leaves triage on the fixture's own risk tier and does not apply a lane
or a tier budget. To remove the feature from the tree:

```bash
git revert <tiers-commit>
```
