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
   starting: the harness interrupts at once, and since that request may
   have been cut short, the run settles at no less than its reservation,
   which covered the request's bound before the session started.
6. On a 429 that ended the session (ResultMessage.api_error_status 429, an
   assistant message with error rate_limit in a session that ended in error,
   or anthropic.RateLimitError from the simulator), back off and run a fresh
   session, up to MAX_ATTEMPTS, waiting BACKOFF_S and then twice as long
   each time. The planner backs off the same way on its own call.
7. Settle the ledger with the actual total, write the trace and the summary.
   A run the month's spend cap stopped writes no summary (see below).

Spend. A run may spend only what it has reserved in the ledger, so runs
going at once never count on the same headroom. When the run needs more, it
tops its reservation up first (Reservation.cover), against the month cap
and under the ledger's lock, up to RUN_CAP_USD in all. The simulator and
planner guards top up before each of their HTTP attempts, the anthropic
client's own retries included, by that attempt's upper bound, and refuse it
when the ledger won't allow it (see sim.budget).

The agent's calls go through the CLI, so the harness bounds them itself.
Every agent call asks for at most the config's max_output_tokens
(CLAUDE_CODE_MAX_OUTPUT_TOKENS). The CLI streams each call's API events,
since its AssistantMessage usage shows only the stream's opening count (1
output token). A call counts at its prompt (from message_start) and that
output cap, with no cache discount, until its message_delta gives the real
output, and from then on at its list price; each call adds a stream_usage
event to the trace. A message the CLI writes itself (model <synthetic>,
such as its "API Error" text after a 429) is no API call and costs nothing.
Before the agent's next call is sent, the run holds that call's upper bound
as committed spend: a prompt of the last call's prompt and output, the tool
results going back, and framing (or, for an attempt's first call, the
system prompt, the first message, and CLI_PROMPT_TOKENS for the CLI's own
part), at one token per byte and no cache discount, plus the output cap.
While a call streams, what is held is that call's own bound. The
simulator's and planner's guards can't spend what is held. The hold is
checked, with a top-up if needed, at the start of every attempt and in the
PostToolBatch hook, which the CLI awaits after a turn's tool calls and
before it sends their results back to the model. When it can't be covered,
the attempt never starts, or the hook tells the CLI to stop (continue
false), so the call is never sent; agent.wiring_check shows the bundled CLI
honoring both the output cap and the stop. So no agent call that starts an
attempt or follows a tool call can take a run past what it reserved or past
RUN_CAP_USD, or, with runs going at once, take the month past
SPEND_CAP_USD. Holding a whole call's bound costs room: on Opus it is about
$0.16 of output plus $0.05 per 10,000 prompt tokens, so an A-opus run stops
with run_cap once it has spent RUN_CAP_USD less that (see agent.configs).

Three kinds of agent call have no hook before them, so the harness checks
them as early as the stream shows they are coming: the CLI's own follow-up
to a response that ended without a tool call and without ending the turn
(it hit the output cap, for one; message_delta shows it), its resume or
retry after a stream that ended with no message_delta (message_stop shows
it), and its retry of a request that got no HTTP answer (a system api_retry
message with no error status shows it, and that request counts at the
bound held for it, since it may have been billed). In each case the harness
holds the next call's bound at once, and stops and interrupts when it
can't be covered. The CLI sends a follow-up or resume within milliseconds,
and a retry after about half a second, so the call may already be on the
wire; then it can overshoot by what it costs before the interrupt lands,
and it counts at its bound. A retry after an error status (a 429, say)
needs no new hold, since the API bills nothing for the refused request. A
call still streaming when the session ends, a call cut off mid-stream, a
request that got no answer, or an interrupt that may have cut a call short
makes the agent's cost unknown, and the run settles at no less than its
reservation.

