# Underspecified issues

Triage scores clarity from the issue text before any spec budget is spent.
A bug is checked for a goal, acceptance criteria, repro steps, and scope. An
enhancement skips the repro check. The score is the share of those parts that
are present. The default threshold is 75, and at most 3 questions are asked.

Below the threshold the run stops after triage. Spec does not start. When the
issue is `agent:approved` and its author is in `allowed_authors`, one comment
is written with those questions and the label `pipeline:needs-info` is
recorded. The comment is not posted to GitHub. `--ask-inline` prints the
questions in the session and does not write the comment.

The checkpoint keeps the run so `pipeline resume` can re-score an updated
issue. If the config is unchanged, the new issue text is allowed without
`--force`. A passing score continues from spec, and the answers are appended
to `agent-brief.md`.

Spec still must not invent a requirement. Each assumption is explicit and
quoted. An unconfirmed assumption needs a question.

The clarity score and whether the run halted are stored on the metrics.

## Verify

```bash
pytest tests/test_clarity.py
```

## Rollback

```yaml
stages:
  clarity:
    enabled: false
```

Triage then routes straight to spec. To remove the check:

```bash
git revert <clarity-commit>
```
