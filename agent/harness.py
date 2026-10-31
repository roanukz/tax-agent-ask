"""Block 5 harness (PRD 6.2): one agent config on one household, in one isolated Agent SDK session.

    summary = await run_one("H009", "A-sonnet", 1, "dev", settings=settings, ledger=ledger, sim_client=client)

The session gets the system prompt from PRD 6.3, no built-in tools, no
settings, an empty working folder, and exactly two MCP servers: "tax" (the
stdio server, started by absolute path with the profiles folder and this
split's rule statuses) and "harness" (in-process, with ask_client). The
harness, not the agent, holds the household's truth file: only the client
simulator behind ask_client reads it.

Order of a run:
1. Skip without spending when eval/runs/{run_id}.summary.json exists.
2. The test split only: eval.run.check_prereg must pass again here, so no
   caller can run test households before eval/prereg.yaml is locked and
   committed, and its commit must be the one the sweep started with.
3. Reserve the config's reserve_usd in the ledger.
4. A-split only: one planner call (Sonnet, Messages API) turns last year's
   summary, the fact keys, and this split's rules into a question plan.
5. One session. On the init frame, agent.guard.check_init must pass, every
   allowed tool must be visible, and both MCP servers must report
   connected, or the run records an init_check harness error, interrupts,
   and makes no further calls. A PreToolUse gate also denies every tool
   call until the check has passed, and any tool outside the allowed set.
   record_fact goes through the evidence hook; PostToolUse appends to the
   trace. The CLI sends the init frame only after it has the first user
   message, so a failed check can't keep the first model request from
   starting: the harness interrupts at once and counts what the result
   message reports.
6. On a 429 that ended the session (ResultMessage.api_error_status 429, an
   assistant message with error rate_limit in a session that ended in error,
   or anthropic.RateLimitError from the simulator), back off and run a fresh
   session, up to MAX_ATTEMPTS, waiting BACKOFF_S and then twice as long
   each time. The planner backs off the same way on its own call.
7. Settle the ledger with the actual total, write the trace and the summary.

Spend. The agent's cost is ResultMessage.total_cost_usd, or the list price
of its usage when that is missing; the simulator's and planner's costs come
from their guards. A run may spend only what it has reserved in the ledger,
so runs going at once never count on the same headroom and the month can't
pass SPEND_CAP_USD. When the next call needs more, the run tops its
reservation up first (Reservation.cover), against the month cap and under
the ledger's lock, up to RUN_CAP_USD in all. The simulator and planner
guards top up before each call, by that call's upper bound, and refuse it
when the ledger won't allow it; the harness interrupts the agent when
another call priced like its last can't be covered. The CLI's own
max_budget_usd is a backstop at the most the run could still reserve.

Retries and the ledger. A run reserves under its run ID, and every attempt
spends from that one reservation and its top-ups: a 429 retry never
reserves again, so there is no duplicate reservation and month to date
counts the run once, at the total it settles with. A run that is canceled
settles at once with what it spent (at least what it reserved, since an
interrupted agent call's cost is unknown) and writes no summary; resuming
it later reserves under the next free ID, {run_id}.r2 and so on, so the
ledger keeps both.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

import anthropic
import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from agent.ask_client import (
    ASK_CLIENT_TOOL,
    RATE_LIMITED,
    RUN_CAP,
    ClientChannel,
    ProbeSchedule,
    RunControl,
)
from agent.configs import MAX_TURNS, SIMULATOR_MODEL, Config, get_config
from agent.env import HarnessSettings, MissingKey
from agent.guard import ToolListViolation, WrongCredential, check_init
from agent.hooks import RECORD_FACT_TOOL, EvidenceBook, record_fact_hook
from agent.options import isolated_options
from agent.prompts import PLANNER_PROMPT, SYSTEM_PROMPT, fill, first_message, split_first_message
from agent.summary import build_summary, result_text
from ledger.spend import Ledger, RunCapExceeded, SpendCapExceeded, cost_usd, load_prices, normalize_usage
from rules import engine
from server.intake import fact_view, rule_view
from server.tax_server import TOOL_NAMES
from sim.budget import BudgetedClient, BudgetExceeded, call_bound_usd
from sim.simulator import ClientSimulator, reply_text

REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
PROFILES_DIR = DATA_DIR / "profiles"
RUNS_DIR = REPO / "eval" / "runs"
SERVER_SCRIPT = REPO / "server" / "tax_server.py"

TAX_TOOLS = tuple(f"mcp__tax__{name}" for name in TOOL_NAMES)
ALLOWED_TOOLS = (*TAX_TOOLS, ASK_CLIENT_TOOL)
MCP_SERVERS = ("tax", "harness")
TEST_SPLIT = "test"

MAX_ATTEMPTS = 4
BACKOFF_S = 30.0
DRAIN_TIMEOUT_S = 30.0
PLANNER_MAX_TOKENS = 4096
SLACK_USD = 1e-9  # float slack, a billionth of a dollar, so an exactly covered call is not refused

INIT_CHECK = "init_check"
API_ERROR = "api_error"
EXCEPTION = "exception"
NO_RESULT = "no_result"
PLANNER = "planner"
PLANNER_API = "planner_api_error"
SIMULATOR_GUARD = "simulator"

Sleep = Callable[[float], Awaitable[Any]]


def run_id_for(split: str, household_id: str, config: str, trial: int) -> str:
    return f"{split}-{household_id}-{config}-t{trial}"


def summary_path(runs_dir: Path, run_id: str) -> Path:
    return Path(runs_dir) / f"{run_id}.summary.json"


def trace_path(runs_dir: Path, run_id: str) -> Path:
    return Path(runs_dir) / f"{run_id}.trace.json"


def write_json(path: Path, data: Any) -> None:
    """Writes through a temp file, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str) + "\n")
    os.replace(tmp, path)


