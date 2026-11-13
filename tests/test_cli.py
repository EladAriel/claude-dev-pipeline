import json

from tests.conftest import repo_root

from claude_dev_pipeline.cli import main


def _paths():
    root = repo_root()
    return [
        "--config",
        str(root / "config/pipeline.config.example.yaml"),
        "--models",
        str(root / "config/models.yaml"),
        "--budgets",
        str(root / "config/budgets.yaml"),
    ]


def test_dry_run_prints_the_plan_without_recording(tmp_path, capsys):
    code = main(["--dry-run", *_paths()])
    out = capsys.readouterr().out
    assert code == 0
    assert "mode: dry-run" in out
    assert "api-calls: none" in out
    assert "key=retro" in out
    assert "key=record-metrics" in out
    assert "reconcile" not in out.lower()
    assert "https://api.githubcopilot.com/mcp/x/pull_requests" in out
    assert "https://mcp.atlassian.com/v2/mcp" in out
    assert "name=code_graph enabled=false transport=sandbox-cli mcp=false" in out
    assert "run-cap: $20.00" in out
    assert not (tmp_path / "runs").exists()


def test_run_without_intake_is_rejected(capsys):
    assert main(["run"]) == 2


def test_task_intake_then_status(tmp_path, capsys):
    db_path = tmp_path / "pipeline.db"
    runs = tmp_path / "runs"
    code = main(["run", "add a parser", "--runs-dir", str(runs), "--db", str(db_path), *_paths()])
    out = capsys.readouterr().out
    assert code == 0
    assert "intake: task" in out
    assert "pr: none" in out
    assert "api-calls: none" in out
    capsys.readouterr()
    assert main(["status", "--db", str(db_path)]) == 0
    status = capsys.readouterr().out
    assert "status: dry-run" in status
    assert "ref: add a parser" in status
    assert "pr: none" in status


def test_issue_without_approval_does_not_record(tmp_path, capsys):
    snapshot = tmp_path / "issue.json"
    snapshot.write_text(
        json.dumps(
            {
                "number": 7,
                "labels": ["bug"],
                "timeline": [{"event": "labeled", "label": "bug", "actor": "someone"}],
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "pipeline.db"
    code = main(
        [
            "run",
            "--issue",
            "7",
            "--issue-snapshot",
            str(snapshot),
            "--db",
            str(db_path),
            "--runs-dir",
            str(tmp_path / "runs"),
        ]
    )
    err = capsys.readouterr().err
    assert code == 1
    assert "agent:approved" in err
    assert not db_path.exists()


def test_approved_issue_is_admitted(tmp_path, capsys):
    snapshot = tmp_path / "issue.json"
    snapshot.write_text(
        json.dumps(
            {
                "number": 7,
                "labels": ["agent:approved"],
                "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "pipeline.db"
    code = main(
        [
            "run",
            "--issue",
            "7",
            "--issue-snapshot",
            str(snapshot),
            "--db",
            str(db_path),
            "--runs-dir",
            str(tmp_path / "runs"),
        ]
    )
    assert code == 0
    assert "intake: issue" in capsys.readouterr().out


def test_import_is_dry_run_until_apply(tmp_path, capsys):
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "tasks": [
                    {"id": "01", "title": "Add parser", "body": "Parse tokens.", "blocked_by": []},
                    {"id": "02", "title": "Add printer", "blocked_by": ["01"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    outbox = tmp_path / "outbox.json"
    assert main(["import", str(plan), "--outbox", str(outbox)]) == 0
    dry = capsys.readouterr().out
    assert "mode: dry-run" in dry
    assert "labels=pipeline:queued" in dry
    assert not outbox.exists()
    assert main(["import", str(plan), "--apply", "--outbox", str(outbox)]) == 0
    saved = json.loads(outbox.read_text(encoding="utf-8"))
    assert saved["network"] is False
    assert "```json" in saved["issues"][0]["body"]
    assert "pipeline_task" in saved["issues"][0]["body"]
    assert saved["issues"][0]["labels"] == ["pipeline:queued"]
    assert "<!-- pipeline-status -->" in saved["issues"][0]["status_comment"]


def test_issue_status_updates_the_marker_comment(tmp_path, capsys):
    comments = tmp_path / "comments.json"
    comments.write_text(
        json.dumps([{"id": 9, "body": "<!-- pipeline-status -->\nold"}]),
        encoding="utf-8",
    )
    outbox = tmp_path / "status.json"
    code = main(
        [
            "issue-status",
            "--issue",
            "12",
            "--set",
            "review",
            "--comments",
            str(comments),
            "--apply",
            "--outbox",
            str(outbox),
        ]
    )
    assert code == 0
    assert "comment: update 9" in capsys.readouterr().out
    saved = json.loads(outbox.read_text(encoding="utf-8"))
    assert saved["network"] is False
    assert saved["labels_add"] == ["pipeline:review"]
    assert "pipeline:queued" in saved["labels_remove"]
    assert "pipeline:review" not in saved["labels_remove"]
    assert "<!-- pipeline-status -->" in saved["comment_body"]
