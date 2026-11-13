import json
from pathlib import Path

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.config import load_yaml
from claude_dev_pipeline.db import connect, write_run_artifact
from claude_dev_pipeline.stages.retro import execute_retro
from claude_dev_pipeline.stages.review import select_depth
from claude_dev_pipeline.stages.triage import TierApprovalRequired, execute_triage
from claude_dev_pipeline.tiers import assign_tier


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
        "summary": "Small parser fix.",
    }
    artifact.update(overrides)
    return artifact


def _models() -> dict:
    return {
        "implement": {
            "model": "claude-sonnet-5.5",
            "allowlist": ["claude-sonnet-5.5", "claude-opus-5.5"],
        },
        "verify": {"model": "claude-haiku-5.5"},
        "finder-bugs": {"model": "claude-opus-5.5"},
    }


def test_signals_raise_a_small_tier_and_drop_illegal_models():
    tiers = load_yaml(Path("config/tiers.yaml"))
    artifact = _artifact(
        tier="S",
        signals={
            "files_touched": 2,
            "cross_module": False,
            "security_sensitive": True,
            "ambiguity": "low",
        },
        tier_rationale="Looks small.",
    )
    artifact_models = {
        **_models(),
    }
    # An illegal model on the tier spec is ignored.
    tiers["tiers"]["L"]["models"]["implement"] = {"model": "claude-haiku-5.5", "effort": "low"}
    saved = assign_tier(artifact, tiers, artifact_models)
    assert saved["tier"] == "L"
    assert saved["routing"]["escalated"] is True
    assert saved["routing"]["lane"] == "full"
    assert saved["risk_tier"] == "Deep"
    assert saved["routing"]["review_depth"] == "Deep"
    assert "implement" not in saved["routing"]["models"]
    depth = select_depth(
        triage_tier=saved["routing"]["review_depth"],
        files=["src/parser.py"],
        diff_lines=10,
        protected=[],
        max_diff_lines=1500,
    )
    assert depth == "Deep"


def test_xl_stops_without_approval_and_runs_with_it(tmp_path, repo_root, capsys):
    tiers = load_yaml(repo_root / "config/tiers.yaml")
    snapshot = {
        "number": 4,
        "title": "Rewrite the storage layer",
        "body": "Every module moves.",
        "labels": ["agent:approved"],
        "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
    }
    fixture = tmp_path / "triage.json"
    fixture.write_text(
        json.dumps(
            {
                "artifact": _artifact(tier="XL", risk_tier="Deep", summary="Large migration."),
                "brief": "Migrate storage.",
                "usage": {"amount_cents": 1},
            }
        ),
        encoding="utf-8",
    )
    try:
        execute_triage(
            snapshot=snapshot,
            fixture_path=fixture,
            run_dir=tmp_path / "run",
            run_id="run-xl",
            spent_cents=0,
            run_cap_cents=2000,
            stage_cap_cents=10,
            tiers=tiers,
            models=_models(),
        )
    except TierApprovalRequired as exc:
        assert "--approve-xl" in str(exc)
    else:
        raise AssertionError("XL ran without approval")
    result = execute_triage(
        snapshot=snapshot,
        fixture_path=fixture,
        run_dir=tmp_path / "approved",
        run_id="run-xl-ok",
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=10,
        tiers=tiers,
        models=_models(),
        approve_xl=True,
    )
    assert result.risk_tier == "Deep"
    missing_spec = tmp_path / "missing-spec.json"
    issue = tmp_path / "issue.json"
    issue.write_text(json.dumps(snapshot), encoding="utf-8")
    code = main(
        [
            "run",
            "--issue",
            "4",
            "--issue-snapshot",
            str(issue),
            "--model-fixture",
            str(fixture),
            "--spec-fixture",
            str(missing_spec),
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
    captured = capsys.readouterr()
    assert code == 2
    assert "approve-xl" in captured.err
    assert not missing_spec.exists()


def test_metrics_record_tier_and_escalation(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    triage = assign_tier(
        _artifact(tier="S", signals={"files_touched": 9, "security_sensitive": False}),
        load_yaml(Path("config/tiers.yaml")),
        _models(),
    )
    (run / "triage.json").write_text(json.dumps(triage), encoding="utf-8")
    (run / "verify.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    execute_retro(
        run_dir=run,
        run_id="run-tier",
        caps={"retro": 75},
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=75,
        stop_reason="success",
    )
    metrics = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["tier"] == "L"
    assert metrics["escalated"] is True
    runs = tmp_path / "runs"
    db = tmp_path / "pipeline.db"
    dest = runs / "run-tier"
    dest.mkdir(parents=True)
    (dest / "metrics.json").write_text(
        (run / "metrics.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (dest / "run.json").write_text("{}", encoding="utf-8")
    write_run_artifact(
        runs,
        db,
        {"run_id": "run-tier", "started_at": "2026-11-13T00:00:00+00:00", "status": "succeeded"},
    )
    # write_run_artifact overwrites run.json and rebuilds; metrics.json is still there.
    saved = connect(db)
    row = saved.execute("SELECT tier, escalated FROM run_metrics").fetchone()
    assert row["tier"] == "L"
    assert row["escalated"] == 1


def test_disabled_tiers_leave_the_fixture_unchanged(tmp_path):
    snapshot = {
        "number": 1,
        "title": "Tiny",
        "body": "One line.",
        "labels": ["agent:approved"],
        "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
    }
    fixture = tmp_path / "triage.json"
    fixture.write_text(
        json.dumps(
            {"artifact": _artifact(tier="XL"), "brief": "Tiny.", "usage": {"amount_cents": 1}}
        ),
        encoding="utf-8",
    )
    execute_triage(
        snapshot=snapshot,
        fixture_path=fixture,
        run_dir=tmp_path / "run",
        run_id="run-off",
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=10,
        tiers={"enabled": False},
    )
    saved = json.loads((tmp_path / "run" / "triage.json").read_text(encoding="utf-8"))
    assert "routing" not in saved
    assert saved["tier"] == "XL"
