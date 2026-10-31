"""Block 4: the client simulator and its fidelity check, offline with fake clients."""

import ast
import asyncio
import builtins
import io
import json
import pathlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent.env import HarnessSettings
from generator import generate as gen
from generator.files import load_truths
from ledger.spend import RunCapExceeded, cost_usd, load_prices
from rules.engine import load_facts
from sim import budget, fidelity, simulator
from sim.budget import BudgetedClient, BudgetExceeded
from sim.fakes import FakeResponse, FakeTextBlock, FakeUsage, ScriptedClient, text_response
from sim.fidelity import (
    QUESTIONS,
    AnswerRecord,
    choose_answers_for_review,
    choose_households,
    estimate_cost_usd,
    score,
    text_leaks,
)
from sim.simulator import (
    ClientSimulator,
    ReplyError,
    SimulatorError,
    Usage,
    build_system_prompt,
    parse_reply,
)

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sim"
SPECS = load_facts()
PLACEHOLDER_KEY = "placeholder-not-a-key"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def truth(hid="H902"):
    return json.loads((FIXTURES / "truth" / f"{hid}.json").read_text())


def reply(answer, revealed=None):
    return json.dumps({"answer": answer, "revealed": revealed or {}})


def make_sim(replies, hid="H902", usage=None, **kwargs):
    client = ScriptedClient(replies, usage=usage)
    return ClientSimulator(truth(hid), client, specs=SPECS, **kwargs), client


def prompt_json(prompt, label):
    line = next(x for x in prompt.splitlines() if x.startswith(label))
    return json.loads(line[len(label):])


# ---- system prompt -------------------------------------------------------------


def test_prompt_template_is_prd_6_4_word_for_word():
    prd = (REPO / "PRD-tax-agent-ask.md").read_text()
    block = prd.split("### 6.4")[1].split("```")[1].strip("\n")
    assert simulator.SYSTEM_PROMPT_TEMPLATE == block


def test_prompt_fills_facts_and_persona_without_nulls_or_internal_fields():
    t = truth("H902")
    prompt = build_system_prompt(t, SPECS)
    facts = prompt_json(prompt, "Your facts: ")
    persona = prompt_json(prompt, "Your persona: ")
    assert facts == {k: v for k, v in t["true_facts"].items() if v is not None}
    assert facts["w2_employer_count"] == 3 and facts["clean_energy_install_year"] == 2026
    for null_follow_up in ("efficiency_improvements_year", "ev_acquired_date"):
        assert null_follow_up not in prompt
    assert list(facts) == [k for k in SPECS if k in facts]
    assert persona == {
        "communication_style": "brief",
        "jargon_tolerance": "low",
        "volunteers_info": False,
        "recall_gap": "forgets_new_part_time_job_unless_asked_about_other_work",
    }
    for internal in ("Two earners", "label", "changed_facts", "probes", "home office", "split"):
        assert internal not in prompt
    assert "{true_facts_json}" not in prompt and "{persona_json}" not in prompt
    prd_part = simulator.SYSTEM_PROMPT_TEMPLATE.replace("{true_facts_json}", json.dumps(facts)).replace(
        "{persona_json}", json.dumps(persona))
    assert prompt.startswith(prd_part)
    # The fixture has no prior-year facts, so the prompt says nothing about last year.
    assert prompt[len(prd_part):] == (
        "\nYour recall_gap in plain words: You forget the new part-time job you started this year, so you say "
        "2 employers paid you wages, not 3, unless a question asks about other work. Once one does, you remember "
        "it and answer truthfully from then on."
    )


ALL_TRUTHS = load_truths()


def change_section(prompt):
    """The plain lines under "Compared with last year", or None when the prompt has none."""
    if simulator.CHANGES_INTRO not in prompt:
        return None
    tail = prompt.split(simulator.CHANGES_INTRO + "\n", 1)[1]
    assert all(line.startswith("- ") for line in tail.splitlines())
    return [line[2:] for line in tail.splitlines()]


def test_prompt_says_what_changed_since_last_year_after_the_prd_text():
    t = ALL_TRUTHS["H024"]
    prompt = build_system_prompt(t, SPECS)
    head, _, tail = prompt.partition("\n\n" + simulator.CHANGES_INTRO)
    assert head.splitlines()[-2].startswith("Your persona: ")
    assert head.splitlines()[-1].startswith("Your recall_gap in plain words: You forget the new part-time job")
    assert change_section(prompt) == [
        "Employers paying wages: 1 last year, 2 this year.",
        "New this year: unemployment benefits.",
        "New this year: an electric vehicle purchase.",
    ]
    assert t["persona"]["label"] not in prompt and "label" not in prompt and "a new job" not in prompt
    assert "Last year only" not in prompt
    quiet = ALL_TRUTHS["H043"]
    assert quiet["changed_facts"] == [] and change_section(build_system_prompt(quiet, SPECS)) == [
        "Nothing changed since last year."]


def test_every_changed_fact_of_every_household_is_in_its_prompt():
    for hid, t in ALL_TRUTHS.items():
        prompt = build_system_prompt(t, SPECS)
        lines = change_section(prompt)
        prior, current, changed = t["prior_year_facts"], t["true_facts"], t["changed_facts"]
        top = [k for k in changed if not SPECS[k].is_follow_up]
        assert top or lines == ["Nothing changed since last year."], hid
        for key in top:
            if key in simulator.COUNT_CHANGES:
                expected = f"{simulator.COUNT_CHANGES[key]}: {prior[key]} last year, {current[key]} this year."
                assert expected in lines, (hid, key)
            elif key == "business_app_payments" and "self_employment" in changed:
                business = [x for x in lines if simulator.CHANGE_NOUNS["self_employment"] in x]
                assert len(business) == 1 and "app or online marketplace" in business[0], (hid, key)
            else:
                when = "New this year" if current[key] else "Last year only"
                assert any(x.startswith(f"{when}: {simulator.CHANGE_NOUNS[key]}") for x in lines), (hid, key)
        folded = "business_app_payments" in top and "self_employment" in top
        assert len(lines) == max(1, len(top) - folded), hid
        for line in lines:
            assert fidelity.text_leaks(line, SPECS) == [], (hid, line)
        assert t["persona"]["label"] not in prompt, hid


