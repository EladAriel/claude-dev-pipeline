import json

import yaml

from claude_dev_pipeline.budget import BudgetHardStop
from claude_dev_pipeline.cli import main
from claude_dev_pipeline.config import load_yaml
from claude_dev_pipeline.db import connect
from claude_dev_pipeline.issues import issue_drafts, load_tasks
from claude_dev_pipeline.stages.plan import PlanError, PlanSkipped, execute_plan, validate_plan


def _spec(needs_human: bool = False) -> dict:
    return {
        "acceptance_criteria": [
            {"id": "AC-1", "statement": "Quoted input round-trips."},
            {"id": "AC-2", "statement": "An empty quote is an error."},
        ],
        "open_questions": (
            [{"id": "Q-1", "question": "Which quotes?", "blocks": True}] if needs_human else []
        ),
        "needs_human": needs_human,
        "seams": ["parser.parse"],
        "e2e_scenarios": [{"id": "E2E-1", "scenario": "Run the parser."}],
    }


def _ticket(ticket_id: str, criteria: list[str], blocked: list[str] | None = None) -> dict:
    return {
        "id": ticket_id,
        "title": f"Ticket {ticket_id}",
        "acceptance_criteria": criteria,
        "files": ["src/parser.py"],
        "seams": ["parser.parse"],
        "test_plan": f"Cover {', '.join(criteria) or 'the seam'}.",
        "deps": blocked or [],
        "estimate": "S",
        "suggested_model": "claude-sonnet-5.5",
    }


def _plan() -> dict:
    return {"tasks": [_ticket("01", ["AC-1"]), _ticket("02", ["AC-2"], ["01"])]}


def _approved() -> dict:
    return {
        "number": 12,
        "title": "Parser drops quotes",
        "body": "A quoted token loses its quotes.",
        "labels": ["agent:approved"],
        "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
    }


def _triage_fixture() -> dict:
    return {
        "artifact": {
            "classify": "bug",
            "category": "bug",
            "state": "ready-for-agent",
            "risk_tier": "Standard",
            "decision": "route",
            "needs_human": False,
            "confidence": 90,
            "injection_suspected": False,
            "summary": "Parser bug.",
        },
        "brief": "AI draft: not posted.\n",
        "usage": {"amount_cents": 1},
    }


def _spec_fixture(needs_human: bool = False) -> dict:
    return {
        "artifact": _spec(needs_human),
        "spec_md": "# Spec\n\nAC-1: Quoted input round-trips.\nAC-2: An empty quote is an error.\n",
        "usage": {"amount_cents": 15},
    }


def test_needs_human_does_not_open_the_plan_fixture(tmp_path):
    missing = tmp_path / "missing-plan.json"
    try:
        execute_plan(
            spec=_spec(needs_human=True),
            spec_md="# Spec\n",
            fixture_path=missing,
            run_dir=tmp_path / "run",
            run_id="run-plan",
            spent_cents=16,
            run_cap_cents=2000,
            stage_cap_cents=200,
        )
    except PlanSkipped as exc:
        assert "human" in str(exc)
    else:
        raise AssertionError("plan ran while the spec needed a human")
    assert not missing.exists()


