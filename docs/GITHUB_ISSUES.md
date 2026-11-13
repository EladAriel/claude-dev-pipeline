# GitHub Issues

Issues are the source of truth for tasks.

## Import

`plan.json` is a DAG:

```json
{
  "tasks": [
    {"id": "01", "title": "Add the parser", "body": "Details.", "blocked_by": []},
    {"id": "02", "title": "Add the printer", "blocked_by": ["01"]}
  ]
}
```

`pipeline import plan.json` prints one issue per task and does not call
GitHub. Each draft has:

- the title
- the label `pipeline:queued`
- a body that ends with a fenced JSON block marked `<!-- pipeline-task -->`
- a status comment marked `<!-- pipeline-status -->`

`pipeline import plan.json --apply` writes those drafts to
`runs/issue-outbox.json`. That file is gitignored. The command still does
not call GitHub. It does not add `agent:approved`.

## Status labels

Exactly one of these labels is active:

- `pipeline:queued`
- `pipeline:running`
- `pipeline:review`
- `pipeline:pr-open`
- `pipeline:blocked`
- `pipeline:done`

`pipeline issue-status --issue N --set running` prints the label to add, the
other `pipeline:*` labels to remove, and whether the status comment is
created or updated. A comment whose body contains `<!-- pipeline-status -->`
is updated in place. `--comments comments.json` supplies the current comments
locally. `--apply` writes the operation to a local outbox and does not call
GitHub.

## claude.ai Artifact

There is no Artifact API for this repository to call. A claude.ai Artifact
can still be a UI over these issues by using the user's GitHub connector:

- create the issues from a plan
- add `agent:approved` when the owner decides the work may run
- read `pipeline:*` labels and the single status comment

The connector is the user's. This scaffold does not embed a token for it.
