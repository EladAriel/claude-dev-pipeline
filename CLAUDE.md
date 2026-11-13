# claude-dev-pipeline

Scaffold. From the repo root:

```bash
ruff check .
pytest
pipeline scan-paths
pipeline --dry-run --config config/pipeline.config.example.yaml
pipeline status
```

- Design: `docs/DESIGN.md`
- Owner decisions: `docs/OWNER_DECISIONS.md`
- Issues and the Artifact note: `docs/GITHUB_ISSUES.md`
- Optional herdr: `docs/herdr.md`
- Slash command: `.claude/commands/pipeline.md`

Do not call Anthropic, OpenAI, or GitHub from this scaffold.
