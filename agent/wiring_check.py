"""Manual wiring check: real CLI sessions through the harness, with both MCP servers, that never leave the machine.

    uv run python -m agent.wiring_check

Not run by pytest, since it starts the bundled Claude Code CLI and the tax
server, and stages 3 and 4 serve a fake API over TCP on 127.0.0.1, which
pytest blocks. Like agent.ping --dry-run, it passes a placeholder key, and it
points ANTHROPIC_BASE_URL at a closed local port (stages 1 and 2) or at
agent.fake_api on 127.0.0.1 (stages 3 and 4), so no request can leave the
machine. It spends nothing: every ledger and run file goes to a temp
folder, and the client simulator is a stand-in (one that fails if anything
calls it, in stages 1 and 2).

Every check runs agent.harness.run_one, so the options, hooks, MCP servers,
and init check are the ones a live run uses. That init check already stops
a run on an extra or missing tool, a server that is not connected, or the
wrong key; this check adds the CLI's get_mcp_status view. A thin client
wrapper reads the init frame, asks the CLI for its MCP server status, and
interrupts before the first model request can be retried (stages 1 and 2).

1. Clean session: the visible tools are exactly the 9 allowed ones, both
   MCP servers report connected, apiKeySource is ANTHROPIC_API_KEY, and the
   run ends with no harness error. The server status shows each server's
   name and version, and its protocol version when the CLI reports one.
2. Planted built-in tool: tools=["Bash"] (with Bash taken off the
   disallowed backstop list) makes Bash visible, and the harness's init
   check must abort the run with a harness error before any tool runs.
3. Spend, against agent.fake_api, which streams the real event format
   (output_tokens 1 in message_start, the true count in message_delta):
   every agent request's max_tokens is the config's output cap; no request
   shows the agent a "USD budget"; the working folder in the environment
   block names no run; every call's prompt fit the bound the harness held
   for it before it was sent; an A-opus run at list price with growing
   calls never settles above its reserved lines or RUN_CAP_USD, or below
   what the fake API would have billed; and two A-haiku runs going at once
   near the month cap never push month to date past it, while the second,
   whose calls grow until the month has no headroom left, stops with
   month_cap, raises SpendCapExceeded, and writes no summary.
4. Agent calls the API answers badly, against agent.fake_api's faults: an
   A-opus run whose first three sessions end on a 429 (the CLI's own retries
   off) reaches its fourth attempt at a known cost, since the CLI's own
   message for a refused request costs nothing; a stream cut off
   mid-response, after which the CLI resumes with a call no hook precedes,
   counts at its bound and the resume is held before it is sent; and a
   request that gets no answer at all, which the CLI sends again, counts at
   its bound and the retry is held too. Each run settles at no less than
   the fake API could have billed and no more than it reserved. Last, the
   same cut on A-opus, where the resume's bound no longer fits under the
   run cap, must stop with run_cap at the cut; whether the CLI's resume
   reached the API before the interrupt landed is printed, since that is
   best effort.

Exit code 0 when all four come out as expected.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, SystemMessage

from agent.ask_client import ASK_CLIENT_TOOL, MONTH_CAP, RUN_CAP
from agent.configs import get_config
from agent.env import HarnessSettings
from agent.fake_api import (
    CUT,
    DROPPED,
    REFUSED,
    TEXT,
    TOOL_USE,
    Block,
    FakeMessagesAPI,
    first_user_text,
    intake_id_in,
)
from agent.harness import ALLOWED_TOOLS, STREAM_USAGE, run_one, summary_path, trace_path
from agent.ping import CLOSED_PORT_URL, DRY_RUN_KEY
from ledger.spend import Ledger, SpendCapExceeded, load_prices
from sim.fakes import FakeUsage, ScriptedClient

HOUSEHOLD = "H002"
SPLIT = "dev"
CONFIG = "A-haiku"
SERVERS = ("tax", "harness")
PLANTED = "Bash"


class NoSimulator:
    """Fails loudly if the check ever reaches the client simulator."""

    class messages:  # noqa: N801 - the Anthropic client's attribute name
        @staticmethod
        async def create(**kwargs: Any) -> Any:
            raise AssertionError("the wiring check must never call the simulator")


class StopAfterInit:
    """ClaudeSDKClient, plus: after the init frame, read the MCP server status and interrupt."""

    def __init__(self, *, options: ClaudeAgentOptions) -> None:
        self.inner = ClaudeSDKClient(options=options)
        self.init: dict[str, Any] | None = None
        self.status: dict[str, Any] | None = None
        self.interrupted = False

    async def __aenter__(self) -> StopAfterInit:
        await self.inner.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return await self.inner.__aexit__(*exc)

    async def query(self, prompt: str) -> None:
        await self.inner.query(prompt)

    async def interrupt(self) -> None:
        if not self.interrupted:
            self.interrupted = True
            await self.inner.interrupt()

    async def receive_response(self):
        async for message in self.inner.receive_response():
            yield message
            if isinstance(message, SystemMessage) and message.subtype == "init" and self.init is None:
                self.init = message.data
                self.status = dict(await self.inner.get_mcp_status())
                await self.interrupt()


def closed_port(options: ClaudeAgentOptions) -> ClaudeAgentOptions:
    return dataclasses.replace(options, env={**options.env, "ANTHROPIC_BASE_URL": CLOSED_PORT_URL})


def plant_tool(options: ClaudeAgentOptions, name: str = PLANTED) -> ClaudeAgentOptions:
    """A built-in tool made visible: in tools, and off the disallowed backstop list."""
    return dataclasses.replace(
        options, tools=[name], disallowed_tools=[t for t in options.disallowed_tools if t != name])


def assess(init: Mapping[str, Any] | None, status: Mapping[str, Any] | None) -> list[str]:
    """What is wrong with a clean session's init frame and server status; empty when the wiring is right."""
    if init is None:
        return ["no init frame arrived"]
    problems = []
    tools = list(init.get("tools") or [])
    if sorted(tools) != sorted(ALLOWED_TOOLS) or len(tools) != len(ALLOWED_TOOLS):
        extra = sorted(set(tools) - set(ALLOWED_TOOLS))
        missing = sorted(set(ALLOWED_TOOLS) - set(tools))
        problems.append(f"visible tools are not exactly the allowed 9: extra {extra}, missing {missing}")
    in_init = {s.get("name"): s.get("status") for s in init.get("mcp_servers") or []}
    in_status = {s.get("name"): s.get("status") for s in (status or {}).get("mcpServers") or []}
    for name in SERVERS:
        if in_init.get(name) != "connected":
            problems.append(f"init frame shows server {name} as {in_init.get(name)!r}")
        if in_status.get(name) != "connected":
            problems.append(f"get_mcp_status shows server {name} as {in_status.get(name)!r}")
    if init.get("apiKeySource") != "ANTHROPIC_API_KEY":
        problems.append(f"apiKeySource is {init.get('apiKeySource')!r}, not ANTHROPIC_API_KEY")
    return problems


