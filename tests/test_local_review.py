"""Preparer review screen, Block 4b: the local review page's server side, with no browser and no network socket.

ReviewPage.handle takes a plain request and gives a plain response, so these tests drive the page's routes
directly against the real preparer server: as a child over stdio, the way the page runs it, where the session
matters (the client line, the records, a server that exits), and in process where only the page's own rules are
under test. Every test that runs the page works on copies of the demo drafts in tmp_path; two lock-gate tests run
git over the live tree, and one over a throwaway repository. Tests that name the two app-only tools do
so as the screen would; the page's own code never names them.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.client.stdio import stdio_client

from preparer import local_review as lr
from preparer import server as ps
from preparer.drafts import DraftFolder, Reference, compute_flags

REPO = Path(__file__).resolve().parent.parent
DEMO = REPO / "preparer" / "app" / "demo_drafts"
DIST = REPO / "preparer" / "app" / "dist" / "review.html"
HOST_FILES = [REPO / "preparer" / "app" / f"host.{ext}" for ext in ("html", "js", "css")]
REF = Reference.load()
HOST = {"name": lr.NAME, "version": lr.VERSION}
APP_ONLY = ("save_review", "approve_review")


class Run:
    """One page on a copy of the demo drafts, with what it logged and why it stopped."""

    def __init__(self, tmp_path: Path, stdio: bool) -> None:
        self.drafts = tmp_path / "drafts"
        shutil.copytree(DEMO, self.drafts)
        self.reviews = tmp_path / "reviews"
        self.server_log = tmp_path / "server.log"
        self.lines: list[str] = []
        self.stops: list[str] = []
        self.codes: list[str] = []
        self.stdio = stdio

    def __enter__(self) -> "Run":
        if self.stdio:
            self._log = open(self.server_log, "w", encoding="utf-8")
            server: Any = stdio_client(lr.server_params(self.drafts, self.reviews), errlog=self._log)
        else:
            self._log = None
            server = ps.build_server(self.drafts, self.reviews, log=lambda line: self.lines.append("server: " + line))
        self._cm = lr.running(server, practice=True, log=self.lines.append, on_stop=self.stops.append)
        self.page = self._cm.__enter__()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._cm.__exit__(*exc)
        if self._log:
            self._log.close()

    # Requests as the host page sends them.

    def request(self, method: str, route: str, body: Any = None, *, token: Any = True, origin: Any = lr.ORIGIN,
                host: str = lr.HOST_HEADER, ctype: str = "application/json") -> tuple[int, Any, lr.Response]:
        headers = {"Host": host, "Content-Type": ctype}
        if origin is not None:
            headers["Origin"] = origin
        if token is True and self.page.token:
            headers[lr.TOKEN_HEADER] = self.page.token
        elif isinstance(token, str):
            headers[lr.TOKEN_HEADER] = token
        raw = body if isinstance(body, bytes) else json.dumps({} if body is None else body).encode()
        r = self.page.handle(method, route, headers, raw if method == "POST" else b"")
        data = json.loads(r.body) if r.content_type == "application/json" else r.body
        return r.status, data, r

    def post(self, route: str, body: Any = None, **kw: Any) -> tuple[int, Any]:
        status, data, _ = self.request("POST", route, body, **kw)
        return status, data

    def claim(self) -> str:
        code = self.page.new_code().split("#c=", 1)[1]
        self.codes.append(code)
        status, data = self.post("/api/claim", {"code": code}, token=None)
        assert status == 200, data
        return data["token"]


@pytest.fixture
def inproc(tmp_path):
    with Run(tmp_path, stdio=False) as run:
        yield run


@pytest.fixture
def stdio(tmp_path):
    with Run(tmp_path, stdio=True) as run:
        yield run


def decisions(draft_id: str) -> dict[str, Any]:
    draft = DraftFolder(DEMO, REF).load(draft_id)
    return {
        "documents": [{"rule_id": d["rule_id"], "action": "keep"} for d in draft["documents"]],
        "custom_requests": [{"index": i, "action": "keep"} for i, _ in enumerate(draft["custom_requests"])],
        "added": [], "follow_ups": [], "flags_checked": [f["id"] for f in compute_flags(draft, REF)],
    }


def screen_args(opened: dict[str, Any], draft_id: str) -> dict[str, Any]:
    """What the screen sends to save or approve, from open_review's result as the page delivered it."""
    return {"draft_id": draft_id, "review_token": opened["_meta"][ps.TOKEN_META],
            "draft_sha256": opened["structuredContent"]["draft_sha256"], "decisions": decisions(draft_id), "host": HOST}


# The view and its policy.


