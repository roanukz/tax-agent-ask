"""Runs configs over a split, judges and scores every run, and writes the report.

    uv run python -m eval.run --split dev --configs A-haiku,A-sonnet,B1,B2 --trials 1 --limit 10
    uv run python -m eval.run --split test --prereg eval/prereg.yaml

Options: --configs names the configs, and every split but test needs it;
--trials is the number of trials (default 1). The test split runs what
the locked eval/prereg.yaml lists: --configs and --trials default to its
configs and trials, and a sweep naming another set of configs or another
trial count is refused. --households H002,H009 or --limit N picks
households from the split (default: all of them); --concurrency N is the
most runs at once (default 2); --report is where eval.report writes
(default docs/results.md).

Phases:
1. Refusals, before anything is reserved. The test split needs this
   repo's own eval/prereg.yaml (no other file unlocks it) with locked_on
   and primary_config set, no uncommitted changes to it, and a commit in
   git log; that commit's hash goes into every test summary and score, and
   the Sweep and agent.harness.run_one check the lock again themselves.
   Any config but B2 needs TAX_AGENT_API_KEY in .env. On test, households
   whose list depends on an unverified rule are dropped from the sweep
   (PRD 6.6), and the count is printed.
2. Runs, one sweep per trial. Agent configs go through agent.harness.run_one
   and baselines through eval.baselines: B1 with a ClientSimulator, B2 with
   no model at all, B3 with a Messages client. Every model call reserves in
   the ledger first and settles after, and a run spends only what it has
   reserved, topping up against the month cap when it needs more
   (agent.harness.Reservation). A finished run (its summary exists) is
   skipped without spending; a summary made with a custom system prompt is
   refused, since it is not that config's run. A sweep starts at 2
   concurrent runs, and the limit rises by one, up to --concurrency, only
   after a clean sweep: no failed run, no harness error, and no 429 retry.
   An unclean sweep drops it back to 2. A ledger.spend.SpendCapExceeded
   from any job (an agent run, B1, B3, or a judge call) stops every new
   run, and eval.run writes no summary or verdict for the job it stopped,
   so the next invocation runs it again. That relies on the spend
   contract: the month cap refusing a job's first reservation raises it,
   and when the month cap refuses a call partway through a job,
   ReservedClient and agent.harness.run_one have to raise it too, run_one
   writing the trace but no summary. A month-cap refusal reported any
   other way, as BudgetExceeded or as a run_cap stop with a summary, is a
   failed job or a finished run here, not a stop. A run whose init check
   failed stops every new run too, since the wiring is broken for every
   run after it. It measured no agent behavior, so it is neither judged
   nor scored, and it is never resumed: the next invocation moves its
   summary aside to {run_id}.init_failed.json (and its trace to
   {run_id}.init_failed.trace.json) and runs it again.
3. Judging: each agent run once, through eval.judge, cached in
   eval/runs/{run_id}.judge.json. A cached verdict is always used, even
   after the spend cap stopped the sweep; only new judge calls stop.
4. Scoring, free and redone every time, with the household's B1 median
   question count when B1 runs exist: eval.score.score_run for agent runs,
   and eval.baselines.score_baseline for baselines (eval.score.score_list
   on model_list_items for B3), since a baseline never talks to the client
   and so can't fail on a probe. Without score_baseline, B1 and B2 fall
   back to score_run and B3 to score_list. Written to
   eval/runs/{run_id}.score.json.
5. eval.report.write_report.

eval.baselines, eval.judge, and eval.report are imported when first
needed, so this module can be tested with fakes before they exist.

Exit codes: 0 done, 1 some run or judge failed, 2 refused, 3 the spend cap stopped the sweep.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import yaml

from agent import harness
from agent.configs import CONFIGS, JUDGE_MODEL, JUDGE_RESERVE_USD, SIMULATOR_MODEL, Config, get_config
from agent.env import DEFAULT_DOTENV, HarnessSettings, load_settings, messages_client
from agent.harness import (
    INIT_CHECK, TEST_SPLIT, Budget, Reservation, run_id_for, summary_path, trace_path, write_json,
)
from eval.score import dropped, score_list, score_run
from ledger.spend import DEFAULT_LEDGER, Ledger, SpendCapExceeded, load_prices
from rules import engine
from sim.budget import BudgetedClient
from sim.simulator import ClientSimulator

REPO = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO / "eval" / "runs"
DATA_DIR = REPO / "data"
PREREG_RELATIVE = "eval/prereg.yaml"
PREREG_PATH = REPO / PREREG_RELATIVE
REPORT_PATH = REPO / "docs" / "results.md"
START_CONCURRENCY = 2
SPLITS = ("train", "dev", "test")
JUDGE_FIELDS = ("advice", "advice_reason", "delivery", "delivery_reason", "summary", "summary_reason")
NO_MODEL = "prior_year"
GUARD = "calls"

EXIT_OK, EXIT_FAILED, EXIT_REFUSED, EXIT_CAP = 0, 1, 2, 3


class Refused(RuntimeError):
    """The sweep can't start; nothing was reserved or run."""


