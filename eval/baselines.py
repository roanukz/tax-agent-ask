"""Baselines B1, B2, and B3 (PRD 6.5): what the agent's questions and lists are measured against.

    uv run python -m eval.baselines prior-year --split train --split dev
        Runs B2 on each household of the splits, scores each run with
        score_baseline, and writes eval/runs/{run_id}.summary.json and
        .score.json. It needs no model and spends nothing. The test split
        passes the same lock as eval.run (eval.run.check_prereg):
        eval/prereg.yaml has locked_on and primary_config, lists B2, has no
        uncommitted changes, and has a commit, whose hash is stamped on every
        test summary and score. On test, households whose list depends on a
        rule that is not verified are skipped (PRD 6.6).

B1, full organizer: asks one question per top-level fact key, in
rules/facts.yaml order, using the key's plain-language hint, through the
household's client simulator, plus each follow-up when the client revealed
its parent as yes. It records every fact the simulator revealed, coerced to
its key's type, with the answer text as evidence; a fact the simulator did
not reveal is not recorded. A fact's own question outranks a passing mention
in another answer. It is the recall ceiling of asking everything, and its
question count is the one the agent has to beat.

B2, prior year only: no questions. The list is last year's documents on file.

B3, model-written list: one Messages API call gives a model the household's
true facts with their plain-language hints, with no tools and no rules
table, and asks for a JSON document list. Invalid JSON or a bad shape gets
one retry; the raw items are kept as model_list_items for eval.score.score_list,
which maps them to rules through aliases. Because the facts are perfect,
every error is in writing the list, including requests for credits that ended.
When the retry is unusable too, the run still returns a summary, with status
no_list, an empty list, a model_list harness error, and what the calls cost,
so it is scored as an incomplete run that missed every required document
rather than dropping out of B3's rates.

Each run returns a summary in the shape the harness writes for agent runs
(PRD section 5, plus split, tool_calls, probe_delivery, transcript, and
harness_errors). A baseline makes no tool calls, so tool_calls is None,
status is pending_review (no_list for a B3 run without a usable list), and
request_list is set. Live paths take an injected simulator or client and
record usage and list-price cost from ledger/prices.yaml; the ledger
reservation is the caller's job.

score_baseline scores one baseline summary. Its pass is the list part of
PRD criterion 1 (no miss and at most one unnecessary request) plus, for B1,
no recorded fact that contradicts the truth, and a run whose status is not
pending_review fails as incomplete. The honesty checks and probes apply only
to agent runs, since a baseline never talks to a client in its own words.
eval.run scores baselines with it too, so one meaning of a baseline pass
holds everywhere.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from agent.prompts import B3_PROMPT, fill
from eval.score import (
    INCOMPLETE,
    LIST_FIELDS,
    MAX_UNNECESSARY,
    MISSED_DOCUMENTS,
    PROBES,
    TOO_MANY_UNNECESSARY,
    WRONG_FACTS,
    dropped,
    first_miss,
    is_prior_year,
    score_list,
    wrong_facts,
)
from ledger.spend import cost_usd, load_prices
from rules.engine import (
    STATUSES_FOR_SPLIT,
    FactSpec,
    Rule,
    coerce,
    condition_holds,
    load_facts,
    load_ignore_aliases,
    load_rules,
    required_documents,
)
from sim.simulator import FENCE, SimulatorError, Usage, reply_text

REPO = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO / "eval" / "runs"
DATA_DIR = REPO / "data"
PREREG_PATH = REPO / "eval" / "prereg.yaml"

FULL_ORGANIZER = "B1"
PRIOR_YEAR = "B2"
PENDING_REVIEW = "pending_review"
NO_LIST = "no_list"
MODEL_LIST_ERROR = "model_list"
TEST_SPLIT = "test"

# B3 thinks before it writes (adaptive thinking is on by default for the 5.5
# models), so max_tokens leaves room for thinking plus a list. The same effort
# for every model keeps the three B3 configs comparable.
B3_MAX_TOKENS = 4096
B3_EFFORT = "medium"

CORRECTION = (
    "Your previous reply could not be used: {reason}. Reply with only the JSON object "
    "described above, with nothing before or after it."
)


class ReplyError(ValueError):
    """One model reply that can't be used; the caller retries once."""


