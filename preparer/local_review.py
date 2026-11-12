"""The local review page: an MCP Apps host on 127.0.0.1 for the preparer server and its review screen.

    uv run python -m preparer.local_review --practice    the three demo drafts, a new practice reviews folder
    uv run python -m preparer.local_review               the 24 drafts in data/drafts, only after the prereg lock

Run it in Terminal.app, not in Claude Desktop's terminal panel, which a Claude session can read. It opens
Google Chrome on a profile of its own at a link with a single-use launch code. Enter in the Terminal
window opens a fresh link there, and Ctrl+C stops the page (PRD-preparer-review.md, section 6.5).

What it is. One process that starts the unchanged server, `python -m preparer.server`, as its child over
stdio and holds one MCP session with it for its whole life, as client "preparer-local-host" advertising
MCP Apps, because review tokens live only in the server's memory. The page in Chrome frames the HTML the
server returns from resources/read in a sandboxed iframe with an opaque origin, under the policy the
resource declares, and speaks the MCP Apps protocol to the screen's client (preparer/app/host.js). It has
no review logic of its own: every check on a save or an approval is the server's.

What it relays. A screen's tool call reaches the server only for a tool the server binds to the view with
app visibility (an absent visibility is the spec's default of model and app), only for the open draft,
and only with the page token. The page offers only the lowest-numbered draft not yet approved and shows
only its ID and how many of the drafts are approved; list_drafts' client names and counts never leave
this process.

What it guards. It listens on 127.0.0.1:8766 only and answers only the exact Host and, on every POST, the
exact Origin, with JSON. /api/claim trades a single-use launch code for the page token, which lives only
in the page's memory. A second claim of a code already used is refused, logged, and stops the page, so a
stolen code shows; a claim of a code a newer link replaced, or of one never issued, is refused and
logged. Every other API route needs the token. Nothing grants CORS and no route sends anything anywhere.
On data/drafts it refuses to start from a Claude Code session, and until the prereg parses with locked_on
set to the lock date and is committed, preparer/ and data/drafts are unchanged since the lock commit, and
the built view is current, all checked by child processes, so this process itself opens nothing under
data/. It imports no model
client and no preparer module besides the preparer package and this one. If the server exits, the page
stops and says so, since a restart would retire every review token.

What it writes. The server's stderr goes to a new log for each run under ~/Library/Logs/tax-agent-ask/,
and the page's own refusals to a second log there, by route, tool name and reason, never a request's
arguments or headers. The lock gate's refusals and a failure to listen are printed in the Terminal window
only; a refused view also goes to the page log. Neither log, nor the
Terminal window unless Chrome won't start, carries the page token, a launch code or a review token.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import shlex
import subprocess
import sys
import threading
import traceback
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator, Mapping, Sequence, TextIO

import anyio
from anyio.from_thread import BlockingPortal, start_blocking_portal
from mcp import Client, MCPError
from mcp.client import advertise
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CONNECTION_CLOSED, Implementation

HOST = "127.0.0.1"
PORT = 8766
HOST_HEADER = f"{HOST}:{PORT}"
ORIGIN = f"http://{HOST_HEADER}"
NAME = "preparer-local-host"
VERSION = "0.1.0"
UI_EXTENSION = "io.modelcontextprotocol/ui"
VIEW_MIME = "text/html;profile=mcp-app"
APPS = advertise(UI_EXTENSION, {"mimeTypes": [VIEW_MIME]})

REPO = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent / "app"
HOST_FILES = {
    "/": (APP_DIR / "host.html", "text/html; charset=utf-8"),
    "/host.js": (APP_DIR / "host.js", "text/javascript; charset=utf-8"),
    "/host.css": (APP_DIR / "host.css", "text/css; charset=utf-8"),
}
# Every route the page has. Nothing else answers, and none of them sends anything anywhere.
GET_ROUTES = ("/", "/host.js", "/host.css", "/view")
POST_ROUTES = ("/api/claim", "/api/state", "/api/open", "/api/call", "/api/close")
DEMO_DRAFTS = APP_DIR / "demo_drafts"
DEMO_REVIEWS = APP_DIR / "demo_reviews"
DRAFTS = "data/drafts"    # passed to the server; this process never opens them
REVIEWS = "data/reviews"
PREREG = "eval/review_prereg.yaml"
LOCKED_PATHS = (PREREG, "preparer", DRAFTS)
LOG_DIR = Path.home() / "Library" / "Logs" / "tax-agent-ask"
CHROME_PROFILE = Path.home() / "Library" / "Application Support" / "tax-agent-ask" / "review-chrome-profile"

OPEN_TOOL = "open_review"
LIST_TOOL = "list_drafts"
TOKEN_HEADER = "x-review-page-token"
MAX_BODY_BYTES = 1_000_000
REQUEST_TIMEOUT_S = 30
CALL_TIMEOUT_S = 60
STATUSES = ("pending_review", "in_review", "approved")
APPROVED = "approved"
DRAFT_LINE = re.compile(r"(?P<id>[A-Za-z0-9_-]{1,64}): .*?(?:, (?P<status>" + "|".join(STATUSES) + "))?")
PLAIN_ORIGIN = re.compile(r"https://[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*(?::[0-9]{1,5})?")
TOOL_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
# Run as a child on the committed prereg's text: prints locked_on as a date, or nothing, and fails if the YAML doesn't parse.
READ_LOCK = (
    "import datetime, sys, yaml\n"
    "d = yaml.safe_load(sys.stdin.read())\n"
    "v = d.get('locked_on') if isinstance(d, dict) else None\n"
    "if isinstance(v, datetime.date) and not isinstance(v, datetime.datetime): print(v.isoformat())\n"
    "elif isinstance(v, str): print(v)\n"
)

STOPPED = "The review page has stopped. Its Terminal window says why."
STALE_TAB = "This tab's link is no longer current. Press Enter in the Terminal window that runs the page for a fresh one."
SERVER_EXITED = "the preparer server exited, so every review token it held is gone. Start the page again"
CODE_REUSED = ("a launch code was claimed a second time, so someone else may hold the page token. Close the page's "
               "Chrome window and start the page again")


class PageError(RuntimeError):
    """The page can't start, or the server answered something it can't use, with a plain reason."""


class RelayClosed(RuntimeError):
    """The MCP session with the server is gone."""


# Responses.


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict)


def json_response(status: int, obj: Any) -> Response:
    return Response(status, json.dumps(obj).encode("utf-8"))


def error(status: int, message: str, **extra: Any) -> Response:
    return json_response(status, {"error": message, **extra})


HOST_PAGE_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-src 'self'; "
    "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def view_policy(csp: Mapping[str, Any]) -> str:
    """The screen's Content-Security-Policy: the resource's declared origins as the MCP Apps spec maps them, with
    'unsafe-inline' for script and style because the view is one file, no connections, and the sandbox, so the
    screen gets an opaque origin, no storage, and no way into the host page."""
    res = " ".join(csp.get("resourceDomains") or [])

    def plus(first: str) -> str:
        return f"{first} {res}".strip()

    return "; ".join([
        "default-src 'none'",
        "script-src " + plus("'unsafe-inline'"),
        "style-src " + plus("'unsafe-inline'"),
        "img-src " + plus("data:"),
        "font-src " + (res or "'none'"),
        "media-src " + plus("data:"),
        "connect-src 'none'",
        "frame-src 'none'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'none'",
        f"frame-ancestors {ORIGIN}",
        "sandbox allow-scripts",
    ])


# The MCP session.


def dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


@asynccontextmanager
async def watched(transport: Any, on_closed: Callable[[], None]) -> AsyncIterator[tuple[Any, Any]]:
    """The server's transport, with a call to on_closed when its read side ends while the page is still running,
    which is how the page learns the server exited even when no call is in flight."""
    async with transport as (read, write):
        send, receive = anyio.create_memory_object_stream[Any](max_buffer_size=math.inf)
        state = {"closing": False}

        async def forward() -> None:
            try:
                async with send:
                    async for item in read:
                        await send.send(item)
            except (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream):
                pass
            finally:
                if not state["closing"]:
                    on_closed()

        async with anyio.create_task_group() as tg:
            tg.start_soon(forward)
            try:
                yield receive, write
            finally:
                state["closing"] = True
                tg.cancel_scope.cancel()


class Relay:
    """The one MCP session, called from the HTTP threads through the portal's event loop."""

    def __init__(self, portal: BlockingPortal, client: Any) -> None:
        self.portal = portal
        self.client = client
        self.closed = False

    def _run(self, fn: Callable[[], Any]) -> Any:
        if self.closed:
            raise RelayClosed("the server's session is closed")

        async def go() -> Any:
            with anyio.fail_after(CALL_TIMEOUT_S):
                return await fn()

        try:
            return self.portal.call(go)
        except MCPError as e:
            if self.closed or e.code == CONNECTION_CLOSED:
                self.closed = True
                raise RelayClosed(e.message) from e
            raise
        except (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream) as e:
            self.closed = True
            raise RelayClosed(type(e).__name__) from e

    def tools(self) -> list[dict[str, Any]]:
        return [dump(t) for t in self._run(lambda: self.client.list_tools()).tools]

    def resource(self, uri: str) -> dict[str, Any]:
        return dump(self._run(lambda: self.client.read_resource(uri)))

    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return dump(self._run(lambda: self.client.call_tool(name, dict(arguments))))


