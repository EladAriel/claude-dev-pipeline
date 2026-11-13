import json
import os
import sqlite3
import stat
from pathlib import Path

from claude_dev_pipeline.stages.pr import render_pr
from claude_dev_pipeline.stages.retro import execute_retro
from claude_dev_pipeline.stages.review import execute_review


def _finding(finding_id: str, **overrides) -> dict:
    finding = {
        "id": finding_id,
        "check": "bug-hunt",
        "file": "src/parser.py",
        "line_start": 4,
        "line_end": 4,
        "bug_class": "logic",
        "summary": f"issue {finding_id}",
        "confidence": 90,
        "security": False,
    }
    finding.update(overrides)
    return finding


def _vote(family: str, real: str, confidence: int, finding_id: str) -> dict:
    return {
        "finding_id": finding_id,
        "voter": family,
        "family": family,
        "real": real,
        "confidence": confidence,
        "severity": "important",
        "pre_existing": False,
        "evidence": "src/parser.py:4",
    }


def _install_codex(tmp_path: Path, response: dict, monkeypatch) -> None:
    payload = tmp_path / "response.json"
    payload.write_text(json.dumps(response), encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "codex"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, pathlib\n"
        "args = sys.argv[1:]\n"
        "out = args[args.index('-o') + 1]\n"
        "pathlib.Path(out).write_text(pathlib.Path(os.environ['CODEX_RESPONSE']).read_text())\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("CODEX_RESPONSE", str(payload))


def _decision(model: str = "claude-haiku-5.5") -> dict:
    return {
        "model": model,
        "evidence": "src/parser.py:4",
        "repro": "pytest tests/test_parser.py::test_quotes",
        "amount_cents": 1,
    }


def _run(tmp_path: Path, fixture: dict, **config_extra) -> Path:
    path = tmp_path / "review-fixture.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    config = {
        "review": {"max_rounds": 3, "max_nits": 3},
        "limits": {"max_diff_lines": 1500},
        "paths": {"protected": []},
        "stages": {"reconcile": {"enabled": True, "max_per_round": 3, "max_cents": 5}},
    }
    config["stages"]["reconcile"].update(config_extra)
    run = tmp_path / "runs" / "run-1"
    execute_review(
        run_dir=run,
        run_id="run-1",
        fixture_path=path,
        triage_tier="Standard",
        config=config,
        spent_cents=0,
        run_cap_cents=2000,
        review_cap_cents=150,
        codex_cap_cents=150,
        schema=Path("reviewers/codex/finding.schema.json"),
    )
    return run


def test_evidence_keeps_a_split_and_a_bad_citation_disputes_it(tmp_path, monkeypatch) -> None:
    _install_codex(
        tmp_path,
        {
            "findings": [],
            "votes": [_vote("openai", "no", 91, "keep"), _vote("openai", "no", 90, "drop")],
            "usage": {"amount_cents": 1},
        },
        monkeypatch,
    )
    run = _run(
        tmp_path,
        {
            "diff": {"files": ["src/parser.py"], "lines": 8},
            "rounds": [
                {
                    "findings": [_finding("keep"), _finding("drop")],
                    "votes": {
                        "keep": [_vote("claude", "yes", 92, "keep")],
                        "drop": [_vote("claude", "yes", 93, "drop")],
                    },
                    "tie_break": {
                        "keep": _decision(),
                        "drop": _decision(model="claude-opus-5.5"),
                    },
                    "usage": {"amount_cents": 2},
                }
            ],
        },
    )
    saved = json.loads((run / "review.json").read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in saved["findings"]}
    assert by_id["keep"]["kept"] is True
    assert by_id["keep"]["disputed"] is False
    assert by_id["keep"]["tie_break"] == "claude-haiku-5.5"
    assert by_id["drop"]["disputed"] is True
    assert by_id["drop"]["kept"] is False
    assert by_id["drop"]["blocks"] is False
    ledger = (run / "ledger.jsonl").read_text(encoding="utf-8")
    assert "tie-break" in ledger
    assert "claude-haiku-5.5" in ledger
    (run / "spec.json").write_text(json.dumps({"title": "App"}), encoding="utf-8")
    (run / "plan.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")
    _title, body, _cost = render_pr(run, issue_number=None, usage_cents=0)
    assert "drop" in body
    assert "not blocking" in body


