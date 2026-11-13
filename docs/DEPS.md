# Dependency upgrades

`stages.deps.enabled` defaults on. An issue whose planned paths are all dependency manifests for one ecosystem uses the deps lane. The ecosystems are npm, Python, Go, Rust, Ruby, and Java, and the filename table is `stages.deps.ecosystems`.

The fast lane is turned off and the full test suite still runs. A major-version bump, read from the manifest diff, sets the tier to at least L and turns Codex on next to Claude review. A local audit result (`checks.audit`, or `audit-result.txt` in the repo) that mentions an advisory, CVE, or GHSA blocks the run. Release notes come from `--changelog-fixture`. Nothing is fetched from the network.

A version that appears in the new manifest, was not in the previous file, and is not named in the issue stops the run. Two ecosystems in one issue stop the run. The pipeline does not invent upgrades.

## Verify

```bash
python3 -m pytest -q tests/test_deps.py
```

## Roll back

Set `stages.deps.enabled: false`, then `git revert` the deps-lane commit.
