"""Retro and inline metrics. Proposals are written and never applied."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from claude_dev_pipeline.budget import BudgetHardStop, authorize_session_cents
from claude_dev_pipeline.ledger import Ledger, LedgerEntry
from claude_dev_pipeline.reconcile import agreement_rate, threshold_proposals

MODEL = "claude-haiku-5.5"


class RetroError(Exception):
    def __init__(self, message: str, *, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


class RetroSkipped(Exception):
    pass


@dataclass(frozen=True)
class RetroResult:
    status: str
    amount_cents: int
    proposals: int


def execute_retro(
    *,
    run_dir: Path,
    run_id: str,
    caps: dict[str, int],
    spent_cents: int,
    run_cap_cents: int,
    stage_cap_cents: int,
    stop_reason: str,
    enabled: bool = True,
    fixture_path: Path | None = None,
    model: str = MODEL,
    effort: str = "low",
) -> RetroResult:
    """Write metrics first. A fixture may add proposals. Nothing is applied."""
    if not enabled:
        raise RetroSkipped("stages.retro.enabled is false")
    proposals: list[dict] = []
    usage: dict = {}
    if fixture_path is not None:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        raw = payload.get("proposals") or []
        if not isinstance(raw, list):
            raise RetroError("retro proposals must be a list")
        proposals = [_proposal(item) for item in raw]
        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            raise RetroError("retro usage must be an object")
    proposals.extend(threshold_proposals(run_dir))
    amount = int(usage.get("amount_cents") or 0)
    metrics = compute_metrics(run_dir, caps, stop_reason)
    charged = _book_if_it_fits(
        run_dir,
        run_id,
        amount,
        model,
        effort,
        stage_cap_cents,
        run_cap_cents,
        spent_cents,
    )
    if charged != amount:
        metrics["budget_events"] = int(metrics["budget_events"]) + 1
    metrics["cost_usd"] = round(_ledger_cents(run_dir) / 100, 4)
    _write(run_dir, metrics, proposals, model, charged == amount)
    return RetroResult("recorded", charged, len(proposals))


def compute_metrics(run_dir: Path, caps: dict[str, int], stop_reason: str) -> dict:
    """Deterministic metrics from the ledger and stage artifacts."""
    rows = _ledger_rows(run_dir)
    by_stage: dict[str, int] = {}
    by_model: dict[str, int] = {}
    tokens = {"input": 0, "output": 0}
    for row in rows:
        stage = str(row.get("stage") or "")
        model = str(row.get("model") or "")
        amount = int(row.get("amount_cents") or 0)
        by_stage[stage] = by_stage.get(stage, 0) + amount
        by_model[model] = by_model.get(model, 0) + amount
        tokens["input"] += int(row.get("input_tokens") or 0)
        tokens["output"] += int(row.get("output_tokens") or 0)
    review = _read(run_dir / "review.json")
    verify = _read(run_dir / "verify.json")
    triage = _read(run_dir / "triage.json")
    routing = triage.get("routing") if isinstance(triage.get("routing"), dict) else {}
    halt = _read(run_dir / "halt.json")
    clarity = _read(run_dir / "clarity.json")
    survivors: dict[str, int] = {}
    for finding in review.get("findings") or []:
        if finding.get("survived"):
            family = str(finding.get("family") or "unknown")
            survivors[family] = survivors.get(family, 0) + 1
    total = sum(by_stage.values())
    verify_pass = verify.get("status") == "pass"
    success = stop_reason == "success" and verify_pass
    return {
        "success": success,
        "first_try_success": success and int(review.get("rounds_run") or 0) <= 1,
        "loops": int(review.get("rounds_run") or 0),
        "tokens": tokens,
        "cost": {"by_stage_cents": by_stage, "by_model_cents": by_model, "caps_cents": caps},
        "cost_usd": round(total / 100, 4),
        "active_seconds": 0,
        "wall_seconds": 0,
        "budget_events": 1 if stop_reason == "budget-halt" else 0,
        "trajectory_violations": 0,
        "hidden_failure": False,
        "owner_interventions": 0,
        "rounds_to_pass": int(review.get("rounds_run") or 0),
        "verify_pass": verify_pass,
        "verification": verify.get("verification") or verify.get("status") or "",
        "flaky_tests": len(verify.get("flaky") or []),
        "stop_reason": stop_reason,
        "halt_reason": halt.get("halt_reason") or "",
        "needed_cents": int(halt.get("needed_cents") or 0),
        "tier": routing.get("tier") or triage.get("tier"),
        "lane": routing.get("lane"),
        "escalated": bool(routing.get("escalated")),
        "clarity_score": clarity.get("score"),
        "clarity_halted": bool(clarity.get("halted")),
        "duplicate_halted": bool(_read(run_dir / "duplicate.json").get("halted")),
        "review_survivors_by_family": survivors,
        "agreement_rate": agreement_rate(run_dir),
        "recorded_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }


def _proposal(item: object) -> dict:
    if not isinstance(item, dict):
        raise RetroError("each proposal must be an object")
    proposal = {
        "id": str(item.get("id") or ""),
        "category": str(item.get("category") or ""),
        "severity": str(item.get("severity") or "low"),
        "evidence": str(item.get("evidence") or ""),
        "proposed_change": str(item.get("proposed_change") or ""),
        "target": str(item.get("target") or "config"),
        "expected_metric": str(item.get("expected_metric") or ""),
        "status": "proposed",
        "applied": False,
    }
    if not proposal["id"] or not proposal["proposed_change"]:
        raise RetroError("a proposal needs an id and a proposed change")
    return proposal


def _write(run_dir: Path, metrics: dict, proposals: list[dict], model: str, charged: bool) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    retro = {"applied": False, "charged": charged, "items": proposals, "model": model}
    (run_dir / "retro.json").write_text(
        json.dumps(retro, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = ["# Retro", "", f"Stop reason: {metrics['stop_reason']}", ""]
    if not proposals:
        lines.append("No proposals.")
    for item in proposals:
        lines.append(f"- {item['id']} ({item['category']}): {item['proposed_change']}")
        lines.append(f"  Evidence: {item['evidence']}")
    lines.append("")
    lines.append("Not applied.")
    (run_dir / "retro.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _book_if_it_fits(
    run_dir: Path,
    run_id: str,
    amount: int,
    model: str,
    effort: str,
    stage_cap: int,
    run_cap: int,
    spent: int,
) -> int:
    if amount <= 0:
        return 0
    try:
        budget = authorize_session_cents(
            spent_cents=spent,
            run_cap_cents=run_cap,
            stage_cap_cents=stage_cap,
            stage="retro",
        )
    except BudgetHardStop:
        return 0
    if amount > budget:
        return 0
    Ledger(run_dir / "ledger.jsonl").append(
        LedgerEntry(
            run_id=run_id,
            stage="retro",
            amount_cents=amount,
            provider="anthropic",
            model=model,
            effort=effort,
            note="fixture",
            budget_cents=budget,
        ),
        run_cap_cents=run_cap,
    )
    return amount


def _ledger_rows(run_dir: Path) -> list[dict]:
    path = run_dir / "ledger.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _ledger_cents(run_dir: Path) -> int:
    return sum(int(row.get("amount_cents") or 0) for row in _ledger_rows(run_dir))


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}
