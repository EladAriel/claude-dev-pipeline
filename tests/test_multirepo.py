import json
import subprocess
from pathlib import Path

from claude_dev_pipeline.multirepo import coordinate, split_tasks


def _repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=path, check=True, capture_output=True)


def _sha(path: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_two_repos_share_a_budget_and_skip_unlisted_paths(tmp_path: Path) -> None:
    api = tmp_path / "api"
    web = tmp_path / "web"
    secret = tmp_path / "secret"
    _repo(api)
    _repo(web)
    secret.mkdir()
    (secret / "private.txt").write_text("nope\n", encoding="utf-8")
    api_sha = _sha(api)
    web_sha = _sha(web)
    config = {
        "stages": {
            "multirepo": {
                "enabled": True,
                "repos": [
                    {"name": "api", "path": str(api), "approved": True},
                    {"name": "web", "path": str(web), "approved": False},
                ],
            }
        }
    }
    plan = {
        "tasks": [
            {"id": "a", "title": "Api", "repo": "api", "cost_cents": 4},
            {"id": "w", "title": "Web", "repo": "web", "cost_cents": 4},
            {"id": "s", "title": "Secret", "repo": "secret", "cost_cents": 4},
        ]
    }
    refused = split_tasks(plan, config)["refused"]
    assert refused == [{"repo": "secret", "reason": "not-listed"}]
    run = tmp_path / "run"
    report = coordinate(run, "run1", plan, config, 10, verify=lambda _path: "succeeded")
    by_name = {item["repo"]: item for item in report["repos"]}
    assert by_name["api"]["status"] == "ready"
    assert by_name["web"]["status"] == "halted"
    assert by_name["web"]["reason"] == "not-approved"
    assert _sha(api) == api_sha
    assert _sha(web) == web_sha
    assert not (secret / ".git").exists()
    body = (run / "pr-api.md").read_text(encoding="utf-8")
    assert "Merge order: api, web" in body
    assert "does not merge" in body

    second = coordinate(
        tmp_path / "run-budget",
        "run2",
        {
            "tasks": [
                {"id": "a", "title": "Api", "repo": "api", "cost_cents": 6},
                {"id": "w", "title": "Web", "repo": "web", "cost_cents": 6},
            ]
        },
        {
            "stages": {
                "multirepo": {
                    "repos": [
                        {"name": "api", "path": str(api), "approved": True},
                        {"name": "web", "path": str(web), "approved": True},
                    ]
                }
            }
        },
        10,
        verify=lambda _path: "succeeded",
    )
    statuses = {item["repo"]: item for item in second["repos"]}
    assert statuses["api"]["status"] == "ready"
    assert statuses["web"]["reason"] == "budget"
    saved = json.loads((tmp_path / "run-budget" / "multirepo.json").read_text(encoding="utf-8"))
    assert saved["spent_cents"] == 6
