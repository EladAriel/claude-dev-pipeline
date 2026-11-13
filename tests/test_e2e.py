import json
import os
import stat
import subprocess
from pathlib import Path

import yaml

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.db import connect


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _codex(tmp_path: Path, monkeypatch) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "codex"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "out = sys.argv[sys.argv.index('-o') + 1]\n"
        "Path(out).write_text("
        '\'{"findings": [], "votes": [], "usage": {"input_tokens": 2, "output_tokens": 1, '
        '"amount_cents": 1}}\')\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_offline_pipeline_reaches_retro(tmp_path, monkeypatch, repo_root, capsys):
    _codex(tmp_path, monkeypatch)
    repo = tmp_path / "target"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    config = yaml.safe_load((repo_root / "config/pipeline.config.example.yaml").read_text())
    config["checks"] = {"test": "true", "lint": "", "typecheck": "", "build": ""}
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    snapshot = tmp_path / "issue.json"
    _write(
        snapshot,
        {
            "number": 12,
            "title": "Parser drops quotes",
            "body": "Keep the quotes.",
            "labels": ["agent:approved"],
            "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
        },
    )
    triage = tmp_path / "triage.json"
    _write(
        triage,
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
                "summary": "Quotes are dropped.",
            },
            "brief": "Fix quote handling.",
            "usage": {"amount_cents": 1, "input_tokens": 5, "output_tokens": 2},
        },
    )
    spec = tmp_path / "spec.json"
    _write(
        spec,
        {
            "artifact": {
                "title": "Keep quotes",
                "acceptance_criteria": [{"id": "AC-1", "statement": "Quoted input round-trips."}],
                "open_questions": [],
                "needs_human": False,
                "seams": ["parser.parse"],
                "e2e_scenarios": [{"id": "E2E-1", "scenario": "Parse a quoted string."}],
            },
            "spec_md": "# Spec\n\nAC-1: Quoted input round-trips.\n",
            "usage": {"amount_cents": 1},
        },
    )
    plan = tmp_path / "plan.json"
    _write(
        plan,
        {
            "artifact": {
                "tasks": [
                    {
                        "id": "T1",
                        "title": "Parse quotes",
                        "acceptance_criteria": ["AC-1"],
                        "files": ["parser.py"],
                        "seams": ["parser.parse"],
                        "test_plan": "A quoted string round-trips.",
                        "blocked_by": [],
                        "estimate": "S",
                        "suggested_model": "claude-sonnet-5.5",
                    }
                ]
            },
            "usage": {"amount_cents": 1},
        },
    )
    implement = tmp_path / "implement.json"
    _write(
        implement,
        {
            "tickets": {
                "T1": {
                    "files": [
                        {"path": "parser.py", "content": "def parse(text):\n    return text\n"}
                    ],
                    "tests_added": ["test_parser.py"],
                    "test_command": "true",
                    "test_result": {"exit_code": 0},
                    "status": "done",
                    "usage": {"amount_cents": 1, "input_tokens": 4, "output_tokens": 1},
                }
            }
        },
    )
    review = tmp_path / "review.json"
    _write(
        review,
        {
            "diff": {"files": ["parser.py"], "lines": 2},
            "rounds": [{"findings": [], "votes": {}, "usage": {"amount_cents": 1}}],
        },
    )
    retro = tmp_path / "retro.json"
    _write(
        retro,
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
            "usage": {"amount_cents": 1},
        },
    )
    runs = tmp_path / "runs"
    db = tmp_path / "pipeline.db"
    code = main(
        [
            "run",
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
            "--implement-fixture",
            str(implement),
            "--review-fixture",
            str(review),
            "--retro-fixture",
            str(retro),
            "--target-repo",
            str(repo),
            "--no-push",
            "--config",
            str(config_path),
            "--models",
            str(repo_root / "config/models.yaml"),
            "--budgets",
            str(repo_root / "config/budgets.yaml"),
            "--runs-dir",
            str(runs),
            "--db",
            str(db),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err + captured.out
    assert "mode: pr-rendered" in captured.out
    assert "retro-applied: false" in captured.out
    run = next(path for path in runs.iterdir() if path.is_dir())
    for name in (
        "triage.json",
        "spec.json",
        "plan.json",
        "verify.json",
        "review.json",
        "pr.json",
        "pr-body.md",
        "retro.md",
        "metrics.json",
    ):
        assert (run / name).is_file(), name
    assert list(run.glob("impl-*.json"))
    body = (run / "pr-body.md").read_text(encoding="utf-8")
    assert "Fixes #12" in body
    assert "T1: Parse quotes" in body
    assert "Not applied." in (run / "retro.md").read_text(encoding="utf-8")
    assert "Add a parser check" not in (repo / "parser.py").read_text(encoding="utf-8")
    total = sum(
        int(json.loads(line)["amount_cents"])
        for line in (run / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    assert total <= 2000
    saved = connect(db)
    assert saved.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert saved.execute("SELECT COUNT(*) FROM run_metrics").fetchone()[0] == 1
    assert saved.execute("SELECT COUNT(*) FROM retro_items").fetchone()[0] == 1
    metrics = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["stop_reason"] == "success"
    assert metrics["verify_pass"] is True
