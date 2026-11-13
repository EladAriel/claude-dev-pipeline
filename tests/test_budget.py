from decimal import Decimal

import pytest

from claude_dev_pipeline.budget import (
    BudgetHardStop,
    authorize_session_cents,
    session_cost_usd,
    to_cents,
)


def test_triage_estimate_matches_the_design_table():
    cost = session_cost_usd(
        model="claude-haiku-5.5",
        uncached_input_tokens=10_000,
        cache_write_tokens=5_000,
        cache_read_tokens=30_000,
        output_tokens=3_000,
        active_seconds=180,
    )
    assert cost == Decimal("0.007425")
    assert to_cents(cost) == 1


def test_haiku_long_context_and_runtime_and_search():
    short = session_cost_usd(model="claude-haiku-5.5", uncached_input_tokens=1_000_000)
    long = session_cost_usd(
        model="claude-haiku-5.5",
        uncached_input_tokens=1_000_000,
        prompt_over_100k=True,
    )
    assert short == Decimal("0.10")
    assert long == Decimal("0.50")
    assert session_cost_usd(model="claude-sonnet-5.5", active_seconds=3600) == Decimal("0.08")
    assert session_cost_usd(model="gpt-6.1-sol", web_searches=1) == Decimal("0.01")


def test_authorize_uses_the_smaller_remaining_budget():
    assert (
        authorize_session_cents(
            spent_cents=1999,
            run_cap_cents=2000,
            stage_cap_cents=300,
            stage="implement",
        )
        == 1
    )


def test_authorize_hard_stops_when_the_run_is_spent():
    with pytest.raises(BudgetHardStop):
        authorize_session_cents(spent_cents=2000, run_cap_cents=2000, stage_cap_cents=10)


def test_float_money_is_rejected():
    with pytest.raises(TypeError):
        session_cost_usd(model="claude-opus-5.5", active_seconds=1.5)
