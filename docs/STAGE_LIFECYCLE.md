# Verify and rollback

Every later stage ships with a verification and a rollback. The orchestrator
checks outputs. It does not script the agent's steps, and it does not merge.

| Stage | Verify | Rollback |
| --- | --- | --- |
| Scaffold (this change) | `ruff`, `pytest`, gitleaks, `pipeline scan-paths`, `pipeline --dry-run` | Revert the pull request |
| Triage | Unapproved items produce no model call | Disable the stage and revert its pull request |
| Spec | Acceptance ids, seams, and end-to-end scenarios are present | Disable the stage and revert |
| Plan | The ticket graph is a valid DAG | Disable the stage and revert |
| Implement | Red-green evidence; pushes stay on `agent/*` | Delete the `agent/*` branch and revert |
| Verify | Evidence quotes real command output; end-to-end scenarios are recorded | Disable the check and revert |
| Simplify | Checks stay green, or the simplify commit is dropped | Drop that commit |
| Review | Votes, the round cap, and the security block hold in local tests | Disable a finder or the Codex adapter |
| PR | Draft only; the only GitHub MCP tool is `create_pull_request` | Close the draft and disable the stage |
| Retro | Items are proposals and are not applied | Disable retro and revert |
| Metrics | `run_metrics` matches the ledger for that run | Rebuild the index from the run artifacts |
| Issue import | One draft per task, with a JSON block and `pipeline:queued` | Delete the local outbox; do not keep the issues |
| MCP | The policy tests reject merge, Confluence writes, and stdio | Set `enabled: false` and remove the vault from the stage |

Disabling a stage later is `stages.<name>.enabled: false` in the target repo config.
