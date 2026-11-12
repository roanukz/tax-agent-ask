"""Preparer review screen, Block 3: the view's source, its build, and the static demo, checked without a browser.

The built files must match a fresh build of preparer/app/review.html; after an intended change to the
view or the demo drafts, rebuild with

    uv run python -m preparer.build
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import anyio
import pytest
from mcp import Client
from mcp.client import advertise

from preparer import build, server
from preparer.drafts import DraftFolder, Reference, compute_flags

REPO = Path(__file__).resolve().parent.parent
SOURCE = build.SOURCE.read_text(encoding="utf-8")
DIST = build.DIST.read_text(encoding="utf-8")
DEMO = build.DEMO_PAGE.read_text(encoding="utf-8")
SCRIPT = SOURCE.split('<script type="module">', 1)[1].split("</script>", 1)[0]
FREE_TEXT = re.compile(
    r"<\s*(input|textarea|select)\b|contenteditable|designMode|\bprompt\(|h\(\s*[\"'](input|textarea|select)[\"']|"
    r"createElement\(\s*[\"'](input|textarea|select)",
    re.I,
)
STORAGE = re.compile(r"localStorage|sessionStorage|indexedDB|document\.cookie|caches\.open|openDatabase", re.I)


def test_the_built_files_match_a_fresh_build():
    assert build.stale() == [], "run uv run python -m preparer.build"


def test_both_outputs_name_the_source_sha256():
    sha = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
    for text in (DIST, DEMO):
        assert text.startswith("<!doctype html>\n<!-- Built by preparer/build.py from preparer/app/review.html, source sha256 " + sha)


def test_the_served_view_is_the_source_plus_its_header_only():
    assert DIST == "<!doctype html>\n" + build.header(SOURCE) + SOURCE[len("<!doctype html>\n"):]
    assert "demo-data" not in DIST.split("<script", 1)[0] and 'id="demo-data"' not in DIST


def test_the_demo_embeds_the_three_demo_drafts_as_open_review_gives_them():
    raw = DEMO.split('<script id="demo-data" type="application/json">', 1)[1].split("</script>", 1)[0]
    assert "</" not in raw and raw.isascii()
    payloads = json.loads(raw)
    ref = Reference.load()
    drafts = DraftFolder(build.DEMO_DRAFTS, ref).load_all()
    assert [p["draft"]["draft_id"] for p in payloads] == ["demo1", "demo2", "demo3"]
    for p in payloads:
        draft = drafts[p["draft"]["draft_id"]]
        assert p == server.review_payload(draft, compute_flags(draft, ref), ref, None)
    assert "<title>The preparer's review screen: demo</title>" in DEMO


def test_the_demo_banner_carries_the_project_disclaimers():
    # The same line closes the teardown and the README. It shows only in the static demo, never in Claude.
    line = "Not tax advice. All households are synthetic. Not affiliated with any tax software vendor."
    assert f'const DISCLAIMER = "{line}";' in SCRIPT
    banner = SCRIPT.split("function demoBanner()", 1)[1].split("\nfunction ", 1)[0]
    assert 'h("span", {}, DISCLAIMER)' in banner and SCRIPT.count("DISCLAIMER") == 2


def test_the_view_has_no_free_text_field_and_no_browser_storage():
    for text in (SOURCE, DIST, DEMO):
        assert not FREE_TEXT.search(text)
        assert not STORAGE.search(text)


def test_the_view_client_loads_only_by_dynamic_import_inside_a_host():
    url = "https://unpkg.com/@modelcontextprotocol/ext-apps@1.7.5/dist/src/app-with-deps.js"
    assert f'const CLIENT_URL = "{url}";' in SCRIPT
    assert re.findall(r"https?://[^\s\"'`)]+", SOURCE) == [url]
    assert SCRIPT.count("import(CLIENT_URL)") == 1 and not re.search(r"^\s*import\s", SCRIPT, re.M)
    start_host = SCRIPT.split("async function startHost()", 1)[1].split("\nfunction ", 1)[0]
    assert "import(CLIENT_URL)" in start_host
    assert "if (window.self === window.top) { S.phase = \"nohost\"" in SCRIPT
    assert SCRIPT.index('getElementById("demo-data")') < SCRIPT.index("startHost();")


def test_handlers_are_registered_before_connect_and_the_token_never_reaches_the_model():
    start_host = SCRIPT.split("async function startHost()", 1)[1].split("\nfunction ", 1)[0]
    connect = start_host.index("await app.connect()")
    for handler in ("app.ontoolinput", "app.ontoolresult", "app.ontoolcancelled", "app.onhostcontextchanged", "app.onteardown"):
        assert start_host.index(handler) < connect, handler
    assert 'availableDisplayModes: ["inline", "fullscreen"]' in start_host
    tell = SCRIPT.split("function tellModel(record)", 1)[1].split("\n}\n", 1)[0]
    assert "review_token" not in tell and "sha256" not in tell
    assert SCRIPT.count("updateModelContext(") == 1


def test_build_refuses_a_source_it_cannot_place_the_demo_in():
    with pytest.raises(ValueError):
        build.build("<html></html>", "[]")
    with pytest.raises(ValueError):
        build.build(SOURCE.replace("<title>Preparer review</title>", "<title>x</title>"), "[]")


def test_the_server_serves_the_built_view_by_default(tmp_path):
    assert server.DIST_HTML == build.DIST
    srv = server.build_server(build.DEMO_DRAFTS, tmp_path / "reviews")

    async def read():
        async with Client(srv, extensions=[advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})]) as c:
            return (await c.read_resource(server.VIEW_URI)).contents[0].text

    assert anyio.run(read) == DIST
