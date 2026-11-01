"""Block 4: the client simulator and its fidelity check, offline with fake clients."""

import ast
import asyncio
import builtins
import io
import json
import pathlib
import tempfile
from collections import Counter
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
        "\n\nEvery fact your answer mentions must agree with Your facts, unless your recall_gap makes you forget "
        "it. A fact that is false or 0 in Your facts is a no."
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
    assert head.splitlines()[-4].startswith("Your persona: ")
    assert head.splitlines()[-3].startswith("Your recall_gap in plain words: You forget the new part-time job")
    assert head.splitlines()[-2:] == ["", simulator.FACTS_RULE]
    # The new job is the change H024's recall gap hides, so the list leaves it out.
    assert t["changed_facts"][0] == "w2_employer_count" and change_section(prompt) == [
        "New this year: unemployment benefits.",
        "New this year: an electric vehicle purchase.",
    ]
    assert t["persona"]["label"] not in prompt and "label" not in prompt and "a new job" not in prompt
    assert "Last year only" not in prompt and "Employers paying wages" not in prompt
    crypto = ALL_TRUTHS["H020"]
    assert crypto["changed_facts"] == ["w2_employer_count", "digital_asset_transactions"]
    prompt = build_system_prompt(crypto, SPECS)
    assert change_section(prompt) == ["Employers paying wages: 1 last year, 2 this year."]
    assert "Once one does, you remember it, including that it is new this year, and answer truthfully" in prompt
    quiet = ALL_TRUTHS["H043"]
    assert quiet["changed_facts"] == [] and change_section(build_system_prompt(quiet, SPECS)) == [
        "Nothing changed since last year."]
    forgot_the_only_change = ALL_TRUTHS["H030"]
    assert forgot_the_only_change["changed_facts"] == ["digital_asset_transactions"]
    assert change_section(build_system_prompt(forgot_the_only_change, SPECS)) == ["Nothing changed since last year."]


def test_every_prompt_has_one_rule_that_answers_agree_with_the_facts():
    # The first live check found answers that said charity and overtime were true when the facts said false,
    # and a partnership share of 1 as none, so one rule follows the PRD part and the recall gap sentence.
    template = simulator.SYSTEM_PROMPT_TEMPLATE
    assert "Your facts: " in template and simulator.FACTS_RULE not in template
    assert "false or 0" in simulator.FACTS_RULE and fidelity.text_leaks(simulator.FACTS_RULE, SPECS) == []
    for t in [*ALL_TRUTHS.values(), truth("H901"), truth("H902"), truth("H903")]:
        prompt = build_system_prompt(t, SPECS)
        assert prompt.count(simulator.FACTS_RULE) == 1, t["household_id"]
        head, tail = prompt.split("\n\n" + simulator.FACTS_RULE)
        assert head.splitlines()[-1].startswith(("Your persona: ", simulator.RECALL_GAP_LABEL)), t["household_id"]
        assert tail == "" or tail.startswith("\n\n" + simulator.CHANGES_INTRO + "\n- "), t["household_id"]


def test_every_remembered_change_of_every_household_is_in_its_prompt():
    for hid, t in ALL_TRUTHS.items():
        prompt = build_system_prompt(t, SPECS)
        lines = change_section(prompt)
        prior, current = t["prior_year_facts"], t["true_facts"]
        changed = [k for k in t["changed_facts"] if k not in simulator.hidden_facts(t["persona"]["recall_gap"])]
        assert changed == simulator.remembered_changes(t), hid
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


def test_no_gap_holder_is_told_the_change_its_gap_hides():
    told = 0
    for hid, t in ALL_TRUTHS.items():
        slug = t["persona"]["recall_gap"]
        if slug not in gen.RECALL_GAPS:
            continue
        hidden = simulator.hidden_facts(slug)
        assert hidden[0] == gen.RECALL_GAPS[slug][0], slug
        lines = change_section(build_system_prompt(t, SPECS))
        names = [simulator.COUNT_CHANGES.get(k) or simulator.CHANGE_NOUNS[k] for k in hidden]
        assert not [x for x in lines for name in names if name in x], (hid, slug, lines)
        if set(hidden) & set(t["changed_facts"]):
            told += 1
            # Without the gap the same truth lists the hidden change, so the gap is what leaves it out.
            ungapped = simulator.change_lines({**t, "persona": {}}, SPECS)
            assert [x for x in ungapped for name in names if name in x], hid
    # 30 of the 45 holders have a hidden fact that changed since last year.
    assert told == 30


def test_change_lines_need_known_keys_and_last_years_values():
    t = ALL_TRUTHS["H024"]
    assert simulator.change_lines({"true_facts": t["true_facts"], "changed_facts": t["changed_facts"]}, SPECS) is None
    with pytest.raises(ValueError, match="unknown changed fact keys"):
        build_system_prompt({**t, "changed_facts": ["made_up_key"]}, SPECS)
    with pytest.raises(ValueError, match="must be an object"):
        build_system_prompt({**t, "prior_year_facts": ["not", "an", "object"]}, SPECS)
    with pytest.raises(ValueError, match="lacks a value"):
        build_system_prompt({**t, "prior_year_facts": {}}, SPECS)


LAST_YEAR_ONLY = "Last year only: "


