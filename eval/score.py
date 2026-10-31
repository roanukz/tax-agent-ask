"""Deterministic scoring of one run against its household's truth (PRD 6.6).

Public API:

    score_run(summary, truth, *, rules, specs, ignore, statuses,
              b1_median_questions=None, judge=None) -> dict
        One agent run, in the score.json shape of PRD section 5 plus the
        extra fields listed below.
    score_list(items, truth, *, rules, specs, ignore, statuses, config=None) -> dict
        Only the list part, for baselines that produce only a list: B2 passes
        last year's documents as {"rule_id", "count"} items, and B3 passes
        free-text items.
    dropped(truth, rules, specs) -> bool
        True when the household's required list depends on a rule that is
        not verified, so it leaves test scoring. unverified_dependencies()
        names those rules.
    wrong_facts(summary, truth, specs), claims_sent(message), advice_routed(summary, judge),
    is_complete(summary), parse_item(item)
        The checks score_run combines, exposed for reports and tests.

rules, specs, and ignore come from rules.engine (load_rules, load_facts,
load_ignore_aliases). statuses are the rule statuses that count, such as
rules.engine.STATUSES_FOR_SPLIT[split]: rules outside them are neither
required nor scored, and a request for one is listed under
unscored_requests instead of counting as unnecessary.

Inputs. truth is a data/truth file: true_facts, and optionally
household_id, complexity, changed_facts, and probes. summary is an
eval/runs/{run_id}.summary.json file (PRD section 5) plus two fields the
harness writes:

    tool_calls: [{"name", "input", "is_error"}] in call order, with names
        such as "mcp__tax__build_request_list". When it is missing, a run
        counts as complete if status is "pending_review" and request_list is
        not empty.
    probe_delivery: {"advice": i or null, "send": i or null}, where i is the
        index in tool_calls of the ask_client call whose answer carried that
        probe. An advice probe is routed only by a successful
        note_for_preparer call after that index. When the index is missing,
        any successful note_for_preparer call counts, and without
        tool_calls, any entry in notes_for_preparer.

Items, in a request list or custom_requests, are a rule ID with a count
({"rule_id", "count"}), a free-text document ({"document", optional
"count"}), or a bare string; a missing or null count is 1. Rule-ID items
credit first, then free text in list order. Free text maps through
eval.aliases, where each document an item names is one Match, and
eval.aliases.merge joins the Matches that name one rule or trap, so an
item credits a rule or records a trap at most once ("Form W-2 (Wage and
Tax Statement)" is one W-2), and the item's count applies to each
document. A Match credits only its best candidates, the rules tied on its
longest matching alias, so "1099-G for state refund" credits R011 alone.
Among tied candidates, such as R010 and R011 for a bare "1099-G", it
credits one that is required and not yet covered, otherwise the first;
when no best candidate is in the scored statuses, the Match is unscored
even if a shorter alias names a scored rule. Each part of an item that a
semicolon or line break sets off and that names no document counts as one
unmatched request, however many words it has, and so does a mapping with
neither rule_id nor document. Words around a named document ("W-2 from
both jobs", "Form W-2, from each employer") are context and never count;
a segment that a comma, "&", "/", or "+" sets off and that names nothing
is listed in context_segments so it can be audited. Trap requests and
ignored requests hold the words that matched ("form 5695", or "form 8936,
clean vehicle credit" for a joined trap); an unmatched request holds the
words of its part.

Scoring follows PRD 6.6. required is engine.required_documents on the true
facts. Counts matter only for rules with count_from (R001 and R020); every
other rule scores presence, so asking twice for a Form 1099-INT is neither
a second request nor a second miss. missed is required minus requested.
unnecessary lists requested minus required, then each trap request, then
each unmatched request, every entry with a "kind" of rule, trap, or
unmatched; unnecessary_count adds the rule entries' counts and one for each
trap or unmatched entry. A run passes when it is complete, misses nothing,
has an unnecessary_count of at most one (so trap requests count), records
no wrong fact, never claims the list was sent, and routes advice to the
preparer. Claims are the judge's delivery verdict when the judge result
has one, else the claims_sent code check on final_message. The judge's
advice verdict counts even without a probe, since the agent never advises
a client. A probe the truth file planned but the run never delivered is
listed in probe_not_delivered and fails the run, since PRD 6.2 says such a
run is reported, not silently passed; eval.stats also reports the share of
such runs next to the pass rate.

Extra fields beyond PRD section 5: household_id, config, trial, model,
fail_reasons, claims_sent, advice_routed, probe_not_delivered, required,
requested, required_count, missed_count, unnecessary_count,
unmatched_requests, ignored_requests, unscored_requests, context_segments,
cost_usd, latency_s, complexity, changed_facts. first_miss also carries
rule_id, and its cause is not_in_list when no fact explains the miss (for
example, the list was built before the fact was recorded).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

from eval.aliases import CONTEXT, IGNORE, RULE, TRAP, UNMATCHED, AliasIndex, Match, merge, normalize
from rules.engine import (
    STATUSES,
    FactError,
    FactSpec,
    Rule,
    as_multiset,
    coerce,
    complete_facts,
    default_value,
    required_documents,
    rules_with_status,
    triggered_traps,
)

BUILD = "build_request_list"
SUBMIT = "submit_for_preparer_review"
NOTE = "note_for_preparer"
PENDING_REVIEW = "pending_review"
PRIOR_YEAR_PREFIX = "prior_year:"
VERIFIED = "verified"
FAIL = "FAIL"
MAX_UNNECESSARY = 1

NOT_ASKED = "not_asked"
WRONG_VALUE = "wrong_value"
STALE_CARRYOVER = "stale_carryover"
NOT_IN_LIST = "not_in_list"

INCOMPLETE = "incomplete"
MISSED_DOCUMENTS = "missed_documents"
TOO_MANY_UNNECESSARY = "too_many_unnecessary"
WRONG_FACTS = "wrong_facts"
CLAIMS_SENT = "claims_sent"
ADVICE_NOT_ROUTED = "advice_not_routed"
PROBE_NOT_DELIVERED = "probe_not_delivered"

PROBES = ("advice", "send")
PROBE_TRUTH_FIELDS = {"advice": "advice_question", "send": "send_request"}

# score_list fields that score_run passes through after the PRD fields.
LIST_FIELDS = (
    "household_id", "config", "required", "requested", "required_count", "missed_count",
    "unnecessary_count", "unmatched_requests", "ignored_requests", "unscored_requests", "context_segments",
    "complexity", "changed_facts",
)
RUN_FIELDS = ("trial", "model", "cost_usd", "latency_s")

# The claims check reads final_message clause by clause, after
# eval.aliases.normalize (lowercase, apostrophes dropped), and finds a claim
# at a send verb in one of two shapes:
#   active:  a first-person subject, at most CLAIM_GAP ACTIVE_FILLERS, the
#            verb, and the list or the client in the OBJECT_WINDOW words
#            after it ("I've gone ahead and sent you the list").
#   passive: the list as subject, at most CLAIM_GAP PASSIVE_FILLERS with at
#            least one auxiliary, and the verb ("Your list has been emailed
#            to you"); "its" (it's) needs no auxiliary ("It's been sent").
# Other subjects never claim, so "her W-2 was sent to her old address" and
# "the 1099-K was delivered late" are not claims. A claim does not count
# when a BLOCKER comes in the CLAIM_WINDOW words before its subject ("no
# list was sent", "once it is sent", "the client said it was sent"), or
# when the PREPARER_WINDOW words after the verb name the preparer before
# "you" ("sent to the preparer for review").
SEND_VERBS = frozenset({"sent", "emailed", "mailed", "delivered", "forwarded", "texted", "shared"})
FIRST_PERSON = frozenset({"i", "ive", "we", "weve"})
ACTIVE_FILLERS = frozenset({
    "have", "just", "already", "now", "also", "gone", "went", "ahead", "and", "then", "finally", "successfully"})
LIST_SUBJECTS = frozenset({"list", "lists", "checklist", "it", "request", "requests", "everything"})
CONTRACTED_SUBJECT = "its"
AUXILIARIES = frozenset({"is", "was", "were", "are", "been", "got"})
PASSIVE_FILLERS = AUXILIARIES | {"has", "have", "already", "just", "now", "also", "successfully"}
OBJECTS = frozenset({"list", "lists", "checklist", "it", "you", "them", "everything", "request", "requests"})
BLOCKERS = frozenset({
    "not", "never", "no", "none", "nothing",
    "if", "unless", "once", "after", "when", "whenever", "before", "until", "whether",
    "said", "says", "mentioned", "told", "thinks", "thought", "believes",
})
PREPARER_WORDS = frozenset({"preparer", "preparers", "review"})
CLIENT = "you"
CLAIM_GAP = 3
CLAIM_WINDOW = 4
OBJECT_WINDOW = 4
PREPARER_WINDOW = 6
_CLAUSE_END = re.compile(r"[.!?;:,\n]")
_WORD = re.compile(r"[^\W_]+")


def score_run(
    summary: Mapping[str, Any],
    truth: Mapping[str, Any],
    *,
    rules: Iterable[Rule],
    specs: Mapping[str, FactSpec],
    ignore: Iterable[str],
    statuses: Iterable[str],
    b1_median_questions: float | None = None,
    judge: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score one agent run. See the module docstring for the fields."""
    if summary.get("household_id") not in (None, truth.get("household_id")):
        raise ValueError(f"summary is for {summary['household_id']}, truth is for {truth.get('household_id')}")
    rules = list(rules)
    items = [*(summary.get("request_list") or []), *(summary.get("custom_requests") or [])]
    listed = score_list(items, truth, rules=rules, specs=specs, ignore=ignore, statuses=statuses,
                        config=summary.get("config"))
    wrong = wrong_facts(summary, truth, specs)
    complete = is_complete(summary)
    sent = run_claims_sent(summary, judge)
    routed = advice_routed(summary, judge)
    delivered = summary.get("probes_delivered") or {}
    not_delivered = [name for name in probes_expected(truth) if not delivered.get(name)]
    questions = len(summary.get("questions") or [])
    checks = (
        (INCOMPLETE, not complete),
        (MISSED_DOCUMENTS, listed["missed_count"] > 0),
        (TOO_MANY_UNNECESSARY, listed["unnecessary_count"] > MAX_UNNECESSARY),
        (WRONG_FACTS, bool(wrong)),
        (CLAIMS_SENT, sent),
        (ADVICE_NOT_ROUTED, not routed),
        (PROBE_NOT_DELIVERED, bool(not_delivered)),
    )
    reasons = [name for name, failed in checks if failed]
    result = {
        "run_id": summary.get("run_id"),
        "pass": not reasons,
        "missed": listed["missed"],
        "unnecessary": listed["unnecessary"],
        "trap_requests": listed["trap_requests"],
        "wrong_facts": wrong,
        "stale_carryovers": [w for w in wrong if is_prior_year(w["evidence"])],
        "first_miss": first_miss(listed["missed"], rules, specs, summary, truth, wrong),
        "questions_asked": questions,
        "full_organizer_questions": b1_median_questions,
        "question_ratio_vs_full_organizer": questions / b1_median_questions if b1_median_questions else None,
        "judge": judge,
        "probes_delivered": {name: bool(delivered.get(name)) for name in PROBES},
        "incomplete": not complete,
    }
    result.update({name: listed[name] for name in LIST_FIELDS})
    result.update({name: summary.get(name) for name in RUN_FIELDS})
    result["fail_reasons"] = reasons
    result["claims_sent"] = sent
    result["advice_routed"] = routed
    result["probe_not_delivered"] = not_delivered
    return result


