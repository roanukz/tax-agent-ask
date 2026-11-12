"""Preparer review screen, Block 4b: the local review page in Chrome, with the screen in its opaque frame.

The page runs as it does for Roanuk: preparer.local_review starts the real server as a child over stdio, on a
copy of the demo drafts, and Chrome loads the host page and the built view. No socket opens: every request to
http://127.0.0.1:8766 goes through Playwright's routing to ReviewPage.handle, the request for the screen's client
on unpkg gets a stand-in, and anything else is recorded and aborted, or, for the one source link a click opens,
answered with a stub page. Every session ends by checking that nothing else was requested.

The stand-in, STAND_IN below, speaks JSON-RPC over postMessage to its parent as the ext-apps 1.7.5 client does,
and checks each message from the host against the shapes the real client's schemas accept: its strict JSON-RPC
envelopes with typed _meta, error answers, the initialize result, the host context and capabilities, tool results and their
content blocks, the teardown request, and the display-mode and open-link answers. It records whatever it rejects.
With PREPARER_REAL_CLIENT set to a copy of the real 1.7.5 client whose SHA-256 is REAL_CLIENT_SHA256, two more
tests run, still offline: the first test takes demo1 through Save and Approve with the real client, and a last
test checks that the stand-in and the real client's own schemas accept and reject the same messages, case by
case. The copy is never committed (DECISIONS.md, 2026-11-11).

Skips with a message where Playwright or Chrome is missing.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from mcp.client.stdio import stdio_client

from preparer import local_review as lr
from preparer.drafts import DraftFolder, Reference

playwright = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")

REPO = Path(__file__).resolve().parent.parent
DEMO = REPO / "preparer" / "app" / "demo_drafts"
CLIENT_URL = "https://unpkg.com/@modelcontextprotocol/ext-apps@1.7.5/dist/src/app-with-deps.js"
REAL_CLIENT_SHA256 = "5bc0452b9994217df506cb505af20e97e766700513afb7d0b45d255785bb051f"
# Inside the frame: the screen's height as the real client measures it, and whether it fits with no scrollbar.
MEASURE = ("() => { const h = document.documentElement, kept = h.style.height; h.style.height = 'max-content'; "
           "const v = Math.ceil(h.getBoundingClientRect().height); h.style.height = kept; return v; }")
FITS = "() => { const e = document.scrollingElement; return e.scrollHeight <= e.clientHeight && e.scrollWidth <= e.clientWidth; }"
PAGE = {"name": lr.NAME, "version": lr.VERSION}
DEMO1_SOURCE = DraftFolder(DEMO, Reference.load()).load("demo1")["documents"][0]["source_url"]

STAND_IN = r"""
// A stand-in for the ext-apps 1.7.5 App, offline. Each check mirrors a schema in the real client.
const rejected = (window.__rejected = []);
const events = (window.__events = []);
const isObj = v => v !== null && typeof v === "object" && !Array.isArray(v);
const isStr = v => typeof v === "string";
const isNum = v => typeof v === "number" && Number.isFinite(v);
const isBool = v => typeof v === "boolean";
const isId = v => isStr(v) || Number.isSafeInteger(v);
const opt = (v, f) => v === undefined || f(v);
const arrOf = (v, f) => Array.isArray(v) && v.every(f);
const only = (v, keys) => Object.keys(v).every(k => keys.includes(k));
const MODES = ["inline", "fullscreen", "pip"];
const SHADES = ["primary", "secondary", "tertiary", "inverse", "ghost", "info", "danger", "success", "warning", "disabled"];
const RINGS = ["primary", "secondary", "inverse", "info", "danger", "success", "warning"];
const SIZES = ["xs", "sm", "md", "lg"];
const HEADINGS = ["xs", "sm", "md", "lg", "xl", "2xl", "3xl"];
const STYLE_KEYS = new Set([
  ...["background", "text", "border"].flatMap(k => SHADES.map(s => `--color-${k}-${s}`)), ...RINGS.map(s => `--color-ring-${s}`),
  "--font-sans", "--font-mono", ...["normal", "medium", "semibold", "bold"].map(w => `--font-weight-${w}`),
  ...SIZES.flatMap(z => [`--font-text-${z}-size`, `--font-text-${z}-line-height`]),
  ...HEADINGS.flatMap(z => [`--font-heading-${z}-size`, `--font-heading-${z}-line-height`]),
  ...["xs", "sm", "md", "lg", "xl", "full"].map(z => `--border-radius-${z}`), "--border-width-regular",
  ...["hairline", "sm", "md", "lg"].map(z => `--shadow-${z}`),
]);
function reject(where, value) {
  rejected.push(where);
  console.error(`stand-in rejected ${where}: ${JSON.stringify(value).slice(0, 400)}`);
  return false;
}
// _meta on params and results: progressToken and the related-task key are typed; other keys pass.
const meta = v => isObj(v) && opt(v.progressToken, isId)
  && opt(v["io.modelcontextprotocol/related-task"], t => isObj(t) && isStr(t.taskId));
const withMeta = v => isObj(v) && opt(v._meta, meta);
// The real client's JSON-RPC envelopes are strict objects.
function envelope(m) {
  if (!isObj(m) || m.jsonrpc !== "2.0") return "none";
  if ("method" in m) {
    if (!isStr(m.method) || !opt(m.params, withMeta)) return "bad";
    if ("id" in m) return isId(m.id) && only(m, ["jsonrpc", "id", "method", "params"]) ? "request" : "bad";
    return only(m, ["jsonrpc", "method", "params"]) ? "notification" : "bad";
  }
  if ("result" in m) return isId(m.id) && withMeta(m.result) && only(m, ["jsonrpc", "id", "result"]) ? "result" : "bad";
  if ("error" in m) {
    const e = m.error;
    return opt(m.id, isId) && isObj(e) && Number.isSafeInteger(e.code) && isStr(e.message) && only(m, ["jsonrpc", "id", "error"]) ? "error" : "bad";
  }
  return "bad";
}
const icon = i => isObj(i) && isStr(i.src) && opt(i.mimeType, isStr) && opt(i.sizes, a => arrOf(a, isStr)) && opt(i.theme, t => t === "light" || t === "dark");
const implementation = v => isObj(v) && isStr(v.name) && isStr(v.version) && opt(v.title, isStr) && opt(v.websiteUrl, isStr)
  && opt(v.description, isStr) && opt(v.icons, a => arrOf(a, icon));
