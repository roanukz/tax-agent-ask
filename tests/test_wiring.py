"""The offline parts of agent/wiring_check.py.

The check itself starts the bundled CLI, so pytest never runs it; run it by
hand with uv run python -m agent.wiring_check. These tests cover what it
decides and how it plants a tool, and drive its run_one path with a fake
CLI client in place of ClaudeSDKClient.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage

from agent import wiring_check
from agent.env import HarnessSettings
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
    assert "ANTHROPIC_BASE_URL" not in base.env


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
