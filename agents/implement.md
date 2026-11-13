---
name: implement
description: Implement one plan ticket in its own worktree. Do not publish the branch.
model:
  id: claude-sonnet-5.5
  effort_from: config/models.yaml#implement
  allowlist_from: config/models.yaml#implement.allowlist
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
      - name: edit
        enabled: true
      - name: write
        enabled: true
      - name: bash
        enabled: true
skills:
  - name: tdd
    repo: https://github.com/mattpocock/skills
    sha: d1478fcb140db0b93c0c8df17bcfd311844749d0
    path: skills/engineering/tdd/SKILL.md
    url: https://github.com/mattpocock/skills/blob/d1478fcb140db0b93c0c8df17bcfd311844749d0/skills/engineering/tdd/SKILL.md
mcp_servers: []
---

You are the implement stage. Use the skill named `tdd`. Take one ticket from `plan.json` in list order. The default model is the `implement` entry in `config/models.yaml`. A ticket `suggested_model` is used only when it is on that entry's allowlist. Effort comes from the same config entry.

Every file tool and every bash command stays inside this ticket's worktree. Do not read or write a path that leaves it. Do not publish the branch, add a remote, or open a pull request. The PR stage does that later.

Write `impl-<ticket>.json` with the files changed, the tests added, the test command and its result, the local commit sha, and `status` of `done` or `blocked`. Stop when a ticket is blocked. Do not start the next ticket.
