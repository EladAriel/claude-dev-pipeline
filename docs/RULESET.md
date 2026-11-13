# `main` ruleset

The owner applies this ruleset. This scaffold does not change repository settings.

| Rule | Setting |
| --- | --- |
| Pull request required before merging | Yes, with 0 required approvals |
| Code owner review required | No |
| Conversation resolution | Yes |
| Required status checks | `ci`, `secret-scan`, `pipeline/review` |
| Branches up to date | Yes |
| Force pushes and branch deletion | Blocked |
| Bypass list | Empty |
| Who merges | The owner, in the pull request UI |

`pipeline/review` is a commit status the local orchestrator will set per head
SHA in a later stage. It is green only when no important finding and no
security finding is open and every finding has a resolution. A new commit
makes it pending again.

No agent path merges. `CODEOWNERS` names `@EladAriel` and is not a required review.
