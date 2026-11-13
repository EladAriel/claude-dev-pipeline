import json
import subprocess
from pathlib import Path

import yaml

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.config import load_yaml
from claude_dev_pipeline.stages.verify import VerifyError, execute_verify


def _repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    (path / "tests").mkdir()
    (path / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "tests"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=path, check=True, capture_output=True)
    return path


def _layout(tmp_path: Path, repo: Path, *, criteria: list[str] | None = None) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    ids = criteria or ["AC-1"]
    (run / "spec.json").write_text(
        json.dumps(
            {
                "acceptance_criteria": [{"id": item, "statement": item} for item in ids],
                "open_questions": [],
                "needs_human": False,
                "seams": ["parser.parse"],
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
                        "title": "Parse",
                        "acceptance_criteria": ids,
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
                "tests_added": ["tests/test_ok.py"],
                "test_command": 'python3 -c "print(1)"',
                "test_result": {"exit_code": 0, "output": "1"},
                "acceptance_tests": [{"id": item, "test": "tests/test_ok.py"} for item in ids],
                "commit_sha": "abc",
            }
        ),
        encoding="utf-8",
    )
    return run


def _config(command: str, timeout: float = 5) -> dict:
    return {
        "checks": {"test": command, "lint": "", "typecheck": "", "build": ""},
        "checks_timeout_seconds": timeout,
    }


def test_all_green_does_not_open_a_model_fixture(tmp_path):
    repo = _repo(tmp_path / "target")
    run = _layout(tmp_path, repo)
    missing = tmp_path / "missing-verify.json"
    result = execute_verify(
        run_dir=run,
        run_id="run-verify",
        config=_config("python3 -c \"print('green')\""),
        fixture_path=missing,
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=10,
    )
    saved = json.loads((run / "verify.json").read_text(encoding="utf-8"))
    assert result.model_called is False
    assert result.amount_cents == 0
    assert saved["status"] == "pass"
    assert saved["tickets"][0]["passed"] is True
    assert saved["tickets"][0]["commands"][0]["output"].strip() == "green"
    assert not missing.exists()
    ledger = (run / "ledger.jsonl").read_text(encoding="utf-8")
    assert "commands-only" in ledger
    assert '"model": "none"' in ledger
    assert '"model_called": false' in ledger


def test_failing_command_uses_the_fixture_and_stops(tmp_path):
    repo = _repo(tmp_path / "target")
    run = _layout(tmp_path, repo)
    fixture = tmp_path / "verify.json"
    fixture.write_text(
        json.dumps(
            {
                "summary": {
                    "failure_summary": "The test command exited 1.",
                    "suggested_fix_ticket": "01",
                },
                "usage": {"amount_cents": 2, "output_tokens": 8},
            }
        ),
        encoding="utf-8",
    )
    result = execute_verify(
        run_dir=run,
        run_id="run-verify",
        config=_config('python3 -c "import sys; sys.exit(1)"'),
        fixture_path=fixture,
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=10,
    )
    saved = json.loads((run / "verify.json").read_text(encoding="utf-8"))
    assert result.status == "fail"
    assert result.model_called is True
    assert result.amount_cents == 2
    summary = saved["tickets"][0]["failure_summary"]
    assert summary.startswith("The test command exited 1.")
    assert "AC-1" in saved["coverage_gaps"]
    assert saved["tickets"][0]["suggested_fix_ticket"] == "01"
    assert "fixture" in (run / "ledger.jsonl").read_text(encoding="utf-8")


def test_timeout_and_coverage_gap(tmp_path):
    repo = _repo(tmp_path / "target")
    run = _layout(tmp_path, repo, criteria=["AC-1", "AC-2"])
    report = json.loads((run / "impl-01.json").read_text(encoding="utf-8"))
    report["acceptance_tests"] = [{"id": "AC-1", "test": "tests/test_ok.py"}]
    (run / "impl-01.json").write_text(json.dumps(report), encoding="utf-8")
    fixture = tmp_path / "verify.json"
    fixture.write_text(
        json.dumps(
            {
                "summary": {"failure_summary": "Timed out.", "suggested_fix_ticket": "01"},
                "usage": {"amount_cents": 1},
            }
        ),
        encoding="utf-8",
    )
    result = execute_verify(
        run_dir=run,
        run_id="run-verify",
        config=_config('python3 -c "import time; time.sleep(5)"', timeout=0.2),
        fixture_path=fixture,
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=10,
    )
    saved = json.loads((run / "verify.json").read_text(encoding="utf-8"))
    assert result.status == "fail"
    assert saved["tickets"][0]["commands"][0]["timed_out"] is True
    assert "AC-2" in saved["coverage_gaps"]