def test_ended_facts_are_the_last_year_only_lines():
    for hid, t in ALL_TRUTHS.items():
        ended = simulator.ended_facts(t, SPECS)
        lines = [x for x in change_section(build_system_prompt(t, SPECS)) if x.startswith(LAST_YEAR_ONLY)]
        assert bool(ended) == bool(lines), hid
        for key in ended:
            assert key in simulator.remembered_changes(t) and not SPECS[key].is_follow_up, (hid, key)
            assert key not in simulator.COUNT_CHANGES and key != "filing_status", (hid, key)
            assert t["prior_year_facts"][key] and not t["true_facts"][key], (hid, key)
            if key == "business_app_payments" and "self_employment" in ended:
                # Ended app payments ride on the ended business's line.
                noun = simulator.CHANGE_NOUNS["self_employment"] + ", with some customers paying through an app"
            else:
                noun = simulator.CHANGE_NOUNS[key]
            assert any(x.startswith(LAST_YEAR_ONLY + noun) for x in lines), (hid, key)
    assert simulator.ended_facts(ALL_TRUTHS["H011"], SPECS) == ["tuition_paid"]
    business_and_tuition = ["self_employment", "business_app_payments", "tuition_paid"]
    assert simulator.ended_facts(ALL_TRUTHS["H022"], SPECS) == business_and_tuition
    assert simulator.ended_facts(ALL_TRUTHS["H032"], SPECS) == ["marketplace_coverage", "tuition_paid"]
    assert simulator.ended_facts(ALL_TRUTHS["H015"], SPECS) == ["self_employment"]
    # A count that fell is a line with both numbers, and a hand-made truth has no change list.
    assert "Employers paying wages: 3 last year, 2 this year." in change_section(
        build_system_prompt(ALL_TRUTHS["H010"], SPECS))
    assert simulator.ended_facts(ALL_TRUTHS["H010"], SPECS) == [] and simulator.ended_facts(truth("H902"), SPECS) == []
    # A gap that hides the only change leaves nothing that ended.
    side_gig = "forgets_side_gig_unless_asked_about_freelance_work"
    forgot = {**ALL_TRUTHS["H015"], "persona": {**ALL_TRUTHS["H015"]["persona"], "recall_gap": side_gig}}
    assert simulator.ended_facts(forgot, SPECS) == []
    assert change_section(build_system_prompt(forgot, SPECS)) == ["Nothing changed since last year."]
    # Over train and dev, the households the fidelity check can use: 9 with 11 such lines.
    pool = fidelity.pool_ids(fidelity.load_splits(REPO / "data"))
    ended = {hid: simulator.ended_facts(ALL_TRUTHS[hid], SPECS) for hid in pool}
    ended = {hid: keys for hid, keys in ended.items() if keys}
    assert sorted(ended) == ["H011", "H015", "H022", "H032", "H059", "H065", "H067", "H085", "H091"]
    lines = [x for hid in ended for x in change_section(build_system_prompt(ALL_TRUTHS[hid], SPECS))
             if x.startswith(LAST_YEAR_ONLY)]
    assert len(lines) == 11
    assert Counter(k for keys in ended.values() for k in keys) == {
        "tuition_paid": 5, "self_employment": 3, "marketplace_coverage": 3, "business_app_payments": 2}


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
            key = gen.RECALL_GAPS[slug][0]
            new = not t["prior_year_facts"][key] and bool(t["true_facts"][key])
            assert ("including that it is new this year" in sentence) == new, (t["household_id"], slug)


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
    real = json.loads(json.dumps(ALL_TRUTHS["H020"]))
    sim = ClientSimulator(real, ScriptedClient([]), specs=SPECS)
    before = sim.system_prompt
    real["prior_year_facts"]["w2_employer_count"] = 0
    real["changed_facts"].append("sold_home")
    real["persona"]["recall_gap"] = "none"
    assert sim.system_prompt == before
    assert change_section(before) == ["Employers paying wages: 1 last year, 2 this year."]


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
    sim, client = make_sim(['```json\n{"answer": "Yes.", "revealed": {"interest_income": true}}\n```'])
    result = await sim.answer("Did you earn interest?")
    assert result.answer == "Yes." and result.revealed == {"interest_income": True}
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
            "k1_count": 0,
            "dependents_count": "--3",
            "ev_acquired_date": None,
        },
    }
    sim, _ = make_sim([json.dumps(raw), reply("Yes."), json.dumps({"answer": "Yes.", "revealed": [1]})])
    result = await sim.answer("Tell me about work.")
    assert result.revealed == {"self_employment": True, "k1_count": 0, "ev_acquired_date": None}
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
    second = reply("Yes, a part-time job too, so three, and my side business.",
                   {"w2_employer_count": 3, "self_employment": True})
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


# ---- the contradiction retry ---------------------------------------------------------


TIPS_AND_SHARE = "Any interest, tips, or partnership income?"


@pytest.mark.anyio
async def test_a_contradiction_goes_back_once_and_the_corrected_answer_replaces_the_first():
    # H902 has bank interest, no tips, and no partnership share.
    first = reply("Some interest, tips at work, and a share in my cousin's partnership.",
                  {"interest_income": True, "tips_income": True, "k1_count": 1})
    fixed = reply("Some interest from the bank, but no tips and no partnership share.",
                  {"interest_income": True, "tips_income": False, "k1_count": 0})
    sim, client = make_sim([first, fixed, reply("No.")], usage=FakeUsage(input_tokens=100, output_tokens=20))
    result = await sim.answer(TIPS_AND_SHARE)
    assert result.answer == "Some interest from the bank, but no tips and no partnership share."
    assert result.revealed == {"interest_income": True, "tips_income": False, "k1_count": 0}
    assert result.raw_text == fixed and result.raw_revealed == json.loads(fixed)["revealed"]
    assert result.corrected == [{"key": "tips_income", "first": True, "final": False},
                                {"key": "k1_count", "first": 1, "final": 0}]
    assert result.unresolved == [] and result.retried is False and result.retry_reason is None
    # The retry is the first answer, then a turn that names only the facts it got wrong.
    retry = client.calls[1]["messages"]
    assert retry[:2] == [{"role": "user", "content": TIPS_AND_SHARE}, {"role": "assistant", "content": first}]
    assert retry[2] == {"role": "user", "content": (
        "Your last answer said tips_income is true and k1_count is 1, but Your facts say tips_income is false and "
        "k1_count is 0. Answer the same question again, truthfully, using only Your facts. Reply with only the "
        "JSON object, as before.")}
    assert len(retry) == 3 and client.calls[1]["system"] == client.calls[0]["system"]
    # Both calls count, and only the corrected answer stays in the conversation.
    assert result.usage == Usage(input_tokens=200, output_tokens=40) and sim.calls == 2
    assert sim.history == [{"role": "user", "content": TIPS_AND_SHARE}, {"role": "assistant", "content": fixed}]
    await sim.answer("Anything else?")
    assert client.calls[2]["messages"] == [*sim.history[:2], {"role": "user", "content": "Anything else?"}]


