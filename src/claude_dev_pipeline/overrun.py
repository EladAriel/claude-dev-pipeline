"""Project stage cost before any write. Halt on a boundary, or downgrade early."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from claude_dev_pipeline.budget import cheapest_model, max_output_tokens
from claude_dev_pipeline.config import load_yaml
from claude_dev_pipeline.tiers import allowlist_for

STAGE_ORDER = ("triage", "spec", "plan", "implement", "verify", "review", "pr")


@dataclass(frozen=True)
class Projection:
    action: str
    reason: str
    estimate_cents: int
    remaining_cents: int
    needed_cents: int
    max_tokens: int
    downgraded_models: dict


def load_policy(path: Path) -> dict:
    if not path.is_file():
        return {"enabled": False}
    data = load_yaml(path)
    data.setdefault("enabled", True)
    data.setdefault("soft_ratio", 0.8)
    data.setdefault("estimates_cents", {})
    return data


def project_stage(
    *,
    stage: str,
    spent_cents: int,
    run_cap_cents: int,
    tier_cap_cents: int | None,
    estimate_cents: int,
    quoted_cents: int,
    quoted_output_tokens: int,
    soft_ratio: float,
    model: str,
    estimates: dict[str, int],
) -> Projection:
    """Decide before the stage writes anything."""
    cap = run_cap_cents if not tier_cap_cents else min(run_cap_cents, tier_cap_cents)
    remaining = max(0, cap - spent_cents)
    projected = max(int(estimate_cents), int(quoted_cents))
    tokens = max_output_tokens(model, remaining)
    later = _needed(stage, estimates)
    if projected > remaining or quoted_output_tokens > tokens:
        reason = (
            "call-overshoot"
            if quoted_cents > remaining or quoted_output_tokens > tokens
            else "projected-overrun"
        )
        return Projection("halt", reason, projected, remaining, later, tokens, {})
    soft = int(cap * float(soft_ratio))
    if spent_cents + projected >= soft:
        return Projection("downgrade", "soft-threshold", projected, remaining, later, tokens, {})
    return Projection("proceed", "", projected, remaining, later, tokens, {})


def downgrade_models(models: dict) -> dict:
    """Cheapest allowlisted model and low effort. Security blocking is unchanged."""
    updated = {
        key: dict(value) if isinstance(value, dict) else value for key, value in models.items()
    }
    chosen = {}
    for stage, spec in list(updated.items()):
        if not isinstance(spec, dict):
            continue
        cheapest = cheapest_model(allowlist_for(models, stage))
        if not cheapest:
            continue
        current = dict(spec)
        current["model"] = cheapest
        current["effort"] = "low"
        updated[stage] = current
        chosen[stage] = cheapest
    return {"models": updated, "chosen": chosen, "review_depth": "Quick", "security_block": True}


def quoted_usage(path: Path | None) -> tuple[int, int]:
    if path is None or not path.is_file():
        return 0, 0
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return 0, 0
    amount, tokens = _usage(data.get("usage"))
    tickets = data.get("tickets")
    if isinstance(tickets, dict):
        for session in tickets.values():
            if isinstance(session, dict):
                extra_amount, extra_tokens = _usage(session.get("usage"))
                amount += extra_amount
                tokens += extra_tokens
    return amount, tokens


def write_halt(
    run_dir: Path, *, run_id: str, stage: str, projection: Projection, spent_cents: int
) -> None:
    completed = (
        [name for name in STAGE_ORDER if STAGE_ORDER.index(name) < STAGE_ORDER.index(stage)]
        if stage in STAGE_ORDER
        else []
    )
    payload = {
        "completed_stages": completed,
        "halt_reason": projection.reason,
        "kept_worktrees": True,
        "ledger_offset": _ledger_bytes(run_dir),
        "max_tokens": projection.max_tokens,
        "needed_cents": projection.needed_cents,
        "pushed": False,
        "remaining_stages": [name for name in STAGE_ORDER if name not in completed],
        "run_id": run_id,
        "spent_cents": spent_cents,
        "stopped_before": stage,
        "version": 1,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "halt.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run_dir / "state.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _needed(stage: str, estimates: dict[str, int]) -> int:
    if stage not in STAGE_ORDER:
        return int(estimates.get(stage) or 0)
    index = STAGE_ORDER.index(stage)
    return sum(int(estimates.get(name) or 0) for name in STAGE_ORDER[index:])


def _usage(raw: object) -> tuple[int, int]:
    if not isinstance(raw, dict):
        return 0, 0
    return int(raw.get("amount_cents") or 0), int(raw.get("output_tokens") or 0)


def _ledger_bytes(run_dir: Path) -> int:
    path = run_dir / "ledger.jsonl"
    if not path.is_file():
        return 0
    return path.stat().st_size
