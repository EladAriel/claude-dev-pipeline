---
description: Run the local pipeline CLI and report status plus the PR link
argument-hint: "[task text]"
---

Run the local `pipeline` CLI from the repository root. Do not call Anthropic,
OpenAI, or GitHub. Do not merge.

The user's words are the task, unless they name a spec file or an issue number:

```bash
pipeline run "$ARGUMENTS"
```

Use `pipeline run --spec <file>` or `pipeline run --issue <n>` instead when
that is what they asked for. An issue runs only when a local snapshot shows
that the owner applied `agent:approved`.

Then run:

```bash
pipeline status
```

Report the run id, the intake, the status, and the PR link from that output.
Say that no pull request is open when the line is `pr: none`.

Herdr is optional. If it is installed and the user asked for a background
pane, follow `docs/herdr.md`. Otherwise run the CLI in this session.

This is a dry-run scaffold. A live model session is not available here.
