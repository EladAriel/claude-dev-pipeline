import json

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.stages.spec import SpecError, validate_artifact


def _snapshot(**extra) -> dict:
    base = {
        "number": 6,
        "title": "Broken",
        "body": "",
        "author": "EladAriel",
        "labels": ["agent:approved"],
        "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
    }
    base.update(extra)
    return base


def _triage() -> dict:
    return {
        "artifact": {
            "classify": "bug",
            "category": "bug",
            "state": "ready-for-agent",
            "risk_tier": "Standard",
            "decision": "route",
            "needs_human": False,
            "confidence": 60,
            "injection_suspected": False,
            "summary": "Parser bug.",
            "tier": "M",
        },
        "brief": "Fix the parser.",
        "usage": {"amount_cents": 1},
    }


def _run(tmp_path, repo_root, snapshot, *, inline=False, extra=None):
    issue = tmp_path / "issue.json"
    issue.write_text(json.dumps(snapshot), encoding="utf-8")
    triage = tmp_path / "triage.json"
    triage.write_text(json.dumps(_triage()), encoding="utf-8")
    missing = tmp_path / "missing-spec.json"
    argv = [
        "run",
        "--issue",
        "6",
        "--issue-snapshot",
        str(issue),
        "--model-fixture",
        str(triage),
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
        "--clarity",
        str(repo_root / "config/clarity.yaml"),
        "--runs-dir",
        str(tmp_path / "runs"),
        "--db",
        str(tmp_path / "pipeline.db"),
    ]
    if inline:
        argv.append("--ask-inline")
    if extra:
        argv.extend(extra)
    return main(argv), missing


def test_low_clarity_skips_spec_and_posts_one_comment(tmp_path, repo_root, capsys):
    code, missing = _run(tmp_path, repo_root, _snapshot())
    captured = capsys.readouterr()
    assert code == 2, captured.out + captured.err
    assert not missing.exists()
    run = next((tmp_path / "runs").iterdir())
    assert not (run / "spec.json").exists()
    comment = (run / "clarity-comment.txt").read_text(encoding="utf-8")
    assert comment.count("?") <= 3
    assert "pipeline:needs-info" in (run / "clarity-labels.json").read_text(encoding="utf-8")
    summary = (run / "summary.md").read_text(encoding="utf-8")
    assert "What is missing" in summary
    metrics = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["clarity_halted"] is True
    assert metrics["clarity_score"] < 75
    again = (run / "clarity-comment.txt").read_text(encoding="utf-8")
    assert again == comment


def test_inline_skips_the_comment_and_a_stranger_is_not_posted(tmp_path, repo_root, capsys):
    code, _missing = _run(tmp_path, repo_root, _snapshot(), inline=True)
    assert code == 2
    run = next((tmp_path / "runs").iterdir())
    assert not (run / "clarity-comment.txt").exists()
    assert "clarity-question:" in capsys.readouterr().out
    stranger = tmp_path / "other"
    stranger.mkdir()
    code, _missing = _run(stranger, repo_root, _snapshot(author="someone-else"))
    assert code == 2
    other = next((stranger / "runs").iterdir())
    assert not (other / "clarity-comment.txt").exists()


def test_updated_issue_resumes_into_spec(tmp_path, repo_root, capsys):
    snapshot = _snapshot()
    code, missing = _run(tmp_path, repo_root, snapshot)
    assert code == 2
    run = next((tmp_path / "runs").iterdir())
    snapshot["body"] = (
        "Goal: keep quotes. Acceptance: done when quotes round-trip. "
        "Repro steps: when I pass a quoted token it drops them. "
        "Scope: only the lexer."
    )
    (tmp_path / "issue.json").write_text(json.dumps(snapshot), encoding="utf-8")
    missing.write_text(
        json.dumps(
            {
                "artifact": {
                    "title": "Keep quotes",
                    "acceptance_criteria": [{"id": "AC-1", "statement": "Quotes round-trip."}],
                    "open_questions": [],
                    "needs_human": False,
                    "seams": ["parser.parse"],
                    "e2e_scenarios": [{"id": "E2E-1", "scenario": "Parse quotes."}],
                    "assumptions": [],
                },
                "spec_md": "# Spec\n\nAC-1: Quotes round-trip.\n",
                "usage": {"amount_cents": 1},
            }
        ),
        encoding="utf-8",
    )
    resumed = main(
        [
            "resume",
            run.name,
            "--runs-dir",
            str(tmp_path / "runs"),
            "--db",
            str(tmp_path / "db2"),
        ]
    )
    assert resumed == 0, capsys.readouterr().err
    assert (run / "spec.json").is_file()
    assert "Owner answers:" in (run / "agent-brief.md").read_text(encoding="utf-8")
    assert missing.exists()


def test_spec_rejects_a_guessed_assumption():
    artifact = {
        "acceptance_criteria": [{"id": "AC-1", "statement": "Quotes round-trip."}],
        "open_questions": [],
        "needs_human": False,
        "seams": ["parser.parse"],
        "e2e_scenarios": [{"id": "E2E-1", "scenario": "Parse quotes."}],
        "assumptions": [{"id": "A-1", "statement": "Quotes are double quotes.", "explicit": True}],
    }
    spec_md = "# Spec\n\nAC-1: Quotes round-trip.\n\nAssumption: Quotes are double quotes.\n"
    try:
        validate_artifact(artifact, spec_md)
    except SpecError as exc:
        assert "question" in str(exc)
    else:
        raise AssertionError("an unconfirmed assumption was accepted")
    artifact["open_questions"] = [
        {"id": "Q-1", "question": "Are quotes double quotes only?", "blocks": False}
    ]
    # The question must name the assumption statement or its id.
    artifact["open_questions"][0]["question"] = "Confirm A-1"
    validate_artifact(artifact, spec_md)
