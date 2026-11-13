import json
import subprocess
from pathlib import Path

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.stages.debug import DebugSkipped, execute_debug


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    _git(path, "config", "user.email", "dev@example.com")
    _git(path, "config", "user.name", "Dev")
    (path / "README.md").write_text("base\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "base")
    return path


def _fixture(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "diagnosis": {
                    "reproduce": "Run the parser on a quoted string.",
                    "hypotheses": ["The quote is stripped before the token is stored."],
                    "root_cause": "The lexer drops quote characters.",
                    "evidence": "scratch/test_repro.py fails on a quoted token.",
                    "confidence": 80,
                    "suggested_fix": "Keep the quote characters on the token.",
                },
                "failing_test": {
                    "path": "test_repro.py",
                    "content": "def test_quotes():\n    assert False\n",
                },
                "usage": {"amount_cents": 2},
            }
        ),
        encoding="utf-8",
    )


def test_debug_stays_in_the_scratch_worktree(tmp_path, repo_root, capsys):
    repo = _repo(tmp_path / "target")
    before = _git(repo, "rev-parse", "HEAD")
    fixture = tmp_path / "debug.json"
    _fixture(fixture)
    code = main(
        [
            "debug",
            "Quotes disappear",
            "--fixture",
            str(fixture),
            "--target-repo",
            str(repo),
            "--fix",
            "--config",
            str(repo_root / "config/pipeline.config.example.yaml"),
            "--models",
            str(repo_root / "config/models.yaml"),
            "--budgets",
            str(repo_root / "config/budgets.yaml"),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--db",
            str(tmp_path / "pipeline.db"),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "push: none" in captured.out
    assert "pr: none" in captured.out
    assert "The lexer drops quote characters." in captured.out
    assert _git(repo, "rev-parse", "HEAD") == before
    assert "test_repro.py" not in _git(repo, "status", "--short")
    run = next((tmp_path / "runs").iterdir())
    assert (run / "scratch" / "test_repro.py").is_file()
    plan = json.loads((run / "plan.json").read_text(encoding="utf-8"))
    assert plan["tasks"][0]["files"] == ["test_repro.py"]
    assert (run / "triage.json").is_file()


def test_disabled_debug_does_not_open_the_fixture(tmp_path):
    missing = tmp_path / "missing.json"
    try:
        execute_debug(
            run_dir=tmp_path / "run",
            run_id="run-debug",
            description="unused",
            fixture_path=missing,
            target_repo=None,
            spent_cents=0,
            run_cap_cents=2000,
            stage_cap_cents=50,
            enabled=False,
        )
    except DebugSkipped:
        pass
    else:
        raise AssertionError("disabled debug opened the fixture")
    assert not missing.exists()
