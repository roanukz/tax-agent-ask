"""Household bootstrap statistics: aggregates, metrics, intervals, and breakdowns."""

from pathlib import Path

import pytest
import yaml

from eval import stats
from eval.stats import (
    DRAWS,
    LEVEL,
    METRICS,
    SEED,
    Household,
    bootstrap,
    bootstrap_difference,
    breakdown,
    group_values,
    households,
    interval,
    percentile,
    resamples,
    summarize,
)

PREREG = Path(__file__).resolve().parent.parent / "eval" / "prereg.yaml"


def score(hid, passed, config="A-haiku", **fields):
    s = {
        "household_id": hid,
        "config": config,
        "pass": passed,
        "missed_count": 0,
        "required_count": 4,
        "unnecessary_count": 0,
        "trap_requests": [],
        "questions_asked": 10,
        "question_ratio_vs_full_organizer": 0.25,
        "cost_usd": 0.02,
        "latency_s": 20.0,
        "complexity": "simple",
        "changed_facts": [],
    }
    s.update(fields)
    return s


def household(**fields):
    base = dict(household_id="H1", runs=1, passes=1, missed=0, required=1, unnecessary=0, traps=0,
                questions=(), ratios=(), costs=(), latencies=())
    base.update(fields)
    return Household(**base)


SCORES = [
    score("H001", True, questions_asked=8, cost_usd=0.01, latency_s=10.0),
    score("H001", True, questions_asked=12, cost_usd=0.03, latency_s=30.0),
    score("H001", False, missed_count=1, unnecessary_count=2, trap_requests=[{"request": "form 5695"}]),
    score("H002", True, complexity="complex", changed_facts=["w2_employer_count", "self_employment"]),
    score("H002", True, complexity="complex", changed_facts=["w2_employer_count", "self_employment"]),
    score("H002", True, complexity="complex", changed_facts=["w2_employer_count", "self_employment"]),
]


def test_defaults_match_prereg():
    intervals = yaml.safe_load(PREREG.read_text())["intervals"]
    assert intervals == {"method": "household_bootstrap", "draws": DRAWS, "seed": SEED, "level": LEVEL}


def test_households_aggregate_trials():
    hs = households(SCORES)
    assert [h.household_id for h in hs] == ["H001", "H002"]
    h1 = hs[0]
    assert (h1.runs, h1.passes, h1.missed, h1.required, h1.unnecessary, h1.traps) == (3, 2, 1, 12, 2, 1)
    assert h1.questions == (8, 12, 10)
    assert h1.costs == (0.01, 0.03, 0.02)
    assert h1.latencies == (10.0, 30.0, 20.0)
    assert h1.ratios == (0.25, 0.25, 0.25)


def test_households_skip_missing_optional_fields():
    hs = households([{"household_id": "H9", "pass": True, "question_ratio_vs_full_organizer": None}])
    h = hs[0]
    assert (h.runs, h.passes, h.missed, h.required, h.unnecessary, h.traps, h.probes_missed) == (1, 1, 0, 0, 0, 0, 0)
    assert h.questions == h.ratios == h.costs == h.latencies == ()


def test_households_count_runs_with_a_probe_not_delivered():
    scores = [score("H1", True, probe_not_delivered=["advice", "send"]), score("H1", True, probe_not_delivered=[]),
              score("H1", False, probe_not_delivered=["send"]), score("H1", True)]
    assert households(scores)[0].probes_missed == 2


def test_probe_not_delivered_rate_pools_runs():
    hs = [household(runs=3, probes_missed=1), household(household_id="H2", runs=1, probes_missed=1)]
    assert stats.probe_not_delivered_rate(hs) == 0.5
    assert METRICS["probe_not_delivered_rate"] is stats.probe_not_delivered_rate


def test_undelivered_probe_runs_show_next_to_the_pass_rate():
    scores = [score("H1", True, probe_not_delivered=["advice"]), score("H2", True)]
    result = bootstrap(scores, draws=50)
    assert result["pass_rate"]["estimate"] == 1.0
    assert result["probe_not_delivered_rate"]["estimate"] == 0.5


def test_households_reject_mixed_configs():
    scores = [score("H1", True, config="A-haiku"), score("H1", False, config="B1")]
    with pytest.raises(ValueError, match=r"^scores mix configs \['A-haiku', 'B1'\]; pass one config at a time, "
                                         r"or use summarize or breakdown\(scores, 'config'\)$"):
        households(scores)


def test_households_reject_a_missing_config_next_to_a_named_one():
    with pytest.raises(ValueError, match="None"):
        households([{"household_id": "H1", "pass": True}, score("H2", True)])


def test_households_accept_one_config_or_none():
    assert len(households([score("H1", True), score("H2", True)])) == 2
    assert len(households([{"household_id": "H1", "pass": True}, {"household_id": "H2", "pass": False}])) == 2


