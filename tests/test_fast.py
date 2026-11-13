import json

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.lanes import (
    diff_stats,
    escalate_for_diff,
    mark_escalated,
    touches_security,
    write_fast_review,
    write_synthesized,
)
from claude_dev_pipeline.stages.review import execute_review


def _issue() -> dict:
    return {"title": "Keep quotes", "body": "Quoted text round-trips."}


def test_fast_lane_writes_one_ticket_and_skips_spec(tmp_path, repo_root, capsys):
    snapshot = {
        "number": 8,
        "title": "Keep quotes",
        "body": "Quoted text round-trips.",
        "labels": ["agent:approved"],
        "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
    }
    issue = tmp_path / "issue.json"
    issue.write_text(json.dumps(snapshot), encoding="utf-8")
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
                    "confidence": 90,
                    "injection_suspected": False,
                    "summary": "Small fix.",
                    "tier": "S",
                },
                "brief": "Keep the quotes.",
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
            "8",
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
    captured = capsys.readouterr()
    assert code == 0, captured.err + captured.out
    assert "fast-lane: skipped spec and plan" in captured.out
    assert not missing.exists()
    plan = json.loads(next((tmp_path / "runs").rglob("plan.json")).read_text(encoding="utf-8"))
    assert plan["tasks"][0]["id"] == "T1"
    assert plan["tasks"][0]["suggested_model"] == "claude-sonnet-5.5"


def test_diff_and_security_policy(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    write_synthesized(run, _issue())
    (run / "triage.json").write_text(
        json.dumps({"routing": {"lane": "fast", "escalated": False, "tier": "S"}}),
        encoding="utf-8",
    )
    work = tmp_path / "work"
    work.mkdir()
    (work / "auth.py").write_text("token = 1\n" * 10, encoding="utf-8")
    (run / "impl-T1.json").write_text(
        json.dumps(
            {"files_changed": ["src/auth/login.py"], "worktree": str(work), "status": "done"}
        ),
        encoding="utf-8",
    )
    # The changed path is recorded even when the worktree file name differs.
    files, _lines = diff_stats(run)
    assert touches_security(files, ["**/auth/**"])
    assert escalate_for_diff(["a.py"] * 4, 10, {"max_files": 3, "max_lines": 200}) == "standard"
    mark_escalated(run, "standard", "diff")
    saved = json.loads((run / "triage.json").read_text(encoding="utf-8"))
    assert saved["routing"]["escalated"] is True
    assert saved["routing"]["escalation_reason"] == "diff"
    plan = write_fast_review(run, {"fast": {"security_globs": ["**/auth/**"]}})
    assert plan["codex"] is True
    assert "security" in plan["only_checks"]


def test_bug_hunt_drops_other_checks_and_security_still_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    fixture = tmp_path / "review.json"
    fixture.write_text(
        json.dumps(
            {
                "diff": {"files": ["src/auth/login.py"], "lines": 4},
                "rounds": [
                    {
                        "findings": [
                            {
                                "id": "style",
                                "check": "simplify",
                                "file": "src/a.py",
                                "bug_class": "style",
                                "summary": "rename it",
                                "confidence": 90,
                            },
                            {
                                "id": "sec",
                                "check": "security",
                                "file": "src/auth/login.py",
                                "bug_class": "injection",
                                "summary": "query is built from input",
                                "confidence": 95,
                                "security": True,
                            },
                        ],
                        "votes": {
                            "sec": [
                                {
                                    "family": "claude",
                                    "real": "yes",
                                    "confidence": 90,
                                    "severity": "important",
                                    "pre_existing": False,
                                    "evidence": "src/auth/login.py:1",
                                }
                            ]
                        },
                        "usage": {"amount_cents": 1},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = execute_review(
        run_dir=tmp_path / "out",
        run_id="run-fast",
        fixture_path=fixture,
        triage_tier="Quick",
        config={"review": {"max_rounds": 3, "max_nits": 3}, "limits": {"max_diff_lines": 1500}},
        spent_cents=0,
        run_cap_cents=2000,
        review_cap_cents=150,
        codex_cap_cents=150,
        codex_enabled=False,
        only_checks=["bug-hunt", "security"],
    )
    saved = json.loads((tmp_path / "out" / "review.json").read_text(encoding="utf-8"))
    ids = [item["id"] for item in saved["findings"]]
    assert "style" not in ids
    assert result.security_block is True
