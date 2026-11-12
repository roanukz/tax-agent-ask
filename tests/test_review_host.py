"""Preparer review screen, Block 3: the built view inside a host, in a browser, against the real server.

The view loads its client from unpkg only inside a host. Here a page in tmp_path frames
preparer/app/dist/review.html, and the request for the client is answered with a small stand-in
module, FAKE_CLIENT below, that offers the same App methods the view calls. It passes every tool
call to the real preparer server, run in process with an MCP Apps client. So the view's
host path runs offline: the call shapes, the token round trip, full screen and its refusal, an
echoed result, read-only without server tools, the fallback when no result arrives, a late
original result, a canceled call, and what the view tells the model. Every other request is
aborted and recorded. Block 4 checks the real client in Claude Desktop.

Skips with a message where Playwright or Chrome is missing.
"""

from __future__ import annotations

import concurrent.futures
import json
import shutil
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.client import advertise
from mcp.types import Implementation

from preparer import server as ps

playwright = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")

REPO = Path(__file__).resolve().parent.parent
DIST = REPO / "preparer" / "app" / "dist" / "review.html"
DEMO = REPO / "preparer" / "app" / "demo_drafts"
CLIENT_URL = "https://unpkg.com/@modelcontextprotocol/ext-apps@1.7.5/dist/src/app-with-deps.js"
APPS = advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})
FULL_CAPS = {"serverTools": {}, "updateModelContext": {"text": {}}, "openLinks": {}, "sandbox": {"permissions": {"clipboardWrite": {}}}}

FAKE_CLIENT = """
export function applyDocumentTheme(t) { document.documentElement.setAttribute("data-theme", t); }
export function applyHostStyleVariables(v) { for (const [k, x] of Object.entries(v)) document.documentElement.style.setProperty(k, x); }
export function applyHostFonts() {}
const stripMeta = r => { const c = { ...r }; delete c._meta; return c; };
export class App {
  constructor(info, caps) { this.info = info; this.appCaps = caps; }
  async connect() {
    const handlers = ["ontoolinput", "ontoolresult", "ontoolcancelled", "onhostcontextchanged", "onteardown"]
      .filter(n => typeof this[n] === "function");
    this.plan = await window.hostCall("plan", { appInfo: this.info, appCaps: this.appCaps, handlers });
    const p = this.plan;
    const open = async () => {
      const r = await window.hostCall("tool", { name: "open_review", arguments: { draft_id: p.draft } });
      return p.strip === "none" ? r : stripMeta(r);
    };
    setTimeout(async () => {
      if (p.cancel) { this.ontoolcancelled({ reason: "user" }); return; }
      this.ontoolinput({ arguments: { draft_id: p.draft } });
      if (p.deliver === "now") this.ontoolresult(await open());
      if (p.deliver === "late") { const early = await open(); setTimeout(() => this.ontoolresult(early), p.late_ms); }
      if (p.deliver === "after") setTimeout(async () => this.ontoolresult(await open()), p.late_ms);
    }, 0);
  }
  getHostContext() { return this.plan.ctx; }
  getHostCapabilities() { return this.plan.caps; }
  getHostVersion() { return { name: "fake-host", version: "1.0" }; }
  async callServerTool(params) {
    await window.hostCall("log", ["callServerTool", params]);
    const raw = await window.hostCall("tool", params);
    const r = this.plan.strip === "all" ? stripMeta(raw) : raw;
    const echoed = this.plan.strip === "none" ? raw : stripMeta(raw);
    if (this.plan.echo === "before") this.ontoolresult(echoed);
    else if (this.plan.echo) setTimeout(() => this.ontoolresult(echoed), 0);
    return r;
  }
  async updateModelContext(params) { await window.hostCall("log", ["updateModelContext", params]); return {}; }
  async requestDisplayMode(params) {
    await window.hostCall("log", ["requestDisplayMode", params]);
    return { mode: this.plan.grant ? params.mode : "inline" };
  }
  async openLink(params) { await window.hostCall("log", ["openLink", params]); return {}; }
}
"""


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            chrome = p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # noqa: BLE001 - any launch failure means no usable Chrome here
            pytest.skip(f"Google Chrome is not available to Playwright here: {str(e).splitlines()[0]}")
        yield chrome
        chrome.close()


