"""pipeline CLI. Scaffold commands print plans and issue payloads; they do not call APIs."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from claude_dev_pipeline import __version__
from claude_dev_pipeline.budget import BudgetHardStop, format_usd, stage_run_cap
from claude_dev_pipeline.config import (
    effective_model,
    load_yaml,
    parse_caps,
    run_cap_cents,
    stage_enabled,
)
from claude_dev_pipeline.db import (
    aggregate_report,
    init_db,
    latest_run,
    rebuild_db,
    write_run_artifact,
)
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
from claude_dev_pipeline.lanes import (
    diff_stats,
    escalate_for_diff,
    fast_lane,
    limits_for,
    mark_escalated,
    write_fast_review,
    write_synthesized,
)
from claude_dev_pipeline.mcp_policy import validate_mcp_config
from claude_dev_pipeline.plan import STAGES, Stage, require_gates
from claude_dev_pipeline.scan import load_allowlist, scan_tree
from claude_dev_pipeline.stages.implement import (
    ImplementError,
    ImplementSkipped,
    execute_implement,
    fanout_lines,
)
from claude_dev_pipeline.stages.plan import PlanError, PlanSkipped, execute_plan
from claude_dev_pipeline.stages.pr import PrBlocked, PrError, PrSkipped, execute_pr
from claude_dev_pipeline.stages.retro import RetroError, RetroSkipped, execute_retro
from claude_dev_pipeline.stages.review import ReviewError, ReviewSkipped, execute_review
from claude_dev_pipeline.stages.spec import SpecError, SpecSkipped, execute_spec
from claude_dev_pipeline.stages.triage import TriageError, execute_triage
from claude_dev_pipeline.stages.verify import VerifyError, execute_verify
from claude_dev_pipeline.tiers import TierApprovalRequired, apply_routing_models, load_tiers


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
    if args.command == "report":
        return _report(args)
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
    run.add_argument(
        "--model-fixture",
        type=Path,
        help="recorded triage response; required to run triage, and never a live API call",
    )
    run.add_argument(
        "--spec-fixture",
        type=Path,
        help="recorded spec response; used only after triage routes, and never a live API call",
    )
    run.add_argument(
        "--plan-fixture",
        type=Path,
        help="recorded plan response; skipped when the spec needs a human",
    )
    run.add_argument(
        "--implement-fixture",
        type=Path,
        help="recorded per-ticket edits; never publishes the target repo",
    )
    run.add_argument(
        "--verify-fixture",
        type=Path,
        help="recorded failure summary; not read when every check passes",
    )
    run.add_argument(
        "--review-fixture",
        type=Path,
        help="recorded finder and vote rounds; Codex stays a local subprocess",
    )
    run.add_argument(
        "--no-push",
        action="store_true",
        help="render pr.json and pr-body.md without pushing or calling MCP",
    )
    run.add_argument(
        "--retro-fixture",
        type=Path,
        help="recorded retro proposals; never applied and never a live API call",
    )
    run.add_argument("--target-repo", type=Path)
    run.add_argument("--tiers", type=Path)
    run.add_argument(
        "--approve-xl",
        action="store_true",
        help="allow a tier XL run; without this flag XL stops before any later stage",
    )

    report = sub.add_parser("report", help="aggregate inline metrics from the local index")
    report.add_argument("--db", type=Path)
    report.add_argument("--models", type=Path)

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
    work_cap = stage_run_cap(
        run_cap_cents=cap,
        retro_reserve_cents=caps["retro"],
        stage="work",
    )
    run_id = "run-" + uuid.uuid4().hex[:12]
    started = datetime.now(UTC).replace(microsecond=0).isoformat()
    runs_dir = args.runs_dir or Path("runs")
    db_path = args.db or Path("runs/pipeline.db")
    run_dir = runs_dir / run_id
    triage_status = "dry-run"
    risk_tier = None
    mode = "dry-run"
    spent_cents = 0
    exit_code = 0
    stop_reason = "dry-run" if args.dry_run or args.model_fixture is None else "success"

    def _finish(code: int, reason: str) -> int:
        if args.model_fixture is not None and not args.dry_run:
            _run_retro(
                args,
                run_dir=run_dir,
                run_id=run_id,
                models=models,
                pipeline=pipeline,
                caps=caps,
                cap=cap,
                stop_reason=reason,
            )
        print("claude-dev-pipeline scaffold")
        print(f"mode: {mode}")
        print("api-calls: none")
        print("merge: none")
        print(f"run-id: {run_id}")
        print(f"intake: {intake.kind}")
        print(f"ref: {intake.ref}")
        print("pr: none")
        print(f"run-cap: {format_usd(cap)}")
        print(f"stop-reason: {reason}")
        if args.dry_run or args.model_fixture is None:
            _print_stages(models, pipeline, caps)
        payload = {
            "run_id": run_id,
            "repo": None,
            "issue_ref": str(intake.issue_number) if intake.issue_number is not None else None,
            "pipeline_version": __version__,
            "started_at": started,
            "ended_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "status": triage_status,
            "risk_tier": risk_tier,
            "review_depth": risk_tier,
            "run_cap_usd": cap / 100,
            "outcome": reason,
            "verified_by": None,
            "intake_kind": intake.kind,
            "intake_ref": intake.ref,
            "pr_url": None,
        }
        write_run_artifact(runs_dir, db_path, payload)
        print(f"recorded: {db_path}")
        return code

    if args.model_fixture is not None and not args.dry_run:
        if intake.kind != "issue":
            print(
                "triage runs from an approved issue, not from free text or a spec", file=sys.stderr
            )
            return 2
        mode = "triage-fixture"
        if not stage_enabled(pipeline, "triage"):
            triage_status = "skipped"
            print("triage: disabled by stages.triage.enabled")
        else:
            snapshot = json.loads(
                (args.issue_snapshot or Path("issues") / f"{args.issue}.json").read_text(
                    encoding="utf-8"
                )
            )
            try:
                tiers = _tiers(args, pipeline)
                result = execute_triage(
                    snapshot=snapshot,
                    fixture_path=args.model_fixture,
                    run_dir=run_dir,
                    run_id=run_id,
                    spent_cents=0,
                    run_cap_cents=work_cap,
                    stage_cap_cents=caps["triage"],
                    tiers=tiers,
                    models=models,
                    approve_xl=bool(getattr(args, "approve_xl", False)),
                )
            except TierApprovalRequired as exc:
                print(str(exc), file=sys.stderr)
                return _finish(2, "tier-xl")
            except BudgetHardStop as exc:
                print(str(exc), file=sys.stderr)
                return _finish(1, "budget-halt")
            except TriageError as exc:
                print(str(exc), file=sys.stderr)
                return _finish(exc.code, "stop")
            triage_status = result.status
            risk_tier = result.risk_tier
            spent_cents = result.amount_cents
            if result.decision != "route":
                stop_reason = "triage-rejected"
            print(f"triage: {result.decision}")
            print(f"risk-tier: {result.risk_tier}")
            print(f"triage-cost: {format_usd(result.amount_cents)}")
            routing = _saved_routing(run_dir)
            if routing:
                budget = int(routing.get("budget_cents") or 0)
                if budget > 0:
                    work_cap = min(work_cap, budget)
                models = apply_routing_models(models, routing)
                print(f"tier: {routing.get('tier')} lane={routing.get('lane')}")
            on_fast = fast_lane(pipeline, routing) and result.decision == "route"
            spec_status = None
            plan_status = None
            if on_fast:
                issue = json.loads((run_dir / "issue.json").read_text(encoding="utf-8"))
                write_synthesized(run_dir, issue)
                print("fast-lane: skipped spec and plan")
                mode = "fast-lane"
            else:
                spec_status = _run_spec(
                    args,
                    run_dir=run_dir,
                    run_id=run_id,
                    models=models,
                    pipeline=pipeline,
                    caps=caps,
                    cap=work_cap,
                    spent_cents=spent_cents,
                )
                if spec_status == "failed":
                    return _finish(1, "stop")
                if spec_status == "succeeded":
                    mode = "spec-fixture"
                    triage_status = "succeeded"
                plan_status = _run_plan(
                    args,
                    run_dir=run_dir,
                    run_id=run_id,
                    models=models,
                    pipeline=pipeline,
                    caps=caps,
                    cap=work_cap,
                )
                if plan_status == "failed":
                    return _finish(1, "stop")
                if plan_status == "succeeded":
                    mode = "plan-fixture"
                    triage_status = "succeeded"
            impl_status = _run_implement(
                args,
                run_dir=run_dir,
                run_id=run_id,
                models=models,
                pipeline=pipeline,
                caps=caps,
                cap=work_cap,
            )
            if impl_status == "failed":
                return _finish(1, "stop")
            if impl_status == "succeeded":
                mode = "implement-fixture"
                triage_status = "succeeded"
            if impl_status == "blocked":
                mode = "implement-blocked"
                triage_status = "blocked"
                stop_reason = "implement-blocked"
            if impl_status == "succeeded":
                if on_fast:
                    files, line_count = diff_stats(run_dir)
                    bigger = escalate_for_diff(files, line_count, limits_for(pipeline))
                    if bigger:
                        mark_escalated(run_dir, bigger, "diff")
                        on_fast = False
                        print(f"fast-lane: escalated to {bigger}")
                verify_limit = limits_for(pipeline)["max_verify_failures"] if on_fast else 1
                failures = 0
                verify_status = None
                while failures < verify_limit:
                    verify_status = _run_verify(
                        args,
                        run_dir=run_dir,
                        run_id=run_id,
                        pipeline=pipeline,
                        caps=caps,
                        cap=work_cap,
                        effort=str((models.get("verify") or {}).get("effort") or "low"),
                    )
                    if verify_status != "failed":
                        break
                    failures += 1
                if on_fast and failures >= verify_limit and verify_status == "failed":
                    mark_escalated(run_dir, "standard", "verify")
                    print("fast-lane: escalated after repeated verify failures")
                if verify_status == "failed":
                    mode = "verify-failed"
                    triage_status = "failed"
                    exit_code = 1
                    stop_reason = "verify-failed"
                elif verify_status == "succeeded":
                    mode = "verify-pass"
                    triage_status = "succeeded"
                    if on_fast and not (run_dir / "fast.json").is_file():
                        write_fast_review(run_dir, pipeline)
                    review_status = _run_review(
                        args,
                        run_dir=run_dir,
                        run_id=run_id,
                        models=models,
                        pipeline=pipeline,
                        caps=caps,
                        cap=work_cap,
                    )
                    if review_status == "failed":
                        mode = "review-blocked"
                        triage_status = "failed"
                        exit_code = 1
                        stop_reason = "review-blocked"
                    elif review_status == "succeeded":
                        mode = "review-pass"
                        triage_status = "succeeded"
                        pr_status = _run_pr(
                            args,
                            run_dir=run_dir,
                            run_id=run_id,
                            models=models,
                            pipeline=pipeline,
                            caps=caps,
                            cap=work_cap,
                        )
                        if pr_status == "blocked":
                            mode = "pr-blocked"
                            triage_status = "blocked"
                            exit_code = 1
                            stop_reason = "pr-blocked"
                        elif pr_status == "rendered":
                            mode = "pr-rendered"
                        elif pr_status == "draft":
                            mode = "pr-draft"
    return _finish(exit_code, stop_reason)


def _fast_plan(run_dir: Path) -> dict:
    path = run_dir / "fast.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _tiers(args: argparse.Namespace, pipeline: dict) -> dict | None:
    if not stage_enabled(pipeline, "tiers"):
        return None
    path = getattr(args, "tiers", None) or Path("config/tiers.yaml")
    loaded = load_tiers(path)
    if not loaded.get("enabled", True):
        return None
    return loaded


def _saved_routing(run_dir: Path) -> dict:
    path = run_dir / "triage.json"
    if not path.is_file():
        return {}
    triage = json.loads(path.read_text(encoding="utf-8"))
    routing = triage.get("routing")
    return routing if isinstance(routing, dict) else {}


def _run_retro(
    args: argparse.Namespace,
    *,
    run_dir: Path,
    run_id: str,
    models: dict,
    pipeline: dict,
    caps: dict[str, int],
    cap: int,
    stop_reason: str,
) -> None:
    if not stage_enabled(pipeline, "retro"):
        print("retro: disabled by stages.retro.enabled")
        return
    model = str((models.get("retro") or {}).get("model") or "claude-haiku-5.5")
    effort = str((models.get("retro") or {}).get("effort") or "low")
    try:
        result = execute_retro(
            run_dir=run_dir,
            run_id=run_id,
            caps=caps,
            spent_cents=_spent_cents(run_dir),
            run_cap_cents=cap,
            stage_cap_cents=caps["retro"],
            stop_reason=stop_reason,
            fixture_path=getattr(args, "retro_fixture", None),
            model=model,
            effort=effort,
        )
    except RetroSkipped as exc:
        print(f"retro: skipped ({exc})")
        return
    except (BudgetHardStop, RetroError) as exc:
        print(str(exc), file=sys.stderr)
        return
    print(f"retro: {result.status}")
    print(f"retro-proposals: {result.proposals}")
    print("retro-applied: false")


def _report(args: argparse.Namespace) -> int:
    models_path = args.models or Path("config/models.yaml")
    models = load_yaml(models_path) if models_path.exists() else {}
    payload = aggregate_report(args.db or Path("runs/pipeline.db"), models)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _run_spec(
    args: argparse.Namespace,
    *,
    run_dir: Path,
    run_id: str,
    models: dict,
    pipeline: dict,
    caps: dict[str, int],
    cap: int,
    spent_cents: int,
) -> str | None:
    if args.spec_fixture is None:
        return None
    if not stage_enabled(pipeline, "spec"):
        print("spec: disabled by stages.spec.enabled")
        return "skipped"
    triage_path = run_dir / "triage.json"
    if not triage_path.is_file():
        print("spec: skipped (triage did not route)")
        return "skipped"
    triage = json.loads(triage_path.read_text(encoding="utf-8"))
    if triage.get("decision") != "route":
        print("spec: skipped (triage rejected)")
        return "skipped"
    spec_stage = next(stage for stage in STAGES if stage.key == "spec")
    model = effective_model(spec_stage, models, pipeline)
    brief = (run_dir / "agent-brief.md").read_text(encoding="utf-8")
    try:
        result = execute_spec(
            triage=triage,
            brief=brief,
            fixture_path=args.spec_fixture,
            run_dir=run_dir,
            run_id=run_id,
            spent_cents=spent_cents,
            run_cap_cents=cap,
            stage_cap_cents=caps["spec"],
            mcp_config=pipeline,
            model=model["model"],
            effort=model["effort"],
        )
    except SpecSkipped as exc:
        print(f"spec: skipped ({exc})")
        return "skipped"
    except BudgetHardStop as exc:
        print(str(exc), file=sys.stderr)
        return "failed"
    except SpecError as exc:
        print(str(exc), file=sys.stderr)
        return "failed"
    print(f"spec: needs_human={str(result.needs_human).lower()}")
    print(f"spec-cost: {format_usd(result.amount_cents)}")
    print("mcp-contacted: none")
    return "succeeded"


def _run_plan(
    args: argparse.Namespace,
    *,
    run_dir: Path,
    run_id: str,
    models: dict,
    pipeline: dict,
    caps: dict[str, int],
    cap: int,
) -> str | None:
    if args.plan_fixture is None:
        return None
    if not stage_enabled(pipeline, "plan"):
        print("plan: disabled by stages.plan.enabled")
        return "skipped"
    spec_path = run_dir / "spec.json"
    if not spec_path.is_file():
        print("plan: skipped (no spec)")
        return "skipped"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if spec.get("needs_human") is True:
        print("plan: skipped (spec needs a human)")
        return "skipped"
    plan_stage = next(stage for stage in STAGES if stage.key == "plan")
    model = effective_model(plan_stage, models, pipeline)
    spec_md = (run_dir / "spec.md").read_text(encoding="utf-8")
    try:
        result = execute_plan(
            spec=spec,
            spec_md=spec_md,
            fixture_path=args.plan_fixture,
            run_dir=run_dir,
            run_id=run_id,
            spent_cents=_spent_cents(run_dir),
            run_cap_cents=cap,
            stage_cap_cents=caps["plan"],
            mcp_config=pipeline,
            model=model["model"],
            effort=model["effort"],
        )
    except PlanSkipped as exc:
        print(f"plan: skipped ({exc})")
        return "skipped"
    except BudgetHardStop as exc:
        print(str(exc), file=sys.stderr)
        return "failed"
    except PlanError as exc:
        print(str(exc), file=sys.stderr)
        return "failed"
    print(f"plan: tickets={result.tickets}")
    print(f"plan-cost: {format_usd(result.amount_cents)}")
    print("mcp-contacted: none")
    return "succeeded"


def _run_implement(
    args: argparse.Namespace,
    *,
    run_dir: Path,
    run_id: str,
    models: dict,
    pipeline: dict,
    caps: dict[str, int],
    cap: int,
) -> str | None:
    if getattr(args, "implement_fixture", None) is None:
        return None
    if not stage_enabled(pipeline, "implement"):
        print("implement: disabled by stages.implement.enabled")
        return "skipped"
    plan_path = run_dir / "plan.json"
    if not plan_path.is_file():
        print("implement: skipped (no plan)")
        return "skipped"
    if args.target_repo is None:
        print("implement requires --target-repo", file=sys.stderr)
        return "failed"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    cfg = _implement_cfg(models, pipeline)
    try:
        result = execute_implement(
            plan=plan,
            fixture_path=args.implement_fixture,
            run_dir=run_dir,
            run_id=run_id,
            target_repo=args.target_repo,
            spent_cents=_spent_cents(run_dir),
            run_cap_cents=cap,
            stage_cap_cents=caps["implement"],
            implement_cfg=cfg,
            effort=str(cfg.get("effort") or "high"),
        )
    except ImplementSkipped as exc:
        print(f"implement: skipped ({exc})")
        return "skipped"
    except BudgetHardStop as exc:
        print(str(exc), file=sys.stderr)
        return "failed"
    except ImplementError as exc:
        print(str(exc), file=sys.stderr)
        return "failed"
    print(f"implement: {result.status} tickets={','.join(result.tickets)}")
    if result.stopped_at:
        print(f"implement-stopped: {result.stopped_at}")
    return result.status


def _run_verify(
    args: argparse.Namespace,
    *,
    run_dir: Path,
    run_id: str,
    pipeline: dict,
    caps: dict[str, int],
    cap: int,
    effort: str,
) -> str | None:
    if not stage_enabled(pipeline, "verify"):
        print("verify: disabled by stages.verify.enabled")
        return "skipped"
    try:
        result = execute_verify(
            run_dir=run_dir,
            run_id=run_id,
            config=pipeline,
            fixture_path=getattr(args, "verify_fixture", None),
            spent_cents=_spent_cents(run_dir),
            run_cap_cents=cap,
            stage_cap_cents=caps["verify"],
            effort=effort,
        )
    except BudgetHardStop as exc:
        print(str(exc), file=sys.stderr)
        return "failed"
    except VerifyError as exc:
        print(str(exc), file=sys.stderr)
        return "failed"
    print(f"verify: {result.status}")
    print(f"verify-model-called: {str(result.model_called).lower()}")
    print(f"verify-cost: {format_usd(result.amount_cents)}")
    return "failed" if result.status == "fail" else "succeeded"


def _run_review(
    args: argparse.Namespace,
    *,
    run_dir: Path,
    run_id: str,
    models: dict,
    pipeline: dict,
    caps: dict[str, int],
    cap: int,
) -> str | None:
    if not stage_enabled(pipeline, "review"):
        print("review: disabled by stages.review.enabled")
        return "skipped"
    if getattr(args, "review_fixture", None) is None:
        print("review: skipped (no fixture)")
        return "skipped"
    triage_path = run_dir / "triage.json"
    tier = "Standard"
    if triage_path.is_file():
        triage = json.loads(triage_path.read_text(encoding="utf-8"))
        routing = triage.get("routing") if isinstance(triage.get("routing"), dict) else {}
        tier = str(routing.get("review_depth") or triage.get("risk_tier") or tier)
    codex_cfg = (pipeline.get("reviewers") or {}).get("codex") or {}
    quick = str((pipeline.get("review") or {}).get("quick_finder_model") or "claude-sonnet-5.5")
    finder = str((models.get("finder-bugs") or {}).get("model") or "claude-opus-5.5")
    fast_plan = _fast_plan(run_dir)
    codex_on = codex_cfg.get("enabled", True) is not False
    if "codex" in fast_plan:
        codex_on = codex_on and bool(fast_plan["codex"])
    try:
        result = execute_review(
            run_dir=run_dir,
            run_id=run_id,
            fixture_path=args.review_fixture,
            triage_tier=tier,
            config=pipeline,
            spent_cents=_spent_cents(run_dir),
            run_cap_cents=cap,
            review_cap_cents=caps["finder-bugs"],
            codex_cap_cents=caps["codex"],
            enabled=True,
            codex_enabled=codex_on,
            quick_model=quick,
            finder_model=finder,
            only_checks=fast_plan.get("only_checks"),
        )
    except ReviewSkipped as exc:
        print(f"review: skipped ({exc})")
        return "skipped"
    except BudgetHardStop as exc:
        print(str(exc), file=sys.stderr)
        return "failed"
    except ReviewError as exc:
        print(str(exc), file=sys.stderr)
        return "failed"
    print(f"review: {result.status} depth={result.depth}")
    print(f"review-cross-model: {str(result.cross_model).lower()}")
    print(f"review-rounds: {result.rounds_run}")
    if result.security_block or result.status == "failure":
        return "failed"
    return "succeeded"


def _run_pr(
    args: argparse.Namespace,
    *,
    run_dir: Path,
    run_id: str,
    models: dict,
    pipeline: dict,
    caps: dict[str, int],
    cap: int,
) -> str | None:
    if not stage_enabled(pipeline, "pr"):
        print("pr: disabled by stages.pr.enabled")
        return "skipped"
    if getattr(args, "target_repo", None) is None:
        print("pr: skipped (no target repo)")
        return "skipped"
    plan_path = run_dir / "plan.json"
    branches: list[str] = []
    if plan_path.is_file():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        branches = [f"agent/{run_id}-{task['id']}" for task in plan.get("tasks") or []]
    issue_number = getattr(args, "issue", None)
    push = not getattr(args, "no_push", False)
    model = str((models.get("pr") or {}).get("model") or "claude-haiku-5.5")
    effort = str((models.get("pr") or {}).get("effort") or "low")
    try:
        result = execute_pr(
            run_dir=run_dir,
            run_id=run_id,
            target_repo=args.target_repo,
            base="main",
            ticket_branches=branches,
            spent_cents=_spent_cents(run_dir),
            run_cap_cents=cap,
            stage_cap_cents=caps["pr"],
            push=push,
            mcp=_RecordingMcp(run_dir / "pr-mcp.json") if push else None,
            model=model,
            effort=effort,
            issue_number=issue_number,
        )
    except PrSkipped as exc:
        print(f"pr: skipped ({exc})")
        return "skipped"
    except PrBlocked as exc:
        print(f"pr: blocked ({exc})", file=sys.stderr)
        return "blocked"
    except (BudgetHardStop, PrError) as exc:
        print(str(exc), file=sys.stderr)
        return "blocked"
    print(f"pr: {result.status}")
    print(f"pr-pushed: {str(result.pushed).lower()}")
    return result.status


class _RecordingMcp:
    """Offline stand-in. It records the tool call and does not open a socket."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[dict] = []

    def call(self, tool: str, payload: dict) -> dict:
        self.calls.append({"tool": tool, "payload": payload})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.calls, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if tool == "create_pull_request":
            return {"url": "local://draft"}
        return {"ok": True}


