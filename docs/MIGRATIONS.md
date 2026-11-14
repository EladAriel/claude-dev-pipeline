# Schema migrations

`stages.migrations.enabled` defaults on. A change that matches the migration table (Alembic, Django, Prisma, Rails, Flyway, Liquibase, Knex, golang-migrate, or a `migrations/*.sql` directory) is tier L. Review is Deep and Codex stays on.

The change needs a down migration (`*.down.sql` or `downgrade`/`down`) or a `Rollback reason:` line in the issue. `DROP`, column rename, `SET NOT NULL`, `LOCK TABLE`, and `BACKFILL` block unless the issue names that operation. The PR body gets a migration checklist. Nothing here merges.

`local_command` runs only when it does not point at a remote database or a credential. A missing command skips the database.

## Verify

```bash
python3 -m pytest -q tests/test_migrations.py
```

## Roll back

Set `stages.migrations.enabled: false`, then `git revert` the migrations commit.
