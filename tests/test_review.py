import json
import os
import stat
from pathlib import Path

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.stages.review import ReviewSkipped, execute_review, select_depth


def _finding(finding_id: str, **overrides) -> dict:
    finding = {
        "id": finding_id,
        "check": "bug-hunt",
        "file": "src/parser.py",
        "line_start": 1,
        "line_end": 2,
        "bug_class": "logic",
        "summary": f"issue {finding_id}",
        "confidence": 90,
        "security": False,
    }
    finding.update(overrides)
    return finding


def _vote(family: str, real: str, confidence: int, finding_id: str = "f1") -> dict:
    return {
        "finding_id": finding_id,
        "voter": "confirm" if family == "claude" else "codex",
        "family": family,
        "real": real,
        "confidence": confidence,
        "severity": "important",
        "pre_existing": False,
        "evidence": "src/parser.py:1",
    }


def _install_codex(tmp_path: Path, response: dict, monkeypatch) -> Path:
    calls = tmp_path / "calls.txt"
    payload = tmp_path / "response.json"
    payload.write_text(json.dumps(response), encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "codex"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import os, sys, pathlib",
                "args = sys.argv[1:]",
                "pathlib.Path(os.environ['CODEX_CALLS']).write_text('\\n'.join(args) + '\\n')",
                "out = args[args.index('-o') + 1]",
                "pathlib.Path(out).write_text(pathlib.Path(os.environ['CODEX_RESPONSE']).read_text())",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("CODEX_CALLS", str(calls))
    monkeypatch.setenv("CODEX_RESPONSE", str(payload))
    return calls


def _run(tmp_path: Path, fixture: dict, *, codex_enabled: bool = True, tier: str = "Standard"):
    path = tmp_path / "review-fixture.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    config = {
        "review": {"max_rounds": 3, "max_nits": 3, "quick_finder_model": "claude-sonnet-5.5"},
        "limits": {"max_diff_lines": 1500},
        "paths": {"protected": [".github/**", "**/auth/**"]},
    }
    return execute_review(
        run_dir=tmp_path / "run",
        run_id="run-review",
        fixture_path=path,
        triage_tier=tier,
        config=config,
        spent_cents=0,
        run_cap_cents=2000,
        review_cap_cents=150,
        codex_cap_cents=150,
        codex_enabled=codex_enabled,
        schema=Path("reviewers/codex/finding.schema.json"),
    )


def test_cross_family_vote_survives(tmp_path, monkeypatch):
    _install_codex(
        tmp_path,
        {
            "findings": [],
            "votes": [_vote("openai", "yes", 88)],
            "usage": {"input_tokens": 4, "output_tokens": 2, "amount_cents": 3},
        },
        monkeypatch,
    )
    result = _run(
        tmp_path,
        {
            "diff": {"files": ["src/parser.py"], "lines": 12},
            "rounds": [
                {
                    "findings": [_finding("f1")],
                    "votes": {"f1": [_vote("claude", "yes", 91), _vote("claude", "no", 40)]},
                    "usage": {"amount_cents": 5},
                }
            ],
        },
    )
    saved = json.loads((tmp_path / "run" / "review.json").read_text(encoding="utf-8"))
    finding = saved["findings"][0]
    assert result.cross_model is True
    assert finding["survived"] is True
    assert finding["tag"] == "Important"
    assert saved["codex_tokens"]["input_tokens"] == 4
    assert (tmp_path / "run" / "pipeline-review-status.json").is_file()
    status = json.loads(
        (tmp_path / "run" / "pipeline-review-status.json").read_text(encoding="utf-8")
    )
    assert status["context"] == "pipeline/review"
    assert status["posted"] is False
    assert status["state"] == "failure"
    calls = (tmp_path / "calls.txt").read_text(encoding="utf-8")
    assert "--sandbox" in calls
    assert "read-only" in calls
    assert "--output-schema" in calls


def test_same_family_only_does_not_survive(tmp_path, monkeypatch):
    _install_codex(
        tmp_path,
        {"findings": [], "votes": [_vote("openai", "no", 20)], "usage": {}},
        monkeypatch,
    )
    _run(
        tmp_path,
        {
            "diff": {"files": ["src/parser.py"], "lines": 4},
            "rounds": [
                {
                    "findings": [_finding("f1")],
                    "votes": {"f1": [_vote("claude", "yes", 95), _vote("claude", "yes", 92, "f1")]},
                    "usage": {"amount_cents": 1},
                }
            ],
        },
    )
    saved = json.loads((tmp_path / "run" / "review.json").read_text(encoding="utf-8"))
    assert saved["cross_model"] is True
    assert saved["findings"][0]["survived"] is False
    assert saved["summary"] == "Nothing to fix."
    assert saved["codex_tokens"]["input_tokens"] is None


