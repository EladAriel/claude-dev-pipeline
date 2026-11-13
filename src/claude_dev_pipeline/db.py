"""SQLite index over run artifacts. The JSON files remain the source of truth."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_MIGRATION = Path(__file__).with_name("migrations") / "001_initial.sql"
_DELETE_ORDER = (
    "votes",
    "resolutions",
    "usage",
    "findings",
    "stages",
    "retro_items",
    "incidents",
    "run_metrics",
    "runs",
)


def init_db(path: Path) -> None:
    """Create the index if needed. Sets user_version to 1 and WAL mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > 1:
            raise RuntimeError(f"unsupported pipeline.db user_version {version}")
        if version < 1:
            conn.executescript(_MIGRATION.read_text(encoding="utf-8"))
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
    finally:
        conn.close()


def connect(path: Path) -> sqlite3.Connection:
    init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def rebuild_db(runs_dir: Path, db_path: Path) -> int:
    """Replace the index with rows read from ``runs/<id>/`` artifacts.

    Returns the number of runs indexed. Missing optional files are skipped.
    A malformed artifact raises and leaves the caller to discard the file.
    """
    init_db(db_path)
    conn = connect(db_path)
    try:
        for table in _DELETE_ORDER:
            conn.execute(f"DELETE FROM {table}")
        count = 0
        if runs_dir.exists():
            for run_dir in sorted(path for path in runs_dir.iterdir() if path.is_dir()):
                run_path = run_dir / "run.json"
                if not run_path.exists():
                    continue
                _index_run(conn, run_dir, json.loads(run_path.read_text(encoding="utf-8")))
                count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def _index_run(conn: sqlite3.Connection, run_dir: Path, run: dict) -> None:
    run_id = str(run["run_id"])
    conn.execute(
        """
        INSERT INTO runs (
          run_id, repo, issue_ref, pipeline_version, started_at, ended_at, status,
          risk_tier, review_depth, run_cap_usd, outcome, verified_by,
          intake_kind, intake_ref, pr_url
        ) VALUES (
          :run_id, :repo, :issue_ref, :pipeline_version, :started_at, :ended_at, :status,
          :risk_tier, :review_depth, :run_cap_usd, :outcome, :verified_by,
          :intake_kind, :intake_ref, :pr_url
        )
        """,
        {
            "run_id": run_id,
            "repo": run.get("repo"),
            "issue_ref": run.get("issue_ref"),
            "pipeline_version": run.get("pipeline_version"),
            "started_at": run.get("started_at"),
            "ended_at": run.get("ended_at"),
            "status": run.get("status"),
            "risk_tier": run.get("risk_tier"),
            "review_depth": run.get("review_depth"),
            "run_cap_usd": run.get("run_cap_usd"),
            "outcome": run.get("outcome"),
            "verified_by": run.get("verified_by"),
            "intake_kind": run.get("intake_kind"),
            "intake_ref": run.get("intake_ref"),
            "pr_url": run.get("pr_url"),
        },
    )
    ledger_path = run_dir / "ledger.jsonl"
    if ledger_path.exists():
        for raw in ledger_path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                _index_ledger_line(conn, json.loads(raw))
    metrics_path = run_dir / "metrics.json"
    if metrics_path.exists():
        _index_metrics(conn, run_id, json.loads(metrics_path.read_text(encoding="utf-8")))


def _index_ledger_line(conn: sqlite3.Connection, row: dict) -> None:
    cursor = conn.execute(
        """
        INSERT INTO stages (
          run_id, name, provider, model, status, budget_cents, gate_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["run_id"],
            row["stage"],
            row.get("provider"),
            row.get("model"),
            row.get("status") or "recorded",
            int(row["budget_cents"]) if row.get("budget_cents") else int(row["amount_cents"]),
            row.get("gate_message") or row.get("note"),
        ),
    )
    amount = int(row["amount_cents"])
    conn.execute(
        """
        INSERT INTO usage (
          stage_id, input_tokens, cache_write_tokens, cache_read_tokens, output_tokens,
          web_searches, list_cost_usd, runtime_cost_usd
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cursor.lastrowid,
            int(row.get("input_tokens", 0)),
            int(row.get("cache_write_tokens", 0)),
            int(row.get("cache_read_tokens", 0)),
            int(row.get("output_tokens", 0)),
            int(row.get("web_searches", 0)),
            amount / 100,
            0,
        ),
    )


def latest_run(db_path: Path) -> dict | None:
    """Return the newest indexed run, or None when the database does not exist."""
    if not db_path.exists():
        return None
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC, run_id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def write_run_artifact(runs_dir: Path, db_path: Path, payload: dict) -> str:
    """Write ``run.json`` first, then rebuild the sqlite index from ``runs_dir``."""
    run_id = str(payload["run_id"])
    dest = runs_dir / run_id
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "run.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    rebuild_db(runs_dir, db_path)
    return run_id


def _index_metrics(conn: sqlite3.Connection, run_id: str, metrics: dict) -> None:
    conn.execute(
        """
        INSERT INTO run_metrics (
          run_id, success, first_try_success, loops, cost_usd, wall_seconds,
          trajectory_violations, hidden_failure, owner_interventions, rounds_to_pass,
          recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            metrics.get("success"),
            metrics.get("first_try_success"),
            metrics.get("loops"),
            metrics.get("cost_usd"),
            metrics.get("wall_seconds"),
            metrics.get("trajectory_violations"),
            metrics.get("hidden_failure"),
            metrics.get("owner_interventions"),
            metrics.get("rounds_to_pass"),
            metrics.get("recorded_at"),
        ),
    )
