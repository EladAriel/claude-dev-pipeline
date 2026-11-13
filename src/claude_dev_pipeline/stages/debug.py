"""Read-only diagnosis. The failing test stays in a scratch worktree."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from claude_dev_pipeline.budget import BudgetHardStop, authorize_session_cents
from claude_dev_pipeline.lanes import write_synthesized
from claude_dev_pipeline.ledger import Ledger, LedgerEntry

MODEL = "claude-haiku-5.5"


class DebugError(Exception):
    def __init__(self, message: str, *, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


class DebugSkipped(Exception):
    pass


@dataclass(frozen=True)
class DebugResult:
    status: str
    amount_cents: int
    scratch: Path


def execute_debug(
    *,
    run_dir: Path,
    run_id: str,
    description: str,
    fixture_path: Path,
    target_repo: Path | None,
    spent_cents: int,
    run_cap_cents: int,
    stage_cap_cents: int,
    enabled: bool = True,
    fix: bool = False,
) -> DebugResult:
    if not enabled:
        raise DebugSkipped("stages.debug.enabled is false")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    report = fixture.get("diagnosis")
    if not isinstance(report, dict):
        raise DebugError("diagnosis fixture must be an object")
    usage = fixture.get("usage") or {}
    amount = int(usage.get("amount_cents") or 0)
    budget = authorize_session_cents(
        spent_cents=spent_cents,
        run_cap_cents=run_cap_cents,
        stage_cap_cents=stage_cap_cents,
        stage="debug",
    )
    if amount > budget:
        raise BudgetHardStop(
            "debug exceeds the stage budget",
            spent_cents=spent_cents,
            cap_cents=run_cap_cents,
            stage="debug",
        )
    scratch = _scratch(run_dir, target_repo)
    failing = fixture.get("failing_test") or {}
    relative = str(failing.get("path") or "test_repro.py")
    content = str(failing.get("content") or "")
    dest = scratch / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    _write_report(run_dir, description, report, relative, fix)
    if fix:
        _handoff(run_dir, description, relative, content)
    Ledger(run_dir / "ledger.jsonl").append(
        LedgerEntry(
            run_id=run_id,
            stage="debug",
            amount_cents=amount,
            provider="anthropic",
            model=MODEL,
            effort="low",
            note="fixture",
            budget_cents=budget,
        ),
        run_cap_cents=run_cap_cents,
    )
    return DebugResult("handoff" if fix else "diagnosed", amount, scratch)


def _scratch(run_dir: Path, target_repo: Path | None) -> Path:
    scratch = run_dir / "scratch"
    if target_repo is None:
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch
    if not (target_repo / ".git").exists():
        raise DebugError("target repo is not a git checkout")
    scratch.parent.mkdir(parents=True, exist_ok=True)
    if not scratch.exists():
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(scratch), "HEAD"],
            cwd=target_repo,
            check=True,
            capture_output=True,
            text=True,
        )
    return scratch


def _write_report(run_dir: Path, description: str, report: dict, test_path: str, fix: bool) -> None:
    lines = [
        "# Diagnosis",
        "",
        f"Asked: {description}",
        "",
        "## Reproduce",
        str(report.get("reproduce") or ""),
        "",
        "## Hypotheses",
    ]
    for item in report.get("hypotheses") or []:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Failing test",
            f"`{test_path}` in the scratch worktree.",
            "",
            "## Root cause",
            str(report.get("root_cause") or ""),
            "",
            "## Evidence",
            str(report.get("evidence") or ""),
            "",
            f"Confidence: {int(report.get('confidence') or 0)}",
            "",
            "## Suggested fix",
            str(report.get("suggested_fix") or ""),
            "",
            "Read-only. No push and no pull request.",
        ]
    )
    if fix:
        lines.append("Handoff: the failing test is the fast-lane acceptance check.")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "diagnosis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _handoff(run_dir: Path, description: str, test_path: str, content: str) -> None:
    write_synthesized(run_dir, {"title": description, "body": description})
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    plan["tasks"][0]["test_plan"] = f"The scratch test {test_path} fails before the fix."
    plan["tasks"][0]["acceptance_criteria"] = ["AC-1"]
    plan["tasks"][0]["files"] = [test_path]
    (run_dir / "plan.json").write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run_dir / "failing_test.txt").write_text(content, encoding="utf-8")
    triage = {
        "decision": "route",
        "risk_tier": "Quick",
        "tier": "S",
        "routing": {
            "escalated": False,
            "lane": "fast",
            "review_depth": "Quick",
            "tier": "S",
        },
    }
    (run_dir / "triage.json").write_text(
        json.dumps(triage, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
