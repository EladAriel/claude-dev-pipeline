"""Review stage. Claude finders come from fixtures. Codex is a local subprocess."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path

from claude_dev_pipeline.budget import BudgetHardStop, authorize_session_cents
from claude_dev_pipeline.ledger import Ledger, LedgerEntry

DEPTHS = ("Quick", "Standard", "Deep")
CHECKS = ("bug-hunt", "simplify", "end-to-end", "security")
CONFIDENCE_FLOOR = 80


class ReviewError(Exception):
    def __init__(self, message: str, *, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


class ReviewSkipped(Exception):
    pass


@dataclass(frozen=True)
class ReviewResult:
    status: str
    depth: str
    cross_model: bool
    rounds_run: int
    round_cap_reached: bool
    security_block: bool


def select_depth(
    *,
    triage_tier: str,
    files: list[str],
    diff_lines: int,
    protected: list[str],
    max_diff_lines: int,
) -> str:
    """Take the higher of the triage tier and a deterministic diff rule."""
    derived = "Quick"
    if _forces_deep(files, protected) or diff_lines > max_diff_lines:
        derived = "Deep"
    elif diff_lines > max(1, max_diff_lines // 2):
        derived = "Standard"
    triage = triage_tier if triage_tier in DEPTHS else "Standard"
    return DEPTHS[max(DEPTHS.index(triage), DEPTHS.index(derived))]


def execute_review(
    *,
    run_dir: Path,
    run_id: str,
    fixture_path: Path,
    triage_tier: str,
    config: dict,
    spent_cents: int,
    run_cap_cents: int,
    review_cap_cents: int,
    codex_cap_cents: int,
    enabled: bool = True,
    codex_enabled: bool = True,
    schema: Path | None = None,
    quick_model: str = "claude-sonnet-5.5",
    finder_model: str = "claude-opus-5.5",
) -> ReviewResult:
    if not enabled:
        raise ReviewSkipped("stages.review.enabled is false")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    diff = fixture.get("diff") or {}
    files = [str(item) for item in diff.get("files") or []]
    depth = select_depth(
        triage_tier=triage_tier,
        files=files,
        diff_lines=int(diff.get("lines") or 0),
        protected=[str(item) for item in (config.get("paths") or {}).get("protected") or []],
        max_diff_lines=int((config.get("limits") or {}).get("max_diff_lines") or 1500),
    )
    model = quick_model if depth == "Quick" else finder_model
    effort = "high" if depth == "Deep" else "medium"
    rounds = list(fixture.get("rounds") or [])
    max_rounds = int((config.get("review") or {}).get("max_rounds") or 3)
    max_nits = int((config.get("review") or {}).get("max_nits") or 3)
    seen: set[str] = set()
    kept: list[dict] = []
    scored_all: list[dict] = []
    fix_tickets: list[dict] = []
    rounds_run = 0
    cap_reached = False
    cross_model = False
    claude_cost = 0
    codex_cost = 0
    codex_tokens: dict[str, int | None] = {"input_tokens": None, "output_tokens": None}
    schema_path = schema or _default_schema()
    for index, round_data in enumerate(rounds[:max_rounds], start=1):
        rounds_run = index
        findings = _prepare_findings(round_data.get("findings") or [], model, seen)
        codex_findings, codex_votes, tokens, codex_amount, called = _codex_round(
            enabled=codex_enabled,
            schema=schema_path,
            output=run_dir / f"findings-codex-{index}.json",
            prompt=f"review round {index}",
            seen=seen,
        )
        if called:
            cross_model = True
            codex_tokens = tokens
            codex_cost += codex_amount
        findings.extend(codex_findings)
        votes = {str(key): list(value) for key, value in (round_data.get("votes") or {}).items()}
        for vote in list(round_data.get("codex_votes") or []) + codex_votes:
            votes.setdefault(str(vote.get("finding_id")), []).append(vote)
        scored = [_score(item, votes.get(item["id"], []), cross_model) for item in findings]
        scored_all.extend(scored)
        new_kept = _cap_nits([item for item in scored if item["kept"]], max_nits)
        kept.extend(new_kept)
        fix_tickets = _fix_tickets(new_kept)
        claude_cost += int((round_data.get("usage") or {}).get("amount_cents") or 0)
        blocking = any(item["blocks"] for item in new_kept)
        if not blocking and not fix_tickets:
            break
        if index == max_rounds and (blocking or fix_tickets):
            cap_reached = True
    if len(rounds) > max_rounds and cap_reached:
        cap_reached = True
    _book(
        run_dir,
        run_id,
        "review",
        claude_cost,
        model,
        effort,
        review_cap_cents,
        run_cap_cents,
        spent_cents,
    )
    if cross_model:
        _book(
            run_dir,
            run_id,
            "codex",
            codex_cost,
            "gpt-6.1-sol",
            "none",
            codex_cap_cents,
            run_cap_cents,
            spent_cents + claude_cost,
        )
    security_block = any(item["blocks"] and item["security"] for item in kept)
    status = "failure" if security_block or fix_tickets else "success"
    result = {
        "depth": depth,
        "cross_model": cross_model,
        "rounds_run": rounds_run,
        "round_cap_reached": cap_reached,
        "finder_model": model,
        "bug_hunt_passes": 2 if depth == "Deep" else 1,
        "checks": list(CHECKS),
        "findings": scored_all,
        "fix_tickets": fix_tickets,
        "security_block": security_block,
        "codex_tokens": codex_tokens,
        "summary": _summary(kept, security_block),
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "review.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run_dir / "review.md").write_text(result["summary"] + "\n", encoding="utf-8")
    payload = {
        "context": "pipeline/review",
        "state": status,
        "description": result["summary"].split("\n", 1)[0],
        "posted": False,
    }
    (run_dir / "pipeline-review-status.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ReviewResult(
        status=status,
        depth=depth,
        cross_model=cross_model,
        rounds_run=rounds_run,
        round_cap_reached=cap_reached,
        security_block=security_block,
    )


def _codex_round(
    *,
    enabled: bool,
    schema: Path,
    output: Path,
    prompt: str,
    seen: set[str],
) -> tuple[list[dict], list[dict], dict[str, int | None], int, bool]:
    empty = {"input_tokens": None, "output_tokens": None}
    if not enabled:
        return [], [], empty, 0, False
    try:
        data = _run_codex(schema=schema, output=output, prompt=prompt)
    except CodexError:
        return [], [], empty, 0, False
    findings = []
    for item in data.get("findings") or []:
        prepared = _stamp(item, "openai", "gpt-6.1-sol")
        if prepared["fingerprint"] in seen or prepared["confidence"] < CONFIDENCE_FLOOR:
            continue
        seen.add(prepared["fingerprint"])
        findings.append(prepared)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    tokens = data.get("tokens") or empty
    amount = int(usage.get("amount_cents") or 0)
    return findings, list(data.get("votes") or []), tokens, amount, True


def _run_codex(*, schema: Path, output: Path, prompt: str) -> dict:
    module = _adapter()
    try:
        return module.run_codex(schema=schema, output=output, prompt=prompt)
    except module.CodexError as exc:
        raise CodexError(str(exc)) from exc


def _adapter():
    path = _default_schema().with_name("adapter.py")
    spec = importlib.util.spec_from_file_location("codex_adapter", path)
    if spec is None or spec.loader is None:
        raise CodexError("codex adapter is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CodexError(Exception):
    pass


def _prepare_findings(raw: list[dict], model: str, seen: set[str]) -> list[dict]:
    prepared = []
    for item in raw:
        if item.get("style_only"):
            continue
        if int(item.get("confidence") or 0) < CONFIDENCE_FLOOR:
            continue
        stamped = _stamp(item, "claude", model)
        if stamped["fingerprint"] in seen:
            continue
        seen.add(stamped["fingerprint"])
        prepared.append(stamped)
    return prepared


def _stamp(item: dict, family: str, model: str) -> dict:
    summary = str(item.get("summary") or "")
    bug_class = str(item.get("bug_class") or item.get("check") or "unknown")
    path = str(item.get("file") or "")
    fingerprint = hashlib.sha256(f"{bug_class}|{path}|{summary}".encode()).hexdigest()[:16]
    return {
        "id": str(item.get("id") or fingerprint),
        "check": str(item.get("check") or "bug-hunt"),
        "family": family,
        "model": model,
        "file": path,
        "line_start": int(item.get("line_start") or 1),
        "line_end": int(item.get("line_end") or 1),
        "bug_class": bug_class,
        "summary": summary,
        "confidence": int(item.get("confidence") or 0),
        "security": bool(item.get("security")),
        "fingerprint": fingerprint,
    }


def _score(finding: dict, votes: list[dict], cross_model: bool) -> dict:
    yeses = [vote for vote in votes if _yes(vote)]
    families = {str(vote.get("family")) for vote in yeses}
    other_family = any(family != finding["family"] for family in families)
    survived = len(yeses) >= 2 and other_family
    security_kept = finding["security"] and len(yeses) >= 1
    tag = _tag(finding, yeses)
    kept = (survived or security_kept) and tag != "dropped"
    blocks = finding["security"] and security_kept and tag != "Pre-existing"
    if tag == "Pre-existing":
        blocks = False
        kept = survived or security_kept
    return {
        **finding,
        "survived": survived and cross_model,
        "kept": kept and (cross_model or security_kept),
        "tag": tag,
        "blocks": blocks and (cross_model or security_kept),
        "votes": len(votes),
    }


def _yes(vote: dict) -> bool:
    return str(vote.get("real")) == "yes" and int(vote.get("confidence") or 0) >= CONFIDENCE_FLOOR


def _tag(finding: dict, yeses: list[dict]) -> str:
    if finding.get("style_only"):
        return "dropped"
    if any(vote.get("pre_existing") for vote in yeses) or finding.get("pre_existing"):
        return "Pre-existing"
    severities = [str(vote.get("severity") or "") for vote in yeses]
    if severities and severities.count("important") >= severities.count("nit"):
        return "Important"
    if finding["security"]:
        return "Important"
    return "Nit"


def _cap_nits(findings: list[dict], max_nits: int) -> list[dict]:
    nits = 0
    kept = []
    for finding in findings:
        if finding["tag"] == "Nit":
            if nits >= max_nits:
                continue
            nits += 1
        kept.append(finding)
    return kept


def _fix_tickets(findings: list[dict]) -> list[dict]:
    tickets = []
    for finding in findings:
        if finding["tag"] not in {"Important"} and not finding["blocks"]:
            continue
        if not finding["kept"]:
            continue
        tickets.append(
            {
                "id": f"fix-{finding['id']}",
                "title": f"Fix {finding['bug_class']}",
                "body": finding["summary"],
                "acceptance_criteria": [],
                "files": [finding["file"]] if finding["file"] else ["unknown"],
                "seams": [finding["bug_class"]],
                "test_plan": finding["summary"],
                "blocked_by": [],
                "estimate": "S",
                "suggested_model": "claude-sonnet-5.5",
            }
        )
    return tickets


def _summary(findings: list[dict], security_block: bool) -> str:
    important = sum(1 for item in findings if item["tag"] == "Important")
    nits = sum(1 for item in findings if item["tag"] == "Nit")
    pre = sum(1 for item in findings if item["tag"] == "Pre-existing")
    total = important + nits + pre
    if total == 0 and not security_block:
        return "Nothing to fix."
    head = f"{total} findings: {important} Important, {nits} Nit, {pre} Pre-existing."
    if security_block:
        head += " Security blocks the run."
    return head


def _book(
    run_dir: Path,
    run_id: str,
    stage: str,
    amount: int,
    model: str,
    effort: str,
    stage_cap: int,
    run_cap: int,
    spent: int,
) -> None:
    budget = authorize_session_cents(
        spent_cents=spent,
        run_cap_cents=run_cap,
        stage_cap_cents=stage_cap,
        stage=stage,
    )
    if amount > budget:
        raise BudgetHardStop(
            f"{stage} exceeds the stage budget",
            spent_cents=spent,
            cap_cents=run_cap,
            stage=stage,
        )
    Ledger(run_dir / "ledger.jsonl").append(
        LedgerEntry(
            run_id=run_id,
            stage=stage,
            amount_cents=amount,
            provider="openai" if stage == "codex" else "anthropic",
            model=model,
            effort=effort,
            note="fixture",
            budget_cents=budget,
        ),
        run_cap_cents=run_cap,
    )


def _forces_deep(files: list[str], protected: list[str]) -> bool:
    markers = (
        "/auth/",
        "secret",
        ".github/workflows",
        "package.json",
        "requirements.txt",
        "go.mod",
    )
    for path in files:
        lowered = path.lower()
        if any(marker in lowered for marker in markers):
            return True
        for pattern in protected:
            prefix = pattern.split("*", 1)[0].rstrip("/")
            if prefix and prefix.lower() in lowered:
                return True
    return False


def _default_schema() -> Path:
    return Path(__file__).resolve().parents[3] / "reviewers" / "codex" / "finding.schema.json"