def protocol_version(server: Mapping[str, Any]) -> str:
    for source in (server, server.get("serverInfo") or {}):
        for name in ("protocolVersion", "protocol_version"):
            if source.get(name):
                return str(source[name])
    return "not shown"


async def session(tmp: Path, name: str, *, plant: bool) -> tuple[dict[str, Any], StopAfterInit]:
    clients: list[StopAfterInit] = []

    def factory(*, options: ClaudeAgentOptions) -> StopAfterInit:
        clients.append(StopAfterInit(options=options))
        return clients[-1]

    def adjust(options: ClaudeAgentOptions) -> ClaudeAgentOptions:
        options = closed_port(options)
        return plant_tool(options) if plant else options

    settings = HarnessSettings(api_key=DRY_RUN_KEY, spend_cap_usd=150.0, run_cap_usd=0.50)
    ledger = Ledger(tmp / f"{name}-spend.jsonl", cap_usd=settings.spend_cap_usd, run_cap_usd=settings.run_cap_usd)
    summary = await run_one(
        HOUSEHOLD, CONFIG, 1, SPLIT, settings=settings, ledger=ledger, sim_client=NoSimulator(),
        client_factory=factory, runs_dir=tmp / name, adjust_options=adjust, max_attempts=1,
    )
    return summary, clients[0]


