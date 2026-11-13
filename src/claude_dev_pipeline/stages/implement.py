"""Implement stage. One local commit per ticket. This module does not publish anything."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from claude_dev_pipeline.budget import BudgetHardStop, authorize_session_cents
from claude_dev_pipeline.ledger import Ledger, LedgerEntry
from claude_dev_pipeline.scan import scan_text

PROVIDER = "anthropic"
_FORBIDDEN_GIT = {"push", "pull", "fetch", "remote", "request-pull"}


class ImplementError(Exception):
    def __init__(self, message: str, *, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


class ImplementSkipped(Exception):
    """No plan is ready, so the implement fixture must not be opened."""


@dataclass(frozen=True)
class ImplementResult:
    status: str
    tickets: tuple[str, ...]
    stopped_at: str | None


def choose_model(ticket: dict, implement_cfg: dict) -> str:
    default = str(implement_cfg.get("model") or "claude-sonnet-5.5")
    raw_allow = implement_cfg.get("allowlist") or [default]
    allow = {str(item) for item in raw_allow} if isinstance(raw_allow, list) else {default}
    suggested = str(ticket.get("suggested_model") or default).strip()
    if suggested in allow:
        return suggested
    return default


def fanout_lines(tasks: list[dict], implement_cfg: dict) -> list[str]:
    lines = []
    for task in tasks:
        ticket = str(task.get("id", "")).strip()
        model = choose_model(task, implement_cfg)
        lines.append(f"implement-fanout: ticket={ticket} model={model} branch=agent/<run>-{ticket}")
    return lines


def execute_implement(
    *,
    plan: dict,
    fixture_path: Path,
    run_dir: Path,
    run_id: str,
    target_repo: Path,
    spent_cents: int,
    run_cap_cents: int,
    stage_cap_cents: int,
    implement_cfg: dict,
    effort: str = "high",
    skip_tickets: set[str] | None = None,
    on_ticket=None,
) -> ImplementResult:
    """Apply recorded edits in per-ticket worktrees of the target repo."""
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ImplementSkipped("no tickets")
    if not target_repo.is_dir() or not (target_repo / ".git").exists():
        raise ImplementError(f"target repo is not a git checkout: {target_repo}", code=2)
    fixture = _load_fixture(fixture_path)
    sessions = fixture.get("tickets")
    if not isinstance(sessions, dict):
        raise ImplementError("implement fixture tickets must be an object")
    finished: list[str] = []
    stopped: str | None = None
    running_spent = spent_cents
    base_sha = _git(target_repo, "rev-parse", "HEAD").strip()
    shas: dict[str, str] = {}
    for task in tasks:
        ticket = str(task.get("id", "")).strip()
        if skip_tickets and ticket in skip_tickets:
            finished.append(ticket)
            continue
        session = sessions.get(ticket)
        if not isinstance(session, dict):
            raise ImplementError(f"missing implement fixture for ticket {ticket}")
        budget_cents = authorize_session_cents(
            spent_cents=running_spent,
            run_cap_cents=run_cap_cents,
            stage_cap_cents=stage_cap_cents,
            stage=f"implement {ticket}",
        )
        amount_cents = int((session.get("usage") or {}).get("amount_cents", 0))
        if amount_cents < 0 or amount_cents > budget_cents:
            raise BudgetHardStop(
                f"implement ticket {ticket} exceeds the stage budget",
                spent_cents=running_spent,
                cap_cents=run_cap_cents,
                stage="implement",
            )
        model = choose_model(task, implement_cfg)
        report, charge = _run_ticket(
            session=session,
            ticket=ticket,
            run_id=run_id,
            target_repo=target_repo,
            start_sha=_parent_sha(task, shas, base_sha),
            model=model,
            effort=effort,
        )
        report["budget_cents"] = budget_cents
        _write_report(run_dir, ticket, report)
        if charge:
            _book(
                run_dir,
                run_id,
                ticket,
                amount_cents,
                model,
                effort,
                budget_cents,
                run_cap_cents,
                session.get("usage") or {},
            )
            running_spent += amount_cents
        if report["commit_sha"]:
            shas[ticket] = str(report["commit_sha"])
        finished.append(ticket)
        if on_ticket is not None:
            on_ticket(ticket, report)
        if report["status"] != "done":
            stopped = ticket
            break
    status = "blocked" if stopped else "succeeded"
    return ImplementResult(status=status, tickets=tuple(finished), stopped_at=stopped)


def _parent_sha(task: dict, shas: dict[str, str], base_sha: str) -> str:
    deps = task.get("blocked_by") or []
    if deps and deps[-1] in shas:
        return shas[deps[-1]]
    return base_sha


def _run_ticket(
    *,
    session: dict,
    ticket: str,
    run_id: str,
    target_repo: Path,
    start_sha: str,
    model: str,
    effort: str,
) -> tuple[dict, bool]:
    branch = f"agent/{run_id}-{ticket}"
    worktree = target_repo.parent / f".worktrees-{target_repo.name}" / run_id / ticket
    report = {
        "ticket": ticket,
        "branch": branch,
        "worktree": str(worktree),
        "model": model,
        "effort": effort,
        "files_changed": [],
        "tests_added": list(session.get("tests_added") or []),
        "test_command": str(session.get("test_command") or ""),
        "test_result": session.get("test_result") or {},
        "commit_sha": None,
        "status": "blocked",
        "scan": [],
    }
    try:
        paths = _materialize(worktree, target_repo, branch, start_sha, session.get("files") or [])
    except ImplementError as exc:
        report["blocked_reason"] = str(exc)
        return report, True
    report["files_changed"] = paths
    diff = _git(worktree, "diff", "--cached", "--")
    findings = []
    for path in paths:
        findings.extend(scan_text((worktree / path).read_text(encoding="utf-8"), path))
    findings.extend(scan_text(diff, "diff"))
    if findings:
        report["scan"] = [{"path": item.path, "rule": item.rule} for item in findings]
        report["blocked_reason"] = "diff failed the path and secret scan"
        _git(worktree, "reset", "-q")
        _git(worktree, "clean", "-fd")
        return report, True
    result = report["test_result"] if isinstance(report["test_result"], dict) else {}
    declared = str(session.get("status") or "done")
    failed = int(result.get("exit_code", 0)) != 0 or declared == "blocked"
    if failed:
        report["blocked_reason"] = "tests failed" if declared != "blocked" else "fixture blocked"
        return report, True
    _git(worktree, "commit", "-m", f"implement {ticket}")
    report["commit_sha"] = _git(worktree, "rev-parse", "HEAD").strip()
    report["status"] = "done"
    return report, True


def _materialize(
    worktree: Path, target_repo: Path, branch: str, start_sha: str, files: object
) -> list[str]:
    if not isinstance(files, list):
        raise ImplementError("ticket files must be a list")
    pending: list[tuple[str, str]] = []
    for item in files:
        if not isinstance(item, dict):
            raise ImplementError("each file must be an object")
        relative = str(item.get("path", "")).strip()
        _inside(worktree, relative)
        pending.append((relative, str(item.get("content", ""))))
    worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(target_repo, "worktree", "add", "-b", branch, str(worktree), start_sha)
    written: list[str] = []
    for relative, content in pending:
        dest = _inside(worktree, relative)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        written.append(relative)
    if written:
        _git(worktree, "add", "--", *written)
    return written


def _inside(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not relative or relative.startswith("/"):
        raise ImplementError(f"path escapes the worktree: {relative}")
    dest = (root / path).resolve()
    if not dest.is_relative_to(root.resolve()):
        raise ImplementError(f"path escapes the worktree: {relative}")
    return dest


def _book(
    run_dir: Path,
    run_id: str,
    ticket: str,
    amount_cents: int,
    model: str,
    effort: str,
    budget_cents: int,
    run_cap_cents: int,
    usage: dict,
) -> None:
    Ledger(run_dir / "ledger.jsonl").append(
        LedgerEntry(
            run_id=run_id,
            stage="implement",
            amount_cents=amount_cents,
            provider=PROVIDER,
            model=model,
            effort=effort,
            ticket=ticket,
            note="fixture",
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            budget_cents=budget_cents,
        ),
        run_cap_cents=run_cap_cents,
    )


def _write_report(run_dir: Path, ticket: str, report: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"impl-{ticket}.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_fixture(path: Path) -> dict:
    if not path.is_file():
        raise ImplementError(f"implement fixture not found: {path}", code=2)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ImplementError("implement fixture must be a JSON object", code=2)
    return data


def _git(repo: Path, *args: str) -> str:
    if any(arg in _FORBIDDEN_GIT for arg in args):
        raise ImplementError("implement cannot publish the target repo")
    command = [
        "git",
        "-C",
        str(repo),
        "-c",
        "user.email=implement@example.com",
        "-c",
        "user.name=pipeline",
        *args,
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ImplementError(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout
