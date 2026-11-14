"""Open a revert pull request. Never update or merge the base branch."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path

_SHA = re.compile(r"^[0-9a-f]{7,40}$")


def enabled(config: dict) -> bool:
    return ((config or {}).get("stages") or {}).get("revert", {}).get("enabled", True) is not False


def perform(
    repo: Path,
    sha: str,
    run_id: str,
    reason: str,
    runs_dir: Path,
    *,
    verify_command: str = "",
    original: str = "",
) -> dict:
    if not _SHA.match(sha):
        return {"status": "stopped", "reason": "missing commit"}
    base = _rev(repo, "main") or _rev(repo, "HEAD")
    branch = f"pipeline/revert-{run_id}"
    previous = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    created = _git(repo, "checkout", "-b", branch)
    if created.returncode != 0:
        _git(repo, "checkout", branch)
    reverted = _git(repo, "revert", "--no-edit", sha)
    if reverted.returncode != 0:
        files = _unmerged(repo)
        _git(repo, "revert", "--abort")
        if previous and previous != "HEAD":
            _git(repo, "checkout", previous)
        report = {
            "status": "stopped",
            "reason": "conflict",
            "files": files,
            "branch": branch,
            "sha": sha,
        }
        _write(runs_dir / run_id, report, reason, original)
        _restore_base(repo, base)
        return report
    verified = _verify(repo, verify_command)
    report = {
        "status": "ready" if verified else "verify-failed",
        "reason": reason,
        "branch": branch,
        "sha": sha,
        "original": original,
        "base": "main",
        "draft": False,
        "pushed": False,
        "verified": verified,
    }
    _write(runs_dir / run_id, report, reason, original)
    _pr(runs_dir / run_id, report)
    _sqlite(runs_dir / "reverts.sqlite", run_id, sha, branch, reason)
    if previous and previous != "HEAD":
        _git(repo, "checkout", previous)
    _restore_base(repo, base)
    return report


def _write(run_dir: Path, report: dict, reason: str, original: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "revert.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lesson = {
        "applied": False,
        "items": [
            {
                "id": "revert-lesson",
                "category": "revert",
                "severity": "high",
                "evidence": original or report.get("sha", ""),
                "proposed_change": reason or "Record why this change was reverted.",
                "target": "retro",
                "expected_metric": "reverted",
                "status": "proposed",
                "applied": False,
            }
        ],
    }
    (run_dir / "retro.json").write_text(
        json.dumps(lesson, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _pr(run_dir: Path, report: dict) -> None:
    body = [
        f"Revert {report.get('sha')}.",
        "",
        f"Original: {report.get('original') or 'unknown'}",
        "",
        "The pipeline does not merge this pull request.",
        "",
    ]
    payload = {
        "base": "main",
        "body": "\n".join(body),
        "branch": report.get("branch"),
        "draft": False,
        "pr_url": None,
        "pushed": False,
        "title": f"Revert {report.get('sha', '')[:7]}",
        "tool": "create_pull_request",
    }
    (run_dir / "pr.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _verify(repo: Path, command: str) -> bool:
    if not command:
        return True
    completed = subprocess.run(
        command.split(),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def _sqlite(path: Path, run_id: str, sha: str, branch: str, reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reverts (
              run_id TEXT PRIMARY KEY,
              sha TEXT,
              branch TEXT,
              reason TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO reverts (run_id, sha, branch, reason)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
              sha = excluded.sha,
              branch = excluded.branch,
              reason = excluded.reason
            """,
            (run_id, sha, branch, reason),
        )
        conn.commit()
    finally:
        conn.close()


def _unmerged(repo: Path) -> list[str]:
    completed = _git(repo, "diff", "--name-only", "--diff-filter=U")
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _restore_base(repo: Path, sha: str) -> None:
    current = _rev(repo, "main")
    if current and sha and current != sha:
        raise RuntimeError("the base branch moved during revert")


def _rev(repo: Path, ref: str) -> str:
    completed = _git(repo, "rev-parse", "--verify", ref)
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    import os

    env = os.environ.copy()
    env["GIT_EDITOR"] = "true"
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
