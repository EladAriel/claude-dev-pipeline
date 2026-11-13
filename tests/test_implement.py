import json
import subprocess
from pathlib import Path

import yaml

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.config import load_yaml
from claude_dev_pipeline.db import connect
from claude_dev_pipeline.stages.implement import ImplementError, choose_model, execute_implement


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=path, check=True, capture_output=True)
    return path


def _plan() -> dict:
    return {
        "tasks": [
            {
                "id": "01",
                "title": "Parse quotes",
                "acceptance_criteria": ["AC-1"],
                "files": ["src/parser.py"],
                "seams": ["parser.parse"],
                "test_plan": "Cover AC-1.",
                "blocked_by": [],
                "estimate": "S",
                "suggested_model": "claude-opus-5.5",
                "body": "Cover AC-1.",
            },
            {
                "id": "02",
                "title": "Reject empty quotes",
                "acceptance_criteria": ["AC-2"],
                "files": ["src/parser.py"],
                "seams": ["parser.parse"],
                "test_plan": "Cover AC-2.",
                "blocked_by": ["01"],
                "estimate": "M",
                "suggested_model": "not-a-real-model",
                "body": "Cover AC-2.",
            },
            {
                "id": "03",
                "title": "Later",
                "acceptance_criteria": [],
                "files": ["src/later.py"],
                "seams": ["later.run"],
                "test_plan": "Not reached.",
                "blocked_by": ["02"],
                "estimate": "S",
                "suggested_model": "claude-sonnet-5.5",
                "body": "Not reached.",
            },
        ]
    }


def _session(path: str, content: str, *, status: str = "done", exit_code: int = 0) -> dict:
    return {
        "status": status,
        "files": [{"path": path, "content": content}],
        "tests_added": ["tests/test_parser.py"] if status == "done" else [],
        "test_command": "pytest",
        "test_result": {"exit_code": exit_code, "output": "ok" if exit_code == 0 else "failed"},
        "usage": {"amount_cents": 10, "output_tokens": 4},
    }


def _cfg() -> dict:
    return {
        "model": "claude-sonnet-5.5",
        "effort": "high",
        "allowlist": ["claude-sonnet-5.5", "claude-opus-5.5"],
    }


def test_allowlist_keeps_the_default_model():
    assert choose_model({"suggested_model": "claude-opus-5.5"}, _cfg()) == "claude-opus-5.5"
    assert choose_model({"suggested_model": "not-a-real-model"}, _cfg()) == "claude-sonnet-5.5"


