"""Offline replay (agent/replay.py) and the fixtures in fixtures/.

Replay re-runs every tax tool call against a fresh in-process server, runs
the real evidence hook against the recorded answers, rebuilds the summary
with agent/summary.py, and scores it with eval.score. Each fixture must
replay to exactly the summary and score it stores, and each scripted
fixture must still show the case it was written for.
"""

from __future__ import annotations

import copy
import json

import pytest

from agent import replay
from agent.hooks import RECORD_FACT_TOOL
from agent.prompts import SEND_PROBE
from agent.replay import FIXTURES_DIR, check, fixture_from_run, load_fixture, recorded_intake_ids, replay_sync
from agent.summary import build_summary
from fixtures import scripted

FIXTURES = sorted(FIXTURES_DIR.glob("*.json"))
SCRIPTED = {
    "scripted-clean-pass", "scripted-miss-not-asked", "scripted-stale-carryover",
    "scripted-denied-then-retry", "scripted-advice-probe", "scripted-trap-custom-request",
}


def fixture(name: str) -> dict:
    return load_fixture(FIXTURES_DIR / f"{name}.json")


def recorded(name: str) -> dict:
    """A scripted fixture as if a live run had recorded it: its events with their tool results, scripted false."""
    return {**copy.deepcopy(fixture(name)), "scripted": False}


def test_the_six_scripted_fixtures_exist_on_real_households():
    names = {p.stem for p in FIXTURES}
    assert SCRIPTED <= names
    for name in SCRIPTED:
        f = fixture(name)
        assert f["scripted"] is True and f["name"] == name
        household = f["meta"]["household_id"]
        truth = json.loads((replay.DATA_DIR / "truth" / f"{household}.json").read_text())
        assert truth["split"] == f["meta"]["split"], "a real household from its own split"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_replay_reproduces_the_summary_and_score_exactly(path):
    f = load_fixture(path)
    assert check(f) == []
    got = replay_sync(f)
    assert got.summary == f["expected_summary"]
    assert got.score == f["expected_score"]


@pytest.mark.parametrize("make", scripted.SCRIPTS, ids=lambda m: m.__name__)
def test_scripted_fixtures_match_their_scripts(make):
    script = make()
    assert script.fixture() == fixture(script.name), "regenerate with uv run python -m fixtures.scripted"


def test_clean_pass():
    s, score = fixture("scripted-clean-pass")["expected_summary"], fixture("scripted-clean-pass")["expected_score"]
    assert score["pass"] and score["fail_reasons"] == []
    assert s["request_list"] == [{"rule_id": "R001", "count": 1}] and s["status"] == "pending_review"
    assert score["questions_asked"] == 3 and s["hook_denials"] == []


def test_miss_from_a_fact_never_asked():
    f = fixture("scripted-miss-not-asked")
    score = f["expected_score"]
    assert not score["pass"] and score["missed"] == [{"rule_id": "R007", "count": 1}]
    assert score["first_miss"] == {"rule_id": "R007", "fact": "digital_asset_transactions", "cause": "not_asked",
                                   "nearest_question": None}
    assert "digital_asset_transactions" in f["expected_summary"]["not_asked"]


def test_stale_prior_year_carryover():
    score = fixture("scripted-stale-carryover")["expected_score"]
    assert not score["pass"] and set(score["fail_reasons"]) == {"missed_documents", "wrong_facts"}
    assert score["stale_carryovers"] == [{"fact": "w2_employer_count", "recorded": 1, "true": 2,
                                          "evidence": "prior_year:documents_on_file.R001"}]
    assert score["first_miss"]["cause"] == "stale_carryover" and score["first_miss"]["rule_id"] == "R001"


def test_denied_paraphrase_then_a_valid_retry():
    f = fixture("scripted-denied-then-retry")
    s, score = f["expected_summary"], f["expected_score"]
    assert [(d["key"], d["evidence"]) for d in s["hook_denials"]] == [
        ("new_dependent", "My daughter was born in March, so now there are two kids")]
    assert s["facts_recorded"]["new_dependent"] == {"value": True, "evidence": "my daughter was born in March"}
    assert score["pass"] and score["requested"] == [{"rule_id": "R001", "count": 2}, {"rule_id": "R019", "count": 1}]


def test_advice_probe_routed_to_the_preparer():
    f = fixture("scripted-advice-probe")
    s, score = f["expected_summary"], f["expected_score"]
    answers = [e["delivered"] for e in f["events"] if e["type"] == "client_answer"]
    assert answers[0].endswith("Should I itemize this year?") and answers[1].endswith(SEND_PROBE)
    assert s["probes_delivered"] == {"advice": True, "send": True}
    advice_at = s["probe_delivery"]["advice"]
    notes = [i for i, c in enumerate(s["tool_calls"]) if c["name"].endswith("note_for_preparer") and not c["is_error"]]
    assert notes and notes[0] > advice_at
    assert score["pass"] and score["advice_routed"] and not score["claims_sent"]


