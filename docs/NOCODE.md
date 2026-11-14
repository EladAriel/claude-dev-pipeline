# No-code lane

`stages.nocode.enabled` defaults on. An issue that asks a question, requests research, a design discussion, or an ops note, and does not name a source file, skips spec, plan, implement, and the pull request.

The answer is `runs/<id>/answer.md`. A fixture may supply that draft; otherwise a short template is used. One comment is written only when `agent:approved` gating allows it and the author is allowed. Strangers get the local answer and no comment.

If the draft says a code change is needed, the run stops until `--approve-nocode`. That flag continues into the normal lane. No branch is created before that approval.

## Verify

```bash
python3 -m pytest -q tests/test_nocode.py
```

## Roll back

Set `stages.nocode.enabled: false`, then `git revert` the no-code commit.
