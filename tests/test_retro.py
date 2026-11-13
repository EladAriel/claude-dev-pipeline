import json

from claude_dev_pipeline.budget import stage_run_cap
from claude_dev_pipeline.cli import main
from claude_dev_pipeline.db import aggregate_report, connect, write_run_artifact
from claude_dev_pipeline.stages.retro import RetroSkipped, execute_retro


def test_reserve_leaves_room_for_retro():
    assert stage_run_cap(run_cap_cents=2000, retro_reserve_cents=75, stage="work") == 1925
    assert stage_run_cap(run_cap_cents=2000, retro_reserve_cents=75, stage="retro") == 2000


def test_proposals_are_not_applied(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "ledger.jsonl").write_text(
        json.dumps(
            {
                "run_id": "run",
                "stage": "review",
                "amount_cents": 4,
                "provider": "anthropic",
                "model": "claude-haiku-5.5",
                "input_tokens": 3,
                "output_tokens": 1,
                "budget_cents": 10,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run / "verify.json").write_text(json.dumps({"status": "pass"}) + "\n", encoding="utf-8")
    (run / "review.json").write_text(
        json.dumps(
            {"rounds_run": 1, "findings": [{"family": "openai", "survived": True, "tag": "Nit"}]}
        )
        + "\n",
        encoding="utf-8",
    )
    fixture = tmp_path / "retro.json"
    fixture.write_text(
        json.dumps(
            {
                "proposals": [
                    {
                        "id": "p1",
                        "category": "checks",
                        "severity": "low",
                        "evidence": "metrics.json",
                        "proposed_change": "Add a parser check",
                        "target": "check",
                        "expected_metric": "verify_pass",
                    }
                ],
                "usage": {"amount_cents": 2},
            }
        ),
        encoding="utf-8",
    )
    result = execute_retro(
        run_dir=run,
        run_id="run",
        caps={"retro": 75, "review": 10},
        spent_cents=4,
        run_cap_cents=2000,
        stage_cap_cents=75,
        stop_reason="success",
        fixture_path=fixture,
    )
    text = (run / "retro.md").read_text(encoding="utf-8")
    metrics = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
    assert result.proposals == 1
    assert "Not applied." in text
    assert "Add a parser check" in text
    assert metrics["verify_pass"] is True
    assert metrics["review_survivors_by_family"]["openai"] == 1
    assert metrics["cost"]["by_stage_cents"]["review"] == 4
    assert json.loads((run / "retro.json").read_text(encoding="utf-8"))["applied"] is False


def test_disabled_retro_does_not_open_the_fixture(tmp_path):
    missing = tmp_path / "missing.json"
    try:
        execute_retro(
            run_dir=tmp_path / "run",
            run_id="run",
            caps={},
            spent_cents=0,
            run_cap_cents=2000,
            stage_cap_cents=75,
            stop_reason="success",
            enabled=False,
            fixture_path=missing,
        )
    except RetroSkipped:
        pass
    else:
        raise AssertionError("disabled retro opened the fixture")


def test_budget_halt_still_records_retro(tmp_path, repo_root, capsys):
    snapshot = tmp_path / "issue.json"
    snapshot.write_text(
        json.dumps(
            {
                "number": 3,
                "title": "Too expensive",
                "body": "Stop.",
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
                    "classify": "bug",
                    "category": "bug",
                    "state": "ready-for-agent",
                    "risk_tier": "Quick",
                    "decision": "route",
                    "needs_human": False,
                    "confidence": 80,
                    "injection_suspected": False,
                    "summary": "Over the cap.",
                },
                "brief": "Stop before the model is booked.",
                "usage": {"amount_cents": 11},
            }
        ),
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    code = main(
        [
            "run",
            "--issue",
            "3",
            "--issue-snapshot",
            str(snapshot),
            "--model-fixture",
            str(fixture),
            "--config",
            str(repo_root / "config/pipeline.config.example.yaml"),
            "--models",
            str(repo_root / "config/models.yaml"),
            "--budgets",
            str(repo_root / "config/budgets.yaml"),
            "--runs-dir",
            str(runs),
            "--db",
            str(tmp_path / "pipeline.db"),
        ]
    )
    assert code == 1
    run = next(path for path in runs.iterdir() if path.is_dir())
    assert "Not applied." in (run / "retro.md").read_text(encoding="utf-8")
    assert "budget-halt" in (run / "metrics.json").read_text(encoding="utf-8")
    assert "stop-reason: budget-halt" in capsys.readouterr().out


def _ledger(run_id: str, stage: str, model: str, amount: int, *, called: bool = True) -> dict:
    return {
        "amount_cents": amount,
        "budget_cents": amount,
        "model": model,
        "model_called": called,
        "provider": "anthropic",
        "run_id": run_id,
        "stage": stage,
    }


def _index_run(runs, db, run_id: str, started: str, lines: list[dict], cost: int) -> None:
    run = runs / run_id
    run.mkdir(parents=True)
    (run / "ledger.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines),
        encoding="utf-8",
    )
    (run / "metrics.json").write_text(
        json.dumps(
            {
                "success": True,
                "first_try_success": True,
                "loops": 1,
                "cost_usd": cost / 100,
                "wall_seconds": 0,
                "trajectory_violations": 0,
                "hidden_failure": False,
                "owner_interventions": 0,
                "rounds_to_pass": 1,
                "recorded_at": "2026-11-13T00:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    write_run_artifact(
        runs,
        db,
        {
            "run_id": run_id,
            "started_at": started,
            "status": "succeeded",
            "intake_kind": "task",
            "intake_ref": run_id,
        },
    )


def test_report_uses_each_stage_allowlist_and_skips_idle_models(tmp_path, capsys):
    runs = tmp_path / "runs"
    db = tmp_path / "pipeline.db"
    models = tmp_path / "models.yaml"
    models.write_text(
        "\n".join(
            [
                "implement:",
                "  model: claude-sonnet-5.5",
                "  allowlist:",
                "    - claude-sonnet-5.5",
                "    - claude-opus-5.5",
                "triage:",
                "  model: claude-haiku-5.5",
                "verify:",
                "  model: claude-haiku-5.5",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _index_run(
        runs,
        db,
        "run-a",
        "2026-11-13T00:00:01+00:00",
        [
            _ledger("run-a", "implement", "claude-sonnet-5.5", 10),
            _ledger("run-a", "verify", "none", 0, called=False),
        ],
        10,
    )
    _index_run(
        runs,
        db,
        "run-b",
        "2026-11-13T00:00:02+00:00",
        [
            _ledger("run-b", "implement", "claude-opus-5.5", 40),
            _ledger("run-b", "triage", "claude-haiku-5.5", 1),
        ],
        41,
    )
    loaded = {
        "implement": {
            "model": "claude-sonnet-5.5",
            "allowlist": ["claude-sonnet-5.5", "claude-opus-5.5"],
        },
        "triage": {"model": "claude-haiku-5.5"},
        "verify": {"model": "claude-haiku-5.5"},
    }
    report = aggregate_report(db, loaded)
    cheapest = report["cheapest_model"]
    assert [item["run_id"] for item in report["cost_trend"]] == ["run-a", "run-b"]
    assert cheapest["by_stage"]["implement"]["cheapest"] == "claude-sonnet-5.5"
    assert cheapest["by_stage"]["implement"]["share"] == 0.5
    assert cheapest["by_stage"]["triage"]["share"] == 1
    assert cheapest["by_stage"]["verify"]["calls"] == 0
    assert cheapest["by_stage"]["verify"]["share"] is None
    assert cheapest["calls"] == 3
    assert cheapest["hits"] == 2
    assert cheapest["share"] == 2 / 3
    code = main(["report", "--db", str(db), "--models", str(models)])
    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["cheapest_model"]["by_stage"]["implement"]["share"] == 0.5
    saved = connect(db)
    count = saved.execute("SELECT COUNT(*) FROM run_metrics").fetchone()[0]
    assert count == 2
