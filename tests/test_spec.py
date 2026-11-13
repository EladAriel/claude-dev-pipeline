import json

import yaml

from claude_dev_pipeline.budget import BudgetHardStop
from claude_dev_pipeline.cli import main
from claude_dev_pipeline.config import load_yaml
from claude_dev_pipeline.db import connect
from claude_dev_pipeline.mcp_policy import McpPolicyError, read_only_mcp_for
from claude_dev_pipeline.stages.spec import SpecError, SpecSkipped, execute_spec, validate_artifact


def _routed() -> dict:
    return {
        "classify": "bug",
        "category": "bug",
        "state": "ready-for-agent",
        "risk_tier": "Standard",
        "decision": "route",
        "needs_human": False,
        "confidence": 88,
        "injection_suspected": False,
        "summary": "Parser bug.",
    }


def _spec_body(blocked: bool) -> tuple[dict, str]:
    artifact = {
        "acceptance_criteria": [
            {"id": "AC-1", "statement": "Quoted input round-trips."},
        ],
        "open_questions": (
            [{"id": "Q-1", "question": "Which quote styles count?", "blocks": True}]
            if blocked
            else []
        ),
        "needs_human": blocked,
        "seams": ["parser.parse"],
        "e2e_scenarios": [{"id": "E2E-1", "scenario": "Run the parser on a quoted string."}],
    }
    spec_md = "# Spec\n\nAC-1: Quoted input round-trips.\n"
    return artifact, spec_md


def _approved(number: int = 12) -> dict:
    return {
        "number": number,
        "title": "Parser drops quotes",
        "body": "A quoted token loses its quotes.",
        "labels": ["agent:approved"],
        "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
    }


def test_blocking_question_requires_a_human():
    artifact, spec_md = _spec_body(blocked=True)
    validate_artifact(artifact, spec_md)
    artifact["needs_human"] = False
    try:
        validate_artifact(artifact, spec_md)
    except SpecError as exc:
        assert "needs_human" in str(exc)
    else:
        raise AssertionError("blocking question was accepted without a human")


def test_rejected_triage_does_not_open_the_spec_fixture(tmp_path):
    missing = tmp_path / "missing-spec.json"
    try:
        execute_spec(
            triage={"decision": "reject"},
            brief="unused",
            fixture_path=missing,
            run_dir=tmp_path / "run",
            run_id="run-spec",
            spent_cents=1,
            run_cap_cents=2000,
            stage_cap_cents=300,
        )
    except SpecSkipped as exc:
        assert "rejected" in str(exc)
    else:
        raise AssertionError("spec ran after triage rejected")
    assert not missing.exists()
    assert not (tmp_path / "run" / "spec.json").exists()


def test_recorded_spec_books_the_cap_and_read_only_mcp(tmp_path, repo_root):
    artifact, spec_md = _spec_body(blocked=False)
    fixture = tmp_path / "spec.json"
    fixture.write_text(
        json.dumps(
            {
                "artifact": artifact,
                "spec_md": spec_md,
                "usage": {"amount_cents": 20, "input_tokens": 40, "output_tokens": 15},
            }
        ),
        encoding="utf-8",
    )
    config = load_yaml(repo_root / "config/pipeline.config.example.yaml")
    result = execute_spec(
        triage=_routed(),
        brief="AI draft: not posted.\n",
        fixture_path=fixture,
        run_dir=tmp_path / "run",
        run_id="run-spec",
        spent_cents=1,
        run_cap_cents=2000,
        stage_cap_cents=300,
        mcp_config=config,
        effort="medium",
    )
    saved = json.loads((tmp_path / "run" / "spec.json").read_text(encoding="utf-8"))
    mcp_plan = json.loads((tmp_path / "run" / "spec-mcp.json").read_text(encoding="utf-8"))
    assert saved["acceptance_criteria"][0]["id"] == "AC-1"
    assert result.needs_human is False
    assert result.budget_cents == 300
    assert {item["name"] for item in mcp_plan} == {"github_issues_readonly", "atlassian"}
    assert all(item["enabled"] is False and item["contacted"] is False for item in mcp_plan)
    assert all(item["access"] == "read" for item in mcp_plan)


