import json
import sqlite3
from pathlib import Path

from claude_dev_pipeline.migrations import assess, local_database, note
from claude_dev_pipeline.stages.pr import render_pr


def test_tools_rollback_and_destructive_sql(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "alembic" / "versions").mkdir(parents=True)
    up = repo / "alembic" / "versions" / "001_add.py"
    up.write_text("def upgrade():\n    pass\n", encoding="utf-8")
    report = assess(repo, ["alembic/versions/001_add.py"], "Add a column.", {})
    assert report["tool"] == "alembic"
    assert report["tier"] == "L"
    assert report["rollback"] is False
    assert any(item["kind"] == "missing-rollback" for item in report["findings"])

    (repo / "migrations").mkdir()
    (repo / "migrations" / "001.up.sql").write_text(
        "ALTER TABLE users DROP COLUMN name;\n", encoding="utf-8"
    )
    (repo / "migrations" / "001.down.sql").write_text(
        "ALTER TABLE users ADD COLUMN name text;\n", encoding="utf-8"
    )
    allowed = assess(
        repo,
        ["migrations/001.up.sql", "migrations/001.down.sql"],
        "Please drop column name.",
        {},
    )
    assert allowed["rollback"] is True
    assert allowed["blocking"] is False

    blocked = assess(
        repo,
        ["migrations/001.up.sql", "migrations/001.down.sql"],
        "Tidy the schema.",
        {},
    )
    assert any(item["kind"] == "drop" and item["blocking"] for item in blocked["findings"])
    assert (
        assess(repo, ["README.md"], "docs", {"stages": {"migrations": {"enabled": False}}})["lane"]
        == ""
    )


def test_local_sqlite_only_and_checklist(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "migrations").mkdir(parents=True)
    (repo / "migrations" / "001.up.sql").write_text("CREATE TABLE t (id int);\n", encoding="utf-8")
    (repo / "migrations" / "001.down.sql").write_text("DROP TABLE t;\n", encoding="utf-8")
    run = tmp_path / "runs" / "run-1"
    command = "python3 -c \"import sqlite3; sqlite3.connect('local.db').close()\""
    report = note(
        run,
        repo,
        ["migrations/001.up.sql", "migrations/001.down.sql"],
        "Create table t.\nRollback reason: drop the new table.",
        {"stages": {"migrations": {"local_command": command}}},
    )
    assert report["database"]["ran"] is True
    assert report["database"]["exit_code"] == 0
    assert (repo / "local.db").is_file()
    refused = local_database(
        repo, {"stages": {"migrations": {"local_command": "psql postgres://user:pw@db/app"}}}
    )
    assert refused["ran"] is False
    (run / "spec.json").write_text(json.dumps({"title": "Migrate"}), encoding="utf-8")
    (run / "plan.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")
    (run / "review.json").write_text("{}", encoding="utf-8")
    _title, body, _cost = render_pr(run, issue_number=None, usage_cents=0)
    assert "Migration checklist" in body
    conn = sqlite3.connect(run / "migrations.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM migrations").fetchone()[0] == 1