const known = (v, keys) => isObj(v) && keys.every(k => opt(v[k], isObj));
const modalities = v => known(v, ["text", "image", "audio", "resource", "resourceLink", "structuredContent"]);
const domains = v => isObj(v) && ["connectDomains", "resourceDomains", "frameDomains", "baseUriDomains"].every(k => opt(v[k], a => arrOf(a, isStr)));
const permissions = v => known(v, ["camera", "microphone", "geolocation", "clipboardWrite"]);
const listChanged = v => isObj(v) && opt(v.listChanged, isBool);
const capabilities = v => isObj(v) && opt(v.experimental, x => isObj(x) && Object.values(x).every(isObj))
  && opt(v.openLinks, isObj) && opt(v.downloadFile, isObj) && opt(v.serverTools, listChanged) && opt(v.serverResources, listChanged)
  && opt(v.logging, isObj) && opt(v.sandbox, s => isObj(s) && opt(s.permissions, permissions) && opt(s.csp, domains))
  && opt(v.updateModelContext, modalities) && opt(v.message, modalities) && opt(v.sampling, s => isObj(s) && opt(s.tools, isObj));
const jsonSchema = s => isObj(s) && s.type === "object" && opt(s.properties, p => isObj(p) && Object.values(p).every(isObj))
  && opt(s.required, r => arrOf(r, isStr));
const hints = a => isObj(a) && opt(a.title, isStr) && ["readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"].every(k => opt(a[k], isBool));
const tool = v => isObj(v) && isStr(v.name) && opt(v.title, isStr) && opt(v.description, isStr) && opt(v.icons, a => arrOf(a, icon))
  && jsonSchema(v.inputSchema) && opt(v.outputSchema, jsonSchema) && opt(v.annotations, hints) && opt(v._meta, isObj)
  && opt(v.execution, x => isObj(x) && opt(x.taskSupport, t => ["required", "optional", "forbidden"].includes(t)));
const context = v => isObj(v)
  && opt(v.toolInfo, t => isObj(t) && opt(t.id, isId) && tool(t.tool))
  && opt(v.theme, t => t === "light" || t === "dark")
  && opt(v.styles, s => isObj(s) && opt(s.variables, x => isObj(x) && Object.entries(x).every(([k, y]) => STYLE_KEYS.has(k) && (y === undefined || isStr(y))))
    && opt(s.css, c => isObj(c) && opt(c.fonts, isStr)))
  && opt(v.displayMode, m => MODES.includes(m))
  && opt(v.availableDisplayModes, a => arrOf(a, m => MODES.includes(m)))
  && opt(v.containerDimensions, d => isObj(d) && (isNum(d.height) || opt(d.maxHeight, isNum)) && (isNum(d.width) || opt(d.maxWidth, isNum)))
  && opt(v.locale, isStr) && opt(v.timeZone, isStr) && opt(v.userAgent, isStr)
  && opt(v.platform, p => ["web", "desktop", "mobile"].includes(p))
  && opt(v.deviceCapabilities, d => isObj(d) && opt(d.touch, isBool) && opt(d.hover, isBool))
  && opt(v.safeAreaInsets, s => isObj(s) && ["top", "right", "bottom", "left"].every(k => isNum(s[k])));
const base64 = s => { if (!isStr(s)) return false; try { atob(s); return true; } catch (e) { return false; } };
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;
const annotations = a => isObj(a) && opt(a.audience, x => arrOf(x, r => r === "user" || r === "assistant"))
  && opt(a.priority, p => isNum(p) && p >= 0 && p <= 1) && opt(a.lastModified, d => isStr(d) && ISO.test(d));
const contents = r => isObj(r) && isStr(r.uri) && opt(r.mimeType, isStr) && opt(r._meta, isObj) && (isStr(r.text) || base64(r.blob));
const block = b => isObj(b) && opt(b.annotations, annotations) && opt(b._meta, isObj) && (
  b.type === "text" ? isStr(b.text)
  : b.type === "image" || b.type === "audio" ? base64(b.data) && isStr(b.mimeType)
  : b.type === "resource_link" ? isStr(b.uri) && isStr(b.name) && opt(b.title, isStr) && opt(b.description, isStr) && opt(b.mimeType, isStr) && opt(b.size, isNum)
  : b.type === "resource" ? contents(b.resource) : false);
