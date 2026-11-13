"""One cheap tie-break when Claude and Codex disagree at confidence 80 or higher."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from claude_dev_pipeline.budget import cheapest_model

FLOOR = 80
_CLAUDE = {"claude", "anthropic"}
_CODEX = {"openai", "codex"}
_POOL = ("claude-haiku-5.5", "claude-sonnet-5.5", "claude-opus-5.5", "gpt-6.1-sol")


def enabled(config: dict) -> bool:
    section = (config.get("stages") or {}).get("reconcile") or {}
    return section.get("enabled", True) is not False


def tie_model(used: set[str]) -> str:
    """Cheapest priced model that is neither of the two that disagreed."""
    pool = [name for name in _POOL if name not in used]
    return cheapest_model(pool) or "claude-haiku-5.5"


def apply_round(
    findings: list[dict],
    votes: dict[str, list[dict]],
    decisions: dict,
    config: dict,
) -> tuple[list[dict], list[dict], int]:
    """Return updated findings, agreement rows, and tie-break cents. Nothing is auto-tuned."""
    if not enabled(config):
        return findings, [], 0
    section = (config.get("stages") or {}).get("reconcile") or {}
    cap = max(0, int(section.get("max_per_round") or 3))
    max_cents = int(section.get("max_cents") if section.get("max_cents") is not None else 5)
    used = 0
    spent = 0
    rows: list[dict] = []
    updated: list[dict] = []
    for finding in findings:
        ballot = list(votes.get(str(finding.get("id")), []))
        if not _split(ballot):
            updated.append(finding)
            paired = _pair(ballot)
            if paired is not None:
                rows.append({**paired, "finding_id": finding["id"], "false_positive": 0})
            continue
        decision = decisions.get(str(finding["id"])) if isinstance(decisions, dict) else None
        decision = decision if isinstance(decision, dict) else {}
        amount = int(decision.get("amount_cents") or 0)
        over = used >= cap or amount < 0 or spent + amount > max_cents
        if over:
            updated.append(_disputed(finding))
            rows.append(_row(ballot, finding["id"], 1))
            continue
        used += 1
        spent += amount
        expected = tie_model({str(finding.get("model") or ""), "gpt-6.1-sol"})
        if _holds(finding, decision, expected) and not finding.get("security"):
            kept = dict(finding)
            kept["kept"] = True
            kept["disputed"] = False
            kept["tie_break"] = expected
            updated.append(kept)
            rows.append(_row(ballot, finding["id"], 0))
        else:
            cited = expected if _holds(finding, decision, expected) else ""
            updated.append(_disputed(finding, model=cited))
            false_positive = 0 if finding.get("security") else 1
            rows.append(
                {
                    **_pair(ballot, default=True),
                    "finding_id": finding["id"],
                    "false_positive": false_positive,
                }
            )
    return updated, rows, spent


def _row(ballot: list[dict], finding_id: str, false_positive: int) -> dict:
    paired = _pair(ballot, default=True) or {}
    return {
        **paired,
        "finding_id": finding_id,
        "false_positive": false_positive,
    }


def dispute_lines(review: dict) -> list[str]:
    disputed = [item for item in review.get("findings") or [] if item.get("disputed")]
    if not disputed:
        return []
    lines = ["Disputed findings:"]
    for item in disputed:
        state = "still blocking" if item.get("blocks") else "not blocking"
        lines.append(f"- {item.get('id')} ({state}): {item.get('summary')}")
    lines.append("")
    return lines


def persist(run_dir: Path, run_id: str, rows: list[dict]) -> None:
    if not rows:
        return
    agreed = sum(1 for row in rows if row.get("agreed"))
    rate = round(agreed / len(rows), 4)
    payload = {"run_id": run_id, "rows": rows, "agreement_rate": rate}
    (run_dir / "agreement.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path = run_dir.parent / "pipeline.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_agreement (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT,
              finding_id TEXT,
              claude TEXT,
              codex TEXT,
              agreed INTEGER,
              false_positive INTEGER
            )
            """
        )
        conn.execute("DELETE FROM model_agreement WHERE run_id = ?", (run_id,))
        for row in rows:
            conn.execute(
                """
                INSERT INTO model_agreement (
                  run_id, finding_id, claude, codex, agreed, false_positive
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    row.get("finding_id"),
                    row.get("claude"),
                    row.get("codex"),
                    1 if row.get("agreed") else 0,
                    int(row.get("false_positive") or 0),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def agreement_rate(run_dir: Path) -> float | None:
    path = run_dir / "agreement.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    value = data.get("agreement_rate")
    return None if value is None else float(value)


def threshold_proposals(run_dir: Path) -> list[dict]:
    """Suggest a threshold change. The caller must leave it unapplied."""
    path = run_dir / "agreement.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("rows") or []
    misses = sum(int(row.get("false_positive") or 0) for row in rows)
    rate = data.get("agreement_rate")
    if not misses and rate in (None, 1, 1.0):
        return []
    return [
        {
            "id": "reconcile-threshold",
            "category": "review",
            "severity": "low",
            "evidence": f"agreement rate {rate}; false positives {misses}",
            "proposed_change": "Consider raising the disagreement confidence floor above 80.",
            "target": "config",
            "expected_metric": "agreement_rate",
            "status": "proposed",
            "applied": False,
        }
    ]


def _disputed(finding: dict, model: str = "") -> dict:
    row = dict(finding)
    row["disputed"] = True
    row["tie_break"] = model or None
    if finding.get("security") and finding.get("blocks"):
        row["blocks"] = True
        row["kept"] = True
    else:
        row["blocks"] = False
        row["kept"] = False
        row["survived"] = False
    row["tag"] = "disputed"
    return row


def _holds(finding: dict, decision: dict, expected: str) -> bool:
    if str(decision.get("model") or "") != expected:
        return False
    needle = f"{finding.get('file')}:{finding.get('line_start')}"
    if needle not in str(decision.get("evidence") or ""):
        return False
    repro = str(decision.get("repro") or decision.get("failing_test") or "").strip()
    return bool(repro)


def _split(votes: list[dict]) -> bool:
    claude_yes = any(_family(vote, _CLAUDE) and _yes(vote) for vote in votes)
    claude_no = any(_family(vote, _CLAUDE) and _no(vote) for vote in votes)
    codex_yes = any(_family(vote, _CODEX) and _yes(vote) for vote in votes)
    codex_no = any(_family(vote, _CODEX) and _no(vote) for vote in votes)
    return (claude_yes and codex_no) or (codex_yes and claude_no)


def _pair(votes: list[dict], default: bool = False) -> dict | None:
    claude = _stance(votes, _CLAUDE)
    codex = _stance(votes, _CODEX)
    if claude == "absent" or codex == "absent":
        if not default:
            return None
    return {"claude": claude, "codex": codex, "agreed": claude == codex and claude in {"yes", "no"}}


def _stance(votes: list[dict], families: set[str]) -> str:
    if any(_family(vote, families) and _yes(vote) for vote in votes):
        return "yes"
    if any(_family(vote, families) and _no(vote) for vote in votes):
        return "no"
    return "absent"


def _family(vote: dict, families: set[str]) -> bool:
    return str(vote.get("family") or "") in families


def _yes(vote: dict) -> bool:
    return str(vote.get("real")) == "yes" and int(vote.get("confidence") or 0) >= FLOOR


def _no(vote: dict) -> bool:
    return str(vote.get("real")) == "no" and int(vote.get("confidence") or 0) >= FLOOR
