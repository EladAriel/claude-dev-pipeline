"""pipeline CLI. Scaffold commands print plans and issue payloads; they do not call APIs."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from claude_dev_pipeline import __version__
from claude_dev_pipeline.budget import format_usd
from claude_dev_pipeline.config import (
    effective_model,
    load_yaml,
    parse_caps,
    run_cap_cents,
    stage_enabled,
)
from claude_dev_pipeline.db import init_db, latest_run, rebuild_db, write_run_artifact
from claude_dev_pipeline.intake import (
    IntakeError,
    intake_from_issue,
    intake_from_spec,
    intake_from_task,
)
from claude_dev_pipeline.issues import (
    issue_drafts,
    load_tasks,
    plan_status_write,
    write_outbox,
)
from claude_dev_pipeline.mcp_policy import validate_mcp_config
from claude_dev_pipeline.plan import STAGES, Stage, require_gates
from claude_dev_pipeline.scan import load_allowlist, scan_tree


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "scan-paths":
        return _scan(args.root or Path.cwd())
    if args.command == "db":
        return _db(args)
    if args.command == "status":
        return _status(args)
    if args.command == "import":
        return _import_plan(args)
    if args.command == "issue-status":
        return _issue_status(args)
    if args.command == "run":
        return _run(args)
    if args.dry_run:
        return _dry_run(args)
    parser.print_help(sys.stderr)
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pipeline")
    parser.add_argument("--version", action="version", version=f"pipeline {__version__}")
    parser.add_argument("--dry-run", action="store_true", help="print the stage plan and exit")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--models", type=Path)
    parser.add_argument("--budgets", type=Path)
    parser.add_argument("--root", type=Path)
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="record a dry-run intake and print the stage plan")
    run.add_argument("task", nargs="?", help="task text; GitHub issues remain the source of truth")
    run.add_argument("--spec", type=Path)
    run.add_argument("--issue", type=int)
    run.add_argument("--issue-snapshot", type=Path)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--config", type=Path)
    run.add_argument("--models", type=Path)
    run.add_argument("--budgets", type=Path)
    run.add_argument("--db", type=Path)
    run.add_argument("--runs-dir", type=Path)

    status = sub.add_parser("status", help="print the latest local run")
    status.add_argument("--db", type=Path)

    imported = sub.add_parser("import", help="turn plan.json into one GitHub issue per task")
    imported.add_argument("plan", type=Path)
    imported.add_argument(
        "--apply",
        action="store_true",
        help="write the issue payloads to a local outbox; still does not call GitHub",
    )
    imported.add_argument("--outbox", type=Path)

    issue_status = sub.add_parser("issue-status", help="stub a pipeline:* label and status comment")
    issue_status.add_argument("--issue", type=int, required=True)
    issue_status.add_argument("--set", dest="status_value", required=True)
    issue_status.add_argument("--comments", type=Path)
    issue_status.add_argument("--pr", dest="pr_url")
    issue_status.add_argument("--apply", action="store_true")
    issue_status.add_argument("--outbox", type=Path)

    database = sub.add_parser("db")
    database.add_argument("subcommand", choices=["init", "rebuild"])
    database.add_argument("--db", type=Path)
    database.add_argument("--runs", type=Path)

    scan = sub.add_parser("scan-paths")
    scan.add_argument("--root", type=Path)
    return parser


def _run(args: argparse.Namespace) -> int:
    selected = [args.task is not None, args.spec is not None, args.issue is not None]
    if sum(selected) > 1:
        print("pass only one of a task, --spec, or --issue", file=sys.stderr)
        return 2
    if not any(selected):
        if args.dry_run:
            return _dry_run(args)
        print(
            'usage: pipeline run "<task>" | pipeline run --spec FILE | pipeline run --issue N',
            file=sys.stderr,
        )
        return 2
    loaded = _load(args)
    if isinstance(loaded, int):
        return loaded
    try:
        if args.issue is not None:
            snapshot = args.issue_snapshot or Path("issues") / f"{args.issue}.json"
            intake = intake_from_issue(args.issue, snapshot)
        elif args.spec is not None:
            intake = intake_from_spec(args.spec)
        else:
            intake = intake_from_task(args.task or "")
    except IntakeError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    models, pipeline, caps, cap = loaded
    run_id = "run-" + uuid.uuid4().hex[:12]
    started = datetime.now(UTC).replace(microsecond=0).isoformat()
    print("claude-dev-pipeline scaffold")
    print("mode: dry-run")
    print("api-calls: none")
    print("merge: none")
    print(f"run-id: {run_id}")
    print(f"intake: {intake.kind}")
    print(f"ref: {intake.ref}")
    print("pr: none")
    print(f"run-cap: {format_usd(cap)}")
    _print_stages(models, pipeline, caps)
    payload = {
        "run_id": run_id,
        "repo": None,
        "issue_ref": str(intake.issue_number) if intake.issue_number is not None else None,
        "pipeline_version": __version__,
        "started_at": started,
        "ended_at": started,
        "status": "dry-run",
        "risk_tier": None,
        "review_depth": None,
        "run_cap_usd": cap / 100,
        "outcome": None,
        "verified_by": None,
        "intake_kind": intake.kind,
        "intake_ref": intake.ref,
        "pr_url": None,
    }
    runs_dir = args.runs_dir or Path("runs")
    db_path = args.db or Path("runs/pipeline.db")
    write_run_artifact(runs_dir, db_path, payload)
    print(f"recorded: {db_path}")
    return 0


def _status(args: argparse.Namespace) -> int:
    db_path = args.db or Path("runs/pipeline.db")
    row = latest_run(db_path)
    print("claude-dev-pipeline scaffold")
    print("api-calls: none")
    if row is None:
        print("status: none")
        print("pr: none")
        return 0
    print(f"run-id: {row['run_id']}")
    print(f"status: {row['status']}")
    print(f"intake: {row.get('intake_kind') or '-'}")
    print(f"ref: {row.get('intake_ref') or '-'}")
    print(f"issue: {row.get('issue_ref') or '-'}")
    print(f"pr: {row.get('pr_url') or 'none'}")
    return 0


def _import_plan(args: argparse.Namespace) -> int:
    if not args.plan.is_file():
        print(f"plan not found: {args.plan}", file=sys.stderr)
        return 2
    try:
        drafts = issue_drafts(load_tasks(args.plan))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("claude-dev-pipeline scaffold")
    print("mode: apply-stub" if args.apply else "mode: dry-run")
    print("api-calls: none")
    print(f"issues: {len(drafts)}")
    for draft in drafts:
        print(
            f"- id={draft.task_id} title={draft.title} "
            f"labels={','.join(draft.labels)} status-comment=create"
        )
    if not args.apply:
        return 0
    outbox = args.outbox or Path("runs/issue-outbox.json")
    write_outbox(
        outbox,
        {
            "api-calls": "none",
            "issues": [
                {
                    "body": draft.body,
                    "labels": list(draft.labels),
                    "status_comment": draft.status_comment,
                    "task_id": draft.task_id,
                    "title": draft.title,
                }
                for draft in drafts
            ],
            "kind": "import",
            "network": False,
        },
    )
    print(f"stub: wrote {len(drafts)} issue drafts to {outbox}")
    return 0


def _issue_status(args: argparse.Namespace) -> int:
    comments: list[dict] = []
    if args.comments is not None:
        if not args.comments.is_file():
            print(f"comments not found: {args.comments}", file=sys.stderr)
            return 2
        loaded = json.loads(args.comments.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            print("comments file must be a list", file=sys.stderr)
            return 2
        comments = loaded
    try:
        operation = plan_status_write(
            issue_number=args.issue,
            status=args.status_value,
            existing_comments=comments,
            pr_url=args.pr_url,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("claude-dev-pipeline scaffold")
    print("mode: apply-stub" if args.apply else "mode: dry-run")
    print("api-calls: none")
    print(f"issue: {operation.issue_number}")
    print(f"status: {operation.status}")
    print(f"labels-add: {','.join(operation.add_labels)}")
    print(f"labels-remove: {','.join(operation.remove_labels)}")
    if operation.comment_action == "update":
        print(f"comment: update {operation.comment_id}")
    else:
        print("comment: create")
    if not args.apply:
        return 0
    outbox = args.outbox or Path("runs/issue-status-outbox.json")
    write_outbox(
        outbox,
        {
            "api-calls": "none",
            "comment_action": operation.comment_action,
            "comment_body": operation.comment_body,
            "comment_id": operation.comment_id,
            "issue": operation.issue_number,
            "kind": "issue-status",
            "labels_add": list(operation.add_labels),
            "labels_remove": list(operation.remove_labels),
            "network": False,
            "status": operation.status,
        },
    )
    print(f"stub: wrote status operation to {outbox}")
    return 0


def _dry_run(args: argparse.Namespace) -> int:
    loaded = _load(args)
    if isinstance(loaded, int):
        return loaded
    models, pipeline, caps, cap = loaded
    print("claude-dev-pipeline scaffold")
    print("mode: dry-run")
    print("api-calls: none")
    print("merge: none")
    print(f"run-cap: {format_usd(cap)}")
    _print_stages(models, pipeline, caps)
    return 0


def _load(args: argparse.Namespace) -> tuple[dict, dict, dict[str, int], int] | int:
    require_gates()
    models_path = args.models or Path("config/models.yaml")
    budgets_path = args.budgets or Path("config/budgets.yaml")
    models = load_yaml(models_path) if models_path.exists() else {}
    budgets = load_yaml(budgets_path) if budgets_path.exists() else {}
    pipeline: dict = {}
    if args.config is not None:
        if not args.config.exists():
            print(f"config not found: {args.config}", file=sys.stderr)
            return 2
        pipeline = load_yaml(args.config)
        problems = validate_mcp_config(pipeline)
        if problems:
            print("mcp policy failed:", file=sys.stderr)
            for problem in problems:
                print(f"- {problem}", file=sys.stderr)
            return 1
    caps = parse_caps(budgets)
    return models, pipeline, caps, run_cap_cents(budgets, pipeline)


def _print_stages(models: dict, pipeline: dict, caps: dict[str, int]) -> None:
    print("stages:")
    for stage in STAGES:
        print(_format_stage(stage, models, pipeline, caps))
    print("mcp-contacted: none")
    if pipeline:
        print("mcp:")
        for line in _format_mcp(pipeline):
            print(line)
    else:
        print("mcp: no repo config loaded")


def _format_stage(stage: Stage, models: dict, pipeline: dict, caps: dict[str, int]) -> str:
    model = effective_model(stage, models, pipeline)
    cents = 0 if stage.budget_key == "none" else caps[stage.budget_key]
    skills = ",".join(stage.skills) if stage.skills else "-"
    flags = []
    if stage.conditional:
        flags.append("conditional")
    if not stage_enabled(pipeline, stage.key):
        flags.append("disabled")
    flag_text = f" flags={','.join(flags)}" if flags else ""
    notes = f" notes={stage.notes}" if stage.notes else ""
    return (
        f"- order={stage.order} key={stage.key} model={model['model']} "
        f"effort={model['effort']} cap={format_usd(cents)} skills={skills} "
        f"verify={stage.verify} rollback={stage.rollback}{flag_text}{notes}"
    )


def _format_mcp(pipeline: dict) -> list[str]:
    lines = []
    for name, spec in (pipeline.get("mcp_servers") or {}).items():
        auth = spec.get("auth") or {}
        vault = auth.get("vault") or auth.get("read_vault")
        lines.append(
            f"- name={name} enabled={str(spec.get('enabled')).lower()} "
            f"type={spec.get('type')} url={spec.get('url')} vault={vault}"
        )
    code_graph = pipeline.get("code_graph") or {}
    lines.append(
        "- name=code_graph "
        f"enabled={str(code_graph.get('enabled')).lower()} "
        f"transport={code_graph.get('transport')} mcp=false"
    )
    return lines


def _db(args: argparse.Namespace) -> int:
    if args.subcommand == "init":
        if args.db is None:
            print("db init requires --db", file=sys.stderr)
            return 2
        init_db(args.db)
        print(f"initialized {args.db}")
        return 0
    if args.subcommand == "rebuild":
        if args.db is None or args.runs is None:
            print("db rebuild requires --runs and --db", file=sys.stderr)
            return 2
        count = rebuild_db(args.runs, args.db)
        print(f"indexed {count} runs into {args.db}")
        return 0
    print(
        "usage: pipeline db init --db PATH | pipeline db rebuild --runs DIR --db PATH",
        file=sys.stderr,
    )
    return 2


def _scan(root: Path) -> int:
    findings = scan_tree(root, load_allowlist(root / ".pathscan-allow"))
    for finding in findings:
        print(f"{finding.path}:{finding.line}: {finding.rule}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
