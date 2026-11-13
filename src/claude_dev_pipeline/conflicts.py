"""Rebase a pipeline branch onto a moved base. Never touch the base branch."""

from __future__ import annotations

import fnmatch
import json
import subprocess
from pathlib import Path

from claude_dev_pipeline.github_gate import admit_issue
from claude_dev_pipeline.ledger import Ledger, LedgerEntry

_MODEL = "claude-sonnet-5.5"


def enabled(config: dict) -> bool:
    section = (config.get("stages") or {}).get("conflicts") or {}
    return section.get("enabled", True) is not False


def needs_retry(repo: Path, run_dir: Path, run_id: str, base: str) -> bool:
    """True when a recorded result is stale because the base commit moved."""
    if not _exists(repo, _branch(run_id)):
        return False
    current = _rev(repo, base)
    recorded = _load(run_dir / "conflicts.json")
    if not recorded or not current:
        return _behind(repo, _branch(run_id), base)
    return recorded.get("base") != current


def fast_forward_ok(repo: Path, branch: str, remote_sha: str | None) -> bool:
    """A push is allowed only when the remote tip is already in the local branch."""
    if not remote_sha:
        return True
    completed = _git(repo, "merge-base", "--is-ancestor", remote_sha, branch)
    return completed.returncode == 0


def summary_lines(run_dir: Path) -> list[str]:
    data = _load(run_dir / "conflicts.json")
    if data.get("status") != "stopped":
        return []
    lines = ["", "## Conflicts", "WARNING: rebase conflicts remain."]
    for name in data.get("files") or []:
        lines.append(f"- {name}")
    return lines


def pr_lines(run_dir: Path) -> list[str]:
    data = _load(run_dir / "conflicts.json")
    if data.get("status") != "stopped":
        return []
    lines = ["WARNING: merge conflicts remain."]
    for name in data.get("files") or []:
        lines.append(f"- {name}")
    lines.append("")
    return lines


def settle(
    *,
    repo: Path,
    run_id: str,
    base: str,
    run_dir: Path,
    config: dict,
    fixture: dict | None = None,
    verify=None,
    review=None,
    labels: list[str] | None = None,
    timeline: list[dict] | None = None,
    spent_cents: int = 0,
    run_cap_cents: int = 2000,
) -> dict:
    """Rebase ``pipeline/<run-id>`` onto ``base``. Restore the previous checkout."""
    if not enabled(config):
        return {"status": "clean", "action": "disabled", "files": []}
    branch = _branch(run_id)
    if not _exists(repo, branch):
        return {"status": "clean", "action": "absent", "files": []}
    base_sha = _rev(repo, base)
    recorded = _load(run_dir / "conflicts.json")
    if recorded.get("base") == base_sha and recorded.get("status") in {"resolved", "stopped"}:
        return {
            "status": "clean" if recorded.get("status") == "resolved" else "stopped",
            "action": "cached",
            "files": list(recorded.get("files") or []),
        }
    if base_sha and not _behind(repo, branch, base):
        _save(run_dir, _payload("resolved", base_sha, branch, [], "current"))
        return {"status": "clean", "action": "current", "files": []}
    previous = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    started = _git(repo, "rebase", base, branch)
    try:
        if started.returncode == 0:
            return _checked(
                repo, run_dir, run_id, base, base_sha, branch, [], "clean", verify, review
            )
        files = _unmerged(repo)
        trivial, hard = _split_files(repo, files, _patterns(config))
        for path in trivial:
            _take_base(repo, path)
        hard = _unmerged(repo)
        if hard:
            reason = _apply_ticket(
                repo,
                run_dir,
                run_id,
                hard,
                fixture,
                config,
                spent_cents,
                run_cap_cents,
            )
            if reason:
                return _stop(
                    repo, run_dir, base, base_sha, branch, hard, reason, labels, timeline, previous
                )
            continued = _continue(repo)
            if continued.returncode != 0 or _unmerged(repo):
                return _stop(
                    repo,
                    run_dir,
                    base,
                    base_sha,
                    branch,
                    hard,
                    "resolve failed",
                    labels,
                    timeline,
                    previous,
                )
        elif _rebasing(repo):
            continued = _continue(repo)
            if continued.returncode != 0:
                return _stop(
                    repo,
                    run_dir,
                    base,
                    base_sha,
                    branch,
                    files,
                    "resolve failed",
                    labels,
                    timeline,
                    previous,
                )
        return _checked(
            repo,
            run_dir,
            run_id,
            base,
            base_sha,
            branch,
            files,
            "ticket" if hard else "trivial",
            verify,
            review,
        )
    finally:
        if _rev(repo, base) != base_sha:
            raise RuntimeError("the base branch moved during conflict resolution")
        if previous and previous != "HEAD" and _rebasing(repo) is False:
            _git(repo, "checkout", previous)