def ui_meta(item: Mapping[str, Any]) -> dict[str, Any]:
    meta = item.get("_meta")
    ui = meta.get("ui") if isinstance(meta, dict) else None
    return ui if isinstance(ui, dict) else {}


def app_tools(tools: Sequence[Mapping[str, Any]], view_uri: str) -> list[str]:
    """The tools the screen may call: bound to the view, with app in their visibility. An absent visibility is the
    MCP Apps spec's default, model and app."""
    out = []
    for tool in tools:
        ui = ui_meta(tool)
        visibility = ui.get("visibility", ["model", "app"])
        if ui.get("resourceUri") == view_uri and isinstance(visibility, list) and "app" in visibility:
            out.append(str(tool["name"]))
    return out


@dataclass(frozen=True)
class View:
    uri: str
    html: str
    csp: dict[str, list[str]]
    permissions: dict[str, dict[str, Any]]
    tools: tuple[str, ...]

    @property
    def policy(self) -> str:
        return view_policy(self.csp)


def checked_view(tools: Sequence[Mapping[str, Any]], read: Callable[[str], Mapping[str, Any]]) -> View:
    """The view open_review renders, refused unless it is a ui:// resource of type text/html;profile=mcp-app that
    declares only resource origins, each a plain https:// origin. Of the permissions it declares, only clipboard
    writes are granted."""
    opener = next((t for t in tools if t.get("name") == OPEN_TOOL), None)
    if opener is None:
        raise PageError(f"the server has no {OPEN_TOOL} tool")
    uri = ui_meta(opener).get("resourceUri")
    if not isinstance(uri, str) or not uri.startswith("ui://"):
        raise PageError(f"{OPEN_TOOL} renders no ui:// resource")
    contents = read(uri).get("contents") or []
    if len(contents) != 1:
        raise PageError("the view resource must hold exactly one document")
    doc = contents[0]
    if doc.get("mimeType") != VIEW_MIME or not isinstance(doc.get("text"), str) or not doc["text"]:
        raise PageError(f"the view resource is not text of type {VIEW_MIME}")
    ui = ui_meta(doc)
    csp = ui.get("csp") or {}
    if not isinstance(csp, dict):
        raise PageError("the view's declared policy is not an object")
    for key, origins in csp.items():
        if key != "resourceDomains" and origins:
            raise PageError(f"the view declares {key}, which this page does not grant")
    origins = csp.get("resourceDomains") or []
    if not isinstance(origins, list) or not all(isinstance(o, str) and PLAIN_ORIGIN.fullmatch(o) for o in origins):
        raise PageError("the view declares a resource origin that is not a plain https:// origin")
    declared = ui.get("permissions") or {}
    permissions = {"clipboardWrite": {}} if isinstance(declared, dict) and "clipboardWrite" in declared else {}
    names = app_tools(tools, uri)
    if OPEN_TOOL not in names:
        raise PageError(f"{OPEN_TOOL} is not bound to its own view with app visibility")
    return View(uri, doc["text"], {"resourceDomains": list(origins)} if origins else {}, permissions, tuple(names))