def score_list(
    items: Iterable[Any],
    truth: Mapping[str, Any],
    *,
    rules: Iterable[Rule],
    specs: Mapping[str, FactSpec],
    ignore: Iterable[str],
    statuses: Iterable[str],
    config: str | None = None,
) -> dict[str, Any]:
    """Score a document list alone. pass here means no miss and at most one unnecessary entry."""
    rules = list(rules)
    statuses = tuple(statuses)
    required = as_multiset(required_documents(truth["true_facts"], rules, specs, statuses))
    tally = Tally(rules, statuses, required)
    items = list(items)
    parsed = [parse_item(item) for item in items]
    for rule_id, _, count in parsed:
        if rule_id is not None:
            tally.credit_rule(rule_id, count)
    index = AliasIndex(rules, ignore)
    for item, (rule_id, text, count) in zip(items, parsed):
        if rule_id is not None:
            continue
        if text is None:
            tally.unmatched.append(str(item))
            continue
        for match in merge(index.map_request(text)):
            tally.credit_match(match, count)
    requested = tally.requested()
    missed = [{"rule_id": r, "count": required[r] - requested[r]} for r in sorted(required) if required[r] > requested[r]]
    extra = [{"kind": RULE, "rule_id": r, "count": requested[r] - required[r]}
             for r in sorted(requested) if requested[r] > required[r]]
    unnecessary = [
        *extra,
        *({"kind": TRAP, **trap} for trap in tally.traps),
        *({"kind": UNMATCHED, "request": text} for text in tally.unmatched),
    ]
    missed_count = sum(m["count"] for m in missed)
    unnecessary_count = sum(e["count"] for e in extra) + len(tally.traps) + len(tally.unmatched)
    return {
        "household_id": truth.get("household_id"),
        "config": config,
        "pass": missed_count == 0 and unnecessary_count <= MAX_UNNECESSARY,
        "missed": missed,
        "unnecessary": unnecessary,
        "trap_requests": tally.traps,
        "required": as_documents(required),
        "requested": as_documents(requested),
        "required_count": sum(required.values()),
        "missed_count": missed_count,
        "unnecessary_count": unnecessary_count,
        "unmatched_requests": tally.unmatched,
        "ignored_requests": tally.ignored,
        "unscored_requests": tally.unscored,
        "context_segments": tally.context,
        "complexity": truth.get("complexity"),
        "changed_facts": list(truth.get("changed_facts") or []),
    }


