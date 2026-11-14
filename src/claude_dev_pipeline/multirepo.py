"""One coordinated pull request per configured repository."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def enabled(config: dict) -> bool:
    return _section(config).get("enabled", True) is not False


def allowed(config: dict) -> dict[str, dict]:
    found = {}
    for item in _section(config).get("repos") or []:
        if isinstance(item, dict) and item.get("name"):
            found[str(item["name"])] = item
    return found


def split_tasks(plan: dict, config: dict) -> dict:
    """Tasks for repos that are not configured are refused and never opened."""
    known = allowed(config)
    groups: dict[str, list] = {}
    refused = []
    for task in plan.get("tasks") or []:
        name = str(task.get("repo") or "")
        if not name:
            groups.setdefault("", []).append(task)
            continue
        if name not in known:
            refused.append({"repo": name, "reason": "not-listed"})
            continue
        groups.setdefault(name, []).append(task)
    return {"groups": groups, "refused": refused}


def coordinate(
    run_dir: Path,
    run_id: str,
    plan: dict,
    config: dict,
    budget_cents: int,
    verify=None,
) -> dict:
    if not enabled(config):
        return {"repos": [], "spent_cents": 0}
    pieces = split_tasks(plan, config)
    known = allowed(config)
    order = [name for name in known if name in pieces["groups"]]
    if not order and not pieces["refused"]:
        return {"repos": [], "spent_cents": 0}
    results = []
    spent = 0
    for name in order:
        item = known[name]
        path = Path(str(item.get("path") or ""))
        problem = _access(path, bool(item.get("approved")))
        if problem:
            results.append({"repo": name, "status": "halted", "reason": problem})
            continue
        cost = _cost(pieces["groups"][name])
        if spent + cost > budget_cents:
            results.append({"repo": name, "status": "halted", "reason": "budget"})
            continue
        branch = f"pipeline/{run_id}/{name}"
        created = _branch(path, branch)
        if not created:
            results.append({"repo": name, "status": "halted", "reason": "branch"})
            continue
        verified = verify(path) if verify else "succeeded"
        if verified != "succeeded":
            results.append({"repo": name, "status": "halted", "reason": "verify"})
            continue
        spent += cost
        results.append(
            {
                "repo": name,
                "status": "ready",
                "branch": branch,
                "cost_cents": cost,
            }
        )
    report = {
        "repos": results,
        "refused": pieces["refused"],
        "order": order,
        "spent_cents": spent,
        "budget_cents": budget_cents,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    _bodies(run_dir, report)
    (run_dir / "multirepo.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def summary_lines(run_dir: Path) -> list[str]:
    path = run_dir / "multirepo.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("repos") and not data.get("refused"):
        return []
    lines = ["", "Multi-repo:"]
    for item in data.get("repos") or []:
        label = f"{item.get('status')} {item.get('reason') or ''}".rstrip()
        lines.append(f"- {item.get('repo')}: {label}")
    for item in data.get("refused") or []:
        lines.append(f"- {item.get('repo')}: refused ({item.get('reason')})")
    if data.get("order"):
        lines.append("Merge order: " + ", ".join(data["order"]))
    return lines


def _bodies(run_dir: Path, report: dict) -> None:
    ready = [item for item in report["repos"] if item.get("status") == "ready"]
    order = ", ".join(report.get("order") or [])
    for item in ready:
        others = [f"{other['repo']} `{other['branch']}`" for other in ready if other is not item]
        lines = [
            f"Repo {item['repo']}.",
            "",
            f"Merge order: {order}",
            "",
        ]
        if others:
            lines.append("Also: " + "; ".join(others))
            lines.append("")
        lines.append("The pipeline does not merge.")
        path = run_dir / f"pr-{item['repo']}.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _access(path: Path, approved: bool) -> str:
    if not approved:
        return "not-approved"
    if not path.is_dir() or not (path / ".git").exists():
        return "not-a-repo"
    if not os.access(path, os.W_OK):
        return "not-writable"
    return ""


def _cost(tasks: list) -> int:
    total = 0
    for task in tasks:
        total += int(task.get("cost_cents") or 1)
    return total


def _branch(repo: Path, branch: str) -> bool:
    previous = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    created = _git(repo, "checkout", "-b", branch)
    if created.returncode != 0:
        return False
    if previous and previous != "HEAD":
        _git(repo, "checkout", previous)
    current = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return current == base


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, check=False, capture_output=True, text=True)


def _section(config: dict) -> dict:
    return (config.get("stages") or {}).get("multirepo") or {}
