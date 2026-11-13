import json
import sqlite3

import pytest

from claude_dev_pipeline.db import connect, init_db, latest_run, rebuild_db


def test_init_is_idempotent_and_uses_wal(tmp_path):
    db_path = tmp_path / "pipeline.db"
    init_db(db_path)
    init_db(db_path)
    conn = connect(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert version == 1
    assert str(mode).lower() == "wal"
    assert "run_metrics" in tables
    assert "golden_tasks" not in tables
    assert "eval_runs" not in tables
    assert "eval_results" not in tables


def test_foreign_key_rejects_an_orphan_usage_row(tmp_path):
    db_path = tmp_path / "pipeline.db"
    conn = connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO usage (stage_id, input_tokens) VALUES (999, 1)")
    finally:
        conn.close()


def test_rebuild_round_trips_a_fixture_run(tmp_path):
    runs = tmp_path / "runs"
    run_dir = runs / "run_fixture"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "run_fixture",
                "repo": "example/app",
                "issue_ref": "12",
                "pipeline_version": "0.1.0",
                "started_at": "2026-11-13T00:00:00+00:00",
                "ended_at": "2026-11-13T00:05:00+00:00",
                "status": "dry-run",
                "run_cap_usd": 20,
                "intake_kind": "issue",
                "intake_ref": "12",
                "pr_url": None,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "ledger.jsonl").write_text(
        json.dumps(
            {
                "run_id": "run_fixture",
                "stage": "triage",
                "amount_cents": 1,
                "provider": "anthropic",
                "model": "claude-haiku-5.5",
                "input_tokens": 10,
                "output_tokens": 3,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "metrics.json").write_text(
        json.dumps({"success": 1, "cost_usd": 0.01, "trajectory_violations": 0}),
        encoding="utf-8",
    )
    db_path = tmp_path / "pipeline.db"
    assert rebuild_db(runs, db_path) == 1
    row = latest_run(db_path)
    assert row is not None
    assert row["intake_kind"] == "issue"
    assert row["pr_url"] is None
    conn = connect(db_path)
    try:
        usage = conn.execute("SELECT list_cost_usd, input_tokens FROM usage").fetchone()
        metrics = conn.execute("SELECT trajectory_violations FROM run_metrics").fetchone()
    finally:
        conn.close()
    assert usage["list_cost_usd"] == pytest.approx(0.01)
    assert usage["input_tokens"] == 10
    assert metrics["trajectory_violations"] == 0
