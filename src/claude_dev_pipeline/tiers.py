"""Difficulty tiers. Start cheap, and raise the tier when the signals say so."""

from __future__ import annotations

from pathlib import Path

from claude_dev_pipeline.config import load_yaml

ORDER = ("S", "M", "L", "XL")
RISK_TO_TIER = {"Quick": "S", "Standard": "M", "Deep": "L"}
TIER_TO_RISK = {"S": "Quick", "M": "Standard", "L": "Deep", "XL": "Deep"}


class TierApprovalRequired(Exception):
    def __init__(self) -> None:
        super().__init__("tier XL needs explicit approval; rerun with --approve-xl")


def load_tiers(path: Path) -> dict:
    if not path.is_file():
        return {"enabled": False, "tiers": {}}
    data = load_yaml(path)
    data.setdefault("enabled", True)
    data.setdefault("tiers", {})
    return data


def allowlist_for(models: dict, stage: str) -> list[str]:
    spec = models.get(stage) if isinstance(models, dict) else None
    if not isinstance(spec, dict):
        return []
    raw = spec.get("allowlist")
    if isinstance(raw, list) and raw:
        return [str(item) for item in raw if str(item).strip()]
    model = str(spec.get("model") or "").strip()
    return [model] if model else []


def assign_tier(artifact: dict, tiers: dict, models: dict) -> dict:
    """Fill tier, signals, and routing. Raise the tier when signals demand it."""
    updated = dict(artifact)
    signals = _signals(artifact.get("signals"))
    stated = str(artifact.get("tier") or RISK_TO_TIER.get(str(artifact.get("risk_tier")), "M"))
    if stated not in ORDER:
        stated = "M"
    floor = _signal_floor(signals, tiers.get("signal_floors") or {})
    chosen = _higher(stated, floor)
    spec = (tiers.get("tiers") or {}).get(chosen) or {}
    overrides = _legal_overrides(spec.get("models") or {}, models)
    updated["tier"] = chosen
    updated["tier_rationale"] = str(artifact.get("tier_rationale") or _rationale(chosen, stated))
    updated["signals"] = signals
    updated["risk_tier"] = spec.get("risk_tier") or TIER_TO_RISK[chosen]
    updated["routing"] = {
        "budget_cents": int(spec.get("budget_cents") or 0),
        "codex": str(spec.get("codex") or "on"),
        "escalated": chosen != stated,
        "lane": str(spec.get("lane") or "standard"),
        "models": overrides,
        "requires_approval": bool(spec.get("requires_approval")),
        "review_depth": str(spec.get("review_depth") or updated["risk_tier"]),
        "tier": chosen,
    }
    return updated


def apply_routing_models(models: dict, routing: dict) -> dict:
    updated = {
        key: dict(value) if isinstance(value, dict) else value for key, value in models.items()
    }
    for stage, spec in (routing.get("models") or {}).items():
        current = dict(updated.get(stage) or {})
        current.update(spec)
        updated[stage] = current
    return updated


def _signals(raw: object) -> dict:
    source = raw if isinstance(raw, dict) else {}
    ambiguity = str(source.get("ambiguity") or "low")
    if ambiguity not in {"low", "medium", "high"}:
        ambiguity = "medium"
    return {
        "ambiguity": ambiguity,
        "cross_module": bool(source.get("cross_module")),
        "files_touched": max(0, int(source.get("files_touched") or 1)),
        "security_sensitive": bool(source.get("security_sensitive")),
    }


def _signal_floor(signals: dict, floors: dict) -> str:
    floor = "S"
    files = int(signals["files_touched"])
    for tier, minimum in (floors.get("files_touched") or {}).items():
        if files >= int(minimum):
            floor = _higher(floor, str(tier))
    if signals["cross_module"]:
        floor = _higher(floor, str(floors.get("cross_module") or "M"))
    if signals["security_sensitive"]:
        floor = _higher(floor, str(floors.get("security_sensitive") or "L"))
    if signals["ambiguity"] == "high":
        floor = _higher(floor, str(floors.get("ambiguity_high") or "L"))
    return floor if floor in ORDER else "S"


def _legal_overrides(raw: object, models: dict) -> dict:
    if not isinstance(raw, dict):
        return {}
    legal = {}
    for stage, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        model = str(spec.get("model") or "").strip()
        allowed = allowlist_for(models, str(stage))
        if model and allowed and model not in allowed:
            continue
        if not model:
            continue
        legal[str(stage)] = {"effort": str(spec.get("effort") or ""), "model": model}
    return legal


def _higher(left: str, right: str) -> str:
    if left not in ORDER:
        left = "S"
    if right not in ORDER:
        right = "S"
    return ORDER[max(ORDER.index(left), ORDER.index(right))]


def _rationale(chosen: str, stated: str) -> str:
    if chosen == stated:
        return f"Tier {chosen} matches the estimate."
    return f"Tier raised from {stated} to {chosen} because of the signals."
