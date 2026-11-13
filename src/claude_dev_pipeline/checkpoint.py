"""Atomic run checkpoints. A crash can resume from the last completed stage."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def input_hash(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path).encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def load_state(run_dir: Path) -> dict:
    path = run_dir / "state.json"
    if not path.is_file():
        raise FileNotFoundError(f"no checkpoint at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("checkpoint must be an object")
    return data


def save_state(run_dir: Path, state: dict) -> None:
    ledger = run_dir / "ledger.jsonl"
    state["ledger_offset"] = ledger.stat().st_size if ledger.is_file() else 0
    state["version"] = 1
    atomic_write(run_dir / "state.json", json.dumps(state, indent=2, sort_keys=True) + "\n")


def fresh_state(
    *, run_id: str, inputs: str, fixtures: dict, target_repo: str, run_cap_cents: int
) -> dict:
    return {
        "branches": {},
        "completed_stages": [],
        "fixtures": fixtures,
        "halt_reason": "",
        "input_hash": inputs,
        "ledger_offset": 0,
        "run_cap_cents": run_cap_cents,
        "run_id": run_id,
        "target_repo": target_repo,
        "tickets": {},
        "version": 1,
    }


def mark_stage(state: dict, stage: str) -> None:
    if stage not in state["completed_stages"]:
        state["completed_stages"].append(stage)


def record_ticket(state: dict, ticket: str, report: dict) -> None:
    state["tickets"][ticket] = {
        "branch": str(report.get("branch") or ""),
        "head": str(report.get("commit_sha") or ""),
        "status": str(report.get("status") or ""),
        "worktree": str(report.get("worktree") or ""),
    }
    state["branches"][ticket] = state["tickets"][ticket]


def ticket_intact(record: dict) -> bool:
    worktree = Path(str(record.get("worktree") or ""))
    head = str(record.get("head") or "")
    branch = str(record.get("branch") or "")
    if not head or not worktree.is_dir():
        return False
    if _git(worktree, "rev-parse", "HEAD") != head:
        return False
    if _git(worktree, "status", "--porcelain"):
        return False
    if branch and _git(worktree, "rev-parse", "--verify", branch) == "":
        return False
    return True


def discard_ticket(record: dict) -> None:
    worktree = Path(str(record.get("worktree") or ""))
    branch = str(record.get("branch") or "")
    repo = _worktree_repo(worktree) if worktree.is_dir() else None
    if repo is not None and worktree.is_dir():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
    elif worktree.is_dir():
        subprocess.run(["rm", "-rf", str(worktree)], check=False)
    if repo is not None and branch:
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )


def _worktree_repo(worktree: Path) -> Path | None:
    git_file = worktree / ".git"
    if not git_file.is_file():
        return None
    line = git_file.read_text(encoding="utf-8").strip()
    if not line.startswith("gitdir:"):
        return None
    gitdir = Path(line.split(":", 1)[1].strip())
    # .git/worktrees/<name> -> repo root is three parents up from the git dir's common dir
    common = gitdir
    for _ in range(3):
        if (common / "config").is_file() and common.name == ".git":
            return common.parent
        common = common.parent
    return None


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()