def test_change_lines_need_known_keys_and_last_years_values():
    t = ALL_TRUTHS["H024"]
    assert simulator.change_lines({"true_facts": t["true_facts"], "changed_facts": t["changed_facts"]}, SPECS) is None
    with pytest.raises(ValueError, match="unknown changed fact keys"):
        build_system_prompt({**t, "changed_facts": ["made_up_key"]}, SPECS)
    with pytest.raises(ValueError, match="must be an object"):
        build_system_prompt({**t, "prior_year_facts": ["not", "an", "object"]}, SPECS)
    with pytest.raises(ValueError, match="lacks a value"):
        build_system_prompt({**t, "prior_year_facts": {}}, SPECS)


def test_every_generator_recall_gap_has_a_plain_sentence():
    assert set(simulator.RECALL_GAP_WORDS) == set(gen.RECALL_GAPS)
    for slug in gen.RECALL_GAPS:
        holders = [t for t in ALL_TRUTHS.values() if t["persona"]["recall_gap"] == slug]
        assert holders, slug
        for t in holders:
            prompt = build_system_prompt(t, SPECS)
            line = next(x for x in prompt.splitlines() if x.startswith(simulator.RECALL_GAP_LABEL))
            sentence = line[len(simulator.RECALL_GAP_LABEL):]
            assert prompt_json(prompt, "Your persona: ")["recall_gap"] == slug
            assert sentence.startswith("You forget ") and sentence.endswith("answer truthfully from then on.")
            assert "_" not in sentence and "{" not in sentence and fidelity.text_leaks(sentence, SPECS) == []


@pytest.mark.parametrize("jobs, says", [
    (2, "1 employer paid you wages, not 2"),
    (3, "2 employers paid you wages, not 3"),
    (4, "3 employers paid you wages, not 4"),
])
@pytest.mark.parametrize("gap", ["forgets_second_job_unless_asked_about_other_work",
                                 "forgets_new_part_time_job_unless_asked_about_other_work"])
def test_job_gaps_forget_exactly_one_job_whatever_the_count(gap, jobs, says):
    sentence = simulator.recall_gap_sentence(gap, {"w2_employer_count": jobs})
    assert f"so you say {says}, unless a question asks about other work." in sentence


def test_a_slug_without_a_sentence_shows_as_the_slug_alone():
    prompt = build_system_prompt(truth("H903"), SPECS)
    assert prompt_json(prompt, "Your persona: ")["recall_gap"] == truth("H903")["persona"]["recall_gap"]
    assert simulator.RECALL_GAP_LABEL not in prompt
    assert simulator.RECALL_GAP_LABEL not in build_system_prompt(truth("H901"), SPECS)


def test_truth_is_copied_and_validated():
    t = truth("H902")
    sim = ClientSimulator(t, ScriptedClient([]), specs=SPECS)
    before = sim.system_prompt
    t["true_facts"]["w2_employer_count"] = 9
    t["persona"]["volunteers_info"] = True
    assert sim.system_prompt == before
    bad = truth("H902")
    bad["true_facts"]["made_up_key"] = True
    with pytest.raises(ValueError, match="unknown fact keys"):
        ClientSimulator(bad, ScriptedClient([]), specs=SPECS)
    bad = truth("H902")
    bad["true_facts"]["w2_employer_count"] = "three"
    with pytest.raises(ValueError, match="invalid"):
        ClientSimulator(bad, ScriptedClient([]), specs=SPECS)
    real = json.loads(json.dumps(ALL_TRUTHS["H024"]))
    sim = ClientSimulator(real, ScriptedClient([]), specs=SPECS)
    before = sim.system_prompt
    real["prior_year_facts"]["w2_employer_count"] = 0
    real["changed_facts"].append("sold_home")
    assert sim.system_prompt == before and change_section(before)[0].startswith("Employers paying wages: 1 last")


# ---- isolation -------------------------------------------------------------------


@pytest.mark.parametrize("not_data", ["data/truth/H902.json", Path("data/truth/H902.json"), b"{}"])
def test_simulator_takes_data_not_paths(not_data):
    with pytest.raises(TypeError, match="truth dict"):
        ClientSimulator(not_data, ScriptedClient([]), specs=SPECS)
    with pytest.raises(ValueError, match="true_facts"):
        ClientSimulator({"household_id": "H902"}, ScriptedClient([]), specs=SPECS)


