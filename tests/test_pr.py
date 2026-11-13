import json
import subprocess
from pathlib import Path

from claude_dev_pipeline.cli import main
from claude_dev_pipeline.config import load_yaml
from claude_dev_pipeline.mcp_policy import MERGE_TOOLS, validate_mcp_config
from claude_dev_pipeline.stages.pr import (
    DENIED_TOOLS,
    PrBlocked,
    PrError,
    PrSkipped,
    _call,
    execute_pr,
)


class FakeMcp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool: str, payload: dict) -> dict:
        self.calls.append((tool, payload))
        if tool == "create_pull_request":
            return {"url": "https://example.test/pull/1"}
        if tool in {"post_commit_status", "addOrEditJiraIssueComment", "transitionJiraIssue"}:
            return {"ok": True}
        raise AssertionError(tool)


class IssueClient:
    def __init__(self) -> None:
        self.writes: list = []

    def apply(self, write) -> None:
        self.writes.append(write)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _target(tmp_path: Path) -> tuple[Path, Path]:
    bare = tmp_path / "remote.git"
    repo = tmp_path / "target"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "origin", "main")
    _git(repo, "checkout", "-b", "agent/run-T1")
    (repo / "src.py").write_text("print(1)\n", encoding="utf-8")
    _git(repo, "add", "src.py")
    _git(repo, "commit", "-m", "ticket")
    _git(repo, "checkout", "main")
    return repo, bare


def _artifacts(run: Path, *, block: bool = False) -> None:
    run.mkdir()
    (run / "verify.json").write_text(json.dumps({"status": "pass"}) + "\n", encoding="utf-8")
    findings = []
    if not block:
        findings = [
            {"tag": "Nit", "blocks": False},
            {"tag": "Pre-existing", "blocks": False},
        ]
    review = {
        "security_block": block,
        "summary": "2 findings: 0 Important, 1 Nit, 1 Pre-existing.",
        "findings": findings,
    }
    if block:
        review["findings"] = [{"tag": "Important", "blocks": True, "security": True}]
        review["summary"] = "Security blocks the run."
    (run / "review.json").write_text(json.dumps(review) + "\n", encoding="utf-8")
    (run / "spec.json").write_text(json.dumps({"title": "Add a parser"}) + "\n", encoding="utf-8")
    (run / "plan.json").write_text(
        json.dumps({"tasks": [{"id": "T1", "title": "Parse input"}]}) + "\n",
        encoding="utf-8",
    )
    (run / "pipeline-review-status.json").write_text(
        json.dumps({"context": "pipeline/review", "state": "success", "posted": False}) + "\n",
        encoding="utf-8",
    )
    (run / "ledger.jsonl").write_text(
        json.dumps(
            {
                "run_id": "run",
                "stage": "review",
                "amount_cents": 12,
                "provider": "anthropic",
                "model": "claude-haiku-5.5",
                "note": "fixture",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _run(tmp_path: Path, **kwargs):
    repo, bare = _target(tmp_path)
    run = tmp_path / "run"
    _artifacts(run, block=kwargs.pop("block", False))
    result = execute_pr(
        run_dir=run,
        run_id="run",
        target_repo=repo,
        base="main",
        ticket_branches=["agent/run-T1"],
        spent_cents=12,
        run_cap_cents=2000,
        stage_cap_cents=25,
        **kwargs,
    )
    return result, run, bare


def test_no_push_renders_the_body(tmp_path):
    _result, run, bare = _run(tmp_path, push=False, issue_number=7, usage_cents=3)
    body = (run / "pr-body.md").read_text(encoding="utf-8")
    saved = json.loads((run / "pr.json").read_text(encoding="utf-8"))
    assert "Fixes #7" in body
    assert "T1: Parse input" in body
    assert "0 Important, 1 Nit, 1 Pre-existing" in body
    assert "Cost so far: $0.15" in body
    assert saved["pushed"] is False
    assert saved["draft"] is True
    assert saved["tool"] == "create_pull_request"
    listed = subprocess.run(
        ["git", "ls-remote", str(bare), "pipeline/run"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert listed.stdout == ""


def test_blocked_review_does_not_push_or_open(tmp_path):
    mcp = FakeMcp()
    try:
        _run(tmp_path, push=True, mcp=mcp, block=True)
    except PrBlocked:
        pass
    else:
        raise AssertionError("blocking review opened a pull request")
    assert mcp.calls == []
    bare = tmp_path / "remote.git"
    listed = subprocess.run(
        ["git", "ls-remote", str(bare), "pipeline/run"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert listed.stdout == ""


def test_merge_tool_is_denied(repo_root):
    config = load_yaml(repo_root / "config/pipeline.config.example.yaml")
    errors = validate_mcp_config(config)
    assert errors == []
    github = config["mcp_servers"]["github_pull_requests"]
    pr = github["stages"]["pr"]
    assert pr["tools"] == ["create_pull_request"]
    assert "merge_pull_request" in github["deny_tools"]
    assert MERGE_TOOLS <= DENIED_TOOLS or "merge_pull_request" in DENIED_TOOLS
    fake = FakeMcp()
    try:
        _call(fake, "merge_pull_request", {})
    except PrError as exc:
        assert "denied" in str(exc)
    else:
        raise AssertionError("merge was allowed")
    assert fake.calls == []


def test_unapproved_issue_is_untouched(tmp_path):
    mcp = FakeMcp()
    issues = IssueClient()
    result, _run_dir, bare = _run(
        tmp_path,
        push=True,
        mcp=mcp,
        issue_client=issues,
        issue_number=9,
        issue_labels=["bug"],
        issue_timeline=[{"event": "labeled", "label": "agent:approved", "actor": "someone-else"}],
    )
    assert result.issue_updated is False
    assert issues.writes == []
    assert any(tool == "create_pull_request" for tool, _payload in mcp.calls)
    assert "merge_pull_request" not in [tool for tool, _payload in mcp.calls]
    listed = subprocess.run(
        ["git", "ls-remote", str(bare), "pipeline/run"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "pipeline/run" in listed.stdout


def test_disabled_pr_does_not_open_artifacts(tmp_path):
    missing = tmp_path / "missing"
    try:
        execute_pr(
            run_dir=missing,
            run_id="run",
            target_repo=tmp_path,
            base="main",
            ticket_branches=[],
            spent_cents=0,
            run_cap_cents=2000,
            stage_cap_cents=25,
            enabled=False,
        )
    except PrSkipped:
        pass
    else:
        raise AssertionError("disabled PR ran")


def test_dry_run_mentions_the_draft(repo_root, capsys):
    code = main(["--dry-run", "--config", str(repo_root / "config/pipeline.config.example.yaml")])
    assert code == 0
    assert "create_pull_request" in capsys.readouterr().out
