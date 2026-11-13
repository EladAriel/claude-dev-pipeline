# Verify stage

Haiku is the model in `config/models.yaml` (`verify`, low effort). The agent
file is `agents/verify.md`. Tools are `read`, `grep`, `glob`, and `bash`, and
commands run with the ticket worktree as the working directory. A command
token that leaves the worktree is rejected.

The orchestrator runs this stage. It does not call the model first. For each
done ticket it runs the target config commands `checks.test`, `checks.lint`,
`checks.typecheck`, and `checks.build`. An empty command is skipped. Each
command has a timeout (`checks_timeout_seconds`, default 30) and its output
is captured.

It also checks that every acceptance id in `spec.json` is mapped to a passing
test on an `impl-*.json` report. The report's `acceptance_tests` list is the
mapping. When that list is absent, the plan ticket's acceptance ids count
only if `tests_added` is non-empty and the fresh commands passed.

When every command passes and every acceptance id is covered, `verify.json`
is written with `model_called: false` and a zero ledger charge. The model
fixture is not opened.

When a command fails, times out, or an acceptance id is uncovered, the stage
reads the recorded summary fixture, books it against the verify cap and the
run cap, and stops the run before review.

## Run

```bash
pipeline run --dry-run --config config/pipeline.config.example.yaml
```

That prints the verify stage and does not run commands.

## Verify

```bash
pytest tests/test_verify.py
```

The tests build a temporary git checkout and fake commands. They include the
all-green path, which does not open a model fixture.

## Rollback

Disable the stage:

```yaml
stages:
  verify:
    enabled: false
```

A disabled verify stage does not run commands and does not write
`verify.json`. To remove the stage from the tree:

```bash
git revert <verify-commit>
```