def test_simulator_module_has_no_file_access():
    tree = ast.parse((REPO / "sim" / "simulator.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    allowed = {"__future__", "asyncio", "copy", "json", "re", "dataclasses", "typing", "ledger.spend", "rules.engine"}
    assert imported <= allowed
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    assert not names & {"open", "Path", "read_text", "read_bytes", "listdir", "glob", "walk", "scandir"}


@pytest.mark.anyio
async def test_answering_never_touches_the_filesystem(monkeypatch):
    sim, _ = make_sim([reply("Yes, three jobs.", {"w2_employer_count": 3})])

    def refuse(*args, **kwargs):
        raise AssertionError("the simulator tried to open a file")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(io, "open", refuse)
    monkeypatch.setattr(pathlib.Path, "open", refuse)
    monkeypatch.setattr(pathlib.Path, "read_text", refuse)
    result = await sim.answer("How many jobs did you have?")
    assert result.revealed == {"w2_employer_count": 3}


# ---- answers ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_answer_text_never_carries_the_revealed_json():
    raw = reply("Three jobs, one I started in October.", {"w2_employer_count": 3})
    sim, _ = make_sim([raw])
    result = await sim.answer("How many employers paid you wages this year?")
    assert result.answer == "Three jobs, one I started in October."
    assert "revealed" not in result.answer and "{" not in result.answer
    assert result.revealed == {"w2_employer_count": 3}
    assert result.raw_text == raw
    assert result.retried is False and result.anomalies == []


@pytest.mark.parametrize(
    "text",
    [
        '{"answer": "Yes.", "revealed": {"interest_income": true}}',
        '```json\n{"answer": "Yes.", "revealed": {"interest_income": true}}\n```',
        '```\n{"answer": "Yes.", "revealed": {"interest_income": true}}\n```',
        'Here you go:\n```json\n{"answer": "Yes.", "revealed": {"interest_income": true}}\n```',
        '  {"answer": "Yes.", "revealed": {"interest_income": true}} trailing words',
    ],
)
def test_parse_reply_tolerates_fences_and_prose(text):
    assert parse_reply(text) == {"answer": "Yes.", "revealed": {"interest_income": True}}


@pytest.mark.parametrize(
    "text,reason",
    [
        ("Sure, I had three jobs.", "not valid JSON"),
        ('["answer", "revealed"]', "not a JSON object"),
        ('{"revealed": {}}', "no \"answer\""),
        ('{"answer": "   ", "revealed": {}}', "no \"answer\""),
        ('{"answer": 3, "revealed": {}}', "no \"answer\""),
        ('{"answer": "Yes {\\"revealed\\": 1}", "revealed": {}}', "contains JSON"),
    ],
)
def test_parse_reply_rejects(text, reason):
    with pytest.raises(ReplyError, match=reason):
        parse_reply(text)


@pytest.mark.anyio
async def test_fenced_reply_is_used_without_a_retry():
    sim, client = make_sim(['```json\n{"answer": "No.", "revealed": {"interest_income": false}}\n```'])
    result = await sim.answer("Did you earn interest?")
    assert result.answer == "No." and result.revealed == {"interest_income": False}
    assert result.retried is False and len(client.calls) == 1


@pytest.mark.anyio
async def test_one_retry_with_a_correction_then_success():
    sim, client = make_sim(
        ["Sure, I had three jobs.", reply("Three.", {"w2_employer_count": 3})],
        usage=FakeUsage(input_tokens=100, output_tokens=20),
    )
    result = await sim.answer("How many jobs?")
    assert result.retried is True and "not valid JSON" in result.retry_reason
    assert result.answer == "Three." and result.revealed == {"w2_employer_count": 3}
    retry = client.calls[1]["messages"]
    assert retry[-3] == {"role": "user", "content": "How many jobs?"}
    assert retry[-2] == {"role": "assistant", "content": "Sure, I had three jobs."}
    assert retry[-1]["role"] == "user" and retry[-1]["content"].startswith("Your last reply could not be used")
    assert result.usage == Usage(input_tokens=200, output_tokens=40)
    # The failed attempt stays out of the conversation.
    assert sim.history == [
        {"role": "user", "content": "How many jobs?"},
        {"role": "assistant", "content": reply("Three.", {"w2_employer_count": 3})},
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "first,reason",
    [
        (json.dumps({"revealed": {}}), "answer"),
        (json.dumps({"answer": 'Yes {"revealed": {"x": 1}}', "revealed": {}}), "contains JSON"),
        (text_response(reply("Yes."), stop_reason="max_tokens"), "cut off"),
        (text_response("", stop_reason="refusal"), "declined"),
    ],
)
async def test_unusable_first_replies_are_retried(first, reason):
    sim, client = make_sim([first, reply("Yes.")])
    result = await sim.answer("Did you earn interest?")
    assert result.retried is True and reason in result.retry_reason
    assert result.answer == "Yes." and len(client.calls) == 2
    if isinstance(first, FakeResponse) and not first.content[0].text:
        # An empty reply can't be an assistant turn, so only the correction is added.
        assert client.calls[1]["messages"][-2]["role"] == "user"


@pytest.mark.anyio
async def test_second_failure_raises_and_keeps_the_usage():
    replies = ["not json", "still not json", reply("Yes.")]
    sim, _ = make_sim(replies, usage=FakeUsage(input_tokens=50, output_tokens=5))
    with pytest.raises(SimulatorError) as caught:
        await sim.answer("Did you earn interest?")
    err = caught.value
    assert len(err.reasons) == 2 and err.raw_texts == ["not json", "still not json"]
    assert err.usage == Usage(input_tokens=100, output_tokens=10)
    assert sim.total_usage == Usage(input_tokens=100, output_tokens=10) and sim.calls == 2
    assert sim.history == []
    result = await sim.answer("Did you earn interest?")
    assert result.answer == "Yes." and len(sim.history) == 2


@pytest.mark.anyio
async def test_bad_revealed_entries_become_anomalies():
    raw = {
        "answer": "Two jobs and a little freelance work.",
        "revealed": {
            "w2_employer_count": "two",
            "made_up_key": True,
            "self_employment": "true",
            "k1_count": 2,
            "dependents_count": "--3",
            "ev_acquired_date": None,
        },
    }
    sim, _ = make_sim([json.dumps(raw), reply("Yes."), json.dumps({"answer": "Yes.", "revealed": [1]})])
    result = await sim.answer("Tell me about work.")
    assert result.revealed == {"self_employment": True, "k1_count": 2, "ev_acquired_date": None}
    assert {(a["kind"], a.get("key")) for a in result.anomalies} == {
        ("uncoercible", "w2_employer_count"),
        ("uncoercible", "dependents_count"),
        ("unknown_key", "made_up_key"),
    }
    assert result.raw_revealed == raw["revealed"]
    empty = await sim.answer("Anything else?")
    assert empty.anomalies == [] and empty.revealed == {}
    not_object = await sim.answer("Anything more?")
    assert not_object.anomalies == [{"kind": "revealed_not_object", "value": [1]}]


@pytest.mark.anyio
async def test_missing_revealed_is_an_anomaly():
    sim, _ = make_sim([json.dumps({"answer": "No."})])
    result = await sim.answer("Did you earn interest?")
    assert result.revealed == {} and result.anomalies == [{"kind": "missing_revealed"}]


@pytest.mark.anyio
async def test_history_is_passed_on_later_calls():
    first = reply("Two jobs.", {"w2_employer_count": 2})
    second = reply("Yes, a part-time job too, so three.", {"w2_employer_count": 3, "self_employment": False})
    sim, client = make_sim([first, second])
    await sim.answer("How many jobs?")
    await sim.answer("Any other work?")
    assert client.calls[0]["messages"] == [{"role": "user", "content": "How many jobs?"}]
    assert client.calls[1]["messages"] == [
        {"role": "user", "content": "How many jobs?"},
        {"role": "assistant", "content": first},
        {"role": "user", "content": "Any other work?"},
    ]
    assert client.calls[0]["system"] == client.calls[1]["system"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"answer": " Three jobs. ", "revealed": {"w2_employer_count": "3"}}\n```',
        'Sure, here it is: {"answer": "Three jobs.", "revealed": {"w2_employer_count": "3"}} Hope that helps.',
    ],
)
async def test_history_keeps_the_clean_json_not_the_raw_reply(raw):
    sim, client = make_sim([raw, reply("No.")])
    first = await sim.answer("How many jobs?")
    assert first.raw_text == raw and first.retried is False
    await sim.answer("Any side work?")
    clean = json.dumps({"answer": "Three jobs.", "revealed": {"w2_employer_count": "3"}})
    assert client.calls[1]["messages"][1] == {"role": "assistant", "content": clean}
    assert sim.history[1]["content"] == clean
    assert "```" not in clean and "Sure" not in clean and "Hope" not in clean


@pytest.mark.anyio
async def test_usage_per_answer_and_in_total():
    a = text_response(reply("Yes."), FakeUsage(input_tokens=40, output_tokens=8, cache_creation_input_tokens=700))
    b = text_response(reply("No."), FakeUsage(input_tokens=60, output_tokens=6, cache_read_input_tokens=700))
    sim, _ = make_sim([a, b])
    first = await sim.answer("Q1?")
    second = await sim.answer("Q2?")
    assert first.usage == Usage(input_tokens=40, output_tokens=8, cache_write_tokens=700)
    assert second.usage == Usage(input_tokens=60, output_tokens=6, cache_read_tokens=700)
    assert sim.total_usage == Usage(input_tokens=100, output_tokens=14, cache_read_tokens=700, cache_write_tokens=700)
    assert sim.total_usage.as_dict() == {
        "input_tokens": 100, "output_tokens": 14, "cache_read_tokens": 700, "cache_write_tokens": 700}
    assert sim.max_prompt_tokens == 760 and sim.calls == 2


def test_usage_from_api_shapes():
    assert Usage.from_api(None) == Usage()
    assert Usage.from_api({"input_tokens": 3, "cache_read_input_tokens": 4}) == Usage(3, 0, 4, 0)
    assert Usage.from_api({"input_tokens": 3, "cache_write_tokens": 5}) == Usage(3, 0, 0, 5)
    assert Usage.from_api(FakeUsage(1, 2, 3, None)) == Usage(1, 2, 3, 0)


@pytest.mark.anyio
async def test_request_shape_caches_the_system_prompt():
    sim, client = make_sim([reply("Yes."), reply("Yes.")])
    await sim.answer("Q?")
    kwargs = client.calls[0]
    assert kwargs["model"] == "claude-haiku-5-5"
    assert kwargs["max_tokens"] == simulator.DEFAULT_MAX_TOKENS
    assert kwargs["system"] == [{"type": "text", "text": sim.system_prompt, "cache_control": {"type": "ephemeral"}}]
    assert kwargs["output_config"] == {"effort": "low"}
    assert "temperature" not in kwargs
    custom, client = make_sim([reply("Yes.")], model="claude-sonnet-5-5", max_tokens=300, effort=None)
    await custom.answer("Q?")
    assert client.calls[0]["model"] == "claude-sonnet-5-5" and client.calls[0]["max_tokens"] == 300
    assert "output_config" not in client.calls[0]


@dataclass
class ThinkingBlock:
    thinking: str
    type: str = "thinking"


@pytest.mark.anyio
async def test_thinking_blocks_are_skipped():
    response = FakeResponse(content=[ThinkingBlock("let me check my facts"), FakeTextBlock(reply("No."))])
    sim, _ = make_sim([response])
    result = await sim.answer("Q?")
    assert result.answer == "No." and result.retried is False


@pytest.mark.anyio
async def test_empty_question_is_rejected():
    sim, client = make_sim([])
    with pytest.raises(ValueError):
        await sim.answer("  ")
    assert client.calls == []


# ---- fidelity: questions and leaks ----------------------------------------------


def test_questions_cover_the_topics_in_plain_language():
    assert len(QUESTIONS) == 10
    assert len({q.id for q in QUESTIONS}) == 10
    assert [q.topic for q in QUESTIONS] == [
        "jobs", "side work", "banks and investments", "retirement and Social Security",
        "health coverage and HSAs", "home", "school and child care", "charity and gambling",
        "energy upgrades and vehicles", "anything else that changed",
    ]
    asked = [k for q in QUESTIONS for k in q.asks]
    assert len(asked) == len(set(asked)) and set(asked) <= set(SPECS)
    assert set(SPECS) - set(asked) == {"itemized_last_year"}
    for q in QUESTIONS:
        assert text_leaks(q.text, SPECS) == [], q.id
        for key in q.asks:
            parent = SPECS[key].asked_if
            if parent:
                assert parent["fact"] in q.asks, key
    assert [q.id for q in QUESTIONS if q.open_to_changes] == ["q10"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("I had two jobs and a little side gig.", []),
        ("I got two W-2s and a 1099-NEC.", [("form_number_in_text", "W-2s"), ("form_number_in_text", "1099-NEC")]),
        ("Not sure what a K-1 is.", [("form_number_in_text", "K-1")]),
        (
            "They sent a Form 5498-SA and a 1095-A.",
            [("form_number_in_text", "Form 5498-SA"), ("form_number_in_text", "1095-A")],
        ),
        ("My accountant mentioned Schedule C.", [("form_number_in_text", "Schedule C")]),
        ("We paid about $5695 for the roof and my schedule is busy.", []),
        ("My w2_employer_count is 3.", [("fact_key_in_text", "w2_employer_count")]),
        ("I had 2 jobs in 2026.", []),
    ],
)
def test_text_leaks(text, expected):
    assert [(x["kind"], x["match"]) for x in text_leaks(text, SPECS)] == expected


def rec(qid, revealed=None, answer="Some words.", hid="H902", **kwargs):
    q = next(q for q in QUESTIONS if q.id == qid)
    return AnswerRecord(hid, qid, q.text, answer, revealed or {}, **kwargs)


def test_score_on_canned_answers():
    truths = {"H902": truth("H902")}
    records = [
        rec("q01", {"w2_employer_count": 2, "tips_income": False}, "Two jobs, no tips."),
        rec("q02", {"self_employment": True, "business_app_payments": True, "w2_employer_count": 3}),
        rec("q03", {"interest_income": True, "has_mortgage": True}),
        rec("q04", anomalies=[{"kind": "uncoercible", "key": "social_security", "value": "maybe", "error": "x"},
                              {"kind": "unknown_key", "key": "made_up", "value": 1}]),
        rec("q05", answer=None, error="unusable reply twice", retried=True),
        rec("q06", {"has_mortgage": True}, retried=True),
        rec("q09", {"clean_energy_installed": True, "clean_energy_install_year": 2025}, "Solar, there's a Form 5695."),
        rec("q10", {"filing_status": "married_joint", "self_employment": True}),
    ]
    s = score(truths, list(reversed(records)), SPECS)
    assert (s.compared, s.matched) == (13, 10)
    assert s.match_rate == pytest.approx(10 / 13) and s.meets_bar is False
    assert [
        (m["question_id"], m["key"], m["revealed"], m["truth"], m["corrected_later"], m["corrected_by"])
        for m in s.mismatches
    ] == [
        ("q01", "w2_employer_count", 2, 3, True, "q02"),
        ("q09", "clean_energy_install_year", 2025, 2026, False, None),
    ]
    assert s.mismatches[0]["corrected_on_topic"] is False and s.mismatches[1]["corrected_on_topic"] is None
    assert s.mismatches[0]["recall_gap"] == "forgets_new_part_time_job_unless_asked_about_other_work"
    # q02 corrects q01 off topic (the recall gap breaking); q06 asks about the mortgage, and
    # q10 may bring up self_employment because it changed this year.
    assert [(x["question_id"], x["kind"], x["match"]) for x in s.leaks] == [
        ("q02", "changed_off_topic", "w2_employer_count"),
        ("q03", "unasked_fact", "has_mortgage"),
        ("q09", "form_number_in_text", "Form 5695"),
    ]
    assert s.leaks[0]["earlier_question"] == "q01" and (s.leaks[0]["earlier_value"], s.leaks[0]["value"]) == (2, 3)
    assert s.leaks[0]["recall_gap"] == "forgets_new_part_time_job_unless_asked_about_other_work"
    assert [(a["question_id"], a["kind"]) for a in s.anomalies] == [("q04", "uncoercible"), ("q04", "unknown_key")]
    assert s.errors == [{"household_id": "H902", "question_id": "q05", "error": "unusable reply twice"}]
    assert s.unscored == [{"household_id": "H902", "question_id": "q05", "reason": "failed twice"}]
    assert s.answers == 8 and s.retries == 2
    assert s.per_question["q01"] == {"compared": 2, "matched": 1}
    assert s.per_question["q04"] == {"compared": 1, "matched": 0}
    assert s.recall_gap_households == ["H902"] and s.match_rate_without_recall_gaps is None


def test_every_fact_right_meets_the_bar_and_one_wrong_does_not():
    t = truth("H903")
    truths = {"H903": t}
    values = fidelity.truth_values(t, SPECS)
    records = []
    for q in QUESTIONS:
        keys = list(q.asks) + (["itemized_last_year"] if q.id == "q10" else [])
        records.append(rec(q.id, {k: values[k] for k in keys}, hid="H903"))
    s = score(truths, records, SPECS)
    assert (s.compared, s.matched, s.meets_bar) == (39, 39, True)
    # itemized_last_year was never asked about, so revealing it is a leak.
    assert [x["match"] for x in s.leaks] == ["itemized_last_year"]
    records[0].revealed["w2_employer_count"] = 5
    s = score(truths, records, SPECS)
    assert (s.matched, s.meets_bar) == (38, False)
    assert s.match_rate_without_recall_gaps is None


def test_volunteering_clients_dont_leak_by_revealing_more():
    s = score({"H901": truth("H901")}, [rec("q01", {"w2_employer_count": 1, "has_mortgage": False}, hid="H901")], SPECS)
    assert s.leaks == [] and s.matched == 2
    assert s.recall_gap_households == [] and s.match_rate_without_recall_gaps == 1.0
    s = score({"H901": truth("H901")}, [rec("q01", answer="My tips_income was zero.", hid="H901")], SPECS)
    assert [x["kind"] for x in s.leaks] == ["fact_key_in_text"]


def test_recall_gap_none_means_no_gap():
    t = truth("H902")
    assert fidelity.recall_gap_of(t) == "forgets_new_part_time_job_unless_asked_about_other_work"
    for no_gap in ("none", None, ""):
        t["persona"]["recall_gap"] = no_gap
        assert fidelity.recall_gap_of(t) is None
    s = score({"H902": t}, [rec("q01", {"w2_employer_count": 3})], SPECS)
    assert s.recall_gap_households == [] and s.match_rate_without_recall_gaps == 1.0


def test_open_question_may_bring_up_facts_that_changed():
    records = [rec("q10", {"business_app_payments": True, "interest_income": True})]
    s = score({"H902": truth("H902")}, records, SPECS)
    assert [x["match"] for x in s.leaks] == ["interest_income"]


def test_null_follow_up_matches_null_truth():
    s = score({"H902": truth("H902")}, [rec("q09", {"ev_purchased": False, "ev_acquired_date": None})], SPECS)
    assert (s.compared, s.matched, s.mismatches) == (2, 2, [])
    s = score({"H902": truth("H902")}, [rec("q03", {"k1_count": None})], SPECS)
    assert s.mismatches[0]["truth"] == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"answer": None, "error": "unusable reply twice", "retried": True},
        {"anomalies": [{"kind": "missing_revealed"}]},
        {"anomalies": [{"kind": "revealed_not_object", "value": [1]}]},
    ],
)
def test_unscored_answers_fail_the_bar_whatever_the_match_rate(bad):
    records = [rec("q01", {"w2_employer_count": 3})] + [rec(q.id, **bad) for q in QUESTIONS[1:]]
    s = score({"H902": truth("H902")}, records, SPECS)
    assert (s.compared, s.match_rate, s.answers) == (1, 1.0, 10)
    assert s.meets_bar is False and len(s.unscored) == 9
    assert {u["question_id"] for u in s.unscored} == {q.id for q in QUESTIONS[1:]}
    clean = score({"H902": truth("H902")}, records[:1], SPECS)
    assert clean.meets_bar is True and clean.unscored == []


