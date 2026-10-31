import os

import pytest

from agent.env import COMPETING_CREDENTIALS, HarnessSettings, MissingKey, child_env, load_settings, read_dotenv
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
    assert opts.max_budget_usd == 0.5
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
