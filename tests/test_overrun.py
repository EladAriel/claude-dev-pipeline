import json
from pathlib import Path

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.overrun import downgrade_models, project_stage


def test_soft_threshold_downgrades_without_dropping_security():
    decision = project_stage(
        stage="spec",
        spent_cents=80,
        run_cap_cents=100,
        tier_cap_cents=100,
        estimate_cents=10,
        quoted_cents=0,
        quoted_output_tokens=0,
        soft_ratio=0.8,
        model="claude-opus-5.5",
        estimates={"spec": 10, "plan": 10},
    )
    assert decision.action == "downgrade"
    updated = downgrade_models(
        {
            "implement": {
                "model": "claude-opus-5.5",
                "allowlist": ["claude-sonnet-5.5", "claude-opus-5.5"],
            }
        }
    )
    assert updated["models"]["implement"]["model"] == "claude-sonnet-5.5"
    assert updated["security_block"] is True
    assert updated["review_depth"] == "Quick"


def test_projected_overrun_and_token_cap():
    decision = project_stage(
        stage="spec",
        spent_cents=90,
        run_cap_cents=100,
        tier_cap_cents=100,
        estimate_cents=20,
        quoted_cents=0,
        quoted_output_tokens=0,
        soft_ratio=0.8,
        model="claude-haiku-5.5",
        estimates={"spec": 20, "plan": 5},
    )
    assert decision.action == "halt"
    assert decision.reason == "projected-overrun"
    assert decision.needed_cents == 25
    assert decision.max_tokens > 0
    overshoot = project_stage(
        stage="spec",
        spent_cents=0,
        run_cap_cents=100,
        tier_cap_cents=None,
        estimate_cents=1,
        quoted_cents=1,
        quoted_output_tokens=10**12,
        soft_ratio=0.99,
        model="claude-haiku-5.5",
        estimates={"spec": 1},
    )
    assert overshoot.action == "halt"
    assert overshoot.reason == "call-overshoot"


def test_halt_stops_before_spec_and_explains_the_rest(tmp_path, repo_root, capsys):
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "enabled: true\nsoft_ratio: 0.99\nestimates_cents:\n  spec: 5000\n  plan: 80\n",
        encoding="utf-8",
    )
    snapshot = tmp_path / "issue.json"
    snapshot.write_text(
        json.dumps(
            {
                "number": 9,
                "title": "Wide change",
                "body": "Touches many modules.",
                "labels": ["agent:approved"],
                "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
            }
        ),
        encoding="utf-8",
    )
    triage = tmp_path / "triage.json"
    triage.write_text(
        json.dumps(
            {
                "artifact": {
                    "classify": "enhancement",
                    "category": "enhancement",
                    "state": "ready-for-agent",
                    "risk_tier": "Deep",
                    "decision": "route",
                    "needs_human": False,
                    "confidence": 70,
                    "injection_suspected": False,
                    "summary": "Large change.",
                    "tier": "L",
                },
                "brief": "Do the wide change.",
                "usage": {"amount_cents": 1},
            }
        ),
        encoding="utf-8",
    )
    missing = tmp_path / "missing-spec.json"
    runs = tmp_path / "runs"
    code = main(
        [
            "run",
            "--issue",
            "9",
            "--issue-snapshot",
            str(snapshot),
            "--model-fixture",
            str(triage),
            "--spec-fixture",
            str(missing),
            "--budget-policy",
            str(policy),
            "--config",
            str(repo_root / "config/pipeline.config.example.yaml"),
            "--models",
            str(repo_root / "config/models.yaml"),
            "--budgets",
            str(repo_root / "config/budgets.yaml"),
            "--tiers",
            str(repo_root / "config/tiers.yaml"),
            "--runs-dir",
            str(runs),
            "--db",
            str(tmp_path / "pipeline.db"),
        ]
    )
    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert "budget: halt before spec" in captured.out
    assert not missing.exists()
    run = next(path for path in runs.iterdir() if path.is_dir())
    assert not (run / "spec.json").exists()
    summary = (run / "summary.md").read_text(encoding="utf-8")
    assert "Halted before spec" in summary
    assert "Left:" in summary
    assert "more would be needed to finish" in summary
    assert (run / "retro.md").is_file()
    metrics = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["halt_reason"] == "projected-overrun"
    assert Path(repo_root / "config/budget_policy.yaml").is_file()