def test_coverage_counts_asked_facts_that_have_a_truth():
    bad_year = {"kind": "uncoercible", "key": "clean_energy_install_year", "value": "last spring", "error": "x"}
    records = [
        rec("q01", {"w2_employer_count": 3, "tips_income": False}),
        rec("q09", {"clean_energy_installed": True}, anomalies=[bad_year]),
        rec("q04", answer=None, error="unusable reply twice"),
    ]
    s = score({"H902": truth("H902")}, records, SPECS)
    # q01 asks 4 facts and q04 asks 2; q09 asks 7, of which 2 are null follow-ups.
    assert (s.asked_facts, s.asked_facts_revealed) == (11, 4)
    assert s.coverage == pytest.approx(4 / 11)


def test_off_topic_reveals_are_leaks_even_after_an_earlier_question():
    records = [
        rec("q01", {"w2_employer_count": 2}),
        rec("q03", {}),
        rec("q04", {"w2_employer_count": 3, "retirement_distribution": False}, "My new part-time job has a plan."),
        rec("q05", {"w2_employer_count": 3}),
        rec("q06", {"interest_income": True, "has_mortgage": True}),
    ]
    s = score({"H902": truth("H902")}, records, SPECS)
    assert [(x["question_id"], x["kind"], x["match"], x.get("earlier_question")) for x in s.leaks] == [
        ("q04", "changed_off_topic", "w2_employer_count", "q01"),
        ("q05", "restated_fact", "w2_employer_count", "q04"),
        # q03 asked about interest but the answer didn't reveal it, so q06 tells the agent something new.
        ("q06", "unasked_fact", "interest_income", None),
    ]
    assert fidelity._leak_detail(s.leaks[0]) == (
        "2 at q01, now 3; recall gap: forgets_new_part_time_job_unless_asked_about_other_work"
    )
    assert fidelity._leak_detail(s.leaks[1]) == "same as q04"
    assert (s.mismatches[0]["corrected_by"], s.mismatches[0]["corrected_on_topic"]) == ("q04", False)
    assert fidelity._corrected(s.mismatches[0]) == "q04, off topic"
    # The open question may bring up a fact that changed, so a correction there is no leak.
    s = score({"H902": truth("H902")}, [rec("q01", {"w2_employer_count": 2}), rec("q10", {"w2_employer_count": 3})], SPECS)
    assert s.leaks == [] and (s.mismatches[0]["corrected_by"], s.mismatches[0]["corrected_on_topic"]) == ("q10", True)
    assert fidelity._corrected(s.mismatches[0]) == "q10"


