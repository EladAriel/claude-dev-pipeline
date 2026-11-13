# Implement stage

Sonnet implements `plan.json` one ticket at a time. The default model and the
effort come from `config/models.yaml` (`implement`). A ticket
`suggested_model` is used only when it is on `implement.allowlist`. The skill
`tdd` is pinned in `skills/manifest.yaml` and is not copied here.

Each ticket gets its own branch `agent/<run>-<ticket>` and a worktree of the
target repository. The target is not this pipeline repository. File tools and
bash stay inside that worktree. The stage commits locally. It does not publish
the branch and it does not open a pull request.

`impl-<ticket>.json` records the files changed, the tests added, the test
command and result, the commit sha, and `status` `done` or `blocked`. A
blocked ticket stops the run. Later tickets are not started. The diff is
scanned for private paths and secret-shaped tokens before the commit. A
failed scan blocks the ticket and does not commit.

Each ticket is booked against the implement cap and the run cap, then appended
to the ledger and indexed in SQLite.

## Run

```bash
pipeline run --dry-run --plan-fixture plan-response.json \
  --config config/pipeline.config.example.yaml
pipeline run --issue 12 --issue-snapshot issues/12.json \
  --model-fixture triage-response.json \
  --spec-fixture spec-response.json \
  --plan-fixture plan-response.json \
  --implement-fixture implement-response.json \
  --target-repo /path/to/target
```

The dry-run prints one `implement-fanout` line per ticket and does not touch
the target repo.

## Verify

```bash
pytest tests/test_implement.py
pipeline run --dry-run --plan-fixture plan-response.json \
  --config config/pipeline.config.example.yaml
```

The tests build a temporary git repository. They do not call a model API.

## Rollback

Disable the stage:

```yaml
stages:
  implement:
    enabled: false
```

A disabled implement stage writes no `impl-*.json` and creates no worktree.
To remove the stage from the tree:

```bash
git revert <implement-commit>
```

Local ticket branches in a target repo can be deleted there. They were never
published by this stage.