def test_mixed_configs_fail_everywhere_but_the_config_split():
    scores = [score("H1", True, config="A"), score("H1", False, config="B1")]
    with pytest.raises(ValueError):
        bootstrap(scores, draws=10)
    with pytest.raises(ValueError):
        breakdown(scores, "complexity", draws=10)
    with pytest.raises(ValueError):
        bootstrap_difference(scores, scores, "pass_rate", draws=10)
    by_config = breakdown(scores, "config", draws=10)
    assert by_config["A"]["pass_k"]["estimate"] == 1.0
    assert by_config["B1"]["pass_k"]["estimate"] == 0.0
    assert summarize(scores, draws=10)["A"]["by_complexity"]["simple"]["pass_k"]["estimate"] == 1.0


def test_households_sorted_by_id():
    assert [h.household_id for h in households([score("H3", True), score("H1", True), score("H2", True)])] == ["H1", "H2", "H3"]


def test_metrics_on_known_households():
    hs = households(SCORES)
    assert METRICS["pass_rate"](hs) == 5 / 6
    assert METRICS["pass_k"](hs) == 0.5
    assert METRICS["missed_document_rate"](hs) == 1 / 24
    assert METRICS["unnecessary_per_household"](hs) == (2 / 3 + 0) / 2
    assert METRICS["trap_requests_per_household"](hs) == (1 / 3 + 0) / 2
    assert METRICS["median_questions"](hs) == 10
    assert METRICS["median_question_ratio"](hs) == 0.25
    assert METRICS["cost_per_household_usd"](hs) == pytest.approx((0.02 + 0.02) / 2)
    assert METRICS["latency_per_household_s"](hs) == pytest.approx(20.0)


def test_metric_names():
    assert list(METRICS) == [
        "pass_rate", "pass_k", "missed_document_rate", "unnecessary_per_household",
        "trap_requests_per_household", "median_questions", "median_question_ratio",
        "cost_per_household_usd", "latency_per_household_s", "probe_not_delivered_rate"]


def test_metrics_without_data_are_none():
    empty = household(runs=1, required=0, questions=(), ratios=(), costs=(), latencies=())
    assert stats.missed_document_rate([empty]) is None
    assert stats.median_questions([empty]) is None
    assert stats.median_question_ratio([empty]) is None
    assert stats.cost_per_household_usd([empty]) is None
    assert stats.latency_per_household_s([empty]) is None
    for name, metric in METRICS.items():
        assert metric([]) is None, name


def test_cost_averages_per_household_first():
    hs = [household(household_id="A", costs=(0.10, 0.30)), household(household_id="B", costs=(0.40,)),
          household(household_id="C", costs=())]
    assert stats.cost_per_household_usd(hs) == pytest.approx(0.30)


def test_median_pools_runs():
    hs = [household(questions=(1, 2, 3)), household(questions=(100,))]
    assert stats.median_questions(hs) == 2.5


def test_percentile():
    values = [1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 0) == 1.0
    assert percentile(values, 1) == 4.0
    assert percentile(values, 0.5) == 2.5
    assert percentile(values, 0.25) == pytest.approx(1.75)
    assert percentile([7.0], 0.975) == 7.0
    assert percentile([], 0.5) is None


def test_interval_drops_none_and_sorts():
    assert interval([3.0, None, 1.0, 2.0], 0.5) == (1.5, 2.5)
    assert interval([None, None], 0.95) == (None, None)


def test_resamples_are_deterministic_and_seeded():
    a = resamples(5, draws=20, seed=7)
    assert a == resamples(5, draws=20, seed=7)
    assert a != resamples(5, draws=20, seed=8)
    assert len(a) == 20 and all(len(idx) == 5 and set(idx) <= set(range(5)) for idx in a)
    assert resamples(0, draws=20, seed=7) == []


def test_bootstrap_shape_and_determinism():
    a = bootstrap(SCORES, draws=300)
    assert a == bootstrap(SCORES, draws=300)
    assert set(a) == set(METRICS)
    pr = a["pass_rate"]
    assert set(pr) == {"estimate", "low", "high", "n_households", "n_runs"}
    assert pr["estimate"] == 5 / 6
    assert (pr["n_households"], pr["n_runs"]) == (2, 6)
    assert 2 / 3 <= pr["low"] <= pr["estimate"] <= pr["high"] <= 1.0


def test_bootstrap_moves_a_households_trials_together():
    # One household passes all three trials and one fails all three, so every
    # draw's pass rate is 0, 0.5, or 1, never a third.
    scores = [score("H1", True)] * 3 + [score("H2", False)] * 3
    seen = []

    def spy(hs):
        seen.append(stats.pass_rate(hs))
        return seen[-1]

    bootstrap(scores, {"pass_rate": spy}, draws=200)
    assert set(seen) <= {0.0, 0.5, 1.0}
    assert {0.0, 1.0} <= set(seen)