@pytest.mark.anyio
async def test_an_answer_that_still_contradicts_after_the_retry_is_kept_and_unresolved():
    first = reply("Yes, I gave to charity and got tips.", {"charitable_giving": True, "tips_income": True})
    again = reply("I gave a little to charity, and I had overtime.",
                  {"charitable_giving": True, "tips_income": False, "overtime_pay": True})
    sim, client = make_sim([first, again])
    result = await sim.answer("Did you give to charity, or get tips?")
    assert len(client.calls) == 2, "one contradiction retry per question"
    assert result.answer == "I gave a little to charity, and I had overtime." and result.raw_text == again
    assert result.revealed == {"charitable_giving": True, "tips_income": False, "overtime_pay": True}
    # tips_income is fixed; charitable_giving is still wrong, and overtime_pay is newly wrong, so it has no first value.
    assert result.corrected == [{"key": "tips_income", "first": True, "final": False}]
    assert result.unresolved == [{"key": "charitable_giving", "first": True, "final": True},
                                 {"key": "overtime_pay", "final": True}]
    assert sim.history[-1] == {"role": "assistant", "content": again}


@pytest.mark.anyio
async def test_an_unusable_reply_to_the_retry_keeps_the_first_answer_unresolved():
    first = reply("Yes, I got tips.", {"tips_income": True})
    sim, client = make_sim([first, "Sorry, no tips after all."])
    result = await sim.answer("Did you get tips?")
    assert len(client.calls) == 2, "the retry's reply is not retried again"
    assert result.answer == "Yes, I got tips." and result.raw_text == first and result.revealed == {"tips_income": True}
    assert result.unresolved == [{"key": "tips_income", "first": True, "final": True,
                                  "retry_error": "the reply is not valid JSON"}]
    assert result.corrected == [] and result.retried is False
    assert sim.history == [{"role": "user", "content": "Did you get tips?"}, {"role": "assistant", "content": first}]


@pytest.mark.anyio
async def test_an_unusable_first_reply_and_a_contradiction_each_get_their_own_retry():
    usable = reply("Yes, tips.", {"tips_income": True})
    sim, client = make_sim(["Sure, I had tips.", usable, reply("No tips.", {"tips_income": False})])
    result = await sim.answer("Did you get tips?")
    assert len(client.calls) == 3 and result.retried is True and "not valid JSON" in result.retry_reason
    assert result.answer == "No tips." and result.corrected == [{"key": "tips_income", "first": True, "final": False}]
    # The contradiction retry follows the usable reply; the unusable one stays out of it.
    resent = client.calls[2]["messages"]
    assert [m["content"] for m in resent[:2]] == ["Did you get tips?", usable] and len(resent) == 3
    assert resent[2]["content"].startswith("Your last answer said tips_income is true, but Your facts say")


@pytest.mark.anyio
async def test_a_fact_the_recall_gap_hides_is_never_sent_back():
    # H902 forgets its new part-time job, so 2 employers for 3 is the gap at work.
    sim, client = make_sim([reply("Just the two jobs.", {"w2_employer_count": 2, "tips_income": False})])
    result = await sim.answer("How many employers paid you wages?")
    assert result.revealed == {"w2_employer_count": 2, "tips_income": False} and len(client.calls) == 1
    assert result.corrected == result.unresolved == []
    side_gig = ALL_TRUTHS["H087"]
    assert simulator.hidden_facts(side_gig["persona"]["recall_gap"]) == ("self_employment", "business_app_payments")
    assert side_gig["true_facts"]["self_employment"] and side_gig["true_facts"]["business_app_payments"]
    client = ScriptedClient([reply("No side work, just my job.", {"self_employment": False,
                                                                  "business_app_payments": False})])
    result = await ClientSimulator(side_gig, client, specs=SPECS).answer("Any side work?")
    assert len(client.calls) == 1 and result.corrected == result.unresolved == []


@pytest.mark.anyio
async def test_an_uncoercible_value_or_unknown_key_is_an_anomaly_not_a_contradiction():
    raw = {"answer": "Maybe some tips.", "revealed": {"tips_income": "maybe", "k1_count": "--3", "made_up_key": True}}
    sim, client = make_sim([json.dumps(raw)])
    result = await sim.answer("Did you get tips?")
    assert len(client.calls) == 1 and result.revealed == {}
    assert {(a["kind"], a["key"]) for a in result.anomalies} == {
        ("uncoercible", "tips_income"), ("uncoercible", "k1_count"), ("unknown_key", "made_up_key")}
    assert result.corrected == result.unresolved == []


def test_contradictions_compare_coerced_values_as_the_scorer_does():
    t = truth("H902")
    values = simulator.true_values(t["true_facts"], SPECS)
    assert values == fidelity.truth_values(t, SPECS)
    revealed, anomalies = simulator.check_revealed({
        "interest_income": "true", "w2_employer_count": "2", "ev_acquired_date": None, "tips_income": None,
        "k1_count": 0, "filing_status": "single", "clean_energy_install_year": "2026"}, SPECS)
    assert anomalies == []
    hidden = simulator.hidden_facts(t["persona"]["recall_gap"])
    # A null for a follow-up that doesn't apply matches; a null for a fact that is false is wrong, as in the scorer.
    assert simulator.contradictions(revealed, values, hidden) == {"tips_income": None, "filing_status": "single"}
    assert simulator.contradictions(revealed, values) == {
        "w2_employer_count": 2, "tips_income": None, "filing_status": "single"}
    note = simulator.contradiction_note({"tips_income": None, "ev_acquired_date": "2026-03-02", "k1_count": 2}, values)
    assert note.startswith(
        'Your last answer said tips_income does not apply to you, ev_acquired_date is "2026-03-02", and k1_count is '
        "2, but Your facts say tips_income is false, ev_acquired_date does not apply to you, and k1_count is 0. ")


