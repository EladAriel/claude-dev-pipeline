# Multi-repo changes

`stages.multirepo.enabled` defaults on. `repos` lists the only repositories a cross-repo ticket may touch. A task with `repo` set to anything else is refused and that path is never opened.

Each allowed, approved, writable git checkout gets a local branch `pipeline/<run-id>/<name>`. The base branch is not updated and nothing is pushed or merged. Pull request notes include the other branches and the config merge order. Verify runs per repo. One budget covers every repo; a repo that would exceed it is halted and reported. A repo that is not approved, not a git checkout, or not writable is halted the same way.

## Verify

```bash
python3 -m pytest -q tests/test_multirepo.py
```

## Roll back

Set `stages.multirepo.enabled: false`, then `git revert` the multi-repo commit.
