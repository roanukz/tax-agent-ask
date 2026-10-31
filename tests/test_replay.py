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
from agent.prompts import SEND_PROBE
from agent.replay import FIXTURES_DIR, check, fixture_from_run, load_fixture, recorded_intake_ids, replay_sync
from fixtures import scripted

FIXTURES = sorted(FIXTURES_DIR.glob("*.json"))
SCRIPTED = {
    "scripted-clean-pass", "scripted-miss-not-asked", "scripted-stale-carryover",
    "scripted-denied-then-retry", "scripted-advice-probe", "scripted-trap-custom-request",
}


def fixture(name: str) -> dict:
    return load_fixture(FIXTURES_DIR / f"{name}.json")


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
