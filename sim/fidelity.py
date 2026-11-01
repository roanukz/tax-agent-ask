"""Block 4 fidelity check: does the simulated client tell the truth, and only what it's asked?

    uv run python -m sim.fidelity --dry-run
        Runs the whole pipeline offline with a fake client that answers from
        the truth, so it should score 100% with no leaks. Spends nothing,
        writes nothing to the ledger, and writes its reports to a new temp
        folder (or --out-dir), each marked DRY RUN.

    uv run python -m sim.fidelity
        The live check on Haiku. Needs TAX_AGENT_API_KEY in .env, reserves an
        estimated cost in the ledger before the first call, stops before any
        call that could push the run past that reservation, and settles the
        actual cost after the last. Writes docs/fidelity/fidelity_report.md,
        docs/fidelity/fidelity_answers.json, and
        data/labels/sim_answers_for_review.md.

Eleven scripted plain-language questions run over train and dev households,
chosen deterministically: 20 drawn by complexity, plus every other one whose
client prompt has a "Last year only" line. Code compares every revealed value
with the truth (bar: at least 98% match, with every answer scored) and flags
the leaks it can see: a fact the question didn't ask about from a client who
doesn't volunteer, and a fact key name or form number in the answer text.
Roanuk reads 30 answers for leaks the JSON doesn't show. Test households are
never used.

The simulator sends an answer that contradicts the truth outside the recall
gap back to its model once (sim.simulator), and an agent sees only the
answer kept, so the match rate is on the answers kept. A retry that reveals
a fact the recall gap hides differently from the first answer is dropped,
and the first answer kept. The report counts the answers sent back and the
ones kept that way, and lists each fact the retry corrected or left wrong,
with its first and final value, so the simulator's raw error rate stays in
view.

The first question, q00, asks what changed since last year before any topic
comes up, as the agent does. It asks about every change the client
remembers, which is every change but the ones its recall gap hides, so code
also counts the remembered changes the answer left out and the recall gaps
that broke on it. Both are shown for judgment and are not part of the bar.

The blind labels read a "Last year only" line as this year's twice, so code
also counts the revealed values of the facts that ended: how many match the
truth and how many say the fact is true this year, beside the same facts in
households where they are true this year, and the report lists every answer
that asks about or reveals one. Those values count in the match rate like any
other, but they are too few for the bar to see a misread on its own, so
Roanuk reads that list as well. The counts and the list read the answers
kept, after the contradiction retry, which sends back every first reply that
reveals such a fact as true; code counts those first replies apart, and the
Contradictions sent back table lists them.

Exit codes: 0 when the bar is met, 1 when it is not, 2 when data is missing,
3 when a spend limit stopped the run.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


from agent.env import HarnessSettings, load_settings, messages_client
from ledger.spend import (
    DEFAULT_LEDGER,
    DEFAULT_PRICES,
    Ledger,
    RunCapExceeded,
    SpendCapExceeded,
    cost_usd,
    load_prices,
)
from rules.engine import FactSpec, default_value, load_facts
from sim.budget import BudgetedClient, BudgetExceeded
from sim.fakes import TruthEchoClient
from sim.simulator import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    PERSONA_TRAITS,
    RETRY_CHANGED_GAP,
    ClientSimulator,
    SimulatorError,
    Usage,
    build_system_prompt,
    ended_facts,
    hidden_facts,
    remembered_changes,
    true_values,
)

REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
REPORT_PATH = Path("docs/fidelity/fidelity_report.md")
ANSWERS_PATH = Path("docs/fidelity/fidelity_answers.json")
REVIEW_PATH = Path("data/labels/sim_answers_for_review.md")

# The draw by complexity; every train and dev household with a fact that ended is added to it.
HOUSEHOLDS = 20
REVIEW_ANSWERS = 30
SEED = 4404
MATCH_RATE_BAR = 0.98
CONCURRENCY = 2
POOL_SPLITS = ("train", "dev")

# Cost estimate for the reservation: no cache discount, every answer at its
# full max_tokens, and a retry margin. The spend guard holds the run to it.
# At the estimate's own prices the margin pays for one retry, for an unusable
# reply or a contradiction, on about a quarter of the answers. The first live
# check had no unusable reply, would have sent back 5 of its 220 answers, and
# cost about a fifth of its estimate; the Block 6 agent runs would have sent
# back 12 of 238.
CHARS_PER_TOKEN = 3
ANSWER_TOKENS_IN_HISTORY = 150
RETRY_MARGIN = 1.25

# Answers whose revealed facts can't be scored at all.
UNSCORED_ANOMALIES = ("missing_revealed", "revealed_not_object")
SPEND_LIMITS = (RunCapExceeded, SpendCapExceeded, BudgetExceeded)

HOUSEHOLD_ID = re.compile(r"^[A-Za-z0-9_-]+$")
# Forms a client might name, plus any "Form NNNN". Bare four-digit numbers
# other than 1098 and 1099 are left out, since they read like amounts.
FORM_NUMBER = re.compile(
    r"\b(?:W-?2G?|W-?4|W-?9|1099(?:-?[A-Z]{1,3})?|1098(?:-?[A-Z])?|1095-?[ABC]|SSA-?1099|5498(?:-?SA)?"
    r"|K-?1|Form\s+\d{4}(?:-?[A-Z]{1,3})?)s?\b",
    re.IGNORECASE,
)
SCHEDULE = re.compile(r"\bSchedule [A-Z]{1,2}\b")
DASHES = str.maketrans({chr(0x2013): "-", chr(0x2014): "-"})
# The Contradictions sent back table's outcome for a fact whose first answer was kept because the retry changed a
# fact the recall gap hides; like an unresolved one, it is a mismatch.
GAP_KEPT = "unresolved, recall gap kept"


@dataclass(frozen=True)
class Question:
    id: str
    topic: str
    text: str
    asks: tuple[str, ...]
    # "Anything else that changed?" may also bring up any fact that changed this year.
    open_to_changes: bool = False
    # "What changed since last year?" asks about every change the client remembers, so its facts differ by household.
    asks_changes: bool = False


QUESTIONS: tuple[Question, ...] = (
    Question(
        "q00",
        "what changed",
        "Before we go topic by topic, has anything changed for you or your household since last year?",
        (),
        asks_changes=True,
    ),
    Question(
        "q01",
        "jobs",
        "Let's start with work. How many employers paid you wages this year, counting any job you left? "
        "Did you get tips, overtime pay, or unemployment benefits?",
        ("w2_employer_count", "tips_income", "overtime_pay", "unemployment"),
    ),
    Question(
        "q02",
        "side work",
        "Did you do any freelance, contract, or gig work, or run a business of your own? "
        "If you did, did customers pay you through a payment app or an online marketplace?",
        ("self_employment", "business_app_payments"),
    ),
    Question(
        "q03",
        "banks and investments",
        "Did you earn interest from a bank or credit union, get dividends, or sell any stocks, funds, or "
        "cryptocurrency? Do you own a share of a partnership or small company, or get income from an estate or trust?",
        ("interest_income", "dividend_income", "sold_investments", "digital_asset_transactions", "k1_count"),
    ),
    Question(
        "q04",
        "retirement and Social Security",
        "Did you take any money out of a retirement account or pension, or roll one over? "
        "Did you receive Social Security benefits?",
        ("retirement_distribution", "social_security"),
    ),
    Question(
        "q05",
        "health coverage and HSAs",
        "How did your household get health insurance this year? Did anyone buy a plan through HealthCare.gov "
        "or a state exchange, and did you put money into or take money out of a health savings account?",
        ("marketplace_coverage", "hsa_withdrawal", "hsa_direct_contribution"),
    ),
    Question(
        "q06",
        "home",
        "Tell me about your home. Do you have a mortgage, did you buy or sell a home this year, "
        "and do you rent out any property?",
        ("has_mortgage", "bought_home", "sold_home", "rental_property"),
    ),
    Question(
        "q07",
        "school and child care",
        "Did you pay college tuition or student loan interest for yourself or anyone you support? "
        "Did you pay for child care, or care for another dependent, so you could work?",
        ("tuition_paid", "student_loan_interest", "dependent_care_paid"),
    ),
    Question(
        "q08",
        "charity and gambling",
        "Did you give money or goods to charity this year? "
        "Did you win any money gambling, at a casino, or in a lottery?",
        ("charitable_giving", "gambling_winnings"),
    ),
    Question(
        "q09",
        "energy upgrades and vehicles",
        "Did you put in solar panels or other clean energy equipment, or make energy-saving upgrades like new "
        "windows or a heat pump? If so, what year was the work finished? Did you buy an electric car, or take out "
        "a loan for any new car, and if so, when did you sign the purchase contract?",
        (
            "clean_energy_installed",
            "clean_energy_install_year",
            "efficiency_improvements",
            "efficiency_improvements_year",
            "ev_purchased",
            "ev_acquired_date",
            "new_vehicle_loan",
        ),
    ),
    Question(
        "q10",
        "anything else that changed",
        "Did anything else change for you this year that we haven't talked about, such as getting married or "
        "divorced, a new baby or someone new you support, tax payments you sent in yourself, a state tax refund, "
        "or a special PIN from the IRS?",
        (
            "filing_status",
            "dependents_count",
            "new_dependent",
            "estimated_payments_made",
            "state_refund_received",
            "ip_pin_enrolled",
        ),
        open_to_changes=True,
    ),
)


CHANGE_QUESTION = next(q for q in QUESTIONS if q.asks_changes)


@dataclass
class AnswerRecord:
    household_id: str
    question_id: str
    question: str
    answer: str | None
    revealed: dict[str, Any]
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    retried: bool = False
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    # The contradiction retry, as SimAnswer has it: the facts it corrected, and the ones the answer kept gets wrong.
    corrected: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FidelityScore:
    households: list[str]
    compared: int
    matched: int
    match_rate: float | None
    # The bar: match_rate at least MATCH_RATE_BAR and no unscored answers.
    meets_bar: bool
    mismatches: list[dict[str, Any]]
    anomalies: list[dict[str, Any]]
    leaks: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    retries: int
    per_question: dict[str, dict[str, int]]
    recall_gap_households: list[str]
    match_rate_without_recall_gaps: float | None
    answers: int = 0
    # Answers that failed twice or had no revealed object, so nothing in them was scored.
    unscored: list[dict[str, Any]] = field(default_factory=list)
    # Asked facts with a non-null truth, and how many of them the answers revealed.
    asked_facts: int = 0
    asked_facts_revealed: int = 0
    # The change question: remembered changes asked and named, the ones left out, and gaps it broke.
    changes_asked: int = 0
    changes_named: int = 0
    missed_changes: list[dict[str, Any]] = field(default_factory=list)
    broken_gaps: list[dict[str, Any]] = field(default_factory=list)
    # Facts that ended (the change list's "Last year only" lines): the households with one; the ended facts
    # asked and how many the answers named; and every revealed value of one, how many match, and how many
    # say the fact is true this year, the misread. Each mismatch is also in mismatches, or in anomalies when
    # the value is uncoercible.
    ended_households: list[str] = field(default_factory=list)
    ended_asked: int = 0
    ended_revealed: int = 0
    ended_compared: int = 0
    ended_matched: int = 0
    ended_true: int = 0
    # The same misread before the contradiction retry: ended facts a first reply revealed as true. Every such reply
    # contradicts the truth outside any recall gap, so the simulator sends it back, and ended_true reads the answer
    # kept; this keeps the raw misread in view.
    ended_true_first: int = 0
    ended_mismatches: list[dict[str, Any]] = field(default_factory=list)
    # The control: revealed values of the same facts in the households where they are true this year.
    control_households: list[str] = field(default_factory=list)
    control_compared: int = 0
    control_matched: int = 0
    # The households drawn by complexity, PRD 6.4's sample, and the match rate over them alone.
    drawn_households: list[str] = field(default_factory=list)
    drawn_compared: int = 0
    drawn_matched: int = 0
    # The contradiction retry in the answers scored: each fact it corrected, and each fact the answer kept still
    # gets wrong (also in mismatches), with its household and question. The match rate is on the answers kept, so
    # these keep the simulator's raw error in view.
    corrected: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)

    @property
    def contradicted_answers(self) -> int:
        """Answers sent back because their first usable reply contradicted the truth outside the recall gap."""
        return len({(x["household_id"], x["question_id"]) for x in [*self.corrected, *self.unresolved]})

    @property
    def gap_kept_answers(self) -> int:
        """Answers sent back whose first answer was kept, because the retry changed a fact the recall gap hides."""
        return len({(x["household_id"], x["question_id"]) for x in self.unresolved if x.get(RETRY_CHANGED_GAP)})

    @property
    def coverage(self) -> float | None:
        return self.asked_facts_revealed / self.asked_facts if self.asked_facts else None

    @property
    def change_coverage(self) -> float | None:
        return self.changes_named / self.changes_asked if self.changes_asked else None

    @property
    def ended_match_rate(self) -> float | None:
        return self.ended_matched / self.ended_compared if self.ended_compared else None

    @property
    def control_match_rate(self) -> float | None:
        return self.control_matched / self.control_compared if self.control_compared else None

    @property
    def drawn_match_rate(self) -> float | None:
        return self.drawn_matched / self.drawn_compared if self.drawn_compared else None


@dataclass
class CheckOutcome:
    run_id: str
    dry_run: bool
    score: FidelityScore
    records: list[AnswerRecord]
    usage: Usage
    cost_usd: float
    estimate_usd: float
    report_path: Path
    review_path: Path
    answers_path: Path


# ---- data and sampling -------------------------------------------------------


def _rank(seed: int, *parts: str) -> str:
    return hashlib.sha256(":".join([str(seed), *parts]).encode()).hexdigest()


def pool_ids(splits: Mapping[str, Any]) -> list[str]:
    """Train and dev households, never test ones."""
    test = set(splits.get("test") or [])
    pool = {h for name in POOL_SPLITS for h in (splits.get(name) or [])}
    return sorted(pool - test)


def choose_households(
    splits: Mapping[str, Any],
    complexity: Mapping[str, str] | None = None,
    *,
    n: int = HOUSEHOLDS,
    seed: int = SEED,
) -> list[str]:
    """n train and dev households, stratified by complexity, by a seeded hash rank.

    The hash rank doesn't depend on Python's random module, so the choice is
    the same on every machine and version.
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"households must be a whole number of at least 1, not {n!r}")
    pool = pool_ids(splits)
    if n >= len(pool):
        return pool
    complexity = complexity or {}
    tiers: dict[str, list[str]] = {}
    for hid in pool:
        tiers.setdefault(str(complexity.get(hid, "unknown")), []).append(hid)
    exact = {tier: n * len(ids) / len(pool) for tier, ids in tiers.items()}
    quota = {tier: int(share) for tier, share in exact.items()}
    by_remainder = sorted(tiers, key=lambda t: (-(exact[t] - quota[t]), t))
    for tier in by_remainder[: n - sum(quota.values())]:
        quota[tier] += 1
    chosen: list[str] = []
    for tier, ids in sorted(tiers.items()):
        chosen.extend(sorted(ids, key=lambda h: _rank(seed, h))[: quota[tier]])
    return sorted(chosen)


