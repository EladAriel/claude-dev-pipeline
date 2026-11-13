# MCP assignment

Managed Agents accept a remote MCP server by URL. Auth is a vault
(`static_bearer`), matched to that URL. A different path is a different
server, so read and write use different vaults. Local stdio is not a
Managed Agents MCP server. The scaffold does not connect to any of these.

All `mcp_servers` entries in `config/pipeline.config.example.yaml` are
`enabled: false`.

| Stage | GitHub MCP | Atlassian MCP | Code graph | Vault |
| --- | --- | --- | --- | --- |
| Triage | none | none | none | none |
| Spec | optional `https://api.githubcopilot.com/mcp/x/issues/readonly` (`issue_read`, `search_issues`), off by default | read `https://mcp.atlassian.com/v2/mcp` | CLI pilot, off | `vault-github-read`, `vault-atlassian-read` |
| Plan | none | read: `getJiraIssue`, `searchJiraIssuesUsingJql` | CLI pilot, off | `vault-atlassian-read` |
| Implement, verify, simplify | none | none | none | none |
| Review | none | optional `finder-standards`: `getConfluenceContent` | `finder-history` CLI pilot, off | `vault-atlassian-read` when that read is on |
| PR | `https://api.githubcopilot.com/mcp/x/pull_requests` with only `create_pull_request`, `always_ask`, draft | write, Jira only: `addOrEditJiraIssueComment`, `transitionJiraIssue`, `always_ask` | none | `vault-github-pr`, `vault-atlassian-write` |
| Retro | none | none | none | none |

Denied everywhere: `merge_pull_request`, `executeWrite`, `executeDestructive`,
and Confluence writes (`createConfluenceContent`, `updateConfluenceContent`).

Code graph, when a later stage turns the pilot on, runs inside the sandbox:

- `npm i -g @colbymchenry/codegraph@1.6.2`, then `codegraph init --local-only`
- or `pip install graphifyy==0.9.108`, then a code-only extract

It is not registered as MCP. `integrations.code_graph` stays `none`.