def test_command_cannot_leave_the_worktree(tmp_path):
    repo = _repo(tmp_path / "target")
    run = _layout(tmp_path, repo)
    try:
        execute_verify(
            run_dir=run,
            run_id="run-verify",
            config=_config("cat /etc/hostname"),
            fixture_path=None,
            spent_cents=0,
            run_cap_cents=2000,
            stage_cap_cents=10,
        )
    except VerifyError as exc:
        assert "worktree" in str(exc)
    else:
        raise AssertionError("absolute command was allowed")


def test_dry_run_shows_verify_and_disabled_skips_commands(tmp_path, capsys, repo_root):
    assert (
        main(["--dry-run", "--config", str(repo_root / "config/pipeline.config.example.yaml")]) == 0
    )
    dry = capsys.readouterr().out
    assert "key=verify-evidence" in dry
    assert "claude-haiku-5.5" in dry
    assert "effort=low" in dry
    assert "model only when a command fails" in dry

    config = load_yaml(repo_root / "config/pipeline.config.example.yaml")
    config["stages"]["verify"] = {"enabled": False}
    config["checks"] = {
        "test": 'python3 -c "import sys; sys.exit(9)"',
        "lint": "",
        "typecheck": "",
        "build": "",
    }
    config_path = tmp_path / "pipeline.config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    repo = _repo(tmp_path / "target")
    code = main(
        [
            "run",
            "--issue",
            "12",
            "--issue-snapshot",
            str(_issue(tmp_path)),
            "--model-fixture",
            str(_write(tmp_path / "triage.json", _triage())),
            "--spec-fixture",
            str(_write(tmp_path / "spec.json", _spec_fixture())),
            "--plan-fixture",
            str(_write(tmp_path / "plan.json", _plan_fixture())),
            "--implement-fixture",
            str(_write(tmp_path / "implement.json", _implement_fixture())),
            "--verify-fixture",
            str(tmp_path / "missing-verify.json"),
            "--target-repo",
            str(repo),
            "--config",
            str(config_path),
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
    assert code == 0
    assert "verify: disabled" in capsys.readouterr().out
    assert not any((tmp_path / "runs").rglob("verify.json"))


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _issue(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "issue.json",
        {
            "number": 12,
            "title": "Parser",
            "body": "Quotes.",
            "labels": ["agent:approved"],
            "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
        },
    )


def _triage() -> dict:
    return {
        "artifact": {
            "classify": "bug",
            "category": "bug",
            "state": "ready-for-agent",
            "risk_tier": "Quick",
            "decision": "route",
            "needs_human": False,
            "confidence": 90,
            "injection_suspected": False,
            "summary": "Parser bug.",
        },
        "brief": "AI draft: not posted.\n",
        "usage": {"amount_cents": 1},
    }


def _spec_fixture() -> dict:
    return {
        "artifact": {
            "acceptance_criteria": [{"id": "AC-1", "statement": "Quotes round-trip."}],
            "open_questions": [],
            "needs_human": False,
            "seams": ["parser.parse"],
            "e2e_scenarios": [{"id": "E2E-1", "scenario": "Run it."}],
        },
        "spec_md": "# Spec\n\nAC-1: Quotes round-trip.\n",
        "usage": {"amount_cents": 10},
    }


def _plan_fixture() -> dict:
    return {
        "artifact": {
            "tasks": [
                {
                    "id": "01",
                    "title": "Parse",
                    "acceptance_criteria": ["AC-1"],
                    "files": ["src/parser.py"],
                    "seams": ["parser.parse"],
                    "test_plan": "Cover AC-1.",
                    "blocked_by": [],
                    "estimate": "S",
                    "suggested_model": "claude-sonnet-5.5",
                    "body": "Cover AC-1.",
                }
            ]
        },
        "usage": {"amount_cents": 10},
    }


def _implement_fixture() -> dict:
    return {
        "tickets": {
            "01": {
                "status": "done",
                "files": [
                    {"path": "src/parser.py", "content": "def parse():\n    return 1\n"},
                    {"path": "tests/test_ok.py", "content": "def test_ok():\n    assert True\n"},
                ],
                "tests_added": ["tests/test_ok.py"],
                "test_command": 'python3 -c "print(1)"',
                "test_result": {"exit_code": 0, "output": "1"},
                "usage": {"amount_cents": 10},
            }
        }
    }
