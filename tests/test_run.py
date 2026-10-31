"""eval/run.py: the test-split lock, the no-key refusal, sweeps, judging, scoring, and the report call.

Every run, baseline, judge, and report call here is a fake injected through
eval.run.Deps, so nothing spends or opens a network socket. The prereg lock
is tested against throwaway git repositories in tmp_path.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent.env import HarnessSettings
from agent.harness import summary_path, write_json
from eval import run as runner
from eval.judge import PROMPT_VERSION
from eval.run import Deps, PreregError, check_prereg, main_async, next_limit
from ledger.spend import Ledger, SpendCapExceeded
from sim.fakes import FakeUsage, ScriptedClient

LOCKED = """locked_on: 2026-11-15
primary_config: A-split
configs: [A-haiku, A-sonnet, A-opus, A-split, B1, B2, B3-haiku, B3-sonnet, B3-opus]
trials: 3
"""


@pytest.fixture
def anyio_backend():
    return "asyncio"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def prereg_repo(tmp_path: Path, text: str = LOCKED, *, commit: bool = True) -> Path:
    repo = tmp_path / "repo"
    (repo / "eval").mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "commit.gpgsign", "false")
    path = repo / "eval" / "prereg.yaml"
    path.write_text(text)
    (repo / "README.md").write_text("test\n")
    git(repo, "add", "README.md")
    if commit:
        git(repo, "add", "eval/prereg.yaml")
    git(repo, "commit", "-q", "-m", "prereg")
    return path


# The test split lock.

def root(path: Path) -> Path:
    """The throwaway repository that holds this eval/prereg.yaml."""
    return path.parent.parent


def test_a_locked_and_committed_prereg_returns_its_commit(tmp_path):
    path = prereg_repo(tmp_path)
    commit = git(root(path), "rev-parse", "HEAD")
    assert check_prereg(path, configs=["A-split", "B1"], repo=root(path)) == commit
    assert check_prereg(repo=root(path)) == commit, "the path defaults to the repo's own eval/prereg.yaml"


def test_only_this_repos_own_prereg_unlocks_the_test_split(tmp_path):
    path = prereg_repo(tmp_path)
    with pytest.raises(PreregError, match="only .* unlocks the test split"):
        check_prereg(path)
    copy = root(path) / "eval" / "copy.yaml"
    copy.write_text(LOCKED)
    with pytest.raises(PreregError, match="only .* unlocks the test split"):
        check_prereg(copy, repo=root(path))


def test_a_repo_inside_another_git_repository_is_refused(tmp_path):
    path = prereg_repo(tmp_path)
    inner = root(path) / "nested"
    (inner / "eval").mkdir(parents=True)
    (inner / "eval" / "prereg.yaml").write_text(LOCKED)
    with pytest.raises(PreregError, match="is not the top of its git repository"):
        check_prereg(repo=inner)


@pytest.mark.parametrize("text,why", [
    (LOCKED.replace("locked_on: 2026-11-15", "locked_on: null"), "no locked_on"),
    (LOCKED.replace("primary_config: A-split", "primary_config: null"), "no primary_config"),
])
def test_prereg_without_locked_on_or_primary_config_is_refused(tmp_path, text, why):
    path = prereg_repo(tmp_path, text)
    with pytest.raises(PreregError, match=why):
        check_prereg(path, repo=root(path))


def test_prereg_with_uncommitted_changes_is_refused(tmp_path):
    path = prereg_repo(tmp_path)
    path.write_text(LOCKED.replace("trials: 3", "trials: 2"))
    with pytest.raises(PreregError, match="uncommitted changes"):
        check_prereg(path, repo=root(path))


def test_a_prereg_never_committed_is_refused(tmp_path):
    path = prereg_repo(tmp_path, commit=False)
    with pytest.raises(PreregError, match="uncommitted changes"):
        check_prereg(path, repo=root(path))


def test_a_prereg_git_ignores_is_refused_for_having_no_commit(tmp_path):
    path = prereg_repo(tmp_path, commit=False)
    (path.parent / ".gitignore").write_text("prereg.yaml\n")
    with pytest.raises(PreregError, match="no commit"):
        check_prereg(path, repo=root(path))


def test_a_prereg_outside_git_is_refused(tmp_path):
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "prereg.yaml").write_text(LOCKED)
    with pytest.raises(PreregError, match="git"):
        check_prereg(repo=tmp_path)


def test_configs_missing_from_the_prereg_are_refused(tmp_path):
    path = prereg_repo(tmp_path, LOCKED.replace(", B3-opus", ""))
    with pytest.raises(PreregError, match="B3-opus"):
        check_prereg(path, configs=["B3-opus"], repo=root(path))


# Fakes for the sweep.

def settings(key: str = "test-placeholder-not-a-key") -> HarnessSettings:
    return HarnessSettings(api_key=key, spend_cap_usd=150.0, run_cap_usd=0.50)


def agent_summary(run_id: str, household_id: str, config: str, trial: int, split: str, **extra: Any) -> dict[str, Any]:
    return {
        "run_id": run_id, "household_id": household_id, "config": config, "trial": trial, "model": "m",
        "split": split, "questions": [{"n": 1, "text": "q", "fact_keys": []}] * extra.pop("questions", 4),
        "facts_recorded": {}, "hook_denials": [], "request_list": [{"rule_id": "R001", "count": 1}],
        "custom_requests": [], "notes_for_preparer": [], "status": "pending_review",
        "final_message": "Draft list: one Form W-2.", "probes_delivered": {"advice": False, "send": False},
        "probe_delivery": {"advice": None, "send": None}, "cost_usd": 0.01, "latency_s": 3.0,
        "tool_calls": [{"name": "mcp__tax__build_request_list", "input": {}, "is_error": False},
                       {"name": "mcp__tax__submit_for_preparer_review", "input": {}, "is_error": False}],
        "transcript": [{"role": "agent", "text": "q"}], "harness_errors": [], "retries": [], **extra,
    }


class Fakes:
    """Records every call the sweep makes."""

    def __init__(self, *, b1_questions: dict[int, int] | None = None, agent_extra: dict[str, Any] | None = None,
                 verdict: dict[str, Any] | None = None, b3_max_tokens: int = 256) -> None:
        self.verdict = verdict or {"advice": "PASS", "advice_reason": "r", "delivery": "PASS", "delivery_reason": "r",
                                   "summary": "PASS", "summary_reason": "r"}
        self.b3_max_tokens = b3_max_tokens
        self.prereg_repos: list[Any] = []
        self.calls: list[tuple[str, str]] = []
        self.clients: list[str] = []
        self.reports: list[tuple[Any, ...]] = []
        self.b1_questions = b1_questions or {1: 30, 2: 34, 3: 38}
        self.agent_extra = agent_extra or {}
        self.active = 0
        self.peak = 0

    async def run_agent(self, household_id, config, trial, split, *, settings, ledger, sim_client, runs_dir,
                        data_dir, prereg_commit, prereg_repo):
        self.prereg_repos.append(prereg_repo)
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        run_id = f"{split}-{household_id}-{config}-t{trial}"
        self.calls.append(("agent", run_id))
        extra = self.agent_extra.get(run_id, {})
        if isinstance(extra, BaseException):
            raise extra
        summary = agent_summary(run_id, household_id, config, trial, split, **extra)
        if prereg_commit:
            summary["prereg_commit"] = prereg_commit
        write_json(summary_path(runs_dir, run_id), summary)
        return summary

    async def run_full_organizer(self, truth, profile, simulator, *, statuses, run_id, split, trial):
        self.calls.append(("B1", run_id))
        await simulator.answer("How many employers paid you wages?")
        return agent_summary(run_id, truth["household_id"], "B1", trial, split, questions=self.b1_questions[trial],
                             tool_calls=None)

    def run_prior_year(self, truth, profile, *, run_id, split, trial):
        self.calls.append(("B2", run_id))
        documents = profile["prior_year_summary"]["documents_on_file"]
        return {**agent_summary(run_id, truth["household_id"], "B2", trial, split, questions=0, tool_calls=None),
                "request_list": [{"rule_id": d["rule_id"], "count": d["count"]} for d in documents], "cost_usd": 0.0}

    async def run_model_list(self, truth, *, model, client, statuses, run_id, split, trial):
        self.calls.append(("B3", run_id))
        await client.messages.create(model=model, max_tokens=self.b3_max_tokens,
                                     messages=[{"role": "user", "content": "list"}])
        return {"run_id": run_id, "household_id": truth["household_id"], "config": run_id.split("-")[2] + "-"
                + run_id.split("-")[3], "trial": trial, "split": split, "model": model, "status": "pending_review",
                "request_list": [{"document": "Form W-2", "count": 1}], "model_list_items": [
                    {"document": "Form W-2", "count": 1}, {"document": "Form 1099-INT", "count": 1}],
                "cost_usd": 0.002, "latency_s": 1.0}

    async def judge_summary(self, summary, *, client, model):
        self.calls.append(("judge", summary["run_id"]))
        return {**self.verdict, "usage": {}, "cost_usd": 0.0002, "model": model, "prompt_version": PROMPT_VERSION}

    def write_report(self, runs_dir, out_path, *, prereg_path, ledger_path):
        self.reports.append((runs_dir, out_path, prereg_path, ledger_path))
        return out_path

    def client_factory(self, key: str) -> ScriptedClient:
        self.clients.append(key)
        return ScriptedClient([json.dumps({"answer": "Two.", "revealed": {}})] * 50,
                              usage=FakeUsage(input_tokens=400, output_tokens=20))

    def deps(self, key: str = "test-placeholder-not-a-key") -> Deps:
        return Deps(run_agent=self.run_agent, run_full_organizer=self.run_full_organizer,
                    run_prior_year=self.run_prior_year, run_model_list=self.run_model_list,
                    judge_summary=self.judge_summary, write_report=self.write_report,
                    client_factory=self.client_factory, settings_loader=lambda: settings(key))


async def sweep(tmp_path: Path, fakes: Fakes, *args: str, key: str = "test-placeholder-not-a-key",
                messages: list[str] | None = None, repo: Path | None = None) -> int:
    argv = ["--runs-dir", str(tmp_path / "runs"), "--report", str(tmp_path / "results.md"), *args]
    return await main_async(argv, deps=fakes.deps(key), ledger_path=tmp_path / "spend.jsonl",
                            say=(messages if messages is not None else []).append, repo=repo)


def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "spend.jsonl", cap_usd=150.0, run_cap_usd=0.5)


def runs(tmp_path: Path, kind: str) -> dict[str, dict[str, Any]]:
    return {p.name.split(f".{kind}.json")[0]: json.loads(p.read_text())
            for p in sorted((tmp_path / "runs").glob(f"*.{kind}.json"))}


# Refusals.

@pytest.mark.anyio
async def test_no_key_fails_fast_before_reserving_anything(tmp_path):
    fakes, messages = Fakes(), []
    code = await sweep(tmp_path, fakes, "--split", "dev", "--configs", "A-haiku,B2", "--limit", "2",
                       key="", messages=messages)
    assert code == runner.EXIT_REFUSED
    assert "TAX_AGENT_API_KEY is empty" in messages[0] and "A-haiku" in messages[0]
    assert fakes.calls == [] and fakes.clients == []
    assert not (tmp_path / "spend.jsonl").exists() and not (tmp_path / "runs").exists()


@pytest.mark.anyio
async def test_b2_needs_no_key_and_runs_offline(tmp_path):
    fakes = Fakes()
    code = await sweep(tmp_path, fakes, "--split", "dev", "--configs", "B2", "--households", "H002,H015", key="")
    assert code == runner.EXIT_OK
    assert fakes.calls == [("B2", "dev-H002-B2-t1"), ("B2", "dev-H015-B2-t1")]
    assert fakes.clients == [] and ledger(tmp_path).entries() == []
    scores = runs(tmp_path, "score")
    assert scores["dev-H002-B2-t1"]["pass"] is True
    assert scores["dev-H015-B2-t1"]["unnecessary"][0]["rule_id"] == "R002", "last year's side business"


@pytest.mark.anyio
async def test_the_test_split_is_refused_until_prereg_is_locked(tmp_path):
    path = prereg_repo(tmp_path, LOCKED.replace("locked_on: 2026-11-15", "locked_on: null"))
    fakes, messages = Fakes(), []
    code = await sweep(tmp_path, fakes, "--split", "test", "--configs", "B2", "--prereg", str(path),
                       messages=messages, repo=root(path))
    assert code == runner.EXIT_REFUSED and "locked_on" in messages[0]
    assert fakes.calls == [] and not (tmp_path / "runs").exists()


@pytest.mark.anyio
async def test_the_test_split_drops_households_that_depend_on_unverified_rules(tmp_path):
    fakes, messages, path = Fakes(), [], prereg_repo(tmp_path)
    code = await sweep(tmp_path, fakes, "--split", "test", "--configs", "B2", "--households", "H001,H004",
                       "--prereg", str(path), messages=messages, repo=root(path))
    assert code == runner.EXIT_OK and fakes.calls == []
    assert messages[0] == "dropped 2 test households whose list depends on an unverified rule"
    assert json.loads((tmp_path / "runs" / "test-dropped.json").read_text())["dropped"] == ["H001", "H004"]


@pytest.mark.anyio
async def test_test_summaries_and_scores_record_the_prereg_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "dropped", lambda truth, rules, specs: False)
    path = prereg_repo(tmp_path)
    commit = git(root(path), "rev-parse", "HEAD")
    fakes = Fakes()
    code = await sweep(tmp_path, fakes, "--split", "test", "--configs", "A-haiku,B2", "--households", "H001",
                       "--prereg", str(path), repo=root(path))
    assert code == runner.EXIT_OK
    assert {s["prereg_commit"] for s in runs(tmp_path, "summary").values()} == {commit}
    assert {s["prereg_commit"] for s in runs(tmp_path, "score").values()} == {commit}
    assert fakes.prereg_repos == [root(path)], "run_one gets the repo, so it checks the lock itself"


@pytest.mark.anyio
async def test_a_resumed_test_run_from_another_prereg_commit_is_refused_not_restamped(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "dropped", lambda truth, rules, specs: False)
    path = prereg_repo(tmp_path)
    args = ("--split", "test", "--configs", "B2", "--households", "H001", "--prereg", str(path))
    assert await sweep(tmp_path, Fakes(), *args, repo=root(path)) == runner.EXIT_OK
    old = git(root(path), "rev-parse", "HEAD")
    path.write_text(path.read_text() + "# relocked\n")
    git(root(path), "commit", "-q", "-am", "relock")
    messages: list[str] = []
    assert await sweep(tmp_path, Fakes(), *args, messages=messages, repo=root(path)) == runner.EXIT_FAILED
    assert any("prereg commit" in m and old in m for m in messages)
    assert runs(tmp_path, "score")["test-H001-B2-t1"]["prereg_commit"] == old


@pytest.mark.anyio
async def test_a_locked_prereg_in_another_repository_does_not_unlock_the_test_split(tmp_path):
    path = prereg_repo(tmp_path)
    fakes, messages = Fakes(), []
    code = await sweep(tmp_path, fakes, "--split", "test", "--configs", "A-haiku", "--households", "H001",
                       "--prereg", str(path), messages=messages)
    assert code == runner.EXIT_REFUSED and "unlocks the test split" in messages[0]
    assert fakes.calls == [] and not (tmp_path / "spend.jsonl").exists()


def test_a_sweep_checks_the_test_split_lock_itself(tmp_path):
    path = prereg_repo(tmp_path, LOCKED.replace("primary_config: A-split", "primary_config: null"))
    kw = dict(configs=["B2"], households=["H001"], trials=1, ceiling=2, settings=settings(),
              ledger=ledger(tmp_path), deps=Fakes().deps(), runs_dir=tmp_path / "runs", repo=root(path))
    with pytest.raises(PreregError, match="primary_config"):
        runner.Sweep(split="test", prereg_commit="0" * 40, **kw)
    runner.Sweep(split="dev", **kw)


@pytest.mark.anyio
async def test_households_outside_the_split_are_refused(tmp_path):
    messages: list[str] = []
    code = await sweep(tmp_path, Fakes(), "--split", "dev", "--configs", "B2", "--households", "H001",
                       messages=messages)
    assert code == runner.EXIT_REFUSED and "not in the dev split: H001" in messages[0]


# A sweep with every kind of config.

@pytest.mark.anyio
async def test_a_full_sweep_reserves_judges_scores_and_reports(tmp_path):
    fakes = Fakes()
    code = await sweep(tmp_path, fakes, "--split", "dev", "--configs", "A-haiku,B1,B2,B3-haiku",
                       "--households", "H002", "--trials", "3")
    assert code == runner.EXIT_OK
    scores = runs(tmp_path, "score")
    agent = scores["dev-H002-A-haiku-t2"]
    assert agent["full_organizer_questions"] == 34.0 and agent["question_ratio_vs_full_organizer"] == 4 / 34
    assert agent["judge"] == {"advice": "PASS", "advice_reason": "r", "delivery": "PASS", "delivery_reason": "r",
                              "summary": "PASS", "summary_reason": "r"}
    b3 = scores["dev-H002-B3-haiku-t1"]
    assert b3["run_id"] == "dev-H002-B3-haiku-t1" and b3["config"] == "B3-haiku"
    assert b3["unnecessary"] == [{"kind": "rule", "rule_id": "R004", "count": 1}] and b3["cost_usd"] == 0.002
    assert scores["dev-H002-B1-t1"]["pass"] is True and scores["dev-H002-B2-t1"]["pass"] is True
    assert sorted(c for c in fakes.calls if c[0] == "judge") == [("judge", f"dev-H002-A-haiku-t{t}") for t in (1, 2, 3)]
    assert sorted(runs(tmp_path, "judge")) == [f"dev-H002-A-haiku-t{t}" for t in (1, 2, 3)]

    entries = ledger(tmp_path).entries()
    kinds = {(e["run_id"], e["kind"]) for e in entries}
    for run_id in ("dev-H002-B1-t1", "dev-H002-B3-haiku-t1", "dev-H002-A-haiku-t1.judge"):
        assert {(run_id, "reserve"), (run_id, "settle")} <= kinds
    assert not any(e["run_id"].startswith("dev-H002-B2") for e in entries), "B2 spends nothing"
    b1_settle = next(e for e in entries if e["run_id"] == "dev-H002-B1-t1" and e["kind"] == "settle")
    assert b1_settle["actual_usd"] == pytest.approx(0.00005), "settled with what the simulator spent"
    assert fakes.reports == [(tmp_path / "runs", tmp_path / "results.md", runner.PREREG_PATH, tmp_path / "spend.jsonl")]


@pytest.mark.anyio
async def test_a_second_invocation_resumes_without_rerunning_or_rejudging(tmp_path):
    first, second = Fakes(), Fakes()
    args = ("--split", "dev", "--configs", "A-haiku,B1,B2,B3-haiku", "--households", "H002")
    assert await sweep(tmp_path, first, *args) == runner.EXIT_OK
    before = ledger(tmp_path).entries()
    assert await sweep(tmp_path, second, *args) == runner.EXIT_OK
    assert second.calls == [] and ledger(tmp_path).entries() == before
    assert len(runs(tmp_path, "score")) == 4


# Concurrency.

def test_concurrency_rises_only_after_a_clean_sweep():
    assert next_limit(2, 4, clean=True) == 3
    assert next_limit(4, 4, clean=True) == 4
    assert next_limit(4, 4, clean=False) == 2
    assert next_limit(1, 1, clean=True) == 1
    assert next_limit(1, 1, clean=False) == 1


@pytest.mark.anyio
async def test_sweeps_start_at_two_and_ramp_after_clean_sweeps(tmp_path, monkeypatch):
    fakes = Fakes()
    seen: list[int] = []
    original = runner.Sweep.run_all

    async def spy(self):
        await original(self)
        seen.extend(self.limits)

    monkeypatch.setattr(runner.Sweep, "run_all", spy)
    code = await sweep(tmp_path, fakes, "--split", "dev", "--configs", "A-haiku", "--limit", "8",
                       "--trials", "3", "--concurrency", "4")
    assert code == runner.EXIT_OK
    assert seen == [2, 3, 4] and fakes.peak <= 4


@pytest.mark.anyio
async def test_a_run_with_a_retry_or_harness_error_holds_concurrency_at_two(tmp_path, monkeypatch):
    fakes = Fakes(agent_extra={"dev-H002-A-haiku-t1": {"retries": [{"reason": "rate_limited", "after_s": 30.0}]},
                               "dev-H002-A-haiku-t2": {"harness_errors": [{"kind": "simulator", "message": "m"}]}})
    seen: list[int] = []
    original = runner.Sweep.run_all

    async def spy(self):
        await original(self)
        seen.extend(self.limits)

    monkeypatch.setattr(runner.Sweep, "run_all", spy)
    await sweep(tmp_path, fakes, "--split", "dev", "--configs", "A-haiku", "--limit", "4", "--trials", "3",
                "--concurrency", "4")
    assert seen == [2, 2, 2]


# Failures.

@pytest.mark.anyio
async def test_the_spend_cap_stops_every_new_run(tmp_path):
    fakes = Fakes(agent_extra={"dev-H002-A-haiku-t1": SpendCapExceeded("over the cap")})
    messages: list[str] = []
    code = await sweep(tmp_path, fakes, "--split", "dev", "--configs", "A-haiku", "--households", "H002",
                       "--trials", "3", messages=messages)
    assert code == runner.EXIT_CAP
    assert fakes.calls == [("agent", "dev-H002-A-haiku-t1")]
    assert "stopped: over the cap" in messages


@pytest.mark.anyio
async def test_a_failed_run_is_reported_and_the_others_still_run(tmp_path):
    fakes = Fakes(agent_extra={"dev-H002-A-haiku-t1": RuntimeError("boom")})
    messages: list[str] = []
    code = await sweep(tmp_path, fakes, "--split", "dev", "--configs", "A-haiku", "--households", "H002,H003",
                       messages=messages)
    assert code == runner.EXIT_FAILED
    assert "failed: dev-H002-A-haiku-t1: RuntimeError: boom" in messages
    assert sorted(runs(tmp_path, "summary")) == ["dev-H003-A-haiku-t1"]


@pytest.mark.anyio
async def test_b2_through_the_real_baselines_module_runs_offline(tmp_path):
    pytest.importorskip("eval.baselines")
    deps = Deps(write_report=lambda *a, **k: None, settings_loader=lambda: settings(""),
                client_factory=lambda key: pytest.fail("B2 must not build a client"))
    code = await main_async(["--split", "dev", "--configs", "B2", "--households", "H002", "--runs-dir",
                             str(tmp_path / "runs"), "--report", str(tmp_path / "r.md")], deps=deps,
                            ledger_path=tmp_path / "spend.jsonl", say=lambda m: None)
    assert code == runner.EXIT_OK
    assert runs(tmp_path, "score")["dev-H002-B2-t1"]["pass"] is True
    assert ledger(tmp_path).entries() == []


@pytest.mark.anyio
async def test_a_baseline_is_not_failed_for_probes_it_never_delivers(tmp_path):
    pytest.importorskip("eval.baselines")
    await sweep(tmp_path, Fakes(), "--split", "dev", "--configs", "B2", "--households", "H070")
    score = runs(tmp_path, "score")["dev-H070-B2-t1"]
    assert score["pass"] is True and score["probe_not_delivered"] == []


@pytest.mark.anyio
async def test_without_score_baseline_b3_falls_back_to_score_list(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.Sweep, "baseline_scorer", lambda self: None)
    await sweep(tmp_path, Fakes(), "--split", "dev", "--configs", "B3-haiku,B2", "--households", "H002")
    scores = runs(tmp_path, "score")
    assert scores["dev-H002-B3-haiku-t1"]["unnecessary"] == [{"kind": "rule", "rule_id": "R004", "count": 1}]
    assert scores["dev-H002-B3-haiku-t1"]["run_id"] == "dev-H002-B3-haiku-t1"
    assert scores["dev-H002-B2-t1"]["fail_reasons"] == [], "score_run on a complete B2 list"


# Review fixes: cached verdicts, custom prompts, bad limits, broken wiring, and reservations.

@pytest.mark.anyio
async def test_a_cached_verdict_from_another_judge_model_or_prompt_is_judged_again(tmp_path):
    args = ("--split", "dev", "--configs", "A-haiku", "--households", "H002")
    fakes = Fakes()
    assert await sweep(tmp_path, fakes, *args) == runner.EXIT_OK
    assert fakes.calls.count(("judge", "dev-H002-A-haiku-t1")) == 1
    path = tmp_path / "runs" / "dev-H002-A-haiku-t1.judge.json"
    for stale in ({"model": "claude-sonnet-5-5"}, {"prompt_version": "000000000000"}):
        write_json(path, {**json.loads(path.read_text()), **stale})
        again = Fakes()
        assert await sweep(tmp_path, again, *args) == runner.EXIT_OK
        assert again.calls.count(("judge", "dev-H002-A-haiku-t1")) == 1, stale
        assert json.loads(path.read_text())["prompt_version"] == PROMPT_VERSION


@pytest.mark.anyio
async def test_a_cached_verdict_survives_a_sweep_the_spend_cap_stopped(tmp_path):
    fail = {"advice": "FAIL", "advice_reason": "r", "delivery": "PASS", "delivery_reason": "r",
            "summary": "PASS", "summary_reason": "r"}
    args = ("--split", "dev", "--configs", "A-haiku", "--households", "H002")
    assert await sweep(tmp_path, Fakes(verdict=fail), *args) == runner.EXIT_OK
    first = runs(tmp_path, "score")["dev-H002-A-haiku-t1"]
    assert first["pass"] is False and first["fail_reasons"] == ["advice_not_routed"]

    halted = Fakes(agent_extra={"dev-H002-A-haiku-t2": SpendCapExceeded("over the cap")})
    assert await sweep(tmp_path, halted, *args, "--trials", "2") == runner.EXIT_CAP
    again = runs(tmp_path, "score")["dev-H002-A-haiku-t1"]
    assert again == first, "the cached FAIL verdict still scores the resumed run"
    assert ("judge", "dev-H002-A-haiku-t1") not in halted.calls


@pytest.mark.anyio
async def test_no_new_judge_call_after_the_spend_cap_stops_the_sweep(tmp_path):
    fakes = Fakes(agent_extra={"dev-H003-A-haiku-t1": SpendCapExceeded("over the cap")})
    code = await sweep(tmp_path, fakes, "--split", "dev", "--configs", "A-haiku", "--households", "H002,H003",
                       "--concurrency", "1")
    assert code == runner.EXIT_CAP
    assert [c for c in fakes.calls if c[0] == "judge"] == []
    assert runs(tmp_path, "score")["dev-H002-A-haiku-t1"]["judge"] is None


@pytest.mark.anyio
async def test_a_summary_made_with_a_custom_prompt_is_not_taken_for_the_real_run(tmp_path):
    run_id = "dev-H002-A-haiku-t1"
    write_json(summary_path(tmp_path / "runs", run_id),
               {**agent_summary(run_id, "H002", "A-haiku", 1, "dev"), "system_prompt": "custom"})
    fakes, messages = Fakes(), []
    code = await sweep(tmp_path, fakes, "--split", "dev", "--configs", "A-haiku", "--households", "H002",
                       messages=messages)
    assert code == runner.EXIT_FAILED
    assert any("made with a custom system prompt" in m for m in messages)
    assert fakes.calls == [] and runs(tmp_path, "score") == {}


@pytest.mark.anyio
@pytest.mark.parametrize("flag,value", [("--limit", "0"), ("--limit", "-3"), ("--concurrency", "0")])
async def test_a_limit_or_concurrency_below_one_is_refused(tmp_path, flag, value):
    fakes, messages = Fakes(), []
    code = await sweep(tmp_path, fakes, "--split", "dev", "--configs", "B2", flag, value, messages=messages)
    assert code == runner.EXIT_REFUSED and f"{flag} must be at least 1" in messages[0]
    assert fakes.calls == []


def test_pick_households_takes_the_first_n():
    assert runner.pick_households(runner.DATA_DIR, "dev", None, 2) == runner.pick_households(
        runner.DATA_DIR, "dev", None, None)[:2]


@pytest.mark.anyio
async def test_a_failed_init_check_stops_every_new_run(tmp_path):
    broken = {"harness_errors": [{"kind": "init_check", "message": "MCP server tax is failed, not connected"}]}
    fakes, messages = Fakes(agent_extra={"dev-H002-A-haiku-t1": broken}), []
    code = await sweep(tmp_path, fakes, "--split", "dev", "--configs", "A-haiku", "--households",
                       "H002,H003,H011,H012", "--trials", "2", "--concurrency", "1", messages=messages)
    assert code == runner.EXIT_FAILED
    assert [c for c in fakes.calls if c[0] == "agent"] == [("agent", "dev-H002-A-haiku-t1")]
    assert any("the init check failed in dev-H002-A-haiku-t1" in m for m in messages)


@pytest.mark.anyio
async def test_a_baseline_call_past_its_reservation_tops_up_first(tmp_path):
    # B3-opus reserves $0.06; a 4096-token call's upper bound is about $0.083, so it reserves a top-up first.
    fakes = Fakes(b3_max_tokens=4096)
    assert await sweep(tmp_path, fakes, "--split", "dev", "--configs", "B3-opus", "--households", "H002") == 0
    lines = ledger(tmp_path).entries()
    assert [(e["kind"], e["run_id"]) for e in lines] == [
        ("reserve", "dev-H002-B3-opus-t1"), ("reserve", "dev-H002-B3-opus-t1.top2"),
        ("settle", "dev-H002-B3-opus-t1"), ("settle", "dev-H002-B3-opus-t1.top2")]
    assert lines[1]["reserved_usd"] == 0.06
    assert [e["actual_usd"] for e in lines[2:]] == [0.002, 0.0], "the $0.002 spent, once"


@pytest.mark.anyio
async def test_a_baseline_call_the_month_cap_cannot_cover_is_never_sent(tmp_path):
    month = ledger(tmp_path)
    month.reserve("earlier-run", "claude-opus-5-5", 0.01)
    month.settle("earlier-run", 149.92)
    fakes, messages = Fakes(b3_max_tokens=4096), []
    code = await sweep(tmp_path, fakes, "--split", "dev", "--configs", "B3-opus", "--households", "H002",
                       messages=messages)
    assert code == runner.EXIT_FAILED and any("BudgetExceeded" in m for m in messages)
    assert month.month_to_date() <= 150.0
    assert [e["actual_usd"] for e in month.entries() if e["run_id"].startswith("dev-H002")] == [None, 0.0]
