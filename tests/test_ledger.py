import pytest

from claude_dev_pipeline.budget import BudgetHardStop
from claude_dev_pipeline.ledger import Ledger, LedgerEntry


def test_append_stops_at_the_run_cap_without_writing(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append(LedgerEntry("run-1", "triage", 1500), run_cap_cents=2000)
    ledger.append(LedgerEntry("run-1", "spec", 500), run_cap_cents=2000)
    with pytest.raises(BudgetHardStop):
        ledger.append(LedgerEntry("run-1", "plan", 1), run_cap_cents=2000)
    assert ledger.spent_cents("run-1") == 2000
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_other_runs_do_not_share_the_cap(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(LedgerEntry("run-1", "triage", 2000), run_cap_cents=2000)
    ledger.append(LedgerEntry("run-2", "triage", 10), run_cap_cents=2000)
    assert ledger.spent_cents("run-2") == 10
