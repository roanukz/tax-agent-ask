"""Harness settings from .env, without ever putting the key in os.environ.

Claude Code switches its own session to ANTHROPIC_API_KEY when it finds that
variable, so the key is stored as TAX_AGENT_API_KEY and handed only to the
harness's own child processes and API clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DOTENV = REPO / ".env"

# Variables that could give a child Claude Code process a credential other
# than the workspace key, or send it to another provider. Blanked per run.
COMPETING_CREDENTIALS = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)


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
    win, so this replaces any ANTHROPIC_API_KEY the parent shell exported.
    CLAUDE_CONFIG_DIR points at an empty folder so the child can't read the
    developer's Claude Code login, settings, plugins, or MCP config.
    """
    env = {name: "" for name in COMPETING_CREDENTIALS}
    env["ANTHROPIC_API_KEY"] = settings.api_key
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    # No telemetry, error reports, or update checks from eval children.
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env