def test_the_page_binds_the_three_view_tools_and_serves_the_view_under_its_policy(stdio):
    view = stdio.page.view
    assert view.tools == ("open_review", *APP_ONLY)
    assert view.uri == ps.VIEW_URI
    assert view.html == DIST.read_text(encoding="utf-8")
    assert view.csp == {"resourceDomains": ["https://unpkg.com"]} and view.permissions == {"clipboardWrite": {}}
    status, body, r = stdio.request("GET", "/view")
    assert status == 200 and body == DIST.read_bytes() and r.content_type == "text/html; charset=utf-8"
    assert r.headers["Content-Security-Policy"] == (
        "default-src 'none'; script-src 'unsafe-inline' https://unpkg.com; style-src 'unsafe-inline' https://unpkg.com; "
        "img-src data: https://unpkg.com; font-src https://unpkg.com; media-src data: https://unpkg.com; "
        "connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'self'; form-action 'none'; "
        "frame-ancestors http://127.0.0.1:8766; sandbox allow-scripts"
    )
    for route, path in (("/", HOST_FILES[0]), ("/host.js", HOST_FILES[1]), ("/host.css", HOST_FILES[2])):
        status, body, r = stdio.request("GET", route)
        assert status == 200 and body == path.read_bytes()
        assert r.headers["Content-Security-Policy"] == lr.HOST_PAGE_POLICY
        assert r.headers["X-Frame-Options"] == "DENY" and r.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "'unsafe-inline'" not in lr.HOST_PAGE_POLICY and "connect-src 'self'" in lr.HOST_PAGE_POLICY


def fake_tools(**open_ui: Any) -> list[dict[str, Any]]:
    ui = {"resourceUri": "ui://preparer/review.html", **open_ui}
    return [{"name": "list_drafts"}, {"name": "open_review", "_meta": {"ui": ui}},
            {"name": "x_app", "_meta": {"ui": {"resourceUri": "ui://preparer/review.html", "visibility": ["app"]}}},
            {"name": "x_model", "_meta": {"ui": {"resourceUri": "ui://preparer/review.html", "visibility": ["model"]}}},
            {"name": "x_other", "_meta": {"ui": {"resourceUri": "ui://other/view.html"}}}]


def fake_read(mime: str = lr.VIEW_MIME, csp: Any = None, text: str = "<p>view</p>", count: int = 1):
    meta = {"ui": {"csp": csp if csp is not None else {"resourceDomains": ["https://unpkg.com"]}, "permissions": {"camera": {}}}}
    return lambda uri: {"contents": [{"uri": uri, "mimeType": mime, "text": text, "_meta": meta}] * count}


def test_the_screen_may_call_only_tools_bound_to_the_view_with_app_visibility():
    view = lr.checked_view(fake_tools(), fake_read())
    assert view.tools == ("open_review", "x_app")
    assert view.permissions == {}  # camera is declared but never granted; only clipboard writes can be
    assert lr.app_tools(fake_tools(visibility=["model"]), "ui://preparer/review.html") == ["x_app"]


@pytest.mark.parametrize("tools, read, why", [
    (fake_tools(resourceUri="https://example.test/view.html"), fake_read(), "no ui:// resource"),
    ([{"name": "list_drafts"}], fake_read(), "no open_review"),
    (fake_tools(visibility=["model"]), fake_read(), "not bound to its own view"),
    (fake_tools(), fake_read(mime="text/html"), "not text of type"),
    (fake_tools(), fake_read(text=""), "not text of type"),
    (fake_tools(), fake_read(count=2), "exactly one document"),
    (fake_tools(), fake_read(csp={"connectDomains": ["https://api.example.test"]}), "declares connectDomains"),
    (fake_tools(), fake_read(csp={"frameDomains": ["https://example.test"]}), "declares frameDomains"),
    (fake_tools(), fake_read(csp={"resourceDomains": ["http://unpkg.com"]}), "not a plain https:// origin"),
    (fake_tools(), fake_read(csp={"resourceDomains": ["https://*.unpkg.com"]}), "not a plain https:// origin"),
    (fake_tools(), fake_read(csp={"resourceDomains": ["https://unpkg.com/path"]}), "not a plain https:// origin"),
    (fake_tools(), fake_read(csp={"resourceDomains": ["https://unpkg.com 'unsafe-eval'"]}), "not a plain https:// origin"),
    (fake_tools(), fake_read(csp={"resourceDomains": ["https://unpkg.com; script-src *"]}), "not a plain https:// origin"),
    (fake_tools(), fake_read(csp={"resourceDomains": "https://unpkg.com"}), "not a plain https:// origin"),
])
def test_the_page_refuses_to_start_on_a_view_that_is_not_a_plain_mcp_app(tools, read, why):
    with pytest.raises(lr.PageError, match=why):
        lr.checked_view(tools, read)


# Host, Origin, JSON and the page token.


