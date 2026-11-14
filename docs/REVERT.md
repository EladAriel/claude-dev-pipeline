# Revert after merge

`stages.revert.enabled` defaults on.

```bash
pipeline revert <sha-or-run-id> --target-repo <repo> --sha <commit>
```

The command creates `pipeline/revert-<id>`, reverts that commit, and runs the optional `--verify-command`. The pull request payload is not a draft and is not pushed. Main is not updated and nothing is merged.

A conflict aborts the revert and writes the conflicted files. The reason is stored as an unapplied retro lesson and a row in `reverts.sqlite`.

## Verify

```bash
python3 -m pytest -q tests/test_revert.py
```

## Roll back

Set `stages.revert.enabled: false`, then `git revert` the revert-command commit.