class Host:
    """One fake host session: the real server, the plan the fake client reads, and what the view asked for."""

    def __init__(self, browser, tmp_path: Path, **plan: Any) -> None:
        drafts = tmp_path / "drafts"
        shutil.copytree(DEMO, drafts)
        self.reviews = tmp_path / "reviews"
        self.server = ps.build_server(drafts, self.reviews, html_path=DIST, log=lambda line: None)
        self.pool = concurrent.futures.ThreadPoolExecutor(1)
        self.calls: list[tuple[str, Any]] = []
        self.network: list[str] = []
        self.errors: list[str] = []
        self.plan = {
            "draft": "demo2", "deliver": "now", "late_ms": 0, "cancel": False, "grant": True, "echo": False, "strip": "none",
            "caps": FULL_CAPS,
            "ctx": {"theme": "dark", "displayMode": "inline", "availableDisplayModes": ["inline", "fullscreen"],
                    "styles": {"variables": {"--color-background-primary": "rgb(16, 24, 32)"}}},
        }
        self.plan.update(plan)
        host_page = tmp_path / "host.html"
        host_page.write_text(f'<!doctype html><iframe id="f" src="{DIST.as_uri()}" style="width:900px;height:760px"></iframe>')
        self.page = browser.new_page(viewport={"width": 920, "height": 800})
        self.page.route("**/*", self._route)
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.page.expose_function("hostCall", self._call)
        self.page.goto(host_page.as_uri())
        self.view = self.page.frame_locator("#f")

    def _route(self, route):
        url = route.request.url
        if url == CLIENT_URL:
            route.fulfill(body=FAKE_CLIENT, headers={"Content-Type": "text/javascript", "Access-Control-Allow-Origin": "*"})
        elif url.startswith(("file:", "data:")):
            route.continue_()
        else:
            self.network.append(url)
            route.abort()

    def _call(self, kind: str, payload: Any) -> Any:
        if kind == "plan":
            self.calls.append(("plan", payload))
            return self.plan
        if kind == "log":
            self.calls.append((payload[0], payload[1]))
            return None

        async def go():
            async with Client(self.server, extensions=[APPS], client_info=Implementation(name="fake-host", version="1.0")) as c:
                result = await c.call_tool(payload["name"], payload.get("arguments") or {})
                return result.model_dump(mode="json", by_alias=True, exclude_none=True)

        return self.pool.submit(lambda: anyio.run(go)).result()

    def of(self, kind: str) -> list[Any]:
        return [p for k, p in self.calls if k == kind]

    def record(self) -> dict[str, Any]:
        return json.loads((self.reviews / f"{self.plan['draft']}.json").read_text())

    def decide_all(self) -> None:
        v = self.view
        while v.locator("button:text-is('Mark checked')").count():
            v.locator("button:text-is('Mark checked')").first.click()
        v.locator("#tab-documents").click()
        while v.locator("button[aria-pressed='false']:text-is('Keep')").count():
            v.locator("button[aria-pressed='false']:text-is('Keep')").first.click()

    def close(self) -> None:
        self.page.close()
        self.pool.shutdown()


def test_the_view_saves_and_approves_through_the_host_and_tells_the_model_only_counts(browser, tmp_path):
    host = Host(browser, tmp_path)
    v = host.view
    v.locator("#review-button").wait_for()
    plan_call = host.of("plan")[0]
    assert plan_call["appCaps"] == {"availableDisplayModes": ["inline", "fullscreen"]}
    assert set(plan_call["handlers"]) == {"ontoolinput", "ontoolresult", "ontoolcancelled", "onhostcontextchanged", "onteardown"}
    assert v.locator("html").get_attribute("data-theme") == "dark"
    v.locator("#review-button").click()
    v.locator('[role="tab"]').first.wait_for()
    assert host.of("requestDisplayMode") == [{"mode": "fullscreen"}]
    host.decide_all()
    v.locator("button:text-is('Save')").click()
    v.locator("text=Saved at").wait_for()
    v.locator("button:text-is('Approve list')").click()
    v.locator("#approved-text").wait_for()
    tool_calls = host.of("callServerTool")
    assert [c["name"] for c in tool_calls] == ["save_review", "approve_review"]
    token, sha = tool_calls[1]["arguments"]["review_token"], tool_calls[1]["arguments"]["draft_sha256"]
    assert sorted(tool_calls[1]) == ["arguments", "name"]
    assert sorted(tool_calls[1]["arguments"]) == ["decisions", "draft_id", "draft_sha256", "host", "review_token"]
    assert tool_calls[1]["arguments"]["host"] == {"name": "fake-host", "version": "1.0"}
    told = host.of("updateModelContext")
    assert len(told) == 1 and told[0]["content"][0]["type"] == "text"
    assert token not in json.dumps(told) and sha not in json.dumps(told)
    assert told[0]["content"][0]["text"].startswith("The preparer approved the list for draft demo2:")
    record = host.record()
    assert record["status"] == "approved" and record["host"] == {"name": "fake-host", "version": "1.0"}
    assert host.network == [] and host.errors == []
    host.close()


def test_an_echoed_result_of_the_screens_own_call_changes_nothing(browser, tmp_path):
    host = Host(browser, tmp_path, echo=True)
    v = host.view
    v.locator("#review-button").click()
    host.decide_all()
    v.locator("button:text-is('Save')").click()
    v.locator("text=Saved at").wait_for()
    host.page.wait_for_timeout(300)
    assert v.locator('[role="tab"]').count() == 4 and v.locator("text=can't be shown").count() == 0
    assert v.locator("#missing").inner_text() == "Ready to approve."
    host.close()


def test_a_declined_full_screen_shows_the_review_in_place(browser, tmp_path):
    host = Host(browser, tmp_path, grant=False)
    v = host.view
    v.locator("#review-button").click()
    v.locator('[role="tab"]').first.wait_for()
    assert host.of("requestDisplayMode") == [{"mode": "fullscreen"}]
    host.close()


