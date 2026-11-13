"""Plan stage. Fixture responses only: this module does not open a network connection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from claude_dev_pipeline.budget import BudgetHardStop, authorize_session_cents
from claude_dev_pipeline.issues import load_tasks
from claude_dev_pipeline.ledger import Ledger, LedgerEntry
from claude_dev_pipeline.mcp_policy import read_only_mcp_for

PROVIDER = "anthropic"
ESTIMATES = {"S", "M", "L"}


class PlanError(Exception):
    def __init__(self, message: str, *, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


class PlanSkipped(Exception):
    """Spec still needs a human, so the plan fixture must not be opened."""


@dataclass(frozen=True)
class PlanResult:
    status: str
    tickets: int
    amount_cents: int
    budget_cents: int


def execute_plan(
    *,
    spec: dict,
    spec_md: str,
    fixture_path: Path,
    run_dir: Path,
    run_id: str,
    spent_cents: int,
    run_cap_cents: int,
    stage_cap_cents: int,
    mcp_config: dict | None = None,
    model: str = "claude-opus-5.5",
    effort: str = "medium",
) -> PlanResult:
    """Book a recorded plan only when the spec does not need a human."""
    if spec.get("needs_human") is True:
        raise PlanSkipped("spec needs a human")
    if not str(spec_md).strip():
        raise PlanError("spec.md is empty")
    mcp_plan = read_only_mcp_for(mcp_config or {}, "plan")
    budget_cents = authorize_session_cents(
        spent_cents=spent_cents,
        run_cap_cents=run_cap_cents,
        stage_cap_cents=stage_cap_cents,
        stage="plan",
    )
    fixture = _load_fixture(fixture_path)
    artifact = fixture.get("artifact")
    if not isinstance(artifact, dict):
        raise PlanError("fixture artifact must be an object")
    normalized = validate_plan(artifact, spec)
    usage = fixture.get("usage") or {}
    if not isinstance(usage, dict):
        raise PlanError("fixture usage must be an object")
    amount_cents = int(usage.get("amount_cents", 0))
    if amount_cents < 0:
        raise PlanError("fixture amount_cents must be >= 0")
    if amount_cents > budget_cents:
        raise BudgetHardStop(
            "plan fixture exceeds the stage budget",
            spent_cents=spent_cents,
            cap_cents=run_cap_cents,
            stage="plan",
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_path = run_dir / "plan.json"
    plan_path.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        load_tasks(plan_path)
    except ValueError as exc:
        plan_path.unlink(missing_ok=True)
        raise PlanError(str(exc)) from exc
    (run_dir / "plan-mcp.json").write_text(
        json.dumps(mcp_plan, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    Ledger(run_dir / "ledger.jsonl").append(
        LedgerEntry(
            run_id=run_id,
            stage="plan",
            amount_cents=amount_cents,
            provider=PROVIDER,
            model=model,
            effort=effort,
            note="fixture",
            input_tokens=int(usage.get("input_tokens", 0)),
            cache_write_tokens=int(usage.get("cache_write_tokens", 0)),
            cache_read_tokens=int(usage.get("cache_read_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            budget_cents=budget_cents,
        ),
        run_cap_cents=run_cap_cents,
    )
    return PlanResult(
        status="succeeded",
        tickets=len(normalized["tasks"]),
        amount_cents=amount_cents,
        budget_cents=budget_cents,
    )


def validate_plan(artifact: dict, spec: dict) -> dict:
    raw_tasks = artifact.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise PlanError("plan tasks must be a non-empty list")
    spec_ids = _spec_ids(spec)
    tasks: list[dict] = []
    seen: list[str] = []
    covered: set[str] = set()
    for item in raw_tasks:
        if not isinstance(item, dict):
            raise PlanError("each ticket must be an object")
        task = _normalize_ticket(item)
        for dep in task["blocked_by"]:
            if dep not in seen:
                raise PlanError(f"{task['id']} depends on {dep}, which is not an earlier ticket")
        if task["id"] in seen:
            raise PlanError(f"duplicate ticket id {task['id']}")
        for criterion_id in task["acceptance_criteria"]:
            if criterion_id not in spec_ids:
                raise PlanError(f"{criterion_id} is not an acceptance criterion in the spec")
            covered.add(criterion_id)
        seen.append(task["id"])
        tasks.append(task)
    missing = [criterion_id for criterion_id in spec_ids if criterion_id not in covered]
    if missing:
        raise PlanError("uncovered acceptance criteria: " + ", ".join(missing))
    return {"tasks": tasks}


def _normalize_ticket(item: dict) -> dict:
    task_id = str(item.get("id", "")).strip()
    title = str(item.get("title", "")).strip()
    if not task_id or not title:
        raise PlanError("each ticket needs an id and a title")
    criteria = _string_list(
        item.get("acceptance_criteria"), "acceptance_criteria", allow_empty=True
    )
    files = _string_list(item.get("files"), "files", allow_empty=False)
    seams = _string_list(item.get("seams"), "seams", allow_empty=False)
    test_plan = str(item.get("test_plan", "")).strip()
    if not test_plan:
        raise PlanError(f"ticket {task_id} needs a test plan")
    estimate = str(item.get("estimate", "")).strip()
    if estimate not in ESTIMATES:
        raise PlanError(f"ticket {task_id} estimate must be S, M, or L")
    suggested = str(item.get("suggested_model", "")).strip()
    if not suggested:
        raise PlanError(f"ticket {task_id} needs a suggested model")
    blocked = _deps(item, task_id)
    body = str(item.get("body") or test_plan).strip()
    return {
        "acceptance_criteria": criteria,
        "blocked_by": blocked,
        "body": body,
        "estimate": estimate,
        "files": files,
        "id": task_id,
        "seams": seams,
        "suggested_model": suggested,
        "test_plan": test_plan,
        "title": title,
    }


def _deps(item: dict, task_id: str) -> list[str]:
    blocked = item.get("blocked_by")
    deps = item.get("deps")
    if blocked is None and deps is None:
        return []
    if blocked is None:
        return _string_list(deps, "deps", allow_empty=True)
    normalized = _string_list(blocked, "blocked_by", allow_empty=True)
    if deps is not None and _string_list(deps, "deps", allow_empty=True) != normalized:
        raise PlanError(f"ticket {task_id} deps and blocked_by disagree")
    return normalized


def _string_list(value: object, name: str, *, allow_empty: bool) -> list[str]:
    if not isinstance(value, list):
        raise PlanError(f"{name} must be a list")
    items = [str(item).strip() for item in value]
    if any(not item for item in items):
        raise PlanError(f"{name} contains an empty entry")
    if not allow_empty and not items:
        raise PlanError(f"{name} must not be empty")
    return items


def _spec_ids(spec: dict) -> list[str]:
    criteria = spec.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        raise PlanError("spec acceptance_criteria is missing")
    ids: list[str] = []
    for item in criteria:
        if isinstance(item, str):
            criterion_id = item.strip()
        elif isinstance(item, dict):
            criterion_id = str(item.get("id", "")).strip()
        else:
            raise PlanError("spec acceptance criteria must be ids or objects")
        if not criterion_id:
            raise PlanError("spec acceptance criterion id is empty")
        ids.append(criterion_id)
    return ids


def _load_fixture(path: Path) -> dict:
    if not path.is_file():
        raise PlanError(f"plan fixture not found: {path}", code=2)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PlanError("plan fixture must be a JSON object", code=2)
    return data