def test_requests_need_the_exact_host_origin_json_and_page_token(inproc):
    inproc.claim()
    assert inproc.post("/api/state")[0] == 200
    assert inproc.post("/api/state", host="localhost:8766")[0] == 403
    assert inproc.post("/api/state", host="127.0.0.1:8765")[0] == 403
    assert inproc.request("GET", "/", host="evil.test:8766")[0] == 403
    assert inproc.post("/api/state", origin=None)[0] == 403
    assert inproc.post("/api/state", origin="null")[0] == 403
    assert inproc.post("/api/state", origin="http://localhost:8766")[0] == 403
    assert inproc.post("/api/state", origin="127.0.0.1:8766")[0] == 403
    assert inproc.request("GET", "/", origin="https://evil.test")[0] == 403
    assert inproc.post("/api/state", ctype="text/plain")[0] == 415
    assert inproc.post("/api/state", b"[1]")[0] == 400
    assert inproc.post("/api/state", b"{not json")[0] == 400
    assert inproc.post("/api/state", b"{" + b" " * lr.MAX_BODY_BYTES + b"}")[0] == 413
    assert inproc.post("/api/state", token=None)[0] == 401
    assert inproc.post("/api/state", token="x" * 43)[0] == 401
    assert inproc.request("GET", "/api/state")[0] == 405
    assert inproc.request("PUT", "/api/state")[0] == 405
    assert inproc.request("OPTIONS", "/api/state")[0] == 405
    assert inproc.post("/view")[0] == 405
    assert inproc.request("GET", "/nothing")[0] == 404
    assert inproc.request("GET", "/../preparer/server.py")[0] == 404
    assert inproc.post("/api/nothing")[0] == 404
    assert inproc.stops == []
    reasons = [line.split(": ", 1)[1] for line in inproc.lines if line.startswith("refused")]
    assert reasons.count("a foreign Host") == 3 and reasons.count("a foreign Origin") == 1
    assert reasons.count("missing or foreign Origin") == 4 and reasons.count("not JSON") == 1
    assert reasons.count("a body that is not an object") == 1 and reasons.count("a body that is not JSON") == 1
    assert reasons.count("a body over the limit") == 1 and reasons.count("missing or retired page token") == 2
    assert [r for r in reasons if r.startswith("method")] == ["method GET", "method PUT", "method OPTIONS", "method POST"]
    assert "refused (other route) tool (none): a foreign Host" not in inproc.lines  # each logged route is a real one
    assert not any("evil" in line or "localhost" in line for line in inproc.lines)  # no header values


