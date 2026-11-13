"""Session cost math and the run-budget hard stop.

Prices are the list prices recorded in the design on 2026-11-13. This module
does not fetch them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

RUNTIME_USD_PER_HOUR = Decimal("0.08")
WEB_SEARCH_USD = Decimal("0.01")
DEFAULT_RUN_CAP_CENTS = 2000


class BudgetHardStop(Exception):
    """The run cap or the remaining budget refuses another charge or session."""

    def __init__(self, message: str, *, spent_cents: int, cap_cents: int, stage: str = "") -> None:
        super().__init__(message)
        self.spent_cents = spent_cents
        self.cap_cents = cap_cents
        self.stage = stage


@dataclass(frozen=True)
class TokenPrices:
    """USD per million tokens: input, cache write, cache read, output."""

    input: Decimal
    cache_write: Decimal
    cache_read: Decimal
    output: Decimal


_HAIKU = TokenPrices(Decimal("0.10"), Decimal("0.125"), Decimal("0.01"), Decimal("0.50"))
_HAIKU_LONG = TokenPrices(Decimal("0.50"), Decimal("0.625"), Decimal("0.05"), Decimal("2.50"))
_SONNET = TokenPrices(Decimal("2"), Decimal("2.50"), Decimal("0.10"), Decimal("10"))
_OPUS = TokenPrices(Decimal("4"), Decimal("5"), Decimal("0.20"), Decimal("20"))
_CODEX = TokenPrices(Decimal("2"), Decimal("2.50"), Decimal("0.10"), Decimal("10"))

_PRICES = {
    "claude-haiku-5.5": _HAIKU,
    "claude-sonnet-5.5": _SONNET,
    "claude-opus-5.5": _OPUS,
    "gpt-6.1-sol": _CODEX,
}


def cheapest_model(models: list[str] | tuple[str, ...]) -> str | None:
    """Lowest input list price, then lowest output price. Unknown prices are skipped."""
    ranked: list[tuple[Decimal, Decimal, str]] = []
    for name in models:
        prices = _PRICES.get(name)
        if prices is None:
            continue
        ranked.append((prices.input, prices.output, name))
    if not ranked:
        return None
    ranked.sort()
    return ranked[0][2]


def to_cents(amount: Decimal) -> int:
    """Round a USD amount to integer cents, half up."""
    return int((amount * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _decimal(value: Decimal | int | str) -> Decimal:
    if isinstance(value, float):
        raise TypeError("pass Decimal, int, or str for money and durations")
    return value if isinstance(value, Decimal) else Decimal(value)


def session_cost_usd(
    *,
    model: str,
    uncached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
    output_tokens: int = 0,
    active_seconds: Decimal | int | str = 0,
    web_searches: int = 0,
    prompt_over_100k: bool = False,
) -> Decimal:
    """List cost for one session, including runtime and web search.

    For gpt-6.1-sol, ``cache_write_tokens`` is the 30-minute cache write.
    For Claude models it is the 5-minute cache write. Haiku's over-100k
    prices apply only when ``prompt_over_100k`` is set.
    """
    if model not in _PRICES:
        raise ValueError(f"unknown model {model!r}")
    prices = _HAIKU_LONG if model == "claude-haiku-5.5" and prompt_over_100k else _PRICES[model]
    tokens = (
        Decimal(uncached_input_tokens) * prices.input
        + Decimal(cache_write_tokens) * prices.cache_write
        + Decimal(cache_read_tokens) * prices.cache_read
        + Decimal(output_tokens) * prices.output
    ) / Decimal(1_000_000)
    runtime = (_decimal(active_seconds) / Decimal(3600)) * RUNTIME_USD_PER_HOUR
    return tokens + runtime + (Decimal(web_searches) * WEB_SEARCH_USD)


def stage_run_cap(*, run_cap_cents: int, retro_reserve_cents: int, stage: str) -> int:
    """Keep a retro slice inside the run cap so the last stage can still book."""
    if run_cap_cents < 0 or retro_reserve_cents < 0:
        raise ValueError("budget cents must be >= 0")
    if stage == "retro":
        return run_cap_cents
    reserve = min(retro_reserve_cents, run_cap_cents)
    return run_cap_cents - reserve


def authorize_session_cents(
    *,
    spent_cents: int,
    run_cap_cents: int,
    stage_cap_cents: int,
    stage: str = "",
) -> int:
    """Return the cents a new session may spend, or raise BudgetHardStop.

    The session cap is the minimum of the stage cap and the cents still
    left under the run cap. Zero or less is a hard stop: the session is
    not started.
    """
    if spent_cents < 0 or run_cap_cents < 0 or stage_cap_cents < 0:
        raise ValueError("budget cents must be >= 0")
    remaining = run_cap_cents - spent_cents
    if remaining <= 0 or stage_cap_cents <= 0:
        raise BudgetHardStop(
            f"run budget exhausted; refusing to start {stage or 'session'}",
            spent_cents=spent_cents,
            cap_cents=run_cap_cents,
            stage=stage,
        )
    return min(stage_cap_cents, remaining)


def format_usd(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    amount = abs(cents)
    return f"{sign}${amount // 100}.{amount % 100:02d}"