# The page.


def drafts_from(text: str) -> list[tuple[str, str]]:
    """Each draft's ID and status from list_drafts' text, in the server's order (sorted by ID). A draft the server
    can't read gets the status "unreadable" and still blocks the drafts after it."""
    out = []
    for line in text.splitlines():
        m = DRAFT_LINE.fullmatch(line.strip())
        if m:
            out.append((m["id"], m["status"] or "unreadable"))
    return out


def digest(code: str) -> str:
    return hashlib.sha256(code.encode("ascii")).hexdigest()


class ReviewPage:
    """Everything the page's routes do, as plain request in and response out, so tests need no socket."""

    def __init__(
        self,
        relay: Relay,
        *,
        practice: bool,
        log: Callable[[str], None],
        on_stop: Callable[[str], None] = lambda reason: None,
    ) -> None:
        self.relay = relay
        self.practice = practice
        self.log = log
        self.on_stop = on_stop
        self.view = checked_view(relay.tools(), relay.resource)
        self.lock = threading.RLock()
        self.code: str | None = None
        self.issued: dict[str, str] = {}  # each launch code's SHA-256: "open", "replaced" or "used"
        self.token: str | None = None
        self.open: str | None = None
        self.stopped: str | None = None

    # Codes, tokens and stopping.

    def new_code(self) -> str:
        """A fresh single-use launch code, replacing any unclaimed one; returns the link that carries it."""
        with self.lock:
            if self.code is not None:
                self.issued[digest(self.code)] = "replaced"
            self.code = secrets.token_urlsafe(24)
            self.issued[digest(self.code)] = "open"
            return f"{ORIGIN}/#c={self.code}"

    def stop(self, reason: str) -> None:
        with self.lock:
            if self.stopped is not None:
                return
            self.stopped = reason
            self.token = None
            self.code = None
            self.open = None
        self.log(f"stopped: {reason}")
        self.on_stop(reason)

    # Requests.

    def handle(self, method: str, path: str, headers: Mapping[str, str], body: bytes = b"") -> Response:
        h = {k.lower(): v for k, v in headers.items()}
        route = path.split("?", 1)[0].split("#", 1)[0]
        if h.get("host") != HOST_HEADER:
            self.refuse(403, route, None, "a foreign Host", "")
            return Response(403, b"This page answers only at http://127.0.0.1:8766/.\n", "text/plain; charset=utf-8")
        if self.stopped is not None:
            return error(503, STOPPED, stopped=True)
        if method == "GET":
            if "origin" in h and h["origin"] != ORIGIN:
                return self.refuse(403, route, None, "a foreign Origin", "cross-origin request refused")
            if route not in GET_ROUTES:
                if route in POST_ROUTES:
                    return self.refuse(405, route, None, f"method {method}", "method not allowed")
                return error(404, "not found")
            return self.view_page() if route == "/view" else self.host_file(route)
        if route not in POST_ROUTES:
            if route in GET_ROUTES:
                return self.refuse(405, route, None, f"method {method[:10]}", "method not allowed")
            return error(404, "not found")
        if method != "POST":
            return self.refuse(405, route, None, f"method {method[:10]}", "method not allowed")
        if h.get("origin") != ORIGIN:
            return self.refuse(403, route, None, "missing or foreign Origin", "cross-origin request refused")
        if h.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return self.refuse(415, route, None, "not JSON", "send JSON")
        if len(body) > MAX_BODY_BYTES:
            return self.refuse(413, route, None, "a body over the limit", "request too large")
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (ValueError, RecursionError):
            return self.refuse(400, route, None, "a body that is not JSON", "the body is not JSON")
        if not isinstance(payload, dict):
            return self.refuse(400, route, None, "a body that is not an object", "the body must be a JSON object")
        if route == "/api/claim":
            return self.claim(payload)
        with self.lock:
            token = self.token
        if token is None or not secrets.compare_digest(h.get(TOKEN_HEADER, "").encode(), token.encode()):
            return self.refuse(401, route, None, "missing or retired page token", STALE_TAB)
        try:
            if route == "/api/state":
                return json_response(200, self.state())
            if route == "/api/open":
                return self.open_draft(payload)
            if route == "/api/call":
                return self.call(payload)
            with self.lock:  # /api/close
                self.open = None
            return json_response(200, {"closed": True})
        except RelayClosed:
            self.stop(SERVER_EXITED)
            return error(503, STOPPED, stopped=True)
        except TimeoutError:
            self.log(f"{route}: the server did not answer within {CALL_TIMEOUT_S} seconds")
            return error(504, "The preparer server did not answer in time. Try again, or restart the page.")
        except MCPError as e:
            self.log(f"{route}: the server answered with protocol error {e.code}")
            return error(502, f"The preparer server could not run that call: {e.message[:300]}")
        except PageError as e:
            self.log(f"{route}: {e}")
            return error(502, f"The page could not use the server's answer: {e}")

    def refuse(self, status: int, route: str, tool: Any, reason: str, message: str) -> Response:
        where = route if route in GET_ROUTES or route in POST_ROUTES else "(other route)"
        name = tool if isinstance(tool, str) and TOOL_NAME.fullmatch(tool) else ("(none)" if tool is None else "(invalid)")
        self.log(f"refused {where} tool {name}: {reason}")
        return error(status, message)

    def host_file(self, route: str) -> Response:
        path, content_type = HOST_FILES[route]
        return Response(200, path.read_bytes(), content_type, {
            "Content-Security-Policy": HOST_PAGE_POLICY,
            "Cross-Origin-Opener-Policy": "same-origin",
            "X-Frame-Options": "DENY",
        })

    def view_page(self) -> Response:
        return Response(200, self.view.html.encode("utf-8"), "text/html; charset=utf-8", {
            "Content-Security-Policy": self.view.policy,
        })

    def claim(self, payload: Mapping[str, Any]) -> Response:
        code = payload.get("code")
        known = isinstance(code, str) and code.isascii() and 0 < len(code) <= 100
        with self.lock:
            state = self.issued.get(digest(code)) if known else None
            if state == "open" and self.code is not None and secrets.compare_digest(code.encode(), self.code.encode()):
                self.issued[digest(code)] = "used"
                self.code = None
                self.token = secrets.token_urlsafe(32)
                self.open = None
                return json_response(200, {"token": self.token})
        if state == "replaced":
            self.refuse(409, "/api/claim", None, "a link that a newer one replaced", "")
            return error(409, "A newer link replaced this one. Use the newest window, or press Enter in the "
                              "Terminal window that runs the page for a fresh link.")
        if state is None:
            self.refuse(403, "/api/claim", None, "a code this page never issued", "")
            return error(403, "This link did not come from the review page. Press Enter in its Terminal window for one.")
        self.refuse(403, "/api/claim", None, "a code already used", "")
        self.stop(CODE_REUSED)
        return error(403, "This link was already used. " + STOPPED, stopped=True)

    def drafts(self) -> list[tuple[str, str]]:
        result = self.relay.call(LIST_TOOL, {})
        if result.get("isError"):
            raise PageError("the server could not list its drafts")
        text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        return drafts_from(text)

    def state(self) -> dict[str, Any]:
        drafts = self.drafts()
        pending = [d for d, status in drafts if status != APPROVED]
        with self.lock:
            open_draft = self.open
        return {
            "next": pending[0] if pending else None,
            "approved": len(drafts) - len(pending),
            "total": len(drafts),
            "open": open_draft,
            "practice": self.practice,
            "host": {"name": NAME, "version": VERSION},
            "sandbox": {"csp": self.view.csp, "permissions": self.view.permissions},
        }

    def open_draft(self, payload: Mapping[str, Any]) -> Response:
        draft_id = payload.get("draft_id")
        drafts = self.drafts()
        pending = [d for d, status in drafts if status != APPROVED]
        if not pending or draft_id != pending[0]:
            return self.refuse(409, "/api/open", OPEN_TOOL, "not the next draft", "Only the next draft in order can be opened.")
        with self.lock:
            if self.open is not None and self.open != draft_id:
                return self.refuse(409, "/api/open", OPEN_TOOL, "another draft is open", "Close the open draft first.")
            self.open = draft_id
        result = self.relay.call(OPEN_TOOL, {"draft_id": draft_id})
        return json_response(200, {"draft_id": draft_id, "result": result})

    def call(self, payload: Mapping[str, Any]) -> Response:
        name, arguments = payload.get("name"), payload.get("arguments")
        with self.lock:
            open_draft = self.open
        if open_draft is None:
            return self.refuse(409, "/api/call", name, "no draft is open", "No draft is open on this page.")
        if not isinstance(name, str) or name not in self.view.tools:
            return self.refuse(409, "/api/call", name, "not a tool the screen may call", "The screen may not call that tool here.")
        if not isinstance(arguments, dict) or arguments.get("draft_id") != open_draft:
            return self.refuse(409, "/api/call", name, "not the open draft", "The screen asked about a draft that is not open.")
        return json_response(200, {"result": self.relay.call(name, arguments)})