def sample_households(
    splits: Mapping[str, Any],
    pool: Mapping[str, Mapping[str, Any]],
    specs: Mapping[str, FactSpec],
    *,
    n: int = HOUSEHOLDS,
    seed: int = SEED,
) -> list[str]:
    """The n households choose_households draws, plus every train and dev household with a fact that ended.

    Adding them rather than reserving places in the draw keeps the drawn
    households, which hold the cases where the same facts are true this year.
    """
    drawn = choose_households(splits, {h: t.get("complexity") for h, t in pool.items()}, n=n, seed=seed)
    allowed = set(pool_ids(splits))
    return sorted(set(drawn) | {h for h, t in pool.items() if h in allowed and ended_facts(t, specs)})


def choose_answers_for_review(
    records: Sequence[AnswerRecord],
    *,
    n: int = REVIEW_ANSWERS,
    seed: int = SEED,
    questions: Sequence[Question] = QUESTIONS,
) -> list[AnswerRecord]:
    """n answers spread evenly over the questions, by a seeded hash rank.

    Each round takes every question's next answer, so no question gets more
    than one answer above another; when n doesn't divide evenly, the hash
    rank picks which questions get the extra one.
    """
    usable = [r for r in records if r.error is None and r.answer is not None]
    order = [q.id for q in questions]
    ranked = {
        qid: sorted((r for r in usable if r.question_id == qid), key=lambda r: _rank(seed, r.household_id, qid))
        for qid in order
    }
    chosen: list[AnswerRecord] = []
    depth = 0
    while len(chosen) < n and any(len(ranked[qid]) > depth for qid in order):
        layer = sorted(
            (ranked[qid][depth] for qid in order if len(ranked[qid]) > depth),
            key=lambda r: _rank(seed, r.household_id, r.question_id),
        )
        chosen.extend(layer[: n - len(chosen)])
        depth += 1
    return sorted(chosen, key=lambda r: (order.index(r.question_id), r.household_id))


