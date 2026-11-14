"""Schema migrations force a full review and a rollback story."""

from __future__ import annotations

import fnmatch
import json
import re
import shlex
import sqlite3
import subprocess
from pathlib import Path

_TOOLS = {
    "alembic": ["alembic/versions/*.py", "**/alembic/versions/*.py"],
    "django": ["*/migrations/*.py", "**/migrations/*.py"],
    "prisma": ["prisma/migrations/**/migration.sql", "prisma/schema.prisma"],
    "rails": ["db/migrate/*.rb"],
    "flyway": ["db/migration/*.sql"],
    "liquibase": ["**/db.changelog*.xml", "**/db.changelog*.yaml"],
    "knex": ["db/knex/migrations/*"],
    "golang-migrate": ["migrations/*.up.sql", "migrations/*.down.sql"],
    "sql": ["migrations/*.sql", "db/sql/*.sql"],
}
_DESTRUCTIVE = (
    (re.compile(r"\bDROP\s+(TABLE|COLUMN|INDEX|SCHEMA|DATABASE)\b", re.I), "drop"),
    (re.compile(r"\bRENAME\s+(COLUMN|TO)\b", re.I), "rename"),
    (re.compile(r"\bSET\s+NOT\s+NULL\b", re.I), "not-null"),
    (re.compile(r"\bLOCK\s+TABLE\b", re.I), "lock"),
    (re.compile(r"\bBACKFILL\b", re.I), "backfill"),
)
_ORDER = ("S", "M", "L", "XL")


def enabled(config: dict) -> bool:
    return _section(config).get("enabled", True) is not False


def tools(config: dict) -> dict[str, list[str]]:
    custom = _section(config).get("tools")
    if isinstance(custom, dict) and custom:
        return {str(key): [str(item) for item in value] for key, value in custom.items()}
    return {key: list(value) for key, value in _TOOLS.items()}


def detect(repo: Path, files: list[str], config: dict) -> dict:
    """Return the migration tool and files. Empty when nothing matches."""
    if not enabled(config) or not isinstance(repo, Path):
        return {"lane": ""}
    matched: dict[str, list[str]] = {}
    for path in files:
        for name, patterns in tools(config).items():
            if _glob_match(path, patterns):
                matched.setdefault(name, []).append(path)
    if not matched:
        return {"lane": ""}
    tool = sorted(matched)[0]
    return {"lane": "migration", "tool": tool, "files": matched[tool], "tier": "L"}


def destructive(text: str, issue_text: str) -> list[dict]:
    """Blocking ops the issue did not explicitly request."""
    findings = []
    lowered = issue_text.lower()
    for pattern, kind in _DESTRUCTIVE:
        if pattern.search(text) and kind.replace("-", " ") not in lowered and kind not in lowered:
            findings.append({"kind": kind, "blocking": True})
    if re.search(r"\bNOT\s+NULL\b", text, re.I) and not re.search(r"\bDEFAULT\b", text, re.I):
        if "not null" not in lowered:
            findings.append({"kind": "not-null", "blocking": True})
    return findings


def has_rollback(files: list[str], texts: dict[str, str], issue_text: str) -> bool:
    if re.search(r"rollback reason\s*:", issue_text, re.I):
        return True
    for path in files:
        name = Path(path).name
        if name.endswith(".down.sql") or ".down." in name:
            return True
        body = texts.get(path, "")
        if re.search(r"\bdef\s+(down|downgrade)\b", body):
            return True
    return False


def assess(repo: Path, files: list[str], issue_text: str, config: dict) -> dict:
    found = detect(repo, files, config)
    if found.get("lane") != "migration":
        return found
    texts = {}
    findings = []
    for path in found["files"]:
        candidate = repo / path
        texts[path] = candidate.read_text(encoding="utf-8") if candidate.is_file() else ""
        findings.extend(destructive(texts[path], issue_text))
    found["rollback"] = has_rollback(found["files"], texts, issue_text)
    if not found["rollback"]:
        findings.append({"kind": "missing-rollback", "blocking": True})
    found["findings"] = findings
    found["blocking"] = any(item["blocking"] for item in findings)
    return found


def local_database(repo: Path, config: dict) -> dict:
    """Run an up/down command only when it stays on a disposable local database."""
    command = str(_section(config).get("local_command") or "").strip()
    if not command:
        return {"ran": False, "reason": "no local command"}
    if _remote(command):
        return {"ran": False, "reason": "refusing a remote or credentialed database"}
    completed = subprocess.run(
        shlex.split(command),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "ran": True,
        "exit_code": completed.returncode,
        "output": (completed.stdout + completed.stderr)[:400],
    }


def note(run_dir: Path, repo: Path, files: list[str], issue_text: str, config: dict) -> dict:
    report = assess(repo, files, issue_text, config)
    if report.get("lane") != "migration":
        return report
    report["database"] = local_database(repo, config)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "migrations.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _raise_tier(run_dir)
    _record(run_dir / "migrations.sqlite", report)
    return report


def checklist(run_dir: Path) -> list[str]:
    path = run_dir / "migrations.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("lane") != "migration":
        return []
    lines = ["Migration checklist:", f"- Tool: {data.get('tool')}"]
    lines.append(f"- Rollback: {'present' if data.get('rollback') else 'MISSING'}")
    for item in data.get("findings") or []:
        lines.append(f"- Blocking {item.get('kind')}")
    lines.append("- Do not merge until a human reviews the schema change.")
    lines.append("")
    return lines


def summary_line(run_dir: Path) -> str:
    path = run_dir / "migrations.json"
    if not path.is_file():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("blocking"):
        return ""
    kinds = ", ".join(item.get("kind", "") for item in data.get("findings") or [])
    return f"WARNING: migration findings block until reviewed: {kinds}."


def _raise_tier(run_dir: Path) -> None:
    path = run_dir / "triage.json"
    if not path.is_file():
        return
    triage = json.loads(path.read_text(encoding="utf-8"))
    routing = dict(triage.get("routing") or {})
    current = str(routing.get("tier") or "M")
    if current not in _ORDER or _ORDER.index(current) < _ORDER.index("L"):
        routing["tier"] = "L"
    routing["review_depth"] = "Deep"
    routing["codex"] = "on"
    triage["routing"] = routing
    path.write_text(json.dumps(triage, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _record(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS migrations (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              tool TEXT,
              blocking INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO migrations (tool, blocking) VALUES (?, ?)",
            (report.get("tool"), 1 if report.get("blocking") else 0),
        )
        conn.commit()
    finally:
        conn.close()


def _remote(command: str) -> bool:
    lowered = command.lower()
    if any(token in lowered for token in ("database_url", "password=", "postgres://", "mysql://")):
        return True
    if "@" in lowered and "://" in lowered:
        return True
    return False


def _glob_match(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern.removeprefix("**/")):
            return True
    return False


def _section(config: dict) -> dict:
    return (config.get("stages") or {}).get("migrations") or {}
