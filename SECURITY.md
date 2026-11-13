# Security

This is a public repository. Do not commit secrets, account identifiers, or
private filesystem paths.

- `.env*` is gitignored. `.env.example` lists variable names only.
- `claude-lock.json`, `runs/` (including `pipeline.db` and ledgers), and local
  caches are gitignored.
- MCP credentials belong in Anthropic vaults. Vault files in `vaults/` name
  the vault and carry no bearer token.
- Agents do not merge. GitHub MCP for the PR stage allows `create_pull_request`
  only, with `always_ask`. `merge_pull_request` is denied.
- Atlassian writes are the PR-stage comment and transition tools only.
  `executeWrite`, `executeDestructive`, and Confluence writes are denied.
- Code graph tooling is a pinned sandbox CLI in code-only mode. It is not an
  MCP server and it is off by default.
- Issues enter a run only when `agent:approved` was applied by `EladAriel`.
- CI checks out the repository with `permissions: contents: read` and runs on
  push and manual dispatch. It does not run for issues or for pull requests
  opened from other repositories.
- Pre-commit runs gitleaks and ruff.

Report a vulnerability privately to the owner, `@EladAriel`.
