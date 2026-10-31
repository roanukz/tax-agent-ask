"""A local page for entering the blind labels, one household at a time.

    uv run python -m generator.label_ui              opens http://127.0.0.1:8765/ in the browser
    uv run python -m generator.label_ui --no-browser --port 0

The page shows the households on the blind label sheet, in the sheet's order
and words, and saves data/labels/blind_labels.csv as you go, in the format
generator.labels compare reads: one row per household, items separated by
semicolons, and " x2" after an item when there are two. Each item has a name
box and a count box. The server reads each saved item the way compare reads
it (generator.labels read_count), moves a count written in the name ("2 W-2s",
"W-2 (x2)") into the count box, and refuses an item compare would read
differently from what the page shows. Words that read like a count but stay
in the name ("both", "2nd") get a warning, as the sheet asks for x2 instead.

Blind by design. While labeling, the server sends only the sheet's
descriptions and the labels already in the CSV, and item suggestions come
from items typed for other households, never from the rules. The rules'
answers appear only once every household on the sheet has a label and
Roanuk asks to see them. At that moment generator.labels reveal copies the
CSV, byte for byte and once, to blind_labels_before_compare.csv beside it, so
the blind labels stay on record whatever is edited while settling
disagreements; the compare command uses the same gate. From then on each
household shows what compare found for it, with the cited source of each
rule.

What compare reads. A count of 1 is written only for a name with a plural
word ("W-2s x1"), since compare reads a plural with no count as unclear. A
save keeps the file's cell as written when compare reads that cell exactly
as the page's items, so editing only the notes never changes what compare
reads; a cell the page can't show faithfully (two counts in one item, a
count with no name, a count of 0) is refused on save rather than rewritten.
Long dashes become hyphens, as the repo's files allow no em or en dashes.

Safety. The server listens on 127.0.0.1 only and answers only requests whose
Host header names that address, so a web page can't reach it through DNS
rebinding. Every API call needs the token the server put in the page, and
every save or compare a JSON body, so another site open in the same browser
can neither read the labels nor send a save (a cross-site request with a
custom header needs a CORS preflight, which this server never grants). Only
"/" and three API routes exist; nothing serves files from disk. Each
connection gets its own thread and a 10-second timeout, so an idle socket
can't stall saves. Each save replaces the CSV atomically and carries the row
as the page last saw it: if the file changed since (by hand or from another
tab), the save is refused and the page offers the file's version. Rows the
sheet doesn't list, and a second row for a listed household, are written
back unchanged; a row with more cells than the header stops all saves
rather than lose the extra text.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import traceback
import webbrowser
from collections import Counter
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from generator import labels
from generator.files import DATA_DIR, LABELS, REPO, load_truths
from rules.engine import load_facts, load_rules

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PAGE = Path(__file__).with_name("label_ui.html")
TOKEN_PLACEHOLDER = "__LABEL_UI_TOKEN__"
TOKEN_HEADER = "x-label-token"
DEFAULT_LABELER = "roanuk"
MAX_BODY_BYTES = 64 * 1024
MAX_ITEMS = 60
MAX_NAME = 200
MAX_LABELER = 60
MAX_NOTES = 2000
MAX_COUNT = 99
REQUEST_TIMEOUT_S = 10
LONG_DASHES = str.maketrans({chr(0x2013): "-", chr(0x2014): "-"})
EMPTY_ROW = {"documents": "", "labeled_by": "", "notes": ""}


class BadRequest(ValueError):
    """A request the page would never send: wrong types, sizes, or fields."""


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"


def json_response(status: int, obj: Any) -> Response:
    return Response(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def error(status: int, message: str, **extra: Any) -> Response:
    return json_response(status, {"error": message, **extra})


def display_path(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO))
    except ValueError:
        return str(path)


# Items.


def format_item(name: str, count: int) -> str:
    """An item as the file holds it. A name with a plural word keeps "x1", so compare reads the count box's 1."""
    if count == 1 and not any(labels.is_plural(w) for w in labels.words(name)):
        return name
    return f"{name} x{count}"


