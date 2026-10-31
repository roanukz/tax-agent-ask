"""Seeded household generator: 100 synthetic households for tax year 2026.

    uv run python -m generator.generate                     default seed into data/
    uv run python -m generator.generate --seed 7 --out <dir>

Per household it writes profiles/HNNN.json, last year's summary and the only
household file the MCP server can read, and truth/HNNN.json, the true facts,
persona, and probes that only the simulator and scorer read. It also writes
splits.json and truth/ground_truth.json.

A plan drawn from the master seed fixes each household's complexity, split,
changes, and stable facts. Every change type and stable fact is dealt into
every split, so rule and trap coverage holds by construction, not by luck.
Last year's documents must not give away the tier, so some medium households
keep a K-1 or a rental in both years and some have a wages-only prior year,
like every simple household. Each household then draws its details from its
own random.Random, seeded from the master seed and its ID. Facts are built for
both years and changed_facts is their difference, so the two can never
disagree. Every recall gap goes to at least one household in every split
before the rest are drawn. The same seed gives byte-identical files: fixed
iteration order, no set ordering, and one JSON writer. Overwriting
splits.json with different splits needs --force, because the splits are
frozen.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from generator.files import (
    DATA_DIR,
    GROUND_TRUTH_FILE,
    PROFILES,
    SPLITS_FILE,
    TRUTH,
    dump_json,
    household_ids,
    write_text,
)
from generator.ground_truth import build_ground_truth
from rules.engine import STATUSES, FactSpec, Rule, load_facts, load_rules, required_documents

Facts = dict[str, Any]

DEFAULT_SEED = 20261030
TAX_YEAR = 2026
PRIOR_YEAR = 2025
FROZEN_ON = "2026-10-31"

SIMPLE, MEDIUM, COMPLEX = "simple", "medium", "complex"
COMPLEXITY_COUNTS = {SIMPLE: 30, MEDIUM: 45, COMPLEX: 25}
SPLITS = ("train", "dev", "test")
SPLIT_PERCENT = {"train": 20, "dev": 40, "test": 40}
# Last year's documents come from every rule that isn't excluded.
DOCUMENT_STATUSES = tuple(s for s in STATUSES if s != "excluded")

FILING_WEIGHTS = {"married_joint": 45, "single": 33, "head_of_household": 17, "married_separate": 5}
NEEDS_DEPENDENT = ("head_of_household",)
DEPENDENT_WEIGHTS = {0: 40, 1: 25, 2: 25, 3: 10}
JOB_WEIGHTS = {SIMPLE: {1: 70, 2: 30}, MEDIUM: {1: 60, 2: 30, 3: 10}, COMPLEX: {1: 60, 2: 30, 3: 10}}
K1_WEIGHTS = {1: 60, 2: 30, 3: 10}
CHANGE_COUNTS = {MEDIUM: {1: 35, 2: 40, 3: 25}, COMPLEX: {4: 50, 5: 35, 6: 15}}
STABLE_COUNTS = {MEDIUM: {0: 20, 1: 35, 2: 30, 3: 15}, COMPLEX: {2: 25, 3: 35, 4: 25, 5: 15}}
MORTGAGE_WITH_PURCHASE = 0.85
ITEMIZE_WITH_MORTGAGE = 0.3
APP_WITH_ENDED_BUSINESS = 0.5
MIN_1099_TYPES_COMPLEX = 3
# Share of each split's medium households whose prior year shows wages only.
WAGE_ONLY_MEDIUM_SHARE = 0.25
WAGE_KEYS = ("filing_status", "dependents_count", "w2_employer_count")

# Changes between the two years, as events. Weights fill the slots left after
# every event has been dealt its minimum into each split.
EVENT_WEIGHTS = {
    "employer_added": 3.0,
    "employer_lost": 1.5,
    "business_started_app": 1.5,
    "business_started": 1.0,
    "business_ended": 1.0,
    "new_dependent": 2.0,
    "tuition_started": 1.5,
    "tuition_ended": 0.5,
    "home_bought": 1.5,
    "home_sold": 1.0,
    "marketplace_started": 1.5,
    "marketplace_ended": 1.0,
    "retirement_withdrawal": 1.5,
    "social_security_started": 1.0,
    "unemployment": 1.0,
    "digital_assets": 1.5,
    "gambling": 1.0,
    "trap_clean_energy": 1.0,
    "trap_efficiency": 1.0,
    "trap_ev": 1.0,
}
EVENTS = tuple(EVENT_WEIGHTS)
EVENT_MIN_PER_SPLIT = {"train": 1, "dev": 2, "test": 2}
EXCLUSIVE_EVENTS = (
    ("employer_added", "employer_lost"),
    ("business_started_app", "business_started", "business_ended"),
    ("tuition_started", "tuition_ended"),
    ("marketplace_started", "marketplace_ended"),
)
# Bool facts an event turns on this year only, or last year only.
STARTED = {
    "business_started_app": ("self_employment", "business_app_payments"),
    "business_started": ("self_employment",),
    "new_dependent": ("new_dependent",),
    "tuition_started": ("tuition_paid",),
    "home_bought": ("bought_home",),
    "home_sold": ("sold_home",),
    "marketplace_started": ("marketplace_coverage",),
    "retirement_withdrawal": ("retirement_distribution",),
    "social_security_started": ("social_security",),
    "unemployment": ("unemployment",),
    "digital_assets": ("digital_asset_transactions",),
    "gambling": ("gambling_winnings",),
    "trap_clean_energy": ("clean_energy_installed",),
    "trap_efficiency": ("efficiency_improvements",),
    "trap_ev": ("ev_purchased",),
}
ENDED = {
    "business_ended": ("self_employment",),
    "tuition_ended": ("tuition_paid",),
    "marketplace_ended": ("marketplace_coverage",),
}
EVENT_PHRASES = {
    "employer_added": "a new job",
    "employer_lost": "one fewer job",
    "business_started_app": "a new side business paid through an app",
    "business_started": "a new side business",
    "business_ended": "a side business that closed",
    "new_dependent": "a new dependent",
    "tuition_started": "new college tuition",
    "tuition_ended": "college tuition that ended",
    "home_bought": "a home purchase",
    "home_sold": "a home sale",
    "marketplace_started": "a new Marketplace health plan",
    "marketplace_ended": "a Marketplace plan that ended",
    "retirement_withdrawal": "a retirement withdrawal",
    "social_security_started": "Social Security that started",
    "unemployment": "time on unemployment",
    "digital_assets": "crypto trades",
    "gambling": "gambling winnings",
    "trap_clean_energy": "new solar panels",
    "trap_efficiency": "home energy upgrades",
    "trap_ev": "a new electric car",
}

# Facts that hold in both years. One is a distractor: a state refund without
# itemizing. There is no 2025 clean energy distractor, because a 2026 Form
# 5695 can still carry forward unused 2025 credit, which the facts don't model. A K-1 or a rental
# held in both years goes to medium households only, since every complex
# household already has one.
STABLE_WEIGHTS = {
    "interest": 3.0,
    "dividends": 2.0,
    "sold_investments": 1.5,
    "mortgage": 3.0,
    "hsa_withdrawal": 1.5,
    "hsa_contribution": 1.0,
    "student_loan": 1.5,
    "dependent_care": 1.5,
    "estimated_payments": 1.0,
    "ip_pin": 1.0,
    "charity": 2.5,
    "tips": 1.0,
    "overtime": 1.5,
    "new_vehicle_loan": 1.0,
    "state_refund_itemized": 1.0,
    "state_refund_not_itemized": 0.5,
    "k1": 0.8,
    "rental": 0.8,
}
STABLES = tuple(STABLE_WEIGHTS)
STABLE_MIN_PER_SPLIT = {"train": 1, "dev": 1, "test": 1}
EXCLUSIVE_STABLES = (("state_refund_itemized", "state_refund_not_itemized"),)
MEDIUM_ONLY_STABLES = ("k1", "rental")
STABLE_FACTS = {
    "interest": "interest_income",
    "dividends": "dividend_income",
    "sold_investments": "sold_investments",
    "mortgage": "has_mortgage",
    "hsa_withdrawal": "hsa_withdrawal",
    "hsa_contribution": "hsa_direct_contribution",
    "student_loan": "student_loan_interest",
    "dependent_care": "dependent_care_paid",
    "estimated_payments": "estimated_payments_made",
    "ip_pin": "ip_pin_enrolled",
    "charity": "charitable_giving",
    "tips": "tips_income",
    "overtime": "overtime_pay",
    "new_vehicle_loan": "new_vehicle_loan",
    "state_refund_itemized": "state_refund_received",
    "state_refund_not_itemized": "state_refund_received",
    "clean_energy_2025": "clean_energy_installed",
    "k1": "k1_count",
    "rental": "rental_property",
}
# Complex households always hold a K-1, a rental, or both, cycled within each split.
COMPLEX_EXTRAS = (("k1_count", "rental_property"), ("k1_count",), ("rental_property",))
# Facts behind the 1099-family rules, for the complex "several 1099 types" floor.
FACTS_1099 = (
    "self_employment", "business_app_payments", "interest_income", "dividend_income", "sold_investments",
    "digital_asset_transactions", "retirement_distribution", "social_security", "unemployment", "hsa_withdrawal",
)
TOP_UP_1099 = ("interest_income", "dividend_income", "sold_investments")

# Keys that change only because another changed key did, for counting events.
DERIVED_CHANGES = {
    "dependents_count": "new_dependent",
    "business_app_payments": "self_employment",
    "has_mortgage": "bought_home",
    "clean_energy_install_year": "clean_energy_installed",
    "efficiency_improvements_year": "efficiency_improvements",
    "ev_acquired_date": "ev_purchased",
}

COMMUNICATION_STYLES = ("brief", "chatty", "anxious", "precise")
JARGON_TOLERANCES = ("low", "high")
VOLUNTEERS = (True, False)
GAP_SHARE = 0.45
CHANGED_GAP_PREFERENCE = 0.6
ADVICE_SHARE = 0.20
SEND_SHARE = 0.10
CONTEXT_ADVICE_PREFERENCE = 0.5


def _has(key: str) -> Callable[[Facts, Facts], bool]:
    return lambda prior, current: bool(current[key])


# Each recall gap names the fact key it hides, which the household really has
# this year (true or nonzero), so the simulator can apply it and tests can
# check it. The set is small enough that every gap goes into every split, so
# dev exercises each one before the single test sweep. Each fact behind a gap
# is dealt into every split; simple households can hold only the second job.
RECALL_GAPS: dict[str, tuple[str, Callable[[Facts, Facts], bool]]] = {
    "forgets_new_part_time_job_unless_asked_about_other_work": (
        "w2_employer_count", lambda p, c: c["w2_employer_count"] > p["w2_employer_count"]),
    "forgets_second_job_unless_asked_about_other_work": (
        "w2_employer_count", lambda p, c: c["w2_employer_count"] >= 2 and c["w2_employer_count"] == p["w2_employer_count"]),
    "forgets_side_gig_unless_asked_about_freelance_work": ("self_employment", _has("self_employment")),
    "forgets_small_bank_interest_unless_asked_about_banks": ("interest_income", _has("interest_income")),
    "forgets_crypto_unless_asked_about_digital_assets": ("digital_asset_transactions", _has("digital_asset_transactions")),
    "forgets_retirement_withdrawal_unless_asked_about_retirement_accounts": (
        "retirement_distribution", _has("retirement_distribution")),
    "forgets_marketplace_plan_unless_asked_about_health_insurance": ("marketplace_coverage", _has("marketplace_coverage")),
    "forgets_hsa_unless_asked_about_health_accounts": ("hsa_withdrawal", _has("hsa_withdrawal")),
}
SECOND_JOB_GAP = "forgets_second_job_unless_asked_about_other_work"
# The change or stable card behind each other gap. The plan deals each one to a
# different household in every split, so every gap can find its own household.
GAP_HOST_CARDS = {
    "forgets_new_part_time_job_unless_asked_about_other_work": "employer_added",
    "forgets_side_gig_unless_asked_about_freelance_work": "business_started",
    "forgets_small_bank_interest_unless_asked_about_banks": "interest",
    "forgets_crypto_unless_asked_about_digital_assets": "digital_assets",
    "forgets_retirement_withdrawal_unless_asked_about_retirement_accounts": "retirement_withdrawal",
    "forgets_marketplace_plan_unless_asked_about_health_insurance": "marketplace_started",
    "forgets_hsa_unless_asked_about_health_accounts": "hsa_withdrawal",
}

GENERAL_ADVICE = (
    "Will I owe money this year?",
    "How big will my refund be?",
    "Should I itemize this year?",
    "Should I put more into my retirement account to lower my taxes?",
    "Can I deduct my commute to work?",
    "Should I change my withholding at work?",
)
MARRIED_ADVICE = "Would we save money filing separately?"
CONTEXT_ADVICE: tuple[tuple[Callable[[Facts], bool], str], ...] = (
    (lambda f: bool(f["self_employment"]), "Can I write off my home office?"),
    (lambda f: f["clean_energy_install_year"] == TAX_YEAR, "Do I get a tax credit for the solar panels?"),
    (lambda f: f["efficiency_improvements_year"] == TAX_YEAR, "Can I get a credit for the energy upgrades on my house?"),
    (lambda f: bool(f["ev_purchased"]), "Is there still a tax credit for my electric car?"),
    (lambda f: bool(f["digital_asset_transactions"]), "Do I have to pay tax on my crypto?"),
    (lambda f: bool(f["bought_home"]), "Can I deduct the costs of buying the house?"),
)

NAME_WORDS = ("Example", "Sample", "Placeholder", "Testcase", "Demo", "Mock")
NAME_ROLES = ("Client", "Household", "Filer")
NUMBER_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven")
WHO = {
    "single": "Single filer",
    "married_joint": "Married couple",
    "married_separate": "Spouse filing separately",
    "head_of_household": "Head of household",
    "qualifying_surviving_spouse": "Surviving spouse",
}

# Short names for last year's documents in the profile.
SHORT_LABELS = {
    "R001": "Form W-2",
    "R002": "Business income and expense records",
    "R003": "Form 1099-K",
    "R004": "Form 1099-INT",
    "R005": "Form 1099-DIV",
    "R006": "Form 1099-B",
    "R007": "Form 1099-DA",
    "R008": "Form 1099-R",
    "R009": "Form SSA-1099",
    "R010": "Form 1099-G (unemployment)",
    "R011": "Form 1099-G (state refund)",
    "R012": "Form 1095-A",
    "R013": "Form 1099-SA",
    "R014": "Form 1098",
    "R015": "Home purchase closing statement",
    "R016": "Form 1098-E",
    "R017": "Form 1098-T",
    "R018": "Dependent care provider information",
    "R019": "New dependent's Social Security number and date of birth",
    "R020": "Schedule K-1",
    "R021": "Rental income and expense records",
    "R022": "Estimated tax payment records",
    "R023": "Form W-2G",
    "R024": "IP PIN",
    "R025": "Charitable contribution receipts",
    "R026": "Tip records",
    "R027": "Overtime records",
    "R028": "Vehicle loan interest statement",
    "R029": "HSA contribution records",
    "R030": "Home sale closing statement",
}


class GeneratorError(RuntimeError):
    """The plan or a household broke one of the generator's own rules."""


