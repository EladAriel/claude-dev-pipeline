"""Triage stage. Fixture responses only: this module does not open a network connection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from claude_dev_pipeline.budget import BudgetHardStop, authorize_session_cents
from claude_dev_pipeline.github_gate import GITHUB_APPROVED_LABEL, admit_issue
from claude_dev_pipeline.ledger import Ledger, LedgerEntry

CLASSIFY = {"bug", "enhancement"}
STATES = {"needs-triage", "needs-info", "ready-for-agent", "ready-for-human", "wontfix"}
RISK_TIERS = {"Quick", "Standard", "Deep"}
DECISIONS = {"route", "reject"}
MODEL = "claude-haiku-5.5"
PROVIDER = "anthropic"


class TriageError(Exception):
    def __init__(self, message: str, *, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TriageResult:
    status: str
    decision: str
    risk_tier: str
    amount_cents: int
    budget_cents: int


def load_fixture(path: Path) -> dict:
    if not path.is_file():
        raise TriageError(f"model fixture not found: {path}", code=2)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TriageError("model fixture must be a JSON object", code=2)
    return data


def validate_artifact(artifact: dict) -> None:
    required = (
        "classify",
        "category",
        "state",
        "risk_tier",
        "decision",
        "needs_human",
        "confidence",
        "injection_suspected",
        "summary",
    )
    missing = [key for key in required if key not in artifact]
    if missing:
        raise TriageError("triage.json missing " + ", ".join(missing))
    if artifact["classify"] not in CLASSIFY or artifact["category"] != artifact["classify"]:
        raise TriageError("classify and category must match and be bug or enhancement")
    if artifact["state"] not in STATES:
        raise TriageError("state is not a triage state")
    if artifact["risk_tier"] not in RISK_TIERS:
        raise TriageError("risk_tier must be Quick, Standard, or Deep")
    if artifact["decision"] not in DECISIONS:
        raise TriageError("decision must be route or reject")
    if not isinstance(artifact["needs_human"], bool):
        raise TriageError("needs_human must be a boolean")
    if not isinstance(artifact["injection_suspected"], bool):
        raise TriageError("injection_suspected must be a boolean")
    confidence = artifact["confidence"]
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, int)
        or not 0 <= confidence <= 100
    ):
        raise TriageError("confidence must be an integer from 0 to 100")
    if not str(artifact["summary"]).strip():
        raise TriageError("summary is empty")
    injected = artifact["injection_suspected"] and artifact["decision"] == "route"
    if injected and not artifact["needs_human"]:
        raise TriageError("an injection cannot be routed without a human")


def execute_triage(
    *,
    snapshot: dict,
    fixture_path: Path,
    run_dir: Path,
    run_id: str,
    spent_cents: int,
    run_cap_cents: int,
    stage_cap_cents: int,
) -> TriageResult:
    """Validate a recorded model response and book it only after the issue gate.

    The fixture file is opened only after the gate allows the issue.
    """
    decision = admit_issue(
        labels=list(snapshot.get("labels") or []),
        timeline=list(snapshot.get("timeline") or []),
        label=GITHUB_APPROVED_LABEL,
    )
    if not decision.allowed:
        raise TriageError(decision.reason)
    budget_cents = authorize_session_cents(
        spent_cents=spent_cents,
        run_cap_cents=run_cap_cents,
        stage_cap_cents=stage_cap_cents,
        stage="triage",
    )
    fixture = load_fixture(fixture_path)
    artifact = fixture.get("artifact")
    if not isinstance(artifact, dict):
        raise TriageError("fixture artifact must be an object")
    validate_artifact(artifact)
    usage = fixture.get("usage") or {}
    if not isinstance(usage, dict):
        raise TriageError("fixture usage must be an object")
    amount_cents = int(usage.get("amount_cents", 0))
    if amount_cents < 0:
        raise TriageError("fixture amount_cents must be >= 0")
    if amount_cents > budget_cents:
        raise BudgetHardStop(
            "triage fixture exceeds the stage budget",
            spent_cents=spent_cents,
            cap_cents=run_cap_cents,
            stage="triage",
        )
    brief = str(fixture.get("brief") or "").strip()
    if artifact["decision"] == "route" and not brief:
        raise TriageError("a routed issue needs agent-brief.md text in the fixture")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "issue.json").write_text(
        json.dumps(
            {
                "number": snapshot.get("number"),
                "title": snapshot.get("title", ""),
                "body": snapshot.get("body", ""),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "triage.json").write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "agent-brief.md").write_text(brief + ("\n" if brief else ""), encoding="utf-8")
    Ledger(run_dir / "ledger.jsonl").append(
        LedgerEntry(
            run_id=run_id,
            stage="triage",
            amount_cents=amount_cents,
            provider=PROVIDER,
            model=MODEL,
            note="fixture",
            input_tokens=int(usage.get("input_tokens", 0)),
            cache_write_tokens=int(usage.get("cache_write_tokens", 0)),
            cache_read_tokens=int(usage.get("cache_read_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            budget_cents=budget_cents,
        ),
        run_cap_cents=run_cap_cents,
    )
    return TriageResult(
        status="succeeded",
        decision=str(artifact["decision"]),
        risk_tier=str(artifact["risk_tier"]),
        amount_cents=amount_cents,
        budget_cents=budget_cents,
    )