def test_blocked_ticket_stops_the_run_without_publishing(tmp_path):
    repo = _git_repo(tmp_path / "target")
    secret = "prefix " + "gh" + "p_" + "abcdefghijklmnop"
    fixture = tmp_path / "implement.json"
    fixture.write_text(
        json.dumps(
            {
                "tickets": {
                    "01": _session("src/parser.py", "def parse():\n    return 1\n"),
                    "02": _session("src/parser.py", secret, status="blocked", exit_code=1),
                    "03": _session("src/later.py", "def later():\n    return 1\n"),
                }
            }
        ),
        encoding="utf-8",
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched\n", encoding="utf-8")
    result = execute_implement(
        plan=_plan(),
        fixture_path=fixture,
        run_dir=tmp_path / "run",
        run_id="run-impl",
        target_repo=repo,
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=300,
        implement_cfg=_cfg(),
    )
    assert result.status == "blocked"
    assert result.tickets == ("01", "02")
    assert result.stopped_at == "02"
    done = json.loads((tmp_path / "run" / "impl-01.json").read_text(encoding="utf-8"))
    blocked = json.loads((tmp_path / "run" / "impl-02.json").read_text(encoding="utf-8"))
    assert done["status"] == "done"
    assert done["commit_sha"]
    assert done["model"] == "claude-opus-5.5"
    assert done["test_command"] == "pytest"
    assert blocked["status"] == "blocked"
    assert blocked["commit_sha"] is None
    assert blocked["model"] == "claude-sonnet-5.5"
    assert not (tmp_path / "run" / "impl-03.json").exists()
    remotes = subprocess.run(
        ["git", "-C", str(repo), "remote"], check=True, capture_output=True, text=True
    )
    assert remotes.stdout.strip() == ""
    assert outside.read_text(encoding="utf-8") == "untouched\n"
    worktree = Path(done["worktree"])
    assert repo.resolve() in worktree.resolve().parents or str(repo.name) in str(worktree)
    assert "claude-dev-pipeline" not in str(worktree)


def test_escaping_path_and_secret_block_the_ticket(tmp_path):
    repo = _git_repo(tmp_path / "target")
    base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    escape = tmp_path / "escape.json"
    escape.write_text(
        json.dumps({"tickets": {"01": _session("../outside.txt", "nope\n")}}),
        encoding="utf-8",
    )
    plan = {"tasks": [_plan()["tasks"][0]]}
    result = execute_implement(
        plan=plan,
        fixture_path=escape,
        run_dir=tmp_path / "run-escape",
        run_id="run-escape",
        target_repo=repo,
        spent_cents=0,
        run_cap_cents=2000,
        stage_cap_cents=300,
        implement_cfg=_cfg(),
    )
    report = json.loads((tmp_path / "run-escape" / "impl-01.json").read_text(encoding="utf-8"))
    assert result.stopped_at == "01"
    assert report["status"] == "blocked"
    assert report["commit_sha"] is None
    assert (tmp_path / "outside.txt").exists() is False or (tmp_path / "outside.txt").read_text(
        encoding="utf-8"
    ) != "nope\n"
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == base

    secret = "value=" + "sk-" + "ant-" + "secretvalue"
    leaked = tmp_path / "leak.json"
    leaked.write_text(
        json.dumps({"tickets": {"01": _session("src/parser.py", secret)}}),
        encoding="utf-8",
    )
    try:
        execute_implement(
            plan=plan,
            fixture_path=leaked,
            run_dir=tmp_path / "run-leak",
            run_id="run-leak",
            target_repo=repo,
            spent_cents=0,
            run_cap_cents=2000,
            stage_cap_cents=300,
            implement_cfg=_cfg(),
        )
    except ImplementError:
        raise AssertionError("a scanned secret should block the ticket, not crash") from None
    leaked_report = json.loads((tmp_path / "run-leak" / "impl-01.json").read_text(encoding="utf-8"))
    assert leaked_report["status"] == "blocked"
    assert leaked_report["commit_sha"] is None
    assert any(item["rule"] == "secret" for item in leaked_report["scan"])


def test_cli_fanout_and_per_ticket_rows(tmp_path, capsys, repo_root):
    repo = _git_repo(tmp_path / "target")
    plan = _plan()
    plan_fixture = tmp_path / "plan.json"
    plan_fixture.write_text(
        json.dumps({"artifact": plan, "usage": {"amount_cents": 20}}), encoding="utf-8"
    )
    common_models = [
        "--config",
        str(repo_root / "config/pipeline.config.example.yaml"),
        "--models",
        str(repo_root / "config/models.yaml"),
        "--budgets",
        str(repo_root / "config/budgets.yaml"),
    ]
    assert main(["run", "--dry-run", "--plan-fixture", str(plan_fixture), *common_models]) == 0
    dry = capsys.readouterr().out
    assert "key=implement" in dry
    assert "implement-fanout: ticket=01 model=claude-opus-5.5" in dry
    assert "implement-fanout: ticket=02 model=claude-sonnet-5.5" in dry
    assert "implement-fanout: ticket=03 model=claude-sonnet-5.5" in dry

    triage = tmp_path / "triage.json"
    spec = tmp_path / "spec.json"
    implement = tmp_path / "implement.json"
    triage.write_text(
        json.dumps(
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
                    "summary": "Parser bug.",
                },
                "brief": "AI draft: not posted.\n",
                "usage": {"amount_cents": 1},
            }
        ),
        encoding="utf-8",
    )
    spec.write_text(
        json.dumps(
            {
                "artifact": {
                    "acceptance_criteria": [
                        {"id": "AC-1", "statement": "Quotes round-trip."},
                        {"id": "AC-2", "statement": "Empty quotes fail."},
                    ],
                    "open_questions": [],
                    "needs_human": False,
                    "seams": ["parser.parse"],
                    "e2e_scenarios": [{"id": "E2E-1", "scenario": "Run the parser."}],
                },
                "spec_md": "# Spec\n\nAC-1 and AC-2.\n",
                "usage": {"amount_cents": 15},
            }
        ),
        encoding="utf-8",
    )
    implement.write_text(
        json.dumps(
            {
                "tickets": {
                    "01": _session("src/parser.py", "def parse():\n    return 1\n"),
                    "02": _session(
                        "src/parser.py",
                        "def parse():\n    raise ValueError\n",
                        status="blocked",
                        exit_code=1,
                    ),
                    "03": _session("src/later.py", "def later():\n    return 1\n"),
                }
            }
        ),
        encoding="utf-8",
    )
    issue = tmp_path / "issue.json"
    issue.write_text(
        json.dumps(
            {
                "number": 12,
                "title": "Parser",
                "body": "Quotes.",
                "labels": ["agent:approved"],
                "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
            }
        ),
        encoding="utf-8",
    )
    code = main(
        [
            "run",
            "--issue",
            "12",
            "--issue-snapshot",
            str(issue),
            "--model-fixture",
            str(triage),
            "--spec-fixture",
            str(spec),
            "--plan-fixture",
            str(plan_fixture),
            "--implement-fixture",
            str(implement),
            "--target-repo",
            str(repo),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--db",
            str(tmp_path / "pipeline.db"),
            *common_models,
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "mode: implement-blocked" in out
    assert "implement-stopped: 02" in out
    conn = connect(tmp_path / "pipeline.db")
    try:
        rows = conn.execute(
            "SELECT ticket, model, budget_cents FROM stages WHERE name = 'implement'"
        ).fetchall()
    finally:
        conn.close()
    by_ticket = {row["ticket"]: row for row in rows}
    assert set(by_ticket) == {"01", "02"}
    assert by_ticket["01"]["model"] == "claude-opus-5.5"
    assert by_ticket["01"]["budget_cents"] == 300
    assert by_ticket["02"]["model"] == "claude-sonnet-5.5"


def test_disabled_implement_does_not_open_the_fixture(tmp_path, capsys, repo_root):
    config = load_yaml(repo_root / "config/pipeline.config.example.yaml")
    config["stages"]["implement"] = {"enabled": False}
    config_path = tmp_path / "pipeline.config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"artifact": _plan(), "usage": {"amount_cents": 20}}), encoding="utf-8"
    )
    triage = tmp_path / "triage.json"
    spec = tmp_path / "spec.json"
    triage.write_text(
        json.dumps(
            {
                "artifact": {
                    "classify": "bug",
                    "category": "bug",
                    "state": "ready-for-agent",
                    "risk_tier": "Quick",
                    "decision": "route",
                    "needs_human": False,
                    "confidence": 80,
                    "injection_suspected": False,
                    "summary": "Parser bug.",
                },
                "brief": "AI draft: not posted.\n",
                "usage": {"amount_cents": 1},
            }
        ),
        encoding="utf-8",
    )
    spec.write_text(
        json.dumps(
            {
                "artifact": {
                    "acceptance_criteria": [
                        {"id": "AC-1", "statement": "Quotes."},
                        {"id": "AC-2", "statement": "Empty."},
                    ],
                    "open_questions": [],
                    "needs_human": False,
                    "seams": ["parser.parse"],
                    "e2e_scenarios": [{"id": "E2E-1", "scenario": "Run it."}],
                },
                "spec_md": "# Spec\n\nAC-1 and AC-2.\n",
                "usage": {"amount_cents": 10},
            }
        ),
        encoding="utf-8",
    )
    issue = tmp_path / "issue.json"
    issue.write_text(
        json.dumps(
            {
                "number": 4,
                "title": "Parser",
                "body": "Quotes.",
                "labels": ["agent:approved"],
                "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
            }
        ),
        encoding="utf-8",
    )
    code = main(
        [
            "run",
            "--issue",
            "4",
            "--issue-snapshot",
            str(issue),
            "--model-fixture",
            str(triage),
            "--spec-fixture",
            str(spec),
            "--plan-fixture",
            str(plan),
            "--implement-fixture",
            str(tmp_path / "missing-implement.json"),
            "--config",
            str(config_path),
            "--models",
            str(repo_root / "config/models.yaml"),
            "--budgets",
            str(repo_root / "config/budgets.yaml"),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--db",
            str(tmp_path / "pipeline.db"),
        ]
    )
    assert code == 0
    assert "implement: disabled" in capsys.readouterr().out
    assert not any((tmp_path / "runs").rglob("impl-*.json"))


def test_agent_file_pins_tdd_and_the_allowlist(repo_root):
    text = (repo_root / "agents/implement.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---", 2)[1])
    models = load_yaml(repo_root / "config/models.yaml")
    manifest = load_yaml(repo_root / "skills/manifest.yaml")
    skill = next(item for item in manifest["skills"] if item["name"] == "tdd")
    enabled = [item["name"] for item in meta["tools"][0]["configs"] if item["enabled"]]
    assert meta["model"]["id"] == models["implement"]["model"]
    assert meta["model"]["effort_from"] == "config/models.yaml#implement"
    assert models["implement"]["allowlist"] == ["claude-sonnet-5.5", "claude-opus-5.5"]
    assert enabled == ["read", "grep", "glob", "edit", "write", "bash"]
    assert meta["skills"][0]["sha"] == skill["sha"]
    assert meta["skills"][0]["path"] == skill["path"]
    assert "skill named `tdd`" in text
    source = (repo_root / "src/claude_dev_pipeline/stages/implement.py").read_text(encoding="utf-8")
    assert "push" in source
    assert "request-pull" in source