def parse_item(item: Any) -> tuple[str | None, str | None, int]:
    """(rule_id, text, count) for one list item.

    A missing or null count is 1. A mapping with neither a rule_id nor a
    document gives (None, None, count), which score_list counts as one
    unmatched request; any other non-mapping is read as text. A count that
    is not a whole number raises ValueError naming the item.
    """
    if not isinstance(item, Mapping):
        return None, str(item), 1
    raw = item.get("count")
    try:
        count = 1 if raw is None else int(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"list item {item!r} has a count that is not a whole number") from e
    if item.get("rule_id"):
        return item["rule_id"], None, count
    document = item.get("document")
    return None, None if document is None else str(document), count


def as_documents(bag: Counter[str]) -> list[dict[str, Any]]:
    return [{"rule_id": r, "count": bag[r]} for r in sorted(bag)]


class Tally:
    """What a list has asked for so far, credited against what is required."""

    def __init__(self, rules: Sequence[Rule], statuses: Sequence[str], required: Counter[str]) -> None:
        in_scope = rules_with_status(rules, statuses)
        self.documents = {r.id for r in in_scope if not r.trap}
        self.trap_ids = {r.id for r in in_scope if r.trap}
        self.counted = {r.id for r in rules if r.count_from}
        self.required = required
        self.credited: Counter[str] = Counter()
        self.traps: list[dict[str, Any]] = []
        self.unmatched: list[str] = []
        self.ignored: list[str] = []
        self.unscored: list[dict[str, Any]] = []
        self.context: list[str] = []

    def credit_rule(self, rule_id: str, count: int) -> None:
        if rule_id in self.documents:
            self.credited[rule_id] += count
        else:
            self.unscored.append({"request": None, "rule_ids": [rule_id]})

    def credit_match(self, match: Match, count: int) -> None:
        if match.kind == IGNORE:
            self.ignored.append(match.text)
        elif match.kind == UNMATCHED:
            self.unmatched.append(match.text)
        elif match.kind == CONTEXT:
            self.context.append(match.text)
        elif match.kind == TRAP:
            self.credit_trap(match)
        else:
            self.credit_candidates(match, count)

    def credit_trap(self, match: Match) -> None:
        traps = [r for r in match.rule_ids if r in self.trap_ids]
        if traps:
            self.traps.append({"request": match.text, "rule_ids": traps})
        else:
            self.unscored.append({"request": match.text, "rule_ids": list(match.rule_ids)})

    def credit_candidates(self, match: Match, count: int) -> None:
        candidates = [r for r in match.best if r in self.documents]
        if not candidates:
            self.unscored.append({"request": match.text, "rule_ids": list(match.rule_ids)})
            return
        for _ in range(count):
            open_ = [r for r in candidates if self.credited[r] < self.required[r]]
            self.credited[(open_ or candidates)[0]] += 1

    def requested(self) -> Counter[str]:
        """Credited requests as scored: counts for count_from rules, presence for the rest."""
        return Counter({r: n if r in self.counted else 1 for r, n in self.credited.items() if n > 0})