def test_bootstrap_constant_metric_has_a_point_interval():
    result = bootstrap([score("H1", True), score("H2", True)], draws=100)
    assert result["pass_rate"] == {"estimate": 1.0, "low": 1.0, "high": 1.0, "n_households": 2, "n_runs": 2}


def test_bootstrap_with_default_draws_on_a_small_set():
    small = SCORES[:3]
    result = bootstrap(small, {"pass_rate": stats.pass_rate})
    assert result == bootstrap(small, {"pass_rate": stats.pass_rate})
    assert result["pass_rate"]["estimate"] == pytest.approx(2 / 3)
    assert result["pass_rate"]["low"] == result["pass_rate"]["high"] == pytest.approx(2 / 3)


def test_bootstrap_level_widens_the_interval():
    scores = [score(f"H{i}", i % 3 != 0) for i in range(30)]
    narrow = bootstrap(scores, {"pass_rate": stats.pass_rate}, draws=500, level=0.5)["pass_rate"]
    wide = bootstrap(scores, {"pass_rate": stats.pass_rate}, draws=500, level=0.99)["pass_rate"]
    assert wide["low"] <= narrow["low"] <= narrow["high"] <= wide["high"]
    assert wide["high"] - wide["low"] > narrow["high"] - narrow["low"]


def test_bootstrap_empty():
    result = bootstrap([], draws=10)
    assert result["pass_rate"] == {"estimate": None, "low": None, "high": None, "n_households": 0, "n_runs": 0}


def test_group_values():
    s = score("H1", True, complexity="complex", changed_facts=["a", "b"])
    assert group_values(s, "config") == ["A-haiku"]
    assert group_values(s, "complexity") == ["complex"]
    assert group_values(s, "change") == ["changed"]
    assert group_values(s, "changed_fact") == ["a", "b"]
    unchanged = score("H2", True)
    assert group_values(unchanged, "change") == ["unchanged"]
    assert group_values(unchanged, "changed_fact") == ["none"]
    assert group_values({"household_id": "H3", "pass": True}, "complexity") == ["None"]
    with pytest.raises(ValueError, match="unknown breakdown"):
        group_values(s, "color")


def test_breakdown_by_changed_fact_counts_a_household_under_each_fact():
    result = breakdown(SCORES, "changed_fact", draws=50)
    assert list(result) == ["none", "self_employment", "w2_employer_count"]
    assert result["w2_employer_count"]["pass_rate"]["n_households"] == 1
    assert result["none"]["pass_rate"]["estimate"] == 2 / 3


def test_breakdown_by_complexity():
    result = breakdown(SCORES, "complexity", draws=50)
    assert list(result) == ["complex", "simple"]
    assert result["complex"]["pass_rate"]["estimate"] == 1.0


def test_summarize_by_config():
    scores = SCORES + [score("H001", False, config="B1"), score("H002", False, config="B1")]
    result = summarize(scores, {"pass_rate": stats.pass_rate}, draws=50)
    assert list(result) == ["A-haiku", "B1"]
    assert set(result["A-haiku"]) == {"all", "by_complexity", "by_change", "by_changed_fact"}
    assert result["B1"]["all"]["pass_rate"]["estimate"] == 0.0
    assert result["A-haiku"]["by_change"]["changed"]["pass_rate"]["estimate"] == 1.0
    assert result["A-haiku"]["by_change"]["unchanged"]["pass_rate"]["estimate"] == 2 / 3


def test_bootstrap_difference_pairs_households():
    a = [score("H1", True), score("H2", True), score("H3", True)]
    b = [score("H1", False), score("H2", False), score("H9", True)]
    result = bootstrap_difference(a, b, "pass_rate", draws=100)
    assert result == {"estimate": 1.0, "low": 1.0, "high": 1.0, "n_households": 2}


def test_bootstrap_difference_same_scores_is_zero():
    result = bootstrap_difference(SCORES, SCORES, stats.missed_document_rate, draws=100)
    assert result == {"estimate": 0.0, "low": 0.0, "high": 0.0, "n_households": 2}


def test_bootstrap_difference_none_when_a_side_has_no_data():
    a = [score("H1", True, required_count=0)]
    result = bootstrap_difference(a, a, "missed_document_rate", draws=20)
    assert result == {"estimate": None, "low": None, "high": None, "n_households": 1}


def test_bootstrap_difference_without_common_households():
    result = bootstrap_difference([score("H1", True)], [score("H2", True)], "pass_rate", draws=20)
    assert result == {"estimate": None, "low": None, "high": None, "n_households": 0}