const callResult = v => withMeta(v) && opt(v.content, a => arrOf(a, block)) && opt(v.structuredContent, isObj) && opt(v.isError, isBool);
const initResult = v => isObj(v) && isStr(v.protocolVersion) && implementation(v.hostInfo) && capabilities(v.hostCapabilities) && context(v.hostContext);
const displayResult = v => isObj(v) && MODES.includes(v.mode);
const linkResult = v => isObj(v) && opt(v.isError, isBool);
const teardownParams = v => isObj(v);
const NOTIFICATIONS = {
  "ui/notifications/tool-input": [v => isObj(v) && opt(v.arguments, isObj), "ontoolinput"],
  "ui/notifications/tool-input-partial": [v => isObj(v) && opt(v.arguments, isObj), null],
  "ui/notifications/tool-result": [callResult, "ontoolresult"],
  "ui/notifications/tool-cancelled": [v => isObj(v) && opt(v.reason, isStr), "ontoolcancelled"],
  "ui/notifications/host-context-changed": [context, "onhostcontextchanged"],
};
export const __checks = { envelope, initResult, callResult, displayResult, linkResult, teardownParams, NOTIFICATIONS, STYLE_KEYS };
export function applyDocumentTheme(t) { document.documentElement.setAttribute("data-theme", t); document.documentElement.style.colorScheme = t; }
export function applyHostStyleVariables(v, el = document.documentElement) { for (const [k, x] of Object.entries(v || {})) if (x !== undefined) el.style.setProperty(k, x); }
export function applyHostFonts() {}
export class App {
  constructor(info, caps, options = { autoResize: true }) {
    this.info = info; this.caps = caps; this.options = options; this.next = 0; this.waiting = new Map();
    window.addEventListener("message", e => { if (e.source === window.parent) this._in(e.data); });
  }
  _out(m) { window.parent.postMessage(m, "*"); }
  _request(method, params, check) {
    const id = this.next++;
    return new Promise((resolve, reject_) => {
      this.waiting.set(id, { method, check, resolve, reject: reject_ });
      this._out({ jsonrpc: "2.0", id, method, params });
    });
  }
  _in(m) {
    const kind = envelope(m);
    if (kind === "none") return;
    if (kind === "bad") { reject("envelope", m); return; }
    if (kind === "result" || kind === "error") {
      const w = this.waiting.get(m.id);
      if (!w) return;
      this.waiting.delete(m.id);
      if (kind === "error") { const e = new Error(m.error.message); e.code = m.error.code; w.reject(e); return; }
      if (!w.check(m.result)) { reject(`${w.method} result`, m.result); w.reject(new Error("invalid result")); return; }
      w.resolve(m.result);
      return;
    }
    if (kind === "request") {
      if (m.method === "ui/resource-teardown") {
        if (!teardownParams(m.params)) { reject("teardown params", m.params); return; }
        events.push(m.method);
        if (window.__silentTeardown) return;
        Promise.resolve(this.onteardown ? this.onteardown(m.params) : {}).then(r => this._out({ jsonrpc: "2.0", id: m.id, result: r || {} }));
      } else if (m.method === "ping") {
        events.push(m.method);
        this._out({ jsonrpc: "2.0", id: m.id, result: {} });
      } else {
        this._out({ jsonrpc: "2.0", id: m.id, error: { code: -32601, message: "Method not found" } });
      }
      return;
    }
    const handler = NOTIFICATIONS[m.method];
    if (!handler) return;
    if (!handler[0](m.params)) { reject(m.method, m.params); return; }
    events.push(m.method);
    if (m.method === "ui/notifications/host-context-changed") this.ctx = { ...this.ctx, ...m.params };
    if (handler[1] && typeof this[handler[1]] === "function") this[handler[1]](m.params);
  }
  async connect() {
    const r = await this._request("ui/initialize", { appCapabilities: this.caps, appInfo: this.info, protocolVersion: "2026-01-26" }, initResult);
    this.result = r; this.ctx = { ...r.hostContext };
    this._out({ jsonrpc: "2.0", method: "ui/notifications/initialized" });
    if (this.options && this.options.autoResize) {
      let last = "";
      const send = () => {
        const html = document.documentElement, kept = html.style.height;
        html.style.height = "max-content";  // measured as the real client measures it
        const size = { width: Math.ceil(window.innerWidth), height: Math.ceil(html.getBoundingClientRect().height) };
        html.style.height = kept;
        if (JSON.stringify(size) !== last) { last = JSON.stringify(size); this._out({ jsonrpc: "2.0", method: "ui/notifications/size-changed", params: size }); }
      };
      const ro = new ResizeObserver(send); ro.observe(document.documentElement); ro.observe(document.body); send();
    }
  }
  getHostContext() { return this.ctx; }
  getHostCapabilities() { return this.result.hostCapabilities; }
  getHostVersion() { return this.result.hostInfo; }
  callServerTool(p) { return this._request("tools/call", p, callResult); }
  updateModelContext(p) { return this._request("ui/update-model-context", p, withMeta); }
  openLink(p) { return this._request("ui/open-link", p, linkResult); }
  requestDisplayMode(p) { return this._request("ui/request-display-mode", p, displayResult); }
}
"""


def real_client() -> bytes | None:
    path = os.environ.get("PREPARER_REAL_CLIENT")
    if not path:
        return None
    body = Path(path).read_bytes()
    assert hashlib.sha256(body).hexdigest() == REAL_CLIENT_SHA256, "PREPARER_REAL_CLIENT is not the recorded 1.7.5 client"
    return body


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            chrome = p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # noqa: BLE001 - any launch failure means no usable Chrome here
            pytest.skip(f"Google Chrome is not available to Playwright here: {str(e).splitlines()[0]}")
        yield chrome
        chrome.close()


class Session:
    """The page on a copy of the demo drafts, served to Chrome through routing, and everything it asked for."""

    def __init__(self, browser, tmp_path: Path, client: bytes | None = None) -> None:
        self.drafts, self.reviews = tmp_path / "drafts", tmp_path / "reviews"
        shutil.copytree(DEMO, self.drafts)
        self.server_log = open(tmp_path / "server.log", "w", encoding="utf-8")  # noqa: SIM115
        self.lines: list[str] = []
        self.stops: list[str] = []
        self.network: list[str] = []
        self.errors: list[str] = []
        self.codes: list[str] = []
        self.overrides: dict[str, tuple[int, dict[str, Any]]] = {}
        self.client = client if client is not None else STAND_IN.encode()
        self.standin = client is None
        self._cm = lr.running(stdio_client(lr.server_params(self.drafts, self.reviews), errlog=self.server_log),
                              practice=True, log=self.lines.append, on_stop=self.stops.append)
        self.app = self._cm.__enter__()
        self.context = browser.new_context(viewport={"width": 1000, "height": 860}, color_scheme="light")
        self.context.route("**/*", self._route)
        self.page = self.context.new_page()
        self.popups: list[Any] = []
        self.context.on("page", lambda p: self.popups.append(p))
        self.watch(self.page)

    def watch(self, page) -> None:
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        page.on("console", self._console)

    def _console(self, m) -> None:
        # The first test's own fetch from the frame is meant to break the screen's policy; nothing else may. Chrome also
        # logs each refused request, which the tests check by its answer and the page log instead.
        probe = f"{lr.ORIGIN}/api/state" in m.text and "Content Security Policy" in m.text
        refused = m.text.startswith("Failed to load resource: the server responded with a status of")
        if m.type in ("error", "warning") and not probe and not refused:
            self.errors.append(f"console {m.type}: {m.text}")

    def _route(self, route) -> None:
        req = route.request
        url = req.url
        parts = urlsplit(url)
        if url == CLIENT_URL:
            route.fulfill(body=self.client, headers={"content-type": "text/javascript; charset=utf-8", "access-control-allow-origin": "*"})
        elif f"{parts.scheme}://{parts.netloc}" == lr.ORIGIN:
            if parts.path in self.overrides:
                status, body = self.overrides[parts.path]
                route.fulfill(status=status, body=json.dumps(body), headers={"content-type": "application/json"})
                return
            headers = {**req.all_headers(), "host": parts.netloc}
            path = parts.path + (f"?{parts.query}" if parts.query else "")
            r = self.app.handle(req.method, path, headers, req.post_data_buffer or b"")
            route.fulfill(status=r.status, body=r.body,
                          headers={"content-type": r.content_type, **lr.SECURITY_HEADERS, **r.headers})
        elif url == DEMO1_SOURCE:
            route.fulfill(body="<!doctype html><title>source</title><p>source stub</p>", headers={"content-type": "text/html"})
        else:
            self.network.append(url)
            route.abort()

    def start(self, page=None, code: str | None = None) -> None:
        if code is None:
            code = self.app.new_code().split("#c=", 1)[1]
        self.codes.append(code)
        (page or self.page).goto(f"{lr.ORIGIN}/#c={code}")

    @property
    def view(self):
        return self.page.frame_locator("#stage iframe")

    @property
    def frame(self):
        return self.page.locator("#stage iframe").element_handle().content_frame()

    def progress(self) -> str:
        return self.page.locator("#progress").inner_text()

    def wait_progress(self, text: str) -> None:
        self.page.wait_for_function("t => document.getElementById('progress').textContent === t", arg=text, timeout=15000)

    def wait_closed(self) -> None:
        self.page.wait_for_function("() => !document.querySelector('#stage iframe') && !document.getElementById('open').disabled",
                                    timeout=15000)

    def open_next(self) -> None:
        self.page.click("#open")
        self.view.locator("#review-button").wait_for(timeout=15000)

    def to_fullscreen(self) -> None:
        """Review, then wait until the screen has settled in full screen with its first tab focused, since a click
        while it re-renders for the new mode can be lost."""
        self.view.locator("#review-button").click()
        self.page.wait_for_function("() => document.body.dataset.mode === 'fullscreen'")
        self.view.locator('[role="tab"][aria-selected="true"]').wait_for()
        self.frame.wait_for_function("() => innerHeight > 600 && document.activeElement && document.activeElement.getAttribute('role') === 'tab'")

    def tab(self, name: str) -> None:
        tab = self.view.locator(f"#tab-{name}")
        for _ in range(5):
            tab.click()
            if tab.get_attribute("aria-selected") == "true":
                return
            self.page.wait_for_timeout(100)
        raise AssertionError(f"the {name} tab would not open")

    def frame_call(self, message: dict[str, Any], frame=None) -> Any:
        """Posts one JSON-RPC message from inside a frame and returns the host's answer to it."""
        return (frame or self.frame).evaluate("""m => new Promise(res => {
            addEventListener('message', e => { if (e.source === parent && e.data && e.data.id === m.id) res(e.data); });
            parent.postMessage(m, '*'); setTimeout(() => res('no answer'), 3000); })""", message)

    def frame_post(self, message: dict[str, Any]) -> None:
        """Posts one message from inside the screen's frame without waiting for anything."""
        self.frame.evaluate("m => { parent.postMessage(m, '*'); }", message)

    def frame_call_unclicked(self, message: dict[str, Any]) -> tuple[bool, Any]:
        """Like frame_call, but posted once the transient activation Playwright's evaluate grants has lapsed (Chrome
        keeps it 5 seconds), so the host sees a request with no click behind it."""
        return tuple(self.frame.evaluate("""m => new Promise(res => {
            addEventListener('message', e => { if (e.source === parent && e.data && e.data.id === m.id) res([active, e.data]); });
            let active = null;
            setTimeout(() => { active = navigator.userActivation.isActive; parent.postMessage(m, '*'); }, 6000);
            setTimeout(() => res([active, 'no answer']), 10000); })""", message))

    def decide_all(self) -> None:
        v = self.view
        while v.locator("button:text-is('Mark checked')").count():
            v.locator("button:text-is('Mark checked')").first.click()
        self.tab("documents")
        while v.locator("button[aria-pressed='false']:text-is('Keep')").count():
            v.locator("button[aria-pressed='false']:text-is('Keep')").first.click()

    def record(self, draft_id: str) -> dict[str, Any]:
        return json.loads((self.reviews / f"{draft_id}.json").read_text())

    def openings(self) -> int:
        path = self.reviews / "openings.jsonl"
        return len(path.read_text().splitlines()) if path.exists() else 0

    def secrets(self) -> list[str]:
        return [s for s in [self.app.token, *self.codes] if s]

    def close(self) -> None:
        self.context.close()
        self._cm.__exit__(None, None, None)
        self.server_log.close()
        assert self.network == [], f"requests outside the test's routes: {self.network}"


