# Repo without tests

`stages.verification.enabled` defaults on. `mode` defaults to `bootstrap`.

Detection looks at the config `checks.test` override first. When that is empty, it looks for a conventional runner: pytest for Python, `go test` when a Go test file exists, `npm test` when `package.json` defines it, `cargo test`, or Maven/Gradle when a test source exists.

No test command is not a pass. Bootstrap commits the smallest standard harness and a characterization test before the change, and records that work as ticket `H0` so implement does not spend a model call on it. Degraded mode is an explicit opt-in (`mode: degraded`): build, lint, and typecheck can still run, `verify.json` status is `unverified`, the review depth moves up one level, the PR body and `summary.md` carry a warning, and a tier S fast lane is escalated to the standard lane.

## Verify

```bash
python3 -m pytest -q tests/test_harness.py
```

## Roll back

Set `stages.verification.enabled: false`, then `git revert` the verification commit.
