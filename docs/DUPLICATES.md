# Duplicate issues

`stages.duplicates.enabled` defaults on. Before spec, triage compares the issue with open issues supplied offline, with `pipeline/*` runs already on disk, and with the SQLite `runs` index.

Token overlap and shared file paths decide a clear duplicate. A score between `borderline` and `threshold` is confirmed only by a Haiku fixture. The fixture never overrides a clear duplicate, and a missing fixture does not guess.

A duplicate stops the run, writes one comment, and records `pipeline:duplicate`. The comment and the label are written only when `agent:approved` gating allows it and the author is in `allowed_authors` (default `EladAriel`). The issue is not closed.

If this issue already has a pipeline run or a `pipeline/*` pull request, a second run stops unless `--force`.

## Verify

```bash
python3 -m pytest -q tests/test_duplicates.py
```

## Roll back

Set `stages.duplicates.enabled: false`, then `git revert` the duplicates commit.
