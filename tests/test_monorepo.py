import json
import subprocess
from pathlib import Path

from claude_dev_pipeline.monorepo import (
    affected,
    nearest,
    note,
    select_files,
    suite_scope,
    workspaces,
)
from claude_dev_pipeline.stages.verify import execute_verify


def _git(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)


def test_workspaces_affected_scope_and_cap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "packages" / "web").mkdir(parents=True)
    (repo / "packages" / "api").mkdir(parents=True)
    (repo / "package.json").write_text(
        json.dumps({"workspaces": ["packages/*"]}),
        encoding="utf-8",
    )
    (repo / "go.work").write_text("go 1.22\n\nuse ./packages/api\n", encoding="utf-8")
    (repo / "crates" / "core").mkdir(parents=True)
    (repo / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/core"]\n',
        encoding="utf-8",
    )
    found = workspaces(repo, {})
    assert "packages/web" in found
    assert "packages/api" in found
    assert "crates/core" in found
    assert affected(["packages/web/src/app.ts", "README.md"], found, repo) == ["packages/web"]
    (repo / "services" / "billing").mkdir(parents=True)
    manifest = repo / "services" / "billing" / "go.mod"
    manifest.write_text("module example.com/billing\n", encoding="utf-8")
    assert nearest(repo, "services/billing/main.go") == "services/billing"
    files = [f"f{index}.txt" for index in range(5)]
    assert select_files(files, {"stages": {"monorepo": {"max_files": 2, "max_tokens": 100}}}) == [
        "f0.txt",
        "f1.txt",
    ]
    assert suite_scope("M", {}) == "affected"
    assert suite_scope("L", {}) == "full"
    assert workspaces(repo, {"stages": {"monorepo": {"enabled": False}}}) == []


def test_affected_tests_and_sparse_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "packages" / "web").mkdir(parents=True)
    (repo / "packages" / "web" / "keep.txt").write_text("keep\n", encoding="utf-8")
    (repo / "package.json").write_text(
        json.dumps({"workspaces": ["packages/*"]}),
        encoding="utf-8",
    )
    _git(repo)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    run = tmp_path / "run"
    report = note(
        run,
        repo,
        ["packages/web/keep.txt"],
        "M",
        {"stages": {"monorepo": {"sparse": True, "max_files": 10, "max_tokens": 1000}}},
    )
    assert report["packages"] == ["packages/web"]
    assert report["test_scope"] == "affected"
    saved = json.loads((run / "monorepo.json").read_text(encoding="utf-8"))
    assert saved["sparse"] is True
    (run / "spec.json").write_text(
        json.dumps(
            {
                "acceptance_criteria": [{"id": "AC-1", "statement": "Ok."}],
                "open_questions": [],
                "needs_human": False,
                "seams": ["app"],
                "e2e_scenarios": [{"id": "E2E-1", "scenario": "Run."}],
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
                "tests_added": ["packages/web/keep.txt"],
                "acceptance_tests": [{"id": "AC-1", "test": "packages/web/keep.txt"}],
                "commit_sha": "abc",
            }
        ),
        encoding="utf-8",
    )
    result = execute_verify(
        run_dir=run,
        run_id="run-1",
        config={"checks": {"test": "true"}},
        fixture_path=None,
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=10,
    )
    saved_verify = json.loads((run / "verify.json").read_text(encoding="utf-8"))
    assert result.status == "pass"
    assert "packages/web" in saved_verify["tickets"][0]["commands"][0]["command"]
