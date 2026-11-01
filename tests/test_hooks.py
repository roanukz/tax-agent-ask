"""The evidence hook (agent/hooks.py) and the prompts.

Self-contained: the summaries and answers are written here, never read from
data/ or fixtures/; the fact keys and rules come from rules/ through the
engine, as the hook's own defaults do. Checks on the real profiles live in
tests/test_generator.py and tests/test_replay.py.
"""

import anyio
import pytest

from agent.hooks import (
    EMPTY_EVIDENCE,
    NOT_A_KEY,
    NOT_FOUND,
    NOTHING_HOLDS,
    ONE_WORD,
    REASON_BYTES,
    RECORD_FACT_TOOL,
    USE_FIELD,
    WRONG_FIELD,
    EvidenceBook,
    allowed_prior_fields,
    normalize,
    prior_year_support,
    record_fact_hook,
    sentences,
    words,
)
from agent.prompts import B3_PROMPT, JUDGE_PROMPT, PLANNER_PROMPT, SYSTEM_PROMPT, fill
from rules.engine import FactSpec, Rule, load_facts, load_rules, rule_facts

SPECS = load_facts()
RULES = load_rules()
OWN_FIELD_KEYS = {s.key for s in SPECS.values() if s.prior_year_field}

SUMMARY = {
    "tax_year": 2025,
    "filing_status": "married_joint",
    "dependents_count": 1,
    "itemized": False,
    "documents_on_file": [{"rule_id": "R001", "label": "Form W-2", "count": 2}, {"rule_id": "R014", "label": "Form 1098", "count": 1}],
}
# Every field, and every rule on file, traps included.
FULL_SUMMARY = {
    **SUMMARY,
    "documents_on_file": [{"rule_id": r.id, "label": r.document or "", "count": 1} for r in RULES],
}
CLEAN_PASS_ANSWER = "No, nothing changed. Still the same one job, and no kids."


def book_with(*answers, summary=SUMMARY):
    b = EvidenceBook(prior_year_summary=summary)
    for a in answers:
        b.add_answer(a)
    return b


def test_normalize():
    assert normalize("  I  ALSO\npicked\tup ") == "i also picked up"
    assert normalize("I’m") == normalize("I'm")


def test_prior_fields():
    assert allowed_prior_fields(SUMMARY) == {
        "tax_year", "filing_status", "dependents_count", "itemized", "documents_on_file",
        "documents_on_file.R001", "documents_on_file.R014",
    }


@pytest.mark.parametrize(
    "evidence,key",
    [
        ("I also picked up a part-time job in October", "w2_employer_count"),
        ("i also picked UP a part-time   job", "w2_employer_count"),
        ("  part-time job in October  ", "w2_employer_count"),
        ('"I also picked up a part-time job"', "w2_employer_count"),
        ("I’ve got two kids", "dependents_count"),
        ("Yes", "w2_employer_count"),
        ("prior_year:filing_status", "filing_status"),
        ("prior_year: itemized", "itemized_last_year"),
        ("prior_year:documents_on_file.R014", "has_mortgage"),
        ("prior_year:documents_on_file.R001", "w2_employer_count"),
        ("prior_year:dependents_count", "dependents_count"),
    ],
)
def test_valid_evidence(evidence, key):
    ok, reason = book_with("Yes. I also picked up a part-time job in October.", "I've got two kids").check(evidence, key=key)
    assert ok, reason


@pytest.mark.parametrize(
    "evidence,key,why",
    [
        ("I picked up a part-time job", "w2_employer_count", "exact words"),
        ("They said they had three jobs", "w2_employer_count", "exact words"),
        ("October", "w2_employer_count", "whole sentence"),
        ("", "w2_employer_count", "empty"),
        ("   ", "w2_employer_count", "empty"),
        ('""', "w2_employer_count", "empty"),
        ("...", "w2_employer_count", "empty"),
        (None, "w2_employer_count", "empty"),
        (3, "w2_employer_count", "empty"),
        ("prior_year:w2_employer_count", "w2_employer_count", "no such field"),
        ("prior_year:", "filing_status", "no such field"),
        ("prior_year:Filing_Status", "filing_status", "no such field"),
        ("prior_year:tax_year", "has_mortgage", "wrong field"),
        ("prior_year:documents_on_file", "w2_employer_count", "wrong field"),
        ("prior_year:filing_status", "dependents_count", "wrong field"),
        ("prior_year:itemized", "filing_status", "wrong field"),
        ("prior_year:documents_on_file.R004", "interest_income", "nothing in last year's summary holds interest_income"),
        ("prior_year:documents_on_file.R001", "k1_count", "nothing in last year's summary holds k1_count"),
        ("prior_year:filing_status", "w2_count", "'w2_count' is not a fact key"),
        ("prior_year:filing_status", None, "None is not a fact key"),
        ("prior_year:filing_status", ["filing_status"], "is not a fact key"),
        ("prior_year:filing_status", {"key": "filing_status"}, "is not a fact key"),
    ],
)
def test_invalid_evidence(evidence, key, why):
    ok, reason = book_with("Yes. I also picked up a part-time job in October.").check(evidence, key=key)
    assert not ok
    assert why in reason