def format_cell(items: list[tuple[str, int]]) -> str:
    return "; ".join(format_item(name, count) for name, count in items)


def split_cell(cell: str) -> list[dict[str, Any]]:
    """A documents cell as the page's rows: each item's name and count, read as compare reads them.

    A written count of 0 stays 0, and an item with a count but no name, or with a second count left in its
    name, keeps its whole text as the name, so saving it is refused rather than silently changing what
    compare reads. Text is cleaned as a save cleans it, so an untouched item compares equal.
    """
    out = []
    for raw in cell.split(";"):
        text = clean(raw)
        if text:
            name, count = labels.read_count(text)
            name = name.strip()
            if not name or labels.read_count(name)[1] is not None:
                name, count = text, None
            out.append({"name": name, "count": 1 if count is None else count})
    return out


def clean(text: str) -> str:
    """One line of text as a save stores it: long dashes as hyphens, other control characters as spaces."""
    return " ".join("".join(c if c.isprintable() else " " for c in text.translate(LONG_DASHES)).split())


def faithful(cell: str) -> bool:
    """Whether compare reads every item of the cell with the count the page shows for it.

    A plural with no written count is not: compare reads it as unclear while the page shows 1.
    """
    for raw in cell.split(";"):
        text = raw.strip()
        if text:
            name, count = labels.read_count(text)
            if count is None and any(labels.is_plural(w) for w in labels.words(name)):
                return False
    return True


def one_line(value: Any, limit: int, what: str) -> str:
    if not isinstance(value, str):
        raise BadRequest(f"{what} must be text")
    text = clean(value)
    if len(text) > limit:
        raise BadRequest(f"{what} is longer than {limit} characters")
    return text


def notes_text(value: Any) -> str:
    if not isinstance(value, str):
        raise BadRequest("notes must be text")
    text = value.translate(LONG_DASHES).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c if c.isprintable() or c == "\n" else " " for c in text).strip()
    if len(text) > MAX_NOTES:
        raise BadRequest(f"notes are longer than {MAX_NOTES} characters")
    return text


def box_count(value: Any) -> int | None:
    """The count box as a whole number, or None when it isn't one."""
    if value in (None, ""):
        return 1
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,3}", value.strip()):
        return int(value.strip())
    return None


def normalize_items(raw: Any) -> tuple[list[list[tuple[str, int]]], list[dict[str, Any]]]:
    """Each of the page's rows as the (name, count) items compare reads back exactly, and a problem list.

    The first list has one entry per request row, so the page can tell which row each item came from. A
    problem names its row (the index in the request) and is an error, which blocks the save, or a warning,
    which doesn't. A row with an empty name gives no items. A row holding semicolons gives one item per
    piece, since the file separates items with them.
    """
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise BadRequest("items must be a list")
    if len(raw) > MAX_ITEMS:
        raise BadRequest(f"more than {MAX_ITEMS} items")
    per_row: list[list[tuple[str, int]]] = []
    problems: list[dict[str, Any]] = []

    def problem(row: int, level: str, message: str, item: int = 0) -> None:
        problems.append({"row": row, "level": level, "message": message, "item": item})

    for row, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise BadRequest("each item must be an object")
        name = one_line(entry.get("name", ""), MAX_NAME, "an item name")
        count = box_count(entry.get("count", 1))
        items: list[tuple[str, int]] = []
        per_row.append(items)
        if count is None or not 1 <= count <= MAX_COUNT:
            problem(row, "error", f"The count must be a whole number from 1 to {MAX_COUNT}.")
            continue
        pieces = [p.strip() for p in name.split(";") if p.strip()]
        if len(pieces) > 1:
            if count != 1:
                problem(row, "error", "This row holds several items. Put each on its own row, or write each "
                                      "count in the item, such as x2.")
                continue
            problem(row, "warning", f"Split at the semicolons into {len(pieces)} items.")
        for piece in pieces:
            item_name, written = labels.read_count(piece)
            item_name = item_name.strip()
            if item_name and labels.read_count(item_name)[1] is not None:
                problem(row, "error", f'"{piece}" has two counts. Keep one, in the count box.')
                continue
            if written is not None:
                if count not in (1, written):
                    problem(row, "error", f'"{piece}" has a count of {written} in its name, but the count box '
                                          f"says {count}.")
                    continue
                n = written
            else:
                n = count
            if not item_name:
                problem(row, "error", "This item has a count but no name.")
                continue
            if not 1 <= n <= MAX_COUNT:
                problem(row, "error", f"The count must be a whole number from 1 to {MAX_COUNT}.")
                continue
            cell_item = format_item(item_name, n)
            back_name, back = labels.read_count(cell_item)
            if (back_name.strip(), back or 1) != (item_name, n):
                problem(row, "error", f'compare would read "{cell_item}" as {back or 1} of "{back_name.strip()}". '
                                      "Reword the item.")
                continue
            if written is not None and len(pieces) == 1:
                problem(row, "warning", f"Moved the count {written} from the name to the count box.")
            hints = [w for w in labels.words(item_name) if labels.COUNT_WORD.fullmatch(w)]
            if hints:
                problem(row, "warning", f'"{hints[0]}" reads like a count but stays part of the name. If you mean '
                                        "more than one, put the number in the count box.", len(items))
            items.append((item_name, n))
    return per_row, problems