def show_servers(init: Mapping[str, Any], status: Mapping[str, Any] | None) -> list[str]:
    lines = ["  init mcp_servers: " + ", ".join(f"{s.get('name')} {s.get('status')}" for s in init.get("mcp_servers") or [])]
    for server in (status or {}).get("mcpServers") or []:
        info = server.get("serverInfo") or {}
        lines.append(
            f"  get_mcp_status: {server.get('name')} {server.get('status')}, transport {server.get('transport')}, "
            f"serverInfo {info.get('name')} {info.get('version')}, protocol version {protocol_version(server)}"
        )
    return lines


# Stage 3: spend against the fake API.

OPUS_CONFIG = "A-opus"
MONTH_CONFIG = "A-haiku"
LIGHT, HEAVY = "H002", "H003"
ANSWER = "We both had regular jobs, and I had one employer all year."
MONTH_HEADROOM_USD = 0.08  # both first reservations ($0.03 each) and $0.02 more
WORKDIR = re.compile(r"Primary working directory: ([^\\\n\"]+)")


def tool(name: str, **tool_input: Any) -> Block:
    return (TOOL_USE, name if name.startswith("mcp__") else f"mcp__tax__{name}", tool_input)


def household_in(body: Mapping[str, Any]) -> str:
    found = re.search(r"household (H\d{3})", first_user_text(body))
    return found.group(1) if found else LIGHT


def opus_turns(intake: str) -> list[list[Block]]:
    """A run whose calls grow with every tool result, with an evidence denial and a client question on the way."""
    return [
        [tool("start_intake", household_id=LIGHT)],
        [tool("list_fact_keys")],
        [tool("get_document_rules")],
        [tool("record_fact", intake_id=intake, key="w2_employer_count", value=1, evidence="made up words")],
        [tool(ASK_CLIENT_TOOL, question="How many employers paid you wages?", fact_keys=["w2_employer_count"])],
        [tool("record_fact", intake_id=intake, key="w2_employer_count", value=1, evidence="I had one employer all year")],
        [tool("build_request_list", intake_id=intake), tool("list_fact_keys")],
        [tool("submit_for_preparer_review", intake_id=intake)],
        [(TEXT, "Submitted for preparer review.", None)],
    ]


def light_turns(intake: str) -> list[list[Block]]:
    """A short run that fits in its first reservation, with an evidence denial and two tool calls in one turn."""
    return [
        [tool("start_intake", household_id=LIGHT)],
        [tool("record_fact", intake_id=intake, key="w2_employer_count", value=1, evidence="made up words")],
        [tool(ASK_CLIENT_TOOL, question="How many employers paid you wages?", fact_keys=["w2_employer_count"])],
        [tool("record_fact", intake_id=intake, key="w2_employer_count", value=1, evidence="I had one employer all year")],
        [tool("build_request_list", intake_id=intake), tool("list_fact_keys")],
        [tool("submit_for_preparer_review", intake_id=intake)],
        [(TEXT, "Submitted for preparer review.", None)],
    ]


def heavy_turns(intake: str) -> list[list[Block]]:
    """A run whose prompt keeps growing until the month has no headroom left for its next call."""
    return [[tool("start_intake", household_id=HEAVY)],
            *[[tool("list_fact_keys" if i % 2 else "get_document_rules")] for i in range(14)],
            [(TEXT, "Done.", None)]]


def scripted(turns: Callable[[Mapping[str, Any]], list[list[Block]]]) -> Callable[[int, Mapping[str, Any]], list[Block]]:
    def script(step: int, body: Mapping[str, Any]) -> list[Block]:
        plan = turns(body)
        return plan[min(step, len(plan) - 1)]

    return script


def workdirs(body: Mapping[str, Any]) -> list[str]:
    return WORKDIR.findall(json.dumps(body.get("messages") or []))


