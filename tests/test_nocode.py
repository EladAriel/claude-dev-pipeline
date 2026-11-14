import json
from pathlib import Path

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.nocode import classify


def _approved(number: int, title: str, body: str, author: str = "EladAriel") -> dict:
    return {
        "number": number,
        "title": title,
        "body": body,
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
                    "confidence": 80,
                    "injection_suspected": False,
                    "summary": "Question.",
                    "tier": "M",
                },
                "brief": "Answer it.",
                "usage": {"amount_cents": 1},
            }
        ),
        encoding="utf-8",
    )


def _argv(
    tmp: Path, repo_root: Path, issue: Path, number: int, runs: Path, extra: list[str]
) -> list[str]:
    return [
        "run",
        "--issue",
        str(number),
        "--issue-snapshot",
        str(issue),
        "--model-fixture",
        str(tmp / "triage.json"),
        "--spec-fixture",
        str(tmp / "missing-spec.json"),
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
        str(tmp / "pipeline.db"),
        *extra,
    ]


def test_question_writes_an_answer_and_opens_no_pr(tmp_path: Path, repo_root, capsys) -> None:
    assert classify({"title": "Question", "body": "How does the parser work?"}, {})
    assert classify({"title": "Bug", "body": "Fix src/app.py"}, {}) is None
    _triage(tmp_path / "triage.json")
    issue = tmp_path / "issue.json"
    issue.write_text(
        json.dumps(_approved(50, "Question", "How does the parser work?")),
        encoding="utf-8",
    )
    code = main(_argv(tmp_path, repo_root, issue, 50, tmp_path / "runs", []))
    out = capsys.readouterr().out
    assert code == 0
    assert "nocode: answer" in out
    run = next(path for path in (tmp_path / "runs").iterdir() if path.is_dir())
    assert (run / "answer.md").is_file()
    assert (run / "nocode-comment.txt").is_file()
    assert not (run / "pr.json").exists()
    assert not (tmp_path / "missing-spec.json").exists()

    stranger = tmp_path / "stranger.json"
    stranger.write_text(
        json.dumps(_approved(51, "Research", "Research the cache.", author="someone-else")),
        encoding="utf-8",
    )
    main(_argv(tmp_path, repo_root, stranger, 51, tmp_path / "runs-stranger", []))
    other = next(path for path in (tmp_path / "runs-stranger").iterdir() if path.is_dir())
    assert not (other / "nocode-comment.txt").exists()


def test_code_conclusion_needs_approval(tmp_path: Path, repo_root, capsys) -> None:
    _triage(tmp_path / "triage.json")
    issue = tmp_path / "issue.json"
    issue.write_text(
        json.dumps(_approved(52, "Design discussion", "Design discussion for the cache.")),
        encoding="utf-8",
    )
    fixture = tmp_path / "answer.json"
    fixture.write_text(
        json.dumps(
            {
                "answer": "A code change is required.",
                "needs_code": True,
                "usage": {"amount_cents": 1},
            }
        ),
        encoding="utf-8",
    )
    stopped = main(
        _argv(
            tmp_path,
            repo_root,
            issue,
            52,
            tmp_path / "runs-stop",
            ["--answer-fixture", str(fixture)],
        )
    )
    assert stopped == 2
    assert "approval required" in capsys.readouterr().out
    continued = main(
        _argv(
            tmp_path,
            repo_root,
            issue,
            52,
            tmp_path / "runs-go",
            ["--answer-fixture", str(fixture), "--approve-nocode"],
        )
    )
    captured = capsys.readouterr()
    assert continued != 0
    assert "nocode: escalated" in captured.out
    assert "spec" in captured.err.lower() or "model fixture" in captured.err.lower()