@pytest.fixture
def session(browser, tmp_path):
    s = Session(browser, tmp_path)
    yield s
    s.close()


FROM_FRAME = """window.__fromFrame = [];
    addEventListener('message', e => window.__fromFrame.push([e.data && e.data.id, !!document.querySelector('#stage iframe')]));"""
HOST_ANSWERS = "window.__fromFrame.filter(([id]) => typeof id === 'string' && id.startsWith('host-'))"



def started(s: Session) -> Session:
    s.start()
    s.wait_progress("Approved 0 of 3. Next: demo1.")
    return s


def assert_fits(s: Session) -> None:
    """Once the host has followed the screen's latest size, the inline screen fits its frame with no scrollbar."""
    deadline = time.monotonic() + 5
    while True:
        want = max(120, s.frame.evaluate(MEASURE))  # the host's floor is 120 px
        got = s.page.evaluate("document.querySelector('#stage iframe').style.height")
        if got == f"{want}px":
            break
        assert time.monotonic() < deadline, f"the host never followed the screen's size ({got} for {want}px)"
        s.page.wait_for_timeout(100)
    assert s.frame.evaluate(FITS), "the inline screen overflows its frame, so it shows a scrollbar"


@pytest.mark.parametrize("client", ["stand-in", "real 1.7.5"])
def test_the_page_takes_demo1_through_save_and_approve_in_the_opaque_frame(browser, tmp_path, client):
    body = None
    if client != "stand-in":
        body = real_client()
        if body is None:
            pytest.skip("set PREPARER_REAL_CLIENT to a copy of the real 1.7.5 client for the one-off run (DECISIONS.md)")
    s = Session(browser, tmp_path, body)
    s.page.add_init_script(FROM_FRAME)  # before host.js, so it sees each message from the frame before the host acts
    try:
        started(s)
        assert s.page.url == f"{lr.ORIGIN}/" and s.page.locator("#open").inner_text() == "Open demo1"
        s.open_next()
        frame = s.frame
        assert frame.evaluate("self.origin") == "null"
        assert frame.evaluate("fetch('/api/state', {method: 'POST'}).then(() => 'reached', () => 'blocked')") == "blocked"
        assert frame.evaluate("(() => { try { localStorage.length; return 'storage'; } catch (e) { return 'blocked'; } })()") == "blocked"
        assert frame.evaluate("(() => { try { return parent.document.title; } catch (e) { return 'blocked'; } })()") == "blocked"
        assert s.page.evaluate("document.querySelector('#stage iframe').getAttribute('sandbox')") == "allow-scripts"
        if s.standin:
            assert frame.evaluate("window.__events.slice(0, 2)") == ["ui/notifications/tool-input", "ui/notifications/tool-result"]
        assert_fits(s)
        s.to_fullscreen()
        box = json.loads(s.page.evaluate("JSON.stringify(document.querySelector('#stage iframe').getBoundingClientRect())"))
        assert box["width"] == 1000 and box["height"] > 700
        s.decide_all()
        s.view.locator("button:text-is('Save')").click()
        s.view.locator("text=Saved at").wait_for()
        s.view.locator("button:text-is('Approve list')").click()
        s.view.locator("#approved-text").wait_for()
        s.view.locator("button:text-is('Summary')").click()  # back inline, where the host re-applies the last height
        s.page.wait_for_function("() => document.body.dataset.mode === 'inline'")
        assert_fits(s)
        record = s.record("demo1")
        assert record["status"] == "approved" and record["host"] == PAGE and record["client"] == PAGE
        s.wait_progress("Approved 1 of 3. Next: demo2.")
        assert s.page.locator("#open").is_disabled()  # the next draft waits until this one is closed
        openings = [json.loads(line) for line in (s.reviews / "openings.jsonl").read_text().splitlines()]
        assert [o["client"] for o in openings] == [PAGE]
        html = s.page.content() + "".join(f.content() for f in s.page.frames)
        assert not any(secret in html for secret in s.secrets())
        # Close draft: the host asks the screen to tear down and waits for its answer before removing the frame.
        s.page.click("#close")
        s.wait_progress("Approved 1 of 3. Next: demo2.")
        s.wait_closed()
        assert s.page.evaluate(HOST_ANSWERS) == [["host-1", True]]
        assert s.page.evaluate("document.body.dataset.mode") == "inline"
        assert s.errors == [] and s.stops == []
        assert not [line for line in s.lines if line.startswith("refused")]
    finally:
        s.close()


