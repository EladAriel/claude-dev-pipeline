# Spec stage

Opus writes `spec.md` and `spec.json` from `triage.json` and `agent-brief.md`.
The model id and effort come from `config/models.yaml` (`spec`). The skills
`grill-with-docs` and `to-spec` are pinned in `skills/manifest.yaml` and are
not copied here. `grill-with-docs` depends on `grilling` and `domain-modeling`,
which are pinned the same way.

Tools in `agents/spec.md` are read-only: `read`, `grep`, `glob`, and `bash`
with `read_only`. GitHub and Atlassian MCP reads for this stage stay disabled
in the example config. The orchestrator still resolves them through the MCP
policy and records `contacted: false`.

`spec.json` has acceptance criteria, seams, end-to-end scenarios, and open
questions. `needs_human` is true when any question has `blocks: true`.

Spec does not run when triage's `decision` is `reject`. The spec fixture is
not opened in that case.

## Run

```bash
pipeline run --dry-run --config config/pipeline.config.example.yaml
pipeline run --issue 12 --issue-snapshot issues/12.json \
  --model-fixture triage-response.json --spec-fixture spec-response.json
```

The first command prints the spec stage and does not read a fixture. The
second books triage, then spec, from recorded responses. Neither calls a
model API.

## Verify

```bash
pytest tests/test_spec.py
pipeline run --dry-run --config config/pipeline.config.example.yaml
```

## Rollback

Disable the stage:

```yaml
stages:
  spec:
    enabled: false
```

A disabled spec stage writes no `spec.md`. To remove the stage from the tree:

```bash
git revert <spec-commit>
```
