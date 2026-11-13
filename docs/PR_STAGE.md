# PR stage

This note is the stage rollback. The root pull-request rules stay in `PR.md`.

The stage runs after verify passed and review has no blocking finding. It
combines the ticket branches into `pipeline/<run-id>` in the target repo and,
unless `--no-push` is set, pushes that branch with the remote name already
configured on the repo. The push command does not take a token.

The draft pull request is opened with GitHub MCP `create_pull_request` on
`vault-github-pr` and toolset `/x/pull_requests`. Merge tools are denied by
`mcp_policy`. The saved `pipeline/review` payload is posted as a commit status.
The source issue gets `pipeline:pr-open` and one in-place status comment, and
only when the issue still passes the `agent:approved` gate.

Haiku's job is the title and body. The offline path renders them from the spec,
plan, and review artifacts: the issue link, the ticket list, the Important /
Nit / Pre-existing counts, and the cost so far. Atlassian writes stay disabled.
If enabled later, they are `always_ask` and limited to the linked Jira comment
and transition.

`--no-push` writes `pr.json` and `pr-body.md` and does not push or call MCP.

## Verify

```bash
pytest tests/test_pr.py
pipeline run --dry-run --config config/pipeline.config.example.yaml
```

The tests use a temp git repo, a local bare remote, and a fake MCP client.

## Rollback

```yaml
stages:
  pr:
    enabled: false
```

That skips the stage. To remove it from the tree:

```bash
git revert <pr-commit>
```