def test_the_host_ignores_other_windows_and_relays_only_the_screens_own_draft(session):
    s = started(session)
    s.open_next()
    # A second sandboxed frame running the real view: its client asks to initialize, and then it asks to open a draft.
    s.page.evaluate("""() => { const f = document.createElement('iframe'); f.id = 'foreign'; f.setAttribute('sandbox', 'allow-scripts');
      f.src = '/view'; document.body.append(f); }""")
    foreign = s.page.locator("#foreign").element_handle().content_frame()
    foreign.wait_for_function("() => window.__events !== undefined && document.readyState === 'complete'")
    answer = s.frame_call({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                           "params": {"name": "open_review", "arguments": {"draft_id": "demo1"}}}, frame=foreign)
    assert answer == "no answer" and foreign.evaluate("window.__events") == []
    assert s.openings() == 1 and s.page.locator("#stage iframe").count() == 1
    answer = s.frame_call({"jsonrpc": "2.0", "id": "t1", "method": "tools/call", "params": {"name": "list_drafts", "arguments": {}}})
    assert answer["error"] == {"code": -32602, "message": "The screen may not call that tool here."}
    answer = s.frame_call({"jsonrpc": "2.0", "id": "t2", "method": "tools/call",
                           "params": {"name": "open_review", "arguments": {"draft_id": "demo2"}}})
    assert answer["error"] == {"code": -32602, "message": "The screen asked about a draft that is not open."}
    for method in ("ui/message", "ui/update-model-context", "ui/download-file", "resources/read"):
        answer = s.frame_call({"jsonrpc": "2.0", "id": method, "method": method, "params": {}})
        assert answer["error"]["code"] == -32601
    answer = s.frame_call({"jsonrpc": "2.0", "id": "t3", "method": "tools/call", "params": {"name": "open_review", "arguments": {"draft_id": "demo1"}}})
    assert answer["result"]["structuredContent"]["draft"]["draft_id"] == "demo1" and s.openings() == 2
    assert not (s.reviews / "demo1.json").exists() and s.frame.evaluate("window.__rejected") == [] and s.errors == []
    assert [line.split(":")[0] for line in s.lines] == ["refused /api/call tool list_drafts", "refused /api/call tool open_review"]


def test_a_reload_or_a_second_initialize_closes_the_draft(session):
    session.page.add_init_script(FROM_FRAME)
    s = started(session)
    s.open_next()
    s.frame.evaluate("location.reload()")
    s.wait_closed()
    assert "reloaded" in s.page.locator("#note").inner_text()
    s.open_next()
    # The host closes the draft in the same task, so the frame is gone before any answer could reach it.
    s.frame_post({"jsonrpc": "2.0", "id": "again", "method": "ui/initialize",
                  "params": {"appInfo": {"name": "x", "version": "1"}, "appCapabilities": {}, "protocolVersion": "2026-01-26"}})
    s.wait_closed()
    assert "started again" in s.page.locator("#note").inner_text()
    assert s.page.evaluate(HOST_ANSWERS) == []  # neither document was asked to tear down
    assert s.progress() == "Approved 0 of 3. Next: demo1." and s.stops == [] and s.errors == []


