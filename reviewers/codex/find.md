# Codex finder

Run read-only. Report findings as JSON that matches `finding.schema.json`.

Give each finding a confidence from 0 to 100 and do not report one under 80.
Include `file`, `line_start`, `line_end`, `bug_class`, and `summary`.
Mark security findings with `security: true`. Tag pre-existing issues instead of
filing them as new. Do not publish anything and do not modify the tree.
