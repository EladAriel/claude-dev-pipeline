"""The stage plan the dry-run prints. No stage calls a model."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_CAPS_CENTS: dict[str, int] = {
    "triage": 10,
    "spec": 300,
    "plan": 200,
    "implement": 300,
    "verify": 10,
    "simplify": 50,
    "risk": 5,
    "finder-bugs": 150,
    "finder-standards": 50,
    "finder-history": 5,
    "security": 50,
    "verifier": 60,
    "rank": 30,
    "fix-round": 150,
    "re-review": 75,
    "pr": 25,
    "retro": 75,
    "run": 2000,
    "codex": 150,
}

DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "triage": {"provider": "anthropic", "model": "claude-haiku-5.5", "effort": "low"},
    "spec": {"provider": "anthropic", "model": "claude-opus-5.5", "effort": "medium"},
    "plan": {"provider": "anthropic", "model": "claude-opus-5.5", "effort": "medium"},
    "implement": {"provider": "anthropic", "model": "claude-sonnet-5.5", "effort": "high"},
    "verify": {"provider": "anthropic", "model": "claude-haiku-5.5", "effort": "low"},
    "simplify": {"provider": "anthropic", "model": "claude-sonnet-5.5", "effort": "medium"},
    "risk": {"provider": "anthropic", "model": "claude-haiku-5.5", "effort": "low"},
    "finder-bugs": {"provider": "anthropic", "model": "claude-opus-5.5", "effort": "medium"},
    "finder-standards": {"provider": "anthropic", "model": "claude-sonnet-5.5", "effort": "medium"},
    "finder-history": {"provider": "anthropic", "model": "claude-haiku-5.5", "effort": "low"},
    "security": {"provider": "anthropic", "model": "claude-sonnet-5.5", "effort": "medium"},
    "verifier": {"provider": "anthropic", "model": "claude-sonnet-5.5", "effort": "low"},
    "rank": {"provider": "anthropic", "model": "claude-sonnet-5.5", "effort": "low"},
    "pr": {"provider": "anthropic", "model": "claude-haiku-5.5", "effort": "low"},
    "retro": {"provider": "anthropic", "model": "claude-sonnet-5.5", "effort": "medium"},
    "codex": {"provider": "openai", "model": "gpt-6.1-sol", "effort": "none"},
}


@dataclass(frozen=True)
class Stage:
    order: str
    key: str
    model_key: str
    budget_key: str
    skills: tuple[str, ...] = ()
    conditional: bool = False
    notes: str = ""
    verify: str = ""
    rollback: str = ""


def _stage(
    order: str,
    key: str,
    model_key: str,
    budget_key: str,
    skills: tuple[str, ...] = (),
    *,
    conditional: bool = False,
    notes: str = "",
    verify: str,
    rollback: str,
) -> Stage:
    return Stage(
        order=order,
        key=key,
        model_key=model_key,
        budget_key=budget_key,
        skills=skills,
        conditional=conditional,
        notes=notes,
        verify=verify,
        rollback=rollback,
    )


_REVERT = "disable the stage and revert its PR"

STAGES: tuple[Stage, ...] = (
    _stage(
        "0",
        "triage",
        "triage",
        "triage",
        ("triage",),
        notes="proposal only; no tracker writes and no MCP tools",
        verify="items without the owner label produce no model call",
        rollback=_REVERT,
    ),
    _stage(
        "1",
        "spec",
        "spec",
        "spec",
        ("grill-with-docs", "to-spec"),
        verify="the spec artifact has acceptance ids, seams, and e2e scenarios",
        rollback="stages.spec.enabled false, then git revert the spec commit",
    ),
    _stage(
        "2",
        "plan",
        "plan",
        "plan",
        ("to-tickets",),
        verify="the plan artifact is a valid ticket DAG",
        rollback="stages.plan.enabled false, then git revert the plan commit",
    ),
    _stage(
        "3",
        "implement",
        "implement",
        "implement",
        ("tdd", "implement"),
        notes="one session per ticket in plan order on agent/<run>-<ticket>; no publish",
        verify="each ticket has an impl report and a local commit, or the run stops blocked",
        rollback="stages.implement.enabled false, then git revert the implement commit",
    ),
    _stage(
        "4a",
        "verify-evidence",
        "verify",
        "verify",
        verify="the report contains the real command output",
        rollback=_REVERT,
    ),
    _stage(
        "4b",
        "simplify",
        "simplify",
        "simplify",
        verify="checks stay green, or the simplify commit is dropped",
        rollback="drop the simplify commit and disable the stage",
    ),
    _stage(
        "4c",
        "verify-e2e",
        "verify",
        "verify",
        notes="its own session, same verify cap",
        verify="every scenario passes or is marked not e2e-testable",
        rollback=_REVERT,
    ),
    _stage(
        "5.0",
        "risk",
        "risk",
        "risk",
        verify="protected paths force the high tier",
        rollback=_REVERT,
    ),
    _stage(
        "5a",
        "finder-bugs",
        "finder-bugs",
        "finder-bugs",
        verify="findings match the schema and the confidence floor",
        rollback=_REVERT,
    ),
    _stage(
        "5a",
        "finder-standards",
        "finder-standards",
        "finder-standards",
        ("code-review",),
        verify="findings quote a written rule",
        rollback=_REVERT,
    ),
    _stage(
        "5a",
        "finder-history",
        "finder-history",
        "finder-history",
        verify="pre-existing code is tagged and not filed as new",
        rollback=_REVERT,
    ),
    _stage(
        "5a",
        "security",
        "security",
        "security",
        verify="an open security finding blocks the PR stage",
        rollback=_REVERT,
    ),
    _stage(
        "5a",
        "codex-finder",
        "codex",
        "codex",
        notes="local codex exec --sandbox read-only; shared codex cap; not invoked here",
        verify="the sandbox argument stays read-only",
        rollback="disable the codex finder",
    ),
    _stage(
        "5b",
        "verifier-confirm",
        "verifier",
        "verifier",
        verify="the vote cites file and line and is independent of the other votes",
        rollback=_REVERT,
    ),
    _stage(
        "5b",
        "verifier-refute",
        "verifier",
        "verifier",
        verify="the vote is a fresh context with no other vote in its input",
        rollback=_REVERT,
    ),
    _stage(
        "5b",
        "codex-voter",
        "codex",
        "codex",
        notes="shared codex cap; not invoked here",
        verify="the vote is cross-family and read-only",
        rollback="disable the codex voter",
    ),
    _stage(
        "5c",
        "rank",
        "rank",
        "rank",
        verify="the summary starts with a count and every finding has a tag",
        rollback=_REVERT,
    ),
    _stage(
        "5d",
        "fix-round",
        "implement",
        "fix-round",
        ("tdd",),
        conditional=True,
        verify="the fix covers the bug class and checks stay green",
        rollback="revert the fix commit and stop the round",
    ),
    _stage(
        "5e",
        "re-review",
        "finder-bugs",
        "re-review",
        conditional=True,
        notes="diff-only; stops at round 3",
        verify="round 3 stops and asks the owner to reconsider",
        rollback="stop the loop and disable re-review",
    ),
    _stage(
        "6",
        "pr",
        "pr",
        "pr",
        ("pr",),
        notes="draft PR only; GitHub MCP tool is create_pull_request; no merge",
        verify="the PR is a draft and merge is not an enabled tool",
        rollback="close the draft PR and disable the stage",
    ),
    _stage(
        "7",
        "retro",
        "retro",
        "retro",
        ("retro",),
        notes="proposals only; nothing is applied",
        verify="retro items are proposals with evidence and are not applied",
        rollback=_REVERT,
    ),
    _stage(
        "local",
        "record-metrics",
        "none",
        "none",
        notes="inline eval inside the same run cap; no model and no extra budget",
        verify="run_metrics matches the ledger and records trajectory violations",
        rollback="rebuild the sqlite index from the run artifacts",
    ),
)


def require_gates(stages: tuple[Stage, ...] = STAGES) -> None:
    """Each stage carries a verification line and a rollback line."""
    missing = [stage.key for stage in stages if not stage.verify or not stage.rollback]
    if missing:
        raise ValueError(f"stages missing verify or rollback: {', '.join(missing)}")