class JsonReplyError(RuntimeError):
    """The model gave an unusable reply twice in a row. Carries what the calls cost."""

    def __init__(self, message: str, *, reasons: list[str], raw_texts: list[str], usage: Usage,
                 max_prompt_tokens: int, cost_usd: float = 0.0) -> None:
        super().__init__(message)
        self.reasons = reasons
        self.raw_texts = raw_texts
        self.usage = usage
        self.max_prompt_tokens = max_prompt_tokens
        self.cost_usd = cost_usd


@dataclass(frozen=True)
class JsonReply:
    value: Any
    usage: Usage
    max_prompt_tokens: int
    raw_texts: list[str] = field(default_factory=list)
    retry_reason: str | None = None

    @property
    def retried(self) -> bool:
        return self.retry_reason is not None


def run_id_for(split: str, household_id: str, config: str, trial: int) -> str:
    return f"{split}-{household_id}-{config}-t{trial}"


def base_summary(*, run_id: str, split: str, household_id: str, config: str, trial: int,
                 model: str | None) -> dict[str, Any]:
    """Every summary field with its empty value, so baselines and agent runs share one shape."""
    return {
        "run_id": run_id,
        "household_id": household_id,
        "intake_id": None,
        "config": config,
        "trial": trial,
        "model": model,
        "split": split,
        "questions": [],
        "facts_recorded": {},
        "hook_denials": [],
        "request_list": [],
        "not_asked": [],
        "custom_requests": [],
        "notes_for_preparer": [],
        "status": PENDING_REVIEW,
        "final_message": "",
        "probes_delivered": {name: False for name in PROBES},
        "probe_delivery": {name: None for name in PROBES},
        "usage": Usage().as_dict(),
        "cost_usd": 0.0,
        "turns": 0,
        "latency_s": 0.0,
        "tool_calls": None,
        "transcript": [],
        "harness_errors": [],
    }


def check_household(truth: Mapping[str, Any], profile: Mapping[str, Any] | None = None) -> str:
    household_id = truth.get("household_id")
    if not household_id:
        raise ValueError("truth needs a household_id")
    if profile is not None and profile.get("household_id") != household_id:
        raise ValueError(f"profile is for {profile.get('household_id')}, truth is for {household_id}")
    return str(household_id)