class PreregError(Refused):
    """The test split is locked until eval/prereg.yaml is set and committed."""


def git(args: Sequence[str], cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if done.returncode != 0:
        raise PreregError(f"git {' '.join(args)} failed in {cwd}: {done.stderr.strip()}")
    return done.stdout.strip()


def check_prereg(path: Path | None = None, *, configs: Sequence[str] = (), repo: Path | None = None) -> str:
    """The commit hash of the repo's locked eval/prereg.yaml, or PreregError saying why the test split stays locked.

    Only <repo>/eval/prereg.yaml unlocks the test split, and repo must be
    the top of its own git repository. repo is this repository (REPO, read
    when called) except in tests; the CLI has no way to change it.
    """
    root = Path(REPO if repo is None else repo).resolve()
    expected = root / PREREG_RELATIVE
    path = expected if path is None else Path(path).resolve()
    if path != expected:
        raise PreregError(f"only {expected} unlocks the test split, not {path}")
    if not path.is_file():
        raise PreregError(f"{path} does not exist")
    data = yaml.safe_load(path.read_text()) or {}
    for name in ("locked_on", "primary_config"):
        if not data.get(name):
            raise PreregError(f"{path.name} has no {name}; set it and commit the file before the test sweep")
    listed = set(data.get("configs") or [])
    unlisted = [c for c in configs if c not in listed]
    if unlisted:
        raise PreregError(f"{path.name} does not list {', '.join(unlisted)}; the test sweep runs only preregistered configs")
    top = Path(git(["rev-parse", "--show-toplevel"], root)).resolve()
    if top != root:
        raise PreregError(f"{root} is not the top of its git repository ({top} is)")
    if git(["status", "--porcelain", "--", PREREG_RELATIVE], root):
        raise PreregError(f"{path.name} has uncommitted changes; commit it before the test sweep")
    commit = git(["log", "-1", "--format=%H", "--", PREREG_RELATIVE], root)
    if not commit:
        raise PreregError(f"git log shows no commit of {path.name}; commit it before the test sweep")
    return commit


def prereg_plan(path: Path | None = None, *, repo: Path | None = None) -> tuple[list[str], int, str]:
    """(configs, trials, commit): what the locked eval/prereg.yaml says the one test sweep runs, and its commit."""
    commit = check_prereg(path, repo=repo)
    data = yaml.safe_load((Path(REPO if repo is None else repo).resolve() / PREREG_RELATIVE).read_text()) or {}
    listed, trials = data.get("configs"), data.get("trials")
    if not isinstance(listed, list) or not listed:
        raise PreregError(f"{PREREG_RELATIVE} lists no configs for the test sweep")
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
        raise PreregError(f"{PREREG_RELATIVE} needs trials, a whole number of at least 1, not {trials!r}")
    return config_names(str(c) for c in listed), trials, commit


def config_names(names: Iterable[str]) -> list[str]:
    """Each named config once, in order; an unknown name raises KeyError."""
    return list(dict.fromkeys(get_config(n.strip()).name for n in names if n.strip()))


def sweep_plan(args: argparse.Namespace, repo: Path | None = None) -> tuple[list[str], int, str | None]:
    """(configs, trials, prereg commit) for this invocation.

    The test split runs what the locked prereg lists: --configs and --trials
    default to its configs and trials, and any other set of configs or
    trial count is refused, so the one test sweep can't run a subset or
    extra trials. Other splits need --configs, and --trials defaults to 1.
    """
    named = config_names(args.configs.split(",")) if args.configs else None
    if args.split != TEST_SPLIT:
        if named is None:
            raise Refused(f"--configs is required on the {args.split} split")
        return named, 1 if args.trials is None else args.trials, None
    listed, planned, commit = prereg_plan(args.prereg, repo=repo)
    configs = listed if named is None else named
    trials = planned if args.trials is None else args.trials
    if set(configs) != set(listed):
        raise Refused(f"the test sweep runs every config in {PREREG_RELATIVE} ({', '.join(listed)}), "
                      f"not {', '.join(configs) or 'none'}; leave out --configs to run them")
    if trials != planned:
        raise Refused(f"{PREREG_RELATIVE} sets {planned} trials for the test sweep, not {trials}; "
                      "leave out --trials to run them")
    return configs, trials, commit


def live_client(api_key: str) -> Any:
    return messages_client(api_key)


@dataclass
class Deps:
    """What the sweep calls. None means the eval module of that name, imported when first needed."""

    run_agent: Callable[..., Any] = harness.run_one
    run_full_organizer: Callable[..., Any] | None = None
    run_prior_year: Callable[..., Any] | None = None
    run_model_list: Callable[..., Any] | None = None
    judge_summary: Callable[..., Any] | None = None
    score_baseline: Callable[..., Any] | None = None
    write_report: Callable[..., Any] | None = None
    client_factory: Callable[[str], Any] = live_client
    settings_loader: Callable[[], HarnessSettings] = field(
        default=lambda: load_settings(DEFAULT_DOTENV, require_key=False))

    def get(self, name: str, module: str) -> Callable[..., Any]:
        fn = getattr(self, name)
        return fn if fn is not None else getattr(importlib.import_module(module), name)


@dataclass(frozen=True)
class Job:
    split: str
    household_id: str
    config: str
    trial: int

    @property
    def run_id(self) -> str:
        return run_id_for(self.split, self.household_id, self.config, self.trial)

    @property
    def cfg(self) -> Config:
        return get_config(self.config)


def next_limit(limit: int, ceiling: int, clean: bool) -> int:
    """Concurrency for the next sweep: one more after a clean sweep, back to the start after any trouble."""
    start = min(START_CONCURRENCY, ceiling)
    return min(ceiling, limit + 1) if clean else start


def is_clean(summary: Mapping[str, Any]) -> bool:
    return not summary.get("harness_errors") and not summary.get("retries")


def init_failed(summary: Mapping[str, Any]) -> str | None:
    """The init check's message when it stopped this run, else None."""
    for error in summary.get("harness_errors") or []:
        if error.get("kind") == INIT_CHECK:
            return str(error.get("message"))
    return None


def b1_median(runs_dir: Path, split: str, household_id: str) -> float | None:
    counts = [
        len(json.loads(p.read_text()).get("questions") or [])
        for p in sorted(Path(runs_dir).glob(f"{split}-{household_id}-B1-t*.summary.json"))
    ]
    return float(statistics.median(counts)) if counts else None


def needs_key(configs: Sequence[str]) -> list[str]:
    return [c for c in configs if CONFIGS[c].kind != NO_MODEL]


class Sweep:
    """One invocation: the jobs, the shared ledger and client, and what each phase produced."""

    def __init__(
        self,
        *,
        split: str,
        configs: Sequence[str],
        households: Sequence[str],
        trials: int,
        ceiling: int,
        settings: HarnessSettings,
        ledger: Ledger,
        deps: Deps,
        runs_dir: Path = RUNS_DIR,
        data_dir: Path = DATA_DIR,
        prereg_commit: str | None = None,
        repo: Path | None = None,
        say: Callable[[str], None] = print,
    ) -> None:
        self.split = split
        self.configs = list(configs)
        self.repo = repo
        if split == TEST_SPLIT:
            prereg_commit = harness.locked_commit(self.configs, prereg_commit, repo=repo)
        self.prereg_commit = prereg_commit
        self.households = list(households)
        self.trials = trials
        self.ceiling = max(1, ceiling)
        self.settings = settings
        self.ledger = ledger
        self.deps = deps
        self.runs_dir = Path(runs_dir)
        self.data_dir = Path(data_dir)
        self.say = say
        self.statuses = engine.STATUSES_FOR_SPLIT[split]
        self.prices = load_prices()
        self.client = deps.client_factory(settings.api_key) if needs_key(self.configs) else None
        self.limits: list[int] = []
        self.summaries: dict[str, dict[str, Any]] = {}
        self.failures: list[str] = []
        self.halted: str | None = None
        self.broken: str | None = None

    # Phase 2: runs.

    def household(self, household_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        return harness.load_household(self.data_dir, household_id)

    def finish(self, summary: dict[str, Any], job: Job) -> dict[str, Any]:
        """Stamps the prereg commit on a baseline summary and writes it."""
        if self.prereg_commit is not None:
            summary["prereg_commit"] = self.prereg_commit
        write_json(summary_path(self.runs_dir, job.run_id), summary)
        return summary

    async def with_ledger(self, base_id: str, model: str, reserve: float, call: Callable[[BudgetedClient], Any]) -> Any:
        """Reserves, runs call with a guard held to the reservation (topped up while the month cap allows), and settles."""
        reservation = Reservation(self.ledger, base_id, model, reserve, run_cap_usd=self.settings.run_cap_usd)
        budget = Budget(reservation, None, self.prices)
        guard = budget.client(GUARD, self.client, model)
        try:
            return await call(guard)
        finally:
            reservation.settle(budget.total())

    def stored(self, job: Job) -> dict[str, Any] | None:
        """The finished run's summary, or None when the job has to run.

        A run the init check stopped is never resumed: it measured no agent
        behavior, so its summary moves aside and the job runs again.
        """
        path = summary_path(self.runs_dir, job.run_id)
        if not path.exists():
            return None
        summary = json.loads(path.read_text())
        if init_failed(summary):
            aside = self.set_aside(job.run_id)
            self.say(f"set aside: {job.run_id} stopped at the init check, so it runs again; its summary is {aside.name}")
            return None
        return summary

    def set_aside(self, run_id: str) -> Path:
        """Moves the summary to {run_id}.init_failed.json (then .init_failed.2.json, and so on), its trace beside it."""
        stem, n = f"{run_id}.init_failed", 1
        while (self.runs_dir / f"{stem}.json").exists():
            n += 1
            stem = f"{run_id}.init_failed.{n}"
        aside = self.runs_dir / f"{stem}.json"
        os.replace(summary_path(self.runs_dir, run_id), aside)
        trace = trace_path(self.runs_dir, run_id)
        if trace.exists():
            os.replace(trace, self.runs_dir / f"{stem}.trace.json")
        return aside

    async def run_job(self, job: Job) -> dict[str, Any]:
        """Runs a job that has no stored summary (Sweep.stored), and returns the summary it wrote."""
        cfg = job.cfg
        if cfg.kind == "agent":
            return self.checked(await self.deps.run_agent(
                job.household_id, job.config, job.trial, job.split,
                settings=self.settings, ledger=self.ledger, sim_client=self.client,
                runs_dir=self.runs_dir, data_dir=self.data_dir, prereg_commit=self.prereg_commit,
                prereg_repo=self.repo,
            ), job)
        profile, truth = self.household(job.household_id)
        ids = {"run_id": job.run_id, "split": job.split, "trial": job.trial}
        if cfg.kind == NO_MODEL:
            fn = self.deps.get("run_prior_year", "eval.baselines")
            return self.finish(fn(truth, profile, **ids), job)
        if cfg.kind == "full_organizer":
            fn = self.deps.get("run_full_organizer", "eval.baselines")

            async def organizer(guard: BudgetedClient) -> Any:
                simulator = ClientSimulator(truth, guard, model=SIMULATOR_MODEL)
                return await fn(truth, profile, simulator, statuses=self.statuses, **ids)

            return self.finish(await self.with_ledger(job.run_id, SIMULATOR_MODEL, cfg.reserve_usd, organizer), job)
        fn = self.deps.get("run_model_list", "eval.baselines")

        async def model_list(guard: BudgetedClient) -> Any:
            return await fn(truth, model=cfg.model, client=guard, statuses=self.statuses, **ids)

        return self.finish(await self.with_ledger(job.run_id, cfg.model, cfg.reserve_usd, model_list), job)

    def checked(self, summary: dict[str, Any], job: Job) -> dict[str, Any]:
        """A summary made with a custom system prompt is a judge-validation transcript, not this config's run.

        A test-split summary must carry the prereg commit this sweep is locked to.
        """
        if summary.get("system_prompt"):
            raise ValueError(f"{job.run_id}.summary.json was made with a custom system prompt; "
                             "move it aside so the real run can take its place")
        if self.prereg_commit is not None and summary.get("prereg_commit") != self.prereg_commit:
            raise ValueError(f"{job.run_id}.summary.json was made under prereg commit "
                             f"{summary.get('prereg_commit')}, not {self.prereg_commit}; it can't count in this sweep")
        return summary

    async def guarded(self, job: Job, gate: asyncio.Semaphore) -> tuple[Job, dict[str, Any] | None, bool]:
        """(job, summary or None, whether it ran rather than resumed)."""
        async with gate:
            if self.halted or self.broken:
                return job, None, False
            try:
                stored = self.stored(job)
                resumed = stored is not None
                summary = self.checked(stored, job) if resumed else await self.run_job(job)
            except SpendCapExceeded as e:
                self.halted = str(e)
                self.say(f"stopped: {e}")
                return job, None, True
            except Exception as e:
                self.failures.append(f"{job.run_id}: {type(e).__name__}: {e}")
                self.say(f"failed: {job.run_id}: {type(e).__name__}: {e}")
                return job, None, True
            self.say(f"{'resumed' if resumed else 'ran'}: {job.run_id}")
            broken = init_failed(summary)
            if broken and not self.broken:
                self.broken = f"{job.run_id}: {broken}"
                self.failures.append(f"{job.run_id}: the init check failed: {broken}")
                self.say(f"stopped: the init check failed in {job.run_id}, so no new run starts: {broken}")
            return job, summary, not resumed

    def jobs(self, trial: int) -> list[Job]:
        return [Job(self.split, h, c, trial) for h in self.households for c in self.configs]

    async def run_all(self) -> None:
        limit = min(START_CONCURRENCY, self.ceiling)
        for trial in range(1, self.trials + 1):
            if self.halted or self.broken:
                break
            self.limits.append(limit)
            gate = asyncio.Semaphore(limit)
            results = await asyncio.gather(*(self.guarded(job, gate) for job in self.jobs(trial)))
            clean = True
            for job, summary, ran in results:
                if summary is None:
                    clean = clean and not ran
                    continue
                clean = clean and (not ran or is_clean(summary))
                if init_failed(summary):
                    continue  # not a trial: neither judged nor scored, and the next sweep runs it again
                self.summaries[job.run_id] = summary
            limit = next_limit(limit, self.ceiling, clean and not self.halted)

    # Phase 3: judging.

    def cached_verdict(self, run_id: str) -> dict[str, Any] | None:
        """The cached verdict, or None when there is none or it came from another judge model or JUDGE_PROMPT."""
        from eval.judge import PROMPT_VERSION, verdict_problem

        path = self.runs_dir / f"{run_id}.judge.json"
        if not path.exists():
            return None
        cached = json.loads(path.read_text())
        return None if verdict_problem(cached, JUDGE_MODEL, PROMPT_VERSION) else cached

    async def judge_one(self, run_id: str, summary: Mapping[str, Any], gate: asyncio.Semaphore) -> dict[str, Any] | None:
        """The cached verdict, or a new one unless the spend cap has stopped the sweep."""
        cached = self.cached_verdict(run_id)
        if cached is not None or not summary.get("transcript") or self.halted:
            return cached
        fn = self.deps.get("judge_summary", "eval.judge")
        async with gate:
            if self.halted:
                return None
            try:
                verdict = await self.with_ledger(
                    f"{run_id}.judge", JUDGE_MODEL, JUDGE_RESERVE_USD,
                    lambda guard: fn(summary, client=guard, model=JUDGE_MODEL))
            except SpendCapExceeded as e:
                self.halted = str(e)
                self.say(f"stopped: {e}")
                return None
            except Exception as e:
                self.failures.append(f"{run_id} judge: {type(e).__name__}: {e}")
                self.say(f"judge failed: {run_id}: {type(e).__name__}: {e}")
                return None
        write_json(self.runs_dir / f"{run_id}.judge.json", verdict)
        return verdict

    async def judge_all(self) -> dict[str, dict[str, Any] | None]:
        gate = asyncio.Semaphore(self.limits[-1] if self.limits else START_CONCURRENCY)
        agent_runs = {rid: s for rid, s in self.summaries.items() if CONFIGS[s["config"]].kind == "agent"}
        verdicts = await asyncio.gather(*(self.judge_one(rid, s, gate) for rid, s in agent_runs.items()))
        return dict(zip(agent_runs, verdicts))

    # Phase 4: scoring.

    def baseline_scorer(self) -> Callable[..., Any] | None:
        try:
            return self.deps.get("score_baseline", "eval.baselines")
        except (ImportError, AttributeError):
            return None

    def score(self, summary: Mapping[str, Any], judge: Mapping[str, Any] | None) -> dict[str, Any]:
        """score_run for agent runs; eval.baselines.score_baseline for baselines, else score_run or score_list."""
        _, truth = self.household(summary["household_id"])
        kw = {
            "rules": engine.load_rules(), "specs": engine.load_facts(),
            "ignore": engine.load_ignore_aliases(), "statuses": self.statuses,
        }
        kind = CONFIGS[summary["config"]].kind
        b1 = b1_median(self.runs_dir, self.split, summary["household_id"])
        baseline = self.baseline_scorer() if kind != "agent" else None
        if baseline is not None:
            score = baseline(summary, truth, b1_median_questions=b1, **kw)
        elif kind == "model_list":
            score = score_list(summary.get("model_list_items") or [], truth, config=summary["config"], **kw)
            score = {"run_id": summary["run_id"], **score}
            score.update({name: summary.get(name) for name in ("trial", "model", "cost_usd", "latency_s")})
        else:
            verdict = {k: v for k, v in judge.items() if k in JUDGE_FIELDS} if judge else None
            score = score_run(summary, truth, b1_median_questions=b1, judge=verdict, **kw)
        if summary.get("prereg_commit") is not None:
            score["prereg_commit"] = summary["prereg_commit"]
        return score

    def score_all(self, verdicts: Mapping[str, Mapping[str, Any] | None]) -> dict[str, dict[str, Any]]:
        """Scores every run, with its verdict from this sweep or the judge cache, so a cached verdict is never dropped."""
        scores = {}
        for run_id, summary in self.summaries.items():
            scores[run_id] = self.score(summary, verdicts.get(run_id) or self.cached_verdict(run_id))
            write_json(self.runs_dir / f"{run_id}.score.json", scores[run_id])
        return scores


def pick_households(data_dir: Path, split: str, households: str | None, limit: int | None) -> list[str]:
    in_split = list(json.loads((Path(data_dir) / "splits.json").read_text())[split])
    if households:
        chosen = [h.strip() for h in households.split(",") if h.strip()]
        outside = [h for h in chosen if h not in in_split]
        if outside:
            raise Refused(f"not in the {split} split: {', '.join(outside)}")
        return chosen
    if limit is None:
        return in_split
    if limit < 1:
        raise Refused(f"--limit must be at least 1, not {limit}")
    return in_split[:limit]


def drop_unverified(data_dir: Path, households: Sequence[str]) -> tuple[list[str], list[str]]:
    """(kept, dropped): test households whose list depends on an unverified rule leave the sweep."""
    rules, specs = engine.load_rules(), engine.load_facts()
    kept, out = [], []
    for h in households:
        _, truth = harness.load_household(data_dir, h)
        (out if dropped(truth, rules, specs) else kept).append(h)
    return kept, out


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", required=True, choices=SPLITS)
    parser.add_argument("--configs", help="comma-separated, such as A-haiku,A-sonnet,B1,B2,B3-haiku; "
                        "on the test split, the prereg's configs by default")
    parser.add_argument("--trials", type=int, help="default 1; on the test split, the prereg's trials")
    pick = parser.add_mutually_exclusive_group()
    pick.add_argument("--households", help="comma-separated household IDs from the split")
    pick.add_argument("--limit", type=int, help="the first N households of the split")
    parser.add_argument("--concurrency", type=int, default=START_CONCURRENCY, help="the most runs at once (default 2)")
    parser.add_argument("--prereg", type=Path, default=PREREG_PATH,
                        help="for the test split, this must be the repo's own eval/prereg.yaml")
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    return parser.parse_args(argv)


async def main_async(
    argv: Sequence[str] | None = None,
    *,
    deps: Deps | None = None,
    data_dir: Path = DATA_DIR,
    ledger_path: Path = DEFAULT_LEDGER,
    say: Callable[[str], None] = print,
    repo: Path | None = None,
) -> int:
    """repo is the repository whose eval/prereg.yaml unlocks the test split: this one, except in tests."""
    deps = deps or Deps()
    args = parse_args(argv)
    try:
        configs, trials, commit = sweep_plan(args, repo)
        if not configs or trials < 1:
            raise Refused("give at least one config and at least one trial")
        if args.concurrency < 1:
            raise Refused(f"--concurrency must be at least 1, not {args.concurrency}")
        households = pick_households(data_dir, args.split, args.households, args.limit)
        settings = deps.settings_loader()
        live = needs_key(configs)
        if live and not settings.api_key:
            raise Refused(
                f"TAX_AGENT_API_KEY is empty in .env, so {', '.join(live)} can't run; nothing was reserved. "
                "Paste the workspace key into .env, or run only B2, which needs no key."
            )
        if args.split == TEST_SPLIT:
            households, out = drop_unverified(data_dir, households)
            say(f"dropped {len(out)} test households whose list depends on an unverified rule")
            write_json(args.runs_dir / "test-dropped.json", {"dropped": out, "prereg_commit": commit})
        ledger = Ledger(ledger_path, cap_usd=settings.spend_cap_usd, run_cap_usd=settings.run_cap_usd)
        sweep = Sweep(
            split=args.split, configs=configs, households=households, trials=trials, ceiling=args.concurrency,
            settings=settings, ledger=ledger, deps=deps, runs_dir=args.runs_dir, data_dir=data_dir,
            prereg_commit=commit, repo=repo, say=say,
        )
    except (Refused, KeyError) as e:
        say(f"refused: {e}")
        return EXIT_REFUSED

    await sweep.run_all()
    sweep.score_all(await sweep.judge_all())
    try:
        report = deps.get("write_report", "eval.report")
    except (ImportError, AttributeError) as e:
        say(f"no report: eval.report is not available ({e})")
    else:
        path = report(args.runs_dir, args.report, prereg_path=args.prereg, ledger_path=ledger.path)
        say(f"report: {path}")
    say(f"{len(sweep.summaries)} runs, {len(sweep.failures)} failures, month to date ${ledger.month_to_date():.4f}")
    if sweep.halted:
        return EXIT_CAP
    return EXIT_FAILED if sweep.failures else EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    sys.exit(main())