def uncoercible(key, value):
    return {"kind": "uncoercible", "key": key, "value": value, "error": "x"}


def test_uncoercible_reveals_get_the_same_leak_check():
    records = [
        rec("q01", anomalies=[uncoercible("self_employment", "yes"), uncoercible("tips_income", "sometimes")]),
        rec("q02", {"self_employment": True}),
        rec("q03", anomalies=[uncoercible("self_employment", "kind of")]),
    ]
    s = score({"H902": truth("H902")}, records, SPECS)
    assert [(x["question_id"], x["kind"], x["match"]) for x in s.leaks] == [
        ("q01", "unasked_fact", "self_employment"),
        ("q03", "changed_off_topic", "self_employment"),
    ]
    assert s.leaks[1]["value"] == "kind of" and s.leaks[1]["earlier_value"] is True
    volunteer = score(
        {"H901": truth("H901")}, [rec("q01", hid="H901", anomalies=[uncoercible("self_employment", "yes")])], SPECS
    )
    assert volunteer.leaks == []


def test_review_file_shows_every_leak_code_flagged_on_an_answer():
    record = rec("q03", {"interest_income": True, "has_mortgage": True}, "Some interest, and we pay a mortgage.")
    s = score({"H902": truth("H902")}, [record], SPECS)
    review = fidelity.render_review([record], {"H902": truth("H902")}, SPECS, dry_run=True, leaks=s.leaks)
    assert "Code flags: unasked_fact has_mortgage" in review