def test_security_disagreement_still_blocks(tmp_path, monkeypatch) -> None:
    _install_codex(
        tmp_path,
        {"findings": [], "votes": [_vote("openai", "no", 95, "sec")], "usage": {"amount_cents": 1}},
        monkeypatch,
    )
    run = _run(
        tmp_path,
        {
            "diff": {"files": ["src/parser.py"], "lines": 4},
            "rounds": [
                {
                    "findings": [_finding("sec", security=True, bug_class="injection")],
                    "votes": {"sec": [_vote("claude", "yes", 96, "sec")]},
                    "tie_break": {"sec": _decision()},
                    "usage": {"amount_cents": 1},
                }
            ],
        },
    )
    saved = json.loads((run / "review.json").read_text(encoding="utf-8"))
    finding = saved["findings"][0]
    assert finding["blocks"] is True
    assert finding["disputed"] is True
    assert saved["security_block"] is True


def test_cap_sqlite_and_unapplied_threshold_proposal(tmp_path, monkeypatch) -> None:
    _install_codex(
        tmp_path,
        {
            "findings": [],
            "votes": [
                _vote("openai", "no", 90, "one"),
                _vote("openai", "no", 90, "two"),
            ],
            "usage": {"amount_cents": 1},
        },
        monkeypatch,
    )
    config_text = Path("config/pipeline.config.example.yaml").read_text(encoding="utf-8")
    run = _run(
        tmp_path,
        {
            "diff": {"files": ["src/parser.py"], "lines": 4},
            "rounds": [
                {
                    "findings": [_finding("one"), _finding("two")],
                    "votes": {
                        "one": [_vote("claude", "yes", 90, "one")],
                        "two": [_vote("claude", "yes", 90, "two")],
                    },
                    "tie_break": {"one": _decision(), "two": _decision()},
                    "usage": {"amount_cents": 1},
                }
            ],
        },
        max_per_round=1,
    )
    saved = json.loads((run / "review.json").read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in saved["findings"]}
    assert by_id["one"]["tie_break"] == "claude-haiku-5.5"
    assert by_id["two"]["disputed"] is True
    assert by_id["two"].get("tie_break") in (None, "")
    conn = sqlite3.connect(tmp_path / "runs" / "pipeline.db")
    misses = conn.execute("SELECT SUM(false_positive) FROM model_agreement").fetchone()[0]
    assert misses == 1
    execute_retro(
        run_dir=run,
        run_id="run-1",
        caps={},
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=10,
        stop_reason="success",
        fixture_path=None,
    )
    retro = json.loads((run / "retro.json").read_text(encoding="utf-8"))
    assert retro["applied"] is False
    assert retro["items"][0]["id"] == "reconcile-threshold"
    assert retro["items"][0]["applied"] is False
    assert Path("config/pipeline.config.example.yaml").read_text(encoding="utf-8") == config_text


def test_kill_switch_leaves_the_vote_unchanged(tmp_path, monkeypatch) -> None:
    _install_codex(
        tmp_path,
        {"findings": [], "votes": [_vote("openai", "no", 99, "f1")], "usage": {}},
        monkeypatch,
    )
    run = _run(
        tmp_path,
        {
            "diff": {"files": ["src/parser.py"], "lines": 2},
            "rounds": [
                {
                    "findings": [_finding("f1")],
                    "votes": {"f1": [_vote("claude", "yes", 99, "f1")]},
                    "tie_break": {"f1": _decision()},
                    "usage": {"amount_cents": 1},
                }
            ],
        },
        enabled=False,
    )
    saved = json.loads((run / "review.json").read_text(encoding="utf-8"))
    assert saved["findings"][0]["kept"] is False
    assert saved["findings"][0].get("disputed") is not True