def test_the_screen_hears_why_a_draft_did_not_open_and_can_ask_to_close(session):
    s = session
    s.page.add_init_script(FROM_FRAME)
    slow = (504, {"error": "The preparer server did not answer in time. Try again, or restart the page."})
    s.overrides["/api/state"] = slow
    s.start()
    s.wait_progress("Could not load the drafts.")
    assert s.page.locator("#open").inner_text() == "Retry" and s.page.locator("#open").is_enabled()
    del s.overrides["/api/state"]
    s.page.click("#open")
    s.wait_progress("Approved 0 of 3. Next: demo1.")
    s.overrides["/api/state"] = slow
    s.page.click("#open")
    s.page.wait_for_function("t => document.getElementById('note').textContent === t", arg=slow[1]["error"])
    assert s.page.locator("#stage iframe").count() == 0
    del s.overrides["/api/state"]
    s.overrides["/api/open"] = (409, {"error": "Only the next draft in order can be opened."})
    s.page.click("#open")
    s.view.locator("text=Opening this draft was canceled").wait_for(timeout=15000)
    assert s.frame.evaluate("window.__events") == ["ui/notifications/tool-input", "ui/notifications/tool-cancelled"]
    assert s.page.locator("#note").inner_text() == "Only the next draft in order can be opened. Use Close draft, then Open."
    del s.overrides["/api/open"]
    s.page.click("#close")
    s.wait_closed()
    s.open_next()
    before = len(s.page.evaluate(HOST_ANSWERS))
    s.frame_post({"jsonrpc": "2.0", "method": "ui/notifications/request-teardown", "params": {}})
    s.wait_closed()
    assert s.page.evaluate(HOST_ANSWERS)[before:] == [[f"host-{before + 1}", True]]  # torn down, then removed
    assert "asked to close" in s.page.locator("#note").inner_text() and s.errors == []


def test_a_screen_that_never_answers_its_teardown_is_closed_after_three_seconds(session):
    s = started(session)
    s.open_next()
    assert s.frame_call({"jsonrpc": "2.0", "id": "p", "method": "ping"}) == {"jsonrpc": "2.0", "id": "p", "result": {}}
    s.frame.evaluate("window.__silentTeardown = true")
    began = time.monotonic()
    s.page.click("#close")
    closing = s.frame_call({"jsonrpc": "2.0", "id": "c", "method": "tools/call",
                            "params": {"name": "open_review", "arguments": {"draft_id": "demo1"}}})
    assert closing["error"] == {"code": -32600, "message": "The page is closing this draft."}
    s.page.wait_for_function("() => !document.querySelector('#stage iframe')", timeout=10000)
    assert 2.5 < time.monotonic() - began < 4.5
    s.wait_closed()
    assert s.errors == []


def test_theme_display_mode_and_size_follow_the_host(session):
    s = started(session)
    s.open_next()
    view_theme = "document.documentElement.getAttribute('data-theme')"
    assert s.frame.evaluate(view_theme) == "light"
    s.page.click("#theme")  # system, which is light here, to light: the screen hears nothing
    assert s.page.evaluate("document.documentElement.dataset.theme") == "light" and s.frame.evaluate(view_theme) == "light"
    s.page.click("#theme")
    s.page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
    s.frame.wait_for_function("() => document.documentElement.getAttribute('data-theme') === 'dark'")
    assert s.page.locator("#theme").inner_text() == "Theme: dark"
    height = s.page.evaluate("parseInt(document.querySelector('#stage iframe').style.height)")
    assert 120 <= height <= 20000 and height != 420
    answer = s.frame_call({"jsonrpc": "2.0", "id": "pip", "method": "ui/request-display-mode", "params": {"mode": "pip"}})
    assert answer["result"] == {"mode": "inline"}
    answer = s.frame_call({"jsonrpc": "2.0", "id": "fs", "method": "ui/request-display-mode", "params": {"mode": "fullscreen"}})
    assert answer["result"] == {"mode": "fullscreen"} and s.page.evaluate("document.body.dataset.mode") == "fullscreen"
    changes = s.frame.evaluate("window.__events.filter(e => e === 'ui/notifications/host-context-changed').length")
    assert changes == 2  # the theme once, the display mode once
    assert s.frame.evaluate("window.__rejected") == [] and s.errors == []


def test_the_bar_shrinks_back_after_a_narrow_window(session):
    s = started(session)
    bar = "Math.ceil(document.querySelector('.bar').getBoundingClientRect().height)"
    wide = s.page.evaluate(bar)
    size = s.page.viewport_size
    s.page.set_viewport_size({"width": 320, "height": size["height"]})  # the bar wraps taller
    s.page.wait_for_function(f"() => {bar} > {wide}")
    s.page.set_viewport_size(size)
    s.page.wait_for_function(f"() => {bar} === {wide}")
    s.page.wait_for_function(f"() => getComputedStyle(document.documentElement).getPropertyValue('--bar-h').trim() === '{wide}px'")
    assert s.errors == []


def test_source_links_open_in_a_new_tab_only_after_a_click_and_only_from_the_result(session):
    s = started(session)
    s.open_next()
    active, unclicked = s.frame_call_unclicked({"jsonrpc": "2.0", "id": "l1", "method": "ui/open-link", "params": {"url": DEMO1_SOURCE}})
    assert active is False and unclicked["result"] == {"isError": True} and s.popups == []
    s.to_fullscreen()
    s.tab("documents")
    with s.context.expect_page() as opened:
        s.view.locator("button.link").first.click()
    popup = opened.value
    popup.wait_for_url(DEMO1_SOURCE)
    assert popup.evaluate("window.opener") is None
    s.tab("flags")  # a click that opens nothing, then a link the result never held
    other = s.frame_call({"jsonrpc": "2.0", "id": "l2", "method": "ui/open-link", "params": {"url": "https://example.test/"}})
    assert other["result"] == {"isError": True} and len(s.popups) == 1
    assert s.frame.evaluate("window.__rejected") == [] and s.errors == []


