"""Baselines B1, B2, and B3 (PRD 6.5), offline with fake simulators and fake clients."""

import dataclasses
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from eval import baselines
from eval import run as eval_run
from eval.baselines import (
    B3_EFFORT,
    NO_LIST,
    ReplyError,
    b3_facts,
    b3_prompt,
    model_list_config,
    parse_model_list,
    prior_year_list,
    run_full_organizer,
    run_id_for,
    run_model_list,
    run_prior_year,
    run_prior_year_split,
    score_baseline,
    whole_count,
)
from eval.score import score_list
from ledger.spend import cost_usd, load_prices
from rules.engine import (
    STATUSES_FOR_SPLIT,
    complete_facts,
    load_facts,
    load_ignore_aliases,
    load_rules,
    required_documents,
)
from sim.fakes import FakeUsage, ScriptedClient, TruthEchoClient, text_response
from sim.simulator import ClientSimulator, SimulatorError, Usage

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
SPECS = load_facts()
RULES = load_rules()
IGNORE = load_ignore_aliases()
PRICES = load_prices()
DEV = STATUSES_FOR_SPLIT["dev"]
TRAIN_IDS = json.loads((DATA / "splits.json").read_text())["train"]
FOLLOW_UPS = {"clean_energy_install_year": "clean_energy_installed",
              "efficiency_improvements_year": "efficiency_improvements",
              "ev_acquired_date": "ev_purchased"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def load(kind, hid):
    return json.loads((DATA / kind / f"{hid}.json").read_text())


def fake_truth(hid="H901", **facts):
    true = complete_facts({}, SPECS)
    true.update(facts)
    return {"household_id": hid, "split": "dev", "complexity": "medium", "true_facts": true,
            "changed_facts": [k for k in facts], "probes": {"advice_question": "Can I deduct my car?",
                                                          "send_request": True}}


def fake_profile(hid="H901", documents=()):
    return {"household_id": hid, "tax_year": 2026, "prior_year_summary": {
        "tax_year": 2025, "filing_status": "single", "dependents_count": 0, "itemized": False,
        "documents_on_file": [dict(d) for d in documents]}}


@dataclass
class Reply:
    answer: str
    revealed: dict


class FakeSimulator:
    """Reveals what reveal(question_key) returns; records every question it was asked."""

    model = "claude-haiku-5-5"
    max_prompt_tokens = 500

    def __init__(self, reveal, fail_on=()):
        self.reveal = reveal
        self.fail_on = set(fail_on)
        self.asked = []
        self.total_usage = Usage()
        self.key_for = {spec.plain_question_hint: key for key, spec in SPECS.items()}

    async def answer(self, question):
        key = self.key_for[question]
        self.asked.append(key)
        self.total_usage = self.total_usage + Usage(input_tokens=100, output_tokens=10)
        if key in self.fail_on:
            raise SimulatorError("bad reply twice", reasons=["x", "y"], raw_texts=["", ""], usage=Usage())
        return Reply(f"Answer about {key}.", self.reveal(key))


def oracle(truth):
    return lambda key: {} if truth["true_facts"].get(key) is None else {key: truth["true_facts"][key]}


async def organizer(truth, sim, profile=None, statuses=DEV):
    return await run_full_organizer(truth, profile or fake_profile(truth["household_id"]), sim, statuses=statuses,
                                    run_id="dev-H901-B1-t1", split="dev", trial=1, rules=RULES, specs=SPECS,
                                    prices=PRICES)


# ---- B1 -----------------------------------------------------------------------


@pytest.mark.anyio
async def test_b1_asks_top_level_keys_in_order_and_follow_ups_only_after_a_yes():
    truth = fake_truth(clean_energy_installed=True, clean_energy_install_year=2026, ev_purchased=False)
    sim = FakeSimulator(oracle(truth))
    summary = await organizer(truth, sim)
    expected = [k for k in SPECS if k not in FOLLOW_UPS or k == "clean_energy_install_year"]
    assert sim.asked == expected
    assert [q["fact_keys"] for q in summary["questions"]] == [[k] for k in expected]
    assert [q["n"] for q in summary["questions"]] == list(range(1, len(expected) + 1))
    assert [q["text"] for q in summary["questions"]] == [SPECS[k].plain_question_hint for k in expected]
    position = expected.index("clean_energy_install_year")
    assert expected[position - 1] == "clean_energy_installed"
    assert summary["turns"] == len(expected) == 37


@pytest.mark.anyio
async def test_b1_skips_a_follow_up_when_the_parent_was_never_revealed():
    truth = fake_truth(ev_purchased=True, ev_acquired_date="2026-03-02")
    sim = FakeSimulator(lambda key: {} if key == "ev_purchased" else oracle(truth)(key))
    summary = await organizer(truth, sim)
    assert "ev_acquired_date" not in sim.asked
    assert "ev_purchased" not in summary["facts_recorded"]
    assert "ev_purchased" in summary["not_asked"]


@pytest.mark.anyio
async def test_b1_records_revealed_facts_coerced_with_the_answer_as_evidence():
    truth = fake_truth(w2_employer_count=2, interest_income=True)
    reveal = {"w2_employer_count": {"w2_employer_count": "2"}, "interest_income": {"interest_income": "true"}}
    sim = FakeSimulator(lambda key: reveal.get(key, {}))
    summary = await organizer(truth, sim)
    assert summary["facts_recorded"] == {
        "w2_employer_count": {"value": 2, "evidence": "Answer about w2_employer_count."},
        "interest_income": {"value": True, "evidence": "Answer about interest_income."},
    }
    assert summary["request_list"] == [{"rule_id": "R001", "count": 2}, {"rule_id": "R004", "count": 1}]
    assert "filing_status" in summary["not_asked"] and "w2_employer_count" not in summary["not_asked"]


@pytest.mark.anyio
async def test_b1_drops_unknown_null_and_uncoercible_reveals():
    truth = fake_truth()
    reveal = {"filing_status": {"filing_status": "single", "not_a_key": True, "dependents_count": "lots",
                                "new_dependent": None}}
    summary = await organizer(truth, FakeSimulator(lambda key: reveal.get(key, {})))
    assert summary["facts_recorded"] == {"filing_status": {"value": "single", "evidence": "Answer about filing_status."}}


@pytest.mark.anyio
async def test_b1_a_direct_answer_outranks_a_passing_mention():
    truth = fake_truth(self_employment=True)
    reveal = {
        "w2_employer_count": {"self_employment": False},
        "self_employment": {"self_employment": True},
        "business_app_payments": {"self_employment": False},
    }
    summary = await organizer(truth, FakeSimulator(lambda key: reveal.get(key, {})))
    assert summary["facts_recorded"]["self_employment"] == {
        "value": True, "evidence": "Answer about self_employment."}


@pytest.mark.anyio
async def test_b1_a_mention_before_the_direct_question_is_recorded_until_answered():
    truth = fake_truth(business_app_payments=True)
    reveal = {"self_employment": {"self_employment": True, "business_app_payments": True},
              "business_app_payments": {}}
    summary = await organizer(truth, FakeSimulator(lambda key: reveal.get(key, {})))
    assert summary["facts_recorded"]["business_app_payments"]["evidence"] == "Answer about self_employment."


@pytest.mark.anyio
async def test_b1_a_simulator_error_is_logged_and_the_organizer_moves_on():
    truth = fake_truth(interest_income=True)
    sim = FakeSimulator(oracle(truth), fail_on={"interest_income"})
    summary = await organizer(truth, sim)
    assert summary["harness_errors"] == [{"kind": "simulator", "question": 7, "fact_key": "interest_income",
                                          "error": "bad reply twice"}]
    assert "interest_income" not in summary["facts_recorded"]
    assert sim.asked[-1] == "ev_purchased"
    assert sum(1 for t in summary["transcript"] if t["role"] == "client") == len(sim.asked) - 1


@pytest.mark.anyio
async def test_b1_summary_shape_usage_and_cost():
    truth = fake_truth()
    sim = FakeSimulator(oracle(truth))
    summary = await organizer(truth, sim)
    assert summary["config"] == "B1" and summary["status"] == "pending_review"
    assert summary["tool_calls"] is None and summary["model"] is None
    assert summary["simulator_model"] == "claude-haiku-5-5"
    assert summary["probes_delivered"] == {"advice": False, "send": False}
    assert summary["probe_delivery"] == {"advice": None, "send": None}
    n = len(summary["questions"])
    assert summary["usage"] == {"input_tokens": 100 * n, "output_tokens": 10 * n, "cache_read_tokens": 0,
                                "cache_write_tokens": 0}
    expected = cost_usd("claude-haiku-5-5", summary["usage"], PRICES, prompt_tokens=500)
    assert summary["cost_usd"] == expected > 0
    assert summary["cost_breakdown"] == {"agent": 0.0, "simulator": expected, "planner": 0.0}
    roles = [t["role"] for t in summary["transcript"]]
    assert roles == ["agent", "client"] * n
    assert summary["final_message"].startswith("Full organizer baseline:")
    # The harness lists a baseline keeps empty, so a baseline summary has the agent run's shape.
    assert summary["hook_denials"] == summary["question_denials"] == summary["client_contradictions"] == []


@pytest.mark.anyio
async def test_b1_counts_only_usage_from_this_run():
    truth = fake_truth()
    sim = FakeSimulator(oracle(truth))
    sim.total_usage = Usage(input_tokens=5000)
    summary = await organizer(truth, sim)
    assert summary["usage"]["input_tokens"] == 100 * len(summary["questions"])


@pytest.mark.anyio
async def test_b1_refuses_a_profile_for_another_household():
    with pytest.raises(ValueError, match="profile is for H902"):
        await organizer(fake_truth(), FakeSimulator(lambda key: {}), profile=fake_profile("H902"))


@pytest.mark.anyio
@pytest.mark.parametrize("hid", TRAIN_IDS)
async def test_b1_with_an_oracle_simulator_reproduces_the_engine_list(hid):
    truth = load("truth", hid)
    question_keys = {spec.plain_question_hint: [key] for key, spec in SPECS.items()}
    client = TruthEchoClient(truth["true_facts"], question_keys, SPECS)
    sim = ClientSimulator(truth, client, specs=SPECS)
    summary = await run_full_organizer(truth, load("profiles", hid), sim, statuses=DEV,
                                       run_id=run_id_for("train", hid, "B1", 1), split="train", trial=1)
    assert summary["request_list"] == required_documents(truth["true_facts"], RULES, SPECS, DEV)
    recorded = {k: v["value"] for k, v in summary["facts_recorded"].items()}
    assert recorded == {k: v for k, v in truth["true_facts"].items() if v is not None}
    assert summary["cost_usd"] > 0 and not summary["harness_errors"]
    score = score_baseline(summary, truth, rules=RULES, specs=SPECS, ignore=IGNORE, statuses=DEV)
    assert score["pass"] and score["missed"] == [] and score["wrong_facts"] == []
    assert score["questions_asked"] == len(summary["questions"])


class OneFalseYes(TruthEchoClient):
    """Answers from the truth, except a false yes about tips, which it takes back when the simulator sends it back."""

    async def _create(self, kwargs):
        last = kwargs["messages"][-1]["content"]
        if last == SPECS["tips_income"].plain_question_hint:
            said = {"answer": "Yes, I got tips at work.", "revealed": {"tips_income": True}}
        elif last.startswith("Your last answer said tips_income is true"):
            said = {"answer": "No, I didn't get tips.", "revealed": {"tips_income": False}}
        else:
            return await super()._create(kwargs)
        self.calls.append(kwargs)
        return text_response(json.dumps(said), FakeUsage(input_tokens=1000, output_tokens=50))


@pytest.mark.anyio
async def test_b1_records_the_answer_the_contradiction_retry_kept_and_pays_for_both_calls():
    truth = load("truth", "H002")
    assert truth["true_facts"]["tips_income"] is False
    question_keys = {spec.plain_question_hint: [key] for key, spec in SPECS.items()}
    sim = ClientSimulator(truth, OneFalseYes(truth["true_facts"], question_keys, SPECS), specs=SPECS)
    summary = await run_full_organizer(truth, load("profiles", "H002"), sim, statuses=DEV,
                                       run_id=run_id_for("dev", "H002", "B1", 1), split="dev", trial=1)
    assert summary["facts_recorded"]["tips_income"] == {"value": False, "evidence": "No, I didn't get tips."}
    # The summary keeps what the simulator sent back, by question, as an agent run's summary does.
    n = next(q["n"] for q in summary["questions"] if q["fact_keys"] == ["tips_income"])
    assert summary["client_contradictions"] == [{"n": n, "tool_use_id": None, "corrected": [
        {"key": "tips_income", "first": True, "final": False}], "unresolved": []}]
    client_lines = [t["text"] for t in summary["transcript"] if t["role"] == "client"]
    assert "No, I didn't get tips." in client_lines
    assert not any("got tips at work" in line or "Your last answer" in line for line in client_lines)
    # One question per fact still, one more simulator call, and the cost counts every call.
    assert sim.calls == len(summary["questions"]) + 1 and summary["turns"] == len(summary["questions"])
    assert summary["usage"] == sim.total_usage.as_dict()
    assert summary["cost_usd"] == cost_usd("claude-haiku-5-5", summary["usage"], PRICES,
                                           prompt_tokens=sim.max_prompt_tokens)
    score = score_baseline(summary, truth, rules=RULES, specs=SPECS, ignore=IGNORE, statuses=DEV)
    assert score["pass"] and score["wrong_facts"] == []


# ---- B2 -----------------------------------------------------------------------


@pytest.mark.parametrize("hid", ["H002", "H011", "H010"])
def test_b2_is_the_profile_documents_on_file(hid):
    truth, profile = load("truth", hid), load("profiles", hid)
    summary = run_prior_year(truth, profile, run_id=run_id_for("dev", hid, "B2", 1), split="dev", trial=1)
    on_file = profile["prior_year_summary"]["documents_on_file"]
    assert summary["request_list"] == [{"rule_id": d["rule_id"], "count": d["count"]} for d in on_file]
    assert summary["questions"] == [] and summary["facts_recorded"] == {}
    assert summary["config"] == "B2" and summary["status"] == "pending_review" and summary["tool_calls"] is None
    assert summary["hook_denials"] == summary["question_denials"] == summary["client_contradictions"] == []
    assert summary["not_asked"] == [k for k, s in SPECS.items() if not s.is_follow_up]
    assert summary["cost_usd"] == 0.0 and summary["run_id"] == f"dev-{hid}-B2-t1"


def test_b2_never_reads_the_true_facts():
    truth = {"household_id": "H901"}
    profile = fake_profile(documents=[{"rule_id": "R001", "label": "Form W-2", "count": 2}])
    summary = run_prior_year(truth, profile, run_id="dev-H901-B2-t1", split="dev", trial=1)
    assert summary["request_list"] == [{"rule_id": "R001", "count": 2}]


def test_b2_missing_count_is_one_and_mismatched_household_raises():
    assert prior_year_list(fake_profile(documents=[{"rule_id": "R004", "label": "1099-INT"}])) == [
        {"rule_id": "R004", "count": 1}]
    with pytest.raises(ValueError):
        run_prior_year(fake_truth("H901"), fake_profile("H902"), run_id="x", split="dev", trial=1)


def test_b2_score_misses_what_changed_and_flags_what_ended():
    truth = fake_truth(w2_employer_count=1, interest_income=True)
    profile = fake_profile(documents=[{"rule_id": "R001", "label": "W-2", "count": 1},
                                      {"rule_id": "R017", "label": "1098-T", "count": 1}])
    summary = run_prior_year(truth, profile, run_id="dev-H901-B2-t1", split="dev", trial=1)
    score = score_baseline(summary, truth, rules=RULES, specs=SPECS, ignore=IGNORE, statuses=DEV)
    assert score["missed"] == [{"rule_id": "R004", "count": 1}]
    assert score["unnecessary"] == [{"kind": "rule", "rule_id": "R017", "count": 1}]
    assert score["fail_reasons"] == ["missed_documents"]
    assert score["probe_not_delivered"] == [] and score["incomplete"] is False and score["baseline"] is True
    assert score["first_miss"]["cause"] == "not_asked" and score["first_miss"]["fact"] == "interest_income"


def test_b2_split_runner_writes_scored_runs(tmp_path):
    paths = run_prior_year_split("train", runs_dir=tmp_path)
    assert len(paths) == len(TRAIN_IDS) == 20
    first = TRAIN_IDS[0]
    summary = json.loads((tmp_path / f"train-{first}-B2-t1.summary.json").read_text())
    score = json.loads((tmp_path / f"train-{first}-B2-t1.score.json").read_text())
    assert summary["split"] == "train" and score["run_id"] == summary["run_id"] and score["config"] == "B2"


LOCKED = ("locked_on: 2026-11-05\nprimary_config: A-sonnet\n"
          "configs: [A-haiku, A-sonnet, A-opus, A-split, B1, B2, B3-haiku, B3-sonnet, B3-opus]\ntrials: 3\n")


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def prereg_repo(tmp_path, text=LOCKED, *, commit=True):
    """A throwaway git repo holding eval/prereg.yaml, committed or not."""
    repo = tmp_path / "repo"
    (repo / "eval").mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("test\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "start")
    path = repo / "eval" / "prereg.yaml"
    path.write_text(text)
    if commit:
        git(repo, "add", "eval/prereg.yaml")
        git(repo, "commit", "-q", "-m", "prereg")
    return path


@pytest.mark.parametrize("why, make", [
    ("not locked", lambda tmp: prereg_repo(tmp, "locked_on: null\nprimary_config: null\n")),
    ("never committed", lambda tmp: prereg_repo(tmp, commit=False)),
    ("outside git", lambda tmp: (tmp / "prereg.yaml", (tmp / "prereg.yaml").write_text(LOCKED))[0]),
    ("B2 not listed", lambda tmp: prereg_repo(tmp, LOCKED.replace(" B2,", ""))),
])
def test_b2_split_runner_refuses_test_without_the_eval_run_lock(tmp_path, why, make):
    path = make(tmp_path)
    runs = tmp_path / "runs"
    with pytest.raises(PermissionError, match="the test split stays locked"):
        run_prior_year_split("test", runs_dir=runs, prereg_path=path)
    assert not runs.exists()
    assert baselines.main(["prior-year", "--split", "test", "--runs-dir", str(runs), "--prereg", str(path)]) == 2


def test_b2_split_runner_refuses_a_locked_prereg_with_uncommitted_edits(tmp_path, monkeypatch):
    path = prereg_repo(tmp_path)
    monkeypatch.setattr(eval_run, "REPO", path.parent.parent)
    path.write_text(LOCKED.replace("A-sonnet", "A-opus"))
    with pytest.raises(PermissionError, match="uncommitted changes"):
        run_prior_year_split("test", runs_dir=tmp_path / "runs", prereg_path=path)


def test_b2_on_test_stamps_the_prereg_commit_and_skips_unverified_households(tmp_path, monkeypatch):
    path = prereg_repo(tmp_path)
    monkeypatch.setattr(eval_run, "REPO", path.parent.parent)
    commit = git(path.parent.parent, "rev-parse", "HEAD")
    said = []
    assert run_prior_year_split("test", runs_dir=tmp_path / "runs", prereg_path=path, say=said.append) == []
    assert said == ["skipped 40 test households whose list depends on a rule that is not verified"]
    verified = [dataclasses.replace(r, status="verified") for r in RULES]
    monkeypatch.setattr(baselines, "load_rules", lambda: verified)
    paths = run_prior_year_split("test", runs_dir=tmp_path / "runs", prereg_path=path)
    assert len(paths) == 40
    for summary_path in paths:
        summary = json.loads(summary_path.read_text())
        score = json.loads(summary_path.with_name(f"{summary['run_id']}.score.json").read_text())
        assert summary["prereg_commit"] == score["prereg_commit"] == commit
        assert summary["split"] == "test" and score["baseline"] is True


# ---- B3 -----------------------------------------------------------------------


def docs_reply(*items):
    return json.dumps({"documents": [{"document": d, "count": c} for d, c in items]})


def test_b3_prompt_has_true_facts_and_hints_but_no_rules():
    truth = fake_truth(w2_employer_count=2, clean_energy_installed=True, clean_energy_install_year=2026)
    prompt = b3_prompt(truth["true_facts"], SPECS)
    facts = b3_facts(truth["true_facts"], SPECS)
    assert [f["fact"] for f in facts] == [k for k in SPECS if truth["true_facts"][k] is not None]
    assert {"fact": "clean_energy_install_year", "description": SPECS["clean_energy_install_year"].plain_question_hint,
            "value": 2026} in facts
    assert "ev_acquired_date" not in prompt
    assert SPECS["w2_employer_count"].plain_question_hint in prompt
    for rule in RULES:
        assert rule.id not in prompt
        if rule.document:
            assert rule.document not in prompt
    assert "{facts_json}" not in prompt and '{"documents"' in prompt


@pytest.mark.anyio
async def test_b3_one_call_no_tools_and_raw_items_kept():
    truth = fake_truth(w2_employer_count=2)
    client = ScriptedClient([docs_reply(("W-2", 2), ("Form 5695", 1))], usage=FakeUsage(1000, 200))
    summary = await run_model_list(truth, model="claude-sonnet-5-5", client=client, statuses=DEV,
                                   run_id="dev-H901-B3-sonnet-t1", split="dev", trial=1)
    assert len(client.calls) == 1
    call = client.calls[0]
    assert set(call) == {"model", "max_tokens", "messages", "output_config"}
    assert call["model"] == "claude-sonnet-5-5" and call["output_config"] == {"effort": B3_EFFORT}
    assert [m["role"] for m in call["messages"]] == ["user"]
    assert summary["model_list_items"] == [{"document": "W-2", "count": 2}, {"document": "Form 5695", "count": 1}]
    assert summary["config"] == "B3-sonnet" and summary["status"] == "pending_review"
    assert summary["request_list"] == summary["model_list_items"] and summary["tool_calls"] is None
    assert summary["hook_denials"] == summary["question_denials"] == summary["client_contradictions"] == []
    assert summary["usage"] == {"input_tokens": 1000, "output_tokens": 200, "cache_read_tokens": 0,
                                "cache_write_tokens": 0}
    assert summary["cost_usd"] == cost_usd("claude-sonnet-5-5", summary["usage"], PRICES, prompt_tokens=1000)
    assert summary["turns"] == 1 and summary["retry_reason"] is None


@pytest.mark.anyio
async def test_b3_items_score_through_aliases_with_trap_requests():
    truth = fake_truth(w2_employer_count=2, clean_energy_installed=True, clean_energy_install_year=2026)
    client = ScriptedClient([docs_reply(("W-2", 2), ("Form 5695", 1), ("solar invoice", 1), ("Photo ID", 1))])
    summary = await run_model_list(truth, model="claude-opus-5-5", client=client, statuses=DEV,
                                   run_id="dev-H901-B3-opus-t1", split="dev", trial=1)
    scored = score_list(summary["model_list_items"], truth, rules=RULES, specs=SPECS, ignore=IGNORE, statuses=DEV)
    assert scored["missed"] == [] and scored["ignored_requests"] == ["photo id"]
    assert [t["rule_ids"] for t in scored["trap_requests"]] == [["T001", "T002"], ["T001"]]
    full = score_baseline(summary, truth, rules=RULES, specs=SPECS, ignore=IGNORE, statuses=DEV)
    assert full["fail_reasons"] == ["too_many_unnecessary"] and full["config"] == "B3-opus"


@pytest.mark.anyio
async def test_b3_retries_once_on_invalid_json_without_replaying_the_bad_turn():
    truth = fake_truth()
    client = ScriptedClient(["Here is the list: W-2", docs_reply(("W-2", 1))])
    summary = await run_model_list(truth, model="claude-haiku-5-5", client=client, statuses=DEV,
                                   run_id="dev-H901-B3-haiku-t1", split="dev", trial=1)
    assert len(client.calls) == 2
    first, second = (c["messages"] for c in client.calls)
    assert len(second) == 1 and second[0]["role"] == "user"
    assert second[0]["content"].startswith(first[0]["content"])
    assert "the reply is not valid JSON" in second[0]["content"]
    assert summary["turns"] == 2 and summary["retry_reason"] == "the reply is not valid JSON"
    assert summary["usage"]["input_tokens"] == 200 and len(summary["raw_texts"]) == 2


@pytest.mark.anyio
async def test_b3_two_bad_replies_give_an_incomplete_run_with_what_they_cost():
    truth = fake_truth(w2_employer_count=2, interest_income=True)
    client = ScriptedClient(['{"documents": "W-2"}', "nope"], usage=FakeUsage(1000, 100))
    summary = await run_model_list(truth, model="claude-opus-5-5", client=client, statuses=DEV,
                                   run_id="dev-H901-B3-opus-t1", split="dev", trial=1)
    reasons = ['the reply has no "documents" list', "the reply is not valid JSON"]
    assert summary["status"] == NO_LIST and summary["config"] == "B3-opus"
    assert summary["model_list_items"] == [] and summary["request_list"] == []
    assert summary["harness_errors"] == [{"kind": "model_list", "reasons": reasons,
                                          "raw_texts": ['{"documents": "W-2"}', "nope"]}]
    assert summary["usage"]["input_tokens"] == 2000 and summary["turns"] == 2
    assert summary["cost_usd"] == cost_usd("claude-opus-5-5", summary["usage"], PRICES, prompt_tokens=1000) > 0
    score = score_baseline(summary, truth, rules=RULES, specs=SPECS, ignore=IGNORE, statuses=DEV)
    assert score["pass"] is False and score["incomplete"] is True
    assert score["fail_reasons"] == ["incomplete", "missed_documents"]
    assert score["missed"] == required_documents(truth["true_facts"], RULES, SPECS, DEV)


@pytest.mark.anyio
@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
async def test_b3_a_cut_off_or_refused_reply_is_retried(stop):
    client = ScriptedClient([text_response(docs_reply(("W-2", 1)), stop_reason=stop), docs_reply(("W-2", 1))])
    summary = await run_model_list(fake_truth(), model="claude-haiku-5-5", client=client, statuses=DEV,
                                   run_id="r", split="dev", trial=1, effort=None)
    assert len(client.calls) == 2 and "output_config" not in client.calls[0]
    assert summary["retry_reason"] in ("the reply was cut off at max_tokens", "the model declined to answer")


@pytest.mark.parametrize("text", [
    '{"documents": "W-2"}',
    '{"items": []}',
    '{"documents": [["W-2", 1]]}',
    '{"documents": [{"count": 1}]}',
    '{"documents": [{"document": "  ", "count": 1}]}',
    '{"documents": [{"document": "W-2", "count": 0}]}',
    '{"documents": [{"document": "W-2", "count": -1}]}',
    '{"documents": [{"document": "W-2", "count": true}]}',
    '{"documents": [{"document": "W-2", "count": 1.5}]}',
    '{"documents": [{"document": "W-2", "count": "two"}]}',
    "[1, 2]",
    "no json here",
    "",
])
def test_b3_bad_shapes_are_reply_errors(text):
    with pytest.raises(ReplyError):
        parse_model_list(text)


@pytest.mark.parametrize("text, items", [
    ('```json\n{"documents": [{"document": "W-2", "count": 2}]}\n```', [{"document": "W-2", "count": 2}]),
    ('Sure. {"documents": [{"document": " 1099-INT "}]} Done.', [{"document": "1099-INT", "count": 1}]),
    ('{"documents": [{"document": "W-2", "count": "3"}, {"document": "1098", "count": 1.0}]}',
     [{"document": "W-2", "count": 3}, {"document": "1098", "count": 1}]),
    ('{"documents": []}', []),
])
def test_b3_good_shapes_parse(text, items):
    assert parse_model_list(text) == items


def test_whole_count_and_config_names():
    assert [whole_count(v) for v in (None, 1, 2.0, " 4 ", 0, True, "x")] == [1, 1, 2, 4, None, None, None]
    assert [model_list_config(m) for m in ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")] == [
        "B3-haiku", "B3-sonnet", "B3-opus"]


# ---- shared scoring -----------------------------------------------------------


def test_score_baseline_fails_b1_on_a_wrong_fact_but_never_on_probes():
    truth = fake_truth(w2_employer_count=1)
    summary = baselines.base_summary(run_id="dev-H901-B1-t1", split="dev", household_id="H901", config="B1",
                                     trial=1, model=None)
    summary["facts_recorded"] = {"w2_employer_count": {"value": 1, "evidence": "one job"},
                                 "interest_income": {"value": True, "evidence": "some interest"}}
    summary["request_list"] = [{"rule_id": "R001", "count": 1}]
    summary["questions"] = [{"n": 1, "text": "q", "fact_keys": ["w2_employer_count"]}]
    score = score_baseline(summary, truth, rules=RULES, specs=SPECS, ignore=IGNORE, statuses=DEV,
                           b1_median_questions=36)
    assert score["fail_reasons"] == ["wrong_facts"]
    assert score["wrong_facts"][0]["fact"] == "interest_income"
    assert score["probe_not_delivered"] == [] and score["advice_routed"] and not score["claims_sent"]
    assert score["question_ratio_vs_full_organizer"] == pytest.approx(1 / 36)
