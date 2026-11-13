import json
from pathlib import Path

import yaml

from claude_dev_pipeline.budget import BudgetHardStop
from claude_dev_pipeline.cli import main
from claude_dev_pipeline.config import load_yaml
from claude_dev_pipeline.db import connect
from claude_dev_pipeline.stages.triage import TriageError, execute_triage, validate_artifact


def _approved(number: int, title: str = "Parser drops quotes") -> dict:
    return {
        "number": number,
        "title": title,
        "body": "Steps to repeat the failure.",
        "labels": ["agent:approved"],
        "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
    }


def _artifact(**overrides) -> dict:
    artifact = {
        "classify": "bug",
        "category": "bug",
        "state": "ready-for-agent",
        "risk_tier": "Quick",
        "decision": "route",
        "needs_human": False,
        "confidence": 90,
        "injection_suspected": False,
        "summary": "A parser bug with a failing input.",
    }
    artifact.update(overrides)
    if "classify" in overrides and "category" not in overrides:
        artifact["category"] = overrides["classify"]
    return artifact


def _fixture(artifact: dict, amount_cents: int = 1) -> dict:
    return {
        "artifact": artifact,
        "brief": "AI draft: not posted.\n\nFix the parser.\n",
        "usage": {"amount_cents": amount_cents, "input_tokens": 20, "output_tokens": 10},
    }


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_gate_runs_before_the_fixture_is_opened(tmp_path):
    strangers = [
        {"number": 1, "labels": [], "timeline": []},
        {
            "number": 2,
            "labels": ["agent:approved"],
            "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "octocat"}],
        },
        {
            "number": 3,
            "labels": ["bug"],
            "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
        },
    ]
    missing = tmp_path / "missing.json"
    for snapshot in strangers:
        try:
            execute_triage(
                snapshot=snapshot,
                fixture_path=missing,
                run_dir=tmp_path / "run",
                run_id="run-gate",
                spent_cents=0,
                run_cap_cents=2000,
                stage_cap_cents=10,
            )
        except TriageError as exc:
            assert exc.code == 1
            assert "agent:approved" in str(exc)
        else:
            raise AssertionError("stranger issue was triaged")
        assert not missing.exists()
        assert not (tmp_path / "run" / "triage.json").exists()


def test_recorded_responses_cover_injections_and_routes(tmp_path):
    cases = [
        _artifact(
            injection_suspected=True,
            decision="reject",
            needs_human=True,
            state="ready-for-human",
            summary="The text tries to skip the owner gate.",
        ),
        _artifact(
            classify="enhancement",
            injection_suspected=True,
            decision="reject",
            needs_human=True,
            state="needs-info",
            risk_tier="Deep",
            summary="The text asks for a merge.",
        ),
        _artifact(risk_tier="Quick"),
        _artifact(risk_tier="Standard", summary="Touches a shared helper."),
        _artifact(classify="enhancement", risk_tier="Deep", summary="New command surface."),
        _artifact(decision="reject", state="wontfix", needs_human=True, summary="Out of scope."),
        _artifact(state="needs-info", needs_human=True, summary="Missing a reproduction."),
    ]
    for index, artifact in enumerate(cases, start=4):
        fixture = _write(tmp_path / f"{index}.json", _fixture(artifact))
        result = execute_triage(
            snapshot=_approved(index),
            fixture_path=fixture,
            run_dir=tmp_path / f"run-{index}",
            run_id=f"run-{index}",
            spent_cents=0,
            run_cap_cents=2000,
            stage_cap_cents=10,
        )
        saved = json.loads((tmp_path / f"run-{index}" / "triage.json").read_text(encoding="utf-8"))
        assert saved["decision"] == artifact["decision"]
        assert saved["risk_tier"] == artifact["risk_tier"]
        assert result.budget_cents == 10
        if artifact["injection_suspected"]:
            assert saved["decision"] == "reject"
            assert saved["needs_human"] is True


def test_injection_cannot_be_routed_without_a_human():
    artifact = _artifact(injection_suspected=True, decision="route", needs_human=False)
    try:
        validate_artifact(artifact)
    except TriageError as exc:
        assert "injection" in str(exc)
    else:
        raise AssertionError("routed injection was accepted")