def load_splits(data_dir: Path) -> dict[str, Any]:
    path = Path(data_dir) / "splits.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; generate the households (Block 2) first")
    return json.loads(path.read_text())


def load_truths(data_dir: Path, household_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Truth files for the named households only."""
    truths = {}
    for hid in household_ids:
        if not HOUSEHOLD_ID.match(hid):
            raise ValueError(f"bad household ID {hid!r}")
        truths[hid] = json.loads((Path(data_dir) / "truth" / f"{hid}.json").read_text())
    return truths


# ---- scoring -----------------------------------------------------------------


def truth_values(truth: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> dict[str, Any]:
    """Every fact key's true value after coerce, with engine defaults for any missing key.

    The simulator's contradiction check compares with the same values (sim.simulator.true_values).
    """
    facts = truth["true_facts"]
    unknown = sorted(set(facts) - set(specs))
    if unknown:
        raise ValueError(f"{truth.get('household_id')}: unknown fact keys {unknown}")
    return true_values(facts, specs)


def recall_gap_of(truth: Mapping[str, Any]) -> str | None:
    """The persona's recall gap, or None; the generator writes "none" for no gap."""
    gap = (truth.get("persona") or {}).get("recall_gap")
    return None if gap in (None, "", "none") else str(gap)


def _same(a: Any, b: Any) -> bool:
    return type(a) is type(b) and a == b


def text_leaks(answer: str, specs: Mapping[str, FactSpec]) -> list[dict[str, Any]]:
    """Fact key names and form numbers in the words the agent would see.

    A key with no underscore, such as unemployment, is an everyday word, so
    only keys with one count as key names.
    """
    lowered = answer.lower()
    leaks = [
        {"kind": "fact_key_in_text", "match": key}
        for key in specs
        if "_" in key and re.search(rf"(?<![a-z0-9_]){re.escape(key)}(?![a-z0-9_])", lowered)
    ]
    forms = [m.group(0) for m in FORM_NUMBER.finditer(answer)] + [m.group(0) for m in SCHEDULE.finditer(answer)]
    leaks.extend({"kind": "form_number_in_text", "match": form} for form in forms)
    return leaks


def asked_keys(q: Question, truth: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> tuple[str, ...]:
    """The facts q asks this household about: its own, or for the change question each remembered change.

    Follow-ups such as a purchase date ride on their parent, so the change
    question asks only about top-level facts.
    """
    if not q.asks_changes:
        return q.asks
    return tuple(k for k in remembered_changes(truth) if k in specs and not specs[k].is_follow_up)


def allowed_keys(q: Question, truth: Mapping[str, Any]) -> set[str]:
    """Facts an answer to q may reveal: the ones it asks about, and for either change question what changed.

    The change question allows only the changes the client remembers, so a
    hidden fact it reveals is a leak; the open question allows any change.
    """
    allowed = set(q.asks)
    if q.asks_changes:
        allowed |= set(remembered_changes(truth))
    if q.open_to_changes:
        allowed |= set(truth.get("changed_facts") or [])
    return allowed


def off_topic_leak(
    key: str,
    value: Any,
    earlier: tuple[Any, str] | None,
    *,
    comparable: bool = True,
    recall_gap: str | None = None,
) -> dict[str, Any]:
    """Classifies a fact revealed outside the question by what earlier answers said about it.

    unasked_fact: no earlier answer revealed it, so the agent learns something
    it didn't ask for. changed_off_topic: an earlier answer gave another value,
    so this answer corrects (or contradicts) it on a question about something
    else; a recall gap breaking on its own topic looks the same, so the leak
    carries the gap for Roanuk to judge. restated_fact: the earlier value
    again, which tells the agent nothing new. An uncoercible value can't
    equal an earlier valid one, so it never counts as restated.
    """
    if earlier is None:
        return {"kind": "unasked_fact", "match": key}
    earlier_value, earlier_question = earlier
    detail = {"match": key, "earlier_question": earlier_question, "earlier_value": earlier_value}
    if comparable and _same(value, earlier_value):
        return {"kind": "restated_fact", **detail}
    return {"kind": "changed_off_topic", **detail, "value": value, "recall_gap": recall_gap}


def score(
    truths: Mapping[str, Mapping[str, Any]],
    records: Sequence[AnswerRecord],
    specs: Mapping[str, FactSpec],
    questions: Sequence[Question] = QUESTIONS,
    *,
    drawn: Iterable[str] | None = None,
) -> FidelityScore:
    """Compares every revealed value with the truth and flags the leaks code can see.

    A revealed fact counts as compared when its key is a known fact key; an
    uncoercible value counts as compared and not matched. For a client who
    doesn't volunteer, every fact revealed outside the question's own facts
    (plus, for the open question, the facts that changed this year) is a
    leak, of the kind off_topic_leak gives. The bar needs at least 98% of
    compared facts matching and every answer scored: an answer that failed
    twice or had no revealed object fails it.

    On the change question, which asks about no recall gap's topic, a scored
    answer that leaves out a remembered change adds a missed change, and one
    that reveals a hidden fact at its true value adds a broken gap.

    The facts that ended are counted on their own too, beside the same facts
    where they are true this year, and the match rate is also taken over the
    households drawn by complexity (`drawn`; every household when it is None).
    None of these change the bar, and neither do the facts the simulator's
    contradiction retry corrected or left wrong, which are collected from each
    scored answer for the report; one left wrong is a mismatch like any other.
    Like the match rate, the counts of the facts that ended read the answers
    kept, after the retry, which sends back every first reply that reveals one
    as true; ended_true_first counts those first replies from the retry's
    entries.
    """
    by_id = {q.id: q for q in questions}
    order = {q.id: i for i, q in enumerate(questions)}
    expected = {hid: truth_values(t, specs) for hid, t in truths.items()}
    ended = {hid: set(ended_facts(t, specs)) for hid, t in truths.items()}
    ended_keys = set().union(*ended.values())
    drawn_ids = set(truths) if drawn is None else set(drawn) & set(truths)
    ordered = sorted(records, key=lambda r: (r.household_id, order.get(r.question_id, len(order))))
    # Each household's latest revealed value of each fact, and the question that revealed it.
    seen: dict[str, dict[str, tuple[Any, str]]] = {hid: {} for hid in truths}
    compared = matched = retries = asked_facts = asked_revealed = changes_asked = changes_named = 0
    ended_asked = ended_revealed = ended_compared = ended_matched = ended_true = ended_true_first = 0
    control_compared = control_matched = drawn_compared = drawn_matched = 0
    control_households: set[str] = set()
    ended_mismatches: list[dict[str, Any]] = []
    missed_changes: list[dict[str, Any]] = []
    broken_gaps: list[dict[str, Any]] = []
    corrected: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    leaks: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    unscored: list[dict[str, Any]] = []
    per_question = {q.id: {"compared": 0, "matched": 0} for q in questions}
    gap_compared = gap_matched = 0
    recall_gap = sorted(hid for hid, t in truths.items() if recall_gap_of(t))

    for rec in ordered:
        hid, q = rec.household_id, by_id[rec.question_id]
        truth = truths[hid]
        retries += int(rec.retried)
        where = {"household_id": hid, "question_id": q.id}
        asks = asked_keys(q, truth, specs)
        asked_facts += sum(1 for key in asks if expected[hid][key] is not None)
        ended_asks = [key for key in asks if key in ended[hid]]
        ended_asked += len(ended_asks)
        if rec.error is not None:
            errors.append({**where, "error": rec.error})
            unscored.append({**where, "reason": "failed twice"})
            continue
        corrected += [{**where, **c} for c in rec.corrected]
        unresolved += [{**where, **u} for u in rec.unresolved]
        # An entry with no first value was wrong only in the retry, so it is no misread by the first reply.
        ended_true_first += sum(1 for x in [*rec.corrected, *rec.unresolved]
                                if x.get("key") in ended[hid] and x.get("first") is True)
        unusable = [a["kind"] for a in rec.anomalies if a.get("kind") in UNSCORED_ANOMALIES]
        if unusable:
            unscored.append({**where, "reason": unusable[0]})
        volunteers = (truth.get("persona") or {}).get("volunteers_info") is True
        allowed = allowed_keys(q, truth)
        gap = recall_gap_of(truth)
        uncoercible = [a for a in rec.anomalies if a.get("kind") == "uncoercible"]
        for key, value in rec.revealed.items():
            compared += 1
            per_question[q.id]["compared"] += 1
            ok = _same(value, expected[hid][key])
            if ok:
                matched += 1
                per_question[q.id]["matched"] += 1
            else:
                mismatches.append({**where, "key": key, "revealed": value, "truth": expected[hid][key]})
            if hid not in recall_gap:
                gap_compared += 1
                gap_matched += int(ok)
            if hid in drawn_ids:
                drawn_compared += 1
                drawn_matched += int(ok)
            if key in ended[hid]:
                ended_compared += 1
                ended_matched += int(ok)
                ended_true += int(value is True)
                if not ok:
                    ended_mismatches.append({**where, "key": key, "revealed": value, "truth": expected[hid][key]})
            elif key in ended_keys and expected[hid][key]:
                control_compared += 1
                control_matched += int(ok)
                control_households.add(hid)
            if key not in allowed and not volunteers:
                leaks.append({**where, **off_topic_leak(key, value, seen[hid].get(key), recall_gap=gap)})
        for anomaly in rec.anomalies:
            anomalies.append({**where, **anomaly})
        for anomaly in uncoercible:
            key = anomaly.get("key")
            compared += 1
            per_question[q.id]["compared"] += 1
            if hid not in recall_gap:
                gap_compared += 1
            if hid in drawn_ids:
                drawn_compared += 1
            if key in ended[hid]:
                ended_compared += 1
                raw = anomaly.get("value")
                ended_mismatches.append({**where, "key": key, "revealed": raw, "truth": expected[hid][key]})
            elif key in ended_keys and expected[hid][key]:
                control_compared += 1
                control_households.add(hid)
            if key not in allowed and not volunteers:
                leak = off_topic_leak(key, anomaly.get("value"), seen[hid].get(key), comparable=False, recall_gap=gap)
                leaks.append({**where, **leak})
        leaks.extend({**where, **leak} for leak in text_leaks(rec.answer or "", specs))
        named = set(rec.revealed) | {a.get("key") for a in uncoercible}
        asked_revealed += sum(1 for key in asks if expected[hid][key] is not None and key in named)
        ended_revealed += sum(1 for key in ended_asks if key in named)
        if q.asks_changes:
            changes_asked += len(asks)
            changes_named += sum(1 for key in asks if key in named)
            missed_changes += [{**where, "key": key, "truth": expected[hid][key]} for key in asks if key not in named]
            broken_gaps += [
                {**where, "key": key, "value": rec.revealed[key], "recall_gap": gap}
                for key in hidden_facts(gap)
                if key in rec.revealed and _same(rec.revealed[key], expected[hid][key])
            ]
        for key, value in rec.revealed.items():
            seen[hid][key] = (value, q.id)

    for m in mismatches:
        fix = next(
            (
                r
                for r in ordered
                if r.household_id == m["household_id"]
                and order[r.question_id] > order[m["question_id"]]
                and m["key"] in r.revealed
                and _same(r.revealed[m["key"]], m["truth"])
            ),
            None,
        )
        m["corrected_later"] = fix is not None
        m["corrected_by"] = fix.question_id if fix else None
        m["corrected_on_topic"] = (
            m["key"] in allowed_keys(by_id[fix.question_id], truths[m["household_id"]]) if fix else None
        )
        m["recall_gap"] = recall_gap_of(truths[m["household_id"]])

    rate = matched / compared if compared else None
    return FidelityScore(
        households=sorted(truths),
        compared=compared,
        matched=matched,
        match_rate=rate,
        meets_bar=rate is not None and rate >= MATCH_RATE_BAR and not unscored,
        mismatches=mismatches,
        anomalies=anomalies,
        leaks=leaks,
        errors=errors,
        retries=retries,
        per_question=per_question,
        recall_gap_households=recall_gap,
        match_rate_without_recall_gaps=gap_matched / gap_compared if gap_compared else None,
        answers=len(ordered),
        unscored=unscored,
        asked_facts=asked_facts,
        asked_facts_revealed=asked_revealed,
        changes_asked=changes_asked,
        changes_named=changes_named,
        missed_changes=missed_changes,
        broken_gaps=broken_gaps,
        ended_households=sorted(hid for hid in truths if ended[hid]),
        ended_asked=ended_asked,
        ended_revealed=ended_revealed,
        ended_compared=ended_compared,
        ended_matched=ended_matched,
        ended_true=ended_true,
        ended_true_first=ended_true_first,
        ended_mismatches=ended_mismatches,
        control_households=sorted(control_households),
        control_compared=control_compared,
        control_matched=control_matched,
        drawn_households=sorted(drawn_ids),
        drawn_compared=drawn_compared,
        drawn_matched=drawn_matched,
        corrected=corrected,
        unresolved=unresolved,
    )


# ---- running -----------------------------------------------------------------


def estimate_cost_usd(
    truths: Iterable[Mapping[str, Any]],
    specs: Mapping[str, FactSpec],
    model: str,
    prices: Mapping[str, Any],
    questions: Sequence[Question] = QUESTIONS,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> float:
    """A deliberately high estimate: no cache discount, every reply at max_tokens, a retry margin.

    It is not a hard bound (an answer can take three calls: one retry for an
    unusable reply and one for a contradiction), so the live run wraps its
    client in a BudgetedClient that holds spend to this figure.
    """
    total = Usage()
    for truth in truths:
        system = len(build_system_prompt(truth, specs)) // CHARS_PER_TOKEN + 1
        history = 0
        for q in questions:
            asked = len(q.text) // CHARS_PER_TOKEN + 1
            total = total + Usage(input_tokens=system + history + asked, output_tokens=max_tokens)
            history += asked + ANSWER_TOKENS_IN_HISTORY
    return round(cost_usd(model, total.as_dict(), dict(prices)) * RETRY_MARGIN, 6)


async def ask_all(sim: ClientSimulator, questions: Sequence[Question]) -> list[AnswerRecord]:
    records = []
    for q in questions:
        try:
            result = await sim.answer(q.text)
        except SimulatorError as e:
            records.append(
                AnswerRecord(
                    sim.household_id, q.id, q.text, None, {}, retried=True, error=str(e), usage=e.usage.as_dict()
                )
            )
            continue
        records.append(
            AnswerRecord(
                household_id=sim.household_id,
                question_id=q.id,
                question=q.text,
                answer=result.answer,
                revealed=result.revealed,
                anomalies=result.anomalies,
                retried=result.retried,
                usage=result.usage.as_dict(),
                corrected=result.corrected,
                unresolved=result.unresolved,
            )
        )
    return records


async def run_households(
    truths: Mapping[str, Mapping[str, Any]],
    client_for: Callable[[Mapping[str, Any]], Any],
    simulators: list[ClientSimulator],
    *,
    specs: Mapping[str, FactSpec],
    questions: Sequence[Question] = QUESTIONS,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    concurrency: int = CONCURRENCY,
) -> list[AnswerRecord]:
    """One conversation per household, a few at a time.

    Each simulator is appended to `simulators` before its first call, so the
    caller can total the usage even if a call fails. A failure cancels the
    other conversations before it propagates.
    """
    gate = asyncio.Semaphore(concurrency)

    async def one(hid: str) -> list[AnswerRecord]:
        truth = truths[hid]
        sim = ClientSimulator(truth, client_for(truth), model=model, max_tokens=max_tokens, specs=specs)
        simulators.append(sim)
        async with gate:
            return await ask_all(sim, questions)

    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(one(hid)) for hid in sorted(truths)]
    except BaseExceptionGroup as failed:
        raise failed.exceptions[0]
    return [rec for task in tasks for rec in task.result()]


def live_client(api_key: str) -> Any:
    return messages_client(api_key)


def _total(simulators: Sequence[ClientSimulator]) -> tuple[Usage, int]:
    usage = Usage()
    for sim in simulators:
        usage = usage + sim.total_usage
    return usage, max((s.max_prompt_tokens for s in simulators), default=0)


async def run_check(
    *,
    data_dir: Path = DATA_DIR,
    out_dir: Path | None = None,
    dry_run: bool,
    households: int = HOUSEHOLDS,
    seed: int = SEED,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    concurrency: int = CONCURRENCY,
    settings_loader: Callable[[], HarnessSettings] = load_settings,
    client_factory: Callable[[str], Any] = live_client,
    ledger_path: Path = DEFAULT_LEDGER,
    prices_path: Path = DEFAULT_PRICES,
) -> CheckOutcome:
    specs = load_facts()
    splits = load_splits(data_dir)
    pool = load_truths(data_dir, pool_ids(splits))
    drawn = choose_households(splits, {h: t.get("complexity") for h, t in pool.items()}, n=households, seed=seed)
    chosen = sample_households(splits, pool, specs, n=households, seed=seed)
    truths = {hid: pool[hid] for hid in chosen}
    prices = load_prices(prices_path)
    estimate = estimate_cost_usd(truths.values(), specs, model, prices, max_tokens=max_tokens)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"fidelity-{'dry-' if dry_run else ''}{stamp}"
    if out_dir is None:
        out_dir = Path(tempfile.mkdtemp(prefix="fidelity-dry-run-")) if dry_run else REPO

    simulators: list[ClientSimulator] = []
    if dry_run:
        def client_for(truth: Mapping[str, Any]) -> Any:
            question_keys = {q.text: asked_keys(q, truth, specs) for q in QUESTIONS}
            return TruthEchoClient(truth["true_facts"], question_keys, specs)

        records = await run_households(
            truths, client_for, simulators, specs=specs, model=model, max_tokens=max_tokens, concurrency=concurrency
        )
    else:
        settings = settings_loader()
        ledger = Ledger(ledger_path, cap_usd=settings.spend_cap_usd, run_cap_usd=settings.run_cap_usd)
        ledger.reserve(run_id, model, estimate)
        guard: BudgetedClient | None = None
        try:
            guard = BudgetedClient(client_factory(settings.api_key), model=model, prices=prices, limit_usd=estimate)
            records = await run_households(
                truths, lambda _: guard, simulators, specs=specs, model=model, max_tokens=max_tokens,
                concurrency=concurrency,
            )
        finally:
            # Every call went through the guard, which also counts calls that raised at their bound.
            ledger.settle(run_id, guard.spent_usd if guard is not None else 0.0)

    usage, prompt_tokens = _total(simulators)
    cost = cost_usd(model, usage.as_dict(), prices, prompt_tokens=prompt_tokens)
    result = score(truths, records, specs, drawn=drawn)
    outcome = CheckOutcome(
        run_id=run_id,
        dry_run=dry_run,
        score=result,
        records=records,
        usage=usage,
        cost_usd=cost,
        estimate_usd=estimate,
        report_path=Path(out_dir) / REPORT_PATH,
        review_path=Path(out_dir) / REVIEW_PATH,
        answers_path=Path(out_dir) / ANSWERS_PATH,
    )
    write_outputs(outcome, truths, specs, model=model, seed=seed)
    return outcome


# ---- reports -----------------------------------------------------------------


def _clean(text: Any) -> str:
    """Model text for a repo file: the repo bans em and en dashes."""
    return str(text).translate(DASHES)


def _value(value: Any) -> str:
    return "null" if value is None else json.dumps(value)


def _pct(rate: float | None) -> str:
    return "n/a" if rate is None else f"{rate:.1%}"


def _cell(text: Any) -> str:
    """Text for one Markdown table cell: no long dashes, no line breaks, and pipes escaped."""
    return " ".join(_clean(text).splitlines()).replace("|", "\\|")


def _households(n: int) -> str:
    return f"{n} household" if n == 1 else f"{n} households"


def _usage_text(usage: Usage) -> str:
    u = usage.as_dict()
    return ", ".join(f"{name.removesuffix('_tokens').replace('_', ' ')} {u[name]:,}" for name in u)


def _kind_counts(leaks: Sequence[Mapping[str, Any]]) -> str:
    return ", ".join(f"{kind} {count}" for kind, count in sorted(Counter(x["kind"] for x in leaks).items()))


def _corrected(m: Mapping[str, Any]) -> str:
    if not m.get("corrected_by"):
        return "no"
    return f"{m['corrected_by']}" + ("" if m.get("corrected_on_topic") else ", off topic")


def _leak_detail(x: Mapping[str, Any]) -> str:
    if x["kind"] == "restated_fact":
        return f"same as {x['earlier_question']}"
    if x["kind"] == "changed_off_topic":
        gap = f"; recall gap: {x['recall_gap']}" if x.get("recall_gap") else "; no recall gap"
        return f"{_value(x['earlier_value'])} at {x['earlier_question']}, now {_value(x['value'])}{gap}"
    return ""


def _ended_bullet(s: FidelityScore) -> str:
    if not s.ended_households:
        return '- Facts that ended (the client\'s "Last year only" lines): none of these households has one.'
    # The misread, in bold whenever there is one, in the answers kept and in the first replies sent back: Block 4
    # waits until each is fixed or accepted.
    true = f"Revealed as true this year: {s.ended_true}."
    if s.ended_true:
        true = f"**{true}**"
    first = f"Revealed as true in a first reply the simulator sent back: {s.ended_true_first}."
    if s.ended_true_first:
        first = f"**{first}**"
    control = (
        f"in the {_households(len(s.control_households))} where they are true this year: {s.control_matched} of "
        f"{s.control_compared} match ({_pct(s.control_match_rate)})"
        if s.control_compared
        else "where they are true this year: none revealed"
    )
    return (
        "- Facts that ended, which the client's change list shows as \"Last year only\" lines, in "
        f"{_households(len(s.ended_households))}, read in the answers kept after the contradiction retry: revealed "
        f"values that match the truth: {s.ended_matched} of {s.ended_compared} ({_pct(s.ended_match_rate)}). {true} "
        f"{first} Other values that differ (null, or not a yes or no): "
        f"{s.ended_compared - s.ended_matched - s.ended_true}. Asked about and revealed: {s.ended_revealed} of "
        f"{s.ended_asked}. Control, the same facts {control}. These values count in the match rate too, but at "
        f"{s.ended_compared} of {s.compared} compared the bar can't see a misread on its own, so read the Facts "
        "that ended table; a misread the simulator sent back shows only in the Contradictions sent back table."
    )


def _ended_rows(
    records: Sequence[AnswerRecord], truths: Mapping[str, Mapping[str, Any]], specs: Mapping[str, FactSpec]
) -> list[str]:
    """A table row for each answer that asks about or reveals one of its household's facts that ended.

    The Revealed cell has one part per such fact, joined by "; ": "key value"
    when it matches the truth, "key value, truth X" when it doesn't (an
    uncoercible value shows as given), or "key not revealed".
    """
    by_id = {q.id: q for q in QUESTIONS}
    order = {q.id: i for i, q in enumerate(QUESTIONS)}
    ended = {hid: ended_facts(t, specs) for hid, t in truths.items()}
    values = {hid: truth_values(t, specs) for hid, t in truths.items() if ended[hid]}
    rows = []
    for rec in sorted(records, key=lambda r: (r.household_id, order.get(r.question_id, len(order)))):
        hid = rec.household_id
        if not ended.get(hid):
            continue
        asks = asked_keys(by_id[rec.question_id], truths[hid], specs)
        raw = {a.get("key"): a.get("value") for a in rec.anomalies if a.get("kind") == "uncoercible"}
        keys = [k for k in ended[hid] if k in asks or k in rec.revealed or k in raw]
        if not keys:
            continue
        parts = []
        for key in keys:
            if key in rec.revealed and _same(rec.revealed[key], values[hid][key]):
                parts.append(f"{key} {_value(rec.revealed[key])}")
            elif key in rec.revealed or key in raw:
                shown = rec.revealed[key] if key in rec.revealed else raw[key]
                parts.append(f"{key} {_value(shown)}, truth {_value(values[hid][key])}")
            else:
                parts.append(f"{key} not revealed")
        revealed = "failed twice" if rec.error is not None else "; ".join(parts)
        rows.append(
            f"| {hid} | {rec.question_id} | {', '.join(keys)} | {_cell(revealed)} | {_cell(rec.answer or '')} |"
        )
    return rows


def _contradiction_bullet(s: FidelityScore) -> str:
    rate = s.contradicted_answers / s.answers if s.answers else None
    return (
        "- Answers the simulator sent back once, because they contradicted a fact outside the recall gap: "
        f"{s.contradicted_answers} of {s.answers} ({_pct(rate)}). Facts the retry corrected: {len(s.corrected)}. "
        f"Facts the answers kept still get wrong, counted as mismatches: {len(s.unresolved)}. Answers whose retry "
        f"changed a fact the recall gap hides, so the first answer was kept: {s.gap_kept_answers}. The match rate "
        "is on the answers kept, so this is the simulator's raw error; the Contradictions sent back table lists "
        "each fact."
    )


def _contradiction_rows(
    s: FidelityScore, truths: Mapping[str, Mapping[str, Any]], specs: Mapping[str, FactSpec]
) -> list[str]:
    """A table row for each fact the contradiction retry corrected or left wrong, in household and question order."""
    order = {q.id: i for i, q in enumerate(QUESTIONS)}
    entries = sorted(
        [*((x, "corrected") for x in s.corrected), *((x, "unresolved") for x in s.unresolved)],
        key=lambda e: (e[0]["household_id"], order.get(e[0]["question_id"], len(order))),
    )
    values = {hid: truth_values(truths[hid], specs) for hid in {x["household_id"] for x, _ in entries}}
    rows = []
    for x, outcome in entries:
        first = _value(x["first"]) if "first" in x else "not revealed"
        final = _value(x["final"]) if "final" in x else "not revealed"
        if x.get("retry_error"):
            final += f" (the retry's reply was unusable, so the first answer was kept: {x['retry_error']})"
        if x.get(RETRY_CHANGED_GAP):
            final += (f" (the retry changed {', '.join(x[RETRY_CHANGED_GAP])}, which the recall gap hides, so the "
                      "first answer was kept)")
            outcome = GAP_KEPT
        rows.append(
            f"| {x['household_id']} | {x['question_id']} | {x['key']} | {first} | {_cell(final)} | "
            f"{_value(values[x['household_id']][x['key']])} | {outcome} |"
        )
    return rows


def _banner(dry_run: bool) -> list[str]:
    if not dry_run:
        return []
    return [
        "**DRY RUN.** A fake client answered from the truth, so these numbers prove the pipeline, "
        "not the simulator. No model was called and nothing was spent.",
        "",
    ]


def render_report(
    outcome: CheckOutcome,
    truths: Mapping[str, Mapping[str, Any]],
    specs: Mapping[str, FactSpec],
    *,
    model: str,
) -> str:
    s = outcome.score
    covered = {k for q in QUESTIONS for k in q.asks}
    uncovered = [k for k in specs if k not in covered]
    tiers = Counter(str(t.get("complexity", "unknown")) for t in truths.values())
    added = [h for h in s.households if h not in s.drawn_households]
    lines = [
        "# Simulator fidelity check" + (" (dry run)" if outcome.dry_run else ""),
        "",
        *_banner(outcome.dry_run),
        f"Run `{outcome.run_id}`, model `{model if not outcome.dry_run else 'fake: TruthEchoClient'}`. "
        f"{len(s.households)} train and dev households, {len(QUESTIONS)} scripted questions each. "
        "All households are synthetic. Not tax advice.",
        "",
        "## Result",
        "",
        f"- Revealed facts compared with the truth: {s.compared}",
        f"- Matching the truth: {s.matched} ({_pct(s.match_rate)})",
        f"- Matching the truth in the {_households(len(s.drawn_households))} drawn by complexity (the PRD 6.4 "
        f"sample): {s.drawn_matched} of {s.drawn_compared} ({_pct(s.drawn_match_rate)})",
        f"- Answers scored: {s.answers - len(s.unscored)} of {s.answers}. An answer that failed twice or "
        "had no revealed object can't be scored.",
        f"- Bar: at least {MATCH_RATE_BAR:.0%} of compared facts match, and every answer is scored. "
        f"**{'Met' if s.meets_bar else 'Not met'}.**",
        f"- Match rate without the {len(s.recall_gap_households)} "
        + ("household that has" if len(s.recall_gap_households) == 1 else "households that have")
        + f" a recall gap: {_pct(s.match_rate_without_recall_gaps)}",
        f"- Asked facts (with a non-null truth) that the answers revealed: {s.asked_facts_revealed} of "
        f"{s.asked_facts} ({_pct(s.coverage)}). Shown for judgment, not part of the bar: a recall gap or a "
        "low-jargon client can leave an asked fact out on purpose.",
        f"- {CHANGE_QUESTION.id}, what changed since last year: remembered changes the answers named: "
        f"{s.changes_named} of {s.changes_asked} ({_pct(s.change_coverage)}). Recall gaps that broke on it: "
        f"{len(s.broken_gaps)}. Shown for judgment, not part of the bar.",
        _ended_bullet(s),
        f"- Mismatches: {len(s.mismatches)}. Anomalies: {len(s.anomalies)}. Leaks code caught: {len(s.leaks)}"
        + (f" ({_kind_counts(s.leaks)})" if s.leaks else "")
        + ".",
        f"- Answers that needed a retry for an unusable reply: {s.retries}. "
        f"Answers that failed twice: {len(s.errors)}.",
        _contradiction_bullet(s),
        f"- Tokens: {_usage_text(outcome.usage)}. Cost at list price: ${outcome.cost_usd:.4f} "
        + (
            f"(fake tokens; a live run would reserve ${outcome.estimate_usd:.4f} first)."
            if outcome.dry_run
            else f"(reserved before the first call: ${outcome.estimate_usd:.4f})."
        ),
        "",
        "A recall gap can make the client give a wrong value on purpose, then correct it when asked about "
        "the right topic; those mismatches show the gap and which later answer, if any, corrected them. "
        "A correction on a question that doesn't ask about the fact is also listed under leaks as "
        "`changed_off_topic`.",
        "",
        "## Households",
        "",
        (
            f"Chosen deterministically from train and dev: {len(s.drawn_households)} drawn, stratified by "
            "complexity, plus every household whose client prompt has a \"Last year only\" line "
            f"({', '.join(s.ended_households)}), which adds {len(added)}. By complexity: "
            if s.ended_households
            else "Chosen deterministically from train and dev, stratified by complexity: "
        )
        + ", ".join(f"{tier} {count}" for tier, count in sorted(tiers.items()))
        + ".",
        "",
        ", ".join(s.households),
        "",
        "## Questions",
        "",
        "| ID | Topic | Compared | Matched | Facts asked |",
        "|---|---|---|---|---|",
    ]
    for q in QUESTIONS:
        pq = s.per_question[q.id]
        asks = "each change the client remembers" if q.asks_changes else ", ".join(q.asks)
        lines.append(f"| {q.id} | {q.topic} | {pq['compared']} | {pq['matched']} | {asks} |")
    lines += ["", f"Fact keys no question asks about: {', '.join(uncovered) or 'none'}.", ""]
    for q in QUESTIONS:
        lines.append(f"- **{q.id}:** {q.text}")
    lines += [
        "",
        "## What changed since last year",
        "",
        f"{CHANGE_QUESTION.id} asks what changed before any topic comes up, as the agent does. It asks about no "
        "recall gap's topic, so the client should name every change it remembers and none its recall gap hides. "
        "A change left out is how a false \"nothing changed\" shows; a gap that broke means the client handed "
        "over the fact the agent is meant to probe for.",
        "",
        "Remembered changes the answer left out:",
        "",
    ]
    if s.missed_changes:
        lines += ["| Household | Fact | Truth |", "|---|---|---|"]
        lines += [f"| {m['household_id']} | {m['key']} | {_value(m['truth'])} |" for m in s.missed_changes]
    else:
        lines.append("None.")
    lines += ["", "Recall gaps that broke (the hidden fact revealed at its true value):", ""]
    if s.broken_gaps:
        lines += ["| Household | Fact | Revealed | Recall gap |", "|---|---|---|---|"]
        lines += [
            f"| {b['household_id']} | {b['key']} | {_value(b['value'])} | {_clean(b['recall_gap'])} |"
            for b in s.broken_gaps
        ]
    else:
        lines.append("None.")
    lines += ["", "## Facts that ended", ""]
    if s.ended_households:
        rows = _ended_rows(outcome.records, truths, specs)
        lines += [
            "Each row is an answer that asks about or reveals a fact its household's change list shows as "
            "\"Last year only\": true last year, false this year. A client that reads such a line as this year's "
            "reveals the fact as true, and in an agent run on that household the misread counts against the agent, "
            "as a request for a document the client won't get. Only the words show whether an answer is about last "
            "year, so read every row. Each row is the answer kept, after the contradiction retry: a first reply "
            "that revealed such a fact as true was sent back, and shows in the Contradictions sent back table.",
            "",
        ]
        if rows:
            lines += ["| Household | Question | Ended facts | Revealed | Answer |", "|---|---|---|---|---|", *rows]
        else:
            lines.append("No answer asks about or reveals one.")
    else:
        lines.append('None of these households has a "Last year only" line.')
    lines += [
        "",
        "## Contradictions sent back",
        "",
        "When an answer contradicts a fact outside the recall gap, the simulator tells its model, in its own "
        "conversation, which facts it got wrong, and asks the same question again, once. The answer it gives is "
        "kept even when it still contradicts, unless it reveals a fact the recall gap hides differently from the "
        "first answer, which would break the gap or undo it; then the first answer is kept. An agent would see "
        "only the answer kept. Each row is a fact the first answer contradicted or the answer kept still gets "
        "wrong; \"not revealed\" means that answer left the fact out. A corrected fact is not a mismatch, and an "
        f"unresolved one is, as is one marked \"{GAP_KEPT}\".",
        "",
    ]
    rows = _contradiction_rows(s, truths, specs)
    if rows:
        lines += [
            "| Household | Question | Fact | First answer | Answer kept | Truth | Outcome |",
            "|---|---|---|---|---|---|---|",
            *rows,
        ]
    else:
        lines.append("None.")
    lines += ["", "## Mismatches", ""]
    if s.mismatches:
        lines += [
            "| Household | Question | Fact | Revealed | Truth | Corrected later | Recall gap |",
            "|---|---|---|---|---|---|---|",
        ]
        for m in s.mismatches:
            lines.append(
                f"| {m['household_id']} | {m['question_id']} | {m['key']} | {_value(m['revealed'])} | "
                f"{_value(m['truth'])} | {_corrected(m)} | {_clean(m['recall_gap'] or '')} |"
            )
    else:
        lines.append("None.")
    lines += [
        "",
        "## Unscored answers",
        "",
        "These fail the bar whatever the match rate, since nothing in them could be compared.",
        "",
    ]
    if s.unscored:
        lines += [f"- {u['household_id']} {u['question_id']}: {u['reason']}" for u in s.unscored]
    else:
        lines.append("None.")
    lines += [
        "",
        "## Anomalies",
        "",
        "Revealed entries with an unknown key or a value that doesn't fit the fact's type.",
        "",
    ]
    if s.anomalies:
        lines += ["| Household | Question | Kind | Fact | Value |", "|---|---|---|---|---|"]
        for a in s.anomalies:
            lines.append(
                f"| {a['household_id']} | {a['question_id']} | {a['kind']} | {a.get('key', '')} | "
                f"{_clean(json.dumps(a.get('value')))} |"
            )
    else:
        lines.append("None.")
    lines += [
        "",
        "## Leaks code caught",
        "",
        "The first three kinds are facts revealed outside the question, by a client whose persona doesn't "
        "volunteer. `unasked_fact`: no earlier answer revealed it, so the agent learns something it didn't ask "
        "for. `changed_off_topic`: an earlier answer gave another value; a recall gap breaking on its own topic "
        "looks like this and is fine, while one breaking on any other topic is a leak, so check the gap. "
        "`restated_fact`: the earlier value again, which tells the agent nothing new; the mildest kind. "
        "`fact_key_in_text` and `form_number_in_text`: words in the answer the agent would see.",
        "",
    ]
    if s.leaks:
        lines += ["| Household | Question | Kind | What | Detail |", "|---|---|---|---|---|"]
        lines += [
            f"| {x['household_id']} | {x['question_id']} | {x['kind']} | {_clean(x['match'])} | "
            f"{_clean(_leak_detail(x))} |"
            for x in s.leaks
        ]
    else:
        lines.append("None.")
    lines += ["", "## Simulator errors", ""]
    if s.errors:
        lines += [f"- {e['household_id']} {e['question_id']}: {_clean(e['error'])}" for e in s.errors]
    else:
        lines.append("None.")
    lines += [
        "",
        "## Human review",
        "",
        f"Roanuk reads {REVIEW_ANSWERS} answers in `{REVIEW_PATH}` for leaks the revealed JSON doesn't show. "
        "Every leak he finds gets fixed before Block 5."
        + (
            " He also reads the Facts that ended table, and the first replies in the Contradictions sent back "
            "table, and Block 4 is done only when every fact that ended and was revealed as true this year, in a "
            "first reply or in the answer kept, is fixed or accepted."
            if s.ended_households
            else ""
        ),
        "",
    ]
    return "\n".join(lines)


def render_review(
    sample: Sequence[AnswerRecord],
    truths: Mapping[str, Mapping[str, Any]],
    specs: Mapping[str, FactSpec],
    *,
    dry_run: bool,
    leaks: Sequence[Mapping[str, Any]] = (),
) -> str:
    """The review sample, each answer with its question, facts, and the leaks code flagged on it."""
    by_id = {q.id: q for q in QUESTIONS}
    lines = [
        "# Simulator answers for review" + (" (dry run)" if dry_run else ""),
        "",
        *_banner(dry_run),
        f"{len(sample)} answers from the Block 4 fidelity check, at least {len(sample) // len(QUESTIONS)} per "
        "question, chosen by a seeded hash so the sample is the same on every run.",
        "",
        "Read each answer for leaks the revealed JSON doesn't show: a fact the question didn't ask about, "
        "a form number or tax term this client wouldn't use, or anything that breaks character. Then say in chat "
        "which numbers leak and why. Em and en dashes in model text are shown as hyphens, per the repo rules.",
        "",
    ]
    for n, rec in enumerate(sample, 1):
        q = by_id[rec.question_id]
        truth = truths[rec.household_id]
        values = truth_values(truth, specs)
        persona = truth.get("persona") or {}
        traits = ", ".join(f"{t} {_value(persona.get(t))}" for t in PERSONA_TRAITS)
        asks = asked_keys(q, truth, specs)
        asked = ", ".join(f"{k} {_value(values[k])}" for k in asks)
        other = ", ".join(
            f"{k} {_value(v)}"
            for k, v in values.items()
            if k not in asks and v is not None and v != default_value(specs[k])
        )
        flags = [
            f"{x['kind']} {x['match']}"
            for x in leaks
            if x["household_id"] == rec.household_id and x["question_id"] == rec.question_id
        ]
        lines += [
            f"## {n}. {rec.household_id}, {q.id} ({q.topic})",
            "",
            f"Persona: {_clean(traits)}",
            "",
            f"**Question:** {q.text}",
            "",
            *[f"> {_clean(line)}" for line in (rec.answer or "").splitlines() or [""]],
            "",
            f"Revealed: `{_clean(json.dumps(rec.revealed))}`",
            "",
            f"Facts this question asks about: {asked or 'none'}",
            "",
            f"Other facts with a value (mentioning one is a leak unless the client volunteers): {other or 'none'}",
            "",
            f"Code flags: {_clean(', '.join(flags)) or 'none'}",
            "",
        ]
    return "\n".join(lines)


def write_outputs(
    outcome: CheckOutcome,
    truths: Mapping[str, Mapping[str, Any]],
    specs: Mapping[str, FactSpec],
    *,
    model: str,
    seed: int = SEED,
) -> None:
    sample = choose_answers_for_review(outcome.records, seed=seed)
    for path in (outcome.report_path, outcome.review_path, outcome.answers_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    outcome.report_path.write_text(render_report(outcome, truths, specs, model=model))
    outcome.review_path.write_text(
        render_review(sample, truths, specs, dry_run=outcome.dry_run, leaks=outcome.score.leaks)
    )
    payload = {
        "run_id": outcome.run_id,
        "dry_run": outcome.dry_run,
        "model": None if outcome.dry_run else model,
        "usage": outcome.usage.as_dict(),
        "cost_usd": outcome.cost_usd,
        "records": [asdict(r) for r in outcome.records],
    }
    outcome.answers_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def positive_int(text: str) -> int:
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, not {n}")
    return n


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument(
        "--out-dir", type=Path, default=None, help="default: the repo, or a new temp folder for --dry-run"
    )
    parser.add_argument(
        "--households", type=positive_int, default=HOUSEHOLDS,
        help="how many to draw by complexity; every train and dev household with a \"Last year only\" line is added",
    )
    args = parser.parse_args(argv)
    try:
        outcome = asyncio.run(
            run_check(data_dir=args.data_dir, out_dir=args.out_dir, dry_run=args.dry_run, households=args.households)
        )
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except SPEND_LIMITS as e:
        print(f"stopped by a spend limit: {e}", file=sys.stderr)
        return 3
    s = outcome.score
    print(f"run_id: {outcome.run_id}")
    print(f"dry_run: {outcome.dry_run}")
    print(f"households: {len(s.households)}")
    print(
        f"match_rate: {_pct(s.match_rate)} ({s.matched}/{s.compared}), "
        f"answers scored: {s.answers - len(s.unscored)}/{s.answers}, bar met: {s.meets_bar}"
    )
    print(
        f"match_rate on the {_households(len(s.drawn_households))} drawn by complexity: "
        f"{_pct(s.drawn_match_rate)} ({s.drawn_matched}/{s.drawn_compared})"
    )
    print(
        f"mismatches: {len(s.mismatches)}, anomalies: {len(s.anomalies)}, "
        f"leaks: {len(s.leaks)}, errors: {len(s.errors)}"
    )
    print(
        f"contradictions sent back: {s.contradicted_answers}/{s.answers} answers, "
        f"facts corrected: {len(s.corrected)}, unresolved: {len(s.unresolved)}, "
        f"first answers kept for the recall gap: {s.gap_kept_answers}"
    )
    print(
        f"what changed ({CHANGE_QUESTION.id}): {s.changes_named}/{s.changes_asked} remembered changes named, "
        f"recall gaps broken: {len(s.broken_gaps)}"
    )
    if s.ended_households:
        print(
            f"facts that ended (the bar can't see a misread on its own): {s.ended_true} revealed true this year, "
            f"{s.ended_true_first} in first replies sent back, {s.ended_matched}/{s.ended_compared} revealed values "
            f"match, {s.ended_revealed}/{s.ended_asked} asked and revealed, in {_households(len(s.ended_households))}"
        )
        print(
            f"control, the same facts where true this year: {s.control_matched}/{s.control_compared} revealed "
            f"values match, in {_households(len(s.control_households))}"
        )
    else:
        print('facts that ended: none of these households has a "Last year only" line')
    print(f"cost_usd: {outcome.cost_usd:.4f} (estimate {outcome.estimate_usd:.4f})")
    print(f"report: {outcome.report_path}")
    print(f"review: {outcome.review_path}")
    print(f"answers: {outcome.answers_path}")
    return 0 if s.meets_bar else 1


if __name__ == "__main__":
    raise SystemExit(main())
