# Docs lane

`stages.docs_lane.enabled` defaults on. Triage routes a change to this lane only when every path named in the issue is documentation or non-executable config (markdown, `docs/**`, editor and lint config).

The lane skips spec and plan, skips the test-harness bootstrap, and runs lint, format, link, and schema checks when those commands are configured. Review keeps the bug-hunt check and leaves Codex off. The run cap is lowered to `budget_cents` (default 200).

CI workflows, dependency manifests, Docker and Terraform files, and source files never enter this lane. A workflow file is treated as security-sensitive. If the implemented diff later touches code or runtime config, the lane escalates to the normal review.

No model call decides the lane.

## Verify

```bash
python3 -m pytest -q tests/test_docs_lane.py
```

## Roll back

Set `stages.docs_lane.enabled: false`, then `git revert` the docs-lane commit.
