import json
import sqlite3
from pathlib import Path

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.duplicates import open_run, screen


def _snapshot(number: int, author: str = "EladAriel") -> dict:
    return {
        "number": number,
        "title": "Round trip quoted text",
        "body": "Quoted text round-trips through the parser without dropping marks.",
        "author": author,
        "labels": ["agent:approved"],
        "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
    }


def _triage(path: Path) -> None:
    path.write_text(
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
                    "summary": "Parser quotes.",
                    "tier": "M",
                },
                "brief": "Keep the quotes.",
                "usage": {"amount_cents": 1},
            }
        ),
        encoding="utf-8",
    )


def _run(tmp_path, repo_root, issue, runs, extra=None):
    return [
        "run",
        "--issue",
        str(issue["number"]),
        "--issue-snapshot",
        str(_write(tmp_path / f"issue-{issue['number']}.json", issue)),
        "--model-fixture",
        str(tmp_path / "triage.json"),
        "--spec-fixture",
        str(tmp_path / "missing-spec.json"),
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
        *(extra or []),
    ]


def _write(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_clear_duplicate_halts_before_spec(tmp_path, repo_root, capsys) -> None:
    _triage(tmp_path / "triage.json")
    others = tmp_path / "open.json"
    _write(others, [{**_snapshot(4), "state": "open"}])
    issue = _snapshot(9)
    code = main(
        _run(
            tmp_path,
            repo_root,
            issue,
            tmp_path / "runs",
            ["--duplicate-issues", str(others)],
        )
    )
    out = capsys.readouterr().out
    assert code == 2
    assert "duplicate: halt" in out
    assert not (tmp_path / "missing-spec.json").exists()
    run = next(path for path in (tmp_path / "runs").iterdir() if path.is_dir())
    comment = (run / "duplicate-comment.txt").read_text(encoding="utf-8")
    assert "#4" in comment
    again = (run / "duplicate-comment.txt").read_text(encoding="utf-8")
    assert again == comment
    assert "pipeline:duplicate" in (run / "duplicate-labels.json").read_text(encoding="utf-8")
    conn = sqlite3.connect(tmp_path / "runs" / "pipeline.db")
    assert conn.execute("SELECT COUNT(*) FROM duplicate_candidates").fetchone()[0] == 1


def test_stranger_and_borderline_and_existing_run(tmp_path, repo_root, capsys) -> None:
    _triage(tmp_path / "triage.json")
    others = _write(tmp_path / "open.json", [{**_snapshot(4), "state": "open"}])
    stranger = _snapshot(9, author="someone-else")
    code = main(
        _run(
            tmp_path,
            repo_root,
            stranger,
            tmp_path / "runs-stranger",
            ["--duplicate-issues", str(others)],
        )
    )
    assert code == 2
    run = next(path for path in (tmp_path / "runs-stranger").iterdir() if path.is_dir())
    assert not (run / "duplicate-comment.txt").exists()
    capsys.readouterr()

    borderline = _write(
        tmp_path / "border.json",
        [
            {
                "number": 7,
                "state": "open",
                "title": "Cache crash",
                "body": "Investigate the crash in docs/README.md when the cache is cold.",
            }
        ],
    )
    issue = {
        "number": 11,
        "title": "Onboarding",
        "body": "Please rewrite the onboarding checklist in docs/README.md for new hires.",
        "author": "EladAriel",
        "labels": ["agent:approved"],
        "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
    }
    report = screen(issue, json.loads(borderline.read_text()), {}, fixture=None)
    assert report["halted"] is False
    assert report["borderline"]
    fixture = _write(tmp_path / "confirm.json", {"confirm": [7], "usage": {"amount_cents": 1}})
    confirmed = screen(
        issue,
        json.loads(borderline.read_text()),
        {},
        fixture=json.loads(fixture.read_text()),
    )
    assert confirmed["halted"] is True
    assert confirmed["model_called"] is True

    prior = tmp_path / "runs-open" / "old"
    prior.mkdir(parents=True)
    _write(prior / "issue.json", {"number": 12})
    _write(prior / "pr.json", {"branch": "pipeline/old", "pr_url": "local://old", "draft": True})
    fresh = _snapshot(12)
    blocked = main(_run(tmp_path, repo_root, fresh, tmp_path / "runs-open"))
    text = capsys.readouterr().out
    assert blocked == 2
    assert "already open" in text
    forced = main(_run(tmp_path, repo_root, fresh, tmp_path / "runs-open", ["--force"]))
    forced_out = capsys.readouterr().out
    assert "already open" not in forced_out
    assert forced != 0
    assert open_run(tmp_path / "runs-open", 12, "brand-new") == "local://old"