# ---- fidelity: sampling ----------------------------------------------------------


def big_splits():
    ids = [f"H{i:03d}" for i in range(1, 101)]
    splits = {"train": ids[:20], "dev": ids[20:60] + ["H100"], "test": ids[60:], "ratio": "20/40/40"}
    tiers = {h: ("simple" if i % 10 < 3 else "medium" if i % 10 < 8 else "complex") for i, h in enumerate(ids)}
    return splits, tiers


def test_household_choice_is_deterministic_stratified_and_never_test():
    splits, tiers = big_splits()
    chosen = choose_households(splits, tiers)
    assert chosen == choose_households(dict(splits), dict(tiers))
    assert len(chosen) == 20 and chosen == sorted(chosen)
    assert not set(chosen) & set(splits["test"])
    pool = fidelity.pool_ids(splits)
    assert "H100" not in pool and len(pool) == 60
    counts = {t: sum(1 for h in chosen if tiers[h] == t) for t in ("simple", "medium", "complex")}
    assert counts == {"simple": 6, "medium": 10, "complex": 4}
    assert choose_households(splits, tiers, seed=fidelity.SEED + 1) != chosen
    assert choose_households(splits, None, n=5) == choose_households(splits, {}, n=5)
    assert choose_households({"train": ["H002", "H001"], "dev": []}, n=20) == ["H001", "H002"]


@pytest.mark.parametrize("n", [0, -1, -5, 2.5, True, "3"])
def test_household_count_must_be_a_positive_whole_number(n):
    splits, tiers = big_splits()
    with pytest.raises(ValueError, match="at least 1"):
        choose_households(splits, tiers, n=n)


@pytest.mark.parametrize("bad", ["0", "-5", "two"])
def test_cli_rejects_a_bad_household_count(tmp_path, capsys, bad):
    with pytest.raises(SystemExit) as caught:
        fidelity.main(["--dry-run", "--data-dir", str(tmp_path), "--households", bad])
    assert caught.value.code == 2
    assert "--households" in capsys.readouterr().err


def test_review_sample_is_deterministic_and_spread_over_questions():
    households = [f"H{i:03d}" for i in range(1, 21)]
    records = [rec(q.id, hid=h) for h in households for q in QUESTIONS]
    records[0] = rec("q01", hid="H001", answer=None, error="boom")
    sample = choose_answers_for_review(records)
    assert len(sample) == 30
    assert sample == choose_answers_for_review(list(reversed(records)))
    per_question = {q.id: sum(1 for r in sample if r.question_id == q.id) for q in QUESTIONS}
    assert set(per_question.values()) == {3}
    assert all(r.error is None for r in sample)
    assert choose_answers_for_review(records, seed=1) != sample
    small = choose_answers_for_review(records[:25], n=30)
    assert len(small) == 24