def test_without_server_tools_the_draft_is_read_only(browser, tmp_path):
    host = Host(browser, tmp_path, caps={"openLinks": {}})
    v = host.view
    v.locator("#review-button").click()
    v.locator('[role="tab"]').first.wait_for()
    assert v.locator("text=Approval needs a host that can call server tools.").count() == 1
    assert v.locator("button:text-is('Approve list')").is_disabled() and v.locator("button:text-is('Mark checked')").first.is_disabled()
    host.close()


def test_with_no_result_the_view_opens_the_draft_itself_and_a_late_one_changes_nothing(browser, tmp_path):
    host = Host(browser, tmp_path, deliver="late", late_ms=5500)
    v = host.view
    v.locator("#review-button").wait_for(timeout=10_000)
    assert [c["name"] for c in host.of("callServerTool")] == ["open_review"]
    v.locator("#review-button").click()
    host.decide_all()
    host.page.wait_for_timeout(2500)  # the original result, minted before the fallback's, arrives now
    assert v.locator("#missing").inner_text() == "Ready to approve."
    v.locator("button:text-is('Approve list')").click()
    v.locator("#approved-text").wait_for()
    assert host.record()["status"] == "approved"
    host.close()


def test_the_hosts_own_opening_after_the_fallback_hands_over_its_newer_token(browser, tmp_path):
    host = Host(browser, tmp_path, deliver="after", late_ms=6000)
    v = host.view
    v.locator("#review-button").wait_for(timeout=10_000)
    v.locator("#review-button").click()
    host.decide_all()
    host.page.wait_for_timeout(2500)  # the host's own open_review runs now and retires the fallback's token
    assert v.locator("#missing").inner_text() == "Ready to approve."
    v.locator("button:text-is('Save')").click()
    v.locator("text=Saved at").wait_for()
    v.locator("button:text-is('Approve list')").click()
    v.locator("#approved-text").wait_for()
    assert host.record()["status"] == "approved"
    host.close()


def test_a_host_that_drops_the_results_meta_gets_the_token_from_the_server(browser, tmp_path):
    # The token travels only in the result's _meta. A host that passes the draft without it leaves the screen without a
    # token, so the screen asks the server once itself; that answer comes back to the screen, not the conversation.
    host = Host(browser, tmp_path, strip="host")
    v = host.view
    v.locator("#review-button").click()
    host.decide_all()
    v.locator("button:text-is('Save')").click()
    v.locator("text=Saved at").wait_for()
    calls = host.of("callServerTool")
    assert [c["name"] for c in calls] == ["open_review", "save_review"]
    assert isinstance(calls[1]["arguments"]["review_token"], str) and calls[1]["arguments"]["review_token"]
    assert host.record()["status"] == "in_review" and host.errors == []
    host.close()


@pytest.mark.parametrize("echo", [True, "before"])
def test_echoes_of_the_screens_own_openings_never_start_another_fetch(browser, tmp_path, echo):
    # A host that drops _meta and also echoes the screen's own calls back into ontoolresult, after or before the
    # response, must not make the screen fetch again and again: one extra opening, and Save works.
    host = Host(browser, tmp_path, strip="host", echo=echo)
    v = host.view
    v.locator("#review-button").click()
    host.page.wait_for_timeout(1500)
    host.decide_all()
    v.locator("button:text-is('Save')").click()
    v.locator("text=Saved at").wait_for()
    assert [c["name"] for c in host.of("callServerTool")] == ["open_review", "save_review"]
    assert len((host.reviews / "openings.jsonl").read_text().splitlines()) == 2
    host.close()


def test_with_no_token_anywhere_and_echoes_the_screen_asks_once(browser, tmp_path):
    host = Host(browser, tmp_path, strip="all", echo=True)
    v = host.view
    v.locator("#review-button").click()
    host.page.wait_for_timeout(1500)
    assert [c["name"] for c in host.of("callServerTool")] == ["open_review"]
    assert len((host.reviews / "openings.jsonl").read_text().splitlines()) == 2
    host.close()


def test_with_no_token_anywhere_save_is_refused_on_screen(browser, tmp_path):
    host = Host(browser, tmp_path, strip="all")
    v = host.view
    v.locator("#review-button").click()
    host.decide_all()
    v.locator("button:text-is('Save')").click()
    v.locator("text=This screen has no review token from the server").wait_for()
    assert [c["name"] for c in host.of("callServerTool")] == ["open_review"]
    assert not (host.reviews / "demo2.json").exists()
    host.close()


def test_a_canceled_call_says_so_and_never_falls_back(browser, tmp_path):
    host = Host(browser, tmp_path, cancel=True)
    v = host.view
    v.locator("text=Opening this draft was canceled.").wait_for()
    host.page.wait_for_timeout(4500)
    assert host.of("callServerTool") == []
    host.close()


def test_with_no_draft_named_the_view_says_so(browser, tmp_path):
    host = Host(browser, tmp_path, deliver="never", draft=None)
    host.view.locator("text=The draft didn't arrive from Claude.").wait_for(timeout=10_000)
    assert host.of("callServerTool") == []
    host.close()
