"""Product summary. The template is deterministic. Haiku adds one paragraph."""

from __future__ import annotations

import json
from pathlib import Path

from claude_dev_pipeline.clarity import summary_lines
from claude_dev_pipeline.conflicts import summary_lines as conflict_lines
from claude_dev_pipeline.deps import summary_lines as deps_lines
from claude_dev_pipeline.docs_lane import summary_lines as docs_lines
from claude_dev_pipeline.duplicates import summary_lines as duplicate_lines
from claude_dev_pipeline.flaky import risk_line
from claude_dev_pipeline.github_gate import admit_issue
from claude_dev_pipeline.ledger import Ledger, LedgerEntry


def write_summary(
    run_dir: Path,
    *,
    asked: str,
    stop_reason: str,
    narrative: str | None = None,
    narrative_cents: int = 0,
    snapshot: dict | None = None,
    run_id: str = "",
    run_cap_cents: int = 2000,
) -> str:
    metrics = _read(run_dir / "metrics.json")
    routing = _routing(run_dir)
    pr = _read(run_dir / "pr.json")
    diagnosis = run_dir / "diagnosis.md"
    cost = _cost(run_dir)
    paragraph = (
        narrative.strip() if narrative and narrative.strip() else _fallback(stop_reason, metrics)
    )
    built = pr.get("pr_url") or "No pull request was opened."
    if pr.get("branch") and not pr.get("pr_url"):
        built = f"Branch `{pr['branch']}` is local only."
    lines = [
        "# Summary",
        "",
        paragraph,
        "",
        "## What was asked",
        asked or "No request text was recorded.",
        "",
        "## What was decided and why",
        _decision(routing, stop_reason),
        "",
        "## What was built",
        built,
        "",
        "## Open items and risks",
        _risks(run_dir, metrics),
        "",
        "## Cost and time",
        f"Cost so far: ${cost / 100:.2f}. Active time is not measured in the offline run.",
        "",
        "## Tier and lane",
        _tier_line(routing, metrics),
        "",
        "## Needed from you",
        _needed(stop_reason, pr),
    ]
    halt = _read(run_dir / "halt.json")
    if halt:
        lines.extend(["", "## Budget", _budget_note(halt)])
    if diagnosis.is_file():
        lines.extend(["", "## Diagnosis", "See `diagnosis.md` in this run."])
    lines.extend(summary_lines(run_dir))
    lines.extend(duplicate_lines(run_dir))
    lines.extend(conflict_lines(run_dir))
    lines.extend(docs_lines(run_dir))
    lines.extend(deps_lines(run_dir))
    text = "\n".join(lines) + "\n"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.md").write_text(text, encoding="utf-8")
    comment = run_dir / "summary-comment.txt"
    if (
        snapshot
        and not comment.exists()
        and admit_issue(
            labels=list(snapshot.get("labels") or []),
            timeline=list(snapshot.get("timeline") or []),
        ).allowed
    ):
        comment.write_text(text, encoding="utf-8")
    if narrative and narrative_cents > 0 and run_id:
        Ledger(run_dir / "ledger.jsonl").append(
            LedgerEntry(
                run_id=run_id,
                stage="summary",
                amount_cents=narrative_cents,
                provider="anthropic",
                model="claude-haiku-5.5",
                effort="low",
                note="narrative",
                budget_cents=0,
            ),
            run_cap_cents=run_cap_cents,
        )
    return text


def _decision(routing: dict, stop_reason: str) -> str:
    tier = routing.get("tier") or "unspecified"
    lane = routing.get("lane") or "standard"
    why = routing.get("escalation_reason") or "the triage signals"
    if routing.get("escalated"):
        return f"The run started at tier {tier} and escalated ({why}). Stop reason: {stop_reason}."
    return f"The run stayed on tier {tier}, lane {lane}. Stop reason: {stop_reason}."


def _tier_line(routing: dict, metrics: dict) -> str:
    tier = routing.get("tier") or metrics.get("tier") or "unspecified"
    lane = routing.get("lane") or metrics.get("lane") or "standard"
    escalated = "yes" if routing.get("escalated") or metrics.get("escalated") else "no"
    return f"Tier {tier}, lane {lane}. Escalated: {escalated}."


def _risks(run_dir: Path, metrics: dict) -> str:
    verify = _read(run_dir / "verify.json")
    if verify.get("status") == "unverified":
        return (
            "WARNING: no test command was detected. Verification is unverified. "
            "Review depth was raised one level."
        )
    flaky = risk_line(verify)
    if flaky:
        return flaky
    if (run_dir / "diagnosis.md").is_file() and not (run_dir / "pr.json").is_file():
        return "The diagnosis is not a fix. Nothing has been merged."
    if metrics.get("stop_reason") == "budget-halt" or metrics.get("budget_events"):
        return "The run stopped on the budget. Review the ledger before retrying."
    review = _read(run_dir / "review.json")
    if review.get("security_block"):
        return "A security finding is still blocking the pull request."
    return "No open security finding was recorded."


def _needed(stop_reason: str, pr: dict) -> str:
    if pr.get("pr_url") or pr.get("branch"):
        return "Review the diff and merge it if you are satisfied. The pipeline does not merge."
    if stop_reason in {"budget-halt", "tier-xl", "stop", "escalated"}:
        return "Decide whether to approve the next step or change the approach."
    return "Nothing else is required unless you want a follow-up."


def _budget_note(halt: dict) -> str:
    done = ", ".join(halt.get("completed_stages") or []) or "nothing"
    left = ", ".join(halt.get("remaining_stages") or []) or "nothing"
    needed = int(halt.get("needed_cents") or 0) / 100
    reason = halt.get("halt_reason") or "budget"
    return (
        f"Halted before {halt.get('stopped_before') or 'the next stage'} ({reason}). "
        f"Done: {done}. Left: {left}. "
        f"About ${needed:.2f} more would be needed to finish. Worktrees were kept. "
        "Nothing was pushed."
    )


def _fallback(stop_reason: str, metrics: dict) -> str:
    tier = metrics.get("tier") or "an unspecified tier"
    return (
        f"This run finished with {stop_reason} on {tier}. "
        "The notes below are taken from the ledger."
    )


def _cost(run_dir: Path) -> int:
    path = run_dir / "ledger.jsonl"
    if not path.is_file():
        return 0
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            total += int(json.loads(line).get("amount_cents") or 0)
    return total


def _routing(run_dir: Path) -> dict:
    triage = _read(run_dir / "triage.json")
    routing = triage.get("routing")
    return routing if isinstance(routing, dict) else {}


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}
