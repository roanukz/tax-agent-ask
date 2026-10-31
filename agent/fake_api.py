"""A fake Messages API on 127.0.0.1, for agent.wiring_check to drive the real bundled CLI offline.

    with FakeMessagesAPI(script, output_tokens) as api:
        options.env["ANTHROPIC_BASE_URL"] = api.base_url

It listens on the loopback address only, needs no key (the check passes a
placeholder), and has no model: script(step, body) decides the content
blocks of each reply, where step counts the assistant messages already in
the request. Streamed replies follow the API's event format, as the CLI
reads it: message_start carries the prompt's usage and output_tokens 1,
then the content blocks, then message_delta with the real output count.
The prompt counts one input token per byte of the request's system,
messages, and tools, with no cache, which is more than a real tokenizer
would count, so a run's calls grow the way its tool results do.

faults(n, body), when given, can make the nth agent request fail the way
a real one can: REFUSED answers 429 with a rate_limit_error (billed
nothing), CUT streams the first content block and then an error event, with
no message_delta (as an overloaded stream can end), and DROPPED closes the
connection with no answer at all.

Every request body is kept in .requests, with the usage the fake reported
for it, so a check can see what the CLI sent and what the API would have
billed; a cut or dropped request is kept at its prompt and full output,
the most it could have been billed. Request headers, which hold the key,
are never kept. pytest blocks TCP sockets, so no test starts it.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Sequence

from ledger.spend import cost_usd

HOST = "127.0.0.1"
TEXT = "text"
TOOL_USE = "tool_use"
REFUSED = "refused"
CUT = "cut"
DROPPED = "dropped"

Block = tuple[str, str, Any]  # (TEXT, text, None) or (TOOL_USE, tool name, input)
Script = Callable[[int, Mapping[str, Any]], Sequence[Block]]
Faults = Callable[[int, Mapping[str, Any]], str | None]


def prompt_tokens(body: Mapping[str, Any]) -> int:
    """One token per byte of the request's system, messages, and tools."""
    return sum(len(json.dumps(body.get(name) or [], ensure_ascii=False).encode()) for name in ("system", "messages", "tools"))


def first_user_text(body: Mapping[str, Any]) -> str:
    messages = body.get("messages") or []
    content = messages[0].get("content") if messages else ""
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content or [] if isinstance(b, Mapping))


def tool_result_texts(body: Mapping[str, Any]) -> list[str]:
    texts = []
    for message in body.get("messages") or []:
        for block in message.get("content") if isinstance(message.get("content"), list) else []:
            if isinstance(block, Mapping) and block.get("type") == "tool_result":
                content = block.get("content")
                texts.append(content if isinstance(content, str)
                             else "".join(c.get("text", "") for c in content or [] if isinstance(c, Mapping)))
    return texts


def intake_id_in(body: Mapping[str, Any]) -> str:
    """The intake ID start_intake returned earlier in the conversation, for scripts that need it."""
    for text in tool_result_texts(body):
        try:
            value, _ = json.JSONDecoder().raw_decode(text.strip())
        except ValueError:
            continue
        if isinstance(value, Mapping) and value.get("intake_id"):
            return str(value["intake_id"])
    return "in_missing"


def sse_events(blocks: Sequence[Block], model: str, message_id: str, prompt: int, output: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [{"type": "message_start", "message": {
        "id": message_id, "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": prompt, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                  "output_tokens": 1}}}]
    stop = "end_turn"
    for i, (kind, name, tool_input) in enumerate(blocks):
        if kind == TEXT:
            events.append({"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
            events.append({"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": name}})
        else:
            stop = "tool_use"
            events.append({"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": f"toolu_{message_id}_{i}", "name": name, "input": {}}})
            events.append({"type": "content_block_delta", "index": i, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(tool_input)}})
        events.append({"type": "content_block_stop", "index": i})
    events.append({"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {
        "input_tokens": prompt, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": output}})
    events.append({"type": "message_stop"})
    return events


class FakeMessagesAPI:
    """The fake server, started and stopped by the with block."""

    def __init__(self, script: Script, output_tokens: Callable[[Mapping[str, Any]], int], *,
                 faults: Faults | None = None) -> None:
        self.script = script
        self.output_tokens = output_tokens
        self.faults = faults
        self.agent_requests = 0
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None, "start the fake API first"
        return f"http://{HOST}:{self._server.server_address[1]}"

    def __enter__(self) -> FakeMessagesAPI:
        api = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                self.reply(404, b"", "text/plain")

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {}
                answer = api.answer(self.path, body)
                if answer is None:  # DROPPED: no answer at all
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                self.reply(*answer)

            def reply(self, status: int, out: bytes, kind: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self._server = ThreadingHTTPServer((HOST, 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def answer(self, path: str, body: Mapping[str, Any]) -> tuple[int, bytes, str] | None:
        """(status, body, content type) for one request, or None for a request that gets no answer."""
        if "count_tokens" in path:
            return 200, json.dumps({"input_tokens": prompt_tokens(body)}).encode(), "application/json"
        if not path.startswith("/v1/messages"):
            return 404, b"", "text/plain"
        main = bool(body.get("tools"))
        step = sum(1 for m in body.get("messages") or [] if m.get("role") == "assistant")
        blocks = list(self.script(step, body)) if main else [(TEXT, "ok", None)]
        prompt = prompt_tokens(body)
        output = min(int(self.output_tokens(body)) if main else 1, int(body.get("max_tokens") or 1))
        model = str(body.get("model") or "")
        with self._lock:
            if main:
                self.agent_requests += 1
            fault = self.faults(self.agent_requests, body) if main and self.faults else None
            message_id = f"msg_fake_{len(self.requests) + 1:03d}"
            billed = {"input_tokens": 0, "output_tokens": 0} if fault == REFUSED else {
                "input_tokens": prompt, "output_tokens": output}
            self.requests.append({"path": path, "main": main, "step": step, "model": model, "body": dict(body),
                                  "usage": billed, "fault": fault})
        if fault == REFUSED:
            error = {"type": "error", "error": {"type": "rate_limit_error", "message": "fake rate limit"}}
            return 429, json.dumps(error).encode(), "application/json"
        if fault == DROPPED:
            return None
        if body.get("stream"):
            events = sse_events(blocks, model, message_id, prompt, output)
            if fault == CUT:
                events = events[:4] + [{"type": "error", "error": {"type": "overloaded_error", "message": "fake"}}]
            out = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
            return 200, out, "text/event-stream"
        content = [{"type": "text", "text": b[1]} if b[0] == TEXT else {
            "type": "tool_use", "id": f"toolu_{message_id}_{i}", "name": b[1], "input": b[2]} for i, b in enumerate(blocks)]
        return 200, json.dumps({
            "id": message_id, "type": "message", "role": "assistant", "model": model, "content": content,
            "stop_reason": "tool_use" if any(b[0] == TOOL_USE for b in blocks) else "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": prompt, "output_tokens": output}}).encode(), "application/json"

    def billed_usd(self, prices: Mapping[str, Any], *, where: Callable[[Mapping[str, Any]], bool] = lambda r: True) -> float:
        """What the API would have billed at list price for the requests where says, every reply in full."""
        return round(sum(cost_usd(r["model"], r["usage"], dict(prices), prompt_tokens=r["usage"]["input_tokens"])
                         for r in self.requests if where(r)), 6)
