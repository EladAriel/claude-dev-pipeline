"""Questions, research, design, and ops requests do not open a pull request."""

from __future__ import annotations

import json
import re
from pathlib import Path

from claude_dev_pipeline.github_gate import admit_issue

_HINTS = (
    "how do i",
    "how does",
    "what is",
    "research",
    "design discussion",
    "ops request",
    "no code",
)
_CODE = re.compile(r"\.(py|go|js|ts|tsx|java|rb|rs|sql)\b", re.I)


def enabled(config: dict) -> bool:
    return _section(config).get("enabled", True) is not False


def classify(issue: dict, config: dict) -> dict | None:
    if not enabled(config):
        return None
    text = f"{issue.get('title') or ''}\n{issue.get('body') or ''}".lower()
    if _CODE.search(text):
        return None
    if not any(hint in text for hint in _HINTS):
        return None
    return {"lane": "no-code", "escalated": False}


def draft(issue: dict, fixture: dict | None) -> dict:
    """A fixture may supply the cheap-model draft. Otherwise the template is enough."""
    if isinstance(fixture, dict) and str(fixture.get("answer") or "").strip():
        return {
            "answer": str(fixture["answer"]).strip(),
            "needs_code": bool(fixture.get("needs_code")),
            "amount_cents": int((fixture.get("usage") or {}).get("amount_cents") or 0),
            "model_called": True,
        }
    title = str(issue.get("title") or "this request")
    return {
        "answer": f"No code change was requested for {title}.",
        "needs_code": False,
        "amount_cents": 0,
        "model_called": False,
    }


def apply_answer(
    run_dir: Path,
    issue: dict,
    config: dict,
    fixture: dict | None,
    *,
    approved: bool,
) -> dict:
    decision = classify(issue, config) or {"lane": "", "escalated": False}
    written = draft(issue, fixture)
    needs_code = bool(written["needs_code"])
    decision["needs_code"] = needs_code
    decision["escalated"] = needs_code
    decision["approved"] = approved
    decision["model_called"] = written["model_called"]
    decision["amount_cents"] = written["amount_cents"]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "answer.md").write_text(written["answer"] + "\n", encoding="utf-8")
    (run_dir / "nocode.json").write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    decision["commented"] = False
    if decision.get("lane") == "no-code" and not needs_code and _may_comment(issue, config):
        comment = run_dir / "nocode-comment.txt"
        if not comment.exists():
            comment.write_text(written["answer"] + "\n", encoding="utf-8")
        decision["commented"] = comment.is_file()
        (run_dir / "nocode.json").write_text(
            json.dumps(decision, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return decision


def summary_lines(run_dir: Path) -> list[str]:
    path = run_dir / "nocode.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("escalated") and not data.get("approved"):
        return ["", "No-code lane: a code change needs approval before the normal lane continues."]
    if data.get("lane") == "no-code":
        return ["", "No-code lane: the answer is in answer.md. No pull request was opened."]
    return []


def _may_comment(issue: dict, config: dict) -> bool:
    if not admit_issue(
        labels=list(issue.get("labels") or []),
        timeline=list(issue.get("timeline") or []),
    ).allowed:
        return False
    author = str(issue.get("author") or (issue.get("user") or {}).get("login") or "")
    allowed = _section(config).get("allowed_authors") or ["EladAriel"]
    return author in {str(item) for item in allowed}


def _section(config: dict) -> dict:
    return (config.get("stages") or {}).get("nocode") or {}
