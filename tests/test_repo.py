import ast
import subprocess

import yaml
from tests.conftest import repo_root

from claude_dev_pipeline.config import load_yaml
from claude_dev_pipeline.plan import DEFAULT_CAPS_CENTS, DEFAULT_MODELS, STAGES, require_gates


def test_every_stage_has_a_gate_and_a_budget():
    require_gates()
    root = repo_root()
    budgets = load_yaml(root / "config/budgets.yaml")
    models = load_yaml(root / "config/models.yaml")
    example = load_yaml(root / "config/pipeline.config.example.yaml")
    assert int(budgets["caps_cents"]["run"]) == 2000
    assert "eval_full" not in budgets["caps_cents"]
    assert "golden" not in (root / "config/budgets.yaml").read_text(encoding="utf-8").lower()
    assert not (root / "evals" / "golden").exists()
    assert not (root / "skills" / "vendor").exists()
    assert list((root / "skills").rglob("SKILL.md")) == []
    for stage in STAGES:
        if stage.model_key == "none":
            continue
        assert stage.budget_key in DEFAULT_CAPS_CENTS
        assert models[stage.model_key]["model"] == DEFAULT_MODELS[stage.model_key]["model"]
        assert example["models"][stage.model_key]["model"] == models[stage.model_key]["model"]


def test_manifest_pins_the_plugin_without_copying_it():
    data = load_yaml(repo_root() / "skills/manifest.yaml")
    pin = "d1478fcb140db0b93c0c8df17bcfd311844749d0"
    assert data["plugin"]["version"] == "1.3.2"
    assert data["plugin"]["vendored"] is False
    assert data["plugin"]["git_sha"] == pin
    names = {item["name"]: item for item in data["skills"]}
    expected = {
        "grill-with-docs",
        "triage",
        "to-spec",
        "to-tickets",
        "tdd",
        "implement",
        "code-review",
        "pr",
        "retro",
    }
    assert set(names) == expected
    for item in data["skills"] + data["dependencies"]:
        assert item["sha"] == pin
        assert item["url"].endswith(item["path"])


def test_workflows_do_not_react_to_stranger_pull_requests():
    workflows = repo_root() / ".github" / "workflows"
    for path in workflows.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert "pull_request" not in text
        assert "issues:" not in text
        assert "issue_comment" not in text
        data = yaml.safe_load(text)
        trigger = data.get(True) or data.get("on")
        assert "push" in trigger
        assert data["permissions"]["contents"] == "read"


def test_env_example_has_names_only_and_secrets_are_ignored():
    root = repo_root()
    for line in (root / ".env.example").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        assert key.strip()
        assert value.strip() == ""
    ignored = [
        ".env",
        ".env.local",
        "claude-lock.json",
        "runs/pipeline.db",
        "ledger.jsonl",
    ]
    for name in ignored:
        result = subprocess.run(
            ["git", "check-ignore", "-q", name],
            cwd=root,
            check=False,
        )
        assert result.returncode == 0, name
    visible = subprocess.run(
        ["git", "check-ignore", "-q", ".env.example"],
        cwd=root,
        check=False,
    )
    assert visible.returncode != 0


def test_python_source_does_not_call_a_model_or_merge():
    root = repo_root() / "src"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "/" + "merge" not in text
        assert "gh pr merge" not in text
        tree = ast.parse(text)
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            for module in modules:
                assert module.split(".")[0] not in {"anthropic", "openai", "requests", "httpx"}
