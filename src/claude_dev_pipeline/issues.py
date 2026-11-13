"""GitHub issue drafts and the in-place status comment.

The scaffold builds these payloads and does not call GitHub.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

STATUSES = ("queued", "running", "review", "pr-open", "blocked", "done")
STATUS_MARKER = "<!-- pipeline-status -->"
TASK_MARKER = "<!-- pipeline-task -->"


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    body: str
    blocked_by: tuple[str, ...]


@dataclass(frozen=True)
class IssueDraft:
    task_id: str
    title: str
    body: str
    labels: tuple[str, ...]
    status_comment: str


@dataclass(frozen=True)
class StatusWrite:
    """One label change plus one status comment, created or updated in place."""

    issue_number: int
    status: str
    add_labels: tuple[str, ...]
    remove_labels: tuple[str, ...]
    comment_action: str
    comment_id: int | None
    comment_body: str


def load_tasks(path: Path) -> list[Task]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_tasks = data["tasks"] if isinstance(data, dict) else data
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("plan must contain a non-empty tasks list")
    tasks: list[Task] = []
    for item in raw_tasks:
        if not isinstance(item, dict):
            raise ValueError("each task must be a mapping")
        task_id = str(item.get("id", "")).strip()
        title = str(item.get("title", "")).strip()
        body = str(item.get("body", "")).strip()
        blocked = tuple(str(dep) for dep in item.get("blocked_by") or [])
        if not task_id or not title:
            raise ValueError("each task needs an id and a title")
        tasks.append(Task(task_id, title, body, blocked))
    _validate_graph(tasks)
    return tasks


def issue_drafts(tasks: list[Task]) -> list[IssueDraft]:
    return [
        IssueDraft(
            task_id=task.id,
            title=task.title,
            body=issue_body(task),
            labels=("pipeline:queued",),
            status_comment=status_comment("queued", None),
        )
        for task in tasks
    ]


def issue_body(task: Task) -> str:
    payload = {
        "pipeline_task": {
            "blocked_by": list(task.blocked_by),
            "id": task.id,
            "title": task.title,
        }
    }
    block = json.dumps(payload, indent=2, sort_keys=True)
    parts = [part for part in (task.body.strip(),) if part]
    parts.append(f"{TASK_MARKER}\n```json\n{block}\n```")
    return "\n\n".join(parts)


def status_comment(status: str, pr_url: str | None) -> str:
    _check_status(status)
    link = pr_url if pr_url else "none"
    return f"{STATUS_MARKER}\n**pipeline status:** {status}\n**pr:** {link}"


def plan_status_write(
    *,
    issue_number: int,
    status: str,
    existing_comments: list[dict],
    pr_url: str | None = None,
) -> StatusWrite:
    """Choose labels and whether to create or update the single status comment."""
    _check_status(status)
    label = f"pipeline:{status}"
    remove = tuple(f"pipeline:{name}" for name in STATUSES if name != status)
    comment_id = _status_comment_id(existing_comments)
    return StatusWrite(
        issue_number=issue_number,
        status=status,
        add_labels=(label,),
        remove_labels=remove,
        comment_action="update" if comment_id is not None else "create",
        comment_id=comment_id,
        comment_body=status_comment(status, pr_url),
    )


def write_outbox(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _status_comment_id(comments: list[dict]) -> int | None:
    for comment in comments:
        body = str(comment.get("body", ""))
        if STATUS_MARKER in body:
            return int(comment["id"])
    return None


def _check_status(status: str) -> None:
    if status not in STATUSES:
        known = ", ".join(STATUSES)
        raise ValueError(f"unknown status {status!r}; expected one of {known}")


def _validate_graph(tasks: list[Task]) -> None:
    ids = [task.id for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task id")
    known = set(ids)
    graph = {task.id: task.blocked_by for task in tasks}
    for task in tasks:
        for dep in task.blocked_by:
            if dep == task.id or dep not in known:
                raise ValueError(f"task {task.id} is blocked by unknown id {dep}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(node: str) -> None:
        if node in visiting:
            raise ValueError(f"task graph has a cycle at {node}")
        if node in visited:
            return
        visiting.add(node)
        for dep in graph[node]:
            walk(dep)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        walk(node)