@pytest.mark.anyio
async def test_the_contradiction_retry_is_a_guarded_call_like_any_other():
    sim, inner = make_sim([reply("Yes, I got tips.", {"tips_income": True}), reply("No tips.", {"tips_income": False})],
                          usage=FakeUsage(input_tokens=500, output_tokens=40))
    first = budget.call_bound_usd(sim._request([{"role": "user", "content": "Did you get tips?"}]), HAIKU, PRICES)
    # Room for the first call's bound, not for the retry's on top of what the first call cost.
    guard = BudgetedClient(inner, model=HAIKU, prices=PRICES, limit_usd=first)
    guarded = ClientSimulator(truth("H902"), guard, specs=SPECS)
    with pytest.raises(BudgetExceeded):
        await guarded.answer("Did you get tips?")
    assert len(inner.calls) == 1 and guard.calls == 1 and guarded.history == []
    roomy = BudgetedClient(ScriptedClient([reply("Yes, I got tips.", {"tips_income": True}),
                                           reply("No tips.", {"tips_income": False})],
                                          usage=FakeUsage(input_tokens=500, output_tokens=40)),
                           model=HAIKU, prices=PRICES, limit_usd=1.0)
    result = await ClientSimulator(truth("H902"), roomy, specs=SPECS).answer("Did you get tips?")
    assert result.corrected and roomy.calls == 2
    assert roomy.spent_usd == pytest.approx(cost_usd(HAIKU, result.usage.as_dict(), PRICES))


# ---- fidelity: questions and leaks ----------------------------------------------