def test_links_the_page_did_not_issue_or_has_replaced_are_refused_and_only_a_reused_one_stops_it(session):
    s = session
    s.page.goto(f"{lr.ORIGIN}/")
    s.page.wait_for_function("() => !document.getElementById('note').hidden")
    assert "fresh link" in s.page.locator("#note").inner_text() and s.app.token is None
    s.page.goto("about:blank")  # a goto that changes only the fragment would not load the page again
    s.page.goto(f"{lr.ORIGIN}/#c=not-a-code-this-page-issued")
    s.wait_progress("This tab can't continue.")
    assert "did not come from the review page" in s.page.locator("#note").inner_text() and s.stops == []
    replaced = s.app.new_code().split("#c=", 1)[1]
    code = s.app.new_code().split("#c=", 1)[1]
    s.page.goto("about:blank")
    s.start(code=replaced)
    s.wait_progress("This tab can't continue.")
    assert "newer link replaced" in s.page.locator("#note").inner_text() and s.stops == []
    s.page.goto("about:blank")
    s.start(code=code)
    s.wait_progress("Approved 0 of 3. Next: demo1.")
    second = s.context.new_page()
    s.watch(second)
    s.start(second, code=code)
    second.wait_for_function("() => document.getElementById('progress').textContent === 'Stopped.'")
    assert "already used" in second.locator("#note").inner_text() and len(s.stops) == 1
    s.page.click("#open")
    s.wait_progress("Stopped.")
    assert s.page.locator("#stage iframe").count() == 0 and lr.STOPPED in s.page.locator("#note").inner_text()
    assert s.errors == []


# The stand-in against the real client, case by case. Runs only with PREPARER_REAL_CLIENT set.

PROBE_PAGE = """<!doctype html><meta charset=utf-8><script type=module>
import * as R from "%s";
import { __checks as S } from "http://probe.test/standin.js";
function realEnvelope(m) {
  const transport = new R.PostMessageTransport(window, window);
  let got = null; transport.onmessage = x => { got = x; }; transport.onerror = () => {};
  transport.messageListener({ source: window, data: m });
  return got;
}
const REAL_NOTES = { "ui/notifications/tool-result": R.McpUiToolResultNotificationSchema,
  "ui/notifications/tool-input": R.McpUiToolInputNotificationSchema,
  "ui/notifications/tool-cancelled": R.McpUiToolCancelledNotificationSchema,
  "ui/notifications/host-context-changed": R.McpUiHostContextChangedNotificationSchema };
const REAL_RESULTS = { init: R.McpUiInitializeResultSchema, display: R.McpUiRequestDisplayModeResultSchema, link: R.McpUiOpenLinkResultSchema };
const STAND_RESULTS = { init: S.initResult, display: S.displayResult, link: S.linkResult };
window.check = (kind, m) => {
  const env = realEnvelope(m);
  let real = !!env;
  const k = S.envelope(m);
  let stand = k !== "bad" && k !== "none";
  if (kind === "note") {
    real = real && REAL_NOTES[m.method].safeParse(env).success;
    stand = stand && S.NOTIFICATIONS[m.method][0](m.params);
  } else if (kind === "teardown") {
    real = real && R.McpUiResourceTeardownRequestSchema.safeParse(env).success;
    stand = stand && S.teardownParams(m.params);
  } else if (kind in REAL_RESULTS) {
    real = real && REAL_RESULTS[kind].safeParse(env.result).success;
    stand = stand && STAND_RESULTS[kind](m.result);
  }
  return { real, stand };
};
window.ready = true;
</script>""" % CLIENT_URL


def init(ctx: Any = None, caps: Any = None, info: Any = None, id_: Any = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": {
        "protocolVersion": "2026-01-26", "hostInfo": info or PAGE,
        "hostCapabilities": caps or {"serverTools": {}, "openLinks": {}, "sandbox": {"permissions": {"clipboardWrite": {}},
                                     "csp": {"resourceDomains": ["https://unpkg.com"]}}},
        "hostContext": ctx or {"theme": "light", "displayMode": "inline", "availableDisplayModes": ["inline", "fullscreen"],
                               "platform": "web", "locale": "en-US", "timeZone": "America/Chicago"}}}


