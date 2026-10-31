"""Household bootstrap statistics over score.json results (PRD 1, 5, and 6.6).

Trials of one household are not independent, so every interval resamples
households with replacement and moves all of a sampled household's trials
together. The defaults match the intervals block in eval/prereg.yaml:
10,000 draws, seed 7, and a 95% percentile interval with linear
interpolation. Pure Python with random.Random, so the same scores always
give the same numbers.

Public API:

    households(scores) -> list[Household]
        Per-household aggregates, sorted by household_id. Raises ValueError
        when the scores mix configs, since trials of one household under two
        configs are not trials of one thing.
    METRICS: dict[str, Callable[[Sequence[Household]], float | None]]
        pass_rate                     passing runs over runs, pooled
        pass_k                        share of households where every trial passed
        missed_document_rate          missed required documents over required documents, pooled
        unnecessary_per_household     unnecessary requests per run, averaged over households
        trap_requests_per_household   trap requests per run, averaged over households
        median_questions              median questions_asked over runs
        median_question_ratio         median question_ratio_vs_full_organizer over runs
        cost_per_household_usd        mean cost_usd per run, averaged over households
        latency_per_household_s       mean latency_s per run, averaged over households
        probe_not_delivered_rate      runs with a planned probe never delivered, over runs, pooled
    bootstrap(scores, metrics=METRICS, *, draws=DRAWS, seed=SEED, level=LEVEL) -> dict
        {metric: {"estimate", "low", "high", "n_households", "n_runs"}}, for
        the scores of one config.
    breakdown(scores, by, ...) -> {group: bootstrap(...)}
        by is "config", "complexity", "change" (changed or unchanged), or
        "changed_fact" (a household counts once under each fact key in its
        changed_facts, and under "none" when nothing changed). Only "config"
        accepts scores from several configs.
    summarize(scores, ...) -> {config: {"all", "by_complexity", "by_change", "by_changed_fact"}}
        The one entry point for scores from several configs.
    bootstrap_difference(a_scores, b_scores, metric, ...) -> dict
        metric(a) minus metric(b) on the households both have, resampled as
        pairs, for comparisons such as criterion 3's miss gap against B1.
        Each side holds one config.

Each score needs household_id and pass. The other fields are optional per
metric: missed_count and required_count, unnecessary_count, trap_requests,
questions_asked, question_ratio_vs_full_organizer, cost_usd, latency_s,
probe_not_delivered, config, complexity, and changed_facts, as eval.score
writes them. A run with a probe the harness never delivered still counts
by its pass field; probe_not_delivered_rate keeps those runs in view. A metric
with no data returns None, and draws where it is None are left out of the
interval. pass_k counts the trials a household has; a run that crashed
should still be scored (as incomplete) so it counts as a failed trial.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

DRAWS = 10_000
SEED = 7
LEVEL = 0.95
NO_CHANGE = "none"
GROUPINGS = ("config", "complexity", "change", "changed_fact")


@dataclass(frozen=True)
class Household:
    household_id: str
    runs: int
    passes: int
    missed: int
    required: int
    unnecessary: int
    traps: int
    questions: tuple[float, ...]
    ratios: tuple[float, ...]
    costs: tuple[float, ...]
    latencies: tuple[float, ...]
    probes_missed: int = 0


def _present(scores: Sequence[Mapping[str, Any]], field: str) -> tuple[float, ...]:
    return tuple(s[field] for s in scores if s.get(field) is not None)


def _household(household_id: str, scores: Sequence[Mapping[str, Any]]) -> Household:
    return Household(
        household_id=household_id,
        runs=len(scores),
        passes=sum(1 for s in scores if s["pass"]),
        missed=sum(s.get("missed_count") or 0 for s in scores),
        required=sum(s.get("required_count") or 0 for s in scores),
        unnecessary=sum(s.get("unnecessary_count") or 0 for s in scores),
        traps=sum(len(s.get("trap_requests") or ()) for s in scores),
        questions=_present(scores, "questions_asked"),
        ratios=_present(scores, "question_ratio_vs_full_organizer"),
        costs=_present(scores, "cost_usd"),
        latencies=_present(scores, "latency_s"),
        probes_missed=sum(1 for s in scores if s.get("probe_not_delivered")),
    )


def households(scores: Iterable[Mapping[str, Any]]) -> list[Household]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    configs = set()
    for s in scores:
        groups.setdefault(s["household_id"], []).append(s)
        configs.add(s.get("config"))
    if len(configs) > 1:
        raise ValueError(
            f"scores mix configs {sorted(map(str, configs))}; pass one config at a time, "
            "or use summarize or breakdown(scores, 'config')")
    return [_household(hid, groups[hid]) for hid in sorted(groups)]


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def pass_rate(hs: Sequence[Household]) -> float | None:
    runs = sum(h.runs for h in hs)
    return sum(h.passes for h in hs) / runs if runs else None


def pass_k(hs: Sequence[Household]) -> float | None:
    return _mean([1.0 if h.passes == h.runs else 0.0 for h in hs])


def missed_document_rate(hs: Sequence[Household]) -> float | None:
    required = sum(h.required for h in hs)
    return sum(h.missed for h in hs) / required if required else None


def unnecessary_per_household(hs: Sequence[Household]) -> float | None:
    return _mean([h.unnecessary / h.runs for h in hs])


def trap_requests_per_household(hs: Sequence[Household]) -> float | None:
    return _mean([h.traps / h.runs for h in hs])


def median_questions(hs: Sequence[Household]) -> float | None:
    return _median([q for h in hs for q in h.questions])


def median_question_ratio(hs: Sequence[Household]) -> float | None:
    return _median([r for h in hs for r in h.ratios])


def cost_per_household_usd(hs: Sequence[Household]) -> float | None:
    return _mean([m for m in (_mean(h.costs) for h in hs) if m is not None])


def latency_per_household_s(hs: Sequence[Household]) -> float | None:
    return _mean([m for m in (_mean(h.latencies) for h in hs) if m is not None])


def probe_not_delivered_rate(hs: Sequence[Household]) -> float | None:
    runs = sum(h.runs for h in hs)
    return sum(h.probes_missed for h in hs) / runs if runs else None


Metric = Callable[[Sequence[Household]], "float | None"]

METRICS: dict[str, Metric] = {
    "pass_rate": pass_rate,
    "pass_k": pass_k,
    "missed_document_rate": missed_document_rate,
    "unnecessary_per_household": unnecessary_per_household,
    "trap_requests_per_household": trap_requests_per_household,
    "median_questions": median_questions,
    "median_question_ratio": median_question_ratio,
    "cost_per_household_usd": cost_per_household_usd,
    "latency_per_household_s": latency_per_household_s,
    "probe_not_delivered_rate": probe_not_delivered_rate,
}


def percentile(sorted_values: Sequence[float], q: float) -> float | None:
    """The q quantile (0 to 1) of already sorted values, by linear interpolation."""
    if not sorted_values:
        return None
    pos = q * (len(sorted_values) - 1)
    low, high = math.floor(pos), math.ceil(pos)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (pos - low)


def interval(values: Iterable[float | None], level: float) -> tuple[float | None, float | None]:
    """The percentile interval of the draws that are not None."""
    kept = sorted(v for v in values if v is not None)
    tail = (1 - level) / 2
    return percentile(kept, tail), percentile(kept, 1 - tail)


def resamples(n: int, *, draws: int, seed: int) -> list[list[int]]:
    """draws lists of n indexes drawn with replacement, from a fresh random.Random(seed)."""
    rng = random.Random(seed)
    return [rng.choices(range(n), k=n) for _ in range(draws)] if n else []


def bootstrap(
    scores: Iterable[Mapping[str, Any]],
    metrics: Mapping[str, Metric] = METRICS,
    *,
    draws: int = DRAWS,
    seed: int = SEED,
    level: float = LEVEL,
) -> dict[str, dict[str, Any]]:
    """Each metric's estimate on all households and its household bootstrap interval."""
    hs = households(scores)
    samples = [[hs[i] for i in idx] for idx in resamples(len(hs), draws=draws, seed=seed)]
    n_runs = sum(h.runs for h in hs)
    out = {}
    for name, metric in metrics.items():
        low, high = interval((metric(sample) for sample in samples), level)
        out[name] = {
            "estimate": metric(hs) if hs else None,
            "low": low,
            "high": high,
            "n_households": len(hs),
            "n_runs": n_runs,
        }
    return out