def test_over_budget_spec_does_not_write(tmp_path):
    artifact, spec_md = _spec_body(blocked=False)
    fixture = tmp_path / "spec.json"
    fixture.write_text(
        json.dumps({"artifact": artifact, "spec_md": spec_md, "usage": {"amount_cents": 500}}),
        encoding="utf-8",
    )
    try:
        execute_spec(
            triage=_routed(),
            brief="brief\n",
            fixture_path=fixture,
            run_dir=tmp_path / "run",
            run_id="run-spec",
            spent_cents=0,
            run_cap_cents=2000,
            stage_cap_cents=300,
        )
    except BudgetHardStop:
        pass
    else:
        raise AssertionError("over-budget spec was booked")
    assert not (tmp_path / "run" / "spec.md").exists()


def test_read_only_policy_rejects_a_write_tool_on_spec(repo_root):
    config = load_yaml(repo_root / "config/pipeline.config.example.yaml")
    config["mcp_servers"]["atlassian"]["stages"]["spec"]["tools"] = ["createConfluenceContent"]
    try:
        read_only_mcp_for(config, "spec")
    except McpPolicyError as exc:
        assert exc.errors
    else:
        raise AssertionError("write tool was allowed on spec")


def test_cli_dry_run_shows_spec_and_books_a_routed_fixture(tmp_path, capsys, repo_root):
    triage = tmp_path / "triage.json"
    triage.write_text(
        json.dumps(
            {
                "artifact": _routed(),
                "brief": "AI draft: not posted.\n\nFix the parser.\n",
                "usage": {"amount_cents": 1, "input_tokens": 10, "output_tokens": 5},
            }
        ),
        encoding="utf-8",
    )
    artifact, spec_md = _spec_body(blocked=True)
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "artifact": artifact,
                "spec_md": spec_md,
                "usage": {"amount_cents": 15, "input_tokens": 30, "output_tokens": 12},
            }
        ),
        encoding="utf-8",
    )
    snapshot = tmp_path / "issue.json"
    snapshot.write_text(json.dumps(_approved()), encoding="utf-8")
    common = [
        "--issue",
        "12",
        "--issue-snapshot",
        str(snapshot),
        "--model-fixture",
        str(triage),
        "--spec-fixture",
        str(spec),
        "--runs-dir",
        str(tmp_path / "runs"),
        "--db",
        str(tmp_path / "pipeline.db"),
        "--config",
        str(repo_root / "config/pipeline.config.example.yaml"),
        "--models",
        str(repo_root / "config/models.yaml"),
        "--budgets",
        str(repo_root / "config/budgets.yaml"),
    ]
    assert main(["run", "--dry-run", *common]) == 0
    dry = capsys.readouterr().out
    assert "key=spec" in dry
    assert "claude-opus-5.5" in dry
    assert "effort=medium" in dry
    assert "skills=grill-with-docs,to-spec" in dry
    assert not any((tmp_path / "runs").rglob("spec.json"))

    assert main(["run", *common]) == 0
    booked = capsys.readouterr().out
    assert "mode: spec-fixture" in booked
    assert "api-calls: none" in booked
    assert "spec: needs_human=true" in booked
    assert "mcp-contacted: none" in booked
    saved = json.loads(next((tmp_path / "runs").rglob("spec.json")).read_text(encoding="utf-8"))
    assert saved["needs_human"] is True
    conn = connect(tmp_path / "pipeline.db")
    try:
        rows = conn.execute("SELECT name, model, effort, budget_cents FROM stages").fetchall()
    finally:
        conn.close()
    by_name = {row["name"]: row for row in rows}
    assert by_name["spec"]["model"] == "claude-opus-5.5"
    assert by_name["spec"]["effort"] == "medium"
    assert by_name["spec"]["budget_cents"] == 300
    assert by_name["triage"]["budget_cents"] == 10