# HTTP.


SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def handler_for(page_of: Callable[[], ReviewPage | None]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "preparer-local-review"
        sys_version = ""
        timeout = REQUEST_TIMEOUT_S

        def _answer(self, method: str) -> None:
            page = page_of()
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if page is None:
                response = error(503, "The review page is starting. Reload in a moment.")
            elif length < 0 or length > MAX_BODY_BYTES:
                route = self.path.split("?", 1)[0]
                response = page.refuse(413, route, None, "a body over the limit", "request too large")
            else:
                body = self.rfile.read(length) if length else b""
                try:
                    response = page.handle(method, self.path, dict(self.headers.items()), body)
                except Exception as e:  # noqa: BLE001
                    page.log(f"{method[:10]} request: unexpected {type(e).__name__}")
                    traceback.print_exc()
                    response = error(500, f"The review page hit an unexpected error ({type(e).__name__}); "
                                          "see its Terminal window.")
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            for key, value in {**SECURITY_HEADERS, **response.headers}.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response.body)

        def do_GET(self) -> None:  # noqa: N802
            self._answer("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._answer("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._answer("PUT")

        def do_DELETE(self) -> None:  # noqa: N802
            self._answer("DELETE")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._answer("OPTIONS")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    return Handler


# Logs.


def stamp(t: datetime | None = None) -> str:
    return (t or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PageLog:
    """The page's own log for one run: one line per refusal or stop, stamped in UTC, never a request's arguments."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._file = open(path, "a", encoding="utf-8")  # noqa: SIM115

    def __call__(self, line: str) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.write(f"{stamp()} {line}\n")
                self._file.flush()

    def close(self) -> None:
        with self._lock:
            self._file.close()


def log_paths(base: Path = LOG_DIR, now: datetime | None = None) -> tuple[Path, Path]:
    """This run's two logs, named for the local start time and never shared with another run."""
    base.mkdir(parents=True, exist_ok=True)
    name = (now or datetime.now()).strftime("review-%Y%m%d-%H%M%S")
    for i in range(100):
        stem = name if i == 0 else f"{name}-{i}"
        server, page = base / f"{stem}-server.log", base / f"{stem}-page.log"
        if not server.exists() and not page.exists():
            return server, page
    raise PageError(f"could not name new logs under {base}")


# Starting up.


def lock_date(text: str) -> bool:
    """A real calendar date written YYYY-MM-DD."""
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return len(text) == 10


def lock_refusals(
    repo: Path = REPO,
    env: Mapping[str, str] = os.environ,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    python: str = sys.executable,
) -> list[str]:
    """Why the page must not start on data/drafts yet, from child processes only: an empty list means it may."""
    if env.get("CLAUDECODE"):
        return ["This shell belongs to a Claude Code session (CLAUDECODE is set). Start the review page in Terminal.app."]

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)

    if git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        return ["This checkout is shallow, so its lock commit can't be trusted. Use a full clone."]
    found = git("log", "-1", "--format=%H", "--", PREREG)
    lock = found.stdout.strip()
    if found.returncode != 0 or not lock:
        return [f"{PREREG} has never been committed, so there is no lock commit."]
    shown = git("show", f"{lock}:{PREREG}")
    read = run([python, "-c", READ_LOCK], cwd=repo, input=shown.stdout, capture_output=True, text=True, check=False)
    out = []
    if shown.returncode != 0 or read.returncode != 0:
        out.append(f"{PREREG} as committed does not parse as YAML.")
    elif not lock_date(read.stdout.strip()):
        out.append(f"{PREREG} is not locked: set locked_on to the lock date (YYYY-MM-DD) and commit it before the first review.")
    dirty = git("status", "--porcelain", "--untracked-files=all", "--", *LOCKED_PATHS)
    if dirty.returncode != 0 or dirty.stdout.strip():
        out.append(f"{', '.join(LOCKED_PATHS)} have uncommitted changes; commit or undo them first.")
    ignored = git("ls-files", "--others", "--ignored", "--exclude-standard", "--", DRAFTS)
    hidden = [line for line in ignored.stdout.splitlines() if line.endswith(".json")]  # what the server would read
    if ignored.returncode != 0 or hidden:
        out.append(f"{DRAFTS} holds .json files git ignores, so the lock can't vouch for them: {', '.join(hidden[:5])}.")
    changed = git("diff", "--quiet", lock, "HEAD", "--", *LOCKED_PATHS)
    if changed.returncode != 0:
        out.append(f"{', '.join(LOCKED_PATHS)} changed after the lock commit {lock[:12]}.")
    built = run([python, "-m", "preparer.build", "--check"], cwd=repo, capture_output=True, text=True, check=False)
    if built.returncode != 0:
        out.append("The built view is stale: python -m preparer.build --check failed.")
    return out


def practice_folder(base: Path = DEMO_REVIEWS, now: datetime | None = None) -> Path:
    """A new folder for one practice run's reviews."""
    name = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    for i in range(100):
        path = base / (name if i == 0 else f"{name}-{i}")
        try:
            path.mkdir(parents=True, exist_ok=False)
            return path
        except FileExistsError:
            continue
    raise PageError(f"could not make a new practice folder under {base}")


def chrome_command(url: str, profile: Path = CHROME_PROFILE) -> list[str]:
    """Google Chrome on the page's own profile, with no extensions and no sync."""
    return [
        "open", "-na", "Google Chrome", "--args",
        f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync", "--new-window",
        url,
    ]


def open_chrome(url: str, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
                profile: Path = CHROME_PROFILE, out: TextIO = sys.stdout) -> bool:
    """Opens the link in Chrome, never in the default browser; if Chrome won't start, prints the command to run."""
    command = chrome_command(url, profile)
    try:
        ok = run(command, capture_output=True, text=True, check=False).returncode == 0
    except OSError:
        ok = False
    if not ok:
        print("Google Chrome did not start. Run this in Terminal.app to open the page:", file=out)
        print("  " + shlex.join(command), file=out, flush=True)
    return ok


def sole(group: BaseExceptionGroup) -> BaseException | None:
    """The one exception inside a group of nested groups, or None when there are several."""
    leaves: list[BaseException] = []

    def walk(g: BaseExceptionGroup) -> None:
        for e in g.exceptions:
            walk(e) if isinstance(e, BaseExceptionGroup) else leaves.append(e)

    walk(group)
    return leaves[0] if len(leaves) == 1 else None


@contextmanager
def running(
    server: Any,
    *,
    practice: bool,
    log: Callable[[str], None],
    on_stop: Callable[[str], None] = lambda reason: None,
) -> Iterator[ReviewPage]:
    """One MCP session with the server, as client preparer-local-host advertising MCP Apps, and the page on it.
    `server` is the stdio transport in a real run, or an in-process server in tests. A single error from inside
    the session's task groups comes out as itself, not wrapped in a group, so the caller can tell a refused
    view or a Ctrl+C from anything else."""
    holder: dict[str, ReviewPage | None] = {"page": None}

    def closed() -> None:
        page = holder["page"]
        if page is not None:
            page.relay.closed = True
            page.stop(SERVER_EXITED)

    if not isinstance(server, (str, StdioServerParameters)) and hasattr(server, "__aenter__"):
        server = watched(server, closed)
    client = Client(server, extensions=[APPS], client_info=Implementation(name=NAME, version=VERSION), cache=None)
    try:
        with start_blocking_portal() as portal:
            with portal.wrap_async_context_manager(client) as connected:
                page = ReviewPage(Relay(portal, connected), practice=practice, log=log, on_stop=on_stop)
                holder["page"] = page
                try:
                    yield page
                finally:
                    holder["page"] = None
    except BaseExceptionGroup as group:
        leaf = sole(group)
        if leaf is None:
            raise
        raise leaf from leaf.__cause__


def server_params(drafts: str | Path, reviews: str | Path, python: str = sys.executable) -> StdioServerParameters:
    return StdioServerParameters(
        command=python, args=["-m", "preparer.server", "--drafts", str(drafts), "--reviews", str(reviews)], cwd=str(REPO),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="The local review page: the preparer's review screen on 127.0.0.1.")
    parser.add_argument("--practice", action="store_true",
                        help="use the three demo drafts and a new folder under preparer/app/demo_reviews/")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.practice:
        try:
            refusals = lock_refusals()
        except KeyboardInterrupt:
            print("Stopped: stopped with Ctrl+C.", flush=True)
            return 0
        if refusals:
            print("The review page won't start on data/drafts:", file=sys.stderr)
            for line in refusals:
                print(f"  {line}", file=sys.stderr)
            return 2
    holder: dict[str, ReviewPage | None] = {"page": None}
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), handler_for(lambda: holder["page"]))
    except OSError as e:
        print(f"error: can't listen on {HOST_HEADER} ({e.strerror}). Is the page already running?", file=sys.stderr)
        return 2
    httpd.daemon_threads = True
    stopped = threading.Event()
    reason: dict[str, str] = {}

    def on_stop(why: str) -> None:
        reason.setdefault("why", why)
        stopped.set()

    serving: threading.Thread | None = None
    page_log: PageLog | None = None
    reviews: Path | None = None
    started = False
    try:
        drafts, reviews = (DEMO_DRAFTS, practice_folder(DEMO_REVIEWS)) if args.practice else (Path(DRAFTS), Path(REVIEWS))
        server_log_path, page_log_path = log_paths(LOG_DIR)
        page_log = PageLog(page_log_path)
        page_log(f"start: {'practice on the demo drafts' if args.practice else 'the drafts in data/drafts'}, reviews in {reviews}")
        with open(server_log_path, "a", encoding="utf-8") as server_log, running(
            stdio_client(server_params(drafts, reviews), errlog=server_log),
            practice=args.practice, log=page_log, on_stop=on_stop,
        ) as page:
            started = True
            holder["page"] = page
            serving = threading.Thread(target=httpd.serve_forever, name="local-review-http", daemon=True)
            serving.start()

            def launch() -> None:
                if open_chrome(page.new_code()):
                    print("Opened the page in Chrome, on the page's own profile.", flush=True)

            def keys() -> None:
                for _ in sys.stdin:
                    if stopped.is_set():
                        return
                    launch()
                if not stopped.is_set():
                    print("This window's input closed, so Enter no longer opens a fresh link. Ctrl+C still stops the page.",
                          flush=True)

            # However this ends, the page stops and stops answering before the session with the server closes.
            try:
                print(f"The local review page is running at {ORIGIN}/ ({'practice, 3 demo drafts' if args.practice else 'the 24 drafts'}).")
                print(f"Reviews go to {reviews}. This run's logs are {server_log_path.name} and {page_log_path.name} in {LOG_DIR}.")
                launch()
                print("Press Enter here to open a fresh link (a reload needs one). Ctrl+C stops the page.", flush=True)
                threading.Thread(target=keys, name="local-review-keys", daemon=True).start()
                while not stopped.wait(0.5):
                    pass
            except KeyboardInterrupt:
                page.stop("stopped with Ctrl+C")
            finally:
                httpd.shutdown()
                serving = None
    except KeyboardInterrupt:
        reason.setdefault("why", "stopped with Ctrl+C")
    except PageError as e:
        print(f"error: the page can't start: {e}", file=sys.stderr)
        if page_log is not None:
            page_log(f"refused to start: {e}")
        return 2
    except Exception as e:  # noqa: BLE001 - the server failed to start or the session broke; the server log says more
        print(f"error: the preparer server could not start or stopped ({type(e).__name__}: {str(e)[:200]}). "
              f"See the server log in {LOG_DIR}.", file=sys.stderr)
        if page_log is not None:
            page_log(f"stopped: {type(e).__name__} from the server session")
        return 1
    finally:
        if serving is not None:
            httpd.shutdown()
        httpd.server_close()
        if page_log is not None:
            page_log.close()
        if args.practice and reviews is not None and not started:
            try:
                reviews.rmdir()  # a run that never started leaves no empty practice folder
            except OSError:
                pass
    print(f"Stopped: {reason.get('why', 'done').rstrip('.')}.", flush=True)
    return 0 if reason.get("why") == "stopped with Ctrl+C" else 1


if __name__ == "__main__":
    sys.exit(main())
