"""Append-only JSONL cost ledger. Artifacts are the source of truth."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from claude_dev_pipeline.budget import BudgetHardStop


@dataclass(frozen=True)
class LedgerEntry:
    run_id: str
    stage: str
    amount_cents: int
    provider: str = ""
    model: str = ""
    note: str = ""
    input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    web_searches: int = 0
    budget_cents: int = 0
    effort: str = ""


class LedgerError(Exception):
    """The ledger file is not readable JSONL."""


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def entries(self, run_id: str | None = None) -> list[LedgerEntry]:
        if not self.path.exists():
            return []
        rows: list[LedgerEntry] = []
        for line_no, raw in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise LedgerError(f"{self.path}:{line_no} is not json") from exc
            try:
                rows.append(
                    LedgerEntry(
                        run_id=str(payload["run_id"]),
                        stage=str(payload["stage"]),
                        amount_cents=int(payload["amount_cents"]),
                        provider=str(payload.get("provider", "")),
                        model=str(payload.get("model", "")),
                        note=str(payload.get("note", "")),
                        input_tokens=int(payload.get("input_tokens", 0)),
                        cache_write_tokens=int(payload.get("cache_write_tokens", 0)),
                        cache_read_tokens=int(payload.get("cache_read_tokens", 0)),
                        output_tokens=int(payload.get("output_tokens", 0)),
                        web_searches=int(payload.get("web_searches", 0)),
                        budget_cents=int(payload.get("budget_cents", 0)),
                        effort=str(payload.get("effort", "")),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise LedgerError(f"{self.path}:{line_no} is missing ledger fields") from exc
        if run_id is None:
            return rows
        return [row for row in rows if row.run_id == run_id]

    def spent_cents(self, run_id: str) -> int:
        return sum(row.amount_cents for row in self.entries(run_id))

    def append(self, entry: LedgerEntry, *, run_cap_cents: int) -> None:
        """Record a charge. Refuse, and do not write, if it would pass the run cap."""
        if entry.amount_cents < 0:
            raise ValueError("amount_cents must be >= 0")
        if run_cap_cents < 0:
            raise ValueError("run_cap_cents must be >= 0")
        spent = self.spent_cents(entry.run_id)
        if spent + entry.amount_cents > run_cap_cents:
            raise BudgetHardStop(
                f"refusing to book {entry.stage}; charge would pass the run cap",
                spent_cents=spent,
                cap_cents=run_cap_cents,
                stage=entry.stage,
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(entry), sort_keys=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