def test_cli_skips_spec_when_triage_rejects(tmp_path, capsys, repo_root):
    rejected = _routed()
    rejected["decision"] = "reject"
    rejected["state"] = "wontfix"
    triage = tmp_path / "triage.json"
    triage.write_text(
        json.dumps({"artifact": rejected, "brief": "", "usage": {"amount_cents": 1}}),
        encoding="utf-8",
    )
    code = main(
        [
            "run",
            "--issue",
            "12",
            "--issue-snapshot",
            str(_write_issue(tmp_path)),
            "--model-fixture",
            str(triage),
            "--spec-fixture",
            str(tmp_path / "missing-spec.json"),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--db",
            str(tmp_path / "pipeline.db"),
            "--budgets",
            str(repo_root / "config/budgets.yaml"),
            "--config",
            str(repo_root / "config/pipeline.config.example.yaml"),
        ]
    )
    assert code == 0
    assert "spec: skipped (triage rejected)" in capsys.readouterr().out
    assert not any((tmp_path / "runs").rglob("spec.json"))


def test_disabled_spec_does_not_open_the_fixture(tmp_path, capsys, repo_root):
    config = load_yaml(repo_root / "config/pipeline.config.example.yaml")
    config["stages"]["spec"] = {"enabled": False}
    config_path = tmp_path / "pipeline.config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    triage = tmp_path / "triage.json"
    triage.write_text(
        json.dumps(
            {
                "artifact": _routed(),
                "brief": "AI draft: not posted.\n",
                "usage": {"amount_cents": 1},
            }
        ),
        encoding="utf-8",
    )
    code = main(
        [
            "run",
            "--issue",
            "12",
            "--issue-snapshot",
            str(_write_issue(tmp_path)),
            "--model-fixture",
            str(triage),
            "--spec-fixture",
            str(tmp_path / "missing-spec.json"),
            "--config",
            str(config_path),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--db",
            str(tmp_path / "db.sqlite"),
            "--budgets",
            str(repo_root / "config/budgets.yaml"),
        ]
    )
    assert code == 0
    assert "spec: disabled" in capsys.readouterr().out
    assert not any((tmp_path / "runs").rglob("spec.json"))


def test_agent_file_pins_the_manifest_skills(repo_root):
    text = (repo_root / "agents/spec.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---", 2)[1])
    enabled = [item["name"] for item in meta["tools"][0]["configs"] if item["enabled"]]
    models = load_yaml(repo_root / "config/models.yaml")
    manifest = load_yaml(repo_root / "skills/manifest.yaml")
    assert meta["model"]["id"] == models["spec"]["model"]
    assert meta["model"]["effort_from"] == "config/models.yaml#spec"
    assert models["spec"]["effort"] == "medium"
    assert enabled == ["read", "grep", "glob", "bash"]
    bash = next(item for item in meta["tools"][0]["configs"] if item["name"] == "bash")
    assert bash["read_only"] is True
    by_name = {item["name"]: item for item in manifest["skills"]}
    for skill in meta["skills"]:
        assert skill["sha"] == by_name[skill["name"]]["sha"]
        assert skill["path"] == by_name[skill["name"]]["path"]
    dependencies = {item["name"]: item for item in manifest["dependencies"]}
    for skill in meta["skill_dependencies"]:
        assert skill["sha"] == dependencies[skill["name"]]["sha"]
        assert skill["path"] == dependencies[skill["name"]]["path"]
    assert meta["mcp_servers"] == [
        {"name": "github_issues_readonly", "enabled": False, "access": "read"},
        {"name": "atlassian", "enabled": False, "access": "read"},
    ]


def _write_issue(tmp_path):
    path = tmp_path / "issue.json"
    path.write_text(json.dumps(_approved()), encoding="utf-8")
    return path
