"""Spend ledger: every model call reserves its estimated cost before it runs.

The ledger is an append-only JSON Lines file. A run writes a "reserve" line
before its first model call and a "settle" line after its last. Month to date
spend counts each run once: its actual cost if settled, otherwise its
reservation. Caps reset on the first of each calendar month in UTC, like
Console workspace spend limits.
"""

from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import yaml

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = REPO / "ledger" / "spend.jsonl"
DEFAULT_PRICES = REPO / "ledger" / "prices.yaml"

MILLION = 1_000_000


class SpendCapExceeded(RuntimeError):
    """The reservation would push this month's spend past the cap."""


class RunCapExceeded(RuntimeError):
    """The reservation is larger than one run is allowed to spend."""


class LedgerError(RuntimeError):
    """A reserve or settle call that doesn't match the ledger's history."""


def load_prices(path: Path = DEFAULT_PRICES) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def normalize_usage(usage: dict[str, Any] | None) -> dict[str, int]:
    """Accepts Messages API names and the PRD's run summary names."""
    usage = usage or {}

    def pick(*names: str) -> int:
        for name in names:
            value = usage.get(name)
            if value is not None:
                return int(value)
        return 0

    return {
        "input_tokens": pick("input_tokens"),
        "output_tokens": pick("output_tokens"),
        "cache_read_tokens": pick("cache_read_tokens", "cache_read_input_tokens"),
        "cache_write_tokens": pick("cache_write_tokens", "cache_creation_input_tokens"),
    }


def cost_usd(
    model: str,
    usage: dict[str, Any] | None,
    prices: dict[str, Any],
    *,
    batch: bool = False,
    prompt_tokens: int | None = None,
) -> float:
    """List-price cost of one call, or of summed usage across calls.

    prompt_tokens is the largest single request's prompt size, counting cache
    reads and writes. When it passes a model's max_prompt_tokens, the call is
    charged at the fallback model's rates.
    """
    table = prices["models"]
    if model not in table:
        raise KeyError(f"no list price for model {model!r}")
    rates = table[model]
    limit = rates.get("max_prompt_tokens")
    if limit is not None and prompt_tokens is not None and prompt_tokens > limit:
        rates = table[rates["over_max_prompt_fallback"]]
    u = normalize_usage(usage)
    io_factor = prices.get("batch_discount", 1.0) if batch else 1.0
    total = (
        u["input_tokens"] * rates["input"] * io_factor
        + u["output_tokens"] * rates["output"] * io_factor
        + u["cache_read_tokens"] * rates["cache_read"]
        + u["cache_write_tokens"] * rates["cache_write"]
    ) / MILLION
    return round(total, 6)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _month_key(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m")


class Ledger:
    def __init__(
        self,
        path: Path = DEFAULT_LEDGER,
        *,
        cap_usd: float,
        run_cap_usd: float,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.path = Path(path)
        self.cap_usd = float(cap_usd)
        self.run_cap_usd = float(run_cap_usd)
        self.clock = clock

    @contextmanager
    def _locked(self) -> Iterator[Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                yield f
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    @staticmethod
    def _parse(f: Any) -> list[dict[str, Any]]:
        return [json.loads(line) for line in f.read().splitlines() if line.strip()]

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._locked() as f:
            return self._parse(f)

    @staticmethod
    def _runs(entries: list[dict[str, Any]], month: str) -> dict[str, dict[str, Any]]:
        runs: dict[str, dict[str, Any]] = {}
        for e in entries:
            if e.get("month") != month:
                continue
            run = runs.setdefault(e["run_id"], {"reserved": 0.0, "actual": None})
            if e["kind"] == "reserve":
                run["reserved"] = float(e["reserved_usd"])
            elif e["kind"] == "settle":
                run["actual"] = float(e["actual_usd"])
        return runs

    @classmethod
    def _month_to_date(cls, entries: list[dict[str, Any]], month: str) -> float:
        total = 0.0
        for run in cls._runs(entries, month).values():
            total += run["actual"] if run["actual"] is not None else run["reserved"]
        return round(total, 6)

    def month_to_date(self) -> float:
        return self._month_to_date(self.entries(), _month_key(self.clock()))

    def reserve(self, run_id: str, model: str, estimate_usd: float) -> dict[str, Any]:
        """Records a reservation, or raises before anything is written."""
        estimate = float(estimate_usd)
        if estimate < 0:
            raise LedgerError("an estimate can't be negative")
        if estimate > self.run_cap_usd:
            raise RunCapExceeded(
                f"{run_id}: estimate ${estimate:.4f} is over the run cap ${self.run_cap_usd:.2f}"
            )
        now = self.clock()
        month = _month_key(now)
        with self._locked() as f:
            entries = self._parse(f)
            if run_id in self._runs(entries, month):
                raise LedgerError(f"{run_id} already has a reservation this month")
            mtd = self._month_to_date(entries, month)
            if mtd + estimate > self.cap_usd:
                raise SpendCapExceeded(
                    f"{run_id}: ${mtd:.4f} spent this month plus ${estimate:.4f} "
                    f"would pass the ${self.cap_usd:.2f} cap"
                )
            line = {
                "ts": now.isoformat(),
                "month": month,
                "kind": "reserve",
                "run_id": run_id,
                "model": model,
                "reserved_usd": round(estimate, 6),
                "actual_usd": None,
                "month_to_date_usd": round(mtd + estimate, 6),
                "cap_usd": self.cap_usd,
            }
            f.write(json.dumps(line) + "\n")
            f.flush()
        return line

    def settle(self, run_id: str, actual_usd: float) -> dict[str, Any]:
        actual = float(actual_usd)
        if actual < 0:
            raise LedgerError("a cost can't be negative")
        now = self.clock()
        with self._locked() as f:
            entries = self._parse(f)
            reserve = next(
                (e for e in reversed(entries) if e["run_id"] == run_id and e["kind"] == "reserve"),
                None,
            )
            if reserve is None:
                raise LedgerError(f"{run_id} has no reservation to settle")
            month = reserve["month"]
            if self._runs(entries, month)[run_id]["actual"] is not None:
                raise LedgerError(f"{run_id} is already settled")
            line = {
                "ts": now.isoformat(),
                "month": month,
                "kind": "settle",
                "run_id": run_id,
                "model": reserve["model"],
                "reserved_usd": reserve["reserved_usd"],
                "actual_usd": round(actual, 6),
                "month_to_date_usd": None,
                "cap_usd": self.cap_usd,
            }
            line["month_to_date_usd"] = self._month_to_date(entries + [line], month)
            f.write(json.dumps(line) + "\n")
            f.flush()
        return line
