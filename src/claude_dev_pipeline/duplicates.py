"""Stop a second run or a likely duplicate before spec spends a model budget."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from claude_dev_pipeline.github_gate import admit_issue
from claude_dev_pipeline.ledger import Ledger, LedgerEntry

_WORD = re.compile(r"[a-z0-9]+")
_PATH = re.compile(r"(?<![\w./-])((?:[\w.-]+/)*[\w.-]+\.[\w.-]+)")
_MODEL = "claude-haiku-5.5"


def enabled(config: dict) -> bool:
    return _section(config).get("enabled", True) is not False


def tokens(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if len(word) >= 3}


def paths_of(text: str) -> set[str]:
    return {match.rstrip(".,:;)") for match in _PATH.findall(text)}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def score_pair(issue: dict, other: dict, config: dict) -> dict:
    """Deterministic overlap. Borderline pairs still need a Haiku confirmation."""
    section = _section(config)
    threshold = float(section.get("threshold") or 0.55)
    borderline = float(section.get("borderline") or 0.35)
    text = _text(issue)
    other_text = _text(other)
    overlap = jaccard(tokens(text), tokens(other_text))
    shared = sorted(paths_of(text) & paths_of(other_text))
    if overlap >= threshold or (shared and overlap >= borderline):
        band = "duplicate"
    elif overlap >= borderline or shared:
        band = "borderline"
    else:
        band = "different"
    return {
        "number": other.get("number"),
        "title": other.get("title") or "",
        "score": round(overlap, 4),
        "files": shared,
        "band": band,
    }


def screen(
    issue: dict,
    others: list[dict],
    config: dict,
    *,
    fixture: dict | None = None,
) -> dict:
    """Return the halt decision. A high overlap never asks the model."""
    ranked = []
    for other in others:
        if other.get("state") not in (None, "open"):
            continue
        if other.get("number") == issue.get("number"):
            continue
        ranked.append(score_pair(issue, other, config))
    ranked.sort(key=lambda item: item["score"], reverse=True)
    definite = [item for item in ranked if item["band"] == "duplicate"]
    borderline = [item for item in ranked if item["band"] == "borderline"]
    confirmed: list[dict] = []
    model_called = False
    amount = 0
    if not definite and borderline and isinstance(fixture, dict):
        model_called = True
        allowed = {str(item) for item in fixture.get("confirm") or []}
        amount = int((fixture.get("usage") or {}).get("amount_cents") or 0)
        confirmed = [item for item in borderline if str(item.get("number")) in allowed]
    chosen = definite or confirmed
    return {
        "halted": bool(chosen),
        "reason": "duplicate" if chosen else "",
        "candidates": chosen,
        "borderline": borderline,
        "score": chosen[0]["score"] if chosen else (ranked[0]["score"] if ranked else 0),
        "model_called": model_called and not definite,
        "amount_cents": amount if model_called and not definite else 0,
    }


def open_run(runs_dir: Path, issue_number: int, current: str) -> str:
    """A pipeline run or PR already recorded for this issue number."""
    if not runs_dir.is_dir():
        return ""
    for path in sorted(runs_dir.iterdir()):
        if not path.is_dir() or path.name == current:
            continue
        number = _issue_number(path)
        if number != issue_number:
            continue
        pr_path = path / "pr.json"
        if pr_path.is_file():
            pr = json.loads(pr_path.read_text(encoding="utf-8"))
            branch = str(pr.get("branch") or "")
            if branch.startswith("pipeline/"):
                return pr.get("pr_url") or branch
    db_path = runs_dir.parent / "pipeline.db"
    if not db_path.is_file():
        db_path = runs_dir / "pipeline.db"
    if db_path.is_file():
        return _sqlite_open(db_path, issue_number, current)
    return ""


def apply_duplicate(
    run_dir: Path,
    run_id: str,
    issue: dict,
    report: dict,
    config: dict,
    *,
    run_cap_cents: int,
) -> dict:
    """Write the decision. Comment and label only when gating allows it."""
    report = dict(report)
    report["commented"] = False
    if report.get("halted") and _may_comment(issue, config):
        comment = run_dir / "duplicate-comment.txt"
        if not comment.exists():
            comment.write_text(_comment(report["candidates"]), encoding="utf-8")
            (run_dir / "duplicate-labels.json").write_text(
                json.dumps({"add": ["pipeline:duplicate"]}, indent=2) + "\n",
                encoding="utf-8",
            )
        report["commented"] = comment.is_file()
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "duplicate.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _remember(run_dir.parent / "pipeline.db", run_id, report)
    amount = int(report.get("amount_cents") or 0)
    if report.get("model_called") and amount:
        Ledger(run_dir / "ledger.jsonl").append(
            LedgerEntry(
                run_id=run_id,
                stage="duplicate",
                amount_cents=amount,
                provider="anthropic",
                model=_MODEL,
                effort="low",
                note="confirm",
                budget_cents=amount,
            ),
            run_cap_cents=run_cap_cents,
        )
    return report


def summary_lines(run_dir: Path) -> list[str]:
    path = run_dir / "duplicate.json"
    if not path.is_file():
        return []
    report = json.loads(path.read_text(encoding="utf-8"))
    if not report.get("halted"):
        return []
    if report.get("reason") == "already-open":
        return ["", f"A pipeline run is already open: {report.get('open_run')}."]
    numbers = ", ".join(f"#{item.get('number')}" for item in report.get("candidates") or [])
    return ["", f"Stopped as a duplicate of {numbers}."]


def _comment(candidates: list[dict]) -> str:
    lines = ["This looks like a duplicate.", ""]
    for item in candidates:
        lines.append(f"- #{item.get('number')}: {item.get('title')}")
    lines.append("")
    lines.append("<!-- pipeline-duplicate -->")
    return "\n".join(lines) + "\n"


def _may_comment(issue: dict, config: dict) -> bool:
    if not admit_issue(
        labels=list(issue.get("labels") or []),
        timeline=list(issue.get("timeline") or []),
    ).allowed:
        return False
    author = str(issue.get("author") or (issue.get("user") or {}).get("login") or "")
    allowed = _section(config).get("allowed_authors") or ["EladAriel"]
    return author in {str(item) for item in allowed}


def _text(issue: dict) -> str:
    return f"{issue.get('title') or ''}\n{issue.get('body') or ''}"


def _issue_number(run_dir: Path) -> int | None:
    path = run_dir / "issue.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    number = data.get("number")
    return int(number) if isinstance(number, int) else None


def _sqlite_open(path: Path, issue_number: int, current: str) -> str:
    conn = sqlite3.connect(path)
    try:
        found = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in found}
        if "runs" not in tables:
            return ""
        rows = conn.execute(
            "SELECT run_id, status FROM runs WHERE issue_ref = ?",
            (str(issue_number),),
        ).fetchall()
    except sqlite3.Error:
        return ""
    finally:
        conn.close()
    for run_id, status in rows:
        if str(run_id) == current:
            continue
        if str(status or "") not in {"running", "open", "started"}:
            continue
        return str(run_id)
    return ""


def _remember(path: Path, run_id: str, report: dict) -> None:
    if not report.get("candidates"):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS duplicate_candidates (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT,
              issue_number TEXT,
              score REAL,
              band TEXT
            )
            """
        )
        for item in report["candidates"]:
            conn.execute(
                """
                INSERT INTO duplicate_candidates (run_id, issue_number, score, band)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, str(item.get("number")), float(item.get("score") or 0), item.get("band")),
            )
        conn.commit()
    finally:
        conn.close()


def _section(config: dict) -> dict:
    return (config.get("stages") or {}).get("duplicates") or {}