def _checked(repo, run_dir, run_id, base, base_sha, branch, files, mode, verify, review) -> dict:
    if _rev(repo, base) != base_sha:
        raise RuntimeError("the base branch moved during conflict resolution")
    verified = True
    if mode != "current":
        verified = _recheck(run_dir, verify, review)
    status = "resolved" if verified else "stopped"
    _save(run_dir, _payload(status, base_sha, branch, files, mode, verified=verified))
    if not verified:
        _draft(run_dir, base, branch, files)
    return {"status": status, "action": "resolved" if verified else "stopped", "files": files}


def _stop(repo, run_dir, base, base_sha, branch, files, reason, labels, timeline, previous) -> dict:
    if _rebasing(repo):
        _git(repo, "rebase", "--abort")
    if previous and previous != "HEAD":
        _git(repo, "checkout", previous)
    _save(run_dir, _payload("stopped", base_sha, branch, files, reason))
    _draft(run_dir, base, branch, files)
    _label(run_dir, labels or [], timeline or [])
    return {"status": "stopped", "action": "stopped", "files": files}


def _apply_ticket(repo, run_dir, run_id, files, fixture, config, spent_cents, run_cap_cents) -> str:
    """Return an error string, or empty when the ticket was applied."""
    section = (config.get("stages") or {}).get("conflicts") or {}
    cap = int(section.get("max_cents") if section.get("max_cents") is not None else 25)
    payload = fixture if isinstance(fixture, dict) else {}
    amount = int(payload.get("amount_cents") or 0)
    offered = payload.get("files") if isinstance(payload.get("files"), dict) else {}
    if amount < 0 or amount > cap or amount > max(0, run_cap_cents - spent_cents):
        return "conflict ticket exceeds the budget"
    if not offered or any(path not in files for path in offered) or set(offered) != set(files):
        return "conflict ticket must cover only the conflicted files"
    for path, content in offered.items():
        dest = repo / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(str(content), encoding="utf-8")
        _git(repo, "add", "--", path)
    report = {
        "ticket": "conflict",
        "status": "done",
        "model": _MODEL,
        "files_changed": sorted(offered),
        "scoped_to": sorted(files),
        "worktree": str(repo),
        "amount_cents": amount,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "impl-conflict.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if amount:
        Ledger(run_dir / "ledger.jsonl").append(
            LedgerEntry(
                run_id=run_id,
                stage="implement",
                ticket="conflict",
                amount_cents=amount,
                provider="anthropic",
                model=_MODEL,
                effort="low",
                note="conflict-ticket",
                budget_cents=cap,
            ),
            run_cap_cents=run_cap_cents,
        )
    return ""


def _recheck(run_dir: Path, verify, review) -> bool:
    if verify is None or review is None:
        return False
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "conflict-review.json").write_text('{"depth": "Quick"}\n', encoding="utf-8")
    return verify() == "succeeded" and review() == "succeeded"


