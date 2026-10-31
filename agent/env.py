"""Harness settings from .env, without ever putting the key in os.environ.

Claude Code switches its own session to ANTHROPIC_API_KEY when it finds that
variable, so the key is stored as TAX_AGENT_API_KEY and handed only to the
harness's own child processes and API clients. Both go to API_BASE_URL, and
nowhere a parent shell's ANTHROPIC_BASE_URL or custom headers point.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DOTENV = REPO / ".env"
API_BASE_URL = "https://api.anthropic.com"

# Variables that could give a child Claude Code process a credential other
# than the workspace key. Blanked per run.
COMPETING_CREDENTIALS = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
    "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_IDENTITY_TOKEN",
    "ANTHROPIC_IDENTITY_TOKEN_FILE",
    "ANTHROPIC_FEDERATION_RULE_ID",
    "CLAUDE_CODE_HOST_AUTH_ENV_VAR",
    "CLAUDE_CODE_HOST_CREDS_FILE",
)
# Every switch in the bundled CLI 2.1.314 that sends its requests to another
# provider or through a host's gateway. Blanked per run.
PROVIDER_SWITCHES = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_GATEWAY",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
)
# Variables that add headers to the CLI's requests or route them through a
# local socket. Blanked per run; ANTHROPIC_BASE_URL is pinned to API_BASE_URL.
REQUEST_ROUTING = (
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_UNIX_SOCKET",
)
BLANKED = COMPETING_CREDENTIALS + PROVIDER_SWITCHES + REQUEST_ROUTING


class MissingKey(RuntimeError):
    """TAX_AGENT_API_KEY is empty or missing from .env."""


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(frozen=True)
class HarnessSettings:
    api_key: str = field(repr=False)
    spend_cap_usd: float
    run_cap_usd: float


def load_settings(path: Path = DEFAULT_DOTENV, *, require_key: bool = True) -> HarnessSettings:
    values = read_dotenv(path) if Path(path).exists() else {}
    key = values.get("TAX_AGENT_API_KEY", "")
    if require_key and not key:
        raise MissingKey(f"TAX_AGENT_API_KEY is empty in {path}; paste the workspace key there")
    return HarnessSettings(
        api_key=key,
        spend_cap_usd=float(values.get("SPEND_CAP_USD", "150")),
        run_cap_usd=float(values.get("RUN_CAP_USD", "0.50")),
    )


def child_env(settings: HarnessSettings, config_dir: Path) -> dict[str, str]:
    """Environment overrides for one Agent SDK child process.

    The SDK merges these over the inherited environment, and explicit values
    win, so this replaces any ANTHROPIC_API_KEY or ANTHROPIC_BASE_URL the
    parent shell exported. CLAUDE_CONFIG_DIR points at an empty folder so
    the child can't read the developer's Claude Code login, settings,
    plugins, or MCP config. Overrides applied after this (the dry runs'
    closed port) still win.
    """
    env = {name: "" for name in BLANKED}
    env["ANTHROPIC_API_KEY"] = settings.api_key
    env["ANTHROPIC_BASE_URL"] = API_BASE_URL
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    # No telemetry, error reports, or update checks from eval children.
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def messages_client(api_key: str, *, max_retries: int = 4) -> anthropic.AsyncAnthropic:
    """An async Messages API client on the workspace key, pinned to API_BASE_URL.

    The anthropic client also reads ANTHROPIC_BASE_URL and
    ANTHROPIC_CUSTOM_HEADERS from os.environ; an explicit base_url wins, and
    each header named in ANTHROPIC_CUSTOM_HEADERS is dropped.
    """
    inherited = os.environ.get("ANTHROPIC_CUSTOM_HEADERS") or ""
    names = [line.split(":", 1)[0].strip() for line in inherited.splitlines() if ":" in line]
    return anthropic.AsyncAnthropic(
        api_key=api_key,
        base_url=API_BASE_URL,
        max_retries=max_retries,
        default_headers={name: anthropic.omit for name in names if name},
    )