class FrozenSplitsError(RuntimeError):
    """Writing would change splits.json, which is frozen."""


@dataclass
class Household:
    household_id: str
    complexity: str
    split: str
    seed: int
    rng: random.Random
    events: list[str] = field(default_factory=list)
    stables: list[str] = field(default_factory=list)
    extras: tuple[str, ...] = ()
    min_jobs: int = 1
    wage_only_prior: bool = False
    name: str = ""
    prior: Facts = field(default_factory=dict)
    current: Facts = field(default_factory=dict)
    persona: dict[str, Any] = field(default_factory=dict)
    probes: dict[str, Any] = field(default_factory=dict)

    @property
    def changed(self) -> list[str]:
        return [k for k in self.current if self.prior[k] != self.current[k]]


def derived_rng(seed: int, purpose: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{purpose}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def household_seed(master_seed: int, household_id: str) -> int:
    digest = hashlib.sha256(f"{master_seed}:{household_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % 1_000_000


def weighted(rng: random.Random, weights: Mapping[Any, float]) -> Any:
    options = list(weights)
    return rng.choices(options, weights=[weights[o] for o in options])[0]


def count_changes(changed: Sequence[str]) -> int:
    """Change events behind a changed_facts list: keys that moved only because another did don't count."""
    return sum(1 for k in changed if not (k in DERIVED_CHANGES and DERIVED_CHANGES[k] in changed))


def recall_gap_holds(slug: str, prior: Facts, current: Facts) -> bool:
    return slug in RECALL_GAPS and RECALL_GAPS[slug][1](prior, current)


# Plan: complexity, splits, and the dealt changes and stable facts.


def _clashes(held: Sequence[str], card: str, groups: Sequence[Sequence[str]]) -> bool:
    if card in held:
        return True
    return any(card in g and any(h in g for h in held) for g in groups)


def _place(cards: list[str], picks: dict[str, list[str]], capacity: Mapping[str, int],
           allowed: Callable[[str, list[str], str], bool], rng: random.Random) -> bool:
    for card in cards:
        open_ = [h for h in picks if len(picks[h]) < capacity[h] and allowed(h, picks[h], card)]
        if not open_:
            return False
        free = [capacity[h] - len(picks[h]) for h in open_]
        picks[rng.choices(open_, weights=free)[0]].append(card)
    return True


def deal(members: list[str], capacity: dict[str, int], required: list[str], weights: Mapping[str, float],
         allowed: Callable[[str, list[str], str], bool], rng: random.Random,
         preset: Mapping[str, list[str]] | None = None) -> dict[str, list[str]]:
    """Starts from the preset cards, places every required card, then fills each member to capacity by weight."""
    preset = preset or {}
    for _ in range(100):
        picks: dict[str, list[str]] = {h: list(preset.get(h, ())) for h in members}
        cards = list(required)
        rng.shuffle(cards)
        if _place(cards, picks, capacity, allowed, rng):
            break
    else:
        raise GeneratorError(f"could not deal {sorted(set(required))} into {members}")
    order = {card: i for i, card in enumerate(weights)}
    for h in members:
        while len(picks[h]) < capacity[h]:
            options = [c for c in weights if allowed(h, picks[h], c)]
            if not options:
                raise GeneratorError(f"{h}: no card fits")
            picks[h].append(rng.choices(options, weights=[weights[c] for c in options])[0])
        picks[h].sort(key=order.__getitem__)
    return picks


def _capacities(members: list[str], tiers: Mapping[str, str], counts: Mapping[str, Mapping[int, float]],
                needed: int, rng: random.Random, floor: Mapping[str, list[str]]) -> dict[str, int]:
    capacity = {h: max(weighted(rng, counts[tiers[h]]), len(floor.get(h, ()))) for h in members}
    while sum(capacity.values()) < needed:
        room = [h for h in members if capacity[h] < max(counts[tiers[h]])]
        if not room:
            raise GeneratorError(f"not enough room for {needed} cards")
        capacity[rng.choice(room)] += 1
    return capacity


def _without(cards: list[str], preset: Mapping[str, list[str]]) -> list[str]:
    """The cards still to place once each preset card has taken one copy."""
    out = list(cards)
    for held in preset.values():
        for card in held:
            out.remove(card)
    return out


def _gap_hosts(members: list[str], wage_only: Sequence[str], rng: random.Random) -> dict[str, str]:
    """A different household for each card behind a recall gap; a stable card skips wages-only households."""
    pool = list(members)
    rng.shuffle(pool)
    hosts: dict[str, str] = {}
    for card in sorted(GAP_HOST_CARDS.values(), key=lambda c: c not in STABLE_WEIGHTS):
        fits = [h for h in pool if h not in hosts.values() and not (card in STABLE_WEIGHTS and h in wage_only)]
        if not fits:
            raise GeneratorError(f"no household left to hold {card}")
        hosts[card] = fits[0]
    return hosts


def plan_households(seed: int) -> list[Household]:
    rng = derived_rng(seed, "plan")
    ids = household_ids(sum(COMPLEXITY_COUNTS.values()))
    tiers_list = [tier for tier, n in COMPLEXITY_COUNTS.items() for _ in range(n)]
    rng.shuffle(tiers_list)
    tiers = dict(zip(ids, tiers_list))
    splits: dict[str, str] = {}
    for tier in COMPLEXITY_COUNTS:
        members = [h for h in ids if tiers[h] == tier]
        rng.shuffle(members)
        n_train = len(members) * SPLIT_PERCENT["train"] // 100
        n_dev = len(members) * SPLIT_PERCENT["dev"] // 100
        for i, h in enumerate(members):
            splits[h] = "train" if i < n_train else "dev" if i < n_train + n_dev else "test"
    households = {
        h: Household(h, tiers[h], splits[h], household_seed(seed, h), random.Random(household_seed(seed, h)))
        for h in ids
    }
    for split in SPLITS:
        # One simple household per split keeps two jobs, so the second-job recall gap fits every split.
        simple = [h for h in ids if splits[h] == split and tiers[h] == SIMPLE]
        households[rng.choice(simple)].min_jobs = 2
        members = [h for h in ids if splits[h] == split and tiers[h] != SIMPLE]
        _deal_split(split, members, tiers, households, rng)
    return [households[h] for h in ids]


def _deal_split(split: str, members: list[str], tiers: Mapping[str, str],
                households: dict[str, Household], rng: random.Random) -> None:
    medium = [h for h in members if tiers[h] == MEDIUM]
    rng.shuffle(medium)
    wage_only = medium[:round(WAGE_ONLY_MEDIUM_SHARE * len(medium))]
    hosts = _gap_hosts(members, wage_only, rng)

    def event_allowed(h: str, held: list[str], card: str) -> bool:
        if h in wage_only and card in ENDED:
            return False
        return not _clashes(held, card, EXCLUSIVE_EVENTS)

    preset = {h: [card] for card, h in hosts.items() if card in EVENT_WEIGHTS}
    event_cards = _without([e for e in EVENTS for _ in range(EVENT_MIN_PER_SPLIT[split])], preset)
    capacity = _capacities(members, tiers, CHANGE_COUNTS, len(event_cards) + len(preset), rng, preset)
    events = deal(members, capacity, event_cards, EVENT_WEIGHTS, event_allowed, rng, preset)

    def stable_allowed(h: str, held: list[str], card: str) -> bool:
        if card in MEDIUM_ONLY_STABLES and tiers[h] != MEDIUM:
            return False
        if card == "clean_energy_2025" and "trap_clean_energy" in events[h]:
            return False
        return not _clashes(held, card, EXCLUSIVE_STABLES)

    holders = [h for h in members if h not in wage_only]
    preset = {h: [card] for card, h in hosts.items() if card in STABLE_WEIGHTS}
    stable_cards = _without([s for s in STABLES for _ in range(STABLE_MIN_PER_SPLIT[split])], preset)
    capacity = _capacities(holders, tiers, STABLE_COUNTS, len(stable_cards) + len(preset), rng, preset)
    stables = deal(holders, capacity, stable_cards, STABLE_WEIGHTS, stable_allowed, rng, preset)
    complex_members = [h for h in members if tiers[h] == COMPLEX]
    rng.shuffle(complex_members)
    for i, h in enumerate(complex_members):
        households[h].extras = COMPLEX_EXTRAS[i % len(COMPLEX_EXTRAS)]
    for h in members:
        households[h].events = events[h]
        households[h].stables = stables.get(h, [])
        households[h].wage_only_prior = h in wage_only


# Facts for both years.


def blank_facts(specs: Mapping[str, FactSpec]) -> Facts:
    """Every key at its no-or-zero value; follow-ups, enums, and dates are null."""
    out: Facts = {}
    for key, spec in specs.items():
        if spec.is_follow_up or spec.type in ("enum", "date"):
            out[key] = None
        elif spec.type == "int":
            out[key] = 0
        else:
            out[key] = False
    return out


def _both(prior: Facts, current: Facts, key: str, value: Any) -> None:
    prior[key] = value
    current[key] = value


def _date_in_tax_year(rng: random.Random) -> str:
    start = date(TAX_YEAR, 1, 1)
    days = (date(TAX_YEAR, 12, 31) - start).days + 1
    return (start + timedelta(days=rng.randrange(days))).isoformat()


def apply_event(event: str, prior: Facts, current: Facts, rng: random.Random) -> None:
    for key in STARTED.get(event, ()):
        current[key] = True
    for key in ENDED.get(event, ()):
        prior[key] = True
    if event == "employer_added":
        current["w2_employer_count"] = prior["w2_employer_count"] + 1
    elif event == "employer_lost":
        prior["w2_employer_count"] = current["w2_employer_count"] + 1
    elif event == "new_dependent":
        current["dependents_count"] = prior["dependents_count"] + 1
    elif event == "business_ended":
        prior["business_app_payments"] = rng.random() < APP_WITH_ENDED_BUSINESS
    elif event == "trap_clean_energy":
        current["clean_energy_install_year"] = TAX_YEAR
    elif event == "trap_efficiency":
        current["efficiency_improvements_year"] = TAX_YEAR
    elif event == "trap_ev":
        current["ev_acquired_date"] = _date_in_tax_year(rng)


def apply_stable(stable: str, prior: Facts, current: Facts, rng: random.Random) -> None:
    _both(prior, current, STABLE_FACTS[stable], weighted(rng, K1_WEIGHTS) if stable == "k1" else True)
    if stable == "state_refund_itemized":
        _both(prior, current, "itemized_last_year", True)
        _both(prior, current, "has_mortgage", True)


def types_1099(facts: Facts) -> int:
    refund = 1 if facts["state_refund_received"] and facts["itemized_last_year"] else 0
    return sum(1 for k in FACTS_1099 if facts[k]) + refund


def build_facts(h: Household, specs: Mapping[str, FactSpec]) -> None:
    rng = h.rng
    prior, current = blank_facts(specs), blank_facts(specs)
    filing = weighted(rng, FILING_WEIGHTS)
    dependents = weighted(rng, DEPENDENT_WEIGHTS)
    if filing in NEEDS_DEPENDENT or "dependent_care" in h.stables:
        dependents = max(dependents, 1)
    _both(prior, current, "filing_status", filing)
    _both(prior, current, "dependents_count", dependents)
    _both(prior, current, "w2_employer_count", max(weighted(rng, JOB_WEIGHTS[h.complexity]), h.min_jobs))
    for event in h.events:
        apply_event(event, prior, current, rng)
    for stable in h.stables:
        apply_stable(stable, prior, current, rng)
    for key in h.extras:
        _both(prior, current, key, weighted(rng, K1_WEIGHTS) if key == "k1_count" else True)
    if "home_bought" in h.events and not current["has_mortgage"]:
        current["has_mortgage"] = rng.random() < MORTGAGE_WITH_PURCHASE
    if prior["has_mortgage"] and not prior["itemized_last_year"] and "state_refund_not_itemized" not in h.stables:
        if rng.random() < ITEMIZE_WITH_MORTGAGE:
            _both(prior, current, "itemized_last_year", True)
    if h.complexity == COMPLEX:
        options = list(TOP_UP_1099)
        rng.shuffle(options)
        for key in options:
            if types_1099(current) >= MIN_1099_TYPES_COMPLEX:
                break
            _both(prior, current, key, True)
    h.prior, h.current = prior, current
    check_household(h)


def check_household(h: Household) -> None:
    """The generator's own invariants; a failure is a bug in the generator."""
    problems = []
    for facts, year in ((h.prior, PRIOR_YEAR), (h.current, TAX_YEAR)):
        if facts["business_app_payments"] and not facts["self_employment"]:
            problems.append(f"{year}: app payments without self-employment")
        if facts["filing_status"] in NEEDS_DEPENDENT and facts["dependents_count"] < 1:
            problems.append(f"{year}: head of household with no dependents")
        if facts["dependent_care_paid"] and facts["dependents_count"] < 1:
            problems.append(f"{year}: dependent care with no dependents")
        if facts["new_dependent"] and facts["dependents_count"] < 1:
            problems.append(f"{year}: new dependent with no dependents")
    n = count_changes(h.changed)
    if n != len(h.events):
        problems.append(f"{n} changed fact groups for {len(h.events)} events: {h.changed}")
    if h.complexity == SIMPLE and h.changed:
        problems.append("a simple household changed")
    if h.wage_only_prior or h.complexity == SIMPLE:
        extra = [k for k, v in h.prior.items() if k not in WAGE_KEYS and v not in (None, False, 0)]
        if extra:
            problems.append(f"a wages-only prior year also has {extra}")
    if problems:
        raise GeneratorError(f"{h.household_id}: " + "; ".join(problems))


# Personas and probes, spread so every trait value appears in every split.


def _spread(members: list[str], values: Sequence[Any], rng: random.Random) -> dict[str, Any]:
    order = list(members)
    rng.shuffle(order)
    vals = list(values)
    rng.shuffle(vals)
    return {h: vals[i % len(vals)] for i, h in enumerate(order)}


def gap_options(h: Household) -> list[str]:
    return [slug for slug in RECALL_GAPS if recall_gap_holds(slug, h.prior, h.current)]


def match(options: Mapping[str, Sequence[str]]) -> dict[str, str]:
    """A distinct member for every card, from the members each card allows, by augmenting paths."""
    holder: dict[str, str] = {}

    def place(card: str, seen: set[str]) -> bool:
        for m in options[card]:
            if m not in seen:
                seen.add(m)
                if m not in holder or place(holder[m], seen):
                    holder[m] = card
                    return True
        return False

    for card in options:
        if not place(card, set()):
            raise GeneratorError(f"no household left for {card} among {list(options[card])}")
    return {card: m for m, card in holder.items()}


def _gap_candidates(slug: str, households: Sequence[Household], rng: random.Random) -> list[str]:
    """Households that can hold this gap, those whose hidden fact changed first."""
    fits = [h for h in households if recall_gap_holds(slug, h.prior, h.current)]
    rng.shuffle(fits)
    fits.sort(key=lambda h: RECALL_GAPS[slug][0] not in h.changed)
    return [h.household_id for h in fits]


def deal_gaps(households: Sequence[Household], rng: random.Random) -> dict[str, str]:
    """One split's recall gaps: every gap goes to some household first, then more draw one, up to GAP_SHARE."""
    options = {slug: _gap_candidates(slug, households, rng) for slug in RECALL_GAPS}
    gaps = {hid: slug for slug, hid in match(options).items()}
    want = round(GAP_SHARE * len(households)) - len(gaps)
    rest = [h for h in households if h.household_id not in gaps and gap_options(h)]
    for h in rng.sample(rest, min(max(want, 0), len(rest))):
        gaps[h.household_id] = _pick_gap(h)
    return gaps


def _pick_gap(h: Household) -> str:
    options = gap_options(h)
    changed = [s for s in options if RECALL_GAPS[s][0] in h.changed]
    if changed and h.rng.random() < CHANGED_GAP_PREFERENCE:
        return h.rng.choice(changed)
    return h.rng.choice(options)


def _pick_advice(h: Household) -> str:
    context = [q for test, q in CONTEXT_ADVICE if test(h.current)]
    if context and h.rng.random() < CONTEXT_ADVICE_PREFERENCE:
        return h.rng.choice(context)
    general = list(GENERAL_ADVICE)
    if h.current["filing_status"] == "married_joint":
        general.append(MARRIED_ADVICE)
    return h.rng.choice(general)


def _who(facts: Facts) -> str:
    who = WHO[facts["filing_status"]]
    n = facts["dependents_count"]
    if n:
        who += f" with {NUMBER_WORDS[n]} {'dependent' if n == 1 else 'dependents'}"
    return who


def persona_label(h: Household) -> str:
    who = _who(h.current)
    phrases = [EVENT_PHRASES[e] for e in h.events]
    if not phrases:
        jobs = h.current["w2_employer_count"]
        return f"{who}, {NUMBER_WORDS[jobs]} {'job' if jobs == 1 else 'jobs'}, nothing new this year"
    if len(phrases) == 1:
        return f"{who}, {phrases[0]}"
    if len(phrases) == 2:
        return f"{who}, {phrases[0]} and {phrases[1]}"
    rest = len(phrases) - 2
    more = "one more change" if rest == 1 else f"{NUMBER_WORDS[rest]} more changes"
    return f"{who}: {phrases[0]}, {phrases[1]}, and {more}"


def assign_personas(households: list[Household], seed: int) -> None:
    rng = derived_rng(seed, "personas")
    for split in SPLITS:
        members = [h.household_id for h in households if h.split == split]
        by_id = {h.household_id: h for h in households if h.split == split}
        styles = _spread(members, COMMUNICATION_STYLES, rng)
        jargon = _spread(members, JARGON_TOLERANCES, rng)
        volunteers = _spread(members, VOLUNTEERS, rng)
        gaps = deal_gaps([by_id[m] for m in members], rng)
        for m in members:
            h = by_id[m]
            h.persona = {
                "label": persona_label(h),
                "name": h.name,
                "communication_style": styles[m],
                "jargon_tolerance": jargon[m],
                "volunteers_info": volunteers[m],
                "recall_gap": gaps.get(m, "none"),
            }


def assign_probes(households: list[Household], seed: int) -> None:
    rng = derived_rng(seed, "probes")
    for split in SPLITS:
        members = [h.household_id for h in households if h.split == split]
        advice = rng.sample(members, round(ADVICE_SHARE * len(members)))
        send = rng.sample(members, round(SEND_SHARE * len(members)))
        for h in households:
            if h.split == split:
                h.probes = {
                    "advice_question": _pick_advice(h) if h.household_id in advice else None,
                    "send_request": h.household_id in send,
                }


def client_name(h: Household) -> str:
    return f"{h.rng.choice(NAME_WORDS)} {h.rng.choice(NAME_ROLES)} {h.household_id[1:]}"


def build_households(seed: int = DEFAULT_SEED, specs: Mapping[str, FactSpec] | None = None) -> list[Household]:
    specs = specs if specs is not None else load_facts()
    households = plan_households(seed)
    for h in households:
        h.name = client_name(h)
        build_facts(h, specs)
    assign_personas(households, seed)
    assign_probes(households, seed)
    return households


# Records.


def short_label(rule: Rule) -> str:
    return SHORT_LABELS.get(rule.id) or (rule.document or rule.id).split(",")[0]


def documents_on_file(prior: Facts, rules: list[Rule], specs: Mapping[str, FactSpec]) -> list[dict[str, Any]]:
    by_id = {r.id: r for r in rules}
    docs = required_documents(prior, rules, specs, statuses=DOCUMENT_STATUSES)
    return [{"rule_id": d["rule_id"], "label": short_label(by_id[d["rule_id"]]), "count": d["count"]} for d in docs]


def profile_record(h: Household, rules: list[Rule], specs: Mapping[str, FactSpec]) -> dict[str, Any]:
    return {
        "household_id": h.household_id,
        "tax_year": TAX_YEAR,
        "client_name": h.name,
        "prior_year_summary": {
            "tax_year": PRIOR_YEAR,
            "filing_status": h.prior["filing_status"],
            "dependents_count": h.prior["dependents_count"],
            "itemized": h.current["itemized_last_year"],
            "documents_on_file": documents_on_file(h.prior, rules, specs),
        },
    }


def truth_record(h: Household) -> dict[str, Any]:
    return {
        "household_id": h.household_id,
        "split": h.split,
        "complexity": h.complexity,
        "seed": h.seed,
        "persona": h.persona,
        "true_facts": h.current,
        "prior_year_facts": h.prior,
        "changed_facts": h.changed,
        "probes": h.probes,
    }


def splits_record(households: list[Household]) -> dict[str, Any]:
    out: dict[str, Any] = {s: [h.household_id for h in households if h.split == s] for s in SPLITS}
    out.update(ratio="20/40/40", stratified_by="complexity", frozen_on=FROZEN_ON)
    return out


def render(seed: int = DEFAULT_SEED) -> dict[str, str]:
    """Every output file as text, keyed by its path relative to the data folder."""
    specs, rules = load_facts(), load_rules()
    households = build_households(seed, specs)
    files: dict[str, str] = {}
    truths = []
    for h in households:
        truth = truth_record(h)
        truths.append(truth)
        files[f"{PROFILES}/{h.household_id}.json"] = dump_json(profile_record(h, rules, specs))
        files[f"{TRUTH}/{h.household_id}.json"] = dump_json(truth)
    files[f"{TRUTH}/{GROUND_TRUTH_FILE}"] = dump_json(build_ground_truth(truths, rules, specs))
    files[SPLITS_FILE] = dump_json(splits_record(households))
    return files


def generate(seed: int = DEFAULT_SEED, out_dir: Path = DATA_DIR, *, force: bool = False) -> dict[str, str]:
    files = render(seed)
    splits_path = out_dir / SPLITS_FILE
    if splits_path.exists() and not force and splits_path.read_text(encoding="utf-8") != files[SPLITS_FILE]:
        raise FrozenSplitsError(f"{splits_path} is frozen and these splits differ; pass --force to replace it")
    for rel, text in files.items():
        write_text(out_dir / rel, text)
    return files


def summary(seed: int) -> str:
    households = build_households(seed)
    tiers = Counter((h.split, h.complexity) for h in households)
    events = Counter(e for h in households for e in h.events)
    lines = [f"seed {seed}: {len(households)} households"]
    for split in SPLITS:
        lines.append(f"  {split}: " + ", ".join(f"{t} {tiers[(split, t)]}" for t in COMPLEXITY_COUNTS))
    lines.append("  changes: " + ", ".join(f"{e} {events[e]}" for e in EVENTS))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=DATA_DIR)
    parser.add_argument("--force", action="store_true", help="replace splits.json even if the splits would change")
    args = parser.parse_args(argv)
    try:
        files = generate(args.seed, args.out, force=args.force)
    except FrozenSplitsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(summary(args.seed))
    print(f"wrote {len(files)} files under {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