def request_problems(requests: Sequence[Mapping[str, Any]], cap: int, run_ids: Sequence[str]) -> list[str]:
    """What is wrong with the agent requests the CLI sent: max_tokens, a USD budget, or a working folder that names a run."""
    problems = []
    for r in requests:
        body = r["body"]
        if not r["main"]:
            problems.append(f"a request outside the agent loop reached the API: {r['path']}")
            continue
        if body.get("max_tokens") != cap:
            problems.append(f"{r['path']} step {r['step']}: max_tokens {body.get('max_tokens')}, not {cap}")
        if "USD budget" in json.dumps(body):
            problems.append(f"step {r['step']}: the request shows the agent a USD budget")
        for path in workdirs(body):
            names = [part for part in (*run_ids, "tax-agent-ask", "-dev-", "-test-") if part in path]
            if names or not Path(path).parent.name.startswith("intake-"):
                problems.append(f"step {r['step']}: the working folder {path} names the run ({names})")
    return problems


def held_problems(events: Sequence[Mapping[str, Any]], sent: int) -> list[str]:
    """Each call's prompt must fit the bound held for it, and the harness must count every call the API saw."""
    calls = [e for e in events if e.get("type") == STREAM_USAGE]
    problems = []
    for e in calls:
        prompt = e["usage"]["input_tokens"] + e["usage"]["cache_read_tokens"] + e["usage"]["cache_write_tokens"]
        if e.get("held_prompt_tokens") is None or e["held_prompt_tokens"] < prompt:
            problems.append(f"{e['message_id']}: prompt {prompt} tokens, but {e.get('held_prompt_tokens')} were held")
    if len(calls) != sent:
        problems.append(f"the API saw {sent} agent calls, and the harness counted {len(calls)} finished")
    return problems


def ledger_lines(ledger: Ledger, run_id: str) -> tuple[list[tuple[str, float]], float]:
    """(reserve lines, settled total) for one run."""
    entries = [e for e in ledger.entries() if e["run_id"].split(".top")[0] == run_id]
    reserves = [(e["run_id"], e["reserved_usd"]) for e in entries if e["kind"] == "reserve"]
    return reserves, round(sum(e["actual_usd"] for e in entries if e["kind"] == "settle"), 6)


def trace_of(runs_dir: Path, run_id: str) -> list[dict[str, Any]]:
    path = trace_path(runs_dir, run_id)
    return json.loads(path.read_text())["events"] if path.exists() else []


def to_fake(api: FakeMessagesAPI) -> Callable[[ClaudeAgentOptions], ClaudeAgentOptions]:
    return lambda options: dataclasses.replace(options, env={**options.env, "ANTHROPIC_BASE_URL": api.base_url})


def answers() -> ScriptedClient:
    reply = json.dumps({"answer": ANSWER, "revealed": {"w2_employer_count": 1}})
    return ScriptedClient([reply] * 5, usage=FakeUsage(input_tokens=600, output_tokens=40))


