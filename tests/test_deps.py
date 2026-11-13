import json
import subprocess
from pathlib import Path

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.deps import (
    audit,
    beyond_issue,
    classify_paths,
    inspect_diff,
    is_major,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_one_ecosystem_major_and_advisory(tmp_path: Path) -> None:
    assert classify_paths(["package.json", "package-lock.json"], {})["ecosystem"] == "npm"
    assert classify_paths(["package.json", "pyproject.toml"], {})["split"] is True
    assert classify_paths(["src/app.py"], {})["lane"] == ""
    assert classify_paths(["go.mod"], {"stages": {"deps": {"enabled": False}}})["lane"] == ""
    assert is_major("requests==2.31.0\n", "requests==3.0.0\n") is True
    assert is_major("requests==2.31.0\n", "requests==2.32.0\n") is False
    assert beyond_issue("a==1.2.3\n", "a==1.2.3\nb==9.9.9\n", "bump a") == ["9.9.9"]

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "package.json").write_text('{"version": "1.2.3"}\n', encoding="utf-8")
    _git(repo, "add", "package.json")
    _git(repo, "commit", "-m", "base")
    (repo / "package.json").write_text('{"version": "2.0.0"}\n', encoding="utf-8")
    _git(repo, "add", "package.json")
    _git(repo, "commit", "-m", "bump")
    (repo / "audit-result.txt").write_text("advisory CVE-2024-0001\n", encoding="utf-8")
    found = inspect_diff(repo, ["package.json"], "Bump package.json to 2.0.0", {})
    assert found["lane"] == "deps"
    assert found["major"] is True
    assert found["tier"] == "L"
    assert found["extra"] == []
    assert audit(repo, {})["advisory"] is True


def test_deps_lane_disables_the_fast_lane(tmp_path: Path, repo_root, capsys) -> None:
    issue = tmp_path / "issue.json"
    issue.write_text(
        json.dumps(
            {
                "number": 40,
                "title": "Bump the npm package",
                "body": "Bump package.json from 1.2.3 to 2.0.0 in this repository only.",
                "labels": ["agent:approved"],
                "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
            }
        ),
        encoding="utf-8",
    )
    fixture = tmp_path / "triage.json"
    fixture.write_text(
        json.dumps(
            {
                "artifact": {
                    "classify": "enhancement",
                    "category": "enhancement",
                    "state": "ready-for-agent",
                    "risk_tier": "Quick",
                    "decision": "route",
                    "needs_human": False,
                    "confidence": 90,
                    "injection_suspected": False,
                    "summary": "Bump.",
                    "tier": "S",
                },
                "brief": "Bump the package.",
                "usage": {"amount_cents": 1},
            }
        ),
        encoding="utf-8",
    )
    code = main(
        [
            "run",
            "--issue",
            "40",
            "--issue-snapshot",
            str(issue),
            "--model-fixture",
            str(fixture),
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
            str(tmp_path / "runs"),
            "--db",
            str(tmp_path / "pipeline.db"),
        ]
    )
    out = capsys.readouterr().out
    assert code != 0
    assert "deps-lane: npm" in out
    assert "fast-lane: skipped spec and plan" not in out

    mixed = tmp_path / "mixed.json"
    mixed.write_text(
        json.dumps(
            {
                "number": 41,
                "title": "Bump two ecosystems",
                "body": "Update package.json and pyproject.toml together.",
                "labels": ["agent:approved"],
                "timeline": [{"event": "labeled", "label": "agent:approved", "actor": "EladAriel"}],
            }
        ),
        encoding="utf-8",
    )
    split = main(
        [
            "run",
            "--issue",
            "41",
            "--issue-snapshot",
            str(mixed),
            "--model-fixture",
            str(fixture),
            "--config",
            str(repo_root / "config/pipeline.config.example.yaml"),
            "--models",
            str(repo_root / "config/models.yaml"),
            "--budgets",
            str(repo_root / "config/budgets.yaml"),
            "--tiers",
            str(repo_root / "config/tiers.yaml"),
            "--runs-dir",
            str(tmp_path / "runs-split"),
            "--db",
            str(tmp_path / "split.db"),
        ]
    )
    text = capsys.readouterr().out
    assert split == 2
    assert "one ecosystem per PR" in text
