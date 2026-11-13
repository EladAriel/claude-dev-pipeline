---
name: debug
description: Reproduce a bug and write a diagnosis. Do not push or open a pull request.
model:
  id: claude-haiku-5.5
  effort: low
tools:
  - type: agent_toolset_20260401
    default_config:
      enabled: false
    configs:
      - name: read
        enabled: true
      - name: grep
        enabled: true
      - name: glob
        enabled: true
      - name: bash
        enabled: true
        read_only: true
skills:
  - name: diagnosing-bugs
    repo: https://github.com/mattpocock/skills
    sha: d1478fcb140db0b93c0c8df17bcfd311844749d0
    path: skills/engineering/diagnosing-bugs/SKILL.md
mcp_servers: []
---

You are the debug stage. Use the pinned `diagnosing-bugs` skill. Work only in the scratch worktree. Do not commit, push, or open a pull request.

Reproduce the report, list hypotheses, and add one failing test. Then write the diagnosis: root cause, evidence, confidence, and a suggested fix. Stay on Haiku unless the stage allowlist names Sonnet for a later fix.