async def opus_stage(tmp: Path, prices: Mapping[str, Any]) -> tuple[bool, list[str]]:
    cfg = get_config(OPUS_CONFIG)
    settings = HarnessSettings(api_key=DRY_RUN_KEY, spend_cap_usd=150.0, run_cap_usd=0.50)
    ledger = Ledger(tmp / "opus-spend.jsonl", cap_usd=settings.spend_cap_usd, run_cap_usd=settings.run_cap_usd)
    runs = tmp / "opus-runs"
    with FakeMessagesAPI(scripted(lambda body: opus_turns(intake_id_in(body))), lambda body: 2500) as api:
        summary = await run_one(LIGHT, OPUS_CONFIG, 1, "dev", settings=settings, ledger=ledger, sim_client=answers(),
                                runs_dir=runs, adjust_options=to_fake(api), max_attempts=1, prices=prices)
    run_id = summary["run_id"]
    reserves, settled = ledger_lines(ledger, run_id)
    reserved = round(sum(amount for _, amount in reserves), 6)
    billed = api.billed_usd(prices)
    events = trace_of(runs, run_id)
    result = ([e for e in events if e.get("type") == "result"] or [{}])[-1]
    problems = request_problems(api.requests, cfg.max_output_tokens, [run_id, "A-opus", "-t1"])
    problems += held_problems(events, sum(r["main"] for r in api.requests))
    if not billed <= settled <= reserved <= settings.run_cap_usd + 1e-9:
        problems.append(f"billed ${billed} <= settled ${settled} <= reserved ${reserved} <= run cap does not hold")
    kinds = [e["kind"] for e in summary["harness_errors"]]
    if kinds != [RUN_CAP] or result.get("terminal_reason") != "hook_stopped":
        problems.append(f"expected one run_cap stop at a hook, got {kinds} and {result.get('terminal_reason')}")
    lines = [
        f"3a. {OPUS_CONFIG} on {LIGHT} at list price against the fake API, 2,500 output tokens per call, RUN_CAP_USD $0.50",
        f"  agent requests: {sum(r['main'] for r in api.requests)}; max_tokens in each: "
        f"{sorted({r['body'].get('max_tokens') for r in api.requests if r['main']})} (cap {cfg.max_output_tokens})",
        f"  prompt tokens per call (one per byte): {[r['usage']['input_tokens'] for r in api.requests if r['main']]}",
        f"  held before each call: {[e.get('held_prompt_tokens') for e in events if e.get('type') == STREAM_USAGE]}",
        f"  requests showing a USD budget: {sum('USD budget' in json.dumps(r['body']) for r in api.requests)}",
        f"  working folder the agent sees: {sorted({w for r in api.requests for w in workdirs(r['body'])})}",
        f"  harness errors: {[(e['kind'], e['message'][:110]) for e in summary['harness_errors']]}",
        f"  CLI result: {result.get('subtype')}, terminal_reason {result.get('terminal_reason')}; status {summary['status']}",
        f"  ledger: reserved {reserves}; settled ${settled}; the fake API would have billed ${billed}",
        f"  billed <= settled <= reserved <= RUN_CAP_USD: {'yes' if billed <= settled <= reserved <= 0.50 + 1e-9 else 'no'}",
    ]
    return not problems, lines + [f"  problems: {problems or 'none'}"]