def test_plan_covers_the_spec_and_imports(tmp_path, repo_root):
    fixture = tmp_path / "plan.json"
    fixture.write_text(
        json.dumps({"artifact": _plan(), "usage": {"amount_cents": 20, "output_tokens": 30}}),
        encoding="utf-8",
    )
    result = execute_plan(
        spec=_spec(),
        spec_md="# Spec\n\nAC-1 and AC-2.\n",
        fixture_path=fixture,
        run_dir=tmp_path / "run",
        run_id="run-plan",
        spent_cents=16,
        run_cap_cents=2000,
        stage_cap_cents=200,
        mcp_config=load_yaml(repo_root / "config/pipeline.config.example.yaml"),
        effort="medium",
    )
    saved_path = tmp_path / "run" / "plan.json"
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    tasks = load_tasks(saved_path)
    drafts = issue_drafts(tasks)
    assert result.tickets == 2
    assert result.budget_cents == 200
    assert saved["tasks"][1]["blocked_by"] == ["01"]
    assert saved["tasks"][0]["suggested_model"] == "claude-sonnet-5.5"
    assert [draft.task_id for draft in drafts] == ["01", "02"]
    mcp_plan = json.loads((tmp_path / "run" / "plan-mcp.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in mcp_plan] == ["atlassian"]
    assert mcp_plan[0]["enabled"] is False
    assert mcp_plan[0]["contacted"] is False
    assert mcp_plan[0]["access"] == "read"


def test_cycle_and_gap_are_rejected():
    cycle = {"tasks": [_ticket("01", ["AC-1"], ["02"]), _ticket("02", ["AC-2"], ["01"])]}
    try:
        validate_plan(cycle, _spec())
    except PlanError as exc:
        assert "earlier" in str(exc)
    else:
        raise AssertionError("cycle was accepted")
    gap = {"tasks": [_ticket("01", ["AC-1"])]}
    try:
        validate_plan(gap, _spec())
    except PlanError as exc:
        assert "AC-2" in str(exc)
    else:
        raise AssertionError("uncovered criterion was accepted")


def test_over_budget_plan_does_not_write(tmp_path):
    fixture = tmp_path / "plan.json"
    fixture.write_text(
        json.dumps({"artifact": _plan(), "usage": {"amount_cents": 250}}),
        encoding="utf-8",
    )
    try:
        execute_plan(
            spec=_spec(),
            spec_md="# Spec\n",
            fixture_path=fixture,
            run_dir=tmp_path / "run",
            run_id="run-plan",
            spent_cents=0,
            run_cap_cents=2000,
            stage_cap_cents=200,
        )
    except BudgetHardStop:
        pass
    else:
        raise AssertionError("over-budget plan was booked")
    assert not (tmp_path / "run" / "plan.json").exists()


def test_cli_dry_run_shows_plan_and_books_a_fixture(tmp_path, capsys, repo_root):
    triage = tmp_path / "triage.json"
    spec = tmp_path / "spec.json"
    plan = tmp_path / "plan.json"
    triage.write_text(json.dumps(_triage_fixture()), encoding="utf-8")
    spec.write_text(json.dumps(_spec_fixture()), encoding="utf-8")
    plan.write_text(
        json.dumps({"artifact": _plan(), "usage": {"amount_cents": 20, "output_tokens": 12}}),
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
        "--plan-fixture",
        str(plan),
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
    assert "key=plan" in dry
    assert "claude-opus-5.5" in dry
    assert "effort=medium" in dry
    assert "skills=to-tickets" in dry
    assert not any((tmp_path / "runs").rglob("plan.json"))

    assert main(["run", *common]) == 0
    booked = capsys.readouterr().out
    assert "mode: plan-fixture" in booked
    assert "api-calls: none" in booked
    assert "plan: tickets=2" in booked
    assert "mcp-contacted: none" in booked
    conn = connect(tmp_path / "pipeline.db")
    try:
        row = conn.execute(
            "SELECT model, effort, budget_cents FROM stages WHERE name = 'plan'"
        ).fetchone()
    finally:
        conn.close()
    assert row["model"] == "claude-opus-5.5"
    assert row["effort"] == "medium"
    assert row["budget_cents"] == 200


def test_cli_skips_plan_when_the_spec_needs_a_human(tmp_path, capsys, repo_root):
    triage = tmp_path / "triage.json"
    spec = tmp_path / "spec.json"
    triage.write_text(json.dumps(_triage_fixture()), encoding="utf-8")
    spec.write_text(json.dumps(_spec_fixture(needs_human=True)), encoding="utf-8")
    code = main(
        [
            "run",
            "--issue",
            "12",
            "--issue-snapshot",
            str(_issue_path(tmp_path)),
            "--model-fixture",
            str(triage),
            "--spec-fixture",
            str(spec),
            "--plan-fixture",
            str(tmp_path / "missing-plan.json"),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--db",
            str(tmp_path / "pipeline.db"),
            "--config",
            str(repo_root / "config/pipeline.config.example.yaml"),
            "--budgets",
            str(repo_root / "config/budgets.yaml"),
        ]
    )
    assert code == 0
    assert "plan: skipped (spec needs a human)" in capsys.readouterr().out
    assert not any((tmp_path / "runs").rglob("plan.json"))


def test_disabled_plan_does_not_open_the_fixture(tmp_path, capsys, repo_root):
    config = load_yaml(repo_root / "config/pipeline.config.example.yaml")
    config["stages"]["plan"] = {"enabled": False}
    config_path = tmp_path / "pipeline.config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    triage = tmp_path / "triage.json"
    spec = tmp_path / "spec.json"
    triage.write_text(json.dumps(_triage_fixture()), encoding="utf-8")
    spec.write_text(json.dumps(_spec_fixture()), encoding="utf-8")
    code = main(
        [
            "run",
            "--issue",
            "12",
            "--issue-snapshot",
            str(_issue_path(tmp_path)),
            "--model-fixture",
            str(triage),
            "--spec-fixture",
            str(spec),
            "--plan-fixture",
            str(tmp_path / "missing-plan.json"),
            "--config",
            str(config_path),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--db",
            str(tmp_path / "pipeline.db"),
            "--budgets",
            str(repo_root / "config/budgets.yaml"),
        ]
    )
    assert code == 0
    assert "plan: disabled" in capsys.readouterr().out
    assert not any((tmp_path / "runs").rglob("plan.json"))


def test_agent_file_pins_to_tickets(repo_root):
    text = (repo_root / "agents/plan.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---", 2)[1])
    models = load_yaml(repo_root / "config/models.yaml")
    manifest = load_yaml(repo_root / "skills/manifest.yaml")
    skill = next(item for item in manifest["skills"] if item["name"] == "to-tickets")
    enabled = [item["name"] for item in meta["tools"][0]["configs"] if item["enabled"]]
    assert meta["model"]["id"] == models["plan"]["model"]
    assert meta["model"]["effort_from"] == "config/models.yaml#plan"
    assert models["plan"]["effort"] == "medium"
    assert enabled == ["read", "grep", "glob"]
    assert meta["skills"][0]["sha"] == skill["sha"]
    assert meta["skills"][0]["path"] == skill["path"]
    assert meta["mcp_servers"] == [{"name": "atlassian", "enabled": False, "access": "read"}]
    assert "skill named `to-tickets`" in text


def _issue_path(tmp_path):
    path = tmp_path / "issue.json"
    path.write_text(json.dumps(_approved()), encoding="utf-8")
    return path
