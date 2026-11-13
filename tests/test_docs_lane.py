import json
from pathlib import Path

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.docs_lane import kind_of, leaves_docs, route
from claude_dev_pipeline.stages.verify import execute_verify


def test_docs_paths_route_and_runtime_paths_do_not() -> None:
    docs = {"title": "Docs", "body": "Update docs/README.md and mkdocs.yml."}
    code = {"title": "Code", "body": "Change src/app.py and docs/README.md."}
    workflow = {"title": "CI", "body": "Edit .github/workflows/ci.yml."}
    manifest = {"title": "Deps", "body": "Bump package.json."}
    vague = {"title": "Tidy", "body": "Clean this up."}
    assert route(docs, {})["lane"] == "docs"
    assert route(code, {}) is None
    assert route(workflow, {}) is None
    assert route(manifest, {}) is None
    assert route(vague, {}) is None
    assert kind_of(".github/workflows/ci.yml", {}) == "runtime"
    assert kind_of("src/app.py", {}) == "code"
    assert leaves_docs(["docs/README.md", "src/app.py"], {}) is True
    assert route(docs, {"stages": {"docs_lane": {"enabled": False}}}) is None


def test_docs_lane_skips_spec_and_a_workflow_does_not(tmp_path: Path, repo_root, capsys) -> None:
    issue = tmp_path / "issue.json"
    issue.write_text(
        json.dumps(
            {
                "number": 30,
                "title": "Refresh the install doc",
                "body": "Update docs/README.md so the install steps stay accurate.",
                "labels": ["agent:approved"],
                "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
            }
        ),
        encoding="utf-8",
    )
    fixture = tmp_path / "triage.json"
    fixture.write_text(
        json.dumps(
            {
                "artifact": {
                    "classify": "enhancement",
                    "category": "enhancement",
                    "state": "ready-for-agent",
                    "risk_tier": "Standard",
                    "decision": "route",
                    "needs_human": False,
                    "confidence": 90,
                    "injection_suspected": False,
                    "summary": "Docs only.",
                    "tier": "M",
                },
                "brief": "Update the doc.",
                "usage": {"amount_cents": 1},
            }
        ),
        encoding="utf-8",
    )
    missing = tmp_path / "missing-spec.json"
    code = main(
        [
            "run",
            "--issue",
            "30",
            "--issue-snapshot",
            str(issue),
            "--model-fixture",
            str(fixture),
            "--spec-fixture",
            str(missing),
            "--config",
            str(repo_root / "config/pipeline.config.example.yaml"),
            "--models",
            str(repo_root / "config/models.yaml"),
            "--budgets",
            str(repo_root / "config/budgets.yaml"),
            "--tiers",
            str(repo_root / "config/tiers.yaml"),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--db",
            str(tmp_path / "pipeline.db"),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "docs-lane: skipped spec and plan" in out
    assert not missing.exists()
    saved = json.loads((next((tmp_path / "runs").glob("*/docs.json"))).read_text(encoding="utf-8"))
    assert saved["lane"] == "docs"
    assert saved["budget_cents"] == 200

    workflow = tmp_path / "workflow.json"
    workflow.write_text(
        json.dumps(
            {
                "number": 31,
                "title": "Add a CI job",
                "body": "Edit .github/workflows/ci.yml to run the suite.",
                "labels": ["agent:approved"],
                "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
            }
        ),
        encoding="utf-8",
    )
    again = main(
        [
            "run",
            "--issue",
            "31",
            "--issue-snapshot",
            str(workflow),
            "--model-fixture",
            str(fixture),
            "--spec-fixture",
            str(missing),
            "--config",
            str(repo_root / "config/pipeline.config.example.yaml"),
            "--models",
            str(repo_root / "config/models.yaml"),
            "--budgets",
            str(repo_root / "config/budgets.yaml"),
            "--tiers",
            str(repo_root / "config/tiers.yaml"),
            "--runs-dir",
            str(tmp_path / "runs-ci"),
            "--db",
            str(tmp_path / "ci.db"),
        ]
    )
    text = capsys.readouterr().out
    assert again != 0
    assert "docs-lane:" not in text


def test_docs_verify_skips_the_test_command(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run = tmp_path / "run"
    run.mkdir()
    (run / "docs.json").write_text(
        json.dumps({"lane": "docs", "escalated": False}),
        encoding="utf-8",
    )
    (run / "spec.json").write_text(
        json.dumps(
            {
                "acceptance_criteria": [{"id": "AC-1", "statement": "Docs."}],
                "open_questions": [],
                "needs_human": False,
                "seams": ["docs"],
                "e2e_scenarios": [{"id": "E2E-1", "scenario": "Read it."}],
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
                        "title": "Docs",
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
                "tests_added": [],
                "acceptance_tests": [],
                "commit_sha": "abc",
            }
        ),
        encoding="utf-8",
    )
    result = execute_verify(
        run_dir=run,
        run_id="run-docs",
        config={"checks": {"test": "false", "lint": "true"}},
        fixture_path=None,
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=10,
    )
    saved = json.loads((run / "verify.json").read_text(encoding="utf-8"))
    assert result.status == "pass"
    assert saved["verification"] == "docs"
    assert [item["name"] for item in saved["tickets"][0]["commands"]] == ["lint"]
