import json
import sqlite3
import subprocess
from pathlib import Path

from claude_dev_pipeline.flaky import parse_failures
from claude_dev_pipeline.stages.pr import render_pr
from claude_dev_pipeline.stages.retro import compute_metrics
from claude_dev_pipeline.stages.verify import execute_verify
from claude_dev_pipeline.summary import write_summary

_RUNNER = """\
import pathlib
import sys

state = pathlib.Path(".flake-count")
count = int(state.read_text()) if state.exists() else 0
state.write_text(str(count + 1))
log = pathlib.Path(".calls")
log.write_text((log.read_text() if log.exists() else "") + "\\n".join(sys.argv[1:]) + "\\n")
name = "tests/test_app.py::test_flaky"
target = sys.argv[1] if len(sys.argv) > 1 else ""
if target and target != name:
    raise SystemExit(0)
if count % 2 == 0:
    print(f"FAILED {name}")
    raise SystemExit(1)
raise SystemExit(0)
"""

_ALWAYS_FAIL = """\
print("FAILED tests/test_app.py::test_flaky")
raise SystemExit(1)
"""

_ALWAYS_PASS = "raise SystemExit(0)\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def _layout(tmp_path: Path, repo: Path) -> Path:
    run = tmp_path / "runs" / "run-1"
    run.mkdir(parents=True)
    (run / "spec.json").write_text(
        json.dumps(
            {
                "title": "App",
                "acceptance_criteria": [{"id": "AC-1", "statement": "Works."}],
                "open_questions": [],
                "needs_human": False,
                "seams": ["app"],
                "e2e_scenarios": [{"id": "E2E-1", "scenario": "Run it."}],
            }
        ),
        encoding="utf-8",
    )
    (run / "plan.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "01",
                        "title": "App",
                        "acceptance_criteria": ["AC-1"],
                        "blocked_by": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run / "impl-01.json").write_text(
        json.dumps(
            {
                "ticket": "01",
                "status": "done",
                "worktree": str(repo),
                "tests_added": ["tests/test_app.py"],
                "test_command": "python3 runner.py",
                "test_result": {"exit_code": 0, "output": ""},
                "acceptance_tests": [{"id": "AC-1", "test": "tests/test_app.py"}],
                "commit_sha": "abc",
            }
        ),
        encoding="utf-8",
    )
    (run / "review.json").write_text("{}\n", encoding="utf-8")
    return run


def _config(**overrides) -> dict:
    flaky = {"enabled": True, "retries": 1, "max_seconds": 20, "max_cents": 0}
    flaky.update(overrides)
    return {
        "checks": {"test": "python3 runner.py", "lint": "", "typecheck": "", "build": ""},
        "checks_timeout_seconds": 5,
        "stages": {"flaky": flaky},
    }


def test_parsers_cover_pytest_and_go() -> None:
    output = "FAILED tests/test_app.py::test_flaky\n--- FAIL: TestAdd\n"
    assert parse_failures(output) == ["tests/test_app.py::test_flaky", "TestAdd"]


def test_preexisting_flake_is_quarantined_and_does_not_block(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    _commit(repo, {"runner.py": _RUNNER, "app.py": "x = 1\n"}, "base")
    _commit(repo, {"app.py": "x = 2\n"}, "change")
    before = (repo / "runner.py").read_bytes()
    run = _layout(tmp_path, repo)
    missing = tmp_path / "missing-verify.json"
    result = execute_verify(
        run_dir=run,
        run_id="run-1",
        config=_config(),
        fixture_path=missing,
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=10,
    )
    saved = json.loads((run / "verify.json").read_text(encoding="utf-8"))
    assert result.status == "pass"
    assert result.model_called is False
    assert not missing.exists()
    assert saved["flaky"][0]["kind"] == "preexisting-flaky"
    assert saved["flaky"][0]["blocking"] is False
    assert (repo / "runner.py").read_bytes() == before
    listed = json.loads((tmp_path / "runs" / "quarantine.json").read_text(encoding="utf-8"))
    assert listed["tests"][0]["flake_count"] == 1
    execute_verify(
        run_dir=run,
        run_id="run-2",
        config=_config(),
        fixture_path=missing,
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=10,
    )
    again = json.loads((tmp_path / "runs" / "quarantine.json").read_text(encoding="utf-8"))
    assert again["tests"][0]["flake_count"] == 2
    conn = sqlite3.connect(tmp_path / "runs" / "pipeline.db")
    row = conn.execute("SELECT flake_count FROM quarantine").fetchone()
    assert row[0] == 2
    write_summary(run, asked="ship it", stop_reason="success")
    assert "quarantined" in (run / "summary.md").read_text(encoding="utf-8")
    _title, body, _cost = render_pr(run, issue_number=None, usage_cents=0)
    assert "quarantined" in body
    metrics = compute_metrics(run, {}, "success")
    assert metrics["flaky_tests"] == 1


def test_introduced_failure_still_blocks_and_only_summarizes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    _commit(repo, {"runner.py": _ALWAYS_PASS, "app.py": "x = 1\n"}, "base")
    _commit(repo, {"runner.py": _ALWAYS_FAIL}, "change")
    before = (repo / "runner.py").read_bytes()
    run = _layout(tmp_path, repo)
    fixture = tmp_path / "verify-fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "summary": {"failure_summary": "Introduced.", "suggested_fix_ticket": "01"},
                "usage": {"amount_cents": 2, "output_tokens": 4},
            }
        ),
        encoding="utf-8",
    )
    result = execute_verify(
        run_dir=run,
        run_id="run-1",
        config=_config(),
        fixture_path=fixture,
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=10,
    )
    saved = json.loads((run / "verify.json").read_text(encoding="utf-8"))
    assert result.status == "fail"
    assert result.model_called is True
    assert saved["classified"][0]["kind"] == "introduced-failure"
    assert saved["tickets"][0]["failure_summary"].startswith("Introduced.")
    assert (repo / "runner.py").read_bytes() == before
    assert not (tmp_path / "runs" / "quarantine.json").exists()


def test_kill_switch_and_time_cap_do_not_retry(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    _commit(repo, {"runner.py": _RUNNER, "app.py": "x = 1\n"}, "base")
    _commit(repo, {"app.py": "x = 2\n"}, "change")
    run = _layout(tmp_path, repo)
    fixture = tmp_path / "verify-fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "summary": {"failure_summary": "Stopped.", "suggested_fix_ticket": "01"},
                "usage": {"amount_cents": 1},
            }
        ),
        encoding="utf-8",
    )
    execute_verify(
        run_dir=run,
        run_id="run-1",
        config=_config(enabled=False),
        fixture_path=fixture,
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=10,
    )
    calls = (repo / ".calls").read_text(encoding="utf-8").splitlines()
    assert calls == [""]
    (repo / ".calls").write_text("", encoding="utf-8")
    (repo / ".flake-count").unlink()
    execute_verify(
        run_dir=run,
        run_id="run-1",
        config=_config(max_seconds=0, retries=3),
        fixture_path=fixture,
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=10,
    )
    capped = [line for line in (repo / ".calls").read_text(encoding="utf-8").splitlines() if line]
    assert capped == []
