"""The offline parts of agent/wiring_check.py.

The check itself starts the bundled CLI, so pytest never runs it; run it by
hand with uv run python -m agent.wiring_check. These tests cover what it
decides and how it plants a tool, and drive its run_one path with a fake
CLI client in place of ClaudeSDKClient.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage

from agent import fake_api, wiring_check
from agent.env import API_BASE_URL, HarnessSettings
from agent.harness import ALLOWED_TOOLS
from agent.options import isolated_options
from agent.ping import CLOSED_PORT_URL

CONNECTED = [{"name": "tax", "status": "connected"}, {"name": "harness", "status": "connected"}]
STATUS = {"mcpServers": [{"name": "tax", "status": "connected", "serverInfo": {"name": "tax", "version": "0.1.0"}},
                         {"name": "harness", "status": "connected"}]}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def init(tools=ALLOWED_TOOLS, servers=CONNECTED, source="ANTHROPIC_API_KEY") -> dict[str, Any]:
    return {"tools": list(tools), "mcp_servers": servers, "apiKeySource": source}


def test_assess_accepts_the_nine_tools_two_connected_servers_and_the_workspace_key():
    assert wiring_check.assess(init(), STATUS) == []


@pytest.mark.parametrize("frame,status,expect", [
    (init(tools=[*ALLOWED_TOOLS, "ListMcpResourcesTool"]), STATUS, "extra ['ListMcpResourcesTool']"),
    (init(tools=ALLOWED_TOOLS[:-1]), STATUS, "missing ['mcp__harness__ask_client']"),
    (init(servers=[{"name": "tax", "status": "failed"}, CONNECTED[1]]), STATUS, "server tax as 'failed'"),
    (init(), {"mcpServers": STATUS["mcpServers"][:1]}, "get_mcp_status shows server harness as None"),
    (init(source="none"), STATUS, "apiKeySource is 'none'"),
    (None, STATUS, "no init frame"),
])
def test_assess_names_each_problem(frame, status, expect):
    assert any(expect in p for p in wiring_check.assess(frame, status))


def options(tmp_path: Path):
    settings = HarnessSettings(api_key="placeholder", spend_cap_usd=150, run_cap_usd=0.5)
    return isolated_options(model="claude-haiku-5-5", settings=settings, run_dir=tmp_path, system_prompt="x",
                            allowed_tools=list(ALLOWED_TOOLS))


def test_plant_tool_makes_bash_visible_and_closed_port_points_at_a_closed_port(tmp_path):
    base = options(tmp_path)
    planted = wiring_check.plant_tool(base)
    assert base.tools == [] and "Bash" in base.disallowed_tools
    assert planted.tools == ["Bash"] and "Bash" not in planted.disallowed_tools
    assert wiring_check.closed_port(base).env["ANTHROPIC_BASE_URL"] == CLOSED_PORT_URL
    assert base.env["ANTHROPIC_BASE_URL"] == API_BASE_URL


def test_protocol_version_is_read_when_the_cli_reports_one():
    assert wiring_check.protocol_version({"serverInfo": {"protocolVersion": "2026-07-28"}}) == "2026-07-28"
    assert wiring_check.protocol_version({"protocolVersion": "2025-11-25"}) == "2025-11-25"
    assert wiring_check.protocol_version({"serverInfo": {"name": "tax"}}) == "not shown"


class FakeInner:
    """Stands in for ClaudeSDKClient: init frame from the options it was given, then a result."""

    made: list[FakeInner] = []

    def __init__(self, *, options: Any) -> None:
        self.options = options
        self.interrupts = 0
        FakeInner.made.append(self)

    async def __aenter__(self) -> FakeInner:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def query(self, prompt: str) -> None:
        self.prompt = prompt

    async def interrupt(self) -> None:
        self.interrupts += 1

    async def get_mcp_status(self) -> dict[str, Any]:
        return STATUS

    async def receive_response(self):
        tools = [*self.options.tools, *ALLOWED_TOOLS]
        source = "ANTHROPIC_API_KEY" if self.options.env["ANTHROPIC_BASE_URL"] == CLOSED_PORT_URL else "none"
        yield SystemMessage(subtype="init", data=init(tools=tools, source=source))
        yield ResultMessage(subtype="error_during_execution", duration_ms=5, duration_api_ms=0, is_error=True,
                            num_turns=0, session_id="fake", total_cost_usd=0.0, usage={})


@pytest.mark.anyio
@pytest.mark.parametrize("plant", [False, True], ids=["clean", "planted"])
async def test_sessions_go_through_run_one_and_interrupt_after_init(tmp_path, monkeypatch, plant):
    monkeypatch.setattr(wiring_check, "ClaudeSDKClient", FakeInner)
    FakeInner.made.clear()
    summary, client = await wiring_check.session(tmp_path, "planted" if plant else "clean", plant=plant)
    inner = FakeInner.made[-1]
    assert inner.options.env["ANTHROPIC_BASE_URL"] == CLOSED_PORT_URL
    assert client.status == STATUS and inner.interrupts == 1, "one interrupt, however many asked"
    assert summary["tool_calls"] == []
    if plant:
        assert summary["harness_errors"][0]["kind"] == "init_check"
        assert "Bash" in summary["harness_errors"][0]["message"]
    else:
        assert summary["harness_errors"] == [] and wiring_check.assess(client.init, client.status) == []
    assert (tmp_path / f"{'planted' if plant else 'clean'}-spend.jsonl").exists(), "a temp ledger, not the real one"


def agent_request(step: int = 0, max_tokens: int = 8192, workdir: str = "/tmp/intake-abc123/cwd", extra: str = "") -> dict[str, Any]:
    env = f"# Environment\n - Primary working directory: {workdir}\n{extra}"
    body = {"max_tokens": max_tokens, "tools": [{"name": "mcp__tax__start_intake"}],
            "messages": [{"role": "user", "content": "Start intake for household H002."},
                         {"role": "system", "content": [{"type": "text", "text": env}]}]}
    return {"path": "/v1/messages?beta=true", "main": True, "step": step, "model": "claude-haiku-5-5", "body": body,
            "usage": {"input_tokens": 9000, "output_tokens": 100}}


def test_request_problems_flag_the_cap_a_usd_budget_and_a_working_folder_that_names_the_run():
    run_ids = ["dev-H002-A-haiku-t1"]
    assert wiring_check.request_problems([agent_request()], 8192, run_ids) == []
    assert "max_tokens 128000, not 8192" in wiring_check.request_problems([agent_request(max_tokens=128000)], 8192, run_ids)[0]
    budget = agent_request(extra="USD budget: $0/$0.5; $0.5 remaining")
    assert "USD budget" in wiring_check.request_problems([budget], 8192, run_ids)[0]
    named = agent_request(workdir="/tmp/tax-agent-ask-dev-H002-A-haiku-t1-q1d_g0eo/cwd")
    assert "names the run" in wiring_check.request_problems([named], 8192, run_ids)[0]
    side = {**agent_request(), "main": False}
    assert "outside the agent loop" in wiring_check.request_problems([side], 8192, run_ids)[0]


def stream_usage(message_id: str, prompt: int, held: int | None) -> dict[str, Any]:
    return {"type": "stream_usage", "message_id": message_id, "held_prompt_tokens": held,
            "usage": {"input_tokens": prompt, "output_tokens": 300, "cache_read_tokens": 0, "cache_write_tokens": 0}}


def test_held_problems_flag_a_call_held_below_its_prompt_or_one_the_harness_missed():
    events = [stream_usage("m1", 9000, 13000), stream_usage("m2", 12000, 12500)]
    assert wiring_check.held_problems(events, sent=2) == []
    assert "prompt 12000 tokens, but 11000 were held" in wiring_check.held_problems(
        [events[0], stream_usage("m2", 12000, 11000)], sent=2)[0]
    assert "but None were held" in wiring_check.held_problems([stream_usage("m1", 9000, None)], sent=1)[0]
    assert "the API saw 3 agent calls" in wiring_check.held_problems(events, sent=3)[0]


def sse_types(out: bytes) -> list[str]:
    return [json.loads(line[len("data: "):])["type"] for line in out.decode().splitlines() if line.startswith("data: ")]


def test_the_fake_api_refuses_cuts_and_drops_the_agent_requests_it_is_told_to():
    # answer() needs no socket, so the fake API's replies are checked here; the check itself serves them over TCP.
    faults = {1: fake_api.REFUSED, 2: fake_api.CUT, 3: fake_api.DROPPED}
    api = fake_api.FakeMessagesAPI(lambda step, body: [(fake_api.TOOL_USE, "mcp__tax__list_fact_keys", {})],
                                   lambda body: 400, faults=lambda n, body: faults.get(n))
    body = {"model": "claude-haiku-5-5", "max_tokens": 8192, "stream": True, "tools": [{"name": "x"}],
            "messages": [{"role": "user", "content": "Start intake for household H002."}]}
    status, out, _ = api.answer("/v1/messages", body)
    assert status == 429 and json.loads(out)["error"]["type"] == "rate_limit_error"
    status, out, _ = api.answer("/v1/messages", body)
    assert status == 200 and sse_types(out)[0] == "message_start" and sse_types(out)[-1] == "error"
    assert "message_delta" not in sse_types(out), "a cut stream never gives the real output"
    assert api.answer("/v1/messages", body) is None, "a dropped request gets no answer"
    status, out, _ = api.answer("/v1/messages", body)
    assert status == 200 and sse_types(out)[-2:] == ["message_delta", "message_stop"]
    assert [r["fault"] for r in api.requests] == ["refused", "cut", "dropped", None]
    assert [r["usage"]["output_tokens"] for r in api.requests] == [0, 400, 400, 400], \
        "a refused request bills nothing; a cut or dropped one is kept at the most it could have billed"
