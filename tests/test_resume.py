import json
import subprocess
from pathlib import Path

from claude_dev_pipeline.cli import main


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    _git(path, "config", "user.email", "dev@example.com")
    _git(path, "config", "user.name", "Dev")
    (path / "README.md").write_text("base\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "base")
    return path


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _fixtures(tmp: Path) -> dict[str, Path]:
    issue = _write(
        tmp / "issue.json",
        {
            "number": 4,
            "title": "Keep quotes",
            "body": "Quoted text round-trips.",
            "labels": ["agent:approved"],
            "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
        },
    )
    triage = _write(
        tmp / "triage.json",
        {
            "artifact": {
                "classify": "bug",
                "category": "bug",
                "state": "ready-for-agent",
                "risk_tier": "Standard",
                "decision": "route",
                "needs_human": False,
                "confidence": 90,
                "injection_suspected": False,
                "summary": "Quotes drop.",
                "tier": "M",
            },
            "brief": "Keep quotes.",
            "usage": {"amount_cents": 1},
        },
    )
    spec = _write(
        tmp / "spec.json",
        {
            "artifact": {
                "title": "Keep quotes",
                "acceptance_criteria": [{"id": "AC-1", "statement": "Quotes round-trip."}],
                "open_questions": [],
                "needs_human": False,
                "seams": ["parser.parse"],
                "e2e_scenarios": [{"id": "E2E-1", "scenario": "Parse quotes."}],
            },
            "spec_md": "# Spec\n\nAC-1: Quotes round-trip.\n",
            "usage": {"amount_cents": 1},
        },
    )
    plan = _write(
        tmp / "plan.json",
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
    implement = _write(
        tmp / "implement.json",
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
                    "usage": {"amount_cents": 1},
                }
            }
        },
    )
    review = _write(
        tmp / "review.json",
        {
            "diff": {"files": ["parser.py"], "lines": 2},
            "rounds": [{"findings": [], "votes": {}, "usage": {"amount_cents": 1}}],
        },
    )
    return {
        "issue": issue,
        "triage": triage,
        "spec": spec,
        "plan": plan,
        "implement": implement,
        "review": review,
    }


def _argv(
    tmp: Path, repo: Path, repo_root: Path, fixtures: dict, *, crash: str | None
) -> list[str]:
    config = repo_root / "config/pipeline.config.example.yaml"
    raw = (
        config.read_text(encoding="utf-8")
        .replace('test: "pytest"', 'test: "true"')
        .replace('lint: "ruff check ."', 'lint: ""')
    )
    path = tmp / "pipeline.yaml"
    path.write_text(raw, encoding="utf-8")
    argv = [
        "run",
        "--issue",
        "4",
        "--issue-snapshot",
        str(fixtures["issue"]),
        "--model-fixture",
        str(fixtures["triage"]),
        "--spec-fixture",
        str(fixtures["spec"]),
        "--plan-fixture",
        str(fixtures["plan"]),
        "--implement-fixture",
        str(fixtures["implement"]),
        "--review-fixture",
        str(fixtures["review"]),
        "--target-repo",
        str(repo),
        "--no-push",
        "--config",
        str(path),
        "--models",
        str(repo_root / "config/models.yaml"),
        "--budgets",
        str(repo_root / "config/budgets.yaml"),
        "--tiers",
        str(repo_root / "config/tiers.yaml"),
        "--runs-dir",
        str(tmp / "runs"),
        "--db",
        str(tmp / "pipeline.db"),
    ]
    if crash:
        argv.extend(["--crash-after", crash])
    return argv


def test_resume_after_stage_boundaries_matches(tmp_path, repo_root, capsys):
    repo = _repo(tmp_path / "target")
    fixtures = _fixtures(tmp_path)
    assert main(_argv(tmp_path, repo, repo_root, fixtures, crash=None)) == 0
    full = next((tmp_path / "runs").iterdir())
    golden = (full / "pr-body.md").read_text(encoding="utf-8")
    for stage in ("triage", "spec", "plan", "implement", "verify", "review"):
        crashed = tmp_path / stage
        crashed.mkdir()
        target = _repo(crashed / "target")
        local = _fixtures(crashed)
        code = main(_argv(crashed, target, repo_root, local, crash=stage))
        assert code == 3, capsys.readouterr().out
        run_dir = next((crashed / "runs").iterdir())
        resumed = main(
            [
                "resume",
                run_dir.name,
                "--runs-dir",
                str(crashed / "runs"),
                "--db",
                str(crashed / "pipeline.db"),
            ]
        )
        assert resumed == 0, capsys.readouterr().err
        assert (run_dir / "pr-body.md").read_text(encoding="utf-8") == golden


