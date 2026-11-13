# Merge conflicts

`stages.conflicts.enabled` defaults on. Before the PR stage, and again on resume, the orchestrator checks whether `pipeline/<run-id>` is behind the base branch.

It rebases that branch onto the latest base. Lockfiles, generated files, and whitespace-only hunks match `stages.conflicts.trivial` and are replaced with the base copy. Any other conflict becomes one Sonnet ticket scoped to those files. The ticket has to stay inside `max_cents`. After a resolution, verify runs again and review is forced to Quick.

If the ticket fails or the budget is too small, the rebase is aborted, a draft `pr.json` lists the files, and `summary.md` lists them too. `pipeline:conflict` is recorded only when `agent:approved` gating allows it. The same base commit does not retry the rebase.

The base branch is never committed to. A push is refused unless the remote tip is already in the local branch, so this path does not force-push over someone else's commits.

## Verify

```bash
python3 -m pytest -q tests/test_conflicts.py
```

## Roll back

Set `stages.conflicts.enabled: false`, then `git revert` the conflicts commit.