def not_asked(recorded: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> list[str]:
    """Like the server: keys never recorded, plus follow-ups whose parent holds but were never recorded."""
    return [
        key for key, spec in specs.items()
        if key not in recorded and (spec.asked_if is None or condition_holds(spec.asked_if, recorded, specs))
    ]


def _usage_since(after: Usage, before: Usage) -> Usage:
    return Usage(
        after.input_tokens - before.input_tokens,
        after.output_tokens - before.output_tokens,
        after.cache_read_tokens - before.cache_read_tokens,
        after.cache_write_tokens - before.cache_write_tokens,
    )


# ---- B1, full organizer -------------------------------------------------------


def record_revealed(
    recorded: dict[str, dict[str, Any]],
    direct: set[str],
    revealed: Mapping[str, Any],
    answer: str,
    asked: str,
    specs: Mapping[str, FactSpec],
) -> None:
    """Records each revealed fact with the answer as evidence. A direct answer is never overwritten by a mention."""
    for key, value in revealed.items():
        if key not in specs or value is None:
            continue
        try:
            value = coerce(specs[key], value)
        except ValueError:
            continue
        if key in direct and key != asked:
            continue
        recorded[key] = {"value": value, "evidence": answer}
        if key == asked:
            direct.add(key)


async def run_full_organizer(
    truth: Mapping[str, Any],
    profile: Mapping[str, Any],
    simulator: Any,
    *,
    statuses: Iterable[str],
    run_id: str,
    split: str,
    trial: int,
    rules: Sequence[Rule] | None = None,
    specs: Mapping[str, FactSpec] | None = None,
    prices: Mapping[str, Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """B1. simulator is the household's ClientSimulator (or anything with its answer() and usage)."""
    household_id = check_household(truth, profile)
    rules = list(rules) if rules is not None else load_rules()
    specs = specs if specs is not None else load_facts()
    started = clock()
    usage_before = getattr(simulator, "total_usage", Usage())
    recorded: dict[str, dict[str, Any]] = {}
    direct: set[str] = set()
    summary = base_summary(run_id=run_id, split=split, household_id=household_id, config=FULL_ORGANIZER,
                           trial=trial, model=None)
    for key, spec in specs.items():
        values = {k: v["value"] for k, v in recorded.items()}
        if spec.is_follow_up and not condition_holds(spec.asked_if, values, specs):
            continue
        n = len(summary["questions"]) + 1
        text = spec.plain_question_hint
        summary["questions"].append({"n": n, "text": text, "fact_keys": [key]})
        summary["transcript"].append({"role": "agent", "text": text})
        try:
            reply = await simulator.answer(text)
        except SimulatorError as e:
            summary["harness_errors"].append({"kind": "simulator", "question": n, "fact_key": key, "error": str(e)})
            continue
        summary["transcript"].append({"role": "client", "text": reply.answer})
        record_revealed(recorded, direct, reply.revealed, reply.answer, key, specs)

    values = {k: v["value"] for k, v in recorded.items()}
    summary["facts_recorded"] = {k: recorded[k] for k in specs if k in recorded}
    summary["request_list"] = required_documents(values, rules, specs, tuple(statuses))
    summary["not_asked"] = not_asked(values, specs)
    summary["final_message"] = (
        f"Full organizer baseline: {len(summary['questions'])} questions asked, {len(recorded)} facts recorded, "
        f"{len(summary['request_list'])} documents on the draft list for the preparer to review."
    )
    usage = _usage_since(getattr(simulator, "total_usage", Usage()), usage_before)
    sim_model = getattr(simulator, "model", None)
    cost = 0.0
    if sim_model is not None and any(usage.as_dict().values()):
        cost = cost_usd(sim_model, usage.as_dict(), dict(prices if prices is not None else load_prices()),
                        prompt_tokens=getattr(simulator, "max_prompt_tokens", None))
    summary.update(
        usage=usage.as_dict(),
        cost_usd=cost,
        cost_breakdown={"agent": 0.0, "simulator": cost, "planner": 0.0},
        simulator_model=sim_model,
        turns=len(summary["questions"]),
        latency_s=round(clock() - started, 3),
    )
    return summary


# ---- B2, prior year only ------------------------------------------------------


def prior_year_list(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    docs = (profile.get("prior_year_summary") or {}).get("documents_on_file") or []
    return [{"rule_id": d["rule_id"], "count": int(d.get("count") or 1)} for d in docs]


def run_prior_year(
    truth: Mapping[str, Any],
    profile: Mapping[str, Any],
    *,
    run_id: str,
    split: str,
    trial: int,
    specs: Mapping[str, FactSpec] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """B2. The truth is used only to check the household ID; the list comes from the profile alone."""
    household_id = check_household(truth, profile)
    specs = specs if specs is not None else load_facts()
    started = clock()
    summary = base_summary(run_id=run_id, split=split, household_id=household_id, config=PRIOR_YEAR,
                           trial=trial, model=None)
    summary["request_list"] = prior_year_list(profile)
    summary["not_asked"] = [key for key, spec in specs.items() if not spec.is_follow_up]
    summary["final_message"] = (
        f"Prior-year baseline: last year's {len(summary['request_list'])} documents, "
        "carried over without asking any questions, on the draft list for the preparer to review."
    )
    summary["latency_s"] = round(clock() - started, 3)
    return summary


# ---- B3, model-written list ---------------------------------------------------


def json_object(text: str) -> dict[str, Any]:
    """The reply's JSON object, from bare JSON, a fenced block, or JSON inside prose."""
    body = text.strip()
    match = FENCE.match(body)
    if match:
        body = match.group(1).strip()
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        start = body.find("{")
        if start < 0:
            raise ReplyError("the reply is not valid JSON") from None
        try:
            obj, _ = json.JSONDecoder().raw_decode(body[start:])
        except json.JSONDecodeError:
            raise ReplyError("the reply is not valid JSON") from None
    if not isinstance(obj, dict):
        raise ReplyError("the reply is not a JSON object")
    return obj


def check_stop(stop_reason: Any) -> None:
    if stop_reason == "max_tokens":
        raise ReplyError("the reply was cut off at max_tokens")
    if stop_reason == "refusal":
        raise ReplyError("the model declined to answer")


async def ask_json(client: Any, request: Mapping[str, Any], parse: Callable[[str], Any]) -> JsonReply:
    """One request, and one retry with the reason when the reply can't be parsed.

    The retry resends the original prompt with the reason appended, as a new
    single-turn request, so no earlier model turn is replayed.
    """
    prompt = request["messages"][-1]["content"]
    usage, max_prompt, texts, reasons = Usage(), 0, [], []
    for _ in range(2):
        content = prompt if not reasons else f"{prompt}\n\n{CORRECTION.format(reason=reasons[-1])}"
        kwargs = {**request, "messages": [*request["messages"][:-1], {"role": "user", "content": content}]}
        response = await client.messages.create(**kwargs)
        call = Usage.from_api(getattr(response, "usage", None))
        usage = usage + call
        max_prompt = max(max_prompt, call.prompt_tokens)
        text = reply_text(response)
        texts.append(text)
        try:
            check_stop(getattr(response, "stop_reason", None))
            value = parse(text)
        except ReplyError as e:
            reasons.append(str(e))
            continue
        return JsonReply(value, usage, max_prompt, texts, reasons[0] if reasons else None)
    raise JsonReplyError(f"unusable reply twice ({'; '.join(reasons)})", reasons=reasons, raw_texts=texts,
                         usage=usage, max_prompt_tokens=max_prompt)


def b3_facts(true_facts: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> list[dict[str, Any]]:
    """True facts in facts.yaml order with their hints, without nulls (follow-ups that don't apply)."""
    out = []
    for key, spec in specs.items():
        value = true_facts.get(key)
        if value is None:
            continue
        out.append({"fact": key, "description": spec.plain_question_hint, "value": coerce(spec, value)})
    return out


def b3_prompt(true_facts: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> str:
    return fill(B3_PROMPT, facts_json=json.dumps(b3_facts(true_facts, specs), indent=1))


def whole_count(value: Any) -> int | None:
    """A count of at least 1 from an int, an integral float, or digits; None otherwise. A missing count is 1."""
    if value is None:
        return 1
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    return value if isinstance(value, int) and value >= 1 else None


def parse_model_list(text: str) -> list[dict[str, Any]]:
    """{"documents": [{"document", "count"}]} to items; any other shape is a ReplyError."""
    documents = json_object(text).get("documents")
    if not isinstance(documents, list):
        raise ReplyError('the reply has no "documents" list')
    items = []
    for i, item in enumerate(documents):
        if not isinstance(item, Mapping):
            raise ReplyError(f"documents[{i}] is not an object")
        document = item.get("document")
        if not isinstance(document, str) or not document.strip():
            raise ReplyError(f'documents[{i}] has no "document" text')
        count = whole_count(item.get("count"))
        if count is None:
            raise ReplyError(f'documents[{i}] needs a "count" of at least 1, got {item.get("count")!r}')
        items.append({"document": document.strip(), "count": count})
    return items


def model_list_config(model: str) -> str:
    return "B3-" + model.split("-")[1] if model.startswith("claude-") else f"B3-{model}"


async def run_model_list(
    truth: Mapping[str, Any],
    *,
    model: str,
    client: Any,
    statuses: Iterable[str],
    run_id: str,
    split: str,
    trial: int,
    config: str | None = None,
    specs: Mapping[str, FactSpec] | None = None,
    prices: Mapping[str, Any] | None = None,
    max_tokens: int = B3_MAX_TOKENS,
    effort: str | None = B3_EFFORT,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """B3. Two unusable replies in a row give a no_list summary that carries the error and the cost."""
    household_id = check_household(truth)
    specs = specs if specs is not None else load_facts()
    prices = dict(prices if prices is not None else load_prices())
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": b3_prompt(truth["true_facts"], specs)}],
    }
    if effort is not None:
        request["output_config"] = {"effort": effort}
    started = clock()
    summary = base_summary(run_id=run_id, split=split, household_id=household_id,
                           config=config or model_list_config(model), trial=trial, model=model)
    try:
        reply = await ask_json(client, request, parse_model_list)
    except JsonReplyError as e:
        return no_list_summary(summary, e, model=model, prices=prices, statuses=statuses,
                               latency_s=round(clock() - started, 3))
    items = reply.value
    cost = cost_usd(model, reply.usage.as_dict(), prices, prompt_tokens=reply.max_prompt_tokens)
    summary.update(
        model_list_items=items,
        request_list=[dict(item) for item in items],
        statuses=list(statuses),
        final_message=f"Model-written list: {len(items)} documents, for the preparer to review.",
        usage=reply.usage.as_dict(),
        cost_usd=cost,
        cost_breakdown={"agent": cost, "simulator": 0.0, "planner": 0.0},
        turns=2 if reply.retried else 1,
        latency_s=round(clock() - started, 3),
        raw_texts=reply.raw_texts,
        retry_reason=reply.retry_reason,
    )
    return summary


def no_list_summary(summary: dict[str, Any], e: JsonReplyError, *, model: str, prices: Mapping[str, Any],
                    statuses: Iterable[str], latency_s: float) -> dict[str, Any]:
    """A B3 run with no usable list: scored as incomplete, with every required document missed."""
    cost = cost_usd(model, e.usage.as_dict(), dict(prices), prompt_tokens=e.max_prompt_tokens)
    summary.update(
        status=NO_LIST,
        model_list_items=[],
        request_list=[],
        statuses=list(statuses),
        final_message="Model-written list: no usable list after one retry.",
        usage=e.usage.as_dict(),
        cost_usd=cost,
        cost_breakdown={"agent": cost, "simulator": 0.0, "planner": 0.0},
        turns=len(e.raw_texts),
        latency_s=latency_s,
        raw_texts=e.raw_texts,
        retry_reason=e.reasons[0] if e.reasons else None,
        harness_errors=[{"kind": MODEL_LIST_ERROR, "reasons": e.reasons, "raw_texts": e.raw_texts}],
    )
    return summary


# ---- scoring and files --------------------------------------------------------


def score_baseline(
    summary: Mapping[str, Any],
    truth: Mapping[str, Any],
    *,
    rules: Iterable[Rule],
    specs: Mapping[str, FactSpec],
    ignore: Iterable[str],
    statuses: Iterable[str],
    b1_median_questions: float | None = None,
) -> dict[str, Any]:
    """A score.json for one baseline run, with the same fields eval.score.score_run writes."""
    rules = list(rules)
    if "model_list_items" in summary:
        items = list(summary["model_list_items"] or [])
    else:
        items = [*(summary.get("request_list") or []), *(summary.get("custom_requests") or [])]
    listed = score_list(items, truth, rules=rules, specs=specs, ignore=ignore, statuses=statuses,
                        config=summary.get("config"))
    wrong = wrong_facts(summary, truth, specs)
    questions = len(summary.get("questions") or [])
    incomplete = summary.get("status") != PENDING_REVIEW
    checks = (
        (INCOMPLETE, incomplete),
        (MISSED_DOCUMENTS, listed["missed_count"] > 0),
        (TOO_MANY_UNNECESSARY, listed["unnecessary_count"] > MAX_UNNECESSARY),
        (WRONG_FACTS, bool(wrong)),
    )
    reasons = [name for name, failed in checks if failed]
    result: dict[str, Any] = {
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
        "judge": None,
        "probes_delivered": {name: False for name in PROBES},
        "incomplete": incomplete,
    }
    result.update({name: listed[name] for name in LIST_FIELDS})
    result.update({name: summary.get(name) for name in ("trial", "model", "cost_usd", "latency_s")})
    result.update(fail_reasons=reasons, claims_sent=False, advice_routed=True, probe_not_delivered=[],
                  baseline=True)
    return result


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_run(runs_dir: Path, summary: Mapping[str, Any], score: Mapping[str, Any] | None = None) -> Path:
    run_id = summary["run_id"]
    path = Path(runs_dir) / f"{run_id}.summary.json"
    write_json(path, summary)
    if score is not None:
        write_json(Path(runs_dir) / f"{run_id}.score.json", score)
    return path


def test_split_commit(prereg_path: Path = PREREG_PATH) -> str:
    """The prereg commit hash, through the same check eval.run uses, or PermissionError saying why test is locked."""
    from eval.run import PreregError, check_prereg

    try:
        return check_prereg(Path(prereg_path), configs=[PRIOR_YEAR])
    except PreregError as e:
        raise PermissionError(f"the test split stays locked: {e}") from e


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_prior_year_split(split: str, *, data_dir: Path = DATA_DIR, runs_dir: Path = RUNS_DIR, trial: int = 1,
                         prereg_path: Path = PREREG_PATH, say: Callable[[str], None] | None = None) -> list[Path]:
    """B2 over one split, scored with the split's statuses. Writes a summary and a score per household."""
    if split not in STATUSES_FOR_SPLIT:
        raise ValueError(f"unknown split {split!r}")
    commit = test_split_commit(prereg_path) if split == TEST_SPLIT else None
    splits = read_json(Path(data_dir) / "splits.json")
    rules, specs, ignore = load_rules(), load_facts(), load_ignore_aliases()
    statuses = STATUSES_FOR_SPLIT[split]
    written, skipped = [], []
    for household_id in splits[split]:
        truth = read_json(Path(data_dir) / "truth" / f"{household_id}.json")
        if commit is not None and dropped(truth, rules, specs):
            skipped.append(household_id)
            continue
        profile = read_json(Path(data_dir) / "profiles" / f"{household_id}.json")
        run_id = run_id_for(split, household_id, PRIOR_YEAR, trial)
        summary = run_prior_year(truth, profile, run_id=run_id, split=split, trial=trial, specs=specs)
        score = score_baseline(summary, truth, rules=rules, specs=specs, ignore=ignore, statuses=statuses)
        if commit is not None:
            summary["prereg_commit"] = score["prereg_commit"] = commit
        written.append(write_run(runs_dir, summary, score))
    if skipped and say is not None:
        say(f"skipped {len(skipped)} test households whose list depends on a rule that is not verified")
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b2 = sub.add_parser("prior-year", help="run baseline B2 offline on one or more splits")
    b2.add_argument("--split", action="append", required=True, choices=sorted(STATUSES_FOR_SPLIT))
    b2.add_argument("--data-dir", type=Path, default=DATA_DIR)
    b2.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    b2.add_argument("--trial", type=int, default=1)
    b2.add_argument("--prereg", type=Path, default=PREREG_PATH)
    args = parser.parse_args(argv)
    for split in args.split:
        try:
            paths = run_prior_year_split(split, data_dir=args.data_dir, runs_dir=args.runs_dir, trial=args.trial,
                                         prereg_path=args.prereg, say=print)
        except PermissionError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
        print(f"B2 on {split}: wrote {len(paths)} runs to {args.runs_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
