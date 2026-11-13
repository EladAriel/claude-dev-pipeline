# Herdr (optional)

Herdr is not required. The scaffold does not launch it, and it does not run
`codex exec`. The commands below are the ones to use later, from a machine
where Herdr is installed. They are dry-run examples: they point at
`pipeline`, which in this scaffold calls no paid API.

Docs: https://herdr.dev/tui/docs/agent-automation/ and https://herdr.dev/core/.

## Orchestrator in a pane

The pipeline process is an ordinary terminal command, not a coding agent.
Put it in a pane:

```bash
herdr pane run "$pane_id" "pipeline run --dry-run --config config/pipeline.config.example.yaml"
```

A Claude Code session can start that in the background with herdr-core and
read the report when it finishes:

```bash
hc run --bg --name pipeline -- pipeline run "describe the change"
hc wait pipeline
hc read --lines 80 pipeline
```

To have a helper report back into the session that started it, the helper
prompt ends with `Done: hc send $HC_TERMINAL_ID`. That line is for an agent
prompt. The pipeline CLI itself does not send it.

`pipeline status` prints the latest local run id, the intake, and `pr: none`
until a later stage records a pull request URL.

## Codex review as a Herdr agent

The local review command is `codex exec --sandbox read-only`. Herdr can host
that executable as a Codex agent. Do not run it from this scaffold.

```bash
herdr agent start reviewer --kind codex --pane "$review_pane" -- exec --sandbox read-only
```

The herdr-core form, which returns immediately and asks the helper to report
back:

```bash
hc run --bg --name review codex "exec --sandbox read-only. Done: hc send $HC_TERMINAL_ID"
```

`--kind codex` is the TUI agent kind. `hc run` names the `codex` executable
directly. Either way the sandbox stays read-only, and the result is a report,
not a merge.