def load_household(data_dir: Path, household_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(profile, truth). The truth stays in the harness; the agent can reach only the two MCP servers."""
    profile = json.loads((Path(data_dir) / "profiles" / f"{household_id}.json").read_text())
    truth = json.loads((Path(data_dir) / "truth" / f"{household_id}.json").read_text())
    return profile, truth


def tax_server_config(split: str, state_dir: Path, *, profiles_dir: Path = PROFILES_DIR) -> dict[str, Any]:
    """The stdio config for the tax server, by absolute path, with this split's rule statuses."""
    return {
        "type": "stdio",
        "command": sys.executable,
        "args": [
            str(SERVER_SCRIPT),
            "--profiles", str(Path(profiles_dir).resolve()),
            "--statuses", ",".join(engine.STATUSES_FOR_SPLIT[split]),
            "--state-dir", str(Path(state_dir).resolve()),
        ],
        "env": {"PYTHONPATH": str(REPO)},
    }


def free_ledger_id(ledger: Ledger, run_id: str) -> str:
    """run_id, or {run_id}.r2, .r3, and so on when an earlier attempt already holds it."""
    taken = {e.get("run_id") for e in ledger.entries()}
    if run_id not in taken:
        return run_id
    n = 2
    while f"{run_id}.r{n}" in taken:
        n += 1
    return f"{run_id}.r{n}"


def locked_commit(configs: Sequence[str], claimed: str | None = None, *, repo: Path | None = None) -> str:
    """The prereg commit that unlocks the test split, checked again here so no caller can skip the lock.

    claimed is the commit the sweep started with; a different one now means
    eval/prereg.yaml changed mid-sweep. repo is the repository root, REPO
    except in tests.
    """
    from eval.run import PreregError, check_prereg  # eval.run imports this module, so import it on use

    commit = check_prereg(repo=repo, configs=configs)
    if claimed is not None and claimed != commit:
        raise PreregError(f"the sweep started at prereg commit {claimed}, but eval/prereg.yaml is now at {commit}")
    return commit


def init_problems(data: Mapping[str, Any]) -> list[str]:
    """What the init frame shows wrong beyond agent.guard.check_init: a missing allowed tool, or a server not connected."""
    problems = []
    missing = sorted(set(ALLOWED_TOOLS) - set(data.get("tools") or []))
    if missing:
        problems.append(f"allowed tools missing from the session: {', '.join(missing)}")
    servers = {s.get("name"): s.get("status") for s in data.get("mcp_servers") or [] if isinstance(s, Mapping)}
    for name in MCP_SERVERS:
        if servers.get(name) != "connected":
            problems.append(f"MCP server {name} is {servers.get(name) or 'missing'}, not connected")
    return problems


def ceil_usd(value: float) -> float:
    """Up to the next millionth of a dollar, the ledger's precision."""
    return math.ceil(round(value, 9) * 1_000_000) / 1_000_000


def committed_usd(client: BudgetedClient) -> float:
    """What a guarded client has spent or has in flight."""
    return client.known_usd + client.unknown_usd + client.in_flight_usd


class Reservation:
    """One run's ledger lines: its first reservation, then top-ups, each checked against the month cap.

    A run may spend only what it has reserved. When a call needs more,
    cover() reserves another step (the first reservation's size, or just the
    shortfall when the month has less left) as {ledger_id}.top2, .top3, and
    so on, up to RUN_CAP_USD for the run in all. Ledger.reserve checks each
    line against the month cap under the ledger's file lock, so runs going
    at once can't both count on the same headroom. settle() splits the
    run's total over its lines in order, so month to date counts what the
    run spent, once.
    """

    def __init__(self, ledger: Ledger, run_id: str, model: str | None, first_usd: float, *, run_cap_usd: float) -> None:
        self.ledger = ledger
        self.model = model
        self.step_usd = float(first_usd)
        self.run_cap_usd = float(run_cap_usd)
        self.ledger_id = free_ledger_id(ledger, run_id)
        self.lines: list[tuple[str, float]] = []
        self.done: set[str] = set()
        self._add(self.ledger_id, self.step_usd)

    @property
    def reserved(self) -> float:
        return round(sum(amount for _, amount in self.lines), 6)

    @property
    def settled(self) -> bool:
        return len(self.done) == len(self.lines)

    def _add(self, line_id: str, amount: float) -> None:
        self.ledger.reserve(line_id, self.model, amount)
        self.lines.append((line_id, round(amount, 6)))

    def cover(self, need_usd: float) -> bool:
        """True when the reservation covers need_usd, topping it up first when the run cap and the month cap allow."""
        if need_usd <= self.reserved + SLACK_USD:
            return True
        short = ceil_usd(need_usd - self.reserved)
        room = round(self.run_cap_usd - self.reserved, 6)
        if short > room:
            return False
        for amount in dict.fromkeys((round(min(max(self.step_usd, short), room), 6), short)):
            try:
                self._add(f"{self.ledger_id}.top{len(self.lines) + 1}", amount)
            except (SpendCapExceeded, RunCapExceeded):
                continue
            return True
        return False

    def headroom(self) -> float:
        return max(0.0, self.ledger.cap_usd - self.ledger.month_to_date())

    def settle(self, total_usd: float) -> None:
        """Each line takes the total up to its own amount, in order; the last line takes anything past the reservation.

        A line already settled is skipped, so a second call after a partial failure never settles one twice.
        """
        left = max(0.0, float(total_usd))
        for i, (line_id, amount) in enumerate(self.lines):
            share = left if i == len(self.lines) - 1 else min(left, amount)
            if line_id not in self.done:
                self.ledger.settle(line_id, round(share, 6))
                self.done.add(line_id)
            left = max(0.0, left - share)


class ReservedClient(BudgetedClient):
    """A BudgetedClient whose limit, before each call, is its share of the run's reservation, topped up for that call."""

    def __init__(self, client: Any, *, model: str, prices: Mapping[str, Any],
                 room: Callable[[BudgetedClient, float], float]) -> None:
        super().__init__(client, model=model, prices=prices, limit_usd=0.0)
        self.room = room
        self.call_prices = dict(prices)

    async def create(self, kwargs: Mapping[str, Any]) -> Any:
        self.limit_usd = self.room(self, call_bound_usd(kwargs, self.model, self.call_prices))
        return await super().create(kwargs)


class Budget:
    """One run's spend against its reservation: the agent's messages as they stream, plus each guarded client."""

    def __init__(self, reservation: Reservation, model: str | None, prices: Mapping[str, Any]) -> None:
        self.reservation = reservation
        self.model = model
        self.prices = dict(prices)
        self.agent_done_usd = 0.0
        self.agent_usage = normalize_usage({})
        self.clients: dict[str, ReservedClient] = {}
        self.unknown = False
        self._messages: dict[str, tuple[float, dict[str, int]]] = {}
        self.last_call_usd = 0.0

    def client(self, name: str, inner: Any, model: str) -> ReservedClient:
        """A guard on inner whose calls spend from this run's reservation."""
        self.clients[name] = ReservedClient(inner, model=model, prices=self.prices, room=self.room)
        return self.clients[name]

    def spent(self, name: str) -> float:
        guard = self.clients.get(name)
        return guard.spent_usd if guard is not None else 0.0

    def agent_running(self) -> float:
        return self.agent_done_usd + sum(cost for cost, _ in self._messages.values())

    def committed(self) -> float:
        return self.agent_running() + sum(committed_usd(c) for c in self.clients.values())

    def total(self) -> float:
        return round(self.agent_running() + sum(c.spent_usd for c in self.clients.values()), 6)

    def make_room(self, extra_usd: float) -> bool:
        """True when the reservation covers everything committed plus extra_usd, after a top-up if one is needed."""
        return self.reservation.cover(self.committed() + extra_usd)

    def room(self, client: BudgetedClient, bound_usd: float) -> float:
        """client's limit for a call that could cost bound_usd: the reservation, less everything else committed."""
        self.make_room(bound_usd)
        others = self.committed() - committed_usd(client)
        return self.reservation.reserved - others + SLACK_USD

    def backstop(self) -> float:
        """The CLI's max_budget_usd: the most the run could still reserve, under the run cap and the month's headroom."""
        most = min(self.reservation.run_cap_usd, self.reservation.reserved + self.reservation.headroom())
        return round(max(0.0, most - self.total()), 6)

    def record_message(self, key: str, usage: Mapping[str, Any] | None) -> None:
        """Prices one agent API call as it streams; split messages share an ID and count once."""
        if not usage:
            return
        tokens = normalize_usage(dict(usage))
        prompt = tokens["input_tokens"] + tokens["cache_read_tokens"] + tokens["cache_write_tokens"]
        cost = cost_usd(self.model, tokens, self.prices, prompt_tokens=prompt)
        old = self._messages.get(key)
        if old is None or cost >= old[0]:
            self._messages[key] = (cost, tokens)
        self.last_call_usd = cost

    def end_attempt(self, result: ResultMessage | None, *, queried: bool) -> None:
        streamed_cost = sum(cost for cost, _ in self._messages.values())
        streamed_usage = normalize_usage({})
        for _, tokens in self._messages.values():
            for name, value in tokens.items():
                streamed_usage[name] += value
        if result is not None and result.total_cost_usd is not None:
            cost = float(result.total_cost_usd)
        elif result is not None and result.usage:
            cost = cost_usd(self.model, result.usage, self.prices)
        else:
            cost = streamed_cost
            self.unknown = self.unknown or queried
        usage = normalize_usage(result.usage) if result is not None and result.usage else streamed_usage
        self.agent_done_usd = round(self.agent_done_usd + cost, 6)
        for name, value in usage.items():
            self.agent_usage[name] += value
        self._messages = {}
        self.last_call_usd = 0.0


class Session:
    """Turns one Agent SDK session's message stream and hook calls into the run's event log."""

    def __init__(
        self,
        *,
        events: list[dict[str, Any]],
        control: RunControl,
        budget: Budget,
        channel: ClientChannel,
        book: EvidenceBook,
    ) -> None:
        self.events = events
        self.control = control
        self.budget = budget
        self.channel = channel
        self.evidence = record_fact_hook(book)
        self.book = book
        self.init_ok = False
        self.rate_limited = False
        self.result: ResultMessage | None = None
        self.queried = False
        self._messages = 0

    @property
    def limited(self) -> bool:
        """A 429 that ended the session: from the agent's API calls, or from the simulator."""
        if self.control.stop_kind == RATE_LIMITED:
            return True
        if self.result is not None and self.result.api_error_status == 429:
            return True
        return self.rate_limited and (self.result is None or self.result.is_error)

    # Hooks.

    async def gate(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        """PreToolUse on every tool: nothing runs before the init check passes, after a stop, or outside the allowed set."""
        name = input_data.get("tool_name")
        if not self.init_ok:
            reason = "the tool list check has not passed"
        elif self.control.stop_kind is not None:
            reason = f"the intake has stopped ({self.control.stop_kind})"
        elif name not in ALLOWED_TOOLS:
            reason = f"{name} is not an allowed tool"
        else:
            if name == ASK_CLIENT_TOOL:
                self.channel.expect(tool_use_id, input_data.get("tool_input") or {})
            return {}
        self.events.append({"type": "gate_denial", "tool_use_id": tool_use_id, "tool_name": name, "reason": reason})
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    async def check_evidence(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        """record_fact_hook, logging each denial where it happened."""
        before = len(self.book.denials)
        out = await self.evidence(input_data, tool_use_id, context)
        for denial in self.book.denials[before:]:
            self.events.append({"type": "hook_denial", **denial})
        return out

    async def post_tool_use(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        self.events.append({
            "type": "post_tool_use",
            "tool_use_id": tool_use_id,
            "tool_name": input_data.get("tool_name"),
            "tool_response": input_data.get("tool_response"),
        })
        return {}

    def hooks(self) -> dict[str, list[HookMatcher]]:
        return {
            "PreToolUse": [
                HookMatcher(matcher=None, hooks=[self.gate]),
                HookMatcher(matcher=RECORD_FACT_TOOL, hooks=[self.check_evidence]),
            ],
            "PostToolUse": [HookMatcher(matcher=None, hooks=[self.post_tool_use])],
        }

    # Messages.

    def on_init(self, data: Mapping[str, Any]) -> None:
        self.events.append({
            "type": "init",
            "tools": data.get("tools"),
            "api_key_source": data.get("apiKeySource"),
            "mcp_servers": data.get("mcp_servers"),
            "model": data.get("model"),
            "claude_code_version": data.get("claude_code_version"),
        })
        try:
            check_init(dict(data), ALLOWED_TOOLS)
        except (ToolListViolation, WrongCredential) as e:
            self.control.stop(INIT_CHECK, f"{type(e).__name__}: {e}")
            return
        problems = init_problems(data)
        if problems:
            self.control.stop(INIT_CHECK, "; ".join(problems))
            return
        self.init_ok = True

    def on_assistant(self, message: AssistantMessage) -> None:
        self._messages += 1
        key = message.message_id or f"message-{self._messages}"
        for block in message.content:
            if isinstance(block, TextBlock):
                self.events.append({"type": "assistant_text", "text": block.text, "message_id": key})
            elif isinstance(block, ToolUseBlock):
                self.events.append({"type": "tool_call", "id": block.id, "name": block.name, "input": block.input})
        if message.error == "rate_limit":
            self.rate_limited = True
        self.budget.record_message(key, message.usage)
        calls_tool = any(isinstance(b, ToolUseBlock) for b in message.content)
        if calls_tool and not self.budget.make_room(self.budget.last_call_usd):
            self.control.stop(
                RUN_CAP,
                f"${self.budget.total():.4f} spent of ${self.budget.reservation.reserved:.4f} reserved; another "
                "agent call like the last needs more than the run cap or the month's headroom allows",
            )

    def on_user(self, message: UserMessage) -> None:
        if isinstance(message.content, str):
            return
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                self.events.append({
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "is_error": bool(block.is_error),
                    "text": result_text(block.content),
                })

    def on_result(self, message: ResultMessage) -> None:
        self.result = message
        self.events.append({
            "type": "result",
            "subtype": message.subtype,
            "is_error": message.is_error,
            "num_turns": message.num_turns,
            "duration_ms": message.duration_ms,
            "total_cost_usd": message.total_cost_usd,
            "usage": normalize_usage(message.usage),
            "api_error_status": message.api_error_status,
            "stop_reason": message.stop_reason,
            "result": message.result,
        })
        if message.api_error_status is not None and message.api_error_status != 429:
            self.control.error(API_ERROR, f"the agent's API call failed with status {message.api_error_status}")

    def handle(self, message: Any) -> None:
        if isinstance(message, SystemMessage) and message.subtype == "init":
            self.on_init(message.data)
        elif isinstance(message, AssistantMessage):
            self.on_assistant(message)
        elif isinstance(message, UserMessage):
            self.on_user(message)
        elif isinstance(message, ResultMessage):
            self.on_result(message)

    async def converse(self, client: Any, prompt: str) -> None:
        """Sends the first message and reads to the result; after a stop, interrupts and drains."""
        await client.query(prompt)
        self.queried = True
        interrupted = False
        with anyio.CancelScope() as scope:
            async for message in client.receive_response():
                self.handle(message)
                if self.control.stop_kind is not None and not interrupted:
                    interrupted = True
                    scope.deadline = anyio.current_time() + DRAIN_TIMEOUT_S
                    try:
                        await client.interrupt()
                    except Exception as e:
                        self.control.error(NO_RESULT, f"the interrupt failed: {type(e).__name__}: {e}")
                        break
        if scope.cancelled_caught:
            self.control.error(NO_RESULT, f"no result message within {DRAIN_TIMEOUT_S:.0f} s of the interrupt")


def planner_prompt(
    profile: Mapping[str, Any],
    specs: Mapping[str, engine.FactSpec],
    rules: list[engine.Rule],
    statuses: tuple[str, ...],
) -> str:
    """PLANNER_PROMPT with last year's summary, the fact keys, and this split's rules, as the agent's tools show them."""
    return fill(
        PLANNER_PROMPT,
        prior_year_summary_json=json.dumps(profile["prior_year_summary"], indent=2),
        fact_keys_json=json.dumps([fact_view(spec) for spec in specs.values()], indent=2),
        rules_json=json.dumps([rule_view(r) for r in engine.rules_with_status(rules, statuses)], indent=2),
    )


def parse_plan(text: str) -> list[dict[str, Any]]:
    """The planner's {"plan": [...]}, from bare JSON, a fenced block, or JSON inside prose."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1].rsplit("```", 1)[0]
    start = body.find("{")
    if start < 0:
        raise ValueError("the planner reply has no JSON object")
    obj, _ = json.JSONDecoder().raw_decode(body[start:])
    plan = obj.get("plan") if isinstance(obj, dict) else None
    if not isinstance(plan, list) or not plan:
        raise ValueError('the planner reply has no "plan" list')
    for item in plan:
        if not isinstance(item, dict) or not isinstance(item.get("question"), str):
            raise ValueError("every plan item needs a question")
    return plan


class Run:
    """Everything one run_one call keeps between the planner and its session attempts."""

    def __init__(
        self,
        *,
        cfg: Config,
        meta: dict[str, Any],
        profile: dict[str, Any],
        truth: dict[str, Any],
        settings: HarnessSettings,
        budget: Budget,
        sim_guard: BudgetedClient,
        split: str,
        profiles_dir: Path,
        client_factory: Callable[..., Any],
        adjust_options: Callable[[ClaudeAgentOptions], ClaudeAgentOptions] | None,
        sleep: Sleep,
        max_attempts: int,
        backoff_s: float,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.cfg = cfg
        self.system_prompt = system_prompt
        self.meta = meta
        self.profile = profile
        self.truth = truth
        self.settings = settings
        self.budget = budget
        self.sim_guard = sim_guard
        self.split = split
        self.statuses = engine.STATUSES_FOR_SPLIT[split]
        self.profiles_dir = profiles_dir
        self.client_factory = client_factory
        self.adjust_options = adjust_options
        self.sleep = sleep
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self.specs = engine.load_facts()
        self.rules = engine.load_rules()
        self.events: list[dict[str, Any]] = []
        self.attempts = 0

    def delay(self, n: int) -> float:
        return self.backoff_s * 2 ** (n - 1)

    async def plan(self, client: Any) -> str | None:
        """The A-split question plan as JSON text, or None after a harness error."""
        control = RunControl(self.events)
        guard = self.budget.client(PLANNER, client, self.cfg.planner_model)
        prompt = planner_prompt(self.profile, self.specs, self.rules, self.statuses)
        response = None
        try:
            for n in range(1, self.max_attempts + 1):
                try:
                    response = await guard.messages.create(
                        model=self.cfg.planner_model,
                        max_tokens=PLANNER_MAX_TOKENS,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    break
                except anthropic.RateLimitError:
                    if n == self.max_attempts:
                        control.error(RATE_LIMITED, f"the planner was still rate limited after {n} attempts")
                        return None
                    self.events.append({"type": "retry", "reason": "planner_rate_limited", "after_s": self.delay(n)})
                    await self.sleep(self.delay(n))
        except BudgetExceeded as e:
            control.error(RUN_CAP, f"planner call not sent: {e}")
            return None
        except anthropic.APIError as e:
            control.error(PLANNER_API, f"{type(e).__name__}: {e}")
            return None
        text = reply_text(response)
        try:
            plan = parse_plan(text)
        except ValueError as e:
            self.events.append({"type": "planner", "plan": None, "text": text, "cost_usd": guard.spent_usd})
            control.error(PLANNER, str(e))
            return None
        self.events.append({"type": "planner", "plan": plan, "cost_usd": guard.spent_usd})
        return json.dumps({"plan": plan}, indent=2)

    def options(self, run_dir: Path, session: Session, channel: ClientChannel) -> ClaudeAgentOptions:
        options = isolated_options(
            model=self.cfg.model,
            settings=self.settings,
            run_dir=run_dir,
            system_prompt=self.system_prompt,
            allowed_tools=list(ALLOWED_TOOLS),
            mcp_servers={
                "tax": tax_server_config(self.split, run_dir / "server-state", profiles_dir=self.profiles_dir),
                "harness": channel.server(),
            },
            hooks=session.hooks(),
            max_turns=MAX_TURNS,
        )
        options = dataclasses.replace(options, max_budget_usd=self.budget.backstop())
        return self.adjust_options(options) if self.adjust_options else options

    async def attempt(self, prompt: str) -> Session:
        self.attempts += 1
        self.events.append({"type": "attempt", "n": self.attempts})
        control = RunControl(self.events)
        book = EvidenceBook(prior_year_summary=self.profile["prior_year_summary"])
        simulator = ClientSimulator(self.truth, self.sim_guard, model=SIMULATOR_MODEL, specs=self.specs)
        channel = ClientChannel(simulator, book, ProbeSchedule.from_truth(self.truth), control)
        session = Session(events=self.events, control=control, budget=self.budget, channel=channel, book=book)
        with tempfile.TemporaryDirectory(prefix=f"tax-agent-ask-{self.meta['run_id']}-") as tmp:
            try:
                async with self.client_factory(options=self.options(Path(tmp), session, channel)) as client:
                    await session.converse(client, prompt)
            except Exception as e:
                control.error(EXCEPTION, f"{type(e).__name__}: {e}")
            finally:
                self.budget.end_attempt(session.result, queried=session.queried)
        return session

    async def converse(self, prompt: str) -> None:
        for n in range(1, self.max_attempts + 1):
            session = await self.attempt(prompt)
            if not session.limited:
                return
            if n == self.max_attempts:
                RunControl(self.events).error(RATE_LIMITED, f"the agent was still rate limited after {n} attempts")
                return
            self.events.append({"type": "retry", "reason": RATE_LIMITED, "after_s": self.delay(n)})
            await self.sleep(self.delay(n))

    def costs_event(self, latency_s: float) -> dict[str, Any]:
        return {
            "type": "costs",
            "agent_usd": round(self.budget.agent_done_usd, 6),
            "simulator_usd": round(self.budget.spent(SIMULATOR_GUARD), 6),
            "planner_usd": round(self.budget.spent(PLANNER), 6),
            "agent_usage": dict(self.budget.agent_usage),
            "agent_cost_known": not self.budget.unknown,
            "latency_s": round(latency_s, 3),
            "attempts": self.attempts,
        }


async def run_one(
    household_id: str,
    config: str,
    trial: int,
    split: str,
    *,
    settings: HarnessSettings,
    ledger: Ledger,
    sim_client: Any,
    client_factory: Callable[..., Any] = ClaudeSDKClient,
    planner_client: Any = None,
    runs_dir: Path = RUNS_DIR,
    data_dir: Path = DATA_DIR,
    prices: Mapping[str, Any] | None = None,
    sleep: Sleep = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    max_attempts: int = MAX_ATTEMPTS,
    backoff_s: float = BACKOFF_S,
    prereg_commit: str | None = None,
    adjust_options: Callable[[ClaudeAgentOptions], ClaudeAgentOptions] | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    run_id: str | None = None,
    prereg_repo: Path | None = None,
) -> dict[str, Any]:
    """Runs one agent config on one household and returns its summary (also written to runs_dir).

    system_prompt and run_id are for judge validation transcripts only (a
    deliberately weakened prompt under its own run ID, which it must have,
    so it never takes the place of the real run); a summary made with any
    other prompt says so in its system_prompt field. prereg_repo is the
    repository whose eval/prereg.yaml unlocks the test split, REPO except
    in tests.
    """
    cfg = get_config(config)
    if cfg.kind != "agent":
        raise ValueError(f"{config} is a baseline; eval.run runs it through eval.baselines")
    if split not in engine.STATUSES_FOR_SPLIT:
        raise ValueError(f"unknown split {split!r}")
    default_id = run_id_for(split, household_id, config, trial)
    if system_prompt != SYSTEM_PROMPT and run_id in (None, default_id):
        raise ValueError(f"a custom system prompt needs its own run_id, not {default_id}, the real run's")
    run_id = run_id or default_id
    done = summary_path(runs_dir, run_id)
    if done.exists():
        return json.loads(done.read_text())
    if not settings.api_key:
        raise MissingKey(f"{run_id}: TAX_AGENT_API_KEY is empty in .env, so no live run can start")
    profile, truth = load_household(data_dir, household_id)
    if truth.get("split") != split:
        raise ValueError(f"{household_id} is in the {truth.get('split')} split, not {split}")
    if split == TEST_SPLIT:
        prereg_commit = locked_commit([config], prereg_commit, repo=prereg_repo)
    prices = dict(prices or load_prices())
    meta = {
        "run_id": run_id, "household_id": household_id, "config": config, "trial": trial,
        "model": cfg.model, "split": split, "planner_model": cfg.planner_model, "prereg_commit": prereg_commit,
        "system_prompt": None if system_prompt == SYSTEM_PROMPT else "custom",
    }

    started = clock()
    reservation = Reservation(ledger, run_id, cfg.model, cfg.reserve_usd, run_cap_usd=settings.run_cap_usd)
    budget = Budget(reservation, cfg.model, prices)
    sim_guard = budget.client(SIMULATOR_GUARD, sim_client, SIMULATOR_MODEL)
    run = Run(
        cfg=cfg, meta=meta, profile=profile, truth=truth, settings=settings, budget=budget,
        sim_guard=sim_guard, split=split, profiles_dir=Path(data_dir) / "profiles",
        client_factory=client_factory, adjust_options=adjust_options, sleep=sleep,
        max_attempts=max_attempts, backoff_s=backoff_s, system_prompt=system_prompt,
    )
    settled = False
    try:
        prompt: str | None = first_message(household_id)
        if cfg.planner_model:
            plan_json = await run.plan(planner_client if planner_client is not None else sim_client)
            prompt = split_first_message(household_id, plan_json) if plan_json is not None else None
        if prompt is not None:
            await run.converse(prompt)
        run.events.append(run.costs_event(clock() - started))
        total = budget.total()
        reservation.settle(max(total, reservation.reserved) if budget.unknown else total)
        settled = True
        write_json(trace_path(runs_dir, run_id), {"meta": meta, "events": run.events})
        summary = build_summary(meta, run.events)
        write_json(done, summary)
        return summary
    finally:
        if not settled:
            reservation.settle(max(budget.total(), reservation.reserved))