def test_estimate_stays_under_the_run_cap():
    prices = load_prices()
    estimate = estimate_cost_usd([truth("H903")] * 20, SPECS, "claude-haiku-5-5", prices)
    assert 0 < estimate < 0.50
    shorter = estimate_cost_usd([truth("H903")] * 20, SPECS, "claude-haiku-5-5", prices, max_tokens=300)
    assert shorter < estimate


# ---- spend guard -------------------------------------------------------------------

PRICES = load_prices()
HAIKU = "claude-haiku-5-5"


def request(content="Q?", max_tokens=1024):
    return {
        "model": HAIKU,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": "x" * 4000, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
    }


def test_call_bound_covers_every_prompt_byte_and_max_tokens():
    kwargs = request("é" * 500, max_tokens=1000)
    assert budget.prompt_tokens_bound(kwargs) >= 4000 + 1000
    bound = budget.call_bound_usd(kwargs, HAIKU, PRICES)
    for prompt_rate in ("input_tokens", "cache_write_tokens"):
        assert bound >= cost_usd(HAIKU, {prompt_rate: 5000, "output_tokens": 1000}, PRICES)


@pytest.mark.anyio
async def test_budgeted_client_stops_before_a_call_could_pass_the_limit():
    inner = ScriptedClient([reply("Yes."), reply("No.")], usage=FakeUsage(input_tokens=500, output_tokens=1000))
    one = budget.call_bound_usd(request(), HAIKU, PRICES)
    guard = BudgetedClient(inner, model=HAIKU, prices=PRICES, limit_usd=one * 1.5)
    await guard.messages.create(**request())
    assert guard.spent_usd == pytest.approx(cost_usd(HAIKU, {"input_tokens": 500, "output_tokens": 1000}, PRICES))
    with pytest.raises(BudgetExceeded):
        await guard.messages.create(**request())
    assert len(inner.calls) == 1 and guard.calls == 1


class HeldClient:
    """Holds every call open until released, to test calls in flight."""

    def __init__(self, fail=False):
        self.release = asyncio.Event()
        self.fail = fail
        self.calls = 0
        self.messages = self

    async def create(self, **kwargs):
        self.calls += 1
        await self.release.wait()
        if self.fail:
            raise RuntimeError("simulated API failure")
        return text_response(reply("Yes."), FakeUsage(input_tokens=10, output_tokens=10))


@pytest.mark.anyio
async def test_budgeted_client_counts_calls_in_flight():
    held = HeldClient()
    one = budget.call_bound_usd(request(), HAIKU, PRICES)
    guard = BudgetedClient(held, model=HAIKU, prices=PRICES, limit_usd=one * 1.5)
    first = asyncio.ensure_future(guard.messages.create(**request()))
    await asyncio.sleep(0)
    with pytest.raises(BudgetExceeded):
        await guard.messages.create(**request())
    held.release.set()
    await first
    assert held.calls == 1 and guard.in_flight_usd == pytest.approx(0)


@pytest.mark.anyio
async def test_budgeted_client_counts_a_failed_call_at_its_bound():
    held = HeldClient(fail=True)
    held.release.set()
    guard = BudgetedClient(held, model=HAIKU, prices=PRICES, limit_usd=1.0)
    with pytest.raises(RuntimeError, match="simulated"):
        await guard.messages.create(**request())
    assert guard.spent_usd == pytest.approx(budget.call_bound_usd(request(), HAIKU, PRICES), abs=1e-6)
    assert guard.in_flight_usd == pytest.approx(0) and guard.calls == 0


# ---- fidelity: end to end ----------------------------------------------------------


def make_data_dir(root, n_train=8, n_dev=16, n_test=6):
    """A data folder of synthetic households cloned from the three fixtures."""
    bases = [truth("H901"), truth("H902"), truth("H903")]
    splits = {"train": [], "dev": [], "test": []}
    (root / "truth").mkdir(parents=True)
    n = 0
    for split, count in (("train", n_train), ("dev", n_dev), ("test", n_test)):
        for _ in range(count):
            n += 1
            hid = f"H8{n:02d}"
            t = json.loads(json.dumps(bases[n % 3]))
            t["household_id"], t["split"] = hid, split
            (root / "truth" / f"{hid}.json").write_text(json.dumps(t))
            splits[split].append(hid)
    (root / "splits.json").write_text(json.dumps(splits))
    return root, splits


def test_dry_run_cli_writes_both_reports(tmp_path, capsys):
    data, splits = make_data_dir(tmp_path / "data")
    out = tmp_path / "out"
    code = fidelity.main(["--dry-run", "--data-dir", str(data), "--out-dir", str(out)])
    assert code == 0
    report = (out / fidelity.REPORT_PATH).read_text()
    review = (out / fidelity.REVIEW_PATH).read_text()
    answers = json.loads((out / fidelity.ANSWERS_PATH).read_text())
    assert "DRY RUN" in report and "DRY RUN" in review
    assert "100.0%" in report and "**Met.**" in report and "Answers scored: 200 of 200" in report
    assert review.count("\n## ") == 30
    other_lines = [x for x in review.splitlines() if x.startswith("Other facts with a value")]
    assert len(other_lines) == 30 and not any(" null" in x for x in other_lines)
    assert len(answers["records"]) == 200 and answers["dry_run"] is True and answers["model"] is None
    for test_id in splits["test"]:
        assert test_id not in report and test_id not in review
    for text in (report, review):
        assert chr(0x2014) not in text and chr(0x2013) not in text
    printed = capsys.readouterr().out
    assert "bar met: True" in printed and "dry_run: True" in printed


@pytest.mark.anyio
async def test_dry_run_spends_nothing_and_defaults_to_a_temp_folder(tmp_path, monkeypatch):
    data, _ = make_data_dir(tmp_path / "data")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    ledger = tmp_path / "spend.jsonl"

    def no_settings():
        raise AssertionError("a dry run must not load the key")

    outcome = await fidelity.run_check(data_dir=data, dry_run=True, settings_loader=no_settings, ledger_path=ledger)
    assert not ledger.exists()
    assert outcome.report_path.exists() and REPO not in outcome.report_path.parents
    assert tmp_path / "tmp" in outcome.report_path.parents
    assert outcome.score.match_rate == 1.0 and outcome.score.leaks == [] and outcome.score.anomalies == []
    assert outcome.usage.cache_read_tokens > 0 and outcome.usage.cache_write_tokens > 0
    assert outcome.cost_usd > 0 and outcome.estimate_usd > outcome.cost_usd