async def month_stage(tmp: Path, prices: Mapping[str, Any]) -> tuple[bool, list[str]]:
    cfg = get_config(MONTH_CONFIG)
    settings = HarnessSettings(api_key=DRY_RUN_KEY, spend_cap_usd=150.0, run_cap_usd=0.50)
    ledger = Ledger(tmp / "month-spend.jsonl", cap_usd=settings.spend_cap_usd, run_cap_usd=settings.run_cap_usd)
    ledger.reserve("earlier-run", cfg.model, 0.01)
    ledger.settle("earlier-run", round(settings.spend_cap_usd - MONTH_HEADROOM_USD, 6))
    before = ledger.month_to_date()
    runs = tmp / "month-runs"

    def turns(body: Mapping[str, Any]) -> list[list[Block]]:
        intake = intake_id_in(body)
        return heavy_turns(intake) if household_in(body) == HEAVY else light_turns(intake)

    def output(body: Mapping[str, Any]) -> int:
        return 6000 if household_in(body) == HEAVY else 300

    with FakeMessagesAPI(scripted(turns), output) as api:
        outcomes = await asyncio.gather(*(
            run_one(household, MONTH_CONFIG, 1, "dev", settings=settings, ledger=ledger, sim_client=answers(),
                    runs_dir=runs, adjust_options=to_fake(api), max_attempts=1, prices=prices)
            for household in (LIGHT, HEAVY)), return_exceptions=True)
    after = ledger.month_to_date()
    problems = request_problems(api.requests, cfg.max_output_tokens, [f"dev-{h}-{MONTH_CONFIG}-t1" for h in (LIGHT, HEAVY)])
    lines = [f"3b. Two {MONTH_CONFIG} runs at once against the fake API, month to date ${before} of the "
             f"${settings.spend_cap_usd:.2f} cap (${MONTH_HEADROOM_USD} left)"]
    for household, outcome in zip((LIGHT, HEAVY), outcomes):
        run_id = f"dev-{household}-{MONTH_CONFIG}-t1"
        reserves, settled = ledger_lines(ledger, run_id)
        reserved = round(sum(amount for _, amount in reserves), 6)
        billed = api.billed_usd(prices, where=lambda r, h=household: household_in(r["body"]) == h)
        written = summary_path(runs, run_id).exists()
        kinds = ([e["kind"] for e in trace_of(runs, run_id) if e.get("type") == "harness_error"]
                 if isinstance(outcome, BaseException) else [e["kind"] for e in outcome["harness_errors"]])
        sent = sum(r["main"] and household_in(r["body"]) == household for r in api.requests)
        problems += held_problems([e for e in trace_of(runs, run_id)], sent)
        if not billed <= settled <= reserved <= settings.run_cap_usd + 1e-9:
            problems.append(f"{run_id}: billed ${billed} <= settled ${settled} <= reserved ${reserved} does not hold")
        if household == HEAVY and not (isinstance(outcome, SpendCapExceeded) and kinds == [MONTH_CAP] and not written):
            problems.append(f"{run_id}: expected a month_cap stop with no summary, got {type(outcome).__name__} {kinds}")
        if household == LIGHT and (isinstance(outcome, BaseException) or kinds):
            problems.append(f"{run_id}: expected a clean run, got {type(outcome).__name__} {kinds}")
        lines.append(
            f"  {run_id}: {type(outcome).__name__ if isinstance(outcome, BaseException) else 'summary'}"
            f"{': ' + str(outcome)[:100] if isinstance(outcome, BaseException) else ''}; harness errors {kinds}; "
            f"summary written: {'yes' if written else 'no'}; agent calls {sent}; reserved {reserves}; "
            f"settled ${settled}; billed ${billed}")
    if after > settings.spend_cap_usd + 1e-9:
        problems.append(f"month to date ${after} passed the ${settings.spend_cap_usd} cap")
    lines.append(f"  month to date after: ${after} (cap ${settings.spend_cap_usd:.2f}); "
                 f"passed the cap: {'yes' if after > settings.spend_cap_usd + 1e-9 else 'no'}")
    return not problems, lines + [f"  problems: {problems or 'none'}"]


# Stage 4: agent calls the API answers badly.

FAULT_HOUSEHOLD = "H002"


def short_turns(intake: str) -> list[list[Block]]:
    """A short run whose prompts stay small, so an A-opus run fits well inside its run cap."""
    return [
        [tool("start_intake", household_id=FAULT_HOUSEHOLD)],
        [tool("build_request_list", intake_id=intake)],
        [tool("submit_for_preparer_review", intake_id=intake)],
        [(TEXT, "Submitted for preparer review.", None)],
    ]


def cut_turns(intake: str) -> list[list[Block]]:
    """A short run whose reply at step 1 is text, so when its stream is cut off the CLI resumes it."""
    turns = short_turns(intake)
    return [turns[0], [(TEXT, "Let me build the request list next.", None)], *turns[1:]]


@dataclasses.dataclass
class FaultRun:
    """What one run against the fake API's faults came to."""

    name: str
    summary: dict[str, Any]
    events: list[dict[str, Any]]
    faults: list[str]
    billed: float
    reserved: float
    settled: float
    problems: list[str]

    @property
    def calls(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("type") == STREAM_USAGE]

    @property
    def errors(self) -> list[str]:
        return [e["kind"] for e in self.summary["harness_errors"]]

    def lines(self) -> list[str]:
        costs = ([e for e in self.events if e.get("type") == "costs"] or [{}])[-1]
        return [
            f"  {self.name}: agent requests {len(self.faults)} ({self.faults}); attempts {self.summary['attempts']}; "
            f"status {self.summary['status']}; harness errors "
            f"{[(e['kind'], e['message'][:120]) for e in self.summary['harness_errors']] or 'none'}",
            f"    calls the harness counted (outcome, prompt held before it, cost): "
            f"{[(e.get('outcome'), e.get('held_prompt_tokens'), round(e['cost_usd'], 6)) for e in self.calls]}",
            f"    agent cost ${self.summary['cost_breakdown']['agent']} (known: {costs.get('agent_cost_known')}); "
            f"the fake API could have billed ${self.billed}; reserved ${self.reserved}; settled ${self.settled}",
        ]


