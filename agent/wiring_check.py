"""Manual wiring check: a real CLI session through the harness, with both MCP servers, against a closed port.

    uv run python -m agent.wiring_check

Not run by pytest, since it starts the bundled Claude Code CLI and the tax
server. Like agent.ping --dry-run, it passes a placeholder key and sets
ANTHROPIC_BASE_URL to a closed local port, so no request can leave the
machine. It spends nothing: the ledger and run files go to a temp folder,
and the client simulator is a stand-in that fails if anything calls it.

Both checks run agent.harness.run_one, so the options, hooks, MCP servers,
and init check are the ones a live run uses. That init check already stops
a run on an extra or missing tool, a server that is not connected, or the
wrong key; this check adds the CLI's get_mcp_status view. A thin client
wrapper reads the init frame, asks the CLI for its MCP server status, and
interrupts before the first model request can be retried.

1. Clean session: the visible tools are exactly the 9 allowed ones, both
   MCP servers report connected, apiKeySource is ANTHROPIC_API_KEY, and the
   run ends with no harness error. The server status shows each server's
   name and version, and its protocol version when the CLI reports one.
2. Planted built-in tool: tools=["Bash"] (with Bash taken off the
   disallowed backstop list) makes Bash visible, and the harness's init
   check must abort the run with a harness error before any tool runs.

Exit code 0 when both come out as expected.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, SystemMessage

from agent.env import HarnessSettings
from agent.harness import ALLOWED_TOOLS, run_one
from agent.ping import CLOSED_PORT_URL, DRY_RUN_KEY
from ledger.spend import Ledger

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


async def main_async() -> int:
    ok = True
    with tempfile.TemporaryDirectory(prefix="tax-agent-ask-wiring-") as tmp:
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
    print(f"Wiring check: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