class LiveStandIn:
    """Plays the live client in tests: checks the ledger before every call."""

    def __init__(self, ledger_path, fail_on_call=None, text_for=None, usage_for=None):
        self.ledger_path = ledger_path
        self.fail_on_call = fail_on_call
        self.text_for = text_for or (lambda n: reply("No."))
        self.usage_for = usage_for or (lambda kwargs: FakeUsage(input_tokens=900, output_tokens=40))
        self.calls = 0
        self.requests = []
        self.messages = self

    async def create(self, **kwargs):
        self.calls += 1
        self.requests.append(kwargs)
        kinds = [json.loads(x)["kind"] for x in self.ledger_path.read_text().splitlines()]
        assert kinds == ["reserve"], "every call needs a reservation first and no settle yet"
        if self.calls == self.fail_on_call:
            raise RuntimeError("simulated API failure")
        return text_response(self.text_for(self.calls), self.usage_for(kwargs))


def live_settings(run_cap=0.5):
    return lambda: HarnessSettings(api_key=PLACEHOLDER_KEY, spend_cap_usd=5.0, run_cap_usd=run_cap)


@pytest.mark.anyio
async def test_live_mode_reserves_before_any_call_and_settles_after(tmp_path):
    data, _ = make_data_dir(tmp_path / "data")
    ledger = tmp_path / "spend.jsonl"
    stand_in = LiveStandIn(ledger)

    def factory(key):
        assert key == PLACEHOLDER_KEY
        return stand_in

    outcome = await fidelity.run_check(
        data_dir=data, out_dir=tmp_path / "out", dry_run=False, households=2,
        settings_loader=live_settings(), client_factory=factory, ledger_path=ledger,
    )
    assert stand_in.calls == 20
    lines = [json.loads(x) for x in ledger.read_text().splitlines()]
    assert [x["kind"] for x in lines] == ["reserve", "settle"]
    assert lines[0]["reserved_usd"] == pytest.approx(outcome.estimate_usd)
    expected = cost_usd("claude-haiku-5-5", {"input_tokens": 18000, "output_tokens": 800}, load_prices())
    assert lines[1]["actual_usd"] == pytest.approx(expected) == pytest.approx(outcome.cost_usd)
    report = outcome.report_path.read_text()
    assert "DRY RUN" not in report and "claude-haiku-5-5" in report


@pytest.mark.anyio
async def test_live_mode_settles_even_when_a_call_fails(tmp_path):
    data, _ = make_data_dir(tmp_path / "data")
    ledger = tmp_path / "spend.jsonl"
    stand_in = LiveStandIn(ledger, fail_on_call=3)
    with pytest.raises(RuntimeError, match="simulated API failure"):
        await fidelity.run_check(
            data_dir=data, out_dir=tmp_path / "out", dry_run=False, households=1, concurrency=1,
            settings_loader=live_settings(), client_factory=lambda key: stand_in, ledger_path=ledger,
        )
    lines = [json.loads(x) for x in ledger.read_text().splitlines()]
    assert [x["kind"] for x in lines] == ["reserve", "settle"]
    # Two calls with known usage, plus the failed one at its upper bound, since its usage is unknown.
    known = cost_usd("claude-haiku-5-5", {"input_tokens": 1800, "output_tokens": 80}, load_prices())
    failed = budget.call_bound_usd(stand_in.requests[2], "claude-haiku-5-5", load_prices())
    assert lines[1]["actual_usd"] == pytest.approx(known + failed, abs=2e-6)
    assert not (tmp_path / "out").exists()


@pytest.mark.anyio
async def test_live_mode_stops_before_spend_could_pass_the_reservation(tmp_path):
    data, _ = make_data_dir(tmp_path / "data")
    ledger = tmp_path / "spend.jsonl"
    # Every answer needs a retry and every call uses all of max_tokens, which is more than the estimate.
    stand_in = LiveStandIn(
        ledger,
        text_for=lambda n: "not json" if n % 2 else reply("No."),
        usage_for=lambda kwargs: FakeUsage(input_tokens=1500, output_tokens=kwargs["max_tokens"]),
    )
    with pytest.raises(BudgetExceeded):
        await fidelity.run_check(
            data_dir=data, out_dir=tmp_path / "out", dry_run=False, households=1,
            settings_loader=live_settings(), client_factory=lambda key: stand_in, ledger_path=ledger,
        )
    lines = [json.loads(x) for x in ledger.read_text().splitlines()]
    assert [x["kind"] for x in lines] == ["reserve", "settle"]
    per_call = cost_usd("claude-haiku-5-5", {"input_tokens": 1500, "output_tokens": 1024}, load_prices())
    assert 0 < stand_in.calls < 20
    assert lines[1]["actual_usd"] == pytest.approx(per_call * stand_in.calls, abs=2e-6)
    assert lines[1]["actual_usd"] <= lines[0]["reserved_usd"]
    assert not (tmp_path / "out").exists()


def test_cli_reports_a_spend_limit_stop(monkeypatch, capsys):
    async def stopped(**kwargs):
        raise BudgetExceeded("the next call could cost too much")

    monkeypatch.setattr(fidelity, "run_check", stopped)
    assert fidelity.main(["--dry-run"]) == 3
    assert "stopped by a spend limit" in capsys.readouterr().err


@pytest.mark.anyio
async def test_live_mode_stops_at_the_run_cap_before_any_call(tmp_path):
    data, _ = make_data_dir(tmp_path / "data")

    def factory(key):
        raise AssertionError("no client may be built past the cap")

    with pytest.raises(RunCapExceeded):
        await fidelity.run_check(
            data_dir=data, out_dir=tmp_path / "out", dry_run=False,
            settings_loader=live_settings(run_cap=0.0001), client_factory=factory,
            ledger_path=tmp_path / "spend.jsonl",
        )


def test_cli_explains_missing_data(tmp_path, capsys):
    assert fidelity.main(["--dry-run", "--data-dir", str(tmp_path / "no-data")]) == 2
    assert "generate the households" in capsys.readouterr().err
