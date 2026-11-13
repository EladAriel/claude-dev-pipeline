"""Owner-label gate. Pure: the caller supplies labels and the timeline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

GITHUB_APPROVED_LABEL = "agent:approved"
JIRA_APPROVED_LABEL = "agent-approved"
OWNER = "EladAriel"


@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: str


def admit_issue(
    *,
    labels: Sequence[str],
    timeline: Sequence[Mapping[str, str]],
    label: str = GITHUB_APPROVED_LABEL,
    owner: str = OWNER,
) -> Admission:
    """Admit only when ``label`` is present and its latest application is by ``owner``.

    Anything else is ignored and must not start a model call. ``timeline``
    entries look like ``{"event": "labeled", "label": "...", "actor": "..."}``.
    """
    if label not in labels:
        return Admission(False, f"missing {label}; no model call")
    appliers = [
        str(event.get("actor", ""))
        for event in timeline
        if event.get("event") == "labeled" and event.get("label") == label
    ]
    if not appliers:
        return Admission(
            False, f"{label} is present but no owner event was supplied; no model call"
        )
    if appliers[-1] != owner:
        return Admission(False, f"{label} was not applied by {owner}; no model call")
    return Admission(True, f"{owner} applied {label}")
