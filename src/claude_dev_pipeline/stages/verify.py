"""Verify stage. Commands run first. The model fixture is read only after a failure."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from claude_dev_pipeline.budget import BudgetHardStop, authorize_session_cents
from claude_dev_pipeline.docs_lane import active as docs_active
from claude_dev_pipeline.docs_lane import light_names
from claude_dev_pipeline.flaky import settle_flakes
from claude_dev_pipeline.harness import discover
from claude_dev_pipeline.ledger import Ledger, LedgerEntry
from claude_dev_pipeline.monorepo import affected_suffix

PROVIDER = "anthropic"
MODEL = "claude-haiku-5.5"
CHECK_NAMES = ("test", "lint", "typecheck", "build")
OUTPUT_LIMIT = 2000


class VerifyError(Exception):
    def __init__(self, message: str, *, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class VerifyResult:
    status: str
    model_called: bool
    amount_cents: int


def execute_verify(
    *,
    run_dir: Path,
    run_id: str,
    config: dict,
    fixture_path: Path | None,
    spent_cents: int,
    run_cap_cents: int,
    stage_cap_cents: int,
    effort: str = "low",
) -> VerifyResult:
    """Run configured checks. Summarize with a fixture only when something fails."""
    spec = _read_json(run_dir / "spec.json")
    plan = _read_json(run_dir / "plan.json")
    reports = _impl_reports(run_dir)
    done = [item for item in reports if item.get("status") == "done"]
    if not done:
        raise VerifyError("no done ticket to verify")
    timeout = float(config.get("checks_timeout_seconds") or 30)
    docs = docs_active(run_dir)
    tickets = [_verify_ticket(item, config, timeout, docs=docs, run_dir=run_dir) for item in done]
    tickets, flake_info = settle_flakes(tickets, config, run_dir, run_id, _run_command)
    coverage_gaps = [] if docs else _coverage_gaps(spec, plan, tickets)
    commands_failed = [item for item in tickets if not item["passed"]]
    test_ran = any(item.get("name") == "test" for ticket in tickets for item in ticket["commands"])
    gate = (config.get("stages") or {}).get("verification") or {}
    gate_on = gate.get("enabled", True) is not False
    if docs and not commands_failed:
        _finish(
            run_dir,
            run_id,
            tickets,
            status="pass",
            model_called=False,
            amount_cents=0,
            effort="",
            budget_cents=0,
            run_cap_cents=run_cap_cents,
            usage={},
            verification="docs",
        )
        return VerifyResult(status="pass", model_called=False, amount_cents=0)
    if gate_on and not commands_failed and not coverage_gaps and not test_ran:
        _finish(
            run_dir,
            run_id,
            tickets,
            status="unverified",
            model_called=False,
            amount_cents=0,
            effort="",
            budget_cents=0,
            run_cap_cents=run_cap_cents,
            usage={},
            verification="unverified",
            flaky=flake_info.get("flaky") or [],
        )
        return VerifyResult(status="unverified", model_called=False, amount_cents=0)
    if not commands_failed and not coverage_gaps:
        _finish(
            run_dir,
            run_id,
            tickets,
            status="pass",
            model_called=False,
            amount_cents=0,
            effort="",
            budget_cents=0,
            run_cap_cents=run_cap_cents,
            usage={},
            flaky=flake_info.get("flaky") or [],
            classified=flake_info.get("classified") or [],
        )
        return VerifyResult(status="pass", model_called=False, amount_cents=0)
    summary, suggested, usage = _failure_summary(fixture_path, coverage_gaps, commands_failed)
    amount_cents = int(usage.get("amount_cents", 0))
    budget_cents = authorize_session_cents(
        spent_cents=spent_cents,
        run_cap_cents=run_cap_cents,
        stage_cap_cents=stage_cap_cents,
        stage="verify",
    )
    if amount_cents < 0 or amount_cents > budget_cents:
        raise BudgetHardStop(
            "verify summary exceeds the stage budget",
            spent_cents=spent_cents,
            cap_cents=run_cap_cents,
            stage="verify",
        )
    failed_tickets = [item for item in tickets if not item["passed"]] or tickets[:1]
    for item in tickets:
        if item not in failed_tickets:
            continue
        item["failure_summary"] = summary
        item["suggested_fix_ticket"] = suggested
    _finish(
        run_dir,
        run_id,
        tickets,
        status="fail",
        model_called=True,
        amount_cents=amount_cents,
        effort=effort,
        budget_cents=budget_cents,
        run_cap_cents=run_cap_cents,
        usage=usage,
        coverage_gaps=coverage_gaps,
        flaky=flake_info.get("flaky") or [],
        classified=flake_info.get("classified") or [],
    )
    return VerifyResult(status="fail", model_called=True, amount_cents=amount_cents)


def _verify_ticket(
    report: dict, config: dict, timeout: float, docs: bool = False, run_dir: Path | None = None
) -> dict:
    worktree = Path(str(report.get("worktree") or ""))
    if not worktree.is_dir():
        raise VerifyError(f"worktree missing for ticket {report.get('ticket')}")
    commands = []
    passed = True
    for name, command in _commands(config, worktree, docs=docs, run_dir=run_dir):
        outcome = _run_command(command, worktree, timeout)
        commands.append({"name": name, "command": command, **outcome})
        if outcome["exit_code"] != 0 or outcome["timed_out"]:
            passed = False
    return {
        "ticket": str(report.get("ticket")),
        "passed": passed,
        "worktree": str(worktree),
        "commands": commands,
        "failure_summary": "",
        "suggested_fix_ticket": None,
        "acceptance_ids": _acceptance_ids(report),
        "tests_added": list(report.get("tests_added") or []),
    }


def _commands(
    config: dict,
    worktree: Path,
    docs: bool = False,
    run_dir: Path | None = None,
) -> list[tuple[str, str]]:
    checks = config.get("checks") or {}
    names = ("lint", "format", "link", "schema") if docs else CHECK_NAMES
    allowed = light_names(docs)
    found = []
    for name in names:
        command = str(checks.get(name) or "").strip()
        if command and (allowed is None or name in allowed):
            found.append((name, command))
    if not any(name == "test" for name, _command in found):
        detected = discover(worktree, {"checks": {}})
        if detected.get("command"):
            found.append(("test", str(detected["command"])))
    suffix = affected_suffix(run_dir)
    if suffix:
        found = [
            (name, f"{command} {suffix}" if name == "test" else command) for name, command in found
        ]
    return found


def _run_command(command: str, worktree: Path, timeout: float) -> dict:
    parts = _guard_command(command, worktree)
    try:
        completed = subprocess.run(
            parts,
            cwd=worktree,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = _clip(_as_text(exc.stdout) + _as_text(exc.stderr))
        return {"exit_code": None, "timed_out": True, "output": output}
    output = _clip(completed.stdout + completed.stderr)
    return {"exit_code": completed.returncode, "timed_out": False, "output": output}


def _guard_command(command: str, worktree: Path) -> list[str]:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        raise VerifyError(f"could not parse check command: {exc}") from exc
    if not parts:
        raise VerifyError("empty check command")
    for part in parts:
        if part.startswith("-"):
            continue
        if ".." in Path(part).parts or part.startswith("~") or part.startswith("/"):
            raise VerifyError("check command leaves the worktree")
        if "/" in part:
            _inside(worktree, part)
    return parts


def _inside(root: Path, relative: str) -> Path:
    dest = (root / relative).resolve()
    if not dest.is_relative_to(root.resolve()):
        raise VerifyError("check command leaves the worktree")
    return dest


def _acceptance_ids(report: dict) -> list[str]:
    explicit = report.get("acceptance_tests") or []
    tests = set(report.get("tests_added") or [])
    ids = []
    for item in explicit:
        if not isinstance(item, dict):
            continue
        criterion_id = str(item.get("id", "")).strip()
        test = str(item.get("test", "")).strip()
        if criterion_id and test in tests:
            ids.append(criterion_id)
    return ids


def _coverage_gaps(spec: dict, plan: dict, tickets: list[dict]) -> list[str]:
    required = []
    for item in spec.get("acceptance_criteria") or []:
        if isinstance(item, dict) and str(item.get("id", "")).strip():
            required.append(str(item["id"]))
    by_ticket = {item["ticket"]: item for item in tickets}
    plan_ids: dict[str, list[str]] = {}
    for task in plan.get("tasks") or []:
        plan_ids[str(task.get("id"))] = [
            str(value) for value in task.get("acceptance_criteria") or []
        ]
    covered: set[str] = set()
    for ticket_id, ticket in by_ticket.items():
        if not ticket["passed"] or not ticket["tests_added"]:
            continue
        ids = ticket["acceptance_ids"] or plan_ids.get(ticket_id, [])
        covered.update(ids)
    return [criterion_id for criterion_id in required if criterion_id not in covered]


def _failure_summary(
    fixture_path: Path | None,
    gaps: list[str],
    failed: list[dict],
) -> tuple[str, str | None, dict]:
    if fixture_path is None or not fixture_path.is_file():
        raise VerifyError("verify fixture not found for a failing check", code=2)
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    summary = str((data.get("summary") or {}).get("failure_summary") or "").strip()
    if gaps and "acceptance" not in summary:
        summary = (summary + " " if summary else "") + "uncovered: " + ", ".join(gaps)
    suggested = (data.get("summary") or {}).get("suggested_fix_ticket")
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        raise VerifyError("verify fixture usage must be an object")
    if not summary:
        names = ", ".join(item["ticket"] for item in failed) or ", ".join(gaps)
        summary = f"checks failed for {names}"
    return summary.strip(), (str(suggested) if suggested else None), usage


def _finish(
    run_dir: Path,
    run_id: str,
    tickets: list[dict],
    *,
    status: str,
    model_called: bool,
    amount_cents: int,
    effort: str,
    budget_cents: int,
    run_cap_cents: int,
    usage: dict,
    coverage_gaps: list[str] | None = None,
    verification: str = "tested",
    flaky: list | None = None,
    classified: list | None = None,
) -> None:
    payload = {
        "status": status,
        "verification": verification,
        "model_called": model_called,
        "coverage_gaps": coverage_gaps or [],
        "flaky": flaky or [],
        "classified": classified or [],
        "tickets": tickets,
    }
    (run_dir / "verify.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    Ledger(run_dir / "ledger.jsonl").append(
        LedgerEntry(
            run_id=run_id,
            stage="verify",
            amount_cents=amount_cents,
            provider=PROVIDER if model_called else "",
            model=MODEL if model_called else "none",
            effort=effort,
            note="fixture" if model_called else "commands-only",
            model_called=model_called,
            output_tokens=int(usage.get("output_tokens", 0)),
            input_tokens=int(usage.get("input_tokens", 0)),
            budget_cents=budget_cents,
        ),
        run_cap_cents=run_cap_cents,
    )


def _impl_reports(run_dir: Path) -> list[dict]:
    reports = []
    for path in sorted(run_dir.glob("impl-*.json")):
        reports.append(json.loads(path.read_text(encoding="utf-8")))
    return reports


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise VerifyError(f"missing {path.name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise VerifyError(f"{path.name} must be an object")
    return data


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _clip(text: str) -> str:
    if len(text) <= OUTPUT_LIMIT:
        return text
    return text[:OUTPUT_LIMIT]