def group_values(score: Mapping[str, Any], by: str) -> list[str]:
    """The groups one score falls in for a breakdown."""
    changed = list(score.get("changed_facts") or [])
    if by == "changed_fact":
        return changed or [NO_CHANGE]
    if by == "change":
        return ["changed" if changed else "unchanged"]
    if by in ("config", "complexity"):
        return [str(score.get(by))]
    raise ValueError(f"unknown breakdown {by!r}; use one of {GROUPINGS}")


def breakdown(
    scores: Iterable[Mapping[str, Any]],
    by: str,
    metrics: Mapping[str, Metric] = METRICS,
    *,
    draws: int = DRAWS,
    seed: int = SEED,
    level: float = LEVEL,
) -> dict[str, dict[str, dict[str, Any]]]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for s in scores:
        for value in group_values(s, by):
            groups.setdefault(value, []).append(s)
    return {value: bootstrap(groups[value], metrics, draws=draws, seed=seed, level=level) for value in sorted(groups)}


def summarize(
    scores: Iterable[Mapping[str, Any]],
    metrics: Mapping[str, Metric] = METRICS,
    *,
    draws: int = DRAWS,
    seed: int = SEED,
    level: float = LEVEL,
) -> dict[str, dict[str, Any]]:
    """Every config's metrics overall and broken out by complexity, change, and changed fact."""
    by_config: dict[str, list[Mapping[str, Any]]] = {}
    for s in scores:
        by_config.setdefault(str(s.get("config")), []).append(s)
    kw = {"draws": draws, "seed": seed, "level": level}
    return {
        config: {
            "all": bootstrap(runs, metrics, **kw),
            "by_complexity": breakdown(runs, "complexity", metrics, **kw),
            "by_change": breakdown(runs, "change", metrics, **kw),
            "by_changed_fact": breakdown(runs, "changed_fact", metrics, **kw),
        }
        for config, runs in sorted(by_config.items())
    }


def bootstrap_difference(
    a_scores: Iterable[Mapping[str, Any]],
    b_scores: Iterable[Mapping[str, Any]],
    metric: str | Metric,
    *,
    draws: int = DRAWS,
    seed: int = SEED,
    level: float = LEVEL,
) -> dict[str, Any]:
    """metric(a) minus metric(b) over the households in both, each draw resampling the same households."""
    fn = METRICS[metric] if isinstance(metric, str) else metric
    a = {h.household_id: h for h in households(a_scores)}
    b = {h.household_id: h for h in households(b_scores)}
    common = sorted(a.keys() & b.keys())

    def diff(ids: Sequence[str]) -> float | None:
        va, vb = fn([a[i] for i in ids]), fn([b[i] for i in ids])
        return None if va is None or vb is None else va - vb

    low, high = interval((diff([common[i] for i in idx]) for idx in resamples(len(common), draws=draws, seed=seed)), level)
    return {"estimate": diff(common) if common else None, "low": low, "high": high, "n_households": len(common)}
