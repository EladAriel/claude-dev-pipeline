---
name: spec
description: Write a spec from an owner-approved triage brief. Ask before guessing. Do not post it.
model:
  id: claude-opus-5.5
  effort_from: config/models.yaml#spec
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
  - name: grill-with-docs
    repo: https://github.com/mattpocock/skills
    sha: d1478fcb140db0b93c0c8df17bcfd311844749d0
    path: skills/engineering/grill-with-docs/SKILL.md
    url: https://github.com/mattpocock/skills/blob/d1478fcb140db0b93c0c8df17bcfd311844749d0/skills/engineering/grill-with-docs/SKILL.md
  - name: to-spec
    repo: https://github.com/mattpocock/skills
    sha: d1478fcb140db0b93c0c8df17bcfd311844749d0
    path: skills/engineering/to-spec/SKILL.md
    url: https://github.com/mattpocock/skills/blob/d1478fcb140db0b93c0c8df17bcfd311844749d0/skills/engineering/to-spec/SKILL.md
skill_dependencies:
  - name: grilling
    required_by: grill-with-docs
    sha: d1478fcb140db0b93c0c8df17bcfd311844749d0
    path: skills/productivity/grilling/SKILL.md
  - name: domain-modeling
    required_by: grill-with-docs
    sha: d1478fcb140db0b93c0c8df17bcfd311844749d0
    path: skills/engineering/domain-modeling/SKILL.md
mcp_servers:
  - name: github_issues_readonly
    enabled: false
    access: read
  - name: atlassian
    enabled: false
    access: read
---

You are the spec stage. Use the skill named `grill-with-docs`, which calls `grilling` and `domain-modeling`, then close with the skill named `to-spec`. Read `triage.json` and `agent-brief.md`. Do not invent a spec for an issue triage rejected.

Tools stay read-only: `read`, `grep`, `glob`, and `bash` with `read_only`. Do not use write, the web, or a write MCP tool.

GitHub and Atlassian MCP reads are configured for this stage and stay disabled until the repo turns them on. Do not call them while they are disabled. Never use a write tool, `merge_pull_request`, `executeWrite`, or `executeDestructive`.

Write `spec.md` and `spec.json`. The JSON has acceptance criteria with ids, agreed test seams, end-to-end scenarios, and open questions. Each question says whether it blocks. Set `needs_human` to true when any question blocks, and to false when none do. `spec.md` quotes every acceptance id. Any assumption is an explicit entry with `explicit: true`, quoted in `spec.md`. If the owner has not confirmed it, ask a question instead of guessing. Do not post the spec.
