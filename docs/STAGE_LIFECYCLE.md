# Verify and rollback

Every later stage ships with a verification and a rollback. The orchestrator
checks outputs. It does not script the agent's steps, and it does not merge.

| Stage | Verify | Rollback |
| --- | --- | --- |
| Scaffold (this change) | `ruff`, `pytest`, gitleaks, `pipeline scan-paths`, `pipeline --dry-run` | Revert the pull request |
| Triage | Offline fixtures: unapproved issues make no model call; `triage.json` matches the schema | `stages.triage.enabled: false`, then `git revert` the triage commit |
| Tiers | Offline fixtures: S/M/L/XL set the lane, review depth, and budget; XL stops without `--approve-xl` | `stages.tiers.enabled: false`, then `git revert` the tiers commit |
| Fast lane | Tier S skips spec and plan; a large diff or a second verify failure escalates | `stages.fast.enabled: false`, then `git revert` the fast-lane commit |
| Debug | Offline fixture: diagnosis stays in a scratch worktree and does not push | `stages.debug.enabled: false`, then `git revert` the debug commit |
| Summary | The template is printed last; an unapproved issue gets no status comment | `stages.summary.enabled: false`, then `git revert` the summary commit |
| Budget policy | A projected overrun stops before the next stage writes; security blocking stays | `stages.budget_policy.enabled: false`, then `git revert` the budget-policy commit |
| Clarity | A vague issue stops before spec and asks at most three questions | `stages.clarity.enabled: false`, then `git revert` the clarity commit |
| Spec | Offline fixtures: `spec.json` has acceptance ids, seams, and e2e scenarios; a rejected triage writes none | `stages.spec.enabled: false`, then `git revert` the spec commit |
| Plan | Offline fixtures: `plan.json` is an ordered DAG and covers every spec acceptance id; a spec that needs a human writes none | `stages.plan.enabled: false`, then `git revert` the plan commit |
| Implement | Offline fixtures in a temp git repo: one local commit per done ticket; a blocked ticket stops the run; the diff is scanned | `stages.implement.enabled: false`, then `git revert` the implement commit |
| Verify | Offline fake commands: all-green writes `verify.json` without a model fixture; a failure stops the run | `stages.verify.enabled: false`, then `git revert` the verify commit |
| Untested repo | No test command is unverified unless the user opts into that warning, or a harness is bootstrapped | `stages.verification.enabled: false`, then `git revert` the verification commit |
| Flaky tests | A failing test is retried, compared with the base commit, and a pre-existing flake is quarantined | `stages.flaky.enabled: false`, then `git revert` the flaky-tests commit |
| Simplify | Checks stay green, or the simplify commit is dropped | Drop that commit |
| Review | Offline fixtures and a fake codex: 2-of-3 cross-family vote, security block, round cap, missing codex | `stages.review.enabled: false` and `reviewers.codex.enabled: false`, then `git revert` the review commit |
| PR | Offline temp repo and fake MCP: a blocking review opens nothing; merge is denied; an unapproved issue is untouched | `stages.pr.enabled: false`, then `git revert` the PR commit |
| PR | Draft only; the only GitHub MCP tool is `create_pull_request` | Close the draft and disable the stage |
| Retro | Offline metrics plus a fixture: proposals stay unapplied, and a budget halt still writes them | `stages.retro.enabled: false`, then `git revert` the retro commit |
| Metrics | `run_metrics` matches the ledger for that run | Rebuild the index from the run artifacts |
| Issue import | One draft per task, with a JSON block and `pipeline:queued` | Delete the local outbox; do not keep the issues |
| MCP | The policy tests reject merge, Confluence writes, and stdio | Set `enabled: false` and remove the vault from the stage |

Disabling a stage later is `stages.<name>.enabled: false` in the target repo config.
