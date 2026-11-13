import json

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.summary import write_summary


def test_template_covers_a_stopped_run_and_gates_the_comment(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "triage.json").write_text(
        json.dumps(
            {
                "routing": {
                    "tier": "S",
                    "lane": "fast",
                    "escalated": True,
                    "escalation_reason": "verify",
                }
            }
        ),
        encoding="utf-8",
    )
    (run / "metrics.json").write_text(
        json.dumps({"tier": "S", "lane": "fast", "escalated": True, "stop_reason": "budget-halt"}),
        encoding="utf-8",
    )
    (run / "ledger.jsonl").write_text(
        json.dumps({"amount_cents": 25, "run_id": "run", "stage": "implement"}) + "\n",
        encoding="utf-8",
    )
    (run / "pr.json").write_text(
        json.dumps({"branch": "pipeline/run", "pr_url": None, "draft": True}),
        encoding="utf-8",
    )
    text = write_summary(
        run,
        asked="Keep the quotes",
        stop_reason="budget-halt",
        snapshot={"labels": ["bug"], "timeline": []},
    )
    assert "Keep the quotes" in text
    assert "Escalated: yes" in text
    assert "Cost so far: $0.25" in text
    assert "pipeline/run" in text
    assert "does not merge" in text
    assert not (run / "summary-comment.txt").exists()
    approved = {
        "labels": ["agent:approved"],
        "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
    }
    write_summary(run, asked="Keep the quotes", stop_reason="budget-halt", snapshot=approved)
    assert (run / "summary-comment.txt").is_file()


def test_narrative_fixture_and_the_disabled_fallback(tmp_path, repo_root, capsys):
    narrative = tmp_path / "summary.json"
    narrative.write_text(
        json.dumps({"narrative": "The parser now keeps quotes.", "usage": {"amount_cents": 1}}),
        encoding="utf-8",
    )
    snapshot = tmp_path / "issue.json"
    snapshot.write_text(
        json.dumps(
            {
                "number": 3,
                "title": "Quotes",
                "body": "Keep them.",
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
                    "classify": "bug",
                    "category": "bug",
                    "state": "ready-for-agent",
                    "risk_tier": "Quick",
                    "decision": "reject",
                    "needs_human": False,
                    "confidence": 80,
                    "injection_suspected": False,
                    "summary": "Not worth it.",
                },
                "brief": "",
                "usage": {"amount_cents": 1},
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
            str(triage),
            "--summary-fixture",
            str(narrative),
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
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert captured.out.strip().endswith("Nothing else is required unless you want a follow-up.")
    assert "The parser now keeps quotes." in captured.out
    config = repo_root / "config/pipeline.config.example.yaml"
    # The narrative switch is covered by calling the writer with no paragraph.
    plain = write_summary(tmp_path / "plain", asked="A bug", stop_reason="stop")
    assert "finished with stop" in plain
    assert "The parser now keeps quotes." not in plain
    assert config.is_file()