def test_quote_must_come_from_this_run_so_far():
    b = book_with()
    assert not b.check("yes", key="interest_income")[0]
    b.add_answer("Yes, same bank as last year")
    assert b.check("same bank as last year", key="interest_income")[0]


# ---- a quote: whole words of one answer, in a row ---------------------------------

@pytest.mark.parametrize("evidence", [
    "and no kids", "no kids", "nothing changed", "Still the same one job", "No, nothing changed!",
    "STILL the same one job and no kids", "changed. Still",
])
def test_a_quote_is_whole_words_of_one_answer_in_a_row(evidence):
    assert book_with(CLEAN_PASS_ANSWER).check(evidence, key="w2_employer_count") == (True, "")


@pytest.mark.parametrize("evidence,reason", [
    ("n", NOT_FOUND), ("o", NOT_FOUND), ("k", NOT_FOUND), ("thing", NOT_FOUND), ("ob, an", NOT_FOUND),
    ("same one job ... no kids", NOT_FOUND), ("kids no", NOT_FOUND),
    ("No", ONE_WORD), ('"No."', ONE_WORD), ("kids", ONE_WORD),
])
def test_a_letter_a_fragment_or_a_lone_word_is_not_a_quote(evidence, reason):
    assert book_with(CLEAN_PASS_ANSWER).check(evidence, key="w2_employer_count") == (False, reason)


def test_a_quote_never_spans_two_answers():
    b = book_with("Just the one job.", "No kids yet.")
    assert b.check("the one job", key="w2_employer_count") == (True, "")
    assert b.check("No kids yet", key="dependents_count") == (True, "")
    assert b.check("one job. No kids", key="dependents_count") == (False, NOT_FOUND)


def test_one_word_counts_only_as_a_whole_sentence():
    b = book_with("Yes.", "Two. I stopped working at the third one last year.")
    for evidence in ("Yes", "yes.", '"Yes"', "Yes!", "Two", "two."):
        assert b.check(evidence, key="w2_employer_count") == (True, ""), evidence
    lone = book_with("Yes, I switched jobs in June.")
    assert lone.check("Yes", key="w2_employer_count") == (False, ONE_WORD)
    assert lone.check("June", key="w2_employer_count") == (False, ONE_WORD)
    assert lone.check("Yes, I switched jobs", key="w2_employer_count") == (True, "")


def test_the_one_word_exception_follows_the_clients_punctuation():
    """A decision, pinned: after a comma "No" is part of a longer sentence, and after a period it is a sentence."""
    assert book_with("No, but my wife did.").check("No", key="self_employment") == (False, ONE_WORD)
    assert book_with("No. But my wife did.").check("No", key="self_employment") == (True, "")
    assert book_with("No! But my wife did.").check("No", key="self_employment") == (True, "")
    assert book_with("No\nBut my wife did.").check("No", key="self_employment") == (True, "")


def test_a_typographic_closing_quote_still_ends_a_sentence():
    curly = chr(0x201C) + "Yes." + chr(0x201D) + " That" + chr(0x2019) + "s all."
    for answer in (curly, '"Yes." That\'s all.', "(Yes.) That's all."):
        assert sentences(answer) == [("yes",), ("that's", "all")], answer
        assert book_with(answer).check("Yes", key="ip_pin_enrolled") == (True, ""), answer


def test_words_keep_hyphens_apostrophes_and_numbers():
    assert words("It" + chr(0x2019) + "s a part-time job, $1,200.50!") == ("it's", "a", "part-time", "job", "1,200.50")
    assert words("Yes" + chr(0x2014) + "two jobs") == ("yes", "two", "jobs")
    assert words("My W-2 came on 2/17, a 1099-INT too.") == ("my", "w-2", "came", "on", "2/17", "a", "1099-int", "too")
    b = book_with("I took a part-time job.")
    assert b.check("part-time job", key="w2_employer_count") == (True, "")
    assert b.check("part time job", key="w2_employer_count") == (False, NOT_FOUND)
    assert b.check("time job", key="w2_employer_count") == (False, NOT_FOUND)


