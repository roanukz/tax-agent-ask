"""Offline replay of recorded runs: the Block 5 done-check that fixture replay reproduces scores exactly.

    uv run python -m agent.replay check [fixtures/*.json]
        Replays each fixture and reports any difference from its expected summary and score.
    uv run python -m agent.replay from-run dev-H009-A-sonnet-t1 --name live-dev-H009-sonnet
        Turns a finished run in eval/runs (trace, summary, score) into fixtures/<name>.json.
    uv run python -m agent.replay rebuild fixtures/scripted-*.json
        Rewrites the expected summary and score of scripted fixtures after an intended change.

A fixture (fixtures/*.json) is one run's event log (agent/summary.py lists
the events) with what it produced:

    {"name", "description", "scripted", "meta", "events", "judge",
     "b1_median_questions", "expected_summary", "expected_score"}

Replay drops the recorded tool results, hook denials, and client answers
and walks the log in order. Each tax tool call runs again against a fresh
in-process server (server.tax_server.build_server) through an MCP client,
one server per session attempt, minting the intake IDs the log recorded.
record_fact first goes through the real record_fact_hook, with an
EvidenceBook fed the recorded answers in order. An ask_client call gets its
recorded answer back, since the simulator is not called. A call the
harness's gate denied keeps its recorded result; a gated record_fact still
goes through record_fact_hook first, since the CLI runs both PreToolUse
hooks on it live. In a recorded log (the fixture's scripted flag is false
or absent), a tax call with no recorded result is one the CLI never ran,
as when it stopped on its budget with tool calls in its last message, so
it is passed through without a result rather than run. A scripted log's
results, if it has any, were made by replay rather than by a live CLI
(Script records none, and build_fixture stores the replayed ones), so they
can't tell which calls ran, and every tax call in it runs. Every other
event, such as the trace-only post_tool_use and stream_usage events or any
type replay does not know, is copied through unchanged and never acted on.
agent.summary then rebuilds the summary from the replayed log, and
eval.score.score_run scores it with the recorded judge verdict and B1
median; a test-split run's score gets its prereg commit, as eval.run
stamps it. Nothing here spends anything or opens a network socket.

Script builds the scripted-* fixtures (fixtures/scripted.py): no live run
existed when they were written, so their answers, costs, and token counts
are made up, while their households and truth files are real.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import anyio
from mcp import Client

from agent.ask_client import ASK_CLIENT_TOOL, ProbeSchedule, deliver
from agent.configs import get_config
from agent.harness import ALLOWED_TOOLS, DATA_DIR, REPO, RUNS_DIR, load_household, run_id_for, summary_path, trace_path
from agent.hooks import RECORD_FACT_TOOL, EvidenceBook, record_fact_hook
from agent.summary import TAX_PREFIX, build_summary, parsed
from eval.score import score_run
from rules import engine
from server.tax_server import build_server

FIXTURES_DIR = REPO / "fixtures"
REBUILT = ("tool_result", "hook_denial", "client_answer")
# Trace-only events replay copies through without acting on them, as it does any type it does not know.
TRACE_ONLY = ("post_tool_use", "stream_usage")
FIXTURE_FIELDS = ("name", "description", "scripted", "meta", "judge", "b1_median_questions")


@dataclass
class Replayed:
    events: list[dict[str, Any]]
    summary: dict[str, Any]
    score: dict[str, Any]
    mismatches: list[dict[str, Any]] = field(default_factory=list)


def load_fixture(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def write_fixture(path: Path, fixture: Mapping[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixture, indent=2) + "\n")
    return path


def mcp_text(result: Any) -> str:
    return "".join(getattr(block, "text", "") for block in result.content if getattr(block, "type", "") == "text")


def recorded_intake_ids(events: Sequence[Mapping[str, Any]], *, scripted: bool = False) -> list[str]:
    """The intake ID each start_intake call returned, in order.

    When a scripted log has no result for a start_intake call, the ID is the
    one the next call after it passed. In a recorded log such a call never
    ran, so it minted no ID.
    """
    results = {e["tool_use_id"]: e for e in events if e.get("type") == "tool_result"}
    calls = [e for e in events if e.get("type") == "tool_call"]
    ids = []
    for i, call in enumerate(calls):
        if call.get("name") != TAX_PREFIX + "start_intake":
            continue
        result = results.get(call["id"])
        if result is not None:
            value = parsed(result.get("text"))
            if not result.get("is_error") and isinstance(value, Mapping) and value.get("intake_id"):
                ids.append(value["intake_id"])
            continue
        if not scripted:
            continue
        later = next((c["input"]["intake_id"] for c in calls[i + 1:] if (c.get("input") or {}).get("intake_id")), None)
        if later:
            ids.append(later)
    return ids


def id_factory(ids: list[str]) -> Any:
    queue: Iterator[str] = iter(ids)
    spare = iter(range(1, 1 << 24))

    def mint() -> str:
        return next(queue, None) or f"in_{next(spare):06x}"

    return mint


def _documents(value: Mapping[str, Any]) -> list[tuple[Any, Any]]:
    return [(d.get("rule_id"), d.get("count")) for d in value.get("documents") or []]


# The parts of each tool's result that the summary reads. Rule wording,
# sources, and hints can change without changing a replayed summary.
PROJECTIONS = {
    TAX_PREFIX + "start_intake": lambda v: v.get("intake_id"),
    TAX_PREFIX + "record_fact": lambda v: (v.get("key"), v.get("value"), v.get("evidence")),
    TAX_PREFIX + "build_request_list": lambda v: (_documents(v), v.get("not_asked"), v.get("custom_requests")),
    TAX_PREFIX + "add_custom_request": lambda v: v,
    TAX_PREFIX + "note_for_preparer": lambda v: v,
    TAX_PREFIX + "submit_for_preparer_review": lambda v: (
        v.get("intake_id"), v.get("status"), _documents(v), v.get("not_asked"), v.get("custom_requests"),
        v.get("notes_for_preparer"), v.get("facts_recorded")),
}


def same_result(name: str, a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Same outcome: both failed, or both succeeded with the same parts the summary reads."""
    if bool(a.get("is_error")) != bool(b.get("is_error")):
        return False
    if a.get("is_error") or name not in PROJECTIONS and name.startswith(TAX_PREFIX):
        return True
    pa, pb = parsed(a.get("text")), parsed(b.get("text"))
    if isinstance(pa, Mapping) and isinstance(pb, Mapping) and name in PROJECTIONS:
        return PROJECTIONS[name](pa) == PROJECTIONS[name](pb)
    return a.get("text") == b.get("text")


