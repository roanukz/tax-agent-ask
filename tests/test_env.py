import os
from pathlib import Path

import anthropic
import pytest
from anthropic._models import FinalRequestOptions

from agent.env import (
    API_BASE_URL,
    BLANKED,
    COMPETING_CREDENTIALS,
    PROVIDER_SWITCHES,
    REQUEST_ROUTING,
    HarnessSettings,
    MissingKey,
    SDK_OWNED,
    child_env,
    inherited_blanks,
    load_settings,
    messages_client,
    read_dotenv,
)
from agent.guard import ToolListViolation, WrongCredential, check_init, unexpected_tools
from agent.options import BUILT_IN_TOOLS, isolated_options

FAKE_KEY = "test-key-not-real"


def write(tmp_path, text):
    p = tmp_path / ".env"
    p.write_text(text)
    return p


def test_read_dotenv_handles_comments_export_and_quotes(tmp_path):
    p = write(tmp_path, '# comment\n\nexport A=1\nB="two"\nC=\'three\'\nD=\nnot a pair\n')
    assert read_dotenv(p) == {"A": "1", "B": "two", "C": "three", "D": ""}


def test_missing_key_raises(tmp_path):
    p = write(tmp_path, "TAX_AGENT_API_KEY=\nSPEND_CAP_USD=150\n")
    with pytest.raises(MissingKey):
        load_settings(p)
    assert load_settings(p, require_key=False).api_key == ""


def test_settings_defaults_and_values(tmp_path):
    p = write(tmp_path, f"TAX_AGENT_API_KEY={FAKE_KEY}\nRUN_CAP_USD=0.25\n")
    s = load_settings(p)
    assert s.api_key == FAKE_KEY
    assert s.spend_cap_usd == 150.0
    assert s.run_cap_usd == 0.25
    assert load_settings(write(tmp_path, f"TAX_AGENT_API_KEY={FAKE_KEY}\n")).run_cap_usd == 2.0


def test_env_example_matches_the_defaults():
    example = read_dotenv(Path(__file__).resolve().parent.parent / ".env.example")
    assert example == {"TAX_AGENT_API_KEY": "", "SPEND_CAP_USD": "150", "RUN_CAP_USD": "2.00"}


def test_settings_repr_hides_key():
    s = HarnessSettings(api_key=FAKE_KEY, spend_cap_usd=150, run_cap_usd=0.5)
    assert FAKE_KEY not in repr(s)


def test_child_env_maps_key_without_touching_parent_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-shell-value")
    before = dict(os.environ)
    s = HarnessSettings(api_key=FAKE_KEY, spend_cap_usd=150, run_cap_usd=0.5)
    env = child_env(s, tmp_path / "cfg")
    assert env["ANTHROPIC_API_KEY"] == FAKE_KEY
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "cfg")
    for name in COMPETING_CREDENTIALS:
        assert env[name] == ""
    assert dict(os.environ) == before
    assert "TAX_AGENT_API_KEY" not in env


# Every provider switch and routing variable in the bundled CLI 2.1.314's strings.
CLI_SWITCHES = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_GATEWAY",
                "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD")


def test_child_env_pins_the_api_host_and_blanks_every_provider_switch(tmp_path, monkeypatch):
    for name in (*CLI_SWITCHES, "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_UNIX_SOCKET"):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://redirect.invalid")
    s = HarnessSettings(api_key=FAKE_KEY, spend_cap_usd=150, run_cap_usd=0.5)
    env = child_env(s, tmp_path / "cfg")
    assert env["ANTHROPIC_BASE_URL"] == API_BASE_URL == "https://api.anthropic.com"
    assert set(CLI_SWITCHES) <= set(PROVIDER_SWITCHES)
    assert {"ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_UNIX_SOCKET"} <= set(REQUEST_ROUTING)
    for name in BLANKED:
        assert env[name] == "", f"{name} reaches the child from the parent shell"
    closed = isolated_options(model="claude-haiku-5-5", settings=s, run_dir=tmp_path, system_prompt="x",
                              allowed_tools=[], env_overrides={"ANTHROPIC_BASE_URL": "http://127.0.0.1:9"})
    assert closed.env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9", "a dry run's closed port still wins"


