"""Classify a failing test as flaky or real, then as pre-existing or introduced.

Retries are commands. They do not call a model and they do not edit tests.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import time
from pathlib import Path

_PYTEST_FAIL = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)
_GO_FAIL = re.compile(r"^--- FAIL:\s+(\S+)", re.MULTILINE)

_BLOCKING = {"introduced-failure"}


def enabled(config: dict) -> bool:
    section = (config.get("stages") or {}).get("flaky") or {}
    return section.get("enabled", True) is not False


def parse_failures(output: str) -> list[str]:
    found = _PYTEST_FAIL.findall(output or "") + _GO_FAIL.findall(output or "")
    seen: list[str] = []
    for item in found:
        if item not in seen:
            seen.append(item)
    return seen


def body_lines(verify: dict) -> list[str]:
    items = [item for item in verify.get("flaky") or [] if not item.get("blocking")]
    if not items:
        return []
    lines = ["WARNING: pre-existing flaky tests were quarantined and do not block this change."]
    for item in items:
        lines.append(f"- {item.get('test')}: {item.get('kind')}")
    lines.append("")
    return lines


def risk_line(verify: dict) -> str | None:
    items = [item for item in verify.get("flaky") or [] if not item.get("blocking")]
    if not items:
        return None
    names = ", ".join(str(item.get("test")) for item in items)
    return (
        f"WARNING: pre-existing flaky tests were quarantined and do not block this change: {names}"
    )


def settle_flakes(
    tickets: list[dict],
    config: dict,
    run_dir: Path,
    run_id: str,
    run_command,
) -> tuple[list[dict], dict]:
    """Re-run failing tests, compare them to the base commit, and quarantine flakes."""
    info = {"flaky": [], "retry_cents": 0, "retries": 0}
    if not enabled(config):
        return tickets, info
    section = (config.get("stages") or {}).get("flaky") or {}
    retries = max(0, int(section.get("retries") or 2))
    configured = section.get("max_seconds")
    max_seconds = float(20 if configured is None else configured)
    max_cents = int(section.get("max_cents") or 0)
    if max_cents < 0:
        max_cents = 0
    deadline = time.monotonic() + max_seconds
    timeout = float(config.get("checks_timeout_seconds") or 30)
    classified: list[dict] = []
    for ticket in tickets:
        worktree = Path(str(ticket.get("worktree") or ""))
        for command in ticket.get("commands") or []:
            if command.get("name") != "test":
                continue
            if command.get("exit_code") == 0 and not command.get("timed_out"):
                continue
            names = parse_failures(str(command.get("output") or ""))
            if not names:
                continue
            results = []
            for name in names:
                if int(info["retry_cents"]) > max_cents:
                    row = {"test": name, "kind": "introduced-failure", "blocking": True}
                    results.append(row)
                    classified.append(row)
                    continue
                change = [
                    False,
                    *_probe(
                        command["command"],
                        name,
                        worktree,
                        retries,
                        deadline,
                        timeout,
                        run_command,
                        info,
                    ),
                ]
                base = _on_base(
                    worktree,
                    command["command"],
                    name,
                    run_dir,
                    str(ticket.get("ticket")),
                    retries,
                    deadline,
                    timeout,
                    run_command,
                    info,
                )
                kind = _kind(change, base)
                row = {"test": name, "kind": kind, "blocking": kind in _BLOCKING}
                results.append(row)
                classified.append(row)
            command["classified"] = results
            if results and not any(item["blocking"] for item in results):
                command["blocking"] = False
        blocking = [command for command in ticket.get("commands") or [] if _command_blocks(command)]
        ticket["passed"] = not blocking
    info["flaky"] = [item for item in classified if item["kind"] == "preexisting-flaky"]
    info["classified"] = classified
    info["retry_cents"] = 0
    if info["flaky"]:
        _record(run_dir.parent, info["flaky"], run_id)
    return tickets, info


def _command_blocks(command: dict) -> bool:
    if command.get("blocking") is False:
        return False
    return command.get("exit_code") != 0 or bool(command.get("timed_out"))


def _probe(
    command: str,
    test_id: str,
    worktree: Path,
    runs: int,
    deadline: float,
    timeout: float,
    run_command,
    info: dict,
) -> list[bool]:
    """Run one test up to ``runs`` times. Stop on the first pass."""
    outcome: list[bool] = []
    while len(outcome) < runs:
        if time.monotonic() >= deadline:
            break
        remaining = max(0.1, deadline - time.monotonic())
        result = run_command(f"{command} {test_id}", worktree, min(timeout, remaining))
        info["retries"] = int(info["retries"]) + 1
        passed = result.get("exit_code") == 0 and not result.get("timed_out")
        outcome.append(passed)
        if passed:
            break
    return outcome


def _on_base(
    worktree: Path,
    command: str,
    test_id: str,
    run_dir: Path,
    ticket: str,
    retries: int,
    deadline: float,
    timeout: float,
    run_command,
    info: dict,
) -> list[bool] | None:
    if time.monotonic() >= deadline:
        return None
    parent = _git(worktree, "rev-parse", "--verify", "HEAD^")
    if parent.returncode != 0:
        return None
    dest = run_dir / "flaky-base" / ticket
    if dest.exists():
        _git(worktree, "worktree", "remove", "--force", str(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    added = _git(worktree, "worktree", "add", "--detach", str(dest), parent.stdout.strip())
    if added.returncode != 0:
        return None
    try:
        probed = _probe(
            command,
            test_id,
            dest,
            retries + 1,
            deadline,
            timeout,
            run_command,
            info,
        )
        return probed or None
    finally:
        _git(worktree, "worktree", "remove", "--force", str(dest))


def _kind(change: list[bool], base: list[bool] | None) -> str:
    change_flaky = any(change[1:]) and not change[0]
    change_fail = not any(change)
    if base is None:
        return "introduced-flaky" if change_flaky else "introduced-failure"
    base_flaky = any(base[1:]) and not base[0]
    base_fail = not any(base)
    base_pass = bool(base) and all(base)
    if change_flaky and (base_flaky or base_fail):
        return "preexisting-flaky"
    if change_flaky and base_pass:
        return "introduced-flaky"
    if change_fail and (base_fail or base_flaky):
        return "preexisting-failure"
    return "introduced-failure"


def _record(runs_dir: Path, items: list[dict], run_id: str) -> None:
    path = runs_dir / "quarantine.json"
    current = {"tests": []}
    if path.is_file():
        current = json.loads(path.read_text(encoding="utf-8"))
    by_id = {str(item.get("test")): item for item in current.get("tests") or []}
    for item in items:
        test_id = str(item["test"])
        prev = by_id.get(test_id) or {"test": test_id, "flake_count": 0, "kind": item["kind"]}
        prev["flake_count"] = int(prev.get("flake_count") or 0) + 1
        prev["kind"] = item["kind"]
        prev["last_run_id"] = run_id
        by_id[test_id] = prev
    saved = {"tests": list(by_id.values())}
    path.write_text(json.dumps(saved, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _sqlite(runs_dir / "pipeline.db", saved["tests"])


def _sqlite(path: Path, tests: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quarantine (
              test_id TEXT PRIMARY KEY,
              kind TEXT,
              flake_count INTEGER NOT NULL,
              last_run_id TEXT
            )
            """
        )
        for item in tests:
            conn.execute(
                """
                INSERT INTO quarantine (test_id, kind, flake_count, last_run_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(test_id) DO UPDATE SET
                  kind = excluded.kind,
                  flake_count = excluded.flake_count,
                  last_run_id = excluded.last_run_id
                """,
                (
                    item["test"],
                    item["kind"],
                    int(item["flake_count"]),
                    item.get("last_run_id"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