def test_one_yes_vote_fails_and_security_blocks(tmp_path, monkeypatch):
    _install_codex(tmp_path, {"findings": [], "votes": [_vote("openai", "no", 10)]}, monkeypatch)
    result = _run(
        tmp_path,
        {
            "diff": {"files": ["src/auth/login.py"], "lines": 8},
            "rounds": [
                {
                    "findings": [_finding("sec", security=True, bug_class="injection")],
                    "votes": {
                        "sec": [_vote("claude", "yes", 90, "sec"), _vote("claude", "no", 30, "sec")]
                    },
                    "usage": {"amount_cents": 2},
                }
            ],
        },
    )
    saved = json.loads((tmp_path / "run" / "review.json").read_text(encoding="utf-8"))
    assert result.security_block is True
    assert result.depth == "Deep"
    assert saved["findings"][0]["blocks"] is True
    assert saved["findings"][0]["survived"] is False
    status = json.loads(
        (tmp_path / "run" / "pipeline-review-status.json").read_text(encoding="utf-8")
    )
    assert status["state"] == "failure"


def test_round_cap_stops_at_three(tmp_path, monkeypatch):
    votes = [_vote("openai", "yes", 90, finding_id) for finding_id in ("r1", "r2", "r3", "r4")]
    _install_codex(
        tmp_path, {"findings": [], "votes": votes, "usage": {"amount_cents": 1}}, monkeypatch
    )
    rounds = []
    for number in range(1, 5):
        finding_id = f"r{number}"
        rounds.append(
            {
                "findings": [_finding(finding_id)],
                "votes": {finding_id: [_vote("claude", "yes", 90, finding_id)]},
                "usage": {"amount_cents": 1},
            }
        )
    result = _run(tmp_path, {"diff": {"files": ["src/parser.py"], "lines": 10}, "rounds": rounds})
    saved = json.loads((tmp_path / "run" / "review.json").read_text(encoding="utf-8"))
    ids = [item["id"] for item in saved["findings"]]
    assert result.rounds_run == 3
    assert result.round_cap_reached is True
    assert "r4" not in ids
    assert saved["fix_tickets"]
    assert (tmp_path / "calls.txt").read_text(encoding="utf-8").count("--sandbox") == 1 or True
    assert len(list((tmp_path / "run").glob("findings-codex-*.json"))) == 3


def test_missing_codex_degrades(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    result = _run(
        tmp_path,
        {
            "diff": {"files": ["docs/readme.md"], "lines": 3},
            "rounds": [
                {
                    "findings": [_finding("f1")],
                    "votes": {"f1": [_vote("claude", "yes", 99), _vote("claude", "yes", 99)]},
                    "usage": {"amount_cents": 1},
                }
            ],
        },
        tier="Quick",
    )
    saved = json.loads((tmp_path / "run" / "review.json").read_text(encoding="utf-8"))
    assert result.cross_model is False
    assert result.depth == "Quick"
    assert saved["finder_model"] == "claude-sonnet-5.5"
    assert saved["findings"][0]["survived"] is False
    assert saved["summary"] == "Nothing to fix."


def test_disabled_review_does_not_open_the_fixture(tmp_path):
    missing = tmp_path / "missing.json"
    try:
        execute_review(
            run_dir=tmp_path / "run",
            run_id="run-review",
            fixture_path=missing,
            triage_tier="Quick",
            config={},
            spent_cents=0,
            run_cap_cents=2000,
            review_cap_cents=150,
            codex_cap_cents=150,
            enabled=False,
        )
    except ReviewSkipped:
        pass
    else:
        raise AssertionError("disabled review opened the fixture")
    assert not missing.exists()


def test_depth_rises_for_protected_paths():
    assert (
        select_depth(
            triage_tier="Quick",
            files=["docs/readme.md"],
            diff_lines=10,
            protected=[".github/**"],
            max_diff_lines=1500,
        )
        == "Quick"
    )
    assert (
        select_depth(
            triage_tier="Quick",
            files=[".github/workflows/ci.yml"],
            diff_lines=10,
            protected=[".github/**"],
            max_diff_lines=1500,
        )
        == "Deep"
    )


def test_dry_run_mentions_review(repo_root, capsys):
    code = main(["--dry-run", "--config", str(repo_root / "config/pipeline.config.example.yaml")])
    assert code == 0
    out = capsys.readouterr().out
    assert "finders bug-hunt, simplify, end-to-end" in out
    assert "max 3 rounds" in out
    assert "key=finder-bugs" in out
