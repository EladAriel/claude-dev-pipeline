import json
import subprocess
from pathlib import Path

from claude_dev_pipeline.conflicts import fast_forward_ok, settle
from claude_dev_pipeline.summary import write_summary


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "branch", "-M", "main")


def _commit(repo: Path, name: str, text: str, message: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _git(repo, "add", "--", name)
    _git(repo, "commit", "-m", message)


def _conflict(repo: Path, name: str, base: str, feature: str, upstream: str) -> str:
    _commit(repo, name, base, "base")
    _git(repo, "checkout", "-b", "pipeline/run-1")
    _commit(repo, name, feature, "feature")
    _git(repo, "checkout", "main")
    _commit(repo, name, upstream, "upstream")
    _git(repo, "checkout", "pipeline/run-1")
    return _git(repo, "rev-parse", "main")


def _config(**overrides) -> dict:
    section = {"enabled": True, "max_cents": 25, "trivial": ["*lock*", "*.generated.*"]}
    section.update(overrides)
    return {"stages": {"conflicts": section}}


def test_lockfile_conflict_uses_the_base_copy_and_is_idempotent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    main_sha = _conflict(repo, "package-lock.json", "base\n", "feature\n", "upstream\n")
    calls: list[str] = []
    run = tmp_path / "runs" / "run-1"
    first = settle(
        repo=repo,
        run_id="run-1",
        base="main",
        run_dir=run,
        config=_config(),
        verify=lambda: calls.append("verify") or "succeeded",
        review=lambda: calls.append("review") or "succeeded",
    )
    assert first["status"] == "resolved"
    assert (repo / "package-lock.json").read_text(encoding="utf-8") == "upstream\n"
    assert _git(repo, "rev-parse", "main") == main_sha
    assert calls == ["verify", "review"]
    assert (run / "conflict-review.json").is_file()
    second = settle(
        repo=repo,
        run_id="run-1",
        base="main",
        run_dir=run,
        config=_config(),
        verify=lambda: calls.append("verify") or "succeeded",
        review=lambda: calls.append("review") or "succeeded",
    )
    assert second["action"] == "cached"
    assert calls == ["verify", "review"]


def test_code_conflict_is_a_sonnet_ticket_scoped_to_the_hunk(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    main_sha = _conflict(repo, "app.py", "base\n", "feature\n", "upstream\n")
    run = tmp_path / "runs" / "run-1"
    outcome = settle(
        repo=repo,
        run_id="run-1",
        base="main",
        run_dir=run,
        config=_config(),
        fixture={"amount_cents": 4, "files": {"app.py": "resolved\n"}},
        verify=lambda: "succeeded",
        review=lambda: "succeeded",
    )
    assert outcome["status"] == "resolved"
    assert (repo / "app.py").read_text(encoding="utf-8") == "resolved\n"
    report = json.loads((run / "impl-conflict.json").read_text(encoding="utf-8"))
    assert report["model"] == "claude-sonnet-5.5"
    assert report["files_changed"] == ["app.py"]
    assert _git(repo, "rev-parse", "main") == main_sha
    refused = settle(
        repo=tmp_path / "other",
        run_id="run-1",
        base="main",
        run_dir=tmp_path / "runs" / "other",
        config=_config(),
    )
    assert refused["action"] == "absent"


def test_extra_file_or_budget_stops_without_moving_main(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    main_sha = _conflict(repo, "app.py", "base\n", "feature\n", "upstream\n")
    feature = _git(repo, "rev-parse", "pipeline/run-1")
    run = tmp_path / "runs" / "run-1"
    outcome = settle(
        repo=repo,
        run_id="run-1",
        base="main",
        run_dir=run,
        config=_config(max_cents=1),
        fixture={"amount_cents": 5, "files": {"app.py": "resolved\n", "extra.py": "no\n"}},
        labels=["agent:approved"],
        timeline=[{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
        verify=lambda: "succeeded",
        review=lambda: "succeeded",
    )
    assert outcome["status"] == "stopped"
    assert "app.py" in outcome["files"]
    assert _git(repo, "rev-parse", "main") == main_sha
    assert _git(repo, "rev-parse", "pipeline/run-1") == feature
    assert not (run / "impl-conflict.json").exists()
    assert "pipeline:conflict" in (run / "conflict-labels.json").read_text(encoding="utf-8")
    body = json.loads((run / "pr.json").read_text(encoding="utf-8"))
    assert body["draft"] is True
    assert body["pushed"] is False
    write_summary(run, asked="ship it", stop_reason="conflict")
    assert "app.py" in (run / "summary.md").read_text(encoding="utf-8")
    again = settle(
        repo=repo,
        run_id="run-1",
        base="main",
        run_dir=run,
        config=_config(max_cents=1),
        fixture={"amount_cents": 5, "files": {"app.py": "resolved\n"}},
        verify=lambda: "succeeded",
        review=lambda: "succeeded",
    )
    assert again["action"] == "cached"
    assert _git(repo, "rev-parse", "pipeline/run-1") == feature


def test_whitespace_resolves_and_a_foreign_tip_is_not_force_pushed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    _conflict(repo, "notes.txt", "alpha\n", "alpha \n", "alpha  \n")
    run = tmp_path / "runs" / "run-1"
    outcome = settle(
        repo=repo,
        run_id="run-1",
        base="main",
        run_dir=run,
        config=_config(trivial=[]),
        verify=lambda: "succeeded",
        review=lambda: "succeeded",
    )
    assert outcome["status"] == "resolved"
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "alpha  \n"
    _git(repo, "checkout", "main")
    _commit(repo, "other.txt", "foreign\n", "foreign")
    foreign = _git(repo, "rev-parse", "main")
    assert fast_forward_ok(repo, "pipeline/run-1", foreign) is False
    assert fast_forward_ok(repo, "main", foreign) is True


def test_kill_switch_does_not_rebase(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    _conflict(repo, "app.py", "base\n", "feature\n", "upstream\n")
    feature = _git(repo, "rev-parse", "pipeline/run-1")
    outcome = settle(
        repo=repo,
        run_id="run-1",
        base="main",
        run_dir=tmp_path / "runs" / "run-1",
        config=_config(enabled=False),
        verify=lambda: "succeeded",
        review=lambda: "succeeded",
    )
    assert outcome["action"] == "disabled"
    assert _git(repo, "rev-parse", "pipeline/run-1") == feature