def test_questions_cover_the_topics_in_plain_language():
    assert len(QUESTIONS) == 11
    assert len({q.id for q in QUESTIONS}) == 11
    assert [q.topic for q in QUESTIONS] == [
        "what changed", "jobs", "side work", "banks and investments", "retirement and Social Security",
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
    # The change question comes first, as the agent's does, and asks about no recall gap's topic.
    assert [q.id for q in QUESTIONS if q.asks_changes] == ["q00"] and QUESTIONS[0] is fidelity.CHANGE_QUESTION
    assert QUESTIONS[0].asks == () and "since last year" in QUESTIONS[0].text
    for _, _, topic in simulator.RECALL_GAP_WORDS.values():
        for word in topic.replace(",", "").split():
            assert word in ("or", "other") or word not in QUESTIONS[0].text.lower(), topic


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
    next(r for r in records if r.question_id == "q01").revealed["w2_employer_count"] = 5
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


GAP = "forgets_new_part_time_job_unless_asked_about_other_work"


def test_change_question_asks_each_remembered_change_and_counts_the_ones_left_out():
    t = truth("H902")
    # H902's new part-time job is the change its recall gap hides, so q00 asks about the other three.
    assert t["changed_facts"][0] == "w2_employer_count" and t["persona"]["recall_gap"] == GAP
    q00 = fidelity.CHANGE_QUESTION
    remembered = ("self_employment", "business_app_payments", "clean_energy_installed")
    assert fidelity.asked_keys(q00, t, SPECS) == remembered and fidelity.allowed_keys(q00, t) == set(remembered)
    assert fidelity.asked_keys(QUESTIONS[1], t, SPECS) == QUESTIONS[1].asks
    s = score({"H902": t}, [rec("q00", {"self_employment": True, "clean_energy_installed": True})], SPECS)
    assert (s.changes_asked, s.changes_named, s.broken_gaps, s.leaks) == (3, 2, [], [])
    assert s.missed_changes == [{"household_id": "H902", "question_id": "q00", "key": "business_app_payments",
                                 "truth": True}]
    assert s.change_coverage == pytest.approx(2 / 3) and (s.asked_facts, s.asked_facts_revealed) == (3, 2)
    # A false "nothing changed" leaves every remembered change out.
    s = score({"H902": t}, [rec("q00", {}, "Nothing really changed.")], SPECS)
    assert (s.changes_named, [m["key"] for m in s.missed_changes]) == (0, list(remembered))
    # An answer that failed twice is already unscored, so it adds no missed changes.
    s = score({"H902": t}, [rec("q00", answer=None, error="unusable reply twice", retried=True)], SPECS)
    assert (s.changes_asked, s.missed_changes, s.change_coverage) == (0, [], None)
    # A follow-up that changed may come up but isn't asked; a household with no change gets no q00 facts.
    t["changed_facts"] += ["ev_purchased", "ev_acquired_date"]
    assert fidelity.asked_keys(q00, t, SPECS) == (*remembered, "ev_purchased")
    assert "ev_acquired_date" in fidelity.allowed_keys(q00, t)
    assert fidelity.asked_keys(q00, truth("H901"), SPECS) == ()


def test_change_question_catches_a_recall_gap_that_breaks_on_it():
    t = truth("H902")
    # The forgetful count is a mismatch the gap explains, not a broken gap.
    s = score({"H902": t}, [rec("q00", {"w2_employer_count": 2})], SPECS)
    assert s.broken_gaps == [] and (s.mismatches[0]["key"], s.mismatches[0]["recall_gap"]) == ("w2_employer_count", GAP)
    s = score({"H902": t}, [rec("q00", {"w2_employer_count": 3, "self_employment": True})], SPECS)
    assert s.broken_gaps == [{"household_id": "H902", "question_id": "q00", "key": "w2_employer_count", "value": 3,
                              "recall_gap": GAP}]
    assert [(x["kind"], x["match"]) for x in s.leaks] == [("unasked_fact", "w2_employer_count")]
    # A client who volunteers can't leak, but its gap can still break, which is what the check is for.
    t["persona"]["volunteers_info"] = True
    s = score({"H902": t}, [rec("q00", {"w2_employer_count": 3})], SPECS)
    assert s.leaks == [] and [b["key"] for b in s.broken_gaps] == ["w2_employer_count"]
    # Only the change question asks about no gap's topic, so a later question is judged as before.
    s = score({"H902": t}, [rec("q01", {"w2_employer_count": 3})], SPECS)
    assert s.broken_gaps == [] and s.changes_asked == 0
    side_gig = {**t, "persona": {**t["persona"], "recall_gap": "forgets_side_gig_unless_asked_about_freelance_work"}}
    assert fidelity.asked_keys(fidelity.CHANGE_QUESTION, side_gig, SPECS) == ("w2_employer_count",
                                                                              "clean_energy_installed")
    s = score({"H902": side_gig}, [rec("q00", {"business_app_payments": True})], SPECS)
    assert [b["key"] for b in s.broken_gaps] == ["business_app_payments"]


def test_report_and_review_show_the_change_question():
    t = truth("H902")
    record = rec("q00", {"w2_employer_count": 3}, "I picked up a part-time job.")
    s = score({"H902": t}, [record], SPECS)
    paths = (Path("report.md"), Path("review.md"), Path("answers.json"))
    outcome = fidelity.CheckOutcome("run-1", True, s, [record], Usage(), 0.0, 0.0, *paths)
    report = fidelity.render_report(outcome, {"H902": t}, SPECS, model="fake")
    assert "remembered changes the answers named: 0 of 3 (0.0%). Recall gaps that broke on it: 1." in report
    section = report.split("## What changed since last year")[1].split("## Mismatches")[0]
    assert "| H902 | business_app_payments | true |" in section
    assert f"| H902 | w2_employer_count | 3 | {GAP} |" in section
    assert "| q00 | what changed | 1 | 1 | each change the client remembers |" in report
    review = fidelity.render_review([record], {"H902": t}, SPECS, dry_run=True, leaks=s.leaks)
    assert ("Facts this question asks about: self_employment true, business_app_payments true, "
            "clean_energy_installed true") in review
    assert "Code flags: unasked_fact w2_employer_count" in review


MISREAD = "Yes, we paid tuition for my son."


def tuition_ended_records(q07=None, q07_anomalies=(), q10_answer="Some words."):
    """H011, whose tuition ended: q00 names its changes, q03 restates tuition, q07 misreads it, q10 corrects it."""
    q00 = {"dependents_count": 2, "new_dependent": True, "social_security": True, "has_mortgage": True,
           "bought_home": True, "tuition_paid": False, "efficiency_improvements": True}
    q07 = {"tuition_paid": True, "student_loan_interest": False, "dependent_care_paid": False} if q07 is None else q07
    return [
        rec("q00", q00, hid="H011"),
        rec("q03", {"interest_income": True, "tuition_paid": False}, hid="H011"),
        rec("q07", q07, MISREAD, hid="H011", anomalies=list(q07_anomalies)),
        rec("q10", {"tuition_paid": False}, q10_answer, hid="H011"),
    ]


def test_score_counts_facts_that_ended_and_lists_misreads():
    t = ALL_TRUTHS["H011"]
    assert simulator.ended_facts(t, SPECS) == ["tuition_paid"] and t["persona"]["volunteers_info"] is False
    s = score({"H011": t}, tuition_ended_records(), SPECS)
    assert (s.compared, s.matched, s.meets_bar) == (13, 12, False)
    # q00 and q07 ask about tuition; q00, q03, q07, and q10 reveal it, and only q07 says it is true this year.
    assert s.ended_households == ["H011"]
    assert (s.ended_asked, s.ended_revealed, s.ended_compared, s.ended_matched, s.ended_true) == (2, 2, 4, 3, 1)
    misread = {"household_id": "H011", "question_id": "q07", "key": "tuition_paid", "revealed": True, "truth": False}
    assert s.ended_mismatches == [misread] and s.ended_match_rate == pytest.approx(0.75)
    assert [{k: m[k] for k in misread} for m in s.mismatches] == [misread] and s.mismatches[0]["corrected_by"] == "q10"
    assert [(x["question_id"], x["kind"], x["match"]) for x in s.leaks] == [("q03", "restated_fact", "tuition_paid")]
    # No household here has tuition this year, so the control is empty; with no draw given, every household is drawn.
    assert (s.control_compared, s.control_households, s.control_match_rate) == (0, [], None)
    assert (s.drawn_households, s.drawn_compared, s.drawn_matched) == (["H011"], 13, 12)
    left_out = score({"H011": t}, tuition_ended_records(q07={"student_loan_interest": False}), SPECS)
    assert (left_out.ended_asked, left_out.ended_revealed, left_out.ended_true) == (2, 1, 0)
    # An uncoercible value is compared and not matched, and listed with the anomalies rather than the mismatches.
    maybe = uncoercible("tuition_paid", "maybe")
    s = score({"H011": t}, tuition_ended_records(q07={}, q07_anomalies=[maybe]), SPECS)
    assert (s.ended_compared, s.ended_matched, s.ended_true, s.ended_revealed) == (4, 3, 0, 2)
    assert s.ended_mismatches == [{**misread, "revealed": "maybe"}] and s.mismatches == []
    assert [(a["question_id"], a["kind"], a["key"]) for a in s.anomalies] == [("q07", "uncoercible", "tuition_paid")]
    # The control: the same fact where it is true this year (H058 started paying tuition), and a drawn subset.
    paid = ALL_TRUTHS["H058"]
    assert paid["true_facts"]["tuition_paid"] is True and simulator.ended_facts(paid, SPECS) == []
    records = tuition_ended_records() + [rec("q07", {"tuition_paid": False}, hid="H058"),
                                         rec("q10", {"tuition_paid": True}, hid="H058")]
    s = score({"H011": t, "H058": paid}, records, SPECS, drawn=["H058", "H999"])
    assert (s.control_compared, s.control_matched, s.control_households) == (2, 1, ["H058"])
    assert (s.ended_compared, s.ended_matched) == (4, 3) and s.drawn_households == ["H058"]
    assert (s.drawn_compared, s.drawn_matched, s.compared) == (2, 1, 15)
    nothing_ended = score({"H902": truth("H902")}, [rec("q07", {"tuition_paid": False})], SPECS)
    assert nothing_ended.ended_households == [] and nothing_ended.ended_match_rate is None
    assert nothing_ended.ended_compared == nothing_ended.control_compared == 0


def ended_outcome(records, truths, tmp_path=Path(".")):
    s = score(truths, records, SPECS)
    paths = (tmp_path / "report.md", tmp_path / "review.md", tmp_path / "answers.json")
    return fidelity.CheckOutcome("run-1", True, s, records, Usage(), 0.0, 0.0, *paths)


def test_report_shows_the_facts_that_ended(tmp_path, monkeypatch, capsys):
    truths = {"H011": ALL_TRUTHS["H011"]}
    records = tuition_ended_records(q10_answer="No, nothing else.\nThe tuition | ended last year.")
    outcome = ended_outcome(records, truths, tmp_path)
    report = fidelity.render_report(outcome, truths, SPECS, model="fake")
    assert ("- Facts that ended, which the client's change list shows as \"Last year only\" lines, in 1 household: "
            "revealed values that match the truth: 3 of 4 (75.0%). **Revealed as true this year: 1.** Other values "
            "that differ (null, or not a yes or no): 0. Asked about and revealed: 2 of 2. Control, the same facts "
            "where they are true this year: none revealed. These values count in the match rate too, but at 4 of 13 "
            "compared the bar can't see a misread on its own") in report
    assert "Matching the truth in the 1 household drawn by complexity (the PRD 6.4 sample): 12 of 13 (92.3%)" in report
    assert 'plus every household whose client prompt has a "Last year only" line (H011), which adds 0.' in report
    assert report.index("## What changed since last year") < report.index("## Facts that ended") < report.index(
        "## Mismatches")
    section = report.split("## Facts that ended")[1].split("## Mismatches")[0]
    assert [x for x in section.splitlines() if x.startswith("| H011")] == [
        "| H011 | q00 | tuition_paid | tuition_paid false | Some words. |",
        "| H011 | q03 | tuition_paid | tuition_paid false | Some words. |",
        f"| H011 | q07 | tuition_paid | tuition_paid true, truth false | {MISREAD} |",
        "| H011 | q10 | tuition_paid | tuition_paid false | No, nothing else. The tuition \\| ended last year. |",
    ]
    assert "Block 4 is done only when every fact that ended and was revealed as true this year is fixed" in report
    # The other cells: a fact left out, an uncoercible value, and an answer that failed twice.
    for q07, anomalies, cell in [
        ({"student_loan_interest": False}, [], "tuition_paid not revealed"),
        ({}, [uncoercible("tuition_paid", "maybe | so")], 'tuition_paid "maybe \\| so", truth false'),
    ]:
        other = fidelity.render_report(ended_outcome(tuition_ended_records(q07, anomalies), truths), truths, SPECS,
                                       model="fake")
        assert f"| H011 | q07 | tuition_paid | {cell} | {MISREAD} |" in other
        assert "Revealed as true this year: 0." in other and "**Revealed as true" not in other
    failed = [*tuition_ended_records()[:2], rec("q07", answer=None, error="unusable reply twice", hid="H011")]
    assert "| H011 | q07 | tuition_paid | failed twice |  |" in fidelity.render_report(
        ended_outcome(failed, truths), truths, SPECS, model="fake")
    # A household with no change list has nothing that ended.
    plain = fidelity.render_report(ended_outcome([rec("q01")], {"H902": truth("H902")}), {"H902": truth("H902")},
                                   SPECS, model="fake")
    assert '- Facts that ended (the client\'s "Last year only" lines): none of these households has one.' in plain
    assert 'None of these households has a "Last year only" line.' in plain and "Facts that ended table" not in plain
    assert "Chosen deterministically from train and dev, stratified by complexity: " in plain

    async def checked(**kwargs):
        return outcome

    monkeypatch.setattr(fidelity, "run_check", checked)
    assert fidelity.main(["--dry-run"]) == 1
    printed = capsys.readouterr().out
    assert ("facts that ended (the bar can't see a misread on its own): 1 revealed true this year, 3/4 revealed "
            "values match, 2/2 asked and revealed, in 1 household") in printed
    assert "control, the same facts where true this year: 0/0 revealed values match, in 0 households" in printed
    assert "match_rate on the 1 household drawn by complexity: 92.3% (12/13)" in printed


def test_null_follow_up_matches_null_truth():
    s = score({"H902": truth("H902")}, [rec("q09", {"ev_purchased": False, "ev_acquired_date": None})], SPECS)
    assert (s.compared, s.matched, s.mismatches) == (2, 2, [])
    s = score({"H902": truth("H902")}, [rec("q03", {"k1_count": None})], SPECS)
    assert s.mismatches[0]["truth"] == 0


def test_the_report_counts_and_lists_the_contradictions_sent_back(tmp_path, monkeypatch, capsys):
    t = truth("H902")
    q01, q08 = (next(q for q in QUESTIONS if q.id == qid) for qid in ("q01", "q08"))
    jobs = {"w2_employer_count": 3, "unemployment": False}
    sim, _ = make_sim([
        reply("Three jobs, with tips and overtime.", {**jobs, "tips_income": True, "overtime_pay": True}),
        reply("Three jobs, no tips or overtime.", {**jobs, "tips_income": False, "overtime_pay": False}),
        reply("Yes, to my church.", {"charitable_giving": True, "gambling_winnings": False}),
        reply("Yes, I gave to my church.", {"charitable_giving": True, "gambling_winnings": False}),
    ])
    # Sync, since main() below runs its own event loop.
    records = asyncio.run(fidelity.ask_all(sim, [q01, q08]))
    tips, overtime = ({"key": k, "first": True, "final": False} for k in ("tips_income", "overtime_pay"))
    church = {"key": "charitable_giving", "first": True, "final": True}
    assert [(r.answer, r.corrected, r.unresolved) for r in records] == [
        ("Three jobs, no tips or overtime.", [tips, overtime], []), ("Yes, I gave to my church.", [], [church])]
    s = score({"H902": t}, records, SPECS)
    q01_at, q08_at = ({"household_id": "H902", "question_id": qid} for qid in ("q01", "q08"))
    assert s.corrected == [{**q01_at, **tips}, {**q01_at, **overtime}] and s.unresolved == [{**q08_at, **church}]
    assert s.contradicted_answers == 2 and s.retries == 0
    # The match rate is on the answers kept: the corrected facts match, and the one left wrong is a mismatch.
    assert (s.compared, s.matched) == (6, 5) and [(m["question_id"], m["key"]) for m in s.mismatches] == [
        ("q08", "charitable_giving")]
    paths = (tmp_path / "report.md", tmp_path / "review.md", tmp_path / "answers.json")
    outcome = fidelity.CheckOutcome("run-1", True, s, records, Usage(), 0.0, 0.0, *paths)
    fidelity.write_outputs(outcome, {"H902": t}, SPECS, model="fake")
    report = outcome.report_path.read_text()
    assert ("- Answers the simulator sent back once, because they contradicted a fact outside the recall gap: 2 of 2 "
            "(100.0%). Facts the retry corrected: 2. Facts the answers kept still get wrong, counted as mismatches: "
            "1.") in report
    assert report.index("## Facts that ended") < report.index("## Contradictions sent back") < report.index(
        "## Mismatches")
    section = report.split("## Contradictions sent back")[1].split("## Mismatches")[0]
    assert [x for x in section.splitlines() if x.startswith("| H902")] == [
        "| H902 | q01 | tips_income | true | false | false | corrected |",
        "| H902 | q01 | overtime_pay | true | false | false | corrected |",
        "| H902 | q08 | charitable_giving | true | true | false | unresolved |",
    ]
    answers = json.loads(outcome.answers_path.read_text())["records"]
    assert [(a["corrected"], a["unresolved"]) for a in answers] == [([tips, overtime], []), ([], [church])]
    # A fact the answer kept left out, one only the retry got wrong, and a retry whose reply was unusable.
    others = [
        rec("q01", {"tips_income": False, "unemployment": True}, corrected=[{"key": "overtime_pay", "first": True}],
            unresolved=[{"key": "unemployment", "final": True}]),
        rec("q08", {"charitable_giving": True}, unresolved=[{**church, "retry_error": "the reply is not valid JSON"}]),
    ]
    other = fidelity.render_report(ended_outcome(others, {"H902": t}), {"H902": t}, SPECS, model="fake")
    section = other.split("## Contradictions sent back")[1].split("## Mismatches")[0]
    assert [x for x in section.splitlines() if x.startswith("| H902")] == [
        "| H902 | q01 | overtime_pay | true | not revealed | false | corrected |",
        "| H902 | q01 | unemployment | not revealed | true | false | unresolved |",
        "| H902 | q08 | charitable_giving | true | true (the retry's reply was unusable, so the first answer was kept: "
        "the reply is not valid JSON) | false | unresolved |",
    ]
    clean = fidelity.render_report(ended_outcome([rec("q01")], {"H902": t}), {"H902": t}, SPECS, model="fake")
    assert "sent back once, because they contradicted a fact outside the recall gap: 0 of 1 (0.0%)" in clean
    assert clean.split("## Contradictions sent back")[1].split("## Mismatches")[0].strip().endswith("None.")

    async def checked(**kwargs):
        return outcome

    monkeypatch.setattr(fidelity, "run_check", checked)
    assert fidelity.main(["--dry-run"]) == 1
    assert "contradictions sent back: 2/2 answers, facts corrected: 2, unresolved: 1" in capsys.readouterr().out


@pytest.mark.parametrize(
    "bad",
    [
        {"answer": None, "error": "unusable reply twice", "retried": True},
        {"anomalies": [{"kind": "missing_revealed"}]},
        {"anomalies": [{"kind": "revealed_not_object", "value": [1]}]},
    ],
)
def test_unscored_answers_fail_the_bar_whatever_the_match_rate(bad):
    rest = [q for q in QUESTIONS if q.id != "q01"]
    records = [rec("q01", {"w2_employer_count": 3})] + [rec(q.id, **bad) for q in rest]
    s = score({"H902": truth("H902")}, records, SPECS)
    assert (s.compared, s.match_rate, s.answers) == (1, 1.0, 11)
    assert s.meets_bar is False and len(s.unscored) == 10
    assert {u["question_id"] for u in s.unscored} == {q.id for q in rest}
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


def tuition_truth(hid, tier, ended):
    """A synthetic truth whose tuition ended this year, or that changed nothing."""
    return {"household_id": hid, "complexity": tier, "persona": {"recall_gap": "none"},
            "true_facts": {"tuition_paid": False}, "prior_year_facts": {"tuition_paid": ended},
            "changed_facts": ["tuition_paid"] if ended else []}


def test_sample_adds_every_household_with_a_last_year_only_line():
    splits, tiers = big_splits()
    # H061 is a test household and H100 is listed in both dev and test, so neither may get in.
    ended = {"H005", "H045", "H061", "H100"}
    pool = {h: tuition_truth(h, tier, h in ended) for h, tier in tiers.items()}
    drawn = choose_households(splits, tiers)
    chosen = fidelity.sample_households(splits, pool, SPECS)
    assert chosen == sorted(set(drawn) | {"H005", "H045"}) and len(chosen) > len(drawn)
    assert not {"H061", "H100"} & set(chosen)
    assert fidelity.sample_households(splits, dict(reversed(pool.items())), SPECS) == chosen
    assert fidelity.sample_households(splits, pool, SPECS, n=5) == sorted(
        set(choose_households(splits, tiers, n=5)) | {"H005", "H045"})
    assert fidelity.sample_households(splits, pool, SPECS, n=60) == fidelity.pool_ids(splits)
    nothing_ended = {h: tuition_truth(h, tier, False) for h, tier in tiers.items()}
    assert fidelity.sample_households(splits, nothing_ended, SPECS) == drawn


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
    assert set(per_question.values()) == {2, 3} and sum(per_question.values()) == 30
    assert all(r.error is None for r in sample)
    assert choose_answers_for_review(records, seed=1) != sample
    small = choose_answers_for_review(records[:25], n=30)
    assert len(small) == 24


def check_truths():
    """The live check's households from the frozen data: 20 drawn by complexity and 8 more that ended a fact."""
    splits = fidelity.load_splits(REPO / "data")
    pool = fidelity.load_truths(REPO / "data", fidelity.pool_ids(splits))
    return {hid: pool[hid] for hid in fidelity.sample_households(splits, pool, SPECS)}


def test_estimate_stays_under_the_run_cap():
    prices = load_prices()
    truths = list(check_truths().values())
    estimate = estimate_cost_usd(truths, SPECS, "claude-haiku-5-5", prices)
    # Up from $0.19 for 20 households. A .env made from the old template may still say RUN_CAP_USD=0.50,
    # and the reservation is this estimate, so it has to fit under that as well as the $2.00 default.
    assert len(truths) == 28 and round(estimate, 2) == 0.27 and estimate < 0.50
    shorter = estimate_cost_usd(truths, SPECS, "claude-haiku-5-5", prices, max_tokens=300)
    assert shorter < estimate
    assert estimate_cost_usd([truth("H903")] * 20, SPECS, "claude-haiku-5-5", prices) < estimate


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
    assert "100.0%" in report and "**Met.**" in report and "Answers scored: 220 of 220" in report
    assert "remembered changes the answers named: " in report and "Recall gaps that broke on it: 0." in report
    assert "## What changed since last year" in report
    # The fixture households have no change list, so nothing ended and the draw is the whole check.
    assert "none of these households has one." in report and "## Facts that ended" in report
    assert "Matching the truth in the 20 households drawn by complexity (the PRD 6.4 sample): " in report
    assert review.count("\n## ") == 30 and "at least 2 per question" in review
    other_lines = [x for x in review.splitlines() if x.startswith("Other facts with a value")]
    assert len(other_lines) == 30 and not any(" null" in x for x in other_lines)
    assert len(answers["records"]) == 220 and answers["dry_run"] is True and answers["model"] is None
    for test_id in splits["test"]:
        assert test_id not in report and test_id not in review
    for text in (report, review):
        assert chr(0x2014) not in text and chr(0x2013) not in text
    printed = capsys.readouterr().out
    assert "bar met: True" in printed and "dry_run: True" in printed and "recall gaps broken: 0" in printed
    assert 'facts that ended: none of these households has a "Last year only" line' in printed
    assert "contradictions sent back: 0/220 answers, facts corrected: 0, unresolved: 0" in printed
    assert "sent back once, because they contradicted a fact outside the recall gap: 0 of 220 (0.0%)" in report


@pytest.mark.anyio
async def test_dry_run_spends_nothing_and_defaults_to_a_temp_folder(tmp_path, monkeypatch):
    data, _ = make_data_dir(tmp_path / "data")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    ledger = tmp_path / "spend.jsonl"

    def no_settings():
        raise AssertionError("a dry run must not load the key")

    fakes = []

    class Counted(fidelity.TruthEchoClient):
        def __init__(self, *args):
            super().__init__(*args)
            fakes.append(self)

    monkeypatch.setattr(fidelity, "TruthEchoClient", Counted)
    outcome = await fidelity.run_check(data_dir=data, dry_run=True, settings_loader=no_settings, ledger_path=ledger)
    # The fake answers from the truth, so nothing is sent back: one call per answer, and no retry of either kind.
    s = outcome.score
    assert s.corrected == s.unresolved == [] and s.contradicted_answers == 0 and s.retries == 0
    assert sum(len(f.calls) for f in fakes) == s.answers == 220
    assert not ledger.exists()
    assert outcome.report_path.exists() and REPO not in outcome.report_path.parents
    assert tmp_path / "tmp" in outcome.report_path.parents
    assert outcome.score.match_rate == 1.0 and outcome.score.leaks == [] and outcome.score.anomalies == []
    # The fake names every remembered change and no hidden one, so the change question is clean too.
    assert outcome.score.changes_asked > 0 and outcome.score.change_coverage == 1.0 and outcome.score.broken_gaps == []
    assert outcome.usage.cache_read_tokens > 0 and outcome.usage.cache_write_tokens > 0
    assert outcome.cost_usd > 0 and outcome.estimate_usd > outcome.cost_usd


DRAWN = ["H010", "H011", "H013", "H018", "H021", "H036", "H042", "H043", "H046", "H048", "H051", "H054", "H058",
         "H080", "H081", "H082", "H087", "H089", "H093", "H095"]
ENDED = ["H011", "H015", "H022", "H032", "H059", "H065", "H067", "H085", "H091"]


@pytest.mark.anyio
async def test_the_check_takes_every_train_and_dev_household_with_a_last_year_only_line(tmp_path):
    outcome = await fidelity.run_check(data_dir=REPO / "data", out_dir=tmp_path, dry_run=True)
    s = outcome.score
    splits = fidelity.load_splits(REPO / "data")
    # The draw is the same 20 as before; the 8 ended households it left out are added.
    assert s.drawn_households == DRAWN and s.ended_households == ENDED
    assert s.households == sorted(set(DRAWN) | set(ENDED)) == sorted(check_truths()) and len(s.households) == 28
    assert not set(s.households) & set(splits["test"])
    # Adding rather than reserving keeps a household where each fact that ends elsewhere is true this year.
    truths = fidelity.load_truths(REPO / "data", s.households)
    for key in ("tuition_paid", "self_employment", "business_app_payments", "marketplace_coverage"):
        assert [h for h in s.households if truths[h]["true_facts"][key] and h not in ENDED], key
    assert (s.ended_asked, s.ended_revealed, s.ended_compared, s.ended_matched, s.ended_true) == (26, 26, 26, 26, 0)
    assert s.ended_mismatches == [] and s.ended_match_rate == 1.0
    assert (s.control_compared, s.control_matched, len(s.control_households)) == (32, 32, 10)
    assert (s.drawn_compared, s.drawn_matched) == (747, 747)
    assert (s.answers, s.compared, s.matched, s.meets_bar) == (308, 1065, 1065, True)
    assert s.leaks == [] and s.anomalies == [] and s.change_coverage == 1.0
    assert s.corrected == s.unresolved == [] and s.contradicted_answers == 0 and s.retries == 0
    assert outcome.estimate_usd == estimate_cost_usd(truths.values(), SPECS, "claude-haiku-5-5", load_prices())
    assert round(outcome.estimate_usd, 2) == 0.27 and outcome.estimate_usd < 0.50
    report, review = outcome.report_path.read_text(), outcome.review_path.read_text()
    for test_id in splits["test"]:
        assert test_id not in report and test_id not in review
    section = report.split("## Facts that ended")[1].split("## Mismatches")[0]
    assert len([x for x in section.splitlines() if x.startswith("| H0")]) == 20
    assert ("| H022 | q00 | self_employment, business_app_payments, tuition_paid | self_employment false; "
            "business_app_payments false; tuition_paid false |") in section
    assert "in 9 households: revealed values that match the truth: 26 of 26 (100.0%)" in report
    assert "Matching the truth in the 20 households drawn by complexity (the PRD 6.4 sample): 747 of 747" in report
    # The review sample keeps its rule: 30 answers, 2 or 3 per question.
    assert review.count("\n## ") == 30 and "at least 2 per question" in review


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
    assert stand_in.calls == 2 * len(QUESTIONS)
    lines = [json.loads(x) for x in ledger.read_text().splitlines()]
    assert [x["kind"] for x in lines] == ["reserve", "settle"]
    assert lines[0]["reserved_usd"] == pytest.approx(outcome.estimate_usd)
    usage = {"input_tokens": 900 * stand_in.calls, "output_tokens": 40 * stand_in.calls}
    expected = cost_usd("claude-haiku-5-5", usage, load_prices())
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