async def fault_run(tmp: Path, prices: Mapping[str, Any], name: str, config: str, fault: str, at: Sequence[int],
                    turns: Callable[[str], list[list[Block]]], env: Mapping[str, str] | None = None) -> FaultRun:
    """One run against the fake API, with fault on the agent requests numbered in at."""
    settings = HarnessSettings(api_key=DRY_RUN_KEY, spend_cap_usd=150.0, run_cap_usd=0.50)
    ledger = Ledger(tmp / f"{name}-spend.jsonl", cap_usd=settings.spend_cap_usd, run_cap_usd=settings.run_cap_usd)
    runs = tmp / f"{name}-runs"

    def adjust(api: FakeMessagesAPI) -> Callable[[ClaudeAgentOptions], ClaudeAgentOptions]:
        return lambda options: dataclasses.replace(
            options, env={**options.env, "ANTHROPIC_BASE_URL": api.base_url, **(env or {})})

    faults = lambda n, body: fault if n in at else None  # noqa: E731
    with FakeMessagesAPI(scripted(lambda body: turns(intake_id_in(body))), lambda body: 400, faults=faults) as api:
        summary = await run_one(FAULT_HOUSEHOLD, config, 1, "dev", settings=settings, ledger=ledger,
                                sim_client=answers(), runs_dir=runs, adjust_options=adjust(api), backoff_s=0.01,
                                prices=prices)
    run_id = summary["run_id"]
    reserves, settled = ledger_lines(ledger, run_id)
    reserved = round(sum(amount for _, amount in reserves), 6)
    billed = api.billed_usd(prices, where=lambda r: r["main"])
    problems = request_problems(api.requests, get_config(config).max_output_tokens, [run_id, config, "-t1"])
    if not billed <= summary["cost_breakdown"]["agent"] + 1e-9:
        problems.append(f"{name}: agent cost ${summary['cost_breakdown']['agent']} is under the ${billed} billed")
    if not settled <= reserved <= settings.run_cap_usd + 1e-9:
        problems.append(f"{name}: settled ${settled} <= reserved ${reserved} <= run cap does not hold")
    if any(e.get("held_prompt_tokens") is None for e in trace_of(runs, run_id) if e.get("type") == STREAM_USAGE):
        problems.append(f"{name}: an agent call was sent with nothing held for it")
    return FaultRun(name, summary, trace_of(runs, run_id), [r["fault"] or "ok" for r in api.requests if r["main"]],
                    billed, reserved, settled, problems)


def finished(run: FaultRun) -> list[str]:
    if run.errors or run.summary["status"] != "pending_review":
        return [f"{run.name}: expected a finished intake, got {run.summary['status']} and {run.errors}"]
    return []


