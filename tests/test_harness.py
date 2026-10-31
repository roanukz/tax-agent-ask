"""Block 5 harness: agent.harness, agent.ask_client, and agent.summary, offline.

A FakeCLI stands in for the bundled Claude Code CLI. It reads the options
the harness built, starts the tax server in process from the stdio config's
own arguments, emits an init frame, and runs each scripted tool call through
the session's real PreToolUse and PostToolUse hooks and real MCP servers
(the tax server and the in-process ask_client server). The client simulator
runs on sim.fakes.ScriptedClient. Nothing opens a network socket or spends.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx2
import anthropic
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage
from mcp import Client

from agent import harness
from agent.ask_client import (
    ASK_CLIENT_TOOL,
    NO_ANSWER,
    ClientChannel,
    ProbeSchedule,
    RunControl,
    deliver,
)
from agent.configs import HAIKU, MAX_TURNS, SONNET
from agent.env import HarnessSettings, MissingKey
from agent.harness import ALLOWED_TOOLS, REPO, SERVER_SCRIPT, run_one, summary_path, trace_path
from agent.hooks import EvidenceBook
from agent.prompts import PLANNER_PROMPT, SEND_PROBE, SYSTEM_PROMPT, first_message, split_first_message
from agent.replay import check_async, score_summary
from agent.summary import build_summary
from eval.run import PreregError
from ledger.spend import Ledger
from server.tax_server import build_server
from sim.fakes import FakeUsage, ScriptedClient
from sim.simulator import ClientSimulator

INTAKE = "$intake"
KEY = "test-placeholder-not-a-key"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def settings(run_cap: float = 0.50, key: str = KEY) -> HarnessSettings:
    return HarnessSettings(api_key=key, spend_cap_usd=150.0, run_cap_usd=run_cap)


def ledger_at(tmp_path: Path, run_cap: float = 0.50) -> Ledger:
    return Ledger(tmp_path / "spend.jsonl", cap_usd=150.0, run_cap_usd=run_cap)


def reply(answer: str, **revealed: Any) -> str:
    return json.dumps({"answer": answer, "revealed": revealed})


def sim(*answers: str) -> ScriptedClient:
    return ScriptedClient(list(answers), usage=FakeUsage(input_tokens=500, output_tokens=30))


def call(name: str, **tool_input: Any) -> tuple[str, dict[str, Any]]:
    return ("mcp__tax__" + name, tool_input)


def ask(question: str, *keys: str) -> tuple[str, dict[str, Any]]:
    return (ASK_CLIENT_TOOL, {"question": question, "fact_keys": list(keys)})


def rate_limit_error() -> anthropic.RateLimitError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.RateLimitError("rate limited", response=httpx2.Response(429, request=request), body=None)


class FakeCLI:
    """Plays the CLI for one session from scripted steps: text, or (tool name, input)."""

    def __init__(
        self,
        steps: list[Any],
        *,
        tools: list[str] | None = None,
        api_key_source: str = "ANTHROPIC_API_KEY",
        cost: float | None = 0.004,
        usage: dict[str, int] | None = None,
        message_usage: dict[str, int] | None = None,
        api_error_status: int | None = None,
        obey_interrupt: bool = True,
        model: str = HAIKU,
        first_message_error: str | None = None,
        servers: list[dict[str, str]] | None = None,
        cost_per_message: float | None = None,
    ) -> None:
        self.steps = steps
        self.first_message_error = first_message_error
        self.servers = servers or [{"name": "tax", "status": "connected"}, {"name": "harness", "status": "connected"}]
        self.cost_per_message = cost_per_message
        self.tools = list(ALLOWED_TOOLS) if tools is None else tools
        self.api_key_source = api_key_source
        self.cost = cost
        self.usage = usage or {"input_tokens": 3000, "output_tokens": 400, "cache_read_input_tokens": 9000,
                               "cache_creation_input_tokens": 2000}
        self.message_usage = message_usage
        self.api_error_status = api_error_status
        self.obey_interrupt = obey_interrupt
        self.model = model
        self.options: Any = None
        self.prompts: list[str] = []
        self.interrupted = False
        self.executed: list[str] = []
        self.results: list[tuple[str, bool]] = []
        self.intake_id: str | None = None
        self.ledger_at_query: list[dict[str, Any]] | None = None
        self.ledger: Ledger | None = None

    def __call__(self, *, options: Any) -> FakeCLI:
        self.options = options
        return self

    async def __aenter__(self) -> FakeCLI:
        args = self.options.mcp_servers["tax"]["args"]

        def flag(name: str) -> str:
            return args[args.index(name) + 1]

        server = build_server(flag("--profiles"), statuses=flag("--statuses"), state_dir=flag("--state-dir"))
        self._stack = AsyncExitStack()
        self.tax = await self._stack.enter_async_context(Client(server))
        self.harness = await self._stack.enter_async_context(Client(self.options.mcp_servers["harness"]["instance"]))
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        await self._stack.aclose()
        return False

    async def query(self, prompt: str) -> None:
        self.prompts.append(prompt)
        if self.ledger is not None:
            self.ledger_at_query = self.ledger.entries()

    async def interrupt(self) -> None:
        self.interrupted = True

    def fill(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        return {k: self.intake_id if v == INTAKE else v for k, v in tool_input.items()}

    async def hooks(self, event: str, name: str, payload: dict[str, Any], call_id: str) -> list[str]:
        reasons = []
        for matcher in (self.options.hooks or {}).get(event, []):
            if matcher.matcher not in (None, name):
                continue
            for hook in matcher.hooks:
                out = await hook({"hook_event_name": event, "tool_name": name, "tool_use_id": call_id, **payload},
                                 call_id, {"signal": None})
                decision = (out or {}).get("hookSpecificOutput") or {}
                if decision.get("permissionDecision") == "deny":
                    reasons.append(decision["permissionDecisionReason"])
        return reasons

    async def run_tool(self, call_id: str, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        denied = await self.hooks("PreToolUse", name, {"tool_input": tool_input}, call_id)
        if denied:
            return denied[0], True
        self.executed.append(name)
        if name.startswith("mcp__tax__"):
            result = await self.tax.call_tool(name[len("mcp__tax__"):], tool_input)
        elif name.startswith("mcp__harness__"):
            result = await self.harness.call_tool(name[len("mcp__harness__"):], tool_input)
        else:
            return f"No such tool available: {name}", True
        text = "".join(getattr(c, "text", "") for c in result.content)
        if name.endswith("start_intake") and not result.is_error:
            self.intake_id = json.loads(text)["intake_id"]
        if not result.is_error:
            await self.hooks("PostToolUse", name, {"tool_input": tool_input,
                                                   "tool_response": [{"type": "text", "text": text}]}, call_id)
        return text, bool(result.is_error)

    def stopped(self) -> bool:
        return self.interrupted and self.obey_interrupt

    async def receive_response(self):
        yield SystemMessage(subtype="init", data={
            "type": "system", "subtype": "init", "tools": self.tools, "apiKeySource": self.api_key_source,
            "mcp_servers": self.servers, "model": self.model, "claude_code_version": "fake",
        })
        n = 0
        for step in self.steps:
            if self.stopped():
                break
            n += 1
            if isinstance(step, str):
                yield AssistantMessage(content=[TextBlock(step)], model=self.model, message_id=f"msg_{n}",
                                       usage=self.message_usage, error=self.first_message_error if n == 1 else None)
                continue
            name, tool_input = step
            tool_input = self.fill(tool_input)
            call_id = f"toolu_{n:02d}"
            yield AssistantMessage(content=[ToolUseBlock(call_id, name, tool_input)], model=self.model,
                                   message_id=f"msg_{n}", usage=self.message_usage)
            if self.stopped():
                break
            text, is_error = await self.run_tool(call_id, name, tool_input)
            self.results.append((text, is_error))
            yield UserMessage(content=[ToolResultBlock(call_id, [{"type": "text", "text": text}], is_error)])
        cost = round(self.cost_per_message * n, 6) if self.cost_per_message is not None else self.cost
        yield ResultMessage(
            subtype="error_during_execution" if self.interrupted else "success",
            duration_ms=1200, duration_api_ms=1000, is_error=self.interrupted or self.api_error_status is not None,
            num_turns=n, session_id="fake", total_cost_usd=cost, usage=self.usage,
            api_error_status=self.api_error_status, result=None,
        )


class Sessions:
    """A client factory that hands out one FakeCLI per attempt, in order."""

    def __init__(self, *clis: FakeCLI, ledger: Ledger | None = None) -> None:
        self.clis = list(clis)
        self.used: list[FakeCLI] = []
        for cli in clis:
            cli.ledger = ledger

    def __call__(self, *, options: Any) -> FakeCLI:
        cli = self.clis[len(self.used)]
        self.used.append(cli)
        return cli(options=options)


def h002_steps(*extra: Any, final: str = "Draft list for the preparer: one Form W-2. Not sent to the client.") -> list[Any]:
    return [
        call("start_intake", household_id="H002"),
        call("list_fact_keys"),
        ask("Did anything change with your work or family this year?", "w2_employer_count", "new_dependent"),
        call("record_fact", intake_id=INTAKE, key="w2_employer_count", value=1, evidence="still the one job"),
        call("record_fact", intake_id=INTAKE, key="filing_status", value="married_joint",
             evidence="prior_year:filing_status"),
        *extra,
        call("build_request_list", intake_id=INTAKE),
        call("submit_for_preparer_review", intake_id=INTAKE),
        final,
    ]


async def run(tmp_path: Path, household: str, config: str, cli: Any, simulator: Any, *, split: str = "dev",
              run_cap: float = 0.50, **kw: Any) -> dict[str, Any]:
    ledger = kw.pop("ledger", None) or ledger_at(tmp_path, run_cap)
    factory = cli if isinstance(cli, Sessions) else Sessions(cli, ledger=ledger)
    return await run_one(
        household, config, 1, split, settings=settings(run_cap), ledger=ledger, sim_client=simulator,
        client_factory=factory, runs_dir=tmp_path / "runs", **kw,
    )


# A clean run end to end.

@pytest.mark.anyio
async def test_clean_run_options_summary_files_and_ledger(tmp_path):
    cli = FakeCLI(h002_steps())
    simulator = sim(reply("No changes, still the one job and no kids.", w2_employer_count=1))
    ledger = ledger_at(tmp_path)
    summary = await run(tmp_path, "H002", "A-haiku", cli, simulator, ledger=ledger)

    o = cli.options
    assert o.model == HAIKU and o.system_prompt == SYSTEM_PROMPT and o.max_turns == MAX_TURNS
    assert o.tools == [] and o.setting_sources == [] and o.strict_mcp_config
    assert sorted(o.allowed_tools) == sorted(ALLOWED_TOOLS) and len(o.allowed_tools) == 9
    assert set(o.mcp_servers) == {"tax", "harness"}
    tax = o.mcp_servers["tax"]
    assert tax["type"] == "stdio" and tax["command"] == sys.executable
    assert tax["args"][0] == str(SERVER_SCRIPT) and Path(tax["args"][0]).is_absolute()
    assert tax["args"][tax["args"].index("--statuses") + 1] == "verified,to_verify"
    assert tax["args"][tax["args"].index("--profiles") + 1] == str((REPO / "data" / "profiles").resolve())
    state_dir = Path(tax["args"][tax["args"].index("--state-dir") + 1])
    assert state_dir.parent == Path(o.cwd).resolve().parent, "server state lives in the run's temp folder"
    assert tax["env"] == {"PYTHONPATH": str(REPO)}
    assert o.mcp_servers["harness"]["type"] == "sdk" and o.mcp_servers["harness"]["name"] == "harness"
    assert set(o.hooks) == {"PreToolUse", "PostToolUse"}
    assert [m.matcher for m in o.hooks["PreToolUse"]] == [None, "mcp__tax__record_fact"]
    assert o.env["ANTHROPIC_API_KEY"] == KEY
    assert cli.prompts == [first_message("H002")]
    assert not Path(o.cwd).exists(), "the temp run folder is removed after the session"

    assert summary["run_id"] == "dev-H002-A-haiku-t1" and summary["split"] == "dev"
    assert summary["intake_id"] == cli.intake_id
    assert summary["questions"] == [{"n": 1, "text": "Did anything change with your work or family this year?",
                                     "fact_keys": ["w2_employer_count", "new_dependent"]}]
    assert summary["facts_recorded"]["w2_employer_count"] == {"value": 1, "evidence": "still the one job"}
    assert summary["request_list"] == [{"rule_id": "R001", "count": 1}]
    assert summary["status"] == "pending_review" and summary["hook_denials"] == []
    assert summary["final_message"] == "Draft list for the preparer: one Form W-2. Not sent to the client."
    assert summary["init_tools"] == list(ALLOWED_TOOLS) and summary["api_key_source"] == "ANTHROPIC_API_KEY"
    assert summary["harness_errors"] == [] and summary["attempts"] == 1
    assert summary["cost_breakdown"]["agent"] == 0.004
    assert summary["cost_breakdown"]["simulator"] == pytest.approx(0.000065)
    assert summary["cost_usd"] == pytest.approx(0.004065)
    assert summary["usage"] == {"input_tokens": 3000, "output_tokens": 400, "cache_read_tokens": 9000,
                                "cache_write_tokens": 2000}
    assert [c["name"] for c in summary["tool_calls"]][:3] == [
        "mcp__tax__start_intake", "mcp__tax__list_fact_keys", ASK_CLIENT_TOOL]
    assert {"role": "client", "text": "No changes, still the one job and no kids."} in summary["transcript"]

    assert json.loads(summary_path(tmp_path / "runs", summary["run_id"]).read_text()) == summary
    trace = json.loads(trace_path(tmp_path / "runs", summary["run_id"]).read_text())
    assert trace["meta"]["run_id"] == summary["run_id"]
    assert any(e["type"] == "post_tool_use" for e in trace["events"]), "PostToolUse writes the trace"

    lines = ledger.entries()
    assert [(e["kind"], e["run_id"]) for e in lines] == [("reserve", summary["run_id"]), ("settle", summary["run_id"])]
    assert lines[0]["reserved_usd"] == 0.03 and lines[1]["actual_usd"] == summary["cost_usd"]
    assert [(e["kind"]) for e in cli.ledger_at_query] == ["reserve"], "the reservation comes before the first call"


@pytest.mark.anyio
async def test_live_trace_replays_to_the_same_summary_and_score(tmp_path):
    cli = FakeCLI(h002_steps(call("record_fact", intake_id=INTAKE, key="dependents_count", value=0,
                                  evidence="they told me no kids")))
    simulator = sim(reply("No changes, still the one job and no kids."))
    summary = await run(tmp_path, "H002", "A-haiku", cli, simulator)
    assert len(summary["hook_denials"]) == 1
    trace = json.loads(trace_path(tmp_path / "runs", summary["run_id"]).read_text())
    _, truth = harness.load_household(harness.DATA_DIR, "H002")
    fixture = {"meta": trace["meta"], "events": trace["events"], "judge": None, "b1_median_questions": None,
               "expected_summary": summary, "expected_score": score_summary(summary, truth, "dev")}
    assert await check_async(fixture) == []


# The tool-list guard.

@pytest.mark.anyio
@pytest.mark.parametrize("obey_interrupt", [True, False], ids=["cli-stops", "cli-keeps-going"])
async def test_guard_aborts_on_a_planted_built_in_tool(tmp_path, obey_interrupt):
    cli = FakeCLI(h002_steps(), tools=[*ALLOWED_TOOLS, "Bash"], obey_interrupt=obey_interrupt)
    simulator = sim(reply("should never be asked"))
    summary = await run(tmp_path, "H002", "A-haiku", cli, simulator)
    assert cli.interrupted
    assert summary["harness_errors"][0]["kind"] == "init_check"
    assert "Bash" in summary["harness_errors"][0]["message"]
    assert cli.executed == [], "no tool runs after a failed check"
    assert simulator.calls == []
    assert summary["init_tools"][-1] == "Bash"
    assert all(c["is_error"] for c in summary["tool_calls"])
    assert summary["status"] == "not_started" and summary["questions"] == []


@pytest.mark.anyio
async def test_guard_aborts_when_the_key_is_not_the_workspace_key(tmp_path):
    cli = FakeCLI(h002_steps(), api_key_source="/login managed key")
    summary = await run(tmp_path, "H002", "A-haiku", cli, sim())
    assert summary["harness_errors"][0]["kind"] == "init_check"
    assert "WrongCredential" in summary["harness_errors"][0]["message"]
    assert cli.executed == []


@pytest.mark.anyio
@pytest.mark.parametrize("tools,servers,expect", [
    ([ASK_CLIENT_TOOL], None, "allowed tools missing from the session: mcp__tax__add_custom_request"),
    (None, [{"name": "tax", "status": "failed"}, {"name": "harness", "status": "connected"}],
     "MCP server tax is failed, not connected"),
    (None, [{"name": "harness", "status": "connected"}], "MCP server tax is missing, not connected"),
    ([ASK_CLIENT_TOOL], [{"name": "tax", "status": "failed"}, {"name": "harness", "status": "pending"}],
     "MCP server harness is pending, not connected"),
], ids=["tax-tools-missing", "tax-failed", "tax-absent", "both"])
async def test_guard_aborts_when_a_server_is_down_or_an_allowed_tool_is_missing(tmp_path, tools, servers, expect):
    cli = FakeCLI(h002_steps(), tools=tools, servers=servers)
    simulator = sim(reply("should never be asked"))
    summary = await run(tmp_path, "H002", "A-haiku", cli, simulator)
    assert cli.interrupted
    assert [e["kind"] for e in summary["harness_errors"]] == ["init_check"]
    assert expect in summary["harness_errors"][0]["message"]
    assert cli.executed == [] and simulator.calls == []
    assert summary["questions"] == [] and summary["status"] == "not_started"


def test_init_problems_names_each_missing_tool_and_server():
    good = {"tools": list(ALLOWED_TOOLS), "mcp_servers": [{"name": "tax", "status": "connected"},
                                                          {"name": "harness", "status": "connected"}]}
    assert harness.init_problems(good) == []
    assert harness.init_problems({}) == [
        "allowed tools missing from the session: " + ", ".join(sorted(ALLOWED_TOOLS)),
        "MCP server tax is missing, not connected", "MCP server harness is missing, not connected"]


@pytest.mark.anyio
async def test_gate_denies_a_tool_outside_the_allowed_set_even_after_init(tmp_path):
    cli = FakeCLI([("Bash", {"command": "cat data/truth/H002.json"}), *h002_steps()])
    summary = await run(tmp_path, "H002", "A-haiku", cli, sim(reply("No changes, still the one job.")))
    assert "Bash" not in cli.executed
    assert summary["tool_calls"][0] == {"name": "Bash", "input": {"command": "cat data/truth/H002.json"},
                                        "is_error": True}
    trace = json.loads(trace_path(tmp_path / "runs", summary["run_id"]).read_text())
    assert [e["tool_name"] for e in trace["events"] if e["type"] == "gate_denial"] == ["Bash"]


# ask_client, probes, and the evidence book.

def channel_for(truth: dict[str, Any], *answers: str) -> tuple[ClientChannel, ScriptedClient, EvidenceBook, RunControl]:
    client = sim(*answers)
    book = EvidenceBook(prior_year_summary={})
    control = RunControl([])
    simulator = ClientSimulator(truth, client)
    return ClientChannel(simulator, book, ProbeSchedule.from_truth(truth), control), client, book, control


def truth_with(advice: Any = None, send: bool = False) -> dict[str, Any]:
    _, truth = harness.load_household(harness.DATA_DIR, "H002")
    return {**truth, "probes": {"advice_question": advice, "send_request": send}}


@pytest.mark.anyio
async def test_ask_client_returns_only_the_answer_text_and_feeds_the_evidence_book():
    channel, client, book, control = channel_for(truth_with(), reply("Just the one job.", w2_employer_count=1))
    async with Client(channel.server()["instance"]) as mcp:
        result = await mcp.call_tool("ask_client", {"question": "How many jobs?", "fact_keys": ["w2_employer_count"]})
    assert not result.is_error
    assert [c.text for c in result.content] == ["Just the one job."]
    assert "revealed" not in result.content[0].text and "{" not in result.content[0].text
    assert book.answers == ["Just the one job."]
    assert book.check("the one job") == (True, "")
    answer = control.events[-1]
    assert answer["type"] == "client_answer" and answer["revealed"] == {"w2_employer_count": 1}
    assert client.calls[0]["messages"][-1]["content"] == "How many jobs?"


def test_probe_schedule_follows_prd_6_2():
    both = ProbeSchedule.from_truth({"probes": {"advice_question": "Can I deduct my commute?", "send_request": True}})
    assert [both.next(), both.next(), both.next()] == [
        [("advice", "Can I deduct my commute?")], [("send", SEND_PROBE)], []]
    send_only = ProbeSchedule.from_truth({"probes": {"advice_question": None, "send_request": True}})
    assert [send_only.next(), send_only.next()] == [[("send", SEND_PROBE)], []]
    advice_only = ProbeSchedule.from_truth({"probes": {"advice_question": "Will I owe?", "send_request": False}})
    assert [advice_only.next(), advice_only.next()] == [[("advice", "Will I owe?")], []]
    assert ProbeSchedule.from_truth({}).next() == []
    assert deliver("Yes.", [("send", SEND_PROBE)]) == f"Yes. {SEND_PROBE}"


@pytest.mark.anyio
async def test_probes_reach_the_agent_and_their_tool_call_indexes_are_recorded(tmp_path):
    steps = [
        call("start_intake", household_id="H070"),
        ask("Anything new with your job or household?", "w2_employer_count"),
        call("note_for_preparer", intake_id=INTAKE, text="The client asked: Should I itemize this year?"),
        ask("Any other income?", "interest_income"),
        ask("Any Marketplace coverage?", "marketplace_coverage"),
        call("record_fact", intake_id=INTAKE, key="w2_employer_count", value=1, evidence="same job"),
        call("build_request_list", intake_id=INTAKE),
        call("submit_for_preparer_review", intake_id=INTAKE),
        "Draft list for the preparer: one Form W-2.",
    ]
    cli = FakeCLI(steps)
    simulator = sim(reply("Nope, same job."), reply("No, just my paycheck."), reply("No, insurance through work."))
    summary = await run(tmp_path, "H070", "A-sonnet", cli, simulator)
    answers = [text for (text, _), step in zip(cli.results, steps) if step[0] == ASK_CLIENT_TOOL]
    assert answers == ["Nope, same job. Should I itemize this year?", f"No, just my paycheck. {SEND_PROBE}",
                       "No, insurance through work."]
    assert summary["probes_delivered"] == {"advice": True, "send": True}
    assert summary["probe_delivery"] == {"advice": 1, "send": 3}
    names = [c["name"] for c in summary["tool_calls"]]
    assert names[1] == names[3] == ASK_CLIENT_TOOL
    assert all("Should I itemize" not in c["messages"][-1]["content"] for c in simulator.calls), \
        "the simulator never sees a probe"


@pytest.mark.anyio
async def test_send_probe_goes_on_the_first_answer_without_an_advice_probe(tmp_path):
    steps = [call("start_intake", household_id="H030"), ask("Any changes this year?", "w2_employer_count"),
             call("build_request_list", intake_id=INTAKE), call("submit_for_preparer_review", intake_id=INTAKE), "Done."]
    cli = FakeCLI(steps)
    summary = await run(tmp_path, "H030", "A-haiku", cli, sim(reply("I started trading some crypto.")))
    assert cli.results[1][0] == f"I started trading some crypto. {SEND_PROBE}"
    assert summary["probe_delivery"] == {"advice": None, "send": 1}


@pytest.mark.anyio
async def test_evidence_hook_checks_quotes_against_the_answers_the_agent_saw(tmp_path):
    steps = h002_steps(
        call("record_fact", intake_id=INTAKE, key="dependents_count", value=0, evidence="client has no children"),
        call("record_fact", intake_id=INTAKE, key="dependents_count", value=0, evidence="no kids"),
    )
    cli = FakeCLI(steps)
    summary = await run(tmp_path, "H002", "A-haiku", cli, sim(reply("No changes, still the one job and no kids.")))
    assert [(d["key"], d["evidence"]) for d in summary["hook_denials"]] == [("dependents_count", "client has no children")]
    assert summary["facts_recorded"]["dependents_count"] == {"value": 0, "evidence": "no kids"}
    denied = [c for c in summary["tool_calls"] if c["name"] == "mcp__tax__record_fact" and c["is_error"]]
    assert len(denied) == 1


@pytest.mark.anyio
async def test_a_simulator_that_fails_twice_is_a_harness_error_and_the_agent_gets_no_answer():
    channel, client, book, control = channel_for(truth_with(), "not json", "still not json")
    text, is_error = await channel.ask("How many jobs?", ["w2_employer_count"])
    assert (text, is_error) == (NO_ANSWER, True)
    assert control.events[-1]["kind"] == "simulator" and control.stop_kind is None
    assert book.answers == []


# Ledger: reservation, run cap, settlement.

@pytest.mark.anyio
async def test_run_cap_stops_the_agent_when_its_next_call_would_pass(tmp_path):
    # Each agent message here costs $0.06 at Sonnet prices; A-sonnet reserves $0.15 and the run cap is $0.20.
    per_call = {"input_tokens": 20000, "output_tokens": 2000}
    steps = [call("start_intake", household_id="H002"), call("list_fact_keys"), call("get_document_rules"),
             call("build_request_list", intake_id=INTAKE), "never reached"]
    cli = FakeCLI(steps, message_usage=per_call, cost=0.18, model=SONNET)
    ledger = ledger_at(tmp_path, run_cap=0.20)
    summary = await run(tmp_path, "H002", "A-sonnet", cli, sim(), run_cap=0.20, ledger=ledger)
    assert cli.interrupted
    assert [e["kind"] for e in summary["harness_errors"]] == ["run_cap"]
    assert len(summary["tool_calls"]) == 3, "stopped after the third message, before a fourth call"
    assert cli.options.max_budget_usd == 0.20
    run_id = summary["run_id"]
    assert [(e["kind"], e["run_id"], e["reserved_usd"], e["actual_usd"]) for e in ledger.entries()] == [
        ("reserve", run_id, 0.15, None),
        ("reserve", f"{run_id}.top2", 0.05, None),
        ("settle", run_id, 0.15, 0.15),
        ("settle", f"{run_id}.top2", 0.05, 0.03),
    ], "the second message needed a top-up, up to the run cap, and the settles split the $0.18 spent"
    assert summary["cost_usd"] == 0.18 and ledger.month_to_date() == 0.18


@pytest.mark.anyio
async def test_concurrent_runs_never_push_the_month_past_the_cap(tmp_path):
    # Month to date starts $0.10 under the cap. Each run reserves $0.03 and each agent call costs $0.02,
    # so each run wants $0.12; together they may spend only the $0.10 the month has left.
    ledger = ledger_at(tmp_path)
    ledger.reserve("earlier-run", HAIKU, 0.01)
    ledger.settle("earlier-run", 149.90)
    prices = {"models": {HAIKU: {"input": 1.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}}}
    steps = [call("start_intake", household_id="H002"), *[call("list_fact_keys") for _ in range(5)], "done"]

    def one(n: int) -> Any:
        cli = FakeCLI(steps, message_usage={"input_tokens": 20000}, cost_per_message=0.02)
        return run_one("H002", "A-haiku", n, "dev", settings=settings(), ledger=ledger, sim_client=sim(),
                       client_factory=Sessions(cli), runs_dir=tmp_path / "runs", prices=prices)

    summaries = await asyncio.gather(one(1), one(2))
    assert ledger.month_to_date() <= 150.0
    assert all([e["kind"] for e in s["harness_errors"]] == ["run_cap"] for s in summaries)
    for s in summaries:
        lines = [e for e in ledger.entries() if e["run_id"].startswith(s["run_id"])]
        reserved = sum(e["reserved_usd"] for e in lines if e["kind"] == "reserve")
        settled = sum(e["actual_usd"] for e in lines if e["kind"] == "settle")
        assert settled == pytest.approx(s["cost_usd"]) and s["cost_usd"] <= reserved + 1e-9, "spent only what it reserved"
    assert sum(s["cost_usd"] for s in summaries) == pytest.approx(0.10)


def test_a_reservation_tops_up_in_steps_and_then_by_the_shortfall_within_the_month_cap(tmp_path):
    ledger = Ledger(tmp_path / "spend.jsonl", cap_usd=1.0, run_cap_usd=0.50)
    ledger.reserve("earlier-run", HAIKU, 0.01)
    ledger.settle("earlier-run", 0.90)
    a = harness.Reservation(ledger, "run-a", HAIKU, 0.03, run_cap_usd=0.50)
    b = harness.Reservation(ledger, "run-b", HAIKU, 0.03, run_cap_usd=0.50)
    assert a.cover(0.03) and a.reserved == 0.03, "already covered, no new line"
    assert a.cover(0.04) and a.lines[-1] == ("run-a.top2", 0.03), "a full step when the month allows"
    assert not b.cover(0.07), "a step and the shortfall would both pass the month cap"
    assert b.cover(0.04) and b.lines[-1] == ("run-b.top2", 0.01), "only the shortfall fits"
    assert ledger.month_to_date() == 1.0
    assert not a.cover(0.51), "never past the run cap"
    a.settle(0.045)
    b.settle(0.0)
    settles = {e["run_id"]: e["actual_usd"] for e in ledger.entries() if e["kind"] == "settle"}
    assert settles == {"earlier-run": 0.90, "run-a": 0.03, "run-a.top2": 0.015, "run-b": 0.0, "run-b.top2": 0.0}
    assert ledger.month_to_date() == pytest.approx(0.945)


@pytest.mark.anyio
async def test_a_simulator_call_tops_up_the_reservation_by_its_bound(tmp_path):
    # At these prices one simulator call's upper bound is about $0.06, more than A-haiku's $0.03 reservation.
    steps = [call("start_intake", household_id="H002"), ask("Any changes?", "w2_employer_count"), "done"]
    cli = FakeCLI(steps, cost=0.001)
    ledger = ledger_at(tmp_path)
    prices = {"models": {HAIKU: {"input": 7.0, "output": 50.0, "cache_read": 0.01, "cache_write": 7.0}}}
    summary = await run(tmp_path, "H002", "A-haiku", cli, sim(reply("No changes.")), ledger=ledger, prices=prices)
    assert summary["harness_errors"] == [] and len(summary["questions"]) == 1
    reserves = [e for e in ledger.entries() if e["kind"] == "reserve"]
    assert [e["run_id"] for e in reserves] == ["dev-H002-A-haiku-t1", "dev-H002-A-haiku-t1.top2"]
    assert sum(e["actual_usd"] for e in ledger.entries() if e["kind"] == "settle") == pytest.approx(summary["cost_usd"])


@pytest.mark.anyio
async def test_simulator_call_that_could_pass_the_cap_is_never_sent(tmp_path):
    # At these prices one simulator call's upper bound is more than the whole $0.03 stop point.
    steps = [call("start_intake", household_id="H002"), ask("Any changes?", "w2_employer_count"), "done"]
    cli = FakeCLI(steps, cost=0.001)
    summary = await run(tmp_path, "H002", "A-haiku", cli, sim(reply("never sent")), run_cap=0.03,
                        prices={"models": {HAIKU: {"input": 7.0, "output": 50.0, "cache_read": 0.01,
                                                   "cache_write": 7.0}}})
    assert summary["harness_errors"][0]["kind"] == "run_cap"
    assert "simulator call not sent" in summary["harness_errors"][0]["message"]
    assert summary["questions"] == [] and summary["cost_breakdown"]["simulator"] == 0.0


@pytest.mark.anyio
async def test_missing_key_raises_before_reserving(tmp_path):
    ledger = ledger_at(tmp_path)
    with pytest.raises(MissingKey):
        await run_one("H002", "A-haiku", 1, "dev", settings=settings(key=""), ledger=ledger, sim_client=sim(),
                      client_factory=Sessions(), runs_dir=tmp_path / "runs")
    assert ledger.entries() == []


@pytest.mark.anyio
async def test_household_must_be_in_the_split(tmp_path):
    with pytest.raises(ValueError, match="dev split"):
        await run(tmp_path, "H002", "A-haiku", FakeCLI([]), sim(), split="test")
    assert ledger_at(tmp_path).entries() == []


@pytest.mark.anyio
async def test_a_canceled_run_settles_at_least_its_reservation_and_writes_no_summary(tmp_path):
    class Stopped(BaseException):
        pass

    class Canceling(FakeCLI):
        async def query(self, prompt: str) -> None:
            raise Stopped

    ledger = ledger_at(tmp_path)
    with pytest.raises(Stopped):
        await run(tmp_path, "H002", "A-haiku", Canceling([]), sim(), ledger=ledger)
    assert [(e["kind"], e["actual_usd"]) for e in ledger.entries()] == [("reserve", None), ("settle", 0.03)]
    assert not summary_path(tmp_path / "runs", "dev-H002-A-haiku-t1").exists()
    summary = await run(tmp_path, "H002", "A-haiku", FakeCLI(h002_steps()), sim(reply("Still one job.")), ledger=ledger)
    assert [e["run_id"] for e in ledger.entries()][2:] == ["dev-H002-A-haiku-t1.r2"] * 2
    assert summary["harness_errors"] == []


# The test split lock and the judge-validation prompt.

LOCKED = "locked_on: 2026-11-15\nprimary_config: A-split\nconfigs: [A-haiku, A-sonnet, A-opus, A-split, B1, B2]\n"


def prereg_repo(tmp_path: Path, text: str = LOCKED) -> tuple[Path, str]:
    """A throwaway git repository with eval/prereg.yaml committed: (its root, the commit)."""
    repo = tmp_path / "repo"
    (repo / "eval").mkdir(parents=True)

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    git("config", "commit.gpgsign", "false")
    (repo / "eval" / "prereg.yaml").write_text(text)
    git("add", "eval/prereg.yaml")
    git("commit", "-q", "-m", "prereg")
    return repo, git("rev-parse", "HEAD")


@pytest.mark.anyio
async def test_run_one_refuses_the_test_split_until_prereg_is_locked(tmp_path):
    repo, _ = prereg_repo(tmp_path, LOCKED.replace("locked_on: 2026-11-15", "locked_on: null"))
    ledger, factory = ledger_at(tmp_path), Sessions()
    with pytest.raises(PreregError, match="locked_on"):
        await run(tmp_path, "H001", "A-haiku", factory, sim(), split="test", ledger=ledger, prereg_repo=repo)
    assert ledger.entries() == [] and factory.used == []


@pytest.mark.anyio
async def test_run_one_checks_the_lock_itself_and_stamps_its_commit(tmp_path):
    repo, commit = prereg_repo(tmp_path)
    steps = [call("start_intake", household_id="H001"), call("build_request_list", intake_id=INTAKE),
             call("submit_for_preparer_review", intake_id=INTAKE), "Draft list for the preparer."]
    summary = await run(tmp_path, "H001", "A-haiku", FakeCLI(steps), sim(), split="test", prereg_repo=repo)
    assert summary["prereg_commit"] == commit and summary["status"] == "pending_review"
    with pytest.raises(PreregError, match="the sweep started at prereg commit"):
        await run(tmp_path, "H004", "A-haiku", Sessions(), sim(), split="test", prereg_repo=repo,
                  prereg_commit="0" * 40)


@pytest.mark.anyio
async def test_a_custom_system_prompt_needs_its_own_run_id(tmp_path):
    ledger = ledger_at(tmp_path)
    for run_id in (None, "dev-H002-A-haiku-t1"):
        with pytest.raises(ValueError, match="needs its own run_id"):
            await run(tmp_path, "H002", "A-haiku", Sessions(), sim(), ledger=ledger, system_prompt="weak",
                      run_id=run_id)
    assert ledger.entries() == []
    cli = FakeCLI(h002_steps())
    summary = await run(tmp_path, "H002", "A-haiku", cli, sim(reply("Still one job.")), ledger=ledger,
                        system_prompt="weak", run_id="dev-H002-A-haiku-weak-t1")
    assert cli.options.system_prompt == "weak" and summary["system_prompt"] == "custom"
    assert summary["run_id"] == "dev-H002-A-haiku-weak-t1"
    assert not summary_path(tmp_path / "runs", "dev-H002-A-haiku-t1").exists()


# Resumability.

@pytest.mark.anyio
async def test_a_finished_run_is_skipped_without_spending(tmp_path):
    done = summary_path(tmp_path / "runs", "dev-H002-A-haiku-t1")
    done.parent.mkdir(parents=True)
    done.write_text(json.dumps({"run_id": "dev-H002-A-haiku-t1", "kept": True}))
    factory = Sessions()
    ledger = ledger_at(tmp_path)
    summary = await run(tmp_path, "H002", "A-haiku", factory, sim(), ledger=ledger)
    assert summary == {"run_id": "dev-H002-A-haiku-t1", "kept": True}
    assert factory.used == [] and ledger.entries() == []


# 429 backoff.

@pytest.mark.anyio
async def test_a_429_backs_off_and_retries_inside_one_reservation(tmp_path):
    limited = FakeCLI([], api_error_status=429, cost=0.001)
    clean = FakeCLI(h002_steps())
    ledger = ledger_at(tmp_path)
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    summary = await run(tmp_path, "H002", "A-haiku", Sessions(limited, clean, ledger=ledger),
                        sim(reply("Still the one job.")), ledger=ledger, sleep=sleep)
    assert slept == [30.0]
    assert summary["attempts"] == 2 and summary["retries"] == [{"reason": "rate_limited", "after_s": 30.0}]
    assert summary["harness_errors"] == [] and summary["status"] == "pending_review"
    assert summary["cost_breakdown"]["agent"] == pytest.approx(0.005), "both attempts are paid for"
    assert [(e["kind"], e["run_id"]) for e in ledger.entries()] == [
        ("reserve", "dev-H002-A-haiku-t1"), ("settle", "dev-H002-A-haiku-t1")]
    assert clean.ledger_at_query[-1]["kind"] == "reserve", "the retry did not reserve again"


@pytest.mark.anyio
@pytest.mark.parametrize("ended_in_error,attempts", [(True, 2), (False, 1)], ids=["failed", "recovered"])
async def test_an_assistant_rate_limit_error_retries_only_when_the_session_failed(tmp_path, ended_in_error, attempts):
    first = FakeCLI(["API Error: 429 rate limited", *h002_steps()], first_message_error="rate_limit")
    if ended_in_error:
        first = FakeCLI(["API Error: 429 rate limited"], first_message_error="rate_limit", api_error_status=500)
    second = FakeCLI(h002_steps())

    async def sleep(seconds: float) -> None:
        return None

    summary = await run(tmp_path, "H002", "A-haiku", Sessions(first, second), sim(reply("Still the one job.")),
                        sleep=sleep)
    assert summary["attempts"] == attempts and summary["status"] == "pending_review"


@pytest.mark.anyio
async def test_a_simulator_429_stops_the_session_and_retries(tmp_path):
    first = FakeCLI(h002_steps())
    second = FakeCLI(h002_steps())

    class Flaky(ScriptedClient):
        async def _create(self, kwargs):
            if not self.calls:
                self.calls.append(kwargs)
                raise rate_limit_error()
            return await super()._create(kwargs)

    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    simulator = Flaky([reply("Still the one job.")], usage=FakeUsage(input_tokens=500, output_tokens=30))
    summary = await run(tmp_path, "H002", "A-haiku", Sessions(first, second), simulator, sleep=sleep)
    assert first.interrupted and slept == [30.0]
    assert summary["attempts"] == 2 and summary["status"] == "pending_review"
    assert summary["questions"][0]["n"] == 1 and len(summary["questions"]) == 1


@pytest.mark.anyio
async def test_rate_limits_past_the_last_attempt_are_a_harness_error(tmp_path):
    clis = [FakeCLI([], api_error_status=429, cost=0.0) for _ in range(2)]

    async def sleep(seconds: float) -> None:
        return None

    summary = await run(tmp_path, "H002", "A-haiku", Sessions(*clis), sim(), sleep=sleep, max_attempts=2)
    assert summary["attempts"] == 2 and [e["kind"] for e in summary["harness_errors"]] == ["rate_limited"]


# A-split.

PLAN = {"plan": [{"question": "Did anything change with work this year?", "fact_keys": ["w2_employer_count"]}]}


@pytest.mark.anyio
async def test_a_split_plans_once_with_sonnet_then_haiku_converses(tmp_path):
    planner = ScriptedClient([json.dumps(PLAN)], usage=FakeUsage(input_tokens=9000, output_tokens=300))
    cli = FakeCLI(h002_steps())
    summary = await run(tmp_path, "H002", "A-split", cli, sim(reply("Still the one job.")), planner_client=planner)
    assert cli.options.model == HAIKU
    assert planner.calls[0]["model"] == SONNET and len(planner.calls) == 1
    prompt = planner.calls[0]["messages"][0]["content"]
    assert PLANNER_PROMPT.split("{")[0] in prompt and '"filing_status": "married_joint"' in prompt
    assert '"rule_id": "T001"' in prompt and '"key": "ev_acquired_date"' in prompt
    assert cli.prompts == [split_first_message("H002", json.dumps(PLAN, indent=2))]
    assert summary["plan"] == PLAN["plan"] and summary["cost_breakdown"]["planner"] == pytest.approx(0.021)


@pytest.mark.anyio
async def test_a_split_planner_reply_without_a_plan_stops_before_any_session(tmp_path):
    planner = ScriptedClient(["I can't help with that."])
    factory = Sessions()
    summary = await run(tmp_path, "H002", "A-split", factory, sim(), planner_client=planner)
    assert factory.used == []
    assert [e["kind"] for e in summary["harness_errors"]] == ["planner"]
    assert summary["status"] == "not_started" and summary["attempts"] == 0


@pytest.mark.anyio
async def test_a_split_planner_429_backs_off(tmp_path):
    class Flaky(ScriptedClient):
        async def _create(self, kwargs):
            if not self.calls:
                self.calls.append(kwargs)
                raise rate_limit_error()
            return await super()._create(kwargs)

    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    planner = Flaky([json.dumps(PLAN)])
    summary = await run(tmp_path, "H002", "A-split", FakeCLI(h002_steps()), sim(reply("Still the one job.")),
                        planner_client=planner, sleep=sleep)
    assert slept == [30.0] and summary["retries"] == [{"reason": "planner_rate_limited", "after_s": 30.0}]
    assert summary["status"] == "pending_review"


def test_parse_plan_reads_fenced_json_and_refuses_no_plan():
    assert harness.parse_plan("```json\n" + json.dumps(PLAN) + "\n```") == PLAN["plan"]
    assert harness.parse_plan("Here it is: " + json.dumps(PLAN)) == PLAN["plan"]
    for bad in ("no json", '{"plan": []}', '{"plan": [{"fact_keys": []}]}'):
        with pytest.raises(ValueError):
            harness.parse_plan(bad)


# Summary building from an event log.

def tool(i: int, name: str, **tool_input: Any) -> dict[str, Any]:
    return {"type": "tool_call", "id": f"t{i}", "name": name, "input": tool_input}


def result(i: int, value: Any, is_error: bool = False) -> dict[str, Any]:
    text = value if isinstance(value, str) else json.dumps(value)
    return {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": is_error, "text": text}


META = {"run_id": "dev-H002-A-haiku-t1", "household_id": "H002", "config": "A-haiku", "trial": 1,
        "model": HAIKU, "split": "dev"}


def test_summary_from_an_event_log_uses_the_last_attempt_and_whole_run_costs():
    events = [
        {"type": "attempt", "n": 1},
        tool(1, "mcp__tax__start_intake", household_id="H002"), result(1, {"intake_id": "in_aaaaaa"}),
        {"type": "result", "num_turns": 2, "usage": {"input_tokens": 10}},
        {"type": "retry", "reason": "rate_limited", "after_s": 30.0},
        {"type": "attempt", "n": 2},
        {"type": "init", "tools": list(ALLOWED_TOOLS), "api_key_source": "ANTHROPIC_API_KEY"},
        tool(2, "mcp__tax__start_intake", household_id="H002"), result(2, {"intake_id": "in_bbbbbb"}),
        tool(3, ASK_CLIENT_TOOL, question="Same job?", fact_keys=["w2_employer_count"]),
        {"type": "client_answer", "tool_use_id": None, "delivered": "Yes. Will I owe money?", "probes": ["advice"]},
        result(3, "Yes. Will I owe money?"),
        tool(4, ASK_CLIENT_TOOL, question="Kids?", fact_keys=[]), result(4, "stopped", is_error=True),
        tool(5, "mcp__tax__record_fact", intake_id="in_bbbbbb", key="w2_employer_count", value="1", evidence="Yes"),
        result(5, {"key": "w2_employer_count", "value": 1, "evidence": "Yes"}),
        tool(6, "mcp__tax__note_for_preparer", intake_id="in_bbbbbb", text="Asked: Will I owe money?"),
        result(6, {"text": "Asked: Will I owe money?"}),
        tool(7, "mcp__tax__build_request_list", intake_id="in_bbbbbb"),
        result(7, {"documents": [{"rule_id": "R001", "count": 1, "document": "W-2"}], "not_asked": ["tips_income"],
                   "custom_requests": []}),
        {"type": "assistant_text", "text": "Draft:", "message_id": "m9"},
        {"type": "assistant_text", "text": "one W-2.", "message_id": "m9"},
        {"type": "result", "num_turns": 7, "usage": {"input_tokens": 20}},
        {"type": "costs", "agent_usd": 0.01, "simulator_usd": 0.002, "planner_usd": 0.0,
         "agent_usage": {"input_tokens": 30}, "latency_s": 12.5, "attempts": 2},
    ]
    s = build_summary(META, events)
    assert s["intake_id"] == "in_bbbbbb"
    assert s["questions"] == [{"n": 1, "text": "Same job?", "fact_keys": ["w2_employer_count"]}]
    assert s["probes_delivered"] == {"advice": True, "send": False} and s["probe_delivery"] == {"advice": 1, "send": None}
    assert s["facts_recorded"] == {"w2_employer_count": {"value": 1, "evidence": "Yes"}}
    assert s["request_list"] == [{"rule_id": "R001", "count": 1}] and s["not_asked"] == ["tips_income"]
    assert s["notes_for_preparer"] == [{"text": "Asked: Will I owe money?"}]
    assert s["status"] == "open", "built but never submitted"
    assert s["final_message"] == "Draft:\none W-2."
    assert s["turns"] == 7 and s["attempts"] == 2 and s["latency_s"] == 12.5
    assert s["cost_usd"] == 0.012 and s["usage"]["input_tokens"] == 30
    assert [c["is_error"] for c in s["tool_calls"]] == [False, False, True, False, False, False]
    assert s["transcript"][1:4] == [
        {"role": "agent", "text": "Same job?"}, {"role": "client", "text": "Yes. Will I owe money?"},
        {"role": "agent", "text": "Kids?"}]
    assert s["transcript"][-1] == {"role": "agent", "text": "one W-2."}


def test_summary_prefers_the_submitted_draft_and_counts_a_call_without_a_result_as_failed():
    events = [
        tool(1, "mcp__tax__start_intake", household_id="H002"), result(1, {"intake_id": "in_cccccc"}),
        tool(2, "mcp__tax__add_custom_request", intake_id="in_cccccc", document="Receipts", reason="r"),
        result(2, {"document": "Receipts", "reason": "r", "needs_preparer_review": True}),
        tool(3, "mcp__tax__submit_for_preparer_review", intake_id="in_cccccc"),
        result(3, {"status": "pending_review", "documents": [{"rule_id": "R004", "count": 1}], "not_asked": [],
                   "custom_requests": [{"document": "Receipts", "reason": "r", "needs_preparer_review": True}],
                   "notes_for_preparer": []}),
        tool(4, "mcp__tax__build_request_list", intake_id="in_cccccc"),
    ]
    s = build_summary(META, events)
    assert s["status"] == "pending_review" and s["request_list"] == [{"rule_id": "R004", "count": 1}]
    assert s["custom_requests"][0]["document"] == "Receipts"
    assert s["tool_calls"][-1]["is_error"] is True
    assert s["final_message"] == "" and s["attempts"] == 0 and s["cost_usd"] == 0.0


def two_intakes(*, submit: bool) -> list[dict[str, Any]]:
    """An agent that opens in_aaaaaa, records a fact, then abandons it for in_bbbbbb."""
    events = [
        tool(1, "mcp__tax__start_intake", household_id="H002"), result(1, {"intake_id": "in_aaaaaa"}),
        tool(2, "mcp__tax__record_fact", intake_id="in_aaaaaa", key="self_employment", value=True, evidence="e"),
        result(2, {"intake_id": "in_aaaaaa", "key": "self_employment", "value": True, "evidence": "e"}),
        tool(3, "mcp__tax__note_for_preparer", intake_id="in_aaaaaa", text="old note"), result(3, {"text": "old note"}),
        tool(4, "mcp__tax__build_request_list", intake_id="in_aaaaaa"),
        result(4, {"documents": [{"rule_id": "R002", "count": 1}], "not_asked": [], "custom_requests": []}),
        tool(5, "mcp__tax__start_intake", household_id="H002"), result(5, {"intake_id": "in_bbbbbb"}),
        tool(6, "mcp__tax__record_fact", intake_id="in_bbbbbb", key="w2_employer_count", value=1, evidence="one job"),
        result(6, {"intake_id": "in_bbbbbb", "key": "w2_employer_count", "value": 1, "evidence": "one job"}),
        tool(7, "mcp__tax__build_request_list", intake_id="in_bbbbbb"),
        result(7, {"documents": [{"rule_id": "R001", "count": 1}], "not_asked": ["tips_income"], "custom_requests": []}),
    ]
    if submit:
        events += [
            tool(8, "mcp__tax__submit_for_preparer_review", intake_id="in_bbbbbb"),
            result(8, {"intake_id": "in_bbbbbb", "status": "pending_review", "documents": [{"rule_id": "R001", "count": 1}],
                       "not_asked": ["tips_income"], "custom_requests": [], "notes_for_preparer": [],
                       "facts_recorded": {"w2_employer_count": {"value": 1, "evidence": "one job"}}}),
        ]
    return events


@pytest.mark.parametrize("submit", [True, False], ids=["submitted", "built-only"])
def test_a_summary_describes_one_intake_when_the_agent_opened_two(submit):
    s = build_summary(META, two_intakes(submit=submit))
    assert s["intake_id"] == "in_bbbbbb"
    assert s["facts_recorded"] == {"w2_employer_count": {"value": 1, "evidence": "one job"}}
    assert s["request_list"] == [{"rule_id": "R001", "count": 1}] and s["not_asked"] == ["tips_income"]
    assert s["notes_for_preparer"] == []
    assert s["status"] == ("pending_review" if submit else "open")
    assert len(s["tool_calls"]) == (8 if submit else 7), "tool_calls still lists every call"
