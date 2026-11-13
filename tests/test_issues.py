import json

import pytest

from claude_dev_pipeline.issues import issue_body, load_tasks, plan_status_write


def test_issue_body_contains_one_json_block(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"tasks": [{"id": "01", "title": "Add parser", "blocked_by": []}]}),
        encoding="utf-8",
    )
    task = load_tasks(plan)[0]
    body = issue_body(task)
    start = body.index("```json\n") + len("```json\n")
    end = body.index("\n```", start)
    payload = json.loads(body[start:end])
    assert payload["pipeline_task"]["id"] == "01"
    assert payload["pipeline_task"]["title"] == "Add parser"


def test_status_write_creates_until_a_marker_exists():
    created = plan_status_write(issue_number=4, status="running", existing_comments=[])
    assert created.comment_action == "create"
    assert created.comment_id is None
    updated = plan_status_write(
        issue_number=4,
        status="pr-open",
        existing_comments=[{"id": 3, "body": "note"}, {"id": 8, "body": created.comment_body}],
        pr_url="https://github.com/example/repo/pull/1",
    )
    assert updated.comment_action == "update"
    assert updated.comment_id == 8
    assert updated.add_labels == ("pipeline:pr-open",)
    assert "pipeline:running" in updated.remove_labels
    assert "https://github.com/example/repo/pull/1" in updated.comment_body


def test_unknown_status_and_cycles_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        plan_status_write(issue_number=1, status="shipped", existing_comments=[])
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "tasks": [
                    {"id": "01", "title": "A", "blocked_by": ["02"]},
                    {"id": "02", "title": "B", "blocked_by": ["01"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="cycle"):
        load_tasks(plan)