def test_child_env_blanks_what_a_parent_claude_code_session_or_shell_would_pass_down(tmp_path, monkeypatch):
    for name in ("CLAUDE_EFFORT", "CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING", "ANTHROPIC_MODEL",
                 "MAX_THINKING_TOKENS", "DISABLE_PROMPT_CACHING", "MCP_TIMEOUT"):
        monkeypatch.setenv(name, "1")
    for name in SDK_OWNED:
        monkeypatch.setenv(name, "parent")
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "0")
    s = HarnessSettings(api_key=FAKE_KEY, spend_cap_usd=150, run_cap_usd=0.5)
    env = child_env(s, tmp_path / "cfg")
    for name in ("CLAUDE_EFFORT", "CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING", "ANTHROPIC_MODEL",
                 "MAX_THINKING_TOKENS", "DISABLE_PROMPT_CACHING", "MCP_TIMEOUT"):
        assert env[name] == "", f"{name} reaches the child from the parent"
    assert not set(SDK_OWNED) & set(env), "the SDK sets these on the child itself"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1" and env["ANTHROPIC_API_KEY"] == FAKE_KEY
    assert "PATH" not in env and "HOME" not in env
    assert set(inherited_blanks({"PATH": "/bin", "CLAUDECODE": "1", "TERM": "x"})) == set()


def built_request(client: anthropic.AsyncAnthropic):
    """The request the client would send for a Messages call, built without sending it."""
    return client._build_request(FinalRequestOptions.construct(method="post", url="/v1/messages", json_data={}))


def test_messages_client_is_pinned_to_the_api_host_whatever_the_parent_shell_says(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://redirect.invalid")
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "X-Route-To: elsewhere\nX-Other: 1")
    plain = built_request(anthropic.AsyncAnthropic(api_key=FAKE_KEY))
    assert plain.url.host == "redirect.invalid" and "x-route-to" in plain.headers, "what a bare client would do"
    client = messages_client(FAKE_KEY)
    request = built_request(client)
    assert str(request.url) == "https://api.anthropic.com/v1/messages" and client.max_retries == 4
    assert "x-route-to" not in request.headers and "x-other" not in request.headers
    assert messages_client(FAKE_KEY, max_retries=0).max_retries == 0
    # sim.budget.BudgetedClient sends each call through a copy with its middleware, which keeps the same pins.
    async def passthrough(request, call_next):
        return await call_next(request)

    guarded = built_request(client.with_middleware(passthrough))
    assert str(guarded.url) == "https://api.anthropic.com/v1/messages" and "x-route-to" not in guarded.headers


@pytest.mark.parametrize("factory", ["eval.run:live_client", "eval.judge:default_client", "sim.fidelity:live_client"])
def test_every_in_process_client_factory_is_pinned_to_the_api_host(monkeypatch, factory):
    import importlib

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://redirect.invalid")
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "X-Route-To: elsewhere")
    module, name = factory.split(":")
    client = getattr(importlib.import_module(module), name)(FAKE_KEY)
    request = built_request(client)
    assert str(request.url) == "https://api.anthropic.com/v1/messages" and "x-route-to" not in request.headers
    assert client.max_retries == 4


def test_isolated_options_remove_everything_but_allowed(tmp_path):
    s = HarnessSettings(api_key=FAKE_KEY, spend_cap_usd=150, run_cap_usd=0.5)
    opts = isolated_options(
        model="claude-haiku-5-5",
        settings=s,
        run_dir=tmp_path,
        system_prompt="x",
        allowed_tools=["mcp__harness__ask_client"],
    )
    assert opts.tools == []
    assert opts.setting_sources == []
    assert opts.strict_mcp_config is True
    assert opts.allowed_tools == ["mcp__harness__ask_client"]
    assert set(BUILT_IN_TOOLS) <= set(opts.disallowed_tools)
    assert opts.permission_mode == "dontAsk"
    assert opts.max_budget_usd is None, "a CLI budget would put a running USD budget in the agent's prompt"
    assert opts.include_partial_messages is True
    assert opts.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "8192"
    assert opts.max_turns == 40
    assert opts.env["ANTHROPIC_API_KEY"] == FAKE_KEY
    cwd = tmp_path / "cwd"
    assert opts.cwd == str(cwd) and cwd.is_dir() and not any(cwd.iterdir())
    assert not any((tmp_path / "claude-config").iterdir())


def test_guard_accepts_clean_init():
    check_init({"tools": ["mcp__tax__record_fact"], "apiKeySource": "ANTHROPIC_API_KEY"}, ["mcp__tax__record_fact"])


def test_guard_rejects_planted_built_in_tool():
    init = {"tools": ["mcp__tax__record_fact", "Bash"], "apiKeySource": "ANTHROPIC_API_KEY"}
    assert unexpected_tools(init, ["mcp__tax__record_fact"]) == ["Bash"]
    with pytest.raises(ToolListViolation):
        check_init(init, ["mcp__tax__record_fact"])


@pytest.mark.parametrize("source", ["none", "/login managed key", "apiKeyHelper", None])
def test_guard_rejects_other_credentials(source):
    with pytest.raises(WrongCredential):
        check_init({"tools": [], "apiKeySource": source}, [])
