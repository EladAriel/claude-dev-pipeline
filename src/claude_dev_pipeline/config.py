"""Load the pipeline, model, and budget YAML files."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

from claude_dev_pipeline.plan import DEFAULT_CAPS_CENTS, DEFAULT_MODELS, Stage


def load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a mapping")
    return data


def parse_cents(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"cents value must be an int or a decimal string, got {value!r}")
    return int(value)


def parse_caps(budgets: dict) -> dict[str, int]:
    raw = budgets.get("caps_cents") or {}
    if not isinstance(raw, dict):
        raise ValueError("caps_cents must be a mapping")
    parsed = dict(DEFAULT_CAPS_CENTS)
    for key, value in raw.items():
        parsed[str(key)] = parse_cents(value)
    return parsed


def dollars_to_cents(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str, float)):
        raise ValueError(f"dollar value must be numeric, got {value!r}")
    amount = Decimal(str(value))
    return int((amount * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def run_cap_cents(budgets: dict, pipeline: dict) -> int:
    override = (pipeline.get("budgets_usd") or {}).get("run")
    if override is not None:
        return dollars_to_cents(override)
    return parse_caps(budgets)["run"]


def effective_model(stage: Stage, models: dict, pipeline: dict) -> dict[str, str]:
    if stage.model_key == "none":
        return {"provider": "local", "model": "none", "effort": "none"}
    merged = dict(DEFAULT_MODELS[stage.model_key])
    file_model = models.get(stage.model_key) or {}
    if not isinstance(file_model, dict):
        raise ValueError(f"model {stage.model_key} must be a mapping")
    merged.update({key: str(value) for key, value in file_model.items() if value is not None})
    override = (pipeline.get("models") or {}).get(stage.model_key) or {}
    if not isinstance(override, dict):
        raise ValueError(f"pipeline model {stage.model_key} must be a mapping")
    merged.update({key: str(value) for key, value in override.items() if value is not None})
    return merged


def stage_enabled(pipeline: dict, key: str) -> bool:
    stages = pipeline.get("stages") or {}
    spec = stages.get(key)
    if spec is None:
        return True
    if not isinstance(spec, dict):
        raise ValueError(f"stages.{key} must be a mapping")
    return bool(spec.get("enabled", True))