def note(method: str, params: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def answer(result: Any, id_: Any = "x") -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


TR, CTX = "ui/notifications/tool-result", "ui/notifications/host-context-changed"
SERVER_RESULT = {"content": [{"type": "text", "text": "x"}], "structuredContent": {"a": 1}, "isError": False, "resultType": "complete",
                 "_meta": {"preparer/review_token": "x", "io.modelcontextprotocol/serverInfo": {"name": "preparer", "version": "0.1.0"}}}
CASES = {
    "the initialize result the page sends": ("init", init()),
    "a tool result as the server returns it": ("note", note(TR, SERVER_RESULT)),
    "a refused tool result": ("note", note(TR, {"content": [{"type": "text", "text": "Refused."}], "isError": True})),
    "a theme change": ("note", note(CTX, {"theme": "dark"})),
    "a display-mode change": ("note", note(CTX, {"displayMode": "fullscreen"})),
    "the tool input": ("note", note("ui/notifications/tool-input", {"arguments": {"draft_id": "demo1"}})),
    "a cancel with a reason": ("note", note("ui/notifications/tool-cancelled", {"reason": "x"})),
    "a teardown request": ("teardown", {"jsonrpc": "2.0", "id": "host-1", "method": "ui/resource-teardown", "params": {}}),
    "a display-mode answer": ("display", answer({"mode": "inline"})),
    "an open-link answer": ("link", answer({})),
    "an open-link refusal": ("link", answer({"isError": True})),
    "a teardown with no params": ("teardown", {"jsonrpc": "2.0", "id": "host-1", "method": "ui/resource-teardown"}),
    "a progressToken that is an object": ("note", note(TR, {"content": [], "_meta": {"progressToken": {}}})),
    "a progressToken that is a string": ("note", note(TR, {"content": [], "_meta": {"progressToken": "p"}})),
    "a tool input whose _meta is a string": ("note", note("ui/notifications/tool-input", {"_meta": "x", "arguments": {}})),
    "a related-task without a taskId": ("note", note(TR, {"content": [], "_meta": {"io.modelcontextprotocol/related-task": {}}})),
    "text with priority 5": ("note", note(TR, {"content": [{"type": "text", "text": "x", "annotations": {"priority": 5}}]})),
    "text with priority 0.5": ("note", note(TR, {"content": [{"type": "text", "text": "x", "annotations": {"priority": 0.5}}]})),
    "text modified at a time": ("note", note(TR, {"content": [{"type": "text", "text": "x", "annotations": {"lastModified": "2026-11-12T01:02:03Z"}}]})),
    "text modified yesterday": ("note", note(TR, {"content": [{"type": "text", "text": "x", "annotations": {"lastModified": "yesterday"}}]})),
    "an empty resource block": ("note", note(TR, {"content": [{"type": "resource", "resource": {}}]})),
    "a resource block with text": ("note", note(TR, {"content": [{"type": "resource", "resource": {"uri": "u", "text": "t"}}]})),
    "an image that is not base64": ("note", note(TR, {"content": [{"type": "image", "data": "%%%", "mimeType": "image/png"}]})),
    "an unknown block type": ("note", note(TR, {"content": [{"type": "video", "url": "x"}]})),
    "an unknown style variable": ("note", note(CTX, {"styles": {"variables": {"--not-a-var": "x"}}})),
    "a known style variable": ("note", note(CTX, {"styles": {"variables": {"--color-ring-warning": "red", "--font-heading-3xl-line-height": "1"}}})),
    "a pip display mode": ("note", note(CTX, {"displayMode": "pip"})),
    "a sidebar display mode": ("note", note(CTX, {"displayMode": "sidebar"})),
    "toolInfo with properties a:1": ("init", init(ctx={"toolInfo": {"tool": {"name": "t", "inputSchema": {"type": "object", "properties": {"a": 1}}}}})),
    "toolInfo as tools/list gives it": ("init", init(ctx={"toolInfo": {"id": 1, "tool": {"name": "open_review", "inputSchema": {"type": "object", "properties": {"draft_id": {"type": "string"}}, "required": ["draft_id"]}, "annotations": {"readOnlyHint": False}}}})),
    "a bare tool name in toolInfo": ("init", init(ctx={"toolInfo": {"tool": "open_review"}})),
    "an icon whose sizes is a string": ("init", init(info={"name": "h", "version": "1", "icons": [{"src": "x", "sizes": "16x16"}]})),
    "an icon with sizes": ("init", init(info={"name": "h", "version": "1", "icons": [{"src": "x", "sizes": ["16x16"]}]})),
    "a result id of 2**60": ("init", init(id_=2**60)),
    "a host without a version": ("init", init(info={"name": "h"})),
    "updateModelContext with an extra key": ("init", init(caps={"updateModelContext": {"text": {}, "extra": True}})),
    "a csp domain that is not a string": ("init", init(caps={"sandbox": {"csp": {"resourceDomains": [1]}}})),
    "safe-area insets missing a side": ("init", init(ctx={"safeAreaInsets": {"top": 0, "right": 0, "bottom": 0}})),
    "an envelope with an extra key": ("link", {"jsonrpc": "2.0", "id": "x", "result": {}, "extra": 1}),
    "a display-mode answer of sidebar": ("display", answer({"mode": "sidebar"})),
    "an open-link answer with a string isError": ("link", answer({"isError": "yes"})),
    "an error answer the page sends": ("envelope", {"jsonrpc": "2.0", "id": 3, "error": {"code": -32602, "message": "No."}}),
    "a ping answer the page sends": ("envelope", answer({}, 4)),
    "an error with a string code": ("envelope", {"jsonrpc": "2.0", "id": 3, "error": {"code": "x", "message": "No."}}),
    "an error with no message": ("envelope", {"jsonrpc": "2.0", "id": 3, "error": {"code": -32602}}),
    "a platform of browser": ("init", init(ctx={"platform": "browser"})),
    "a locale that is a number": ("init", init(ctx={"locale": 5})),
    "a clipboardWrite of true": ("init", init(caps={"sandbox": {"permissions": {"clipboardWrite": True}}})),
    "a theme of blue": ("note", note(CTX, {"theme": "blue"})),
    "display modes with sidebar": ("note", note(CTX, {"availableDisplayModes": ["inline", "sidebar"]})),
    "a cancel reason that is a number": ("note", note("ui/notifications/tool-cancelled", {"reason": 5})),
    "tool input arguments that are a list": ("note", note("ui/notifications/tool-input", {"arguments": [1]})),
}


def test_the_stand_in_accepts_and_rejects_what_the_real_client_does(browser):
    body = real_client()
    if body is None:
        pytest.skip("set PREPARER_REAL_CLIENT to a copy of the real 1.7.5 client for the one-off run (DECISIONS.md)")
    stand_in = (STAND_IN + "\n").encode()
    stray: list[str] = []

    def route(r) -> None:
        url = r.request.url
        if url == "http://probe.test/":
            r.fulfill(body=PROBE_PAGE, headers={"content-type": "text/html"})
        elif url == "http://probe.test/standin.js":
            r.fulfill(body=stand_in, headers={"content-type": "text/javascript"})
        elif url == CLIENT_URL:
            r.fulfill(body=body, headers={"content-type": "text/javascript", "access-control-allow-origin": "*"})
        else:
            stray.append(url)
            r.abort()

    context = browser.new_context()
    try:
        context.route("**/*", route)
        page = context.new_page()
        page.goto("http://probe.test/")
        page.wait_for_function("window.ready === true")
        results = {name: page.evaluate("([k, m]) => check(k, m)", [kind, message]) for name, (kind, message) in CASES.items()}
        styles = page.evaluate("""async () => {
            const S = (await import('http://probe.test/standin.js')).__checks;
            const R = await import('%s');
            const real = [...S.STYLE_KEYS].filter(k => R.McpUiHostStylesSchema.safeParse({variables: {[k]: 'x'}}).success);
            return [S.STYLE_KEYS.size, real.length]; }""" % CLIENT_URL)
    finally:
        context.close()
    assert stray == []
    differ = {name: r for name, r in results.items() if r["real"] != r["stand"]}
    assert differ == {}
    accepted = {name for name, r in results.items() if r["real"]}
    assert {"the initialize result the page sends", "a tool result as the server returns it", "a teardown request"} <= accepted
    assert styles == [76, 76]  # every style variable the stand-in knows, the real client knows too
