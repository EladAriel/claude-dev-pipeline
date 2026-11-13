import json
import subprocess
from pathlib import Path

from claude_dev_pipeline.harness import (
    allows_fast_lane,
    bootstrap_repo,
    discover,
    harness_files,
    mode_for,
    raise_depth,
)
from claude_dev_pipeline.stages.pr import render_pr
from claude_dev_pipeline.stages.retro import compute_metrics
from claude_dev_pipeline.stages.verify import execute_verify
from claude_dev_pipeline.summary import write_summary


def _git(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def test_python_and_go_repos_without_tests_are_undetected(tmp_path: Path) -> None:
    python_repo = tmp_path / "py"
    python_repo.mkdir()
    (python_repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    go_repo = tmp_path / "go"
    go_repo.mkdir()
    (go_repo / "go.mod").write_text("module example.com/app\n\ngo 1.22\n", encoding="utf-8")
    (go_repo / "main.go").write_text("package main\n", encoding="utf-8")
    assert discover(python_repo, {"checks": {"test": ""}})["found"] is False
    assert discover(go_repo, {})["language"] == "go"
    assert discover(go_repo, {})["found"] is False


def test_bootstrap_makes_both_languages_detectable(tmp_path: Path) -> None:
    for name, marker in (("py", "app.py"), ("go", "go.mod")):
        repo = tmp_path / name
        repo.mkdir()
        text = "module example.com/app\n" if name == "go" else "x = 1\n"
        (repo / marker).write_text(text, encoding="utf-8")
        if name == "go":
            (repo / "main.go").write_text("package main\n", encoding="utf-8")
        _git(repo)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
        config = {"stages": {"verification": {"mode": "bootstrap"}}}
        assert bootstrap_repo(repo, config) is True
        found = discover(repo, {})
        assert found["found"] is True
        names = [item["path"] for item in harness_files(found["language"], ["app"])]
        assert any(path.endswith("_test.go") or "test_" in path for path in names)


def test_degraded_is_opt_in_and_does_not_bootstrap(tmp_path: Path) -> None:
    assert mode_for({}, "M") == "bootstrap"
    assert mode_for({"stages": {"verification": {"mode": "degraded"}}}, "L") == "degraded"
    assert allows_fast_lane(False) is False
    assert raise_depth("Quick") == "Standard"
    repo = tmp_path / "py"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo)
    config = {"stages": {"verification": {"mode": "degraded"}}}
    assert bootstrap_repo(repo, config) is False
    assert not (repo / "tests" / "test_harness.py").exists()


def test_verify_does_not_pretend_green_without_a_test_command(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run = tmp_path / "run"
    run.mkdir()
    (run / "spec.json").write_text(
        json.dumps(
            {
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
                    {"id": "01", "title": "App", "acceptance_criteria": ["AC-1"], "blocked_by": []}
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
                "test_command": "",
                "test_result": {"exit_code": 0, "output": ""},
                "acceptance_tests": [{"id": "AC-1", "test": "tests/test_ok.py"}],
                "commit_sha": "abc",
            }
        ),
        encoding="utf-8",
    )
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
    (run / "review.json").write_text("{}\n", encoding="utf-8")
    result = execute_verify(
        run_dir=run,
        run_id="run-1",
        config={"checks": {"test": "", "lint": "true"}},
        fixture_path=None,
        spent_cents=0,
        run_cap_cents=100,
        stage_cap_cents=10,
    )
    assert result.status == "unverified"
    verify = json.loads((run / "verify.json").read_text(encoding="utf-8"))
    assert verify["verification"] == "unverified"
    write_summary(run, asked="ship it", stop_reason="success")
    assert "unverified" in (run / "summary.md").read_text(encoding="utf-8")
    _title, body, _cost = render_pr(run, issue_number=None, usage_cents=0)
    assert "WARNING" in body
    metrics = compute_metrics(run, {}, "success")
    assert metrics["verification"] == "unverified"
    assert metrics["verify_pass"] is False