# ---- a prior_year field: only for the fact it holds -------------------------------

def test_prior_year_support_comes_from_the_facts_and_rules():
    support = prior_year_support(FULL_SUMMARY, SPECS, RULES)
    for spec in SPECS.values():
        if spec.prior_year_field:
            assert support[spec.prior_year_field] == {spec.key}
    for rule in RULES:
        read = rule_facts(rule.when) | ({rule.count_from} if rule.count_from else set())
        expected = set() if rule.trap else read - OWN_FIELD_KEYS
        assert support.get(f"documents_on_file.{rule.id}", frozenset()) == expected, rule.id
    assert support["documents_on_file.R001"] == {"w2_employer_count"}
    assert support["documents_on_file.R011"] == {"state_refund_received"}, "itemized_last_year has its own field"
    assert "tax_year" not in support and "documents_on_file" not in support
    trap_only = (set().union(*(rule_facts(r.when) for r in RULES if r.trap))
                 - set().union(*(rule_facts(r.when) for r in RULES if not r.trap)))
    assert len(trap_only) == 6
    assert set().union(*support.values()) == set(SPECS) - trap_only


def test_prior_year_support_reads_only_what_the_summary_has():
    expected = {
        "filing_status": {"filing_status"}, "dependents_count": {"dependents_count"}, "itemized": {"itemized_last_year"},
        "documents_on_file.R001": {"w2_employer_count"}, "documents_on_file.R014": {"has_mortgage"},
    }
    assert prior_year_support(SUMMARY, SPECS, RULES) == expected
    odd = {**SUMMARY, "documents_on_file": [*SUMMARY["documents_on_file"], {"rule_id": "R999"}, {"rule_id": "T001"},
                                            {"rule_id": ["R004"]}, "R004", {"label": "Form 1099-INT"}]}
    assert prior_year_support(odd, SPECS, RULES) == expected
    assert prior_year_support({"filing_status": "single"}, SPECS, RULES) == {"filing_status": {"filing_status"}}
    assert prior_year_support({}, SPECS, RULES) == {}


def fact(key: str, prior_year_field: str | None = None) -> FactSpec:
    return FactSpec(key=key, type="bool", topic="t", plain_question_hint="?",
                    determinable_from_prior_year=prior_year_field is not None, prior_year_field=prior_year_field)


def rule(rule_id: str, when: dict, *, count_from: str | None = None, trap: bool = False) -> Rule:
    return Rule(id=rule_id, when=when, document=None if trap else "A document", count_from=count_from, aliases=(),
                client_reason=None, source_url="", source_title="", source_version="", tax_years=(), trap=trap,
                status="to_verify", verified_by=None, verified_on=None)


def test_a_book_binds_fields_with_the_facts_and_rules_it_is_given():
    specs = {k: fact(k) for k in ("a", "b", "n", "t")} | {"own": fact("own", prior_year_field="mine")}
    rules = [rule("X1", {"all": [{"fact": "a", "eq": True}, {"fact": "own", "eq": True}]}, count_from="n"),
             rule("X2", {"any": [{"fact": "b", "eq": True}]}),
             rule("X3", {"fact": "t", "eq": True}, trap=True)]
    b = EvidenceBook(prior_year_summary={"mine": True, "documents_on_file": [{"rule_id": "X1"}, {"rule_id": "X3"}]},
                     specs=specs, rules=rules)
    assert b.support == {"mine": {"own"}, "documents_on_file.X1": {"a", "n"}}
    assert b.check("prior_year:documents_on_file.X1", key="n") == (True, "")
    assert b.check("prior_year:mine", key="own") == (True, "")
    assert b.check("prior_year:documents_on_file.X1", key="own") == (False, WRONG_FIELD + USE_FIELD.format(field="mine"))
    assert b.check("prior_year:documents_on_file.X3", key="t") == (False, NOTHING_HOLDS.format(key="t"))
    assert b.check("prior_year:documents_on_file.X1", key="filing_status") == (
        False, NOT_A_KEY.format(key="'filing_status'"))


def test_a_field_denial_names_the_field_that_holds_the_fact_and_asks_first():
    b = book_with()
    r001 = "if last year's value still holds, use prior_year:documents_on_file.R001; otherwise ask the client and quote their answer"
    assert USE_FIELD.format(field="documents_on_file.R001") == r001
    assert b.check("prior_year:tax_year", key="w2_employer_count") == (False, "wrong field; " + r001)
    assert b.check("prior_year:documents_on_file", key="w2_employer_count") == (False, "wrong field; " + r001)
    assert b.check("prior_year:w2_employer_count", key="w2_employer_count") == (False, "no such field; " + r001)
    assert b.check("prior_year:filing_status", key="new_dependent") == (
        False, "nothing in last year's summary holds new_dependent; ask the client and quote their answer")
    assert b.check("prior_year:filing_status", key="w2_count") == (
        False, "'w2_count' is not a fact key; list_fact_keys gives every key")
    assert book_with(summary={}).check("prior_year:filing_status", key="filing_status") == (
        False, NOTHING_HOLDS.format(key="filing_status"))


