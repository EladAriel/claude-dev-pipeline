# Monorepo scope

`stages.monorepo.enabled` defaults on. The run looks for workspace manifests: npm/yarn `package.json` workspaces, `pnpm-workspace.yaml`, `go.work`, Cargo `[workspace]`, uv/poetry members in `pyproject.toml`, and `nx.json` / `turbo.json` / Bazel / Pants package directories. A file outside those lists uses the nearest manifest directory.

Only the affected packages are recorded. `max_files` and `max_tokens` cap what a stage is allowed to read; the rest of the tree is not loaded. `sparse: true` turns on a local sparse checkout of those packages. Tests stay on the affected packages unless the tier is in `full_suite_tiers` (default L and XL).

## Verify

```bash
python3 -m pytest -q tests/test_monorepo.py
```

## Roll back

Set `stages.monorepo.enabled: false`, then `git revert` the monorepo commit.