def _draft(run_dir: Path, base: str, branch: str, files: list[str]) -> None:
    if (run_dir / "pr.json").is_file():
        return
    body = "WARNING: merge conflicts remain.\n" + "\n".join(f"- {name}" for name in files) + "\n"
    payload = {
        "base": base,
        "body": body,
        "branch": branch,
        "cost_cents": 0,
        "draft": True,
        "pr_url": None,
        "pushed": False,
        "title": "Pipeline change",
        "tool": "create_pull_request",
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "pr.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "pr-body.md").write_text(body, encoding="utf-8")


def _label(run_dir: Path, labels: list[str], timeline: list[dict]) -> None:
    if not admit_issue(labels=labels, timeline=timeline).allowed:
        return
    (run_dir / "conflict-labels.json").write_text(
        json.dumps({"add": ["pipeline:conflict"]}, indent=2) + "\n",
        encoding="utf-8",
    )


def _split_files(repo: Path, files: list[str], patterns: list[str]) -> tuple[list[str], list[str]]:
    trivial = []
    hard = []
    for path in files:
        if _match(path, patterns) or _whitespace(repo, path):
            trivial.append(path)
        else:
            hard.append(path)
    return trivial, hard


def _match(path: str, patterns: list[str]) -> bool:
    name = Path(path).name
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(path, pattern):
            return True
    return False


def _whitespace(repo: Path, path: str) -> bool:
    text = (repo / path).read_text(encoding="utf-8", errors="replace")
    if "<<<<<<<" not in text:
        return False
    hunks = text.split("<<<<<<<")
    for hunk in hunks[1:]:
        try:
            left, rest = hunk.split("=======", 1)
            right, _tail = rest.split(">>>>>>>", 1)
        except ValueError:
            return False
        if "".join(left.split()) != "".join(right.split()):
            return False
    return True


def _take_base(repo: Path, path: str) -> None:
    _git(repo, "checkout", "--ours", "--", path)
    _git(repo, "add", "--", path)


def _patterns(config: dict) -> list[str]:
    section = (config.get("stages") or {}).get("conflicts") or {}
    return list(section.get("trivial") or ["*lock*", "*.generated.*", "*generated*"])


def _payload(status, base, branch, files, mode, verified: bool = True) -> dict:
    return {
        "status": status,
        "base": base,
        "branch": branch,
        "files": files,
        "mode": mode,
        "verified": verified,
    }


def _save(run_dir: Path, payload: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "conflicts.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _recheck_marker_used(run_dir: Path) -> bool:
    return (run_dir / "conflict-review.json").is_file()


def _branch(run_id: str) -> str:
    return f"pipeline/{run_id}"


def _exists(repo: Path, branch: str) -> bool:
    if not (repo / ".git").exists():
        return False
    completed = _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    return completed.returncode == 0


def _behind(repo: Path, branch: str, base: str) -> bool:
    base_sha = _rev(repo, base)
    merged = _git(repo, "merge-base", branch, base)
    if merged.returncode != 0 or not base_sha:
        return False
    return merged.stdout.strip() != base_sha


def _rev(repo: Path, ref: str) -> str:
    completed = _git(repo, "rev-parse", ref)
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _unmerged(repo: Path) -> list[str]:
    completed = _git(repo, "diff", "--name-only", "--diff-filter=U")
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _rebasing(repo: Path) -> bool:
    git_dir = _git(repo, "rev-parse", "--git-path", "rebase-merge")
    if git_dir.returncode != 0:
        return False
    path = Path(git_dir.stdout.strip())
    if not path.is_absolute():
        path = repo / path
    return path.exists()


def _continue(repo: Path) -> subprocess.CompletedProcess[str]:
    env = {"GIT_EDITOR": "true"}
    result = _git(repo, "-c", "core.editor=true", "rebase", "--continue", env=env)
    text = result.stdout + result.stderr
    if result.returncode != 0 and "No changes" in text:
        return _git(repo, "rebase", "--skip")
    return result


def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    import os

    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        env=merged,
    )