class Replayer:
    """One pass over a recorded log, rebuilding tool results with a fresh server per attempt."""

    def __init__(self, meta: Mapping[str, Any], events: Sequence[Mapping[str, Any]], *, data_dir: Path, state_root: Path,
                 scripted: bool = False) -> None:
        self.meta = meta
        self.events = [dict(e) for e in events]
        self.scripted = scripted
        self.split = meta["split"]
        self.data_dir = Path(data_dir)
        self.state_root = Path(state_root)
        self.profile, _ = load_household(self.data_dir, meta["household_id"])
        self.recorded = {e["tool_use_id"]: e for e in self.events if e.get("type") == "tool_result"}
        self.answers = self._answers()
        self.gated_ids = {e["tool_use_id"] for e in self.events if e.get("type") == "gate_denial"}
        self.mint = id_factory(recorded_intake_ids(self.events, scripted=scripted))
        self.out: list[dict[str, Any]] = []
        self.mismatches: list[dict[str, Any]] = []
        self.client: Client | None = None
        self.book: EvidenceBook | None = None
        self.sessions = 0
        self._stack: AsyncExitStack | None = None

    def _answers(self) -> dict[str, dict[str, Any]]:
        """Recorded answers by call ID; an answer without one goes to the next ask_client call that succeeded."""
        by_id = {e["tool_use_id"]: e for e in self.events if e.get("type") == "client_answer" and e.get("tool_use_id")}
        loose = [e for e in self.events if e.get("type") == "client_answer" and not e.get("tool_use_id")]
        for e in self.events:
            if (e.get("type") == "tool_call" and e.get("name") == ASK_CLIENT_TOOL and e["id"] not in by_id
                    and loose and not (self.recorded.get(e["id"]) or {}).get("is_error", True)):
                by_id[e["id"]] = loose.pop(0)
        return by_id

    async def close_session(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self.client = None

    async def new_session(self) -> None:
        """A fresh server, MCP client, and evidence book, as each harness attempt gets."""
        await self.close_session()
        self.sessions += 1
        server = build_server(
            self.data_dir / "profiles",
            statuses=engine.STATUSES_FOR_SPLIT[self.split],
            state_dir=self.state_root / f"attempt-{self.sessions}",
            id_factory=self.mint,
        )
        self._stack = AsyncExitStack()
        self.client = await self._stack.enter_async_context(Client(server))
        self.book = EvidenceBook(prior_year_summary=self.profile["prior_year_summary"])

    def result(self, call: Mapping[str, Any], *, is_error: bool, text: str) -> None:
        replayed = {"type": "tool_result", "tool_use_id": call["id"], "is_error": is_error, "text": text}
        recorded = self.recorded.get(call["id"])
        if recorded is not None and not same_result(call["name"], recorded, replayed):
            self.mismatches.append({"tool_use_id": call["id"], "name": call["name"], "recorded": recorded, "replayed": replayed})
        self.out.append(replayed)

    def passthrough(self, call: Mapping[str, Any]) -> None:
        if call["id"] in self.recorded:
            self.out.append(dict(self.recorded[call["id"]]))

    async def ask_client(self, call: Mapping[str, Any]) -> None:
        answer = self.answers.get(call["id"])
        if answer is None:
            self.passthrough(call)
            return
        self.book.add_answer(answer["delivered"])
        self.out.append(dict(answer))
        self.result(call, is_error=False, text=answer["delivered"])

    async def evidence(self, call: Mapping[str, Any]) -> dict[str, Any]:
        """record_fact_hook on this call, logging each denial as the harness does; the hook's decision."""
        before = len(self.book.denials)
        hook = record_fact_hook(self.book)
        decision = await hook({"tool_name": call["name"], "tool_input": dict(call.get("input") or {})}, call["id"], None)
        for denial in self.book.denials[before:]:
            self.out.append({"type": "hook_denial", **denial})
        return decision

    async def gated(self, call: Mapping[str, Any]) -> None:
        """A call the gate denied keeps its recorded result; record_fact still meets the evidence hook, as live."""
        if call["name"] == RECORD_FACT_TOOL:
            await self.evidence(call)
        self.passthrough(call)

    def ran(self, call: Mapping[str, Any]) -> bool:
        """Whether the live CLI ran this call: always in a scripted log, else when the log has its result."""
        return self.scripted or call["id"] in self.recorded

    async def tax_tool(self, call: Mapping[str, Any]) -> None:
        tool_input = dict(call.get("input") or {})
        if call["name"] == RECORD_FACT_TOOL:
            decision = await self.evidence(call)
            if decision:
                reason = decision["hookSpecificOutput"]["permissionDecisionReason"]
                self.result(call, is_error=True, text=reason)
                return
        try:
            response = await self.client.call_tool(call["name"][len(TAX_PREFIX):], tool_input)
        except Exception as e:
            self.result(call, is_error=True, text=f"{type(e).__name__}: {e}")
            return
        self.result(call, is_error=bool(response.is_error), text=mcp_text(response))

    async def step(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind in REBUILT:
            return
        if kind in TRACE_ONLY:
            self.out.append(event)
            return
        if kind == "attempt" or (kind == "tool_call" and self.client is None):
            await self.new_session()
        self.out.append(event)
        if kind != "tool_call":
            return  # attempt, init, assistant_text, gate_denial, result, and any type replay does not know
        if event["id"] in self.gated_ids:
            await self.gated(event)
        elif event["name"] == ASK_CLIENT_TOOL:
            await self.ask_client(event)
        elif event["name"].startswith(TAX_PREFIX) and self.ran(event):
            await self.tax_tool(event)
        else:
            self.passthrough(event)

    async def run(self) -> list[dict[str, Any]]:
        try:
            for event in self.events:
                await self.step(event)
        finally:
            await self.close_session()
        return self.out


async def replay_events(
    meta: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    scripted: bool = False,
    data_dir: Path = DATA_DIR,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(replayed events, tool results that differ from the recorded ones). scripted is the fixture's flag."""
    with tempfile.TemporaryDirectory(prefix="tax-agent-ask-replay-") as tmp:
        replayer = Replayer(meta, events, data_dir=data_dir, state_root=Path(tmp), scripted=scripted)
        out = await replayer.run()
        return out, replayer.mismatches


def score_summary(
    summary: Mapping[str, Any],
    truth: Mapping[str, Any],
    split: str,
    *,
    b1_median_questions: float | None = None,
    judge: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return score_run(
        summary,
        truth,
        rules=engine.load_rules(),
        specs=engine.load_facts(),
        ignore=engine.load_ignore_aliases(),
        statuses=engine.STATUSES_FOR_SPLIT[split],
        b1_median_questions=b1_median_questions,
        judge=judge,
    )


async def replay(fixture: Mapping[str, Any], *, data_dir: Path = DATA_DIR) -> Replayed:
    """A fixture without a scripted flag is replayed as a recorded log."""
    meta = fixture["meta"]
    events, mismatches = await replay_events(
        meta, fixture["events"], scripted=bool(fixture.get("scripted")), data_dir=data_dir)
    summary = build_summary(meta, events)
    _, truth = load_household(data_dir, meta["household_id"])
    score = score_summary(
        summary, truth, meta["split"],
        b1_median_questions=fixture.get("b1_median_questions"), judge=fixture.get("judge"),
    )
    if meta.get("prereg_commit") is not None:
        score["prereg_commit"] = meta["prereg_commit"]  # as eval.run stamps every test score
    return Replayed(events=events, summary=summary, score=score, mismatches=mismatches)


def replay_sync(fixture: Mapping[str, Any], *, data_dir: Path = DATA_DIR) -> Replayed:
    async def go() -> Replayed:
        return await replay(fixture, data_dir=data_dir)

    return anyio.run(go)


def differences(expected: Any, actual: Any, path: str = "") -> list[str]:
    """Paths where two JSON values differ, for readable check output."""
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        out = []
        for key in sorted(set(expected) | set(actual), key=str):
            if key not in actual:
                out.append(f"{path}.{key}: missing from replay")
            elif key not in expected:
                out.append(f"{path}.{key}: not in the fixture")
            else:
                out.extend(differences(expected[key], actual[key], f"{path}.{key}"))
        return out
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [d for i, (a, b) in enumerate(zip(expected, actual)) for d in differences(a, b, f"{path}[{i}]")]
    return [] if expected == actual else [f"{path or '.'}: {json.dumps(expected)[:120]} != {json.dumps(actual)[:120]}"]


def problems(fixture: Mapping[str, Any], got: Replayed) -> list[str]:
    found = [f"tool result {m['tool_use_id']} ({m['name']}) differs from the recording" for m in got.mismatches]
    found += [f"summary{d}" for d in differences(fixture["expected_summary"], got.summary)]
    found += [f"score{d}" for d in differences(fixture["expected_score"], got.score)]
    return found


async def check_async(fixture: Mapping[str, Any], *, data_dir: Path = DATA_DIR) -> list[str]:
    """Every way the replay differs from the fixture's expected summary and score; empty when it reproduces them."""
    return problems(fixture, await replay(fixture, data_dir=data_dir))


def check(fixture: Mapping[str, Any], *, data_dir: Path = DATA_DIR) -> list[str]:
    return problems(fixture, replay_sync(fixture, data_dir=data_dir))


def build_fixture(
    name: str,
    description: str,
    meta: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    scripted: bool,
    judge: Mapping[str, Any] | None = None,
    b1_median_questions: float | None = None,
    data_dir: Path = DATA_DIR,
) -> dict[str, Any]:
    """A fixture whose tool results, summary, and score come from replaying the log."""
    fixture: dict[str, Any] = {
        "name": name, "description": description, "scripted": scripted, "meta": dict(meta),
        "judge": judge, "b1_median_questions": b1_median_questions,
    }
    got = replay_sync({**fixture, "events": list(events)}, data_dir=data_dir)
    if got.mismatches:
        raise ValueError(f"{name}: replayed tool results differ from the recording: {got.mismatches[0]}")
    fixture.update(events=got.events, expected_summary=got.summary, expected_score=got.score)
    return fixture


def fixture_from_run(
    run_id: str,
    *,
    name: str,
    description: str = "",
    runs_dir: Path = RUNS_DIR,
) -> dict[str, Any]:
    """A fixture from a finished live run: its trace, and the summary and score it wrote, as expected."""
    trace = json.loads(trace_path(runs_dir, run_id).read_text())
    summary = json.loads(summary_path(runs_dir, run_id).read_text())
    score = json.loads((Path(runs_dir) / f"{run_id}.score.json").read_text())
    return {
        "name": name,
        "description": description or f"Recorded from live run {run_id}.",
        "scripted": False,
        "meta": trace["meta"],
        "judge": score.get("judge"),
        "b1_median_questions": score.get("full_organizer_questions"),
        "events": trace["events"],
        "expected_summary": summary,
        "expected_score": score,
    }


def rebuild(fixture: Mapping[str, Any], *, data_dir: Path = DATA_DIR) -> dict[str, Any]:
    """The same fixture with its expected summary and score replaced by a fresh replay."""
    return build_fixture(
        fixture["name"], fixture["description"], fixture["meta"], fixture["events"],
        scripted=fixture["scripted"], judge=fixture.get("judge"),
        b1_median_questions=fixture.get("b1_median_questions"), data_dir=data_dir,
    )


class Script:
    """Writes a scripted run's event log the way the harness would record it.

    Tool results are left out; build_fixture fills them by replaying the
    calls against the real server. Answers get the household's probes from
    the real ProbeSchedule, so the delivered text matches what the harness
    would have sent.
    """

    def __init__(
        self,
        name: str,
        description: str,
        *,
        household_id: str,
        split: str,
        config: str,
        intake_id: str,
        trial: int = 1,
        data_dir: Path = DATA_DIR,
    ) -> None:
        cfg = get_config(config)
        self.name = name
        self.description = description
        self.data_dir = data_dir
        self.intake_id = intake_id
        _, truth = load_household(data_dir, household_id)
        self.probes = ProbeSchedule.from_truth(truth)
        self.meta = {
            "run_id": run_id_for(split, household_id, config, trial), "household_id": household_id,
            "config": config, "trial": trial, "model": cfg.model, "split": split,
            "planner_model": cfg.planner_model, "prereg_commit": None,
        }
        self.events: list[dict[str, Any]] = [
            {"type": "attempt", "n": 1},
            {
                "type": "init", "tools": sorted(ALLOWED_TOOLS), "api_key_source": "ANTHROPIC_API_KEY",
                "mcp_servers": [{"name": "tax", "status": "connected"}, {"name": "harness", "status": "connected"}],
                "model": cfg.model, "claude_code_version": "scripted",
            },
        ]
        self._calls = 0
        self._messages = 0
        self._started = False

    def _id(self) -> str:
        self._calls += 1
        return f"toolu_{self.name}_{self._calls:02d}".replace("-", "_")

    def say(self, text: str) -> None:
        self._messages += 1
        self.events.append({"type": "assistant_text", "text": text, "message_id": f"msg_{self._messages:02d}"})

    def call(self, tool_name: str, **tool_input: Any) -> str:
        if tool_name == "start_intake":
            self._started = True
        elif self._started and tool_name not in ("list_fact_keys", "get_document_rules"):
            tool_input = {"intake_id": self.intake_id, **tool_input}
        call_id = self._id()
        self.events.append({"type": "tool_call", "id": call_id, "name": TAX_PREFIX + tool_name, "input": tool_input})
        return call_id

    def record(self, key: str, value: Any, evidence: str) -> str:
        return self.call("record_fact", key=key, value=value, evidence=evidence)

    def ask(self, question: str, fact_keys: list[str], answer: str, revealed: Mapping[str, Any] | None = None) -> str:
        call_id = self._id()
        probes = self.probes.next()
        self.events.append({"type": "tool_call", "id": call_id, "name": ASK_CLIENT_TOOL,
                            "input": {"question": question, "fact_keys": list(fact_keys)}})
        self.events.append({
            "type": "client_answer", "tool_use_id": call_id, "question": question, "fact_keys": list(fact_keys),
            "answer": answer, "delivered": deliver(answer, probes), "probes": [n for n, _ in probes],
            "revealed": dict(revealed or {}), "usage": {"input_tokens": 600 + 40 * self._calls, "output_tokens": 40,
                                                         "cache_read_tokens": 0, "cache_write_tokens": 0},
        })
        return call_id

    def finish(self, *, turns: int, usage: Mapping[str, int], agent_usd: float, simulator_usd: float, latency_s: float) -> None:
        texts = [e["text"] for e in self.events if e.get("type") == "assistant_text"]
        self.events.append({
            "type": "result", "subtype": "success", "is_error": False, "num_turns": turns,
            "duration_ms": int(latency_s * 1000) - 400, "total_cost_usd": agent_usd, "usage": dict(usage),
            "api_error_status": None, "stop_reason": "end_turn", "result": texts[-1] if texts else None,
        })
        self.events.append({
            "type": "costs", "agent_usd": agent_usd, "simulator_usd": simulator_usd, "planner_usd": 0.0,
            "agent_usage": dict(usage), "agent_cost_known": True, "latency_s": latency_s, "attempts": 1,
        })

    def fixture(self) -> dict[str, Any]:
        return build_fixture(self.name, self.description, self.meta, self.events, scripted=True, data_dir=self.data_dir)


def fixture_paths(paths: Sequence[str]) -> list[Path]:
    return [Path(p) for p in paths] if paths else sorted(FIXTURES_DIR.glob("*.json"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    check_cmd = sub.add_parser("check", help="replay fixtures and compare")
    check_cmd.add_argument("paths", nargs="*")
    from_run = sub.add_parser("from-run", help="turn a finished run in eval/runs into a fixture")
    from_run.add_argument("run_id")
    from_run.add_argument("--name", required=True)
    from_run.add_argument("--description", default="")
    from_run.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    rebuild_cmd = sub.add_parser("rebuild", help="rewrite the expected summary and score of scripted fixtures")
    rebuild_cmd.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)

    if args.command == "from-run":
        fixture = fixture_from_run(args.run_id, name=args.name, description=args.description, runs_dir=args.runs_dir)
        path = write_fixture(FIXTURES_DIR / f"{args.name}.json", fixture)
        problems = check(fixture)
        print(f"wrote {path}; replay {'reproduces it' if not problems else 'differs:'}")
        for p in problems:
            print("  " + p)
        return 1 if problems else 0
    if args.command == "rebuild":
        for path in fixture_paths(args.paths):
            fixture = load_fixture(path)
            if not fixture.get("scripted"):
                print(f"{path}: recorded from a live run, so its expected values stay as recorded")
                continue
            write_fixture(path, rebuild(fixture))
            print(f"rebuilt {path}")
        return 0
    failed = 0
    for path in fixture_paths(args.paths):
        problems = check(load_fixture(path))
        print(f"{path.name}: {'ok' if not problems else f'{len(problems)} differences'}")
        for p in problems:
            print("  " + p)
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
