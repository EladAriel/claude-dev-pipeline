"""PR stage. Local git push and a draft pull request. No merge tool."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from claude_dev_pipeline.budget import BudgetHardStop, authorize_session_cents
from claude_dev_pipeline.flaky import body_lines
from claude_dev_pipeline.github_gate import admit_issue
from claude_dev_pipeline.issues import plan_status_write
from claude_dev_pipeline.ledger import Ledger, LedgerEntry

ALLOWED_PR_TOOL = "create_pull_request"
DENIED_TOOLS = {"merge_pull_request", "merge", "update_pull_request"}
ATLASSIAN_WRITE_TOOLS = {"addOrEditJiraIssueComment", "transitionJiraIssue"}


class PrError(Exception):
    def __init__(self, message: str, *, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


class PrSkipped(Exception):
    pass


class PrBlocked(PrError):
    pass


@dataclass(frozen=True)
class PrResult:
    status: str
    pushed: bool
    pr_url: str | None
    issue_updated: bool


def execute_pr(
    *,
    run_dir: Path,
    run_id: str,
    target_repo: Path,
    base: str,
    ticket_branches: list[str],
    spent_cents: int,
    run_cap_cents: int,
    stage_cap_cents: int,
    enabled: bool = True,
    push: bool = False,
    remote: str = "origin",
    mcp=None,
    issue_client=None,
    issue_number: int | None = None,
    issue_labels: list[str] | None = None,
    issue_timeline: list[dict] | None = None,
    existing_comments: list[dict] | None = None,
    atlassian=None,
    atlassian_enabled: bool = False,
    jira_key: str | None = None,
    model: str = "claude-haiku-5.5",
    effort: str = "low",
    usage_cents: int = 0,
) -> PrResult:
    if not enabled:
        raise PrSkipped("stages.pr.enabled is false")
    _require_ready(run_dir)
    title, body, cost = render_pr(
        run_dir,
        issue_number=issue_number,
        usage_cents=usage_cents,
    )
    branch = f"pipeline/{run_id}"
    _combine(target_repo, branch, base, ticket_branches)
    pushed = False
    pr_url = None
    if push:
        _push(target_repo, remote, branch)
        pushed = True
        if mcp is not None:
            pr_url = _open_draft(mcp, title, body, branch, base)
    _write_pr(run_dir, title, body, branch, base, pushed, pr_url, cost)
    _book(run_dir, run_id, usage_cents, model, effort, stage_cap_cents, run_cap_cents, spent_cents)
    if push and mcp is not None:
        _post_review_status(mcp, run_dir, _head(target_repo))
    issue_updated = False
    if push and issue_number is not None and issue_client is not None:
        issue_updated = _update_issue(
            issue_client,
            issue_number,
            issue_labels or [],
            issue_timeline or [],
            existing_comments or [],
            pr_url,
        )
    if push and atlassian_enabled and atlassian is not None and jira_key:
        _atlassian(atlassian, jira_key, pr_url)
    return PrResult("draft" if pushed else "rendered", pushed, pr_url, issue_updated)


def render_pr(run_dir: Path, *, issue_number: int | None, usage_cents: int) -> tuple[str, str, int]:
    spec = _load(run_dir / "spec.json")
    plan = _load(run_dir / "plan.json")
    review = _load(run_dir / "review.json")
    ledger_cost = _ledger_cost(run_dir) + usage_cents
    title = str(spec.get("title") or "Pipeline change")
    counts = _counts(review.get("findings") or [])
    lines = []
    verify = _load(run_dir / "verify.json") if (run_dir / "verify.json").is_file() else {}
    if verify.get("status") == "unverified":
        lines.append("WARNING: no test command was detected. Verification is unverified.")
        lines.append("")
    lines.extend(body_lines(verify))
    if issue_number is not None:
        lines.append(f"Fixes #{issue_number}")
        lines.append("")
    lines.append("Tickets:")
    for task in plan.get("tasks") or []:
        lines.append(f"- {task.get('id')}: {task.get('title')}")
    lines.append("")
    lines.append(
        "Review: "
        f"{counts['Important']} Important, "
        f"{counts['Nit']} Nit, "
        f"{counts['Pre-existing']} Pre-existing."
    )
    summary = str(review.get("summary") or "")
    if summary:
        lines.append(summary)
    lines.append("")
    lines.append(f"Cost so far: ${ledger_cost / 100:.2f}")
    return title, "\n".join(lines) + "\n", ledger_cost


def _require_ready(run_dir: Path) -> None:
    verify = _load(run_dir / "verify.json")
    review = _load(run_dir / "review.json")
    if verify.get("status") not in {"pass", "unverified"}:
        raise PrBlocked("verify did not pass")
    if review.get("security_block") or review.get("status") == "failure":
        raise PrBlocked("review is blocking")
    blocking = [
        item
        for item in review.get("findings") or []
        if item.get("blocks") or item.get("tag") == "Important"
    ]
    if blocking:
        raise PrBlocked("review has a blocking finding")


def _combine(repo: Path, branch: str, base: str, tickets: list[str]) -> None:
    _git(repo, "checkout", "-B", branch, base)
    for ticket in tickets:
        _git(repo, "merge", "--no-ff", "--no-edit", ticket)


def _push(repo: Path, remote: str, branch: str) -> None:
    if "://" in remote or "@" in remote:
        raise PrError("push remote must be a configured name, not a URL")
    _git(repo, "push", remote, f"{branch}:{branch}")


def _open_draft(mcp, title: str, body: str, branch: str, base: str) -> str:
    result = _call(
        mcp,
        ALLOWED_PR_TOOL,
        {
            "title": title,
            "body": body,
            "head": branch,
            "base": base,
            "draft": True,
            "vault": "vault-github-pr",
            "toolset": "/x/pull_requests",
        },
    )
    return str(result.get("url") or "")


def _post_review_status(mcp, run_dir: Path, sha: str) -> None:
    payload = _load(run_dir / "pipeline-review-status.json")
    payload["sha"] = sha
    payload["posted"] = True
    _call(mcp, "post_commit_status", payload)
    (run_dir / "pipeline-review-status.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _update_issue(client, number, labels, timeline, comments, pr_url) -> bool:
    if not admit_issue(labels=labels, timeline=timeline).allowed:
        return False
    write = plan_status_write(
        issue_number=number,
        status="pr-open",
        existing_comments=comments,
        pr_url=pr_url,
    )
    client.apply(write)
    return True


def _atlassian(client, key: str, pr_url: str | None) -> None:
    body = f"Draft pull request: {pr_url or 'pending'}"
    for tool in ("addOrEditJiraIssueComment", "transitionJiraIssue"):
        _call(client, tool, {"issue": key, "body": body, "always_ask": True})


def _call(client, tool: str, payload: dict) -> dict:
    if tool in DENIED_TOOLS:
        raise PrError(f"{tool} is denied")
    if tool not in {ALLOWED_PR_TOOL, "post_commit_status", *ATLASSIAN_WRITE_TOOLS}:
        raise PrError(f"{tool} is not an allowed PR-stage tool")
    result = client.call(tool, payload)
    if not isinstance(result, dict):
        raise PrError("tool result must be an object")
    return result


def _write_pr(run_dir, title, body, branch, base, pushed, pr_url, cost) -> None:
    payload = {
        "base": base,
        "body": body,
        "branch": branch,
        "cost_cents": cost,
        "draft": True,
        "pr_url": pr_url,
        "pushed": pushed,
        "title": title,
        "tool": ALLOWED_PR_TOOL,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "pr.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run_dir / "pr-body.md").write_text(body, encoding="utf-8")


def _book(run_dir, run_id, amount, model, effort, stage_cap, run_cap, spent) -> None:
    budget = authorize_session_cents(
        spent_cents=spent,
        run_cap_cents=run_cap,
        stage_cap_cents=stage_cap,
        stage="pr",
    )
    if amount > budget:
        raise BudgetHardStop(
            "pr exceeds the stage budget",
            spent_cents=spent,
            cap_cents=run_cap,
            stage="pr",
        )
    Ledger(run_dir / "ledger.jsonl").append(
        LedgerEntry(
            run_id=run_id,
            stage="pr",
            amount_cents=amount,
            provider="anthropic",
            model=model,
            effort=effort,
            note="rendered",
            budget_cents=budget,
        ),
        run_cap_cents=run_cap,
    )


def _counts(findings: list[dict]) -> dict[str, int]:
    counts = {"Important": 0, "Nit": 0, "Pre-existing": 0}
    for finding in findings:
        tag = str(finding.get("tag") or "")
        if tag in counts:
            counts[tag] += 1
    return counts


def _ledger_cost(run_dir: Path) -> int:
    path = run_dir / "ledger.jsonl"
    if not path.is_file():
        return 0
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            total += int(json.loads(line).get("amount_cents") or 0)
    return total


def _load(path: Path) -> dict:
    if not path.is_file():
        raise PrError(f"missing {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _head(repo: Path) -> str:
    completed = _git(repo, "rev-parse", "HEAD")
    return completed.stdout.strip()


_TOKEN_PREFIXES = ("gh" + "p_", "github_" + "pat_", "sk-" + "ant-")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    for arg in args:
        credential = arg.startswith(_TOKEN_PREFIXES) or ("@" in arg and "://" in arg)
        if credential:
            raise PrError("refusing a credential in a git argument")
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise PrError(completed.stderr.strip() or "git failed")
    return completed