def test_the_page_has_exactly_its_nine_routes(inproc):
    """Criterion 3's route snapshot: four pages to read and five API routes, none of which sends anything anywhere."""
    assert lr.GET_ROUTES == ("/", "/host.js", "/host.css", "/view") and set(lr.HOST_FILES) | {"/view"} == set(lr.GET_ROUTES)
    assert lr.POST_ROUTES == ("/api/claim", "/api/state", "/api/open", "/api/call", "/api/close")
    inproc.claim()
    answered = set()
    paths = [*lr.GET_ROUTES, *lr.POST_ROUTES, "/index.html", "/api", "/api/", "/api/send", "/api/save", "/api/approve",
             "/view/", "/host", "/favicon.ico", "/api/state/x", "//", "/.."]
    for method in ("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
        for path in paths:
            body = {"/api/open": {"draft_id": "demo1"},
                    "/api/call": {"name": "open_review", "arguments": {"draft_id": "demo1"}}}.get(path, {})
            status, _, _ = inproc.request(method, path, body)
            if status == 200:
                answered.add((method, path))
            else:
                assert status in (400, 403, 404, 405, 409), (method, path, status)
    assert answered == {("GET", r) for r in lr.GET_ROUTES} | {("POST", r) for r in lr.POST_ROUTES if r != "/api/claim"}


def test_a_launch_code_works_once_and_a_second_claim_of_it_stops_the_page(inproc):
    code = inproc.page.new_code().split("#c=", 1)[1]
    status, data = inproc.post("/api/claim", {"code": code}, token=None)
    assert status == 200 and re.fullmatch(r"[A-Za-z0-9_-]{43}", data["token"])
    token = data["token"]
    assert inproc.post("/api/state", token=token)[0] == 200
    status, data = inproc.post("/api/claim", {"code": code}, token=None)
    assert status == 403 and "already used" in data["error"] and data["stopped"] is True
    assert len(inproc.stops) == 1 and "claimed a second time" in inproc.stops[0]
    assert inproc.post("/api/state", token=token)[0] == 503
    assert inproc.request("GET", "/")[0] == 503
    assert any(line.startswith("refused /api/claim") for line in inproc.lines)


@pytest.mark.parametrize("body", [{"code": "wrong"}, {"code": None}, {}, {"code": ["x"]}, {"code": "\ud800"},
                                  {"code": "x" * 500}, {"code": ""}])
def test_a_claim_with_a_code_never_issued_is_refused_without_stopping_the_page(inproc, body):
    link = inproc.page.new_code()
    status, data = inproc.post("/api/claim", body, token=None)
    assert status == 403 and "did not come from the review page" in data["error"] and "stopped" not in data
    assert inproc.stops == [] and inproc.page.token is None
    assert inproc.lines[-1] == "refused /api/claim tool (none): a code this page never issued"
    status, _ = inproc.post("/api/claim", {"code": link.split("#c=", 1)[1]}, token=None)
    assert status == 200  # the issued code still works


def test_a_link_a_newer_one_replaced_is_refused_without_stopping_the_page(inproc):
    first = inproc.page.new_code().split("#c=", 1)[1]
    second = inproc.page.new_code().split("#c=", 1)[1]
    status, data = inproc.post("/api/claim", {"code": first}, token=None)
    assert status == 409 and "newer link replaced" in data["error"] and inproc.stops == []
    assert inproc.lines[-1] == "refused /api/claim tool (none): a link that a newer one replaced"
    status, data = inproc.post("/api/claim", {"code": second}, token=None)
    assert status == 200
    status, data = inproc.post("/api/claim", {"code": first}, token=None)
    assert status == 409 and inproc.stops == []
    assert inproc.post("/api/state", token=data.get("token") or inproc.page.token)[0] == 200


def test_a_fresh_link_retires_the_old_tab(inproc):
    first = inproc.claim()
    second = inproc.claim()
    assert first != second
    assert inproc.post("/api/state", token=first)[0] == 401
    assert inproc.post("/api/state", token=second)[0] == 200
    assert inproc.stops == []


# Order and blindness.


def test_state_shows_only_the_next_id_and_the_approved_count(inproc):
    inproc.claim()
    status, data = inproc.post("/api/state")
    assert status == 200
    assert data == {"next": "demo1", "approved": 0, "total": 3, "open": None, "practice": True, "host": HOST,
                    "sandbox": {"csp": {"resourceDomains": ["https://unpkg.com"]}, "permissions": {"clipboardWrite": {}}}}
    names = [d["client_name"] for d in DraftFolder(DEMO, REF).load_all().values()]
    assert names and not any(name in json.dumps(data) for name in names)


def test_drafts_come_in_id_order_with_unreadable_ones_still_in_line():
    text = ("d97: Pat Example, 3 documents, 1 flags, approved\nd98: can't be read (bad file)\n"
            "d99: Sam Sample, approved, 2 documents, 0 flags, in_review\nnoise\n")
    assert lr.drafts_from(text) == [("d97", "approved"), ("d98", "unreadable"), ("d99", "in_review")]
    assert lr.drafts_from("No drafts in the drafts folder.") == []


def test_only_the_next_draft_opens_and_the_relay_holds_to_it(stdio):
    stdio.claim()
    assert stdio.post("/api/call", {"name": "open_review", "arguments": {"draft_id": "demo1"}})[0] == 409
    assert stdio.post("/api/open", {"draft_id": "demo2"})[0] == 409
    assert stdio.post("/api/open", {})[0] == 409
    status, data = stdio.post("/api/open", {"draft_id": "demo1"})
    assert status == 200 and data["draft_id"] == "demo1"
    opened = data["result"]
    assert opened["isError"] is False and opened["structuredContent"]["draft"]["draft_id"] == "demo1"
    assert isinstance(opened["_meta"][ps.TOKEN_META], str)
    assert stdio.post("/api/state")[1]["open"] == "demo1"
    for name in ("list_drafts", "approved_list", "nothing", "", None, 7):
        assert stdio.post("/api/call", {"name": name, "arguments": {"draft_id": "demo1"}})[0] == 409
    args = screen_args(opened, "demo1")
    assert stdio.post("/api/call", {"name": "save_review", "arguments": {**args, "draft_id": "demo2"}})[0] == 409
    assert stdio.post("/api/call", {"name": "save_review", "arguments": [args]})[0] == 409
    assert stdio.post("/api/call", {"name": "save_review"})[0] == 409
    assert stdio.post("/api/open", {"draft_id": "demo1"})[0] == 200  # the same draft again is the screen's reload
    status, data = stdio.post("/api/call", {"name": "open_review", "arguments": {"draft_id": "demo1"}})
    assert status == 200 and data["result"]["structuredContent"]["open_number"] == 3
    assert stdio.post("/api/close")[0] == 200
    assert stdio.post("/api/call", {"name": "open_review", "arguments": {"draft_id": "demo1"}})[0] == 409
    refused = [line for line in stdio.lines if line.startswith("refused /api/")]
    assert len(refused) == 13 and all(re.fullmatch(r"refused /api/(open|call) tool \S+: [a-z ]+", line) for line in refused)
    assert not (stdio.reviews / "demo1.json").exists()


def test_a_full_review_names_the_page_as_client_and_host_and_no_log_carries_a_secret(stdio):
    stdio.claim()
    opened = stdio.post("/api/open", {"draft_id": "demo1"})[1]["result"]
    args = screen_args(opened, "demo1")
    status, data = stdio.post("/api/call", {"name": "save_review", "arguments": args})
    assert status == 200 and data["result"]["isError"] is False
    status, data = stdio.post("/api/call", {"name": "approve_review", "arguments": args})
    assert status == 200 and data["result"]["structuredContent"]["record"]["status"] == "approved"
    status, data = stdio.post("/api/call", {"name": "approve_review", "arguments": args})
    assert status == 200 and data["result"]["isError"] is True and data["result"]["content"][0]["text"] == ps.FINAL
    stdio.post("/api/close")
    assert stdio.post("/api/state")[1] | {"host": None, "sandbox": None} == {
        "next": "demo2", "approved": 1, "total": 3, "open": None, "practice": True, "host": None, "sandbox": None}
    record = json.loads((stdio.reviews / "demo1.json").read_text())
    assert record["client"] == HOST and record["host"] == HOST
    openings = [json.loads(line) for line in (stdio.reviews / "openings.jsonl").read_text().splitlines()]
    assert len(openings) == 1 and openings[0]["client"] == HOST
    log = stdio.server_log.read_text()
    first = log.splitlines()[0]
    assert first == ('preparer: client {"name": "preparer-local-host", "version": "0.1.0"} advertised extensions '
                     '{"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}')
    secrets_seen = [stdio.page.token, *stdio.codes, args["review_token"]]
    for text in (log, "\n".join(stdio.lines)):
        assert not any(s and s in text for s in secrets_seen)


def test_the_page_stops_when_the_server_exits(stdio):
    token = stdio.claim()
    found = subprocess.run(["pgrep", "-P", str(os.getpid()), "-f", "preparer.server"], capture_output=True, text=True)
    pids = [int(p) for p in found.stdout.split()]
    assert len(pids) == 1
    os.kill(pids[0], signal.SIGTERM)
    deadline = time.monotonic() + 10
    while not stdio.stops and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(stdio.stops) == 1 and "server exited" in stdio.stops[0]
    assert stdio.post("/api/state", token=token)[0] == 503
    assert stdio.request("GET", "/view")[0] == 503


def test_a_relay_call_on_a_closed_session_stops_the_page(inproc):
    inproc.claim()
    inproc.page.relay.closed = True
    status, data = inproc.post("/api/state")
    assert status == 503 and data["error"] == lr.STOPPED and len(inproc.stops) == 1


class FakeClient:
    def __init__(self, behavior: str) -> None:
        self.behavior = behavior

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        import anyio
        from mcp import MCPError
        from mcp.types import CONNECTION_CLOSED
        if self.behavior == "slow":
            await anyio.sleep(5)
        if self.behavior == "closed":
            raise MCPError(CONNECTION_CLOSED, "gone")  # known by its code, not its words
        if self.behavior == "protocol":
            raise MCPError(-32602, "Unknown tool: x")
        raise anyio.BrokenResourceError()


def test_the_relay_tells_a_closed_session_from_a_timeout_and_a_protocol_error(monkeypatch):
    from anyio.from_thread import start_blocking_portal
    from mcp import MCPError
    monkeypatch.setattr(lr, "CALL_TIMEOUT_S", 0.05)
    with start_blocking_portal() as portal:
        with pytest.raises(TimeoutError):
            lr.Relay(portal, FakeClient("slow")).call("x", {})
        for behavior in ("closed", "broken"):
            relay = lr.Relay(portal, FakeClient(behavior))
            with pytest.raises(lr.RelayClosed):
                relay.call("x", {})
            assert relay.closed is True
        relay = lr.Relay(portal, FakeClient("protocol"))
        with pytest.raises(MCPError):
            relay.call("x", {})
        assert relay.closed is False


@pytest.mark.parametrize("behavior, status, stops", [("slow", 504, 0), ("protocol", 502, 0), ("closed", 503, 1)])
def test_each_relay_failure_gets_its_own_answer(inproc, monkeypatch, behavior, status, stops):
    inproc.claim()
    monkeypatch.setattr(lr, "CALL_TIMEOUT_S", 0.05)
    inproc.page.relay.client = FakeClient(behavior)
    got, data = inproc.post("/api/state")
    assert got == status and len(inproc.stops) == stops
    if behavior == "protocol":
        assert data["error"] == "The preparer server could not run that call: Unknown tool: x"
        assert inproc.lines[-1] == "/api/state: the server answered with protocol error -32602"


def test_a_refused_view_or_a_ctrl_c_comes_out_of_the_session_unwrapped(tmp_path, monkeypatch):
    drafts = tmp_path / "drafts"
    shutil.copytree(DEMO, drafts)

    def refuse(tools, read):
        raise lr.PageError("the view declares connectDomains, which this page does not grant")

    with monkeypatch.context() as m:
        m.setattr(lr, "checked_view", refuse)
        with pytest.raises(lr.PageError, match="connectDomains"):
            with lr.running(ps.build_server(drafts, tmp_path / "r1", log=lambda line: None), practice=True, log=lambda line: None):
                pass
    with pytest.raises(KeyboardInterrupt):
        with lr.running(ps.build_server(drafts, tmp_path / "r2", log=lambda line: None), practice=True, log=lambda line: None):
            raise KeyboardInterrupt
    with pytest.raises(lr.PageError, match="inside"):
        with lr.running(ps.build_server(drafts, tmp_path / "r3", log=lambda line: None), practice=True, log=lambda line: None):
            raise lr.PageError("inside")


# The HTTP handler.


class StubServer:
    pass


def through_handler(page: lr.ReviewPage | None, request: bytes) -> tuple[bytes, bytes]:
    a, b = socket.socketpair(socket.AF_UNIX)

    def serve() -> None:
        try:
            lr.handler_for(lambda: page)(a, ("127.0.0.1", 0), StubServer())
        finally:
            a.close()

    t = threading.Thread(target=serve)
    t.start()
    try:
        b.sendall(request)
        with contextlib.suppress(OSError):  # the handler may already have answered and closed its end
            b.shutdown(socket.SHUT_WR)
        chunks = []
        while chunk := b.recv(65536):
            chunks.append(chunk)
    finally:
        t.join(timeout=10)
        b.close()
    head, _, body = b"".join(chunks).partition(b"\r\n\r\n")
    return head, body


def test_the_http_handler_sends_the_safety_headers_and_never_cors(inproc):
    head, body = through_handler(inproc.page, b"GET / HTTP/1.1\r\nHost: 127.0.0.1:8766\r\n\r\n")
    assert head.startswith(b"HTTP/1.0 200") and body == HOST_FILES[0].read_bytes()
    for header in (b"Content-Security-Policy: default-src 'none'", b"X-Content-Type-Options: nosniff", b"Cache-Control: no-store",
                   b"Referrer-Policy: no-referrer", b"X-Frame-Options: DENY", b"Cross-Origin-Resource-Policy: same-origin"):
        assert header in head
    head, _ = through_handler(inproc.page, b"OPTIONS /api/state HTTP/1.1\r\nHost: 127.0.0.1:8766\r\n"
                                           b"Origin: https://evil.test\r\nAccess-Control-Request-Method: POST\r\n\r\n")
    assert head.startswith(b"HTTP/1.0 405") and b"Access-Control" not in head
    head, _ = through_handler(inproc.page, b"POST /api/state HTTP/1.1\r\nHost: 127.0.0.1:8766\r\nContent-Length: 99999999\r\n\r\n")
    assert head.startswith(b"HTTP/1.0 413") and inproc.lines[-1] == "refused /api/state tool (none): a body over the limit"
    head, body = through_handler(None, b"GET / HTTP/1.1\r\nHost: 127.0.0.1:8766\r\n\r\n")
    assert head.startswith(b"HTTP/1.0 503") and b"starting" in body


# Starting up: the lock, the folders, the browser.


def fake_git(answers: dict[str, tuple[int, str]]):
    """git and the build check answered from a table by subcommand; the child that reads locked_on runs for real."""
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "-c":
            return subprocess.run([sys.executable, *cmd[1:]], input=kw.get("input"), capture_output=True, text=True)
        key = "build" if cmd[1:] == ["-m", "preparer.build", "--check"] else cmd[1]
        code, out = answers.get(key, (0, ""))
        return SimpleNamespace(returncode=code, stdout=out, stderr="")

    return run, calls


LOCK = "abc123def4567890"
LOCKED = {"log": (0, f"{LOCK}\n"), "show": (0, "# a comment\nlocked_on: 2026-11-12   # the lock\ndrafts: 24\n")}
WATCHED = ["eval/review_prereg.yaml", "preparer", "data/drafts"]


@pytest.mark.parametrize("answers, env, expect", [
    (LOCKED, {}, []),
    (LOCKED, {"CLAUDECODE": "1"}, ["Claude Code session"]),
    ({**LOCKED, "log": (0, "")}, {}, ["never been committed"]),
    ({**LOCKED, "show": (0, "locked_on: '2026-11-12'\n")}, {}, []),
    ({**LOCKED, "show": (0, "locked_on: null\n")}, {}, ["is not locked"]),
    ({**LOCKED, "show": (0, "locked_on: ~\n")}, {}, ["is not locked"]),
    ({**LOCKED, "show": (0, "locked_on:\n")}, {}, ["is not locked"]),
    ({**LOCKED, "show": (0, "locked_on: true\n")}, {}, ["is not locked"]),
    ({**LOCKED, "show": (0, "locked_on: '2026-11-31'\n")}, {}, ["is not locked"]),
    ({**LOCKED, "show": (0, "locked_on: 2026-11-12T09:00:00Z\n")}, {}, ["is not locked"]),
    ({**LOCKED, "show": (0, "drafts:\n  locked_on: 2026-11-12\n")}, {}, ["is not locked"]),
    ({**LOCKED, "show": (0, "locked_on: 2026-11-31\n")}, {}, ["does not parse"]),
    ({**LOCKED, "show": (0, "locked_on: [unclosed\n")}, {}, ["does not parse"]),
    ({**LOCKED, "show": (128, "")}, {}, ["does not parse"]),
    ({**LOCKED, "rev-parse": (0, "true\n")}, {}, ["shallow"]),
    ({**LOCKED, "ls-files": (0, "data/drafts/d99.bak.json\n")}, {}, [".json files git ignores, so the lock can't vouch for them: data/drafts/d99.bak.json"]),
    ({**LOCKED, "ls-files": (0, "data/drafts/.DS_Store\n")}, {}, []),
    ({**LOCKED, "status": (0, " M data/drafts/d99.json\n")}, {}, ["uncommitted changes"]),
    ({**LOCKED, "status": (0, "?? preparer/new.py\n")}, {}, ["uncommitted changes"]),
    ({**LOCKED, "diff": (1, "")}, {}, ["changed after the lock commit abc123def456"]),
    ({**LOCKED, "build": (1, "")}, {}, ["built view is stale"]),
    ({**LOCKED, "show": (0, "locked_on: null\n"), "diff": (1, ""), "build": (1, "")}, {},
     ["is not locked", "changed after", "stale"]),
])
def test_the_lock_gate_needs_a_committed_lock_and_an_unchanged_tree(answers, env, expect):
    run, calls = fake_git(answers)
    refusals = lr.lock_refusals(Path("/repo"), env, run, python="python")
    assert len(refusals) == len(expect) and all(e in r for e, r in zip(expect, refusals))
    assert all(c[0] in ("git", "python") for c in calls)
    if not expect and not env:
        assert calls == [
            ["git", "rev-parse", "--is-shallow-repository"],
            ["git", "log", "-1", "--format=%H", "--", "eval/review_prereg.yaml"],
            ["git", "show", f"{LOCK}:eval/review_prereg.yaml"],
            ["python", "-c", lr.READ_LOCK],
            ["git", "status", "--porcelain", "--untracked-files=all", "--", *WATCHED],
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--", "data/drafts"],
            ["git", "diff", "--quiet", LOCK, "HEAD", "--", *WATCHED],
            ["python", "-m", "preparer.build", "--check"],
        ]


def test_the_lock_gate_on_a_real_git_history(tmp_path):
    """The same gate over a throwaway repository: real git, with only the build check stubbed."""
    repo = tmp_path / "repo"
    for folder in ("eval", "preparer", "data/drafts"):
        (repo / folder).mkdir(parents=True)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    def run(cmd, **kw):
        if cmd[1:] == ["-m", "preparer.build", "--check"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return subprocess.run([sys.executable, *cmd[1:]] if cmd[0] == "python" else cmd, **kw)

    def gate() -> list[str]:
        return lr.lock_refusals(repo, {}, run, python="python")

    git("init", "-q")
    git("config", "user.email", "test@example.test")
    git("config", "user.name", "Test")
    git("config", "commit.gpgsign", "false")
    prereg = repo / "eval" / "review_prereg.yaml"
    (repo / ".gitignore").write_text(".DS_Store\n*.bak.json\n")
    prereg.write_text("locked_on: null\n")
    (repo / "preparer" / "x.py").write_text("x = 1\n")
    (repo / "data" / "drafts" / "d97.json").write_text("{}\n")
    git("add", "-A")
    git("commit", "-qm", "before the lock")
    assert [r.split(":")[0] for r in gate()] == ["eval/review_prereg.yaml is not locked"]
    prereg.write_text("locked_on: 2026-11-12           # the lock\n")
    git("commit", "-qam", "lock")
    assert gate() == []
    (repo / "data" / "drafts" / ".DS_Store").write_bytes(b"finder")
    assert gate() == []
    (repo / "data" / "drafts" / "d98.bak.json").write_text("{}\n")
    assert [r for r in gate() if "git ignores" in r] == [
        "data/drafts holds .json files git ignores, so the lock can't vouch for them: data/drafts/d98.bak.json."]
    (repo / "data" / "drafts" / "d98.bak.json").unlink()
    (repo / "preparer" / "x.py").write_text("x = 2\n")
    assert len(gate()) == 1 and "uncommitted changes" in gate()[0]
    git("commit", "-qam", "a change after the lock")
    assert len(gate()) == 1 and "changed after the lock commit" in gate()[0]


def test_the_lock_gate_refuses_the_working_tree_until_the_prereg_is_locked():
    """Over the live tree: until Block 5 sets locked_on this must refuse; after that the fake-git and throwaway-repo
    tests above carry the gate's logic."""
    import yaml
    prereg = yaml.safe_load((REPO / "eval" / "review_prereg.yaml").read_text())
    refusals = lr.lock_refusals(env={})
    if not prereg.get("locked_on"):
        assert any("is not locked" in r for r in refusals)
    assert not any("Claude Code" in r for r in refusals)


class StubHTTPServer:
    """Stands in for the page's HTTP server in main(), so no socket opens."""

    def __init__(self, address: Any, handler: Any) -> None:
        self.done = threading.Event()
        self.daemon_threads = False

    def serve_forever(self) -> None:
        self.done.wait(30)

    def shutdown(self) -> None:
        self.done.set()

    def server_close(self) -> None:
        pass


@pytest.fixture
def main_run(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "ThreadingHTTPServer", StubHTTPServer)
    monkeypatch.setattr(lr, "DEMO_REVIEWS", tmp_path / "practice")
    monkeypatch.setattr(lr, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    def page_log() -> list[str]:
        return [line.split(" ", 1)[1] for line in next((tmp_path / "logs").glob("*-page.log")).read_text().splitlines()]

    return SimpleNamespace(practice=tmp_path / "practice", page_log=page_log)


def test_main_prints_a_refused_views_reason_logs_it_and_leaves_no_practice_folder(main_run, monkeypatch, capsys):
    def refuse(tools, read):
        raise lr.PageError("the view declares connectDomains, which this page does not grant")

    monkeypatch.setattr(lr, "checked_view", refuse)
    assert lr.main(["--practice"]) == 2
    assert "error: the page can't start: the view declares connectDomains" in capsys.readouterr().err
    assert main_run.page_log()[-1] == "refused to start: the view declares connectDomains, which this page does not grant"
    assert list(main_run.practice.iterdir()) == []


def test_main_ends_a_ctrl_c_during_the_first_launch_as_a_ctrl_c(main_run, monkeypatch, capsys):
    def interrupted(url, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(lr, "open_chrome", interrupted)
    assert lr.main(["--practice"]) == 0
    out = capsys.readouterr().out
    assert out.rstrip().endswith("Stopped: stopped with Ctrl+C.")
    assert main_run.page_log()[-1] == "stopped: stopped with Ctrl+C"
    assert not any("server exited" in line for line in main_run.page_log())


def test_main_ends_a_ctrl_c_during_the_lock_gate_cleanly(monkeypatch, capsys):
    monkeypatch.delenv("CLAUDECODE", raising=False)

    def interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(lr, "lock_refusals", interrupted)
    assert lr.main([]) == 0
    assert capsys.readouterr().out == "Stopped: stopped with Ctrl+C.\n"


def test_main_refuses_data_drafts_from_a_claude_code_session_before_it_listens(monkeypatch, capsys):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert lr.main([]) == 2
    assert "Claude Code session" in capsys.readouterr().err


def test_each_practice_run_gets_a_new_folder(tmp_path):
    from datetime import datetime
    now = datetime(2026, 11, 12, 9, 30, 0)
    first, second = lr.practice_folder(tmp_path, now), lr.practice_folder(tmp_path, now)
    assert first.name == "20261112-093000" and second.name == "20261112-093000-1"
    assert first.is_dir() and second.is_dir() and not any(first.iterdir())


def test_chrome_opens_on_its_own_profile_and_never_the_default_browser(tmp_path):
    command = lr.chrome_command("http://127.0.0.1:8766/#c=abc", tmp_path / "profile")
    assert command[:4] == ["open", "-na", "Google Chrome", "--args"] and command[-1] == "http://127.0.0.1:8766/#c=abc"
    for flag in (f"--user-data-dir={tmp_path / 'profile'}", "--disable-extensions", "--disable-sync", "--no-first-run"):
        assert flag in command
    out = io.StringIO()
    assert lr.open_chrome("http://127.0.0.1:8766/#c=abc", run=lambda *a, **k: SimpleNamespace(returncode=1),
                          profile=tmp_path / "profile", out=out) is False
    assert "did not start" in out.getvalue() and "open -na 'Google Chrome' --args" in out.getvalue()

    def missing(*a, **k):
        raise FileNotFoundError("open")

    assert lr.open_chrome("http://127.0.0.1:8766/#c=abc", run=missing, profile=tmp_path / "p", out=io.StringIO()) is False
    assert lr.CHROME_PROFILE.parts[-3:] == ("Application Support", "tax-agent-ask", "review-chrome-profile")


# What the page's code never does.


SOURCE = REPO / "preparer" / "local_review.py"


def test_the_page_imports_no_other_preparer_module_and_no_model_client():
    out = subprocess.run([sys.executable, "-c", "import json, sys, preparer.local_review; print(json.dumps(sorted(sys.modules)))"],
                         cwd=REPO, capture_output=True, text=True, check=True)
    modules = json.loads(out.stdout)
    assert {m for m in modules if m.split(".")[0] in ("preparer", "server", "rules", "eval", "generator", "harness")} == {
        "preparer", "preparer.local_review"}
    assert not [m for m in modules if m.split(".")[0] in ("anthropic", "claude_agent_sdk", "webbrowser")]
    imported = set()
    for node in ast.walk(ast.parse(SOURCE.read_text())):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not [m for m in imported if m.startswith("preparer")]


def test_the_page_never_names_the_app_only_tools_and_has_no_text_input_storage_or_console():
    for path in [SOURCE, *HOST_FILES]:
        text = path.read_text()
        assert not any(name in text for name in APP_ONLY), path.name
    host = "\n".join(p.read_text() for p in HOST_FILES).lower()
    for banned in ("<input", "<textarea", "<select", "contenteditable", "localstorage", "sessionstorage", "indexeddb",
                   "document.cookie", "console.", "innerhtml", "eval(", "new function"):
        assert banned not in host, banned
    # The page token goes only into the API's header: no line of host.js passes it anywhere else.
    js = HOST_FILES[1].read_text()
    uses = [line.split("//")[0].strip() for line in js.splitlines() if re.search(r"\btoken\b", line.split("//")[0])]
    assert uses == [
        "let token = null;",
        'if (token) headers["x-review-page-token"] = token;',
        "token = null;",
        'ui.progress.textContent = claiming ? "Connecting..." : token ? "Could not load the drafts." : "Waiting for a link.";',
        "const retry = !state && !!token && !claiming;",
        "if (!state && token && !opening && !stopped) {",
        'token = (await api("/api/claim", { code })).token;',
    ]


def test_the_page_opens_nothing_under_data(tmp_path):
    data = (REPO / "data").resolve()
    seen: list[str] = []
    watching = {"on": False}

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if watching["on"] and event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            path = os.fsdecode(args[0])
            try:
                resolved = Path(path).resolve()
            except OSError:
                return
            if resolved == data or data in resolved.parents:
                seen.append(path)

    sys.addaudithook(hook)
    watching["on"] = True
    try:
        with Run(tmp_path, stdio=True) as run:
            run.claim()
            run.post("/api/state")
            opened = run.post("/api/open", {"draft_id": "demo1"})[1]["result"]
            run.post("/api/call", {"name": "save_review", "arguments": screen_args(opened, "demo1")})
            run.post("/api/close")
            run.request("GET", "/view")
        lr.lock_refusals(env={})
    finally:
        watching["on"] = False
    assert seen == []
