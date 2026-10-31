import json
import threading
from datetime import datetime, timezone

import pytest

from ledger.spend import (
    Ledger,
    LedgerError,
    RunCapExceeded,
    SpendCapExceeded,
    cost_usd,
    load_prices,
    normalize_usage,
)

OCT = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
NOV = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, ts: datetime) -> None:
        self.ts = ts

    def __call__(self) -> datetime:
        return self.ts


def make(tmp_path, cap=1.0, run_cap=0.5, ts=OCT):
    clock = Clock(ts)
    return Ledger(tmp_path / "spend.jsonl", cap_usd=cap, run_cap_usd=run_cap, clock=clock), clock


def lines(ledger):
    return [json.loads(x) for x in ledger.path.read_text().splitlines() if x.strip()]


def test_settled_run_counts_actual_cost(tmp_path):
    ledger, _ = make(tmp_path)
    ledger.reserve("r1", "claude-haiku-5-5", 0.10)
    assert ledger.month_to_date() == pytest.approx(0.10)
    settled = ledger.settle("r1", 0.04)
    assert settled["month_to_date_usd"] == pytest.approx(0.04)
    assert ledger.month_to_date() == pytest.approx(0.04)


def test_unsettled_run_counts_its_reservation(tmp_path):
    ledger, _ = make(tmp_path)
    ledger.reserve("r1", "m", 0.2)
    ledger.reserve("r2", "m", 0.3)
    ledger.settle("r1", 0.1)
    assert ledger.month_to_date() == pytest.approx(0.4)


def test_reservation_past_cap_raises_and_writes_nothing(tmp_path):
    ledger, _ = make(tmp_path, cap=1.0)
    ledger.reserve("r1", "m", 0.4)
    ledger.reserve("r2", "m", 0.4)
    with pytest.raises(SpendCapExceeded):
        ledger.reserve("r3", "m", 0.3)
    assert len(lines(ledger)) == 2
    assert ledger.month_to_date() == pytest.approx(0.8)


def test_reservation_exactly_at_cap_is_allowed(tmp_path):
    ledger, _ = make(tmp_path, cap=1.0)
    ledger.reserve("r1", "m", 0.5)
    ledger.reserve("r2", "m", 0.5)
    assert ledger.month_to_date() == pytest.approx(1.0)


def test_run_cap(tmp_path):
    ledger, _ = make(tmp_path, run_cap=0.5)
    with pytest.raises(RunCapExceeded):
        ledger.reserve("r1", "m", 0.51)
    assert not ledger.path.exists() or lines(ledger) == []
    ledger.reserve("r2", "m", 0.5)


def test_negative_amounts_rejected(tmp_path):
    ledger, _ = make(tmp_path)
    with pytest.raises(LedgerError):
        ledger.reserve("r1", "m", -0.01)
    ledger.reserve("r2", "m", 0.1)
    with pytest.raises(LedgerError):
        ledger.settle("r2", -0.01)


def test_duplicate_reservation_rejected(tmp_path):
    ledger, _ = make(tmp_path)
    ledger.reserve("r1", "m", 0.1)
    with pytest.raises(LedgerError):
        ledger.reserve("r1", "m", 0.1)


def test_settle_needs_one_open_reservation(tmp_path):
    ledger, _ = make(tmp_path)
    with pytest.raises(LedgerError):
        ledger.settle("missing", 0.1)
    ledger.reserve("r1", "m", 0.1)
    ledger.settle("r1", 0.05)
    with pytest.raises(LedgerError):
        ledger.settle("r1", 0.05)


def test_cap_resets_on_first_of_month_utc(tmp_path):
    ledger, clock = make(tmp_path, cap=1.0, run_cap=1.0)
    ledger.reserve("oct", "m", 0.9)
    clock.ts = NOV
    assert ledger.month_to_date() == 0.0
    ledger.reserve("nov", "m", 0.9)
    assert ledger.month_to_date() == pytest.approx(0.9)


def test_late_settle_stays_in_reservation_month(tmp_path):
    ledger, clock = make(tmp_path, cap=1.0)
    ledger.reserve("r1", "m", 0.5)
    clock.ts = NOV
    settled = ledger.settle("r1", 0.2)
    assert settled["month"] == "2026-10"
    assert ledger.month_to_date() == 0.0
    clock.ts = OCT
    assert ledger.month_to_date() == pytest.approx(0.2)


def test_reserve_line_shape(tmp_path):
    ledger, _ = make(tmp_path, cap=150)
    line = ledger.reserve("dev-H009-A-sonnet-t1", "claude-sonnet-5-5", 0.10)
    for key in ("run_id", "model", "reserved_usd", "actual_usd", "month_to_date_usd", "cap_usd"):
        assert key in line
    assert line["kind"] == "reserve"
    assert line["cap_usd"] == 150


def test_concurrent_reservations_never_pass_cap(tmp_path):
    ledger, _ = make(tmp_path, cap=1.0, run_cap=0.5)
    outcomes = []

    def worker(i):
        try:
            Ledger(ledger.path, cap_usd=1.0, run_cap_usd=0.5, clock=ledger.clock).reserve(f"r{i}", "m", 0.1)
            outcomes.append("ok")
        except SpendCapExceeded:
            outcomes.append("capped")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("ok") == 10
    assert ledger.month_to_date() == pytest.approx(1.0)


PRICES = {
    "batch_discount": 0.5,
    "models": {
        "h": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125,
              "max_prompt_tokens": 100000, "over_max_prompt_fallback": "s"},
        "s": {"input": 2.0, "output": 10.0, "cache_read": 0.10, "cache_write": 2.5},
    },
}


def test_cost_input_and_output():
    assert cost_usd("h", {"input_tokens": 1_000_000, "output_tokens": 1_000_000}, PRICES) == pytest.approx(0.60)


def test_cost_counts_cache_reads_and_writes_under_both_spellings():
    a = cost_usd("s", {"cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000}, PRICES)
    b = cost_usd("s", {"cache_read_tokens": 1_000_000, "cache_write_tokens": 1_000_000}, PRICES)
    assert a == b == pytest.approx(2.60)


def test_batch_halves_input_and_output_only():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_tokens": 1_000_000}
    assert cost_usd("s", usage, PRICES, batch=True) == pytest.approx(6.0 + 0.10)


def test_long_prompt_uses_fallback_rates():
    usage = {"input_tokens": 1_000_000}
    assert cost_usd("h", usage, PRICES, prompt_tokens=100_000) == pytest.approx(0.10)
    assert cost_usd("h", usage, PRICES, prompt_tokens=100_001) == pytest.approx(2.0)


def test_unknown_model_raises():
    with pytest.raises(KeyError):
        cost_usd("nope", {}, PRICES)


def test_normalize_usage_defaults_to_zero():
    assert normalize_usage(None) == {
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
    }


def test_repo_prices_cover_the_three_models():
    prices = load_prices()
    for model in ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"):
        rates = prices["models"][model]
        for key in ("input", "output", "cache_read", "cache_write"):
            assert rates[key] > 0
    assert prices["models"]["claude-haiku-5-5"]["input"] == 0.10
    assert prices["models"]["claude-sonnet-5-5"]["output"] == 10.0
    assert prices["models"]["claude-opus-5-5"]["cache_read"] == 0.20