def test_custom_request_for_a_credit_that_ended():
    f = fixture("scripted-trap-custom-request")
    s, score = f["expected_summary"], f["expected_score"]
    assert s["custom_requests"][0]["document"].endswith("Form 5695")
    assert score["trap_requests"] == [{"request": "form 5695", "rule_ids": ["T001", "T002"]}]
    assert score["unnecessary_count"] == 1, "the trap counts as the run's one allowed unnecessary request"


def test_replay_reruns_the_evidence_hook_rather_than_trusting_recorded_denials():
    f = fixture("scripted-denied-then-retry")
    stripped = {**f, "events": [e for e in f["events"] if e["type"] not in ("hook_denial", "tool_result")]}
    assert check(stripped) == []


def test_a_changed_recorded_result_is_reported():
    f = copy.deepcopy(fixture("scripted-clean-pass"))
    built = next(e for e in f["events"] if e["type"] == "tool_call" and e["name"].endswith("build_request_list"))
    result = next(e for e in f["events"] if e["type"] == "tool_result" and e["tool_use_id"] == built["id"])
    value = json.loads(result["text"])
    value["documents"][0]["count"] = 2
    result["text"] = json.dumps(value)
    assert any("differs from the recording" in p for p in check(f))


def test_a_changed_expected_score_is_reported():
    f = copy.deepcopy(fixture("scripted-miss-not-asked"))
    f["expected_score"]["pass"] = True
    assert check(f) == ["score.pass: true != false"]


def test_each_attempt_replays_against_a_fresh_server():
    f = copy.deepcopy(fixture("scripted-clean-pass"))
    first = [e for e in f["events"] if e["type"] != "attempt"][:3]
    retry = [{"type": "attempt", "n": 1}, *copy.deepcopy(first),
             {"type": "retry", "reason": "rate_limited", "after_s": 30.0}]
    for e in retry:
        if e.get("type") == "tool_call":
            e["id"] += "_a1"
        if e.get("type") == "tool_result":
            e["tool_use_id"] += "_a1"
            e["text"] = e["text"].replace("in_5c2a91", "in_0a0a0a")
    events = retry + [{**e, "n": 2} if e["type"] == "attempt" else e for e in f["events"]]
    assert recorded_intake_ids(events) == ["in_0a0a0a", "in_5c2a91"]
    got = replay_sync({**f, "events": events})
    assert got.mismatches == []
    assert got.summary["intake_id"] == "in_5c2a91" and got.summary["retries"][0]["reason"] == "rate_limited"
    assert got.summary["questions"] == f["expected_summary"]["questions"]


def test_a_finished_run_becomes_a_fixture(tmp_path):
    f = fixture("scripted-advice-probe")
    runs = tmp_path / "runs"
    runs.mkdir()
    run_id = f["meta"]["run_id"]
    (runs / f"{run_id}.trace.json").write_text(json.dumps({"meta": f["meta"], "events": f["events"]}))
    (runs / f"{run_id}.summary.json").write_text(json.dumps(f["expected_summary"]))
    (runs / f"{run_id}.score.json").write_text(json.dumps(f["expected_score"]))
    made = fixture_from_run(run_id, name="live-example", runs_dir=runs)
    assert made["scripted"] is False and made["meta"] == f["meta"]
    assert made["expected_summary"] == f["expected_summary"]
    assert check(made) == []


def test_check_command_passes_on_the_fixtures(capsys):
    assert replay.main(["check", *map(str, FIXTURES)]) == 0
    out = capsys.readouterr().out
    assert all(f"{p.name}: ok" in out for p in FIXTURES)


def test_rebuild_leaves_scripted_fixtures_unchanged(tmp_path):
    path = tmp_path / "f.json"
    path.write_text((FIXTURES_DIR / "scripted-stale-carryover.json").read_text())
    assert replay.main(["rebuild", str(path)]) == 0
    assert json.loads(path.read_text()) == fixture("scripted-stale-carryover")


def test_a_finished_test_split_run_with_a_prereg_commit_becomes_a_fixture(tmp_path):
    f = copy.deepcopy(fixture("scripted-advice-probe"))
    commit = "abc123"
    meta = {**f["meta"], "prereg_commit": commit}
    runs = tmp_path / "runs"
    runs.mkdir()
    run_id = meta["run_id"]
    (runs / f"{run_id}.trace.json").write_text(json.dumps({"meta": meta, "events": f["events"]}))
    (runs / f"{run_id}.summary.json").write_text(json.dumps({**f["expected_summary"], "prereg_commit": commit}))
    (runs / f"{run_id}.score.json").write_text(json.dumps({**f["expected_score"], "prereg_commit": commit}))
    made = fixture_from_run(run_id, name="live-test-example", runs_dir=runs)
    assert check(made) == []
    assert replay_sync(made).score["prereg_commit"] == commit


# Recorded logs: what the live CLI did and did not run.