def test_every_reason_fits_in_reason_bytes_and_echoes_cut_on_a_character():
    wide = chr(0x1F600) * 100
    keys = [*SPECS, "y" * 5000, wide, chr(0xD800) + "x" * 60, None, [1] * 500, {"a": "b" * 500}]
    names = [*sorted(allowed_prior_fields(FULL_SUMMARY)), "", "x" * 5000, wide]
    b = book_with("Yes, I switched jobs in June.", summary=FULL_SUMMARY)
    reasons = {b.check(e, key=k)[1] for k in keys for e in ("", "Yes", "made up words", *(f"prior_year:{n}" for n in names))}
    assert {EMPTY_EVIDENCE, NOT_FOUND, ONE_WORD} <= reasons
    assert max(len(r.encode()) for r in reasons) <= REASON_BYTES
    cut = b.check("prior_year:filing_status", key=wide)[1]
    assert cut == NOT_A_KEY.format(key="'" + chr(0x1F600) * 9 + "...")
    assert b.check("prior_year:filing_status", key="y" * 5000)[1] == NOT_A_KEY.format(key="'" + "y" * 39 + "...")


# ---- the hook ---------------------------------------------------------------------

def run_hook(book, tool_input, tool_name=RECORD_FACT_TOOL):
    hook = record_fact_hook(book)

    async def go():
        return await hook({"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input}, "tu_1", None)

    return anyio.run(go)


def test_hook_allows_valid_and_logs_nothing():
    b = book_with("Yes, same bank as last year")
    out = run_hook(b, {"intake_id": "in_1", "key": "interest_income", "value": True, "evidence": "same bank as last year"})
    assert out == {}
    assert b.denials == []


def test_hook_denies_and_logs_invalid():
    b = book_with("Yes, same bank as last year")
    out = run_hook(b, {"intake_id": "in_1", "key": "interest_income", "value": True, "evidence": "client has interest"})
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    assert "exact words" in spec["permissionDecisionReason"]
    assert b.denials == [{
        "tool_use_id": "tu_1", "key": "interest_income", "value": True,
        "evidence": "client has interest", "reason": spec["permissionDecisionReason"],
    }]


def test_hook_checks_a_field_against_the_key_it_records():
    b = book_with()
    out = run_hook(b, {"intake_id": "in_1", "key": "has_mortgage", "value": False, "evidence": "prior_year:filing_status"})
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason == WRONG_FIELD + USE_FIELD.format(field="documents_on_file.R014")
    assert b.denials == [{"tool_use_id": "tu_1", "key": "has_mortgage", "value": False,
                          "evidence": "prior_year:filing_status", "reason": reason}]
    ok = {"intake_id": "in_1", "key": "filing_status", "value": "married_joint", "evidence": "prior_year:filing_status"}
    assert run_hook(b, ok) == {} and len(b.denials) == 1
    no_key = run_hook(b, {"intake_id": "in_1", "value": 1, "evidence": "prior_year:filing_status"})
    assert no_key["hookSpecificOutput"]["permissionDecisionReason"] == NOT_A_KEY.format(key="None")


def test_hook_ignores_other_tools():
    b = book_with()
    assert run_hook(b, {"evidence": "nothing"}, tool_name="mcp__tax__note_for_preparer") == {}
    assert b.denials == []


def test_fill_handles_json_braces():
    out = fill(B3_PROMPT, facts_json='{"a": 1}')
    assert '{"documents": [' in out and '{"a": 1}' in out
    with pytest.raises(KeyError):
        fill(B3_PROMPT, nope="x")
    planner = fill(PLANNER_PROMPT, prior_year_summary_json="{}", fact_keys_json="[]", rules_json="[]")
    assert "{prior_year_summary_json}" not in planner
    judge = fill(JUDGE_PROMPT, draft_list="Form W-2", transcript="...")
    assert "Draft list:\nForm W-2" in judge


def test_system_prompt_is_the_prd_text():
    prd = open("PRD-tax-agent-ask.md").read()
    assert SYSTEM_PROMPT in prd
    assert JUDGE_PROMPT.replace("{draft_list}", "{draft_list}") in prd
