"""Options for one isolated Agent SDK run.

The agent under test gets no built-in tools, no settings or CLAUDE.md, no MCP
servers beyond the ones the run passes in, an empty working folder, and an
empty Claude Code config folder.

Every call's output is capped at max_output_tokens (the CLI's
CLAUDE_CODE_MAX_OUTPUT_TOKENS, sent as max_tokens), and the CLI streams each
API event (include_partial_messages), since the AssistantMessage usage it
sends carries only the stream's opening count, an output of 1 token, while
the real output arrives in the message_delta event. The CLI gets no
max_budget_usd: with one, it shows the agent a running USD budget in its
environment block and after every turn, which would differ by model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions

from agent.configs import DEFAULT_MAX_OUTPUT_TOKENS
from agent.env import HarnessSettings, child_env

# A backstop only: tools=[] already removes the built-in set.
BUILT_IN_TOOLS = (
    "Agent",
    "Bash",
    "BashOutput",
    "Edit",
    "ExitPlanMode",
    "Glob",
    "Grep",
    "KillShell",
    "MultiEdit",
    "NotebookEdit",
    "Read",
    "Skill",
    "SlashCommand",
    "Task",
    "TodoWrite",
    "ToolSearch",
    "WebFetch",
    "WebSearch",
    "Write",
)


def isolated_options(
    *,
    model: str,
    settings: HarnessSettings,
    run_dir: Path,
    system_prompt: str,
    allowed_tools: list[str],
    mcp_servers: dict[str, Any] | None = None,
    hooks: dict[str, Any] | None = None,
    max_turns: int = 40,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    env_overrides: dict[str, str] | None = None,
) -> ClaudeAgentOptions:
    cwd = Path(run_dir) / "cwd"
    config_dir = Path(run_dir) / "claude-config"
    cwd.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    env = child_env(settings, config_dir)
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(int(max_output_tokens))
    env.update(env_overrides or {})
    extra: dict[str, Any] = {}
    if hooks:
        extra["hooks"] = hooks
    return ClaudeAgentOptions(
        model=model,
        system_prompt=system_prompt,
        tools=[],
        allowed_tools=list(allowed_tools),
        disallowed_tools=list(BUILT_IN_TOOLS),
        setting_sources=[],
        strict_mcp_config=True,
        mcp_servers=mcp_servers or {},
        permission_mode="dontAsk",
        cwd=str(cwd),
        env=env,
        max_turns=max_turns,
        include_partial_messages=True,
        **extra,
    )
