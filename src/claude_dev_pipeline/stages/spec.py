"""Spec stage. Fixture responses only: this module does not open a network connection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from claude_dev_pipeline.budget import BudgetHardStop, authorize_session_cents
from claude_dev_pipeline.ledger import Ledger, LedgerEntry
from claude_dev_pipeline.mcp_policy import read_only_mcp_for

PROVIDER = "anthropic"


class SpecError(Exception):
    def __init__(self, message: str, *, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


class SpecSkipped(Exception):
    """Triage did not route, so the spec fixture must not be opened."""


@dataclass(frozen=True)
class SpecResult:
    status: str
    needs_human: bool
    amount_cents: int
    budget_cents: int
    mcp_attached: tuple[str, ...]


def execute_spec(
    *,
    triage: dict,
    brief: str,
    fixture_path: Path,
    run_dir: Path,
    run_id: str,
    spent_cents: int,
    run_cap_cents: int,
    stage_cap_cents: int,
    mcp_config: dict | None = None,
    model: str = "claude-opus-5.5",
    effort: str = "medium",
) -> SpecResult:
    """Book a recorded spec only when triage routed the issue."""
    if triage.get("decision") != "route":
        raise SpecSkipped("triage rejected")
    if not str(brief).strip():
        raise SpecError("agent-brief.md is empty")
    mcp_plan = read_only_mcp_for(mcp_config or {}, "spec")
    budget_cents = authorize_session_cents(
        spent_cents=spent_cents,
        run_cap_cents=run_cap_cents,
        stage_cap_cents=stage_cap_cents,
        stage="spec",
    )
    fixture = _load_fixture(fixture_path)
    artifact = fixture.get("artifact")
    spec_md = str(fixture.get("spec_md") or "")
    if not isinstance(artifact, dict):
        raise SpecError("fixture artifact must be an object")
    validate_artifact(artifact, spec_md)
    usage = fixture.get("usage") or {}
    if not isinstance(usage, dict):
        raise SpecError("fixture usage must be an object")
    amount_cents = int(usage.get("amount_cents", 0))
    if amount_cents < 0:
        raise SpecError("fixture amount_cents must be >= 0")
    if amount_cents > budget_cents:
        raise BudgetHardStop(
            "spec fixture exceeds the stage budget",
            spent_cents=spent_cents,
            cap_cents=run_cap_cents,
            stage="spec",
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "spec.json").write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "spec.md").write_text(
        spec_md if spec_md.endswith("\n") else spec_md + "\n", encoding="utf-8"
    )
    (run_dir / "spec-mcp.json").write_text(
        json.dumps(mcp_plan, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    Ledger(run_dir / "ledger.jsonl").append(
        LedgerEntry(
            run_id=run_id,
            stage="spec",
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
    return SpecResult(
        status="succeeded",
        needs_human=bool(artifact["needs_human"]),
        amount_cents=amount_cents,
        budget_cents=budget_cents,
        mcp_attached=tuple(item["name"] for item in mcp_plan),
    )


def validate_artifact(artifact: dict, spec_md: str) -> None:
    criteria = artifact.get("acceptance_criteria")
    questions = artifact.get("open_questions")
    if not isinstance(criteria, list) or not criteria:
        raise SpecError("acceptance_criteria must be a non-empty list")
    if not isinstance(questions, list):
        raise SpecError("open_questions must be a list")
    if not isinstance(artifact.get("needs_human"), bool):
        raise SpecError("needs_human must be a boolean")
    ids: list[str] = []
    for item in criteria:
        if not isinstance(item, dict):
            raise SpecError("each acceptance criterion must be an object")
        criterion_id = str(item.get("id", "")).strip()
        statement = str(item.get("statement", "")).strip()
        if not criterion_id or not statement:
            raise SpecError("each acceptance criterion needs an id and a statement")
        ids.append(criterion_id)
    if len(ids) != len(set(ids)):
        raise SpecError("acceptance criterion ids must be unique")
    if not spec_md.strip():
        raise SpecError("spec.md is empty")
    for criterion_id in ids:
        if criterion_id not in spec_md:
            raise SpecError(f"spec.md does not mention {criterion_id}")
    blocking = False
    question_ids: list[str] = []
    for item in questions:
        if not isinstance(item, dict):
            raise SpecError("each open question must be an object")
        question_id = str(item.get("id", "")).strip()
        question = str(item.get("question", "")).strip()
        if not question_id or not question or not isinstance(item.get("blocks"), bool):
            raise SpecError("each open question needs an id, a question, and blocks")
        question_ids.append(question_id)
        blocking = blocking or item["blocks"]
    if len(question_ids) != len(set(question_ids)):
        raise SpecError("open question ids must be unique")
    if blocking and artifact["needs_human"] is not True:
        raise SpecError("needs_human must be true when an open question blocks")
    if not blocking and artifact["needs_human"] is not False:
        raise SpecError("needs_human must be false when no open question blocks")
    _require_named_list(artifact.get("seams"), "seams")
    scenarios = artifact.get("e2e_scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise SpecError("e2e_scenarios must be a non-empty list")
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise SpecError("each e2e scenario must be an object")
        if not str(scenario.get("id", "")).strip() or not str(scenario.get("scenario", "")).strip():
            raise SpecError("each e2e scenario needs an id and a scenario")


def _require_named_list(value: object, name: str) -> None:
    if not isinstance(value, list) or not value or not all(str(item).strip() for item in value):
        raise SpecError(f"{name} must be a non-empty list of strings")


def _load_fixture(path: Path) -> dict:
    if not path.is_file():
        raise SpecError(f"spec fixture not found: {path}", code=2)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SpecError("spec fixture must be a JSON object", code=2)
    return data