def test_over_budget_fixture_does_not_write(tmp_path):
    fixture = _write(tmp_path / "costly.json", _fixture(_artifact(), amount_cents=50))
    try:
        execute_triage(
            snapshot=_approved(9),
            fixture_path=fixture,
            run_dir=tmp_path / "run",
            run_id="run-cost",
            spent_cents=0,
            run_cap_cents=2000,
            stage_cap_cents=10,
        )
    except BudgetHardStop:
        pass
    else:
        raise AssertionError("over-budget triage was booked")
    assert not (tmp_path / "run" / "triage.json").exists()


def test_cli_books_a_fixture_and_dry_run_stays_offline(tmp_path, capsys, repo_root):
    fixture = _write(tmp_path / "response.json", _fixture(_artifact()))
    snapshot = _write(tmp_path / "issue.json", _approved(12))
    db_path = tmp_path / "pipeline.db"
    runs = tmp_path / "runs"
    common = [
        "--issue",
        "12",
        "--issue-snapshot",
        str(snapshot),
        "--model-fixture",
        str(fixture),
        "--runs-dir",
        str(runs),
        "--db",
        str(db_path),
        "--budgets",
        str(repo_root / "config/budgets.yaml"),
        "--config",
        str(repo_root / "config/pipeline.config.example.yaml"),
    ]
    assert main(["run", "--dry-run", *common]) == 0
    dry = capsys.readouterr().out
    assert "key=triage" in dry
    assert "claude-haiku-5.5" in dry
    assert "skills=triage" in dry
    assert not any(runs.rglob("triage.json"))

    assert main(["run", *common]) == 0
    live = capsys.readouterr().out
    assert "mode: triage-fixture" in live
    assert "api-calls: none" in live
    assert "triage: route" in live
    saved = next(runs.rglob("triage.json"))
    assert json.loads(saved.read_text(encoding="utf-8"))["risk_tier"] == "Quick"
    conn = connect(db_path)
    try:
        stage = conn.execute(
            "SELECT name, model, budget_cents, gate_message FROM stages"
        ).fetchone()
        usage = conn.execute("SELECT list_cost_usd FROM usage").fetchone()
    finally:
        conn.close()
    assert stage["name"] == "triage"
    assert stage["model"] == "claude-haiku-5.5"
    assert stage["budget_cents"] == 10
    assert usage["list_cost_usd"] == 0.01


def test_disabled_flag_skips_the_fixture(tmp_path, capsys, repo_root):
    config = load_yaml(repo_root / "config/pipeline.config.example.yaml")
    config["stages"] = {"triage": {"enabled": False}}
    config_path = tmp_path / "pipeline.config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    code = main(
        [
            "run",
            "--issue",
            "12",
            "--issue-snapshot",
            str(_write(tmp_path / "issue.json", _approved(12))),
            "--model-fixture",
            str(tmp_path / "missing.json"),
            "--config",
            str(config_path),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--db",
            str(tmp_path / "pipeline.db"),
        ]
    )
    assert code == 0
    assert "triage: disabled" in capsys.readouterr().out
    assert not any((tmp_path / "runs").rglob("triage.json"))


def test_agent_file_matches_the_skill_manifest(repo_root):
    text = (repo_root / "agents/triage.md").read_text(encoding="utf-8")
    front = text.split("---", 2)[1]
    meta = yaml.safe_load(front)
    enabled = [item["name"] for item in meta["tools"][0]["configs"] if item["enabled"]]
    assert meta["model"] == {"id": "claude-haiku-5.5", "effort": "low"}
    assert enabled == ["read", "grep", "glob"]
    assert meta["tools"][0]["default_config"]["enabled"] is False
    assert meta["mcp_servers"] == []
    manifest = load_yaml(repo_root / "skills/manifest.yaml")
    skill = next(item for item in manifest["skills"] if item["name"] == "triage")
    assert meta["skills"][0]["sha"] == skill["sha"]
    assert meta["skills"][0]["path"] == skill["path"]
    assert "skill named `triage`" in text