The month cap and the run cap stop a run differently. A run-cap stop is a
run_cap harness error, and the run is scored. A stop because the month's
headroom ran out (Reservation.cover reports which limit refused) is a
month_cap stop: the run settles every line at no less than its
reservation, writes its trace but no summary, and raises
ledger.spend.SpendCapExceeded, so the sweep halts and a later sweep runs it
again under the next free ID.

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
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from agent.ask_client import (
    ASK_CLIENT_TOOL,
    MONTH_CAP,
    RATE_LIMITED,
    RUN_CAP,
    STOPPED,
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
from sim.budget import BudgetedClient, BudgetExceeded
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
# Upper bounds, in tokens at one per byte, on what the CLI adds to the agent's prompts. Its own part of
# the first prompt (its system text, the environment block, and the 9 tool definitions) was under 9,000
# bytes on CLI 2.1.314. Each later call adds the turn's framing and a reminder line, and each tool
# result its own framing (or a denial, whose text the PostToolBatch hook does not show).
CLI_PROMPT_TOKENS = 12_000
TURN_TOKENS = 256
RESULT_TOKENS = 256
# The CLI's own message asking the model to resume a response it cut short, under 400 bytes on CLI 2.1.314.
RESUME_TOKENS = 512
# Stop reasons after which the CLI sends no further call on its own; after tool_use the PostToolBatch hook comes first.
ENDS_TURN = ("end_turn", "stop_sequence")
TOOL_USE = "tool_use"
FINAL_USAGE_WAIT_S = 5.0
STREAM_USAGE = "stream_usage"
# The model name on a message the CLI writes itself, such as its "API Error" text after a refused request.
SYNTHETIC_MODEL = "<synthetic>"

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

    def cover(self, need_usd: float) -> str | None:
        """None when the reservation covers need_usd, after a top-up if needed; otherwise the limit that refused.

        RUN_CAP when the run would pass RUN_CAP_USD, MONTH_CAP when the month's
        spend cap left no room for even the shortfall.
        """
        if need_usd <= self.reserved + SLACK_USD:
            return None
        short = ceil_usd(need_usd - self.reserved)
        room = round(self.run_cap_usd - self.reserved, 6)
        if short > room:
            return RUN_CAP
        refused = MONTH_CAP
        for amount in dict.fromkeys((round(min(max(self.step_usd, short), room), 6), short)):
            try:
                self._add(f"{self.ledger_id}.top{len(self.lines) + 1}", amount)
            except SpendCapExceeded:
                refused = MONTH_CAP
                continue
            except RunCapExceeded:
                refused = RUN_CAP
                continue
            return None
        return refused

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
    """A BudgetedClient whose limit, before each attempt, is its share of the run's reservation, topped up for it.

    The anthropic client's own retries are attempts too, so each one tops up
    before it is sent. When the month's spend cap is what kept the
    reservation from covering an attempt, it raises SpendCapExceeded instead
    of BudgetExceeded.
    """

    def __init__(self, client: Any, *, model: str, prices: Mapping[str, Any],
                 room: Callable[[BudgetedClient, float], tuple[float, str | None]]) -> None:
        super().__init__(client, model=model, prices=prices, limit_usd=0.0)
        self.room = room

    def _admit(self, bound: float) -> None:
        self.limit_usd, refused = self.room(self, bound)
        if refused == MONTH_CAP and committed_usd(self) + bound > self.limit_usd:
            raise SpendCapExceeded(
                f"the next call could cost ${bound:.6f}, and the month's spend cap leaves no room to reserve it")
        super()._admit(bound)


def first_prompt_tokens(system_prompt: str, prompt: str) -> int:
    """An upper bound on the prompt of an attempt's first agent call, at one token per byte."""
    return CLI_PROMPT_TOKENS + len(system_prompt.encode()) + len(prompt.encode())


def result_tokens(response: Any) -> int:
    """An upper bound on one tool result's tokens in the next prompt, at one per byte of its JSON, plus framing."""
    return len(json.dumps(response, default=str).encode()) + RESULT_TOKENS


def synthetic(message: AssistantMessage) -> bool:
    """A message the CLI wrote itself, with no API call behind it: its "API Error" text after a 429, for one.

    The CLI names its model SYNTHETIC_MODEL; an error message with no usage
    at all is taken as one too.
    """
    if message.model == SYNTHETIC_MODEL:
        return True
    return message.error is not None and not any(normalize_usage(message.usage).values())


@dataclasses.dataclass
class AgentCall:
    """One agent API call as the CLI streams it: message_start gives its prompt, message_delta its output.

    cut is a call whose stream ended (message_stop) with no message_delta,
    so its output is unknown and it counts at its bound.
    """

    message_id: str
    raw: dict[str, Any]
    held_prompt: int | None
    usage: dict[str, int] = dataclasses.field(default_factory=dict)
    streamed: bool = True
    done: bool = False
    cut: bool = False
    stop_reason: str | None = None
    final: anyio.Event | None = None

    @property
    def prompt(self) -> int:
        return self.usage["input_tokens"] + self.usage["cache_read_tokens"] + self.usage["cache_write_tokens"]


class Budget:
    """One run's spend against its reservation: the agent's calls as they stream, the hold for its next one, and each guarded client.

    model and max_output_tokens are the agent's; eval.run and eval.judge make
    a Budget with no model for guarded clients alone, which never holds
    anything for an agent.
    """

    def __init__(self, reservation: Reservation, model: str | None, prices: Mapping[str, Any], *,
                 max_output_tokens: int = 0) -> None:
        self.reservation = reservation
        self.model = model
        self.prices = dict(prices)
        self.output_cap = int(max_output_tokens)
        self.agent_done_usd = 0.0
        self.agent_usage = normalize_usage({})
        self.clients: dict[str, ReservedClient] = {}
        self.unknown = False
        self.calls: dict[str, AgentCall] = {}
        self.last: AgentCall | None = None
        self.next_prompt: int | None = None
        self.pending_results = 0
        self.sent = False
        self.lost_usd = 0.0

    def client(self, name: str, inner: Any, model: str) -> ReservedClient:
        """A guard on inner whose calls spend from this run's reservation."""
        self.clients[name] = ReservedClient(inner, model=model, prices=self.prices, room=self.room)
        return self.clients[name]

    def spent(self, name: str) -> float:
        guard = self.clients.get(name)
        return guard.spent_usd if guard is not None else 0.0

    # The agent's calls.

    def call_bound(self, prompt_tokens: int) -> float:
        """The most one agent call with this prompt can cost: no cache discount, and the whole output cap."""
        if self.model is None:
            return 0.0
        as_input = cost_usd(self.model, {"input_tokens": prompt_tokens, "output_tokens": self.output_cap},
                            self.prices, prompt_tokens=prompt_tokens)
        as_write = cost_usd(self.model, {"cache_write_tokens": prompt_tokens, "output_tokens": self.output_cap},
                            self.prices, prompt_tokens=prompt_tokens)
        return max(as_input, as_write)

    def call_cost(self, call: AgentCall) -> float:
        """A finished call at list price; one still streaming at its bound."""
        if call.done:
            return cost_usd(self.model, call.usage, self.prices, prompt_tokens=call.prompt)
        return self.call_bound(call.prompt)

    def hold(self) -> float:
        """The bound held for the agent's next call, or 0 when none is pending."""
        return self.call_bound(self.next_prompt) if self.next_prompt is not None else 0.0

    def follow_up_prompt(self) -> int:
        """The next call's prompt: the last one's prompt and output (the cap until it is known), the results, and framing."""
        last = self.last
        if last is None:
            return self.pending_results + TURN_TOKENS
        output = last.usage["output_tokens"] if last.done else self.output_cap
        return last.prompt + output + self.pending_results + TURN_TOKENS

    def start_attempt(self, first_prompt: int) -> None:
        self.calls = {}
        self.last = None
        self.pending_results = 0
        self.sent = False
        self.next_prompt = first_prompt

    def call_started(self, message_id: str, usage: Mapping[str, Any] | None, *, streamed: bool = True) -> AgentCall:
        """message_start (or, with no stream, the call's first AssistantMessage): at its bound until it finishes."""
        raw = dict(usage or {})
        call = AgentCall(message_id=message_id, raw=raw, held_prompt=self.next_prompt, usage=normalize_usage(raw),
                         streamed=streamed)
        self.calls[message_id] = call
        self.last = call
        self.next_prompt = None
        self.pending_results = 0
        self.sent = False
        return call

    def resume_pending(self) -> None:
        """The CLI follows a response it cut short with a call of its own, and no hook precedes it: hold that call."""
        self.next_prompt = self.follow_up_prompt() + RESUME_TOKENS

    def cut_off(self) -> AgentCall | None:
        """message_stop with no message_delta: the stream ended early, so the call's output is unknown, at its bound."""
        call = self.last
        if call is None or not call.streamed or call.done or call.cut:
            return None
        call.cut = True
        self.resume_pending()
        return call

    def no_answer(self) -> tuple[str, float]:
        """The CLI will send an agent request again (system api_retry) after the last one got no answer.

        A request that got no answer may have been billed. One that never
        started streaming counts at the bound held for it, and that bound
        stays held for the retry; one that had started streaming already
        counts at its bound, and its retry sends the same prompt.
        Returns which of the two it was (or "none", when no agent request
        was open) and what was added.
        """
        last = self.last
        if self.next_prompt is not None and (self.sent or last is None or last.done):
            lost = self.call_bound(self.next_prompt)
            self.lost_usd += lost
            self.sent = True
            return "pending", lost
        if last is not None and not last.done:
            last.cut = True
            self.next_prompt = max(self.next_prompt or 0, last.prompt)
            self.sent = True
            return "streaming", 0.0
        return "none", 0.0

    def call_finished(self, usage: Mapping[str, Any] | None, stop_reason: str | None) -> AgentCall | None:
        """message_delta: the streaming call's final usage, which also sizes the hold for any call after it."""
        call = self.last
        if call is None or call.done:
            return None
        merged = {**call.raw, **{k: v for k, v in dict(usage or {}).items() if v is not None}}
        call.usage = normalize_usage(merged)
        call.done = True
        call.stop_reason = stop_reason
        self.next_prompt = None if stop_reason in ENDS_TURN else self.follow_up_prompt()
        if call.final is not None:
            call.final.set()
        return call

    async def wait_final(self, timeout_s: float) -> None:
        """Lets the message reader catch up with the last call's message_delta, which the CLI sent before its hook."""
        call = self.last
        if call is not None and call.streamed and not call.done and not call.cut:
            call.final = call.final or anyio.Event()
            with anyio.move_on_after(timeout_s):
                await call.final.wait()

    def add_results(self, tokens: int) -> None:
        """Tool results on their way back to the model grow the next call's prompt."""
        self.pending_results += tokens
        if self.next_prompt is not None:
            self.next_prompt = self.follow_up_prompt()

    def batch_done(self, tokens: int) -> None:
        """PostToolBatch: every result of the turn is in, and the next call is pending."""
        self.pending_results = max(self.pending_results, tokens)
        self.next_prompt = self.follow_up_prompt()

    def request_sent(self) -> None:
        """The CLI says it is sending a request (system status requesting): the pending call is on the wire."""
        if self.next_prompt is not None:
            self.sent = True

    def request_open(self) -> bool:
        """Whether an agent request may be on the wire: one pending or one still streaming."""
        return self.next_prompt is not None or any(not c.done for c in self.calls.values())

    # Totals.

    def agent_running(self) -> float:
        return self.agent_done_usd + self.lost_usd + sum(self.call_cost(c) for c in self.calls.values())

    def committed(self) -> float:
        return self.agent_running() + self.hold() + sum(committed_usd(c) for c in self.clients.values())

    def total(self) -> float:
        return round(self.agent_running() + sum(c.spent_usd for c in self.clients.values()), 6)

    def make_room(self, extra_usd: float = 0.0) -> str | None:
        """None when the reservation covers everything committed plus extra_usd, after a top-up if needed; otherwise the limit that refused."""
        return self.reservation.cover(self.committed() + extra_usd)

    def room(self, client: BudgetedClient, bound_usd: float) -> tuple[float, str | None]:
        """client's limit for a call that could cost bound_usd (the reservation, less everything else committed), and any refusal."""
        refused = self.make_room(bound_usd)
        others = self.committed() - committed_usd(client)
        return self.reservation.reserved - others + SLACK_USD, refused

    def end_attempt(self, result: ResultMessage | None, *, queried: bool, cut_short: bool = False) -> None:
        """Adds the attempt's agent cost: the larger of the CLI's figure and the streamed calls (open ones at their bound).

        Requests that got no answer count at their bounds on top. The cost is
        unknown when there was no result, when a call never finished
        streaming, when a request got no answer, or when cut_short says an
        interrupt may have cut off a request the stream never showed.
        """
        streamed_cost = self.lost_usd + sum(self.call_cost(c) for c in self.calls.values())
        streamed_usage = normalize_usage({})
        for call in self.calls.values():
            for name, value in call.usage.items():
                streamed_usage[name] += value
        if result is not None and result.total_cost_usd is not None:
            cost = max(float(result.total_cost_usd), streamed_cost)
        elif result is not None and result.usage:
            cost = max(cost_usd(self.model, result.usage, self.prices), streamed_cost)
        else:
            cost = streamed_cost
            self.unknown = self.unknown or queried
        if any(not c.done for c in self.calls.values()) or self.lost_usd or cut_short:
            self.unknown = True
        usage = normalize_usage(result.usage) if result is not None and result.usage else streamed_usage
        self.agent_done_usd = round(self.agent_done_usd + cost, 6)
        for name, value in usage.items():
            self.agent_usage[name] += value
        self.calls = {}
        self.last = None
        self.next_prompt = None
        self.pending_results = 0
        self.sent = False
        self.lost_usd = 0.0


def over_budget(budget: Budget, refused: str, what: str, bound_usd: float) -> str:
    """The stop message when the reservation can't cover what the run has committed."""
    limit = "the run cap" if refused == RUN_CAP else "the month's spend cap"
    return (f"${budget.total():.4f} spent of ${budget.reservation.reserved:.4f} reserved; {what} "
            f"could cost up to ${bound_usd:.4f}, and {limit} leaves no room to reserve it")


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
        self.cut_short = False
        self._messages = 0

    @property
    def limited(self) -> bool:
        """A 429 that ended the session: from the agent's API calls, or from the simulator."""
        if self.control.stop_kind == RATE_LIMITED:
            return True
        if self.result is not None and self.result.api_error_status == 429:
            return True
        return self.rate_limited and (self.result is None or self.result.is_error)

    def check(self, what: str, bound_usd: float | None = None) -> None:
        """Stops the run when the reservation can't cover everything committed, even after a top-up.

        what is the call the check is for, and bound_usd its bound, the hold by default.
        """
        if self.control.stop_kind is None:
            refused = self.budget.make_room()
            if refused:
                bound = self.budget.hold() if bound_usd is None else bound_usd
                self.control.stop(refused, over_budget(self.budget, refused, what, bound))

    def stop_output(self) -> dict[str, Any]:
        """Hook output that tells the CLI to stop instead of sending anything more to the model."""
        self.budget.next_prompt = None
        return {"continue_": False, "stopReason": STOPPED.format(kind=self.control.stop_kind)}

    # Hooks.

    async def gate(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        """PreToolUse on every tool: nothing runs before the init check passes, after a stop, or outside the allowed set.

        After a stop, the denial also tells the CLI not to continue, so it
        never sends the denial back to the model.
        """
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
        out: dict[str, Any] = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
        if self.control.stop_kind is not None:
            out.update(self.stop_output())
        return out

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
        self.budget.add_results(result_tokens(input_data.get("tool_response")))
        return {}

    async def post_tool_failure(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        self.budget.add_results(result_tokens(input_data.get("error")))
        return {}

    async def check_next_call(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        """PostToolBatch: the CLI awaits it after a turn's tool calls and before it sends their results to the model.

        The next call is held at its bound (the last call's prompt and output,
        every result in the batch, and the output cap) and must fit in the
        reservation, after a top-up if needed; otherwise the CLI is told to
        stop, and the call is never sent.
        """
        await self.budget.wait_final(FINAL_USAGE_WAIT_S)
        responses = [c.get("tool_response") for c in input_data.get("tool_calls") or [] if isinstance(c, Mapping)]
        self.budget.batch_done(sum(result_tokens(r) for r in responses))
        self.check("the agent's next call")
        return self.stop_output() if self.control.stop_kind is not None else {}

    def hooks(self) -> dict[str, list[HookMatcher]]:
        return {
            "PreToolUse": [
                HookMatcher(matcher=None, hooks=[self.gate]),
                HookMatcher(matcher=RECORD_FACT_TOOL, hooks=[self.check_evidence]),
            ],
            "PostToolUse": [HookMatcher(matcher=None, hooks=[self.post_tool_use])],
            "PostToolUseFailure": [HookMatcher(matcher=None, hooks=[self.post_tool_failure])],
            "PostToolBatch": [HookMatcher(matcher=None, hooks=[self.check_next_call])],
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

    def usage_event(self, call: AgentCall, outcome: str) -> dict[str, Any]:
        """A stream_usage trace event: one agent call's usage, cost, and the prompt held for it before it was sent."""
        return {
            "type": STREAM_USAGE,
            "message_id": call.message_id,
            "usage": dict(call.usage),
            "stop_reason": call.stop_reason,
            "cost_usd": self.budget.call_cost(call),
            "held_prompt_tokens": call.held_prompt,
            "outcome": outcome,
        }

    def on_stream(self, message: StreamEvent) -> None:
        """Each agent call's message_start, message_delta, and message_stop, where the real usage is."""
        event = message.event or {}
        kind = event.get("type")
        if kind == "message_start":
            data = event.get("message") or {}
            call = self.budget.call_started(str(data.get("id") or f"call-{len(self.budget.calls) + 1}"), data.get("usage"))
            self.check("an agent call the CLI started", self.budget.call_cost(call))
        elif kind == "message_delta":
            stop_reason = (event.get("delta") or {}).get("stop_reason")
            call = self.budget.call_finished(event.get("usage"), stop_reason)
            if call is None:
                return
            self.events.append(self.usage_event(call, "finished"))
            if stop_reason not in ENDS_TURN and stop_reason != TOOL_USE:
                # The CLI follows this response with a call of its own, which no hook precedes.
                self.budget.resume_pending()
                self.check("the CLI's next call, which no hook precedes,")
        elif kind == "message_stop":
            call = self.budget.cut_off()
            if call is None:
                return
            # The stream ended with no message_delta: the CLI resumes or retries the call, and no hook precedes it.
            self.events.append(self.usage_event(call, "cut_off"))
            self.check("the CLI's call after a cut-off stream, which no hook precedes,")

    def on_system(self, message: SystemMessage) -> None:
        """The CLI's status and api_retry messages, which say when an agent request goes out and when one is sent again."""
        data = message.data or {}
        if message.subtype == "status" and data.get("status") == "requesting":
            self.budget.request_sent()
        elif message.subtype == "api_retry" and data.get("error_status") is None:
            # No HTTP answer (a timeout or a lost connection), so it may have been billed; an error status bills nothing.
            held = self.budget.next_prompt
            which, lost = self.budget.no_answer()
            self.events.append({
                "type": STREAM_USAGE, "message_id": None, "usage": normalize_usage({}), "stop_reason": None,
                "cost_usd": lost, "held_prompt_tokens": held, "outcome": f"no_answer_{which}",
            })
            self.check("the CLI's retry of an agent call that got no answer, which no hook precedes,")

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
        if message.message_id and message.message_id not in self.budget.calls and not synthetic(message):
            # No message_start came for this call, so count it from here, at its bound. A message the CLI wrote
            # itself (its "API Error" text after a 429, say) is no API call and costs nothing.
            call = self.budget.call_started(message.message_id, message.usage, streamed=False)
            self.check("an agent call the CLI started", self.budget.call_cost(call))

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
            "terminal_reason": message.terminal_reason,
            "result": message.result,
        })
        if message.api_error_status is not None and message.api_error_status != 429:
            self.control.error(API_ERROR, f"the agent's API call failed with status {message.api_error_status}")
        if message.subtype == "error_max_budget_usd" or message.terminal_reason == "budget_exhausted":
            self.control.stop(RUN_CAP, f"the CLI's own spend budget ended the session ({message.subtype}, "
                                       f"{message.terminal_reason}) at ${float(message.total_cost_usd or 0):.4f}")

    def handle(self, message: Any) -> None:
        if isinstance(message, StreamEvent):
            if message.parent_tool_use_id is None:
                self.on_stream(message)
        elif isinstance(message, SystemMessage) and message.subtype == "init":
            self.on_init(message.data)
        elif isinstance(message, SystemMessage):
            self.on_system(message)
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
                if self.control.stop_kind is not None and not interrupted and self.result is None:
                    interrupted = True
                    self.cut_short = self.budget.request_open()
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
        except SpendCapExceeded as e:
            control.error(MONTH_CAP, f"planner call not sent: {e}")
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
            max_output_tokens=self.cfg.max_output_tokens,
        )
        return self.adjust_options(options) if self.adjust_options else options

    async def attempt(self, prompt: str) -> Session:
        self.attempts += 1
        self.events.append({"type": "attempt", "n": self.attempts})
        control = RunControl(self.events)
        book = EvidenceBook(prior_year_summary=self.profile["prior_year_summary"])
        simulator = ClientSimulator(self.truth, self.sim_guard, model=SIMULATOR_MODEL, specs=self.specs)
        channel = ClientChannel(simulator, book, ProbeSchedule.from_truth(self.truth), control)
        session = Session(events=self.events, control=control, budget=self.budget, channel=channel, book=book)
        self.budget.start_attempt(first_prompt_tokens(self.system_prompt, prompt))
        session.check("the attempt's first agent call")
        if control.stop_kind is not None:
            self.budget.end_attempt(None, queried=False)
            return session
        # A neutral name: the CLI shows the agent its working folder, which must not name the run.
        with tempfile.TemporaryDirectory(prefix="intake-") as tmp:
            try:
                async with self.client_factory(options=self.options(Path(tmp), session, channel)) as client:
                    await session.converse(client, prompt)
            except Exception as e:
                control.error(EXCEPTION, f"{type(e).__name__}: {e}")
            finally:
                stopped_at_hook = session.result is not None and session.result.terminal_reason == "hook_stopped"
                self.budget.end_attempt(session.result, queried=session.queried,
                                        cut_short=session.cut_short and not stopped_at_hook)
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

    Raises SpendCapExceeded, after settling and writing the trace but no
    summary, when the month's spend cap stopped the run.
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
    budget = Budget(reservation, cfg.model, prices, max_output_tokens=cfg.max_output_tokens)
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
        month = [e for e in run.events if e.get("type") == "harness_error" and e.get("kind") == MONTH_CAP]
        if month:
            reservation.settle(max(budget.total(), reservation.reserved))
            settled = True
            write_json(trace_path(runs_dir, run_id), {"meta": meta, "events": run.events})
            raise SpendCapExceeded(f"{run_id}: {month[0]['message']}")
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