def _implement_cfg(models: dict, pipeline: dict) -> dict:
    cfg = dict(models.get("implement") or {})
    override = (pipeline.get("models") or {}).get("implement") or {}
    if isinstance(override, dict):
        if override.get("model"):
            cfg["model"] = override["model"]
        if override.get("effort"):
            cfg["effort"] = override["effort"]
        if override.get("allowlist"):
            cfg["allowlist"] = override["allowlist"]
    return cfg


def _spent_cents(run_dir: Path) -> int:
    ledger = run_dir / "ledger.jsonl"
    if not ledger.is_file():
        return 0
    total = 0
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if line.strip():
            total += int(json.loads(line)["amount_cents"])
    return total


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
    _print_fanout(args, models, pipeline)
    print(
        "review: Quick/Standard/Deep from triage and diff; "
        "finders bug-hunt, simplify, end-to-end; codex read-only; max 3 rounds"
    )
    print("pr: draft only; create_pull_request; no merge; --no-push renders locally")
    print("retro: metrics first; proposals are not applied; reserved inside the run cap")
    return 0


def _print_fanout(args: argparse.Namespace, models: dict, pipeline: dict) -> None:
    fixture = getattr(args, "plan_fixture", None)
    if fixture is None or not Path(fixture).is_file():
        print("implement-fanout: one session per ticket in plan order")
        return
    data = json.loads(Path(fixture).read_text(encoding="utf-8"))
    tasks = (data.get("artifact") or data).get("tasks") or []
    for line in fanout_lines(tasks, _implement_cfg(models, pipeline)):
        print(line)


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
