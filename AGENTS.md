# Repository rules

This scaffold does not call a model API and does not merge.

## Code review rules

- Behavior claims cite a file and line.
- Style is a nit. A leak, a broken behavior, or a blocked rollback is important.
- Workflow files stay `contents: read`.
- Do not add a merge call, a paid SDK dependency, or a secret value.
- GitHub Issues are the task source of truth. `agent:approved` is applied by
  the owner, not by an agent.
- One `pipeline:*` status label is active at a time. The status comment is
  updated in place.

## Code Review Rules

Codex, when it is wired in a later stage, reads this section. The scaffold
does not invoke `codex exec`.
