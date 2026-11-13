# Review stage

This file is the stage rollback note. The repository's review rules for
finders stay in the root `REVIEW.md`.

Review reads the triage risk tier and raises it when the diff is large or
touches protected, auth, secret, workflow, or dependency paths. Quick uses
the configured Sonnet finder. Standard and Deep use Opus. The parallel checks
are bug-hunt, simplify, and end-to-end. Style-only simplify notes are dropped
before voting.

Codex is a local subprocess:

```bash
codex exec --sandbox read-only --output-schema reviewers/codex/finding.schema.json
```

The adapter is `reviewers/codex/adapter.py`. It times out, parses JSON, and
records token counts when they are present. Missing counts stay null. Codex
cost is a separate ledger row. If Codex is disabled or the executable is
missing, the stage continues with `cross_model` false.

A finding survives with two of three yes votes, each at confidence 80 or
higher, and at least one yes from another model family. Tags are Important,
Nit, and Pre-existing. A security finding with one qualifying yes vote blocks
the run. Important survivors become fix tickets for implement. The loop stops
at three rounds.

`review.json` and `review.md` are the artifacts. `pipeline-review-status.json`
is the `pipeline/review` commit-status payload. It is not posted.

## Verify

```bash
pytest tests/test_review.py
pipeline run --dry-run --config config/pipeline.config.example.yaml
```

The tests put a fake `codex` executable on `PATH`. They do not call a model API.

## Rollback

```yaml
stages:
  review:
    enabled: false
reviewers:
  codex:
    enabled: false
```

Either flag skips that part. `stages.review.enabled: false` skips the stage
and does not open the review fixture. To remove the stage from the tree:

```bash
git revert <review-commit>
```
