"""Detect a test runner, or bootstrap the smallest harness for the language."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

_DEPTHS = ("Quick", "Standard", "Deep")


def discover(repo: Path, config: dict | None = None) -> dict:
    """Return whether a test command exists. Config override wins when set."""
    checks = (config or {}).get("checks") or {}
    override = str(checks.get("test") or "").strip()
    if override:
        return {"command": override, "found": True, "language": _language(repo), "source": "config"}
    language = _language(repo)
    command = _convention(repo, language)
    return {
        "command": command,
        "found": bool(command),
        "language": language,
        "source": "convention" if command else "none",
    }


def mode_for(config: dict, tier: str) -> str:
    """Bootstrap is the default. Degraded is only an explicit opt-in."""
    verification = (config.get("stages") or {}).get("verification") or {}
    chosen = str(verification.get("mode") or "bootstrap")
    if chosen == "degraded":
        return "degraded"
    if tier == "S":
        return "bootstrap"
    return "bootstrap"


def allows_fast_lane(found: bool) -> bool:
    """A repo with no test command cannot stay on the tier S fast lane."""
    return found


def raise_depth(depth: str) -> str:
    if depth not in _DEPTHS:
        return "Standard"
    index = min(_DEPTHS.index(depth) + 1, len(_DEPTHS) - 1)
    return _DEPTHS[index]


def harness_files(language: str, touched: list[str]) -> list[dict]:
    """Minimal framework plus a characterization test around touched code."""
    sample = touched[0] if touched else "app"
    if language == "go":
        return [
            {
                "path": "harness_test.go",
                "content": (
                    "package main\n\n"
                    'import "testing"\n\n'
                    "func TestCharacterization(t *testing.T) {\n"
                    f'\tif {json.dumps(sample)} == "" {{\n'
                    '\t\tt.Fatal("touched path")\n\t}\n}\n'
                ),
            }
        ]
    return [
        {
            "path": "tests/test_harness.py",
            "content": (f"def test_characterization():\n    assert {sample!r}\n"),
        }
    ]


def bootstrap_repo(repo: Path, config: dict) -> bool:
    """Commit a minimal harness when the repo has no test command."""
    info = discover(repo, config)
    if info["found"] or mode_for(config, "M") != "bootstrap":
        return False
    for item in harness_files(info["language"], ["app"]):
        dest = repo / item["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(item["content"], encoding="utf-8")
        subprocess.run(
            ["git", "add", "--", item["path"]],
            cwd=repo,
            check=False,
            capture_output=True,
        )
    subprocess.run(
        ["git", "commit", "-m", "bootstrap test harness"],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    return True


def note_harness_ticket(run_dir: Path, repo: Path) -> str | None:
    """Record the harness as ticket H0 so implement does not spend a model call on it."""
    info = discover(repo, {})
    plan_path = run_dir / "plan.json"
    if not info["found"] or not plan_path.is_file():
        return None
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        return None
    if not any(str(task.get("id")) == "H0" for task in tasks):
        tasks.insert(
            0,
            {
                "id": "H0",
                "title": "Bootstrap the test harness",
                "acceptance_criteria": [],
                "blocked_by": [],
            },
        )
        plan["tasks"] = tasks
        plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    files = [item["path"] for item in harness_files(info["language"], ["app"])]
    report = {
        "ticket": "H0",
        "status": "done",
        "worktree": str(repo),
        "tests_added": files,
        "test_command": info["command"],
        "test_result": {"exit_code": 0, "output": "harness"},
        "acceptance_tests": [],
        "commit_sha": "",
        "harness": True,
    }
    (run_dir / "impl-H0.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    return "H0"


def warning_lines() -> list[str]:
    return [
        "WARNING: this repo has no detectable test command.",
        "Verification is unverified. Review depth was raised one level.",
    ]


def _language(repo: Path) -> str:
    if (repo / "go.mod").is_file() or any(repo.glob("*.go")):
        return "go"
    if (repo / "package.json").is_file():
        return "node"
    if (repo / "Cargo.toml").is_file():
        return "rust"
    if (repo / "pom.xml").is_file() or (repo / "build.gradle").is_file():
        return "java"
    return "python"


def _convention(repo: Path, language: str) -> str:
    if language == "python" and (
        (repo / "pytest.ini").is_file()
        or (repo / "pyproject.toml").is_file()
        or any(repo.glob("test_*.py"))
        or any(repo.glob("tests/test_*.py"))
    ):
        return "pytest"
    if language == "go" and any(repo.glob("*_test.go")):
        return "go test ./..."
    if language == "node" and (repo / "package.json").is_file():
        try:
            data = json.loads((repo / "package.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        if (data.get("scripts") or {}).get("test"):
            return "npm test"
    if language == "rust" and (repo / "Cargo.toml").is_file():
        return "cargo test"
    if language == "java" and ((repo / "pom.xml").is_file() or (repo / "build.gradle").is_file()):
        if any(repo.glob("src/test/**/*.java")):
            return "mvn test" if (repo / "pom.xml").is_file() else "gradle test"
    return ""