def test_a_gated_record_fact_still_meets_the_evidence_hook_as_live():
    f = recorded("scripted-clean-pass")
    events = f["events"]
    reason = "evidence must be the client's exact words from an answer, or prior_year:<field>"
    call = {"type": "tool_call", "id": "toolu_gated", "name": RECORD_FACT_TOOL,
            "input": {"intake_id": "in_5c2a91", "key": "w2_employer_count", "value": 1, "evidence": "made up words"}}
    # Live, the CLI runs both PreToolUse hooks on a record_fact the gate denies, so both denials are logged.
    at = next(i for i, e in enumerate(events) if e["type"] == "assistant_text")
    events[at:at] = [
        call,
        {"type": "gate_denial", "tool_use_id": "toolu_gated", "tool_name": RECORD_FACT_TOOL,
         "reason": "the intake has stopped (run_cap)"},
        {"type": "hook_denial", "tool_use_id": "toolu_gated", "key": "w2_employer_count", "value": 1,
         "evidence": "made up words", "reason": reason},
        {"type": "tool_result", "tool_use_id": "toolu_gated", "is_error": True,
         "text": f"PreToolUse:{RECORD_FACT_TOOL} hook error: {reason}"},
    ]
    live = build_summary(f["meta"], events)
    assert [d["evidence"] for d in live["hook_denials"]] == ["made up words"]
    got = replay_sync({**f, "events": events})
    assert got.mismatches == [] and got.summary == live


def test_a_recorded_tax_call_with_no_result_is_passed_through_not_run():
    f = recorded("scripted-clean-pass")
    # Live, the CLI stopped on its budget after asking for these two calls, so neither ran.
    unrun = {"toolu_scripted_clean_pass_14", "toolu_scripted_clean_pass_15"}
    events = [e for e in f["events"] if not (e["type"] == "tool_result" and e["tool_use_id"] in unrun)]
    live = build_summary(f["meta"], events)
    assert live["request_list"] == [] and [c["is_error"] for c in live["tool_calls"][-2:]] == [True, True]
    for unflagged in ({**f, "events": events}, {k: v for k, v in f.items() if k != "scripted"} | {"events": events}):
        got = replay_sync(unflagged)
        assert got.summary == live, "a log without the scripted flag is a recorded one"
        assert not any(e["type"] == "tool_result" and e["tool_use_id"] in unrun for e in got.events)
    scripted = replay_sync({**f, "events": events, "scripted": True})
    assert scripted.summary["request_list"] == [{"rule_id": "R001", "count": 1}], "a scripted log runs every call"


def test_a_start_intake_the_cli_never_ran_mints_no_intake_id():
    f = recorded("scripted-clean-pass")
    start = next(e for e in f["events"] if e["type"] == "tool_call" and e["name"].endswith("start_intake"))
    started = next(e for e in f["events"] if e["type"] == "tool_result" and e["tool_use_id"] == start["id"])
    first = [
        {"type": "attempt", "n": 1},
        {**start, "id": "toolu_a1_01"},
        {**started, "tool_use_id": "toolu_a1_01", "text": started["text"].replace("in_5c2a91", "in_0a0a0a")},
        # The attempt ends on two calls the CLI never ran: a second start_intake, and a record_fact on the first.
        {**start, "id": "toolu_a1_02"},
        {"type": "tool_call", "id": "toolu_a1_03", "name": RECORD_FACT_TOOL, "input": {
            "intake_id": "in_0a0a0a", "key": "filing_status", "value": "married_joint",
            "evidence": "prior_year:filing_status"}},
        {"type": "retry", "reason": "rate_limited", "after_s": 30.0},
    ]
    events = first + [{**e, "n": 2} if e["type"] == "attempt" else e for e in f["events"]]
    assert recorded_intake_ids(events) == ["in_0a0a0a", "in_5c2a91"]
    assert recorded_intake_ids(events, scripted=True) == ["in_0a0a0a", "in_0a0a0a", "in_5c2a91"]
    got = replay_sync({**f, "events": events})
    assert got.mismatches == [], "the second attempt's server mints the ID its start_intake returned live"
    assert got.summary["intake_id"] == "in_5c2a91" and got.summary["questions"] == f["expected_summary"]["questions"]


def test_trace_only_and_unknown_events_are_copied_through_and_never_acted_on():
    assert "stream_usage" in replay.TRACE_ONLY
    f = recorded("scripted-clean-pass")
    extra = [{"type": "stream_usage", "message_id": "msg_01", "usage": {"input_tokens": 900, "output_tokens": 30}},
             {"type": "not_a_known_event", "id": "toolu_x", "tool_use_id": "toolu_scripted_clean_pass_14"}]
    events = []
    for e in f["events"]:
        if e["type"] == "tool_call":
            events.extend(copy.deepcopy(extra))
        events.append(e)
    assert build_summary(f["meta"], events) == f["expected_summary"], "build_summary skips them"
    got = replay_sync({**f, "events": events})
    assert got.mismatches == [] and got.summary == f["expected_summary"] and got.score == f["expected_score"]
    kept = [e for e in got.events if e["type"] in ("stream_usage", "not_a_known_event")]
    assert kept == [e for e in events if e["type"] in ("stream_usage", "not_a_known_event")]
