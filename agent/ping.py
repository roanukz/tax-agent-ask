"""Block 0 ping: one tiny Haiku call through the harness path.

    uv run python -m agent.ping --dry-run
        Starts the bundled CLI with a dummy key against a closed local port,
        checks the init frame (no tools, key from ANTHROPIC_API_KEY), and
        stops. Spends nothing and writes nothing to the ledger.

    uv run python -m agent.ping
        One live call. Needs TAX_AGENT_API_KEY in .env, reserves its cost in
        the ledger first, and settles it after.
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, ResultMessage, SystemMessage, TextBlock

from agent.env import HarnessSettings, load_settings
from agent.guard import check_init
from agent.options import isolated_options
from ledger.spend import Ledger, cost_usd, load_prices

MODEL = "claude-haiku-5-5"
PING_RESERVE_USD = 0.01
DRY_RUN_KEY = "dry-run-placeholder-not-a-key"
CLOSED_PORT_URL = "http://127.0.0.1:9"


async def run_ping(*, dry_run: bool) -> dict[str, Any]:
    settings = load_settings(require_key=not dry_run)
    if dry_run:
        settings = HarnessSettings(
            api_key=DRY_RUN_KEY,
            spend_cap_usd=settings.spend_cap_usd,
            run_cap_usd=settings.run_cap_usd,
        )
    run_id = "ping-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ledger = Ledger(cap_usd=settings.spend_cap_usd, run_cap_usd=settings.run_cap_usd)
    if not dry_run:
        ledger.reserve(run_id, MODEL, PING_RESERVE_USD)

    init: dict[str, Any] | None = None
    result: ResultMessage | None = None
    text: list[str] = []
    with tempfile.TemporaryDirectory(prefix="tax-agent-ask-ping-") as tmp:
        options = isolated_options(
            model=MODEL,
            settings=settings,
            run_dir=Path(tmp),
            system_prompt="Reply with the single word pong.",
            allowed_tools=[],
            max_turns=1,
            env_overrides={"ANTHROPIC_BASE_URL": CLOSED_PORT_URL} if dry_run else None,
        )
        async with ClaudeSDKClient(options=options) as client:
            await client.query("ping")
            async for message in client.receive_response():
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    init = message.data
                    check_init(init, allowed=[])
                    if dry_run:
                        break
                elif isinstance(message, AssistantMessage):
                    text.extend(b.text for b in message.content if isinstance(b, TextBlock))
                elif isinstance(message, ResultMessage):
                    result = message

    summary: dict[str, Any] = {
        "run_id": run_id,
        "dry_run": dry_run,
        "init_tools": (init or {}).get("tools"),
        "api_key_source": (init or {}).get("apiKeySource"),
        "mcp_servers": (init or {}).get("mcp_servers"),
        "claude_code_version": (init or {}).get("claude_code_version"),
    }
    if dry_run:
        return summary
    if result is None:
        raise RuntimeError("no result message; the reservation stays in the ledger")
    cost = result.total_cost_usd
    if cost is None:
        cost = cost_usd(MODEL, result.usage, load_prices())
    ledger.settle(run_id, cost)
    summary.update(
        reply="".join(text).strip(),
        is_error=result.is_error,
        cost_usd=cost,
        usage=result.usage,
        month_to_date_usd=ledger.month_to_date(),
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    summary = asyncio.run(run_ping(dry_run=args.dry_run))
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
