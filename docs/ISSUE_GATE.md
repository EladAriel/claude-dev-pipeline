# Issue gate

Agents act only on work the owner has approved.

- GitHub Issues use the label `agent:approved`. The latest application of that
  label must be by `EladAriel`.
- Jira uses `agent-approved` with the same owner check.
- Anything else is ignored and does not start a model call. Triage receives
  the approved text, not a raw untrusted body, and it has no MCP tools.
- Import does not add `agent:approved`. The owner adds it, including from a
  claude.ai Artifact through the user's GitHub connector.
- This scaffold reads a local issue snapshot (`issues/<n>.json` or
  `--issue-snapshot`). It does not call GitHub. The snapshot is:

```json
{
  "number": 12,
  "labels": ["agent:approved"],
  "timeline": [
    {"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}
  ]
}
```