# The labels file.


def row_view(row: Mapping[str, str] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    documents = row.get("documents") or ""
    return {
        "documents": documents,
        "labeled_by": row.get("labeled_by") or "",
        "notes": row.get("notes") or "",
        "items": split_cell(documents),
    }


def row_triple(row: Mapping[str, Any] | None) -> tuple[str, str, str]:
    row = row or EMPTY_ROW
    return tuple(str(row.get(k) or "") for k in ("documents", "labeled_by", "notes"))  # type: ignore[return-value]


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass
class LabelRows:
    """The CSV's rows: one per sheet household, keyed by ID, and every other row in file order."""

    rows: dict[str, dict[str, str]]
    others: list[dict[str, str]]
    duplicates: list[str]


class LabelApp:
    """Everything the page asks for, as plain request in and response out, so tests need no socket."""

    def __init__(self, labels_path: Path, data_dir: Path = DATA_DIR, token: str | None = None):
        if labels.is_snapshot(labels_path):
            raise ValueError(f"{labels_path} is the record of the blind labels; open the labels file it was copied "
                             "from instead")
        self.labels_path = Path(labels_path)
        self.snapshot_path = labels.snapshot_path(self.labels_path)
        self.data_dir = Path(data_dir)
        self.token = token or secrets.token_urlsafe(32)
        self.allowed_hosts: set[str] = set()
        truths = load_truths(self.data_dir)
        specs = load_facts()
        self.households = [labels.household_description(truths[h], specs) for h in labels.select_households(truths)]
        self.order = [h["household_id"] for h in self.households]
        self.lock = threading.Lock()

    def allow_port(self, port: int) -> None:
        self.allowed_hosts = {f"{HOST}:{port}", f"localhost:{port}"}

    # Requests.

    def handle(self, method: str, path: str, headers: Mapping[str, str], body: bytes = b"") -> Response:
        h = {k.lower(): v for k, v in headers.items()}
        if h.get("host", "") not in self.allowed_hosts:
            return Response(403, b"This page answers only at its own 127.0.0.1 address.\n", "text/plain; charset=utf-8")
        origin = h.get("origin")
        if origin is not None and origin not in {f"http://{host}" for host in self.allowed_hosts}:
            return error(403, "cross-origin request refused")
        route = path.split("?", 1)[0]
        if route == "/":
            if method != "GET":
                return error(405, "method not allowed")
            return self.page()
        if not route.startswith("/api/"):
            return error(404, "not found")
        if not secrets.compare_digest(h.get(TOKEN_HEADER, "").encode(), self.token.encode()):
            return error(403, "missing or wrong page token; reload the page")
        try:
            if route == "/api/state" and method == "GET":
                return self.state()
            if route in ("/api/save", "/api/compare"):
                if method != "POST":
                    return error(405, "method not allowed")
                if h.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                    return error(415, "send JSON")
                if len(body) > MAX_BODY_BYTES:
                    return error(413, "request too large")
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (ValueError, RecursionError):
                    return error(400, "the body is not JSON")
                if not isinstance(payload, dict):
                    return error(400, "the body must be a JSON object")
                return self.save(payload) if route == "/api/save" else self.compare(payload)
            return error(404, "not found")
        except BadRequest as e:
            return error(400, str(e))
        except labels.LabelFileError as e:
            return error(409, f"{e}. Fix the file by hand, then reload.")

    def page(self) -> Response:
        html = PAGE.read_text(encoding="utf-8").replace(TOKEN_PLACEHOLDER, self.token)
        return Response(200, html.encode("utf-8"), "text/html; charset=utf-8")

    # The file.

    def read_rows(self) -> LabelRows:
        if not self.labels_path.exists():
            return LabelRows({}, [], [])
        listed = set(self.order)
        rows: dict[str, dict[str, str]] = {}
        others: list[dict[str, str]] = []
        duplicates: list[str] = []
        for raw in labels.read_labels(self.labels_path):
            row = {k: raw.get(k, "") for k in labels.CSV_HEADER}
            hid = row["household_id"].strip()
            if hid in listed and hid not in rows:
                row["household_id"] = hid
                rows[hid] = row
            else:
                if hid in listed:
                    duplicates.append(hid)
                others.append(row)
        return LabelRows(rows, others, duplicates)

    def write_rows(self, current: LabelRows) -> None:
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(labels.CSV_HEADER)
        for hid in self.order:
            if hid in current.rows:
                writer.writerow([current.rows[hid][k] for k in labels.CSV_HEADER])
        for row in current.others:
            writer.writerow([row[k] for k in labels.CSV_HEADER])
        atomic_write(self.labels_path, out.getvalue())

    def labeled(self, current: LabelRows) -> list[str]:
        return [h for h in self.order if h in current.rows and labels.has_items(current.rows[h]["documents"])]

    def labeler(self, current: LabelRows) -> str:
        names = [r["labeled_by"].strip() for r in [*current.rows.values(), *current.others] if r["labeled_by"].strip()]
        return Counter(names).most_common(1)[0][0] if names else DEFAULT_LABELER

    # Routes.

    def state(self) -> Response:
        current = self.read_rows()
        return json_response(200, {
            "households": self.households,
            "labels": {hid: row_view(current.rows[hid]) for hid in self.order if hid in current.rows},
            "other_rows": len(current.others),
            "duplicates": sorted(set(current.duplicates)),
            "labeler": self.labeler(current),
            "labeled": len(self.labeled(current)),
            "total": len(self.order),
            "labels_file": display_path(self.labels_path),
            "snapshot_file": display_path(self.snapshot_path),
            "revealed": self.snapshot_path.exists(),
        })

    def save(self, payload: Mapping[str, Any]) -> Response:
        hid = payload.get("household_id")
        if hid not in self.order:
            raise BadRequest("unknown household")
        per_row, problems = normalize_items(payload.get("items"))
        items = [item for row in per_row for item in row]
        notes = notes_text(payload.get("notes", ""))
        labeler = one_line(payload.get("labeled_by", ""), MAX_LABELER, "labeled_by")
        base = payload.get("base")
        if base is not None and not isinstance(base, dict):
            raise BadRequest("base must be an object or null")
        if any(p["level"] == "error" for p in problems):
            return error(422, "Not saved: fix the marked items.", problems=problems)
        cell = format_cell(items)
        with self.lock:
            current = self.read_rows()
            on_disk = current.rows.get(hid)
            if row_triple(on_disk) != row_triple(base):
                return error(409, "This household's row changed in the file since the page loaded it.",
                             row=row_view(on_disk))
            kept = on_disk["documents"] if on_disk else ""
            if kept and faithful(kept) and items == [(i["name"], i["count"]) for i in split_cell(kept)]:
                cell = kept
            if cell or notes:
                current.rows[hid] = {"household_id": hid, "documents": cell, "labeled_by": labeler, "notes": notes}
            else:
                current.rows.pop(hid, None)
            self.write_rows(current)
            labeled = len(self.labeled(current))
        return json_response(200, {
            "row": row_view(current.rows.get(hid)),
            "rows": [[{"name": name, "count": n} for name, n in row] for row in per_row],
            "problems": problems,
            "labeled": labeled,
        })

    def compare(self, payload: Mapping[str, Any]) -> Response:
        with self.lock:
            if not self.labels_path.exists():
                return error(409, f"{display_path(self.labels_path)} is missing. Restore it, then reload.")
            if not self.snapshot_path.exists():
                self.read_rows()
                if payload.get("reveal") is not True:
                    return error(409, "Confirm first: the comparison shows the rules' answers.")
                try:
                    labels.reveal(self.labels_path, self.order)
                except labels.RevealRefused as e:
                    return error(409, str(e))
            rows, labeled = labels.compare_file(self.labels_path, self.data_dir)
            by_id = {r.id: r for r in load_rules()}
            settlements_file = labels.settlements_path(self.labels_path)
            settled = labels.settle(rows, labels.read_settlements(settlements_file, set(by_id)))
        how = {labels.settlement_key(d): s for d, s in settled.settled}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for d in rows:
            rule = by_id.get(d.rule_id)
            s = how.get(labels.settlement_key(d))
            grouped.setdefault(d.household_id, []).append({
                "kind": d.kind,
                "disagreement": labels.is_disagreement(d),
                "rule_id": d.rule_id,
                "document": d.document,
                "rules_say": d.engine_count,
                "label_says": d.label_count,
                "item": d.item,
                "source_title": rule.source_title if rule else "",
                "source_url": rule.source_url if rule else "",
                "status": rule.status if rule else "",
                "settlement": s["settlement"] if s else "",
                "settlement_note": s["note"] if s else "",
            })
        disagreements = sum(1 for d in rows if labels.is_disagreement(d))
        return json_response(200, {
            "households": grouped,
            "disagreements": disagreements,
            "notes": len(rows) - disagreements,
            "labeled": labeled,
            "snapshot_file": display_path(self.snapshot_path),
            "settlements_file": display_path(settlements_file) if settlements_file.exists() else "",
            "settled": len(settled.settled),
            "unsettled": len(settled.unsettled),
        })


# The server.


def handler_for(app: LabelApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "label-ui"
        sys_version = ""
        # An idle or slow connection is dropped after this long instead of holding its thread.
        timeout = REQUEST_TIMEOUT_S

        def _answer(self, method: str) -> None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY_BYTES:
                response = error(413, "request too large")
            else:
                body = self.rfile.read(length) if length else b""
                try:
                    response = app.handle(method, self.path, dict(self.headers.items()), body)
                except Exception as e:  # noqa: BLE001
                    traceback.print_exc()
                    response = error(500, f"The label server hit an unexpected error ({type(e).__name__}); "
                                          "see the terminal it runs in.")
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
                             "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(response.body)

        def do_GET(self) -> None:  # noqa: N802
            self._answer("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._answer("POST")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--labels", type=Path, default=None, help="labels CSV (default data/labels/blind_labels.csv)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}; 0 picks a free one")
    parser.add_argument("--no-browser", action="store_true", help="print the address without opening a browser")
    args = parser.parse_args(argv)
    try:
        app = LabelApp(args.labels or args.data / LABELS / labels.CSV_FILE, args.data)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        server = ThreadingHTTPServer((HOST, args.port), handler_for(app))
        server.daemon_threads = True
    except OSError as e:
        print(f"error: can't listen on {HOST}:{args.port} ({e.strerror}). Try --port 0.", file=sys.stderr)
        return 2
    port = server.server_address[1]
    app.allow_port(port)
    url = f"http://{HOST}:{port}/"
    print(f"Blind labels: {url}  (saving to {display_path(app.labels_path)}; Ctrl+C to stop)", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Labels are saved as you go.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
