"""Preparer review screen, Block 2: the MCP server "preparer".

The tool-list snapshot (five tools, two app-only, no send tool), the ui:// resource, the
text a model or a text-only client gets, every refusal in its order (no MCP Apps, a stale or
wrong token, a changed SHA-256, invalid decisions, an approved draft), review records with the
server's times, the stderr log, the truth guard, the path guard, no model client, the harness
never connecting, and a stdio client.

The tests copy the committed demo drafts into tmp_path. After an intended change to the tool
list, regenerate the snapshot with

    UPDATE_SNAPSHOTS=1 uv run pytest tests/test_preparer_server.py -k snapshot

and review the diff of tests/snapshots/preparer_tool_list.json before committing it.
"""

from __future__ import annotations

import ast
import builtins
import io
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import anyio
import pytest
from mcp import Client
from mcp.client import advertise
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import Implementation

from preparer import server as ps
from preparer.drafts import Reference, compute_flags, draft_digest, dump
from rules.engine import DOCUMENT_RULES_PATH, FACTS_PATH

REPO = Path(__file__).resolve().parent.parent
DEMO = REPO / "preparer" / "app" / "demo_drafts"
SNAPSHOT = REPO / "tests" / "snapshots" / "preparer_tool_list.json"
HTML = "<!doctype html><title>Preparer review</title><p>view</p>"
APPS = advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})
WRONG_MIME = advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html"]})
HOST = Implementation(name="claude-desktop-test", version="9.9")
T0 = datetime(2026, 11, 8, 15, 0, 0, tzinfo=timezone.utc)
SENDING_WORDS = re.compile(
    r"\b(send\w*|sent|e-?mail\w*|mail\w*|deliver\w*|dispatch\w*|transmit\w*|forward\w*|notif\w*|"
    r"share\w*|post|posts|posted|posting|fax\w*|upload\w*|text the client|message the client)\b",
    re.IGNORECASE,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def tick(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def env(tmp_path):
    drafts = tmp_path / "drafts"
    shutil.copytree(DEMO, drafts)
    counter = iter(range(1000))
    clock, logs = Clock(), []
    server = ps.build_server(drafts, tmp_path / "reviews", html=HTML, clock=clock, log=logs.append,
                             token_factory=lambda: f"token-{next(counter):03d}")
    return {"server": server, "drafts": drafts, "reviews": tmp_path / "reviews", "clock": clock, "logs": logs, "tmp": tmp_path}


def apps_client(server):
    return Client(server, extensions=[APPS], client_info=HOST)


def plain_client(server):
    return Client(server, client_info=Implementation(name="claude-code", version="2.1"))


def text(result) -> str:
    return result.content[0].text


def keep_all(opened: dict[str, Any], **changes: Any) -> dict[str, Any]:
    decisions = {
        "documents": [{"rule_id": d["rule_id"], "action": "keep"} for d in opened["draft"]["documents"]],
        "custom_requests": [{"index": i, "action": "keep"} for i, _ in enumerate(opened["draft"]["custom_requests"])],
        "added": [], "follow_ups": [], "flags_checked": [f["id"] for f in opened["flags"]],
    }
    decisions.update(changes)
    return decisions


def args(opened: dict[str, Any], decisions: dict[str, Any], draft_id: str = "demo1", **extra: Any) -> dict[str, Any]:
    out = {"draft_id": draft_id, "review_token": opened["review_token"], "draft_sha256": opened["draft_sha256"], "decisions": decisions}
    out.update(extra)
    return out


# The tool list.


def listing_entry(tool: Any) -> dict[str, Any]:
    d = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    return {k: d[k] for k in ("name", "title", "description", "annotations", "inputSchema", "_meta") if k in d}


@pytest.mark.anyio
async def test_five_tools_two_app_only_and_the_snapshot(env):
    async with apps_client(env["server"]) as c:
        listing = [listing_entry(t) for t in (await c.list_tools()).tools]
    assert [t["name"] for t in listing] == list(ps.TOOL_NAMES)
    vis = {t["name"]: t.get("_meta", {}).get("ui", {}).get("visibility") for t in listing}
    assert vis == {"list_drafts": None, "open_review": None, "save_review": ["app"], "approve_review": ["app"], "approved_list": None}
    uris = {t["name"]: t.get("_meta", {}).get("ui", {}).get("resourceUri") for t in listing}
    assert uris == {"list_drafts": None, "open_review": ps.VIEW_URI, "save_review": ps.VIEW_URI,
                    "approve_review": ps.VIEW_URI, "approved_list": None}
    annotations = {t["name"]: t["annotations"] for t in listing}
    assert annotations["list_drafts"]["readOnlyHint"] and annotations["approved_list"]["readOnlyHint"]
    for name in ("open_review", "save_review", "approve_review"):
        assert annotations[name]["readOnlyHint"] is False and annotations[name]["destructiveHint"] is False
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.write_text(json.dumps(listing, indent=2) + "\n")
    assert SNAPSHOT.exists(), "no snapshot yet; run with UPDATE_SNAPSHOTS=1"
    assert listing == json.loads(SNAPSHOT.read_text()), "tool list changed; see this module's docstring to regenerate"


def test_no_tool_offers_sending_and_none_takes_free_text():
    listing = json.loads(SNAPSHOT.read_text())
    hits = {t["name"]: SENDING_WORDS.findall(json.dumps(t)) for t in listing}
    assert not any(hits.values()), hits
    assert len(listing) == 5
    for tool in listing:
        props = tool["inputSchema"].get("properties", {})
        assert set(props) <= {"draft_id", "review_token", "draft_sha256", "decisions", "host"}, tool["name"]


@pytest.mark.anyio
async def test_the_view_resource_is_an_mcp_app_with_one_origin_and_clipboard(env):
    async with apps_client(env["server"]) as c:
        resources = (await c.list_resources()).resources
        read = await c.read_resource(ps.VIEW_URI)
    assert [str(r.uri) for r in resources] == [ps.VIEW_URI]
    content = read.contents[0]
    assert content.mime_type == "text/html;profile=mcp-app" and content.text == HTML
    meta = content.meta or resources[0].meta
    assert meta == {"ui": {"csp": {"resourceDomains": ["https://unpkg.com"]}, "permissions": {"clipboardWrite": {}}}}


# Text for the model, and for clients without MCP Apps.


@pytest.mark.anyio
async def test_list_drafts_gives_ids_clients_counts_and_status(env):
    async with plain_client(env["server"]) as c:
        listed = text(await c.call_tool("list_drafts", {}))
    lines = listed.splitlines()
    assert [line.split(":")[0] for line in lines] == ["demo1", "demo2", "demo3"]
    assert all(line.endswith("pending_review") and "Demo Client" in line for line in lines)


@pytest.mark.anyio
async def test_open_review_gives_text_only_to_a_client_without_mcp_apps(env):
    for client in (plain_client(env["server"]), Client(env["server"], extensions=[WRONG_MIME])):
        async with client as c:
            result = await c.call_tool("open_review", {"draft_id": "demo1"})
        assert not result.is_error and result.structured_content is None
        assert "can be read here but not approved" in text(result)
        assert "token" not in text(result).lower()


@pytest.mark.anyio
async def test_open_review_gives_an_apps_client_the_draft_flags_and_a_token(env):
    async with apps_client(env["server"]) as c:
        result = await c.call_tool("open_review", {"draft_id": "demo1"})
    sc = result.structured_content
    assert sorted(sc) == ["addable", "app_version", "count_rules", "draft", "draft_sha256", "facts", "flags", "open_number",
                          "reasons", "record", "review_token", "status"]
    assert sc["count_rules"] == {"R001": {"fact": "w2_employer_count", "min": 1, "max": 10},
                                 "R020": {"fact": "k1_count", "min": 1, "max": 10}}
    draft = json.loads((env["drafts"] / "demo1.json").read_text())
    assert sc["draft"] == draft and sc["draft_sha256"] == draft["draft_sha256"] == draft_digest(draft)
    assert sc["flags"] == compute_flags(draft, Reference.load())
    assert sc["review_token"] == "token-000" and sc["status"] == "pending_review" and sc["record"] is None
    body = text(result)
    assert "token-000" not in body and draft["draft_sha256"] not in body
    assert "Approval can't happen in chat" in body
    for f in sc["flags"]:
        assert f["id"] in body
    assert len(json.dumps(result.model_dump(by_alias=True))) < 150_000


@pytest.mark.anyio
async def test_every_opening_is_logged_and_numbered(env):
    async with apps_client(env["server"]) as c:
        first = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
        env["clock"].tick(30)
        second = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
        other = (await c.call_tool("open_review", {"draft_id": "demo2"})).structured_content
    async with plain_client(env["server"]) as c:
        await c.call_tool("open_review", {"draft_id": "demo3"})
        await c.call_tool("open_review", {"draft_id": "d99"})
    assert (first["open_number"], second["open_number"], other["open_number"]) == (1, 2, 1)
    lines = (env["reviews"] / "openings.jsonl").read_text().splitlines()
    sha1 = first["draft_sha256"]
    assert [json.loads(line) for line in lines] == [
        {"draft_id": "demo1", "draft_sha256": sha1, "opened_at": "2026-11-08T15:00:00Z"},
        {"draft_id": "demo1", "draft_sha256": sha1, "opened_at": "2026-11-08T15:00:30Z"},
        {"draft_id": "demo2", "draft_sha256": other["draft_sha256"], "opened_at": "2026-11-08T15:00:30Z"},
        {"draft_id": "demo3", "draft_sha256": json.loads((env["drafts"] / "demo3.json").read_text())["draft_sha256"],
         "opened_at": "2026-11-08T15:00:30Z"},
    ]
    assert "token" not in (env["reviews"] / "openings.jsonl").read_text()


@pytest.mark.anyio
async def test_open_review_refuses_bad_ids_plainly(env):
    async with apps_client(env["server"]) as c:
        for bad in ("d99", "../truth/H001", "demo1.json", ""):
            result = await c.call_tool("open_review", {"draft_id": bad})
            assert result.is_error and text(result).startswith("Refused: ")
            assert "Error executing tool" not in text(result)


# Refusals, in the server's order.


@pytest.mark.anyio
async def test_a_client_without_mcp_apps_can_never_save_or_approve(env):
    async with apps_client(env["server"]) as c:
        opened = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
    for client in (plain_client(env["server"]), Client(env["server"], extensions=[WRONG_MIME])):
        async with client as c:
            for tool in ("save_review", "approve_review"):
                result = await c.call_tool(tool, args(opened, keep_all(opened)))
                assert result.is_error and text(result) == ps.NOT_APPS
    assert not list(env["reviews"].glob("*.json")), "no record was written"


@pytest.mark.anyio
async def test_a_stale_wrong_or_borrowed_token_is_refused(env):
    async with apps_client(env["server"]) as c:
        first = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
        second = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
        other = (await c.call_tool("open_review", {"draft_id": "demo2"})).structured_content
        stale = await c.call_tool("save_review", args(first, keep_all(first)))
        wrong = await c.call_tool("save_review", {**args(second, keep_all(second)), "review_token": "token-999"})
        borrowed = await c.call_tool("save_review", {**args(second, keep_all(second)), "review_token": other["review_token"]})
        ok = await c.call_tool("save_review", args(second, keep_all(second)))
    for result in (stale, wrong, borrowed):
        assert result.is_error and text(result) == ps.STALE_TOKEN
    assert not ok.is_error


@pytest.mark.anyio
async def test_a_restarted_server_has_no_tokens(env):
    async with apps_client(env["server"]) as c:
        opened = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
    restarted = ps.build_server(env["drafts"], env["reviews"], html=HTML, token_factory=lambda: "token-000")
    async with apps_client(restarted) as c:
        result = await c.call_tool("save_review", args(opened, keep_all(opened)))
    assert result.is_error and text(result) == ps.STALE_TOKEN


@pytest.mark.anyio
async def test_a_changed_sha_or_draft_is_refused(env):
    async with apps_client(env["server"]) as c:
        opened = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
        wrong = await c.call_tool("save_review", {**args(opened, keep_all(opened)), "draft_sha256": "0" * 64})
        path = env["drafts"] / "demo1.json"
        draft = json.loads(path.read_text())
        draft["agent_note"] += " Edited."
        draft["draft_sha256"] = draft_digest(draft)
        path.write_text(dump(draft))
        changed = await c.call_tool("save_review", args(opened, keep_all(opened)))
        old_token_new_sha = await c.call_tool("save_review", {**args(opened, keep_all(opened)), "draft_sha256": draft["draft_sha256"]})
        reopened = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
        ok = await c.call_tool("save_review", args(reopened, keep_all(reopened)))
    assert text(wrong) == ps.CHANGED and text(changed) == ps.CHANGED and not ok.is_error
    assert text(old_token_new_sha) == ps.CHANGED, "a token is bound to the SHA-256 it was minted for"


@pytest.mark.anyio
async def test_a_review_saved_for_another_version_is_refused_everywhere(env):
    async with apps_client(env["server"]) as c:
        opened = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
        await c.call_tool("approve_review", args(opened, keep_all(opened)))
        before = (env["reviews"] / "demo1.json").read_bytes()
        path = env["drafts"] / "demo1.json"
        draft = json.loads(path.read_text())
        draft["agent_note"] += " Edited."
        draft["draft_sha256"] = draft_digest(draft)
        path.write_text(dump(draft))
        reopened = await c.call_tool("open_review", {"draft_id": "demo1"})
        listed = await c.call_tool("approved_list", {"draft_id": "demo1"})
        rows = text(await c.call_tool("list_drafts", {}))
    assert text(reopened) == text(listed) == "Refused: the saved review was for another version of this draft"
    assert "demo1: can't be read (the saved review was for another version of this draft)" in rows
    assert (env["reviews"] / "demo1.json").read_bytes() == before


@pytest.mark.anyio
async def test_simultaneous_saves_and_approvals_never_lose_or_break_a_record(env):
    """An approval and a save sent at once, 40 times over, each time on a fresh reviews folder: the approval
    always lands, the record always parses, and the save either landed before it or was refused as final."""
    for i in range(40):
        reviews = env["tmp"] / f"race-{i}"
        server = ps.build_server(env["drafts"], reviews, html=HTML, log=lambda line: None)
        async with apps_client(server) as c:
            opened = (await c.call_tool("open_review", {"draft_id": "demo3"})).structured_content
            results = {}

            async def call(tool):
                results[tool] = await c.call_tool(tool, args(opened, keep_all(opened), draft_id="demo3"))

            async with anyio.create_task_group() as tg:
                tg.start_soon(call, "approve_review")
                tg.start_soon(call, "save_review")
            record = json.loads((reviews / "demo3.json").read_text())
            assert not results["approve_review"].is_error and record["status"] == "approved"
            assert not results["save_review"].is_error or text(results["save_review"]) == ps.FINAL
            assert sorted(p.name for p in reviews.iterdir() if p.name not in (".lock", "openings.jsonl")) == ["demo3.json"]


@pytest.mark.anyio
async def test_invalid_or_incomplete_decisions_are_refused(env):
    async with apps_client(env["server"]) as c:
        opened = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
        bad = await c.call_tool("save_review", args(opened, keep_all(opened, notes="please call me")))
        incomplete = await c.call_tool("approve_review", args(opened, keep_all(opened, flags_checked=[])))
        bad_host = await c.call_tool("save_review", args(opened, keep_all(opened), host={"name": ""}))
    assert bad.is_error and text(bad).startswith("Refused: decisions must have the keys")
    assert incomplete.is_error and text(incomplete).startswith("Refused: approval needs a check on ")
    assert bad_host.is_error and "host must" in text(bad_host)


@pytest.mark.anyio
async def test_both_protocol_eras_negotiate_the_screen_and_refuse_a_text_client(env):
    # The in-memory client above speaks the 2026 per-request era. Claude Desktop's log shows it can pin a
    # local server to the initialize handshake, so the same flow runs there too.
    async with Client(env["server"], mode="legacy", extensions=[APPS], client_info=HOST) as c:
        assert c.protocol_version == "2025-11-25"
        opened = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
        saved = await c.call_tool("save_review", args(opened, keep_all(opened)))
        approved = await c.call_tool("approve_review", args(opened, keep_all(opened)))
    async with Client(env["server"], mode="legacy", client_info=HOST) as c:
        plain = await c.call_tool("open_review", {"draft_id": "demo2"})
        refused = await c.call_tool("approve_review", {"draft_id": "demo2", "review_token": "x", "draft_sha256": "y", "decisions": {}})
    assert not saved.is_error and not approved.is_error
    assert json.loads((env["reviews"] / "demo1.json").read_text())["status"] == "approved"
    assert plain.structured_content is None and text(refused) == ps.NOT_APPS
    assert '"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}' in env["logs"][0]


@pytest.mark.anyio
async def test_approval_is_final(env):
    async with apps_client(env["server"]) as c:
        opened = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
        approved = await c.call_tool("approve_review", args(opened, keep_all(opened)))
        again = await c.call_tool("approve_review", args(opened, keep_all(opened)))
        reopened = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
        save = await c.call_tool("save_review", args(reopened, keep_all(reopened)))
    assert not approved.is_error
    assert text(again) == ps.FINAL and text(save) == ps.FINAL
    assert reopened["status"] == "approved" and reopened["record"]["status"] == "approved"


@pytest.mark.anyio
async def test_refusals_follow_the_prd_order(env):
    """Each refusal wins over every later one: apps, then token, then SHA-256, then decisions, then approved."""
    async with apps_client(env["server"]) as c:
        opened = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
        await c.call_tool("approve_review", args(opened, keep_all(opened)))
        everything_wrong = {**args(opened, {"bad": 1}), "review_token": "nope", "draft_sha256": "0" * 64}
        token_bad = await c.call_tool("save_review", everything_wrong)
        sha_bad = await c.call_tool("save_review", {**everything_wrong, "review_token": opened["review_token"]})
        decisions_bad = await c.call_tool("save_review", {**args(opened, {"bad": 1})})
        approved = await c.call_tool("save_review", args(opened, keep_all(opened)))
    async with plain_client(env["server"]) as c:
        apps_bad = await c.call_tool("save_review", everything_wrong)
    assert [text(r) for r in (apps_bad, token_bad, sha_bad, approved)] == [ps.NOT_APPS, ps.STALE_TOKEN, ps.CHANGED, ps.FINAL]
    assert text(decisions_bad).startswith("Refused: decisions must have the keys")


# Records and the approved list.


@pytest.mark.anyio
async def test_save_reopen_and_approve_keep_the_record_and_its_times(env):
    clock = env["clock"]
    async with apps_client(env["server"]) as c:
        opened = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
        clock.tick(40)
        partial = keep_all(opened, documents=[], flags_checked=[])
        saved = await c.call_tool("save_review", args(opened, partial, host={"name": "Claude", "version": "4.23118.0"}))
        clock.tick(600)
        reopened = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
        clock.tick(25)
        decisions = keep_all(reopened, follow_ups=[{"fact": "w2_employer_count"}])
        approved = await c.call_tool("approve_review", args(reopened, decisions))
        listed = text(await c.call_tool("approved_list", {"draft_id": "demo1"}))
        rows = text(await c.call_tool("list_drafts", {}))
    assert saved.structured_content["record"]["status"] == "in_review"
    assert reopened["status"] == "in_review" and reopened["record"]["decisions"] == partial
    record = json.loads((env["reviews"] / "demo1.json").read_text())
    assert record == approved.structured_content["record"]
    assert record["status"] == "approved" and record["decisions"] == decisions
    assert record["opened_at"] == "2026-11-08T15:00:00Z" and record["saved_at"] == ["2026-11-08T15:00:40Z"]
    assert record["approved_at"] == "2026-11-08T15:11:05Z" and record["seconds_on_screen"] == 40 + 25
    assert record["host"] == {"name": "Claude", "version": "4.23118.0"}
    assert record["client"] == {"name": "claude-desktop-test", "version": "9.9"}
    assert record["follow_up_questions"][0]["fact"] == "w2_employer_count"
    assert "Follow-up questions for the client:" in listed and "stays with the preparer" in listed
    assert rows.splitlines()[0].endswith("approved")


@pytest.mark.anyio
async def test_approved_list_before_approval_says_so(env):
    async with plain_client(env["server"]) as c:
        result = await c.call_tool("approved_list", {"draft_id": "demo2"})
    assert not result.is_error and text(result) == "Draft demo2 is not approved yet (status: pending_review)."


# The stderr log.


@pytest.mark.anyio
async def test_the_first_call_logs_the_client_and_refusals_are_logged(env):
    async with apps_client(env["server"]) as c:
        await c.call_tool("list_drafts", {})
        opened = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
        await c.call_tool("save_review", {"draft_id": "demo1", "review_token": "x", "draft_sha256": "y", "decisions": {}})
        await c.call_tool("save_review", args(opened, {"bad": 1}))
    logs = env["logs"]
    assert logs[0] == ('preparer: client {"name": "claude-desktop-test", "version": "9.9"} advertised extensions '
                       '{"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}')
    assert logs[1] == f"preparer: refused save_review for 'demo1': {ps.STALE_TOKEN}"
    assert logs[2].startswith("preparer: refused save_review for 'demo1': Refused: decisions must have the keys")
    assert len(logs) == 3 and opened["review_token"] == "token-000"
    assert all("token-000" not in line and opened["draft_sha256"] not in line for line in logs)


# Folders, paths, imports.


def test_the_server_refuses_truth_folders(tmp_path):
    with pytest.raises(ValueError, match="truth"):
        ps.build_server(REPO / "data" / "truth", tmp_path / "r", html=HTML)
    with pytest.raises(ValueError, match="truth"):
        ps.build_server(DEMO, REPO / "data" / "truth", html=HTML)
    (tmp_path / "Truth").mkdir()
    with pytest.raises(ValueError, match="truth"):
        ps.build_server(tmp_path / "Truth", tmp_path / "r", html=HTML)
    (tmp_path / "Truth" / "review.html").write_text(HTML)
    with pytest.raises(ValueError, match="view folder can't be inside a truth folder"):
        ps.build_server(DEMO, tmp_path / "r", html_path=tmp_path / "Truth" / "review.html")


@contextmanager
def watching_opens(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Path]]:
    seen: list[Path] = []

    def wrap(real):
        def opener(target, *a, **kw):
            if not isinstance(target, int):
                seen.append(Path(os.fsdecode(os.fspath(target))))
            return real(target, *a, **kw)
        return opener

    with monkeypatch.context() as m:
        m.setattr(builtins, "open", wrap(builtins.open))
        m.setattr(io, "open", wrap(io.open))
        m.setattr(os, "open", wrap(os.open))
        for name in ("open", "read_text", "read_bytes", "write_text", "write_bytes"):
            m.setattr(Path, name, wrap(getattr(Path, name)))
        yield seen


@pytest.mark.anyio
async def test_the_server_opens_only_its_folders_the_rules_and_the_view(tmp_path, monkeypatch):
    drafts, reviews, html = tmp_path / "drafts", tmp_path / "reviews", tmp_path / "review.html"
    shutil.copytree(DEMO, drafts)
    html.write_text(HTML)
    (drafts / "demo4.json").symlink_to(REPO / "data" / "truth" / "H001.json")
    with watching_opens(monkeypatch) as seen:
        server = ps.build_server(drafts, reviews, html_path=html)
        async with apps_client(server) as c:
            await c.call_tool("list_drafts", {})
            opened = (await c.call_tool("open_review", {"draft_id": "demo1"})).structured_content
            await c.call_tool("save_review", args(opened, keep_all(opened)))
            await c.call_tool("approve_review", args(opened, keep_all(opened)))
            await c.call_tool("approved_list", {"draft_id": "demo1"})
            refused = await c.call_tool("open_review", {"draft_id": "demo4"})
            await c.read_resource(ps.VIEW_URI)
    roots = (drafts.resolve(), reviews.resolve())
    files = {FACTS_PATH.resolve(), DOCUMENT_RULES_PATH.resolve(), html.resolve()}
    outside = [p for p in seen if not (p.resolve() in files or any(p.resolve().is_relative_to(r) for r in roots))]
    assert outside == []
    assert not any("truth" in p.parts for p in seen)
    assert refused.is_error and "not a regular file" in text(refused)


PREPARER_IMPORTS = re.compile(r"^(preparer)(\.|$)")


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_harness_never_connects_to_the_preparer_server():
    from agent import harness, wiring_check

    assert harness.MCP_SERVERS == ("tax", "harness") and wiring_check.SERVERS == ("tax", "harness")
    for folder in ("agent", "server", "sim", "rules", "generator", "review"):
        for path in (REPO / folder).rglob("*.py"):
            assert not any(PREPARER_IMPORTS.match(n) for n in imports_of(path)), path
    assert not any(PREPARER_IMPORTS.match(n) for n in imports_of(REPO / "eval" / "run.py"))


def test_the_preparer_never_imports_the_eval_side_or_a_model_client():
    for path in (REPO / "preparer").rglob("*.py"):
        for name in imports_of(path):
            top = name.split(".")[0]
            assert top not in ("eval", "agent", "sim", "anthropic", "claude_agent_sdk", "openai", "generator"), (path, name)


def test_importing_the_server_loads_no_model_client():
    code = (
        "import sys, preparer.server\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('anthropic', 'claude_agent_sdk', 'openai', 'eval', 'agent')))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


# Over stdio, as Claude Desktop runs it.


@pytest.mark.anyio
async def test_stdio_server_lists_tools_and_answers_a_text_only_client(tmp_path):
    drafts, reviews, html = tmp_path / "drafts", tmp_path / "reviews", tmp_path / "review.html"
    shutil.copytree(DEMO, drafts)
    html.write_text(HTML)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "preparer.server", "--drafts", str(drafts), "--reviews", str(reviews), "--html", str(html)],
        cwd=REPO,
    )
    errpath = tmp_path / "stderr.txt"
    with open(errpath, "w") as errlog:
        with anyio.fail_after(60):
            async with Client(stdio_client(params, errlog=errlog)) as c:
                listed = (await c.list_tools()).tools
                opened = await c.call_tool("open_review", {"draft_id": "demo2"})
                refused = await c.call_tool("approve_review", {"draft_id": "demo2", "review_token": "x",
                                                                "draft_sha256": "y", "decisions": {}})
    assert [listing_entry(t) for t in listed] == json.loads(SNAPSHOT.read_text())
    assert not opened.is_error and opened.structured_content is None and "Demo Client 2" in text(opened)
    assert refused.is_error and text(refused) == ps.NOT_APPS
    err = errpath.read_text()
    assert "preparer: client" in err and "advertised extensions null" in err and "refused approve_review" in err