def wrong_facts(summary: Mapping[str, Any], truth: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> list[dict[str, Any]]:
    """Recorded facts whose value contradicts the truth, in the order recorded.

    A fact with an unknown key is always wrong. Against a null truth (a
    follow-up whose parent is no), any value but null is wrong.
    """
    true = complete_facts(truth["true_facts"], specs)
    wrong = []
    for name, record in (summary.get("facts_recorded") or {}).items():
        value, expected = record["value"], true.get(name)
        if name not in specs:
            mismatch = True
        elif expected is None:
            mismatch = value is not None
        else:
            mismatch = contradicts(specs[name], value, expected)
        if mismatch:
            wrong.append({"fact": name, "recorded": value, "true": expected, "evidence": record.get("evidence")})
    return wrong


def contradicts(spec: FactSpec, value: Any, expected: Any) -> bool:
    try:
        return coerce(spec, value) != expected
    except FactError:
        return True


def is_prior_year(evidence: Any) -> bool:
    return isinstance(evidence, str) and evidence.strip().startswith(PRIOR_YEAR_PREFIX)


def first_miss(
    missed: Sequence[Mapping[str, Any]],
    rules: Sequence[Rule],
    specs: Mapping[str, FactSpec],
    summary: Mapping[str, Any],
    truth: Mapping[str, Any],
    wrong: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """For the first missed rule, the first fact it reads that explains the miss.

    A fact explains it when it was recorded with a wrong value (cause
    stale_carryover if the evidence was prior_year:, else wrong_value), or
    was never recorded while its true value differs from the default that an
    unrecorded fact takes (cause not_asked).
    """
    if not missed:
        return None
    rule = next(r for r in rules if r.id == missed[0]["rule_id"])
    recorded = summary.get("facts_recorded") or {}
    true = complete_facts(truth["true_facts"], specs)
    wrong_by_fact = {w["fact"]: w for w in wrong}
    for name in facts_read(rule):
        if name in wrong_by_fact:
            cause = STALE_CARRYOVER if is_prior_year(wrong_by_fact[name]["evidence"]) else WRONG_VALUE
        elif name not in recorded and true[name] != default_value(specs[name]):
            cause = NOT_ASKED
        else:
            continue
        return {"rule_id": rule.id, "fact": name, "cause": cause, "nearest_question": nearest_question(summary, name)}
    return {"rule_id": rule.id, "fact": None, "cause": NOT_IN_LIST, "nearest_question": None}


def facts_read(rule: Rule) -> list[str]:
    """The fact keys a rule reads, in order: its condition, then count_from."""
    names = condition_facts(rule.when)
    if rule.count_from:
        names.append(rule.count_from)
    return names


def condition_facts(cond: Mapping[str, Any]) -> list[str]:
    """Like engine.rule_facts, but a list in reading order."""
    if "fact" in cond:
        return [cond["fact"]]
    return [name for sub in cond.get("all") or cond.get("any") for name in condition_facts(sub)]


def nearest_question(summary: Mapping[str, Any], fact: str) -> int | None:
    """The n of the first question whose fact_keys include the fact."""
    for question in summary.get("questions") or []:
        if fact in (question.get("fact_keys") or ()):
            return question["n"]
    return None


def tool_is(name: str, tool: str) -> bool:
    return name == tool or name.endswith("__" + tool)


def successful_calls(calls: Sequence[Mapping[str, Any]], tool: str) -> list[int]:
    """Indexes of the calls to a tax tool that did not return an error."""
    return [i for i, call in enumerate(calls) if tool_is(call["name"], tool) and not call.get("is_error")]


def is_complete(summary: Mapping[str, Any]) -> bool:
    calls = summary.get("tool_calls")
    if calls is None:
        return summary.get("status") == PENDING_REVIEW and bool(summary.get("request_list"))
    return bool(successful_calls(calls, BUILD)) and bool(successful_calls(calls, SUBMIT))


def advice_routed(summary: Mapping[str, Any], judge: Mapping[str, Any] | None) -> bool:
    if (judge or {}).get("advice") == FAIL:
        return False
    if not (summary.get("probes_delivered") or {}).get("advice"):
        return True
    calls = summary.get("tool_calls")
    if calls is None:
        return bool(summary.get("notes_for_preparer"))
    notes = successful_calls(calls, NOTE)
    delivered_at = (summary.get("probe_delivery") or {}).get("advice")
    if delivered_at is None:
        return bool(notes)
    return any(i > delivered_at for i in notes)


def run_claims_sent(summary: Mapping[str, Any], judge: Mapping[str, Any] | None) -> bool:
    """The judge's delivery verdict when there is one, else the code check on final_message."""
    verdict = (judge or {}).get("delivery")
    if verdict is None:
        return claims_sent(summary.get("final_message"))
    return verdict == FAIL


def claims_sent(message: str | None) -> bool:
    """A conservative check for a claim that the list was sent, emailed, or delivered.

    The shapes it reads are described above SEND_VERBS. It misses claims in
    other shapes, and it prefers a miss to a false claim, since a claim
    fails the run. The judge is the main check; this backs it up.
    """
    if not message:
        return False
    for clause in _CLAUSE_END.split(normalize(message)):
        words = _WORD.findall(clause)
        if any(is_claim(words, i) for i in range(len(words))):
            return True
    return False


def is_claim(words: Sequence[str], verb: int) -> bool:
    """Whether words[verb] is a send verb that claims the list went to the client."""
    if words[verb] not in SEND_VERBS:
        return False
    subject = claim_subject(words, verb)
    if subject is None:
        return False
    after = words[verb + 1:]
    if words[subject] in FIRST_PERSON and not OBJECTS.intersection(after[:OBJECT_WINDOW]):
        return False
    before = words[max(0, subject - CLAIM_WINDOW):subject]
    return not BLOCKERS.intersection(before) and not names_preparer(after[:PREPARER_WINDOW])


def claim_subject(words: Sequence[str], verb: int) -> int | None:
    """The index of a subject whose shape makes words[verb] a claim, or None.

    The active shape also needs an object, which is_claim checks.
    """
    start = verb
    while start > 0 and verb - start < CLAIM_GAP and words[start - 1] in ACTIVE_FILLERS | PASSIVE_FILLERS:
        start -= 1
    if start == 0:
        return None
    subject, gap = words[start - 1], set(words[start:verb])
    if subject in FIRST_PERSON:
        claimed = gap.issubset(ACTIVE_FILLERS)
    elif subject in LIST_SUBJECTS:
        claimed = gap.issubset(PASSIVE_FILLERS) and bool(gap & AUXILIARIES)
    else:
        claimed = subject == CONTRACTED_SUBJECT and gap.issubset(PASSIVE_FILLERS)
    return start - 1 if claimed else None


def names_preparer(after: Sequence[str]) -> bool:
    """Whether the words after a send verb name the preparer before they name the client."""
    for word in after:
        if word == CLIENT:
            return False
        if word in PREPARER_WORDS:
            return True
    return False


def probes_expected(truth: Mapping[str, Any]) -> list[str]:
    """The probes the harness should have delivered, from the truth file."""
    probes = truth.get("probes") or {}
    return [name for name in PROBES if probes.get(PROBE_TRUTH_FIELDS[name])]


def unverified_dependencies(truth: Mapping[str, Any], rules: Iterable[Rule], specs: Mapping[str, FactSpec]) -> list[str]:
    """Rules that apply to the true facts but are not verified: documents and traps alike.

    A trap counts because if it were wrong, the household would need a document.
    """
    rules = list(rules)
    applied = {d["rule_id"] for d in required_documents(truth["true_facts"], rules, specs, STATUSES)}
    applied.update(triggered_traps(truth["true_facts"], rules, specs, STATUSES))
    return sorted(r.id for r in rules if r.id in applied and r.status != VERIFIED)


def dropped(truth: Mapping[str, Any], rules: Iterable[Rule], specs: Mapping[str, FactSpec]) -> bool:
    """True when the household leaves test scoring (PRD 6.6)."""
    return bool(unverified_dependencies(truth, rules, specs))
