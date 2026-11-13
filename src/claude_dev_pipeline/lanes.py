"""Fast lane for tier S. Spec and plan are synthesized. Big diffs escalate."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path

from claude_dev_pipeline.config import stage_enabled


def fast_lane(pipeline: dict, routing: dict) -> bool:
    if not stage_enabled(pipeline, "fast"):
        return False
    if routing.get("lane") != "fast":
        return False
    return (pipeline.get("fast") or {}).get("enabled", True) is not False


def limits_for(pipeline: dict) -> dict:
    raw = pipeline.get("fast") or {}
    globs = raw.get("security_globs") or [
        "**/auth/**",
        "**/*secret*",
        ".github/workflows/**",
    ]
    return {
        "max_files": int(raw.get("max_files") or 3),
        "max_lines": int(raw.get("max_lines") or 200),
        "max_verify_failures": int(raw.get("max_verify_failures") or 2),
        "security_globs": [str(item) for item in globs],
    }


def write_synthesized(run_dir: Path, issue: dict) -> None:
    title = str(issue.get("title") or "Fast-lane change")
    body = str(issue.get("body") or title)
    spec = {
        "acceptance_criteria": [{"id": "AC-1", "statement": title}],
        "e2e_scenarios": [{"id": "E2E-1", "scenario": title}],
        "needs_human": False,
        "open_questions": [],
        "seams": ["change"],
        "title": title,
    }
    plan = {
        "tasks": [
            {
                "acceptance_criteria": ["AC-1"],
                "blocked_by": [],
                "body": body,
                "estimate": "S",
                "files": ["unknown"],
                "id": "T1",
                "seams": ["change"],
                "suggested_model": "claude-sonnet-5.5",
                "test_plan": body,
                "title": title,
            }
        ]
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "spec.json").write_text(
        json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run_dir / "spec.md").write_text(f"# Spec\n\nAC-1: {title}\n", encoding="utf-8")
    (run_dir / "plan.json").write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def diff_stats(run_dir: Path) -> tuple[list[str], int]:
    files: list[str] = []
    lines = 0
    for path in sorted(run_dir.glob("impl-*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        root = Path(str(report.get("worktree") or ""))
        for relative in report.get("files_changed") or []:
            files.append(str(relative))
            candidate = root / str(relative)
            if candidate.is_file():
                lines += len(candidate.read_text(encoding="utf-8").splitlines())
    return files, lines


def touches_security(files: list[str], patterns: list[str]) -> bool:
    for path in files:
        for pattern in patterns:
            if fnmatch.fnmatch(path, pattern):
                return True
            needle = pattern.strip("*").strip("/")
            if needle and needle in path:
                return True
    return False


def escalate_for_diff(files: list[str], line_count: int, limits: dict) -> str | None:
    if len(files) > limits["max_files"] or line_count > limits["max_lines"]:
        if line_count > limits["max_lines"] * 2 or len(files) > limits["max_files"] * 2:
            return "full"
        return "standard"
    return None


def mark_escalated(run_dir: Path, lane: str, reason: str) -> None:
    path = run_dir / "triage.json"
    triage = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    routing = dict(triage.get("routing") or {})
    routing["escalated"] = True
    routing["escalation_reason"] = reason
    routing["lane"] = lane
    routing["review_depth"] = "Deep" if lane == "full" else "Standard"
    triage["routing"] = routing
    path.write_text(json.dumps(triage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_plan(run_dir, only_checks=None, codex=True, escalated=True, reason=reason)


def write_fast_review(run_dir: Path, pipeline: dict) -> dict:
    files, _lines = diff_stats(run_dir)
    limits = limits_for(pipeline)
    secure = touches_security(files, limits["security_globs"])
    checks = ["bug-hunt"]
    if secure:
        checks.append("security")
    plan = _write_plan(run_dir, only_checks=checks, codex=secure, escalated=False, reason="")
    return plan


def _write_plan(
    run_dir: Path,
    *,
    only_checks: list[str] | None,
    codex: bool,
    escalated: bool,
    reason: str,
) -> dict:
    plan = {
        "codex": codex,
        "escalated": escalated,
        "only_checks": only_checks,
        "reason": reason,
    }
    (run_dir / "fast.json").write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return plan
