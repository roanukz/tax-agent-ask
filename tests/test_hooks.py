import anyio
import pytest

from agent.hooks import RECORD_FACT_TOOL, EvidenceBook, allowed_prior_fields, normalize, record_fact_hook
from agent.prompts import B3_PROMPT, JUDGE_PROMPT, PLANNER_PROMPT, SYSTEM_PROMPT, fill

SUMMARY = {
    "tax_year": 2025,
    "filing_status": "married_joint",
    "dependents_count": 1,
    "itemized": False,
    "documents_on_file": [{"rule_id": "R001", "label": "Form W-2", "count": 2}, {"rule_id": "R014", "label": "Form 1098", "count": 1}],
}


def book_with(*answers):
    b = EvidenceBook(prior_year_summary=SUMMARY)
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
    "evidence",
    [
        "I also picked up a part-time job in October",
        "i also picked UP a part-time   job",
        "  part-time job in October  ",
        '"I also picked up a part-time job"',
        "I’ve got two kids",
        "prior_year:filing_status",
        "prior_year: itemized",
        "prior_year:documents_on_file.R014",
    ],
)
def test_valid_evidence(evidence):
    ok, reason = book_with("Yes. I also picked up a part-time job in October.", "I've got two kids").check(evidence)
    assert ok, reason


@pytest.mark.parametrize(
    "evidence,why",
    [
        ("I picked up a part-time job", "exact words"),
        ("They said they had three jobs", "exact words"),
        ("", "empty"),
        ("   ", "empty"),
        ('""', "empty"),
        (None, "empty"),
        (3, "empty"),
        ("prior_year:w2_employer_count", "no field"),
        ("prior_year:documents_on_file.R004", "no field"),
        ("prior_year:", "no field"),
    ],
)
def test_invalid_evidence(evidence, why):
    ok, reason = book_with("Yes. I also picked up a part-time job in October.").check(evidence)
    assert not ok
    assert why in reason


def test_quote_must_come_from_this_run_so_far():
    b = book_with()
    assert not b.check("yes")[0]
    b.add_answer("Yes, same bank as last year")
    assert b.check("same bank as last year")[0]


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
