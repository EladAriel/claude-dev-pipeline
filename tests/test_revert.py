import json
import sqlite3
import subprocess
from pathlib import Path

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.revert_run import perform


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _repo(path: Path) -> None:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")


def test_revert_opens_a_branch_and_leaves_main(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _repo(repo)
    (repo / "app.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "app.txt")
    _git(repo, "commit", "-m", "base")
    (repo / "app.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "commit", "-am", "change")
    sha = _git(repo, "rev-parse", "HEAD")
    main_sha = sha
    report = perform(
        repo,
        sha,
        "run1",
        "The change broke the parser.",
        tmp_path / "runs",
        verify_command="true",
        original="https://example.test/pull/4",
    )
    assert report["status"] == "ready"
    assert report["draft"] is False
    assert report["pushed"] is False
    assert _git(repo, "rev-parse", "main") == main_sha
    branch = _git(repo, "rev-parse", "pipeline/revert-run1")
    assert branch != main_sha
    saved = json.loads((tmp_path / "runs" / "run1" / "retro.json").read_text(encoding="utf-8"))
    assert saved["applied"] is False
    assert saved["items"][0]["proposed_change"] == "The change broke the parser."
    pr = json.loads((tmp_path / "runs" / "run1" / "pr.json").read_text(encoding="utf-8"))
    assert pr["draft"] is False
    assert pr["base"] == "main"
    conn = sqlite3.connect(tmp_path / "runs" / "reverts.sqlite")
    assert conn.execute("SELECT reason FROM reverts").fetchone()[0].startswith("The change")


def test_conflict_stops_without_moving_main(tmp_path: Path, repo_root) -> None:
    repo = tmp_path / "repo"
    _repo(repo)
    (repo / "app.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "app.txt")
    _git(repo, "commit", "-m", "base")
    (repo / "app.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "commit", "-am", "middle")
    middle = _git(repo, "rev-parse", "HEAD")
    (repo / "app.txt").write_text("three\n", encoding="utf-8")
    _git(repo, "commit", "-am", "later")
    main_sha = _git(repo, "rev-parse", "main")
    code = main(
        [
            "revert",
            middle,
            "--target-repo",
            str(repo),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--reason",
            "Conflict.",
            "--config",
            str(repo_root / "config/pipeline.config.example.yaml"),
        ]
    )
    assert code == 1
    assert _git(repo, "rev-parse", "main") == main_sha
    saved = tmp_path / "runs" / middle[:12] / "revert.json"
    report = json.loads(saved.read_text(encoding="utf-8"))
    assert report["status"] == "stopped"
    assert "app.txt" in report["files"]