def test_dirty_ticket_is_redone_and_resume_is_idempotent(tmp_path, repo_root, capsys):
    repo = _repo(tmp_path / "target")
    fixtures = _fixtures(tmp_path)
    assert main(_argv(tmp_path, repo, repo_root, fixtures, crash="implement")) == 3
    run_dir = next((tmp_path / "runs").iterdir())
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    record = state["tickets"]["T1"]
    (Path(record["worktree"]) / "parser.py").write_text("dirty\n", encoding="utf-8")
    assert (
        main(
            [
                "resume",
                run_dir.name,
                "--runs-dir",
                str(tmp_path / "runs"),
                "--db",
                str(tmp_path / "db"),
            ]
        )
        == 0
    )
    ledger = (run_dir / "ledger.jsonl").read_text(encoding="utf-8")
    assert (
        main(
            [
                "resume",
                run_dir.name,
                "--runs-dir",
                str(tmp_path / "runs"),
                "--db",
                str(tmp_path / "db"),
            ]
        )
        == 0
    )
    assert (run_dir / "ledger.jsonl").read_text(encoding="utf-8") == ledger
    assert "resume: pull request already recorded" in capsys.readouterr().out


def test_budget_halt_resumes_with_a_raised_cap(tmp_path, repo_root, capsys):
    repo = _repo(tmp_path / "target")
    fixtures = _fixtures(tmp_path)
    config = (repo_root / "config/pipeline.config.example.yaml").read_text(encoding="utf-8")
    config = config.replace('test: "pytest"', 'test: "true"')
    config = config.replace('lint: "ruff check ."', 'lint: ""')
    config = config.replace("run: 20", "run: 1", 1)
    path = tmp_path / "pipeline.yaml"
    path.write_text(config, encoding="utf-8")
    code = main(
        [
            "run",
            "--issue",
            "4",
            "--issue-snapshot",
            str(fixtures["issue"]),
            "--model-fixture",
            str(fixtures["triage"]),
            "--spec-fixture",
            str(fixtures["spec"]),
            "--plan-fixture",
            str(fixtures["plan"]),
            "--implement-fixture",
            str(fixtures["implement"]),
            "--review-fixture",
            str(fixtures["review"]),
            "--target-repo",
            str(repo),
            "--no-push",
            "--config",
            str(path),
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
    assert code == 1, capsys.readouterr().out
    run_dir = next((tmp_path / "runs").iterdir())
    assert "Halted before spec" in (run_dir / "summary.md").read_text(encoding="utf-8")
    assert not (run_dir / "spec.json").is_file()
    raised = main(
        [
            "resume",
            run_dir.name,
            "--runs-dir",
            str(tmp_path / "runs"),
            "--db",
            str(tmp_path / "db2"),
            "--run-cap-cents",
            "2000",
        ]
    )
    assert raised == 0, capsys.readouterr().err
    assert (run_dir / "pr-body.md").is_file()
    assert (run_dir / "spec.json").is_file()


def test_changed_inputs_need_force_and_a_raised_cap_continues(tmp_path, repo_root, capsys):
    repo = _repo(tmp_path / "target")
    fixtures = _fixtures(tmp_path)
    assert main(_argv(tmp_path, repo, repo_root, fixtures, crash="triage")) == 3
    run_dir = next((tmp_path / "runs").iterdir())
    fixtures["issue"].write_text(
        fixtures["issue"].read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    refused = main(
        ["resume", run_dir.name, "--runs-dir", str(tmp_path / "runs"), "--db", str(tmp_path / "db")]
    )
    assert refused == 2
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    state["run_cap_cents"] = 1
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    low = main(
        [
            "resume",
            run_dir.name,
            "--runs-dir",
            str(tmp_path / "runs"),
            "--db",
            str(tmp_path / "db"),
            "--force",
        ]
    )
    assert low == 1
    raised = main(
        [
            "resume",
            run_dir.name,
            "--runs-dir",
            str(tmp_path / "runs"),
            "--db",
            str(tmp_path / "db"),
            "--force",
            "--run-cap-cents",
            "2000",
        ]
    )
    assert raised == 0, capsys.readouterr().err
    assert (run_dir / "pr-body.md").is_file()
