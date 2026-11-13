"""Score whether an issue is clear enough to spend spec budget."""

from __future__ import annotations

import json
import re
from pathlib import Path

from claude_dev_pipeline.config import load_yaml
from claude_dev_pipeline.github_gate import admit_issue

_QUESTIONS = {
    "goal": "What should change, in one sentence?",
    "acceptance": "How will you know it is done?",
    "repro": "What are the steps that show the bug?",
    "scope": "What is in scope, and what is out of scope?",
}


def load_clarity(path: Path) -> dict:
    if not path.is_file():
        return {"enabled": False}
    data = load_yaml(path)
    data.setdefault("enabled", True)
    data.setdefault("threshold", 75)
    data.setdefault("max_questions", 3)
    data.setdefault("allowed_authors", ["EladAriel"])
    return data


def score_issue(snapshot: dict, classify: str, *, threshold: int, max_questions: int) -> dict:
    """Deterministic score from the issue text. Missing parts become questions."""
    text = _text(snapshot)
    title_words = re.findall(r"[a-z0-9']+", str(snapshot.get("title") or "").lower())
    body_words = re.findall(r"[a-z0-9']+", str(snapshot.get("body") or "").lower())
    bug = classify == "bug"
    fields = ("goal", "acceptance", "repro", "scope") if bug else ("goal", "acceptance", "scope")
    present = {
        "goal": bool(title_words) or _has("goal", text),
        "acceptance": bool(body_words) or _has("acceptance", text),
        "repro": len(body_words) >= 2 or _has("repro", text),
        "scope": bool(body_words) or _has("scope", text),
    }
    missing = [name for name, ok in present.items() if not ok]
    score = int(round(100 * (len(fields) - len(missing)) / len(fields)))
    questions = [_QUESTIONS[name] for name in missing][: max(0, int(max_questions))]
    return {
        "classify": classify,
        "halted": score < int(threshold),
        "missing": missing,
        "questions": questions,
        "score": score,
        "threshold": int(threshold),
    }


def apply_clarity(
    run_dir: Path,
    snapshot: dict,
    classify: str,
    policy: dict,
    *,
    inline: bool,
) -> dict:
    report = score_issue(
        snapshot,
        classify,
        threshold=int(policy.get("threshold") or 75),
        max_questions=int(policy.get("max_questions") or 3),
    )
    report["inline"] = inline
    report["commented"] = False
    if report["halted"] and not inline and _may_comment(snapshot, policy):
        comment = run_dir / "clarity-comment.txt"
        if not comment.exists():
            comment.write_text(_comment(report["questions"]), encoding="utf-8")
            (run_dir / "clarity-labels.json").write_text(
                json.dumps({"add": ["pipeline:needs-info"]}, indent=2) + "\n",
                encoding="utf-8",
            )
        report["commented"] = comment.is_file()
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "clarity.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def summary_lines(run_dir: Path) -> list[str]:
    path = run_dir / "clarity.json"
    if not path.is_file():
        return []
    report = json.loads(path.read_text(encoding="utf-8"))
    if not report.get("halted"):
        return []
    missing = ", ".join(report.get("missing") or []) or "detail"
    questions = report.get("questions") or []
    lines = [
        "",
        "## What is missing",
        f"Clarity score {report.get('score')} is below {report.get('threshold')}.",
        f"Missing: {missing}.",
    ]
    for question in questions:
        lines.append(f"- {question}")
    lines.append("Spec did not run. Answer the questions, then resume.")
    return lines


def _may_comment(snapshot: dict, policy: dict) -> bool:
    if not admit_issue(
        labels=list(snapshot.get("labels") or []),
        timeline=list(snapshot.get("timeline") or []),
    ).allowed:
        return False
    author = str(snapshot.get("author") or (snapshot.get("user") or {}).get("login") or "")
    allowed = {str(name) for name in policy.get("allowed_authors") or []}
    return bool(author) and author in allowed


def _comment(questions: list[str]) -> str:
    lines = [
        "<!-- pipeline-questions -->",
        "This issue needs a bit more detail before a spec starts.",
        "",
    ]
    for index, question in enumerate(questions, start=1):
        lines.append(f"{index}. {question}")
    return "\n".join(lines) + "\n"


def _text(snapshot: dict) -> str:
    parts = [str(snapshot.get("title") or ""), str(snapshot.get("body") or "")]
    for comment in snapshot.get("comments") or []:
        if isinstance(comment, dict):
            parts.append(str(comment.get("body") or ""))
        else:
            parts.append(str(comment))
    return "\n".join(parts).lower()


def _has(name: str, text: str) -> bool:
    patterns = {
        "goal": r"\b(goal|should|want|need to)\b",
        "acceptance": r"\b(acceptance|done when|success)\b",
        "repro": r"\b(repro|steps|when i)\b",
        "scope": r"\b(scope|out of scope|only)\b",
    }
    return re.search(patterns[name], text) is not None
