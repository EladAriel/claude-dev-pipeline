"""Local task intake. Issue admission uses a snapshot file, not the network."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from claude_dev_pipeline.github_gate import GITHUB_APPROVED_LABEL, admit_issue


@dataclass(frozen=True)
class Intake:
    kind: str
    ref: str
    issue_number: int | None = None


class IntakeError(Exception):
    def __init__(self, message: str, *, code: int) -> None:
        super().__init__(message)
        self.code = code


def load_issue_snapshot(number: int, path: Path) -> dict:
    if not path.exists():
        raise IntakeError(
            f"no local snapshot at {path}; the scaffold does not call GitHub to check {number}",
            code=2,
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise IntakeError(f"{path} must be a JSON object", code=2)
    if int(data.get("number", number)) != number:
        raise IntakeError(f"{path} is for issue {data.get('number')}, not {number}", code=2)
    return data


def intake_from_issue(number: int, snapshot_path: Path) -> Intake:
    """Admit an issue only when the snapshot shows the owner applied the label."""
    data = load_issue_snapshot(number, snapshot_path)
    decision = admit_issue(
        labels=list(data.get("labels") or []),
        timeline=list(data.get("timeline") or []),
        label=GITHUB_APPROVED_LABEL,
    )
    if not decision.allowed:
        raise IntakeError(decision.reason, code=1)
    return Intake(kind="issue", ref=str(number), issue_number=number)


def intake_from_task(text: str) -> Intake:
    cleaned = text.strip()
    if not cleaned:
        raise IntakeError("task text is empty", code=2)
    return Intake(kind="task", ref=cleaned)


def intake_from_spec(path: Path) -> Intake:
    if not path.is_file():
        raise IntakeError(f"spec not found: {path}", code=2)
    body = path.read_text(encoding="utf-8")
    if not body.strip():
        raise IntakeError(f"spec is empty: {path}", code=2)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return Intake(kind="spec", ref=f"{path}:{digest}")