async def fault_stage(tmp: Path, prices: Mapping[str, Any]) -> tuple[bool, list[str]]:
    lines = ["4. Agent calls the fake API answers badly"]
    problems: list[str] = []

    run = await fault_run(tmp, prices, "refused", OPUS_CONFIG, REFUSED, (1, 2, 3), short_turns,
                          env={"CLAUDE_CODE_MAX_RETRIES": "0"})
    retries = [e for e in run.events if e.get("type") == "retry"]
    known = ([e for e in run.events if e.get("type") == "costs"] or [{}])[-1].get("agent_cost_known")
    problems += run.problems + finished(run)
    if len(retries) != 3 or run.summary["attempts"] != 4 or not known:
        problems.append(f"refused: expected four attempts at a known cost, got {run.summary['attempts']}, known {known}")
    lines += [f"  {OPUS_CONFIG}: the first three agent requests answered 429, with CLAUDE_CODE_MAX_RETRIES=0",
              *run.lines()]

    run = await fault_run(tmp, prices, "cut", CONFIG, CUT, (2,), cut_turns)
    problems += run.problems + finished(run)
    if [e.get("outcome") for e in run.calls].count("cut_off") != 1:
        problems.append(f"cut: expected one cut_off call, got {[e.get('outcome') for e in run.calls]}")
    lines += [f"  {CONFIG}: agent request 2 cut off mid-stream by an error event, then the CLI's resume",
              *run.lines()]

    run = await fault_run(tmp, prices, "dropped", CONFIG, DROPPED, (2,), short_turns)
    problems += run.problems + finished(run)
    if not any(str(e.get("outcome")).startswith("no_answer") for e in run.calls):
        problems.append(f"dropped: the request with no answer was not counted ({[e.get('outcome') for e in run.calls]})")
    lines += [f"  {CONFIG}: agent request 2 dropped with no answer, then the CLI's retry", *run.lines()]

    # The CLI sends its resume within milliseconds and no hook comes first, so stopping it is best effort.
    run = await fault_run(tmp, prices, "cut-tight", OPUS_CONFIG, CUT, (2,), cut_turns)
    problems += run.problems
    if run.errors != [RUN_CAP] or "cut-off stream" not in run.summary["harness_errors"][0]["message"]:
        problems.append(f"cut-tight: expected a run_cap stop at the cut-off stream, got {run.errors}")
    resumed = len(run.faults) > 2
    lines += [f"  {OPUS_CONFIG}: the same cut, where the resume's bound no longer fits under the run cap",
              *run.lines(), f"    the resume reached the API before the interrupt landed: {'yes' if resumed else 'no'}"]
    return not problems, lines + [f"  problems: {problems or 'none'}"]


async def main_async() -> int:
    ok = True
    with tempfile.TemporaryDirectory(prefix="wiring-") as tmp:
        tmp_path = Path(tmp)
        clean, client = await session(tmp_path, "clean", plant=False)
        init = client.init or {}
        problems = assess(client.init, client.status) + [
            f"harness error: {e['kind']}: {e['message']}" for e in clean["harness_errors"]]
        print(f"1. Clean session through run_one ({CONFIG}, {SPLIT} {HOUSEHOLD}, placeholder key, {CLOSED_PORT_URL})")
        print(f"  claude_code_version: {init.get('claude_code_version')}")
        print(f"  init tools ({len(init.get('tools') or [])}): {json.dumps(init.get('tools'))}")
        print(f"  exactly the 9 allowed tools: {'yes' if sorted(init.get('tools') or []) == sorted(ALLOWED_TOOLS) else 'no'}")
        print("\n".join(show_servers(init, client.status)))
        print(f"  apiKeySource: {init.get('apiKeySource')}")
        print(f"  harness errors: {clean['harness_errors'] or 'none'}; tool calls: {len(clean['tool_calls'])}")
        print(f"  problems: {problems or 'none'}")
        ok = ok and not problems

        planted, client = await session(tmp_path, "planted", plant=True)
        init = client.init or {}
        errors = planted["harness_errors"]
        aborted = bool(errors) and errors[0]["kind"] == "init_check" and PLANTED in errors[0]["message"]
        print(f'2. Planted built-in tool (tools=["{PLANTED}"]) through run_one')
        print(f"  init tools ({len(init.get('tools') or [])}): {json.dumps(init.get('tools'))}")
        print(f"  harness errors: {json.dumps(errors)}")
        print(f"  tool calls after the init frame: {len(planted['tool_calls'])}; questions: {len(planted['questions'])}")
        print(f"  init check aborted the run: {'yes' if aborted else 'no'}")
        ok = ok and aborted and not planted["tool_calls"]

        prices = load_prices()
        for stage in (opus_stage, month_stage, fault_stage):
            passed, lines = await stage(tmp_path, prices)
            print("\n".join(lines))
            ok = ok and passed
    print(f"Wiring check: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
