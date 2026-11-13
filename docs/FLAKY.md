# Flaky tests

`stages.flaky.enabled` defaults on. A failing test command is not a verdict yet.

The stage parses failing pytest node ids and Go test names, then re-runs only those tests up to `retries` times. A pass on retry is flaky. A test that fails every attempt is a real failure. The same test is then run on a detached worktree of the parent commit. A flake that also fails or flakes on that commit is pre-existing: it is appended to `runs/quarantine.json` and the `quarantine` table in `runs/pipeline.db`, with a flake count that persists across runs. It does not block. The PR body and `summary.md` warn. A failure that passes on the base commit blocks as a normal verify failure.

Retries are commands. They do not call a model, and they do not edit or skip tests. Haiku is read only when a real introduced failure remains, and only to summarize it. `max_seconds` caps the retry window. `max_cents` stays 0 because classification spends nothing.

## Verify

```bash
python3 -m pytest -q tests/test_flaky.py
```

## Roll back

Set `stages.flaky.enabled: false`, then `git revert` the flaky-tests commit. The stage goes back to a single command run.
