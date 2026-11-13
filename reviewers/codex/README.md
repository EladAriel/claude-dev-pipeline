# Codex reviewer

`adapter.py` runs:

```bash
codex exec --sandbox read-only --output-schema reviewers/codex/finding.schema.json -o <findings>
```

The sandbox is read-only. Token counts are recorded when the JSON reports them
and stay null otherwise. If `reviewers.codex.enabled` is false, or `codex` is
not on `PATH`, review continues with `cross_model` false.

The stage does not call a paid API by itself. A real `codex` binary would.
