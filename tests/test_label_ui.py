"""generator.label_ui: the page saves what compare reads, stays blind until asked, and refuses other sites."""

import csv
import json
import re
import shutil
import socket
import threading
import time

import pytest

from generator import label_ui, labels
from generator.files import DATA_DIR, load_truths
from rules.engine import load_facts, load_ignore_aliases, load_rules

HOST = "127.0.0.1:8765"
TOKEN = "test-token-not-a-secret"
HEADER = ",".join(labels.CSV_HEADER) + "\n"
RULES = load_rules()
SHEET = (DATA_DIR / "labels" / "blind_label_sheet.md").read_text(encoding="utf-8")
FORM_NUMBER = re.compile(
    r"\b(?:W-?2G?|W-10|1099|1098|1095|1040|5498|5695|8283|8936|8962|4070|SSA|K-1|HUD-1|CP01A)|\bForm\b|\bSchedule\b",
    re.IGNORECASE,
)
RULE_ID = re.compile(r"\b[RT]\d{3}\b")


@pytest.fixture(scope="module")
def shared_app():
    # Loading the truths once keeps the module fast; each test gets its own labels file through rebind.
    return label_ui.LabelApp(DATA_DIR / "labels" / "unused.csv", DATA_DIR, token=TOKEN)


@pytest.fixture
def app(shared_app, tmp_path):
    shared_app.labels_path = tmp_path / "blind_labels.csv"
    shared_app.snapshot_path = labels.snapshot_path(shared_app.labels_path)
    shared_app.labels_path.write_text(HEADER, encoding="utf-8")
    shared_app.allow_port(8765)
    return shared_app


def call(app, method, path, body=None, *, token=TOKEN, host=HOST, origin=None, content_type="application/json"):
    headers = {"Host": host}
    if token is not None:
        headers["X-Label-Token"] = token
    if origin is not None:
        headers["Origin"] = origin
    raw = b""
    if body is not None:
        headers["Content-Type"] = content_type
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    r = app.handle(method, path, headers, raw)
    data = json.loads(r.body) if r.content_type.startswith("application/json") else r.body.decode()
    return r.status, data


def save(app, hid, items, notes="", labeled_by="roanuk", base="disk"):
    if base == "disk":
        _, state = call(app, "GET", "/api/state")
        row = state["labels"].get(hid)
        base = {k: row[k] for k in ("documents", "labeled_by", "notes")} if row else None
    return call(app, "POST", "/api/save", {"household_id": hid, "items": items, "notes": notes,
                                           "labeled_by": labeled_by, "base": base})


def item(name, count=1):
    return {"name": name, "count": count}


def csv_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fill_all(app, cell_items=None):
    for hid in app.order:
        status, data = save(app, hid, cell_items or [item("W-2")])
        assert status == 200, data


# Who may ask.


def test_page_is_served_with_its_token_and_no_placeholder(app):
    status, html = call(app, "GET", "/", token=None)
    assert status == 200
    assert f'content="{TOKEN}"' in html
    assert label_ui.TOKEN_PLACEHOLDER not in html


@pytest.mark.parametrize("host", ["evil.example:8765", "127.0.0.1:9999", "", "127.0.0.1"])
def test_other_hosts_are_refused_so_dns_rebinding_cant_reach_the_page(app, host):
    assert call(app, "GET", "/", host=host)[0] == 403
    assert call(app, "GET", "/api/state", host=host)[0] == 403


def test_localhost_by_name_is_allowed(app):
    assert call(app, "GET", "/api/state", host="localhost:8765")[0] == 200


def test_cross_origin_requests_are_refused(app):
    status, _ = call(app, "POST", "/api/save", {"household_id": app.order[0], "items": []},
                     origin="https://evil.example")
    assert status == 403
    assert call(app, "GET", "/api/state", origin="http://127.0.0.1:8765")[0] == 200


@pytest.mark.parametrize("token", [None, "", "wrong"])
def test_api_needs_the_page_token(app, token):
    assert call(app, "GET", "/api/state", token=token)[0] == 403
    status, _ = call(app, "POST", "/api/save", {"household_id": app.order[0], "items": [item("W-2")]}, token=token)
    assert status == 403
    assert app.labels_path.read_text() == HEADER


def test_saves_need_a_json_body(app):
    hid = app.order[0]
    body = json.dumps({"household_id": hid, "items": [item("W-2")], "base": None}).encode()
    assert call(app, "POST", "/api/save", body, content_type="text/plain")[0] == 415
    assert call(app, "POST", "/api/save", body, content_type="application/x-www-form-urlencoded")[0] == 415
    assert call(app, "POST", "/api/save", b"{not json")[0] == 400
    assert call(app, "POST", "/api/save", b"[1, 2]")[0] == 400
    assert call(app, "POST", "/api/save", b" " * (label_ui.MAX_BODY_BYTES + 1))[0] == 413
    assert app.labels_path.read_text() == HEADER


@pytest.mark.parametrize("path", ["/.env", "/../.env", "/data/truth/H002.json", "/generator/label_ui.html",
                                  "/api/../.env", "/api/truth"])
def test_nothing_else_is_served(app, path):
    status, data = call(app, "GET", path)
    assert status == 404
    assert "true_facts" not in json.dumps(data)


def test_wrong_methods_are_refused(app):
    assert call(app, "POST", "/", {})[0] == 405
    assert call(app, "GET", "/api/save")[0] == 405
    assert call(app, "GET", "/api/compare")[0] == 405


# Blind while labeling.


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


def assert_names_no_rule(text):
    assert not FORM_NUMBER.search(text), FORM_NUMBER.search(text)
    assert not RULE_ID.search(text), RULE_ID.search(text)
    toks = labels.item_forms(text)
    for rule in RULES:
        for name in labels.match_names(rule):
            assert not labels.contains(toks, labels.tokens(name)), (rule.id, name, text)


def test_state_shows_the_sheet_households_in_order_and_in_the_sheets_words(app):
    status, state = call(app, "GET", "/api/state")
    assert status == 200
    assert [h["household_id"] for h in state["households"]] == re.findall(r"^## (H\d{3}) ", SHEET, re.MULTILINE)
    for h in state["households"]:
        section = "\n".join([f"## {h['household_id']} ({h['split']}, {h['complexity']})", "", "This year:", "",
                             *[f"- {line}" for line in h["this_year"]], "", "Changed since last year:", "",
                             *[f"- {line}" for line in h["changed"]]]) + "\n"
        assert section in SHEET
    assert state["total"] == 30 and state["labeled"] == 0 and state["revealed"] is False


def test_state_carries_no_rule_output_before_the_reveal(app):
    _, state = call(app, "GET", "/api/state")
    assert set(state) == {"households", "labels", "other_rows", "duplicates", "labeler", "labeled", "total",
                          "labels_file", "snapshot_file", "revealed"}
    for h in state["households"]:
        assert set(h) == {"household_id", "split", "complexity", "this_year", "changed"}
        for text in strings(h):
            assert_names_no_rule(text)


def test_the_page_itself_names_no_rule_form_or_document():
    html = label_ui.PAGE.read_text(encoding="utf-8")
    text = re.sub(r"<[^>]+>", " ", html)
    for line in text.splitlines():
        assert_names_no_rule(line)


def test_compare_is_refused_until_every_household_has_a_label(app):
    for hid in app.order[:-1]:
        save(app, hid, [item("W-2")])
    status, data = call(app, "POST", "/api/compare", {"reveal": True})
    assert status == 409 and "29 of 30" in data["error"]
    assert not app.snapshot_path.exists()


def test_compare_needs_an_explicit_reveal(app):
    fill_all(app)
    status, data = call(app, "POST", "/api/compare", {})
    assert status == 409 and "Confirm" in data["error"]
    assert call(app, "POST", "/api/compare", {"reveal": "yes"})[0] == 409
    assert not app.snapshot_path.exists()


def test_compare_shows_how_each_disagreement_was_settled(app):
    fill_all(app, [item("W-2", 2)])
    status, data = call(app, "POST", "/api/compare", {"reveal": True})
    assert status == 200 and data["settlements_file"] == "" and data["settled"] == 0
    assert data["unsettled"] == data["disagreements"] > 0
    first = next(r for rs in data["households"].values() for r in rs if r["disagreement"])
    hid = next(h for h, rs in data["households"].items() if first in rs)
    with open(labels.settlements_path(app.labels_path), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(labels.SETTLEMENT_HEADER)
        w.writerow([hid, first["kind"], first["rule_id"], first["rules_say"], first["label_says"], first["item"],
                    "", "changes_only", "the fact did not change"])
    status, data = call(app, "POST", "/api/compare", {})
    assert status == 200 and data["settled"] == 1 and data["unsettled"] == data["disagreements"] - 1
    assert data["settlements_file"].endswith("blind_labels_settlements.csv")
    rows = data["households"][hid]
    assert [(r["settlement"], r["settlement_note"]) for r in rows if r["settlement"]] == [
        ("changes_only", "the fact did not change")]
    labels.settlements_path(app.labels_path).write_text("bad header\n", encoding="utf-8")
    status, data = call(app, "POST", "/api/compare", {})
    assert status == 409 and "header must be" in data["error"]


def test_the_reveal_keeps_the_blind_labels_once_and_matches_the_cli(app):
    fill_all(app, [item("W-2", 2), item("Mortgage interest statement")])
    blind = app.labels_path.read_text(encoding="utf-8")
    status, data = call(app, "POST", "/api/compare", {"reveal": True})
    assert status == 200
    assert app.snapshot_path.read_text(encoding="utf-8") == blind
    assert call(app, "GET", "/api/state")[1]["revealed"] is True

    rows, labeled = labels.compare_file(app.labels_path, DATA_DIR)
    assert labeled == data["labeled"] == 30
    assert data["disagreements"] == sum(1 for d in rows if labels.is_disagreement(d))
    got = [(hid, r["kind"], r["rule_id"], r["rules_say"], r["label_says"], r["item"])
           for hid, rs in data["households"].items() for r in rs]
    want = [(d.household_id, d.kind, d.rule_id, d.engine_count, d.label_count, d.item) for d in rows]
    assert sorted(got) == sorted(want)
    by_id = {r.id: r for r in RULES}
    for rs in data["households"].values():
        for r in rs:
            if r["rule_id"]:
                assert r["source_url"] == by_id[r["rule_id"]].source_url

    # After the reveal, edits and compares go on, and the snapshot never changes.
    hid = app.order[0]
    assert save(app, hid, [item("W-2")])[0] == 200
    assert call(app, "POST", "/api/compare", {})[0] == 200
    assert app.snapshot_path.read_text(encoding="utf-8") == blind


# What gets saved.


def test_save_writes_the_format_compare_reads(app):
    hid = app.order[0]
    status, data = save(app, hid, [item("W-2", 2), item("1099-INT"), item(""), item("1095-A", "1")],
                        notes="first pass", labeled_by="roanuk")
    assert status == 200, data
    assert data["row"]["documents"] == "W-2 x2; 1099-INT; 1095-A"
    assert data["labeled"] == 1
    assert csv_rows(app.labels_path) == [
        {"household_id": hid, "documents": "W-2 x2; 1099-INT; 1095-A", "labeled_by": "roanuk", "notes": "first pass"}]
    parsed = labels.parse_documents(data["row"]["documents"], RULES, load_ignore_aliases())
    assert [(i.candidates, i.count) for i in parsed] == [(("R001",), 2), (("R004",), 1), (("R012",), 1)]
    assert data["rows"] == [[item("W-2", 2)], [item("1099-INT")], [], [item("1095-A")]]


def test_rows_are_written_in_sheet_order_with_the_exact_header(app):
    for hid in reversed(app.order[:3]):
        save(app, hid, [item("W-2")])
    text = app.labels_path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "household_id,documents,labeled_by,notes"
    assert [r["household_id"] for r in csv_rows(app.labels_path)] == app.order[:3]
    labels.read_labels(app.labels_path)


@pytest.mark.parametrize("name, count, stored", [
    ("2 W-2s", 1, ("W-2s", 2)),
    ("two W-2s", 1, ("W-2s", 2)),
    ("W-2 (x3)", 1, ("W-2", 3)),
    ("W-2 (2)", 2, ("W-2", 2)),
    ("W-2 x2", 1, ("W-2", 2)),
    ("K-1 (1065)", 1, ("K-1 (1065)", 1)),
    ("W-2 (2026)", 3, ("W-2 (2026)", 3)),
])
def test_a_count_written_in_the_name_moves_to_the_count_box(app, name, count, stored):
    status, data = save(app, app.order[0], [item(name, count)])
    assert status == 200, data
    assert data["rows"] == [[item(*stored)]]
    assert labels.read_count(data["row"]["documents"]) == (stored[0], None if stored[1] == 1 else stored[1])
    moved = name.strip() != stored[0]
    assert any("Moved the count" in p["message"] for p in data["problems"]) == moved


def test_a_count_in_the_name_that_disagrees_with_the_box_is_refused(app):
    status, data = save(app, app.order[0], [item("W-2"), item("W-2 x2", 3)])
    assert status == 422
    assert [(p["row"], p["level"]) for p in data["problems"]] == [(1, "error")]
    assert app.labels_path.read_text() == HEADER


def test_an_item_compare_would_read_differently_is_refused(app):
    status, data = save(app, app.order[0], [item("1 3 W-2")])
    assert status == 422
    assert "two counts" in data["problems"][0]["message"]


@pytest.mark.parametrize("count", [0, 100, -1, "1.5", "two", True, None])
def test_counts_must_be_whole_numbers_from_1_to_99(app, count):
    status, data = save(app, app.order[0], [item("W-2", count)])
    if count is None:
        assert status == 200 and data["row"]["documents"] == "W-2"
    else:
        assert status == 422, data
        assert data["problems"][0]["level"] == "error"


def test_semicolons_split_a_row_into_items(app):
    status, data = save(app, app.order[0], [item("W-2 x2; 1099-INT;")])
    assert status == 200
    assert data["row"]["documents"] == "W-2 x2; 1099-INT"
    assert data["rows"] == [[item("W-2", 2), item("1099-INT")]]
    assert save(app, app.order[0], [item("W-2; 1099-INT", 2)])[0] == 422


def test_count_words_left_in_a_name_get_a_warning_but_still_save(app):
    status, data = save(app, app.order[0], [item("W-2 for both jobs"), item("Statement from the 2nd lender")])
    assert status == 200
    assert [(p["row"], p["level"]) for p in data["problems"]] == [(0, "warning"), (1, "warning")]
    assert '"both"' in data["problems"][0]["message"] and '"2nd"' in data["problems"][1]["message"]


def test_names_and_notes_are_cleaned(app):
    status, data = save(app, app.order[0], [item("  W-2\t from   work \n")], notes="line one\r\nline two\x00 ",
                        labeled_by=" roanuk ")
    assert status == 200
    assert data["row"]["documents"] == "W-2 from work"
    assert data["row"]["notes"] == "line one\nline two"
    assert data["row"]["labeled_by"] == "roanuk"
    assert csv_rows(app.labels_path)[0]["notes"] == "line one\nline two"


@pytest.mark.parametrize("payload", [
    {"household_id": "H999", "items": []},
    {"household_id": None, "items": []},
    {"household_id": "H002", "items": "W-2"},
    {"household_id": "H002", "items": ["W-2"]},
    {"household_id": "H002", "items": [{"name": 5}]},
    {"household_id": "H002", "items": [item("W-2")] * (label_ui.MAX_ITEMS + 1)},
    {"household_id": "H002", "items": [item("x" * (label_ui.MAX_NAME + 1))]},
    {"household_id": "H002", "items": [], "notes": 3},
    {"household_id": "H002", "items": [], "labeled_by": ["roanuk"]},
    {"household_id": "H002", "items": [], "base": "W-2"},
])
def test_requests_the_page_never_sends_are_refused(app, payload):
    status, _ = call(app, "POST", "/api/save", {"base": None, **payload})
    assert status == 400
    assert app.labels_path.read_text() == HEADER


def test_clearing_a_household_removes_its_row_but_notes_alone_keep_it(app):
    a, b = app.order[:2]
    save(app, a, [item("W-2")])
    save(app, b, [item("W-2")])
    assert save(app, a, [item("")], notes="")[0] == 200
    assert save(app, b, [], notes="come back to this")[0] == 200
    assert csv_rows(app.labels_path) == [
        {"household_id": b, "documents": "", "labeled_by": "roanuk", "notes": "come back to this"}]
    assert call(app, "GET", "/api/state")[1]["labeled"] == 0


def test_a_save_over_a_row_that_changed_on_disk_is_refused(app):
    hid = app.order[0]
    save(app, hid, [item("W-2")])
    stale = {"documents": "W-2", "labeled_by": "roanuk", "notes": ""}
    app.labels_path.write_text(HEADER + f"{hid},W-2 x2,roanuk,edited by hand\n", encoding="utf-8")
    before = app.labels_path.read_text()
    status, data = save(app, hid, [item("1099-INT")], base=stale)
    assert status == 409
    assert data["row"]["documents"] == "W-2 x2" and data["row"]["notes"] == "edited by hand"
    assert app.labels_path.read_text() == before
    status, _ = save(app, hid, [item("1099-INT")], base=None)
    assert status == 409


def test_a_first_save_conflicts_when_the_file_already_has_that_row(app):
    hid = app.order[0]
    app.labels_path.write_text(HEADER + f"{hid},W-2,roanuk,\n", encoding="utf-8")
    assert save(app, hid, [item("1099-INT")], base=None)[0] == 409


def test_other_rows_and_duplicates_are_written_back_unchanged(app):
    a, b = app.order[:2]
    app.labels_path.write_text(HEADER + f'H999,"Odd; item",someone,kept\n{a},W-2,roanuk,first\n'
                               f"{a},1099-INT,roanuk,second\n", encoding="utf-8")
    _, state = call(app, "GET", "/api/state")
    assert state["duplicates"] == [a] and state["other_rows"] == 2
    assert state["labels"][a]["documents"] == "W-2"
    assert save(app, b, [item("W-2")])[0] == 200
    assert csv_rows(app.labels_path) == [
        {"household_id": a, "documents": "W-2", "labeled_by": "roanuk", "notes": "first"},
        {"household_id": b, "documents": "W-2", "labeled_by": "roanuk", "notes": ""},
        {"household_id": "H999", "documents": "Odd; item", "labeled_by": "someone", "notes": "kept"},
        {"household_id": a, "documents": "1099-INT", "labeled_by": "roanuk", "notes": "second"},
    ]


def test_a_wrong_header_is_reported_and_never_overwritten(app):
    app.labels_path.write_text("id,docs\nH002,W-2\n", encoding="utf-8")
    status, data = call(app, "GET", "/api/state")
    assert status == 409 and "header" in data["error"]
    assert save(app, app.order[0], [item("W-2")], base=None)[0] == 409
    assert app.labels_path.read_text() == "id,docs\nH002,W-2\n"


def test_a_missing_labels_file_is_created_on_the_first_save(app):
    app.labels_path.unlink()
    assert call(app, "GET", "/api/state")[1]["labeled"] == 0
    assert save(app, app.order[0], [item("W-2")], base=None)[0] == 200
    assert app.labels_path.read_text().splitlines()[0] == HEADER.strip()


def test_saves_leave_no_temp_files(app, tmp_path):
    for hid in app.order[:3]:
        save(app, hid, [item("W-2")])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["blind_labels.csv"]


def test_existing_cells_load_as_rows_the_way_compare_reads_them(app):
    hid = app.order[0]
    app.labels_path.write_text(HEADER + f'{hid},"2 W-2s; 1099-INT ;; K-1 (1065) x2",roanuk,\n', encoding="utf-8")
    _, state = call(app, "GET", "/api/state")
    assert state["labels"][hid]["items"] == [item("W-2s", 2), item("1099-INT"), item("K-1 (1065)", 2)]


def test_the_labeler_defaults_to_roanuk_then_to_the_name_most_used(app):
    assert call(app, "GET", "/api/state")[1]["labeler"] == "roanuk"
    for hid in app.order[:2]:
        save(app, hid, [item("W-2")], labeled_by="R. Kim")
    save(app, app.order[2], [item("W-2")], labeled_by="roanuk")
    assert call(app, "GET", "/api/state")[1]["labeler"] == "R. Kim"


def test_concurrent_saves_lose_nothing(app):
    def one(hid):
        assert save(app, hid, [item("W-2")], base=None)[0] == 200

    threads = [threading.Thread(target=one, args=(hid,)) for hid in app.order]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [r["household_id"] for r in csv_rows(app.labels_path)] == app.order


# The real request handler, over a Unix socket pair, since pytest blocks TCP.


class StubServer:
    pass


def through_handler(app, request: bytes) -> bytes:
    a, b = socket.socketpair(socket.AF_UNIX)

    def serve():
        try:
            label_ui.handler_for(app)(a, ("127.0.0.1", 0), StubServer())
        finally:
            a.close()

    # The handler runs in its own thread: a socket pair's buffer is smaller than the page.
    t = threading.Thread(target=serve)
    t.start()
    try:
        b.sendall(request)
        b.shutdown(socket.SHUT_WR)
        chunks = []
        while chunk := b.recv(65536):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        t.join(timeout=10)
        b.close()


def test_the_http_handler_serves_the_page_with_safety_headers(app):
    response = through_handler(app, b"GET / HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n\r\n")
    head, _, body = response.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.0 200")
    for header in (b"Content-Security-Policy: default-src 'none'", b"X-Content-Type-Options: nosniff",
                   b"Cache-Control: no-store", b"Referrer-Policy: no-referrer"):
        assert header in head
    assert b"Access-Control-Allow-Origin" not in head
    assert TOKEN.encode() in body


def test_the_http_handler_saves_through_the_app(app):
    payload = json.dumps({"household_id": app.order[0], "items": [item("W-2", 2)], "base": None}).encode()
    request = (b"POST /api/save HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nContent-Type: application/json\r\n"
               b"X-Label-Token: " + TOKEN.encode() + b"\r\nContent-Length: " + str(len(payload)).encode() +
               b"\r\n\r\n" + payload)
    head, _, body = through_handler(app, request).partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.0 200"), head
    assert json.loads(body)["row"]["documents"] == "W-2 x2"


def test_the_http_handler_refuses_an_oversized_body_without_reading_it(app):
    request = (b"POST /api/save HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nContent-Type: application/json\r\n"
               b"X-Label-Token: " + TOKEN.encode() + b"\r\nContent-Length: 999999999\r\n\r\n{}")
    head, _, _ = through_handler(app, request).partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.0 413")


def test_the_sheet_and_the_page_describe_the_same_households():
    truths, specs = load_truths(), load_facts()
    for hid in labels.select_households(truths):
        d = labels.household_description(truths[hid], specs)
        assert labels.household_section(truths[hid], specs) in SHEET
        assert d["household_id"] == hid


# The gate is shared with the compare command.


def test_the_compare_command_keeps_the_gate_too(app, capsys):
    for hid in app.order[:5]:
        save(app, hid, [item("W-2")])
    args = ["compare", "--labels", str(app.labels_path)]
    assert labels.main(args) == 2
    err = capsys.readouterr()
    assert "5 of 30 households have labels" in err.err and "|" not in err.out
    assert labels.main([*args, "--reveal"]) == 2
    assert "5 of 30 households have labels" in capsys.readouterr().err
    assert not app.snapshot_path.exists()

    fill_all(app)
    blind = app.labels_path.read_bytes()
    assert labels.main([*args, "--reveal"]) == 1
    out = capsys.readouterr().out
    assert "kept the blind labels in" in out and "missing_from_label" in out
    assert app.snapshot_path.read_bytes() == blind
    assert call(app, "GET", "/api/state")[1]["revealed"] is True
    # Once on record, compare runs without --reveal, and the snapshot never changes.
    save(app, app.order[0], [item("1099-INT")])
    assert labels.main(args) == 1
    assert app.snapshot_path.read_bytes() == blind


def test_each_labels_file_has_its_own_snapshot(app, tmp_path):
    practice = tmp_path / "practice.csv"
    shutil.copyfile(app.labels_path, practice)
    assert labels.snapshot_path(practice) != app.snapshot_path
    assert labels.snapshot_path(app.labels_path).name == "blind_labels_before_compare.csv"
    with open(practice, "a", encoding="utf-8") as f:
        for hid in app.order:
            f.write(f"{hid},junk,roanuk,\n")
    labels.reveal(practice, app.order)
    assert labels.snapshot_path(practice).exists()
    _, state = call(app, "GET", "/api/state")
    assert state["revealed"] is False
    assert call(app, "POST", "/api/compare", {})[0] == 409


def test_the_snapshot_is_a_byte_copy_written_once(app):
    rows = "".join(f'{hid},"W-2\r\nx",roanuk,"line one\r\nline two"\r\n' for hid in app.order)
    app.labels_path.write_bytes(HEADER.encode() + rows.encode())
    blind = app.labels_path.read_bytes()
    assert call(app, "POST", "/api/compare", {"reveal": True})[0] == 200
    assert app.snapshot_path.read_bytes() == blind
    app.labels_path.write_bytes(blind + b"H999,later,roanuk,\r\n")
    labels.reveal(app.labels_path, app.order)
    assert app.snapshot_path.read_bytes() == blind
    assert not [p for p in app.labels_path.parent.iterdir() if p.name.startswith(".")]


def test_a_cell_of_only_semicolons_is_no_label(app):
    app.labels_path.write_text(HEADER + "".join(f"{hid},W-2,roanuk,\n" for hid in app.order[1:])
                               + f"{app.order[0]}, ; ;,roanuk,\n", encoding="utf-8")
    _, state = call(app, "GET", "/api/state")
    assert state["labeled"] == 29
    status, data = call(app, "POST", "/api/compare", {"reveal": True})
    assert status == 409 and "29 of 30" in data["error"]
    assert labels.compare_file(app.labels_path, DATA_DIR)[1] == 29


# The file is never rewritten in a way that loses text.


def test_a_row_with_extra_cells_stops_saves_instead_of_losing_text(app):
    text = HEADER + f"{app.order[0]},W-2,roanuk,second job ended in May, check the date\n"
    app.labels_path.write_text(text, encoding="utf-8")
    status, data = call(app, "GET", "/api/state")
    assert status == 409 and "line 2" in data["error"] and "quotes" in data["error"]
    assert save(app, app.order[1], [item("W-2")], base=None)[0] == 409
    assert app.labels_path.read_text(encoding="utf-8") == text


def test_missing_trailing_cells_read_as_empty(app):
    app.labels_path.write_text(HEADER + f"{app.order[0]},W-2\n", encoding="utf-8")
    _, state = call(app, "GET", "/api/state")
    assert state["labels"][app.order[0]]["documents"] == "W-2" and state["labels"][app.order[0]]["notes"] == ""


def test_a_file_that_is_not_utf8_is_reported(app):
    app.labels_path.write_bytes(HEADER.encode() + f"{app.order[0]},W-2,roanuk,caf\xe9\n".encode("latin-1"))
    status, data = call(app, "GET", "/api/state")
    assert status == 409 and "UTF-8" in data["error"]


def test_saving_only_the_notes_keeps_the_cell_as_written(app):
    hid = app.order[0]
    app.labels_path.write_text(HEADER + f'{hid},"2 W-2s;1099-INT ; W-2s x1",roanuk,\n', encoding="utf-8")
    _, state = call(app, "GET", "/api/state")
    status, data = save(app, hid, state["labels"][hid]["items"], notes="checked")
    assert status == 200
    assert csv_rows(app.labels_path)[0]["documents"] == "2 W-2s;1099-INT ; W-2s x1"
    assert csv_rows(app.labels_path)[0]["notes"] == "checked"


def test_a_plural_name_keeps_its_count_of_one(app):
    status, data = save(app, app.order[0], [item("W-2s"), item("W-2")])
    assert status == 200
    assert data["row"]["documents"] == "W-2s x1; W-2"
    parsed = labels.parse_documents(data["row"]["documents"], RULES, load_ignore_aliases())
    assert [(i.count, i.unread_counts) for i in parsed] == [(1, ()), (1, ())]


@pytest.mark.parametrize("cell", ["W-2 x0", "(2)", "(x3); W-2"])
def test_a_cell_the_page_cant_show_faithfully_is_refused_on_save(app, cell):
    hid = app.order[0]
    app.labels_path.write_text(HEADER + f'{hid},"{cell}",roanuk,\n', encoding="utf-8")
    _, state = call(app, "GET", "/api/state")
    items = state["labels"][hid]["items"]
    assert all(i["name"] for i in items)
    status, data = save(app, hid, items + [item("1099-INT")])
    assert status == 422, data
    assert csv_rows(app.labels_path)[0]["documents"] == cell


def test_long_dashes_become_hyphens(app):
    status, data = save(app, app.order[0], [item("W" + chr(0x2013) + "2")], notes="ended" + chr(0x2014) + "May",
                        labeled_by="roanuk")
    assert status == 200
    assert data["row"]["documents"] == "W-2" and data["row"]["notes"] == "ended-May"


def test_a_warning_names_the_piece_it_belongs_to(app):
    status, data = save(app, app.order[0], [item("W-2; statement from both banks")])
    assert status == 200
    assert data["rows"] == [[item("W-2"), item("statement from both banks")]]
    hint = [p for p in data["problems"] if '"both"' in p["message"]]
    assert hint and hint[0]["row"] == 0 and hint[0]["item"] == 1


def test_a_huge_number_in_the_body_is_a_bad_request(app):
    body = b'{"household_id": "H002", "items": [{"name": "W-2", "count": ' + b"9" * 5000 + b"}]}"
    assert call(app, "POST", "/api/save", body)[0] == 400


# The handler.


def test_an_unexpected_error_answers_500_and_the_server_keeps_going(app, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(app, "state", boom)
    request = (b"GET /api/state HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nX-Label-Token: " + TOKEN.encode() + b"\r\n\r\n")
    head, _, body = through_handler(app, request).partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.0 500")
    assert "RuntimeError" in json.loads(body)["error"] and "disk on fire" not in json.loads(body)["error"]
    assert "disk on fire" in capsys.readouterr().err


def test_an_idle_connection_is_dropped_after_the_timeout(app, monkeypatch):
    monkeypatch.setattr(label_ui, "REQUEST_TIMEOUT_S", 0.3)
    a, b = socket.socketpair(socket.AF_UNIX)
    started = time.monotonic()
    t = threading.Thread(target=lambda: label_ui.handler_for(app)(a, ("127.0.0.1", 0), StubServer()))
    t.start()
    t.join(timeout=5)
    assert not t.is_alive() and time.monotonic() - started < 3
    a.close()
    b.close()


def test_the_page_opens_no_window_handle_to_other_sites(app):
    head = through_handler(app, b"GET / HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n\r\n").partition(b"\r\n\r\n")[0]
    assert b"Cross-Origin-Opener-Policy: same-origin" in head


def test_the_server_gives_each_connection_its_own_thread():
    source = label_ui.main.__code__.co_names
    assert "ThreadingHTTPServer" in source and "HTTPServer" not in source


# Second review.


def test_the_snapshot_itself_cant_be_opened_or_compared(app, capsys):
    with pytest.raises(ValueError, match="record of the blind labels"):
        label_ui.LabelApp(app.snapshot_path, DATA_DIR)
    assert labels.main(["compare", "--labels", str(app.snapshot_path), "--reveal"]) == 2
    assert "record of the blind labels" in capsys.readouterr().err
    assert not labels.snapshot_path(app.snapshot_path).exists()


@pytest.mark.parametrize("cell", ["3 W-2 x1", "W-2 (2) x1", "two W-2s x1", "1 W-2 (1)"])
def test_an_item_with_two_counts_is_refused_not_rewritten(app, cell):
    hid = app.order[0]
    app.labels_path.write_text(HEADER + f'{hid},"{cell}",roanuk,\n', encoding="utf-8")
    _, state = call(app, "GET", "/api/state")
    assert state["labels"][hid]["items"] == [item(cell)]
    status, data = save(app, hid, state["labels"][hid]["items"], notes="only the notes changed")
    assert status == 422 and "two counts" in data["problems"][0]["message"]
    assert csv_rows(app.labels_path)[0]["documents"] == cell
    assert save(app, hid, [item("two W-2 x2")])[0] == 422


def test_a_plural_with_no_count_in_the_file_is_written_with_the_count_shown(app):
    hid = app.order[0]
    app.labels_path.write_text(HEADER + f"{hid},W-2s,roanuk,\n", encoding="utf-8")
    _, state = call(app, "GET", "/api/state")
    assert state["labels"][hid]["items"] == [item("W-2s")]
    status, data = save(app, hid, [item("W-2s")])
    assert status == 200 and data["row"]["documents"] == "W-2s x1"


@pytest.mark.parametrize("count", ["\u00b2", "9" * 5000, " 7 ", "007"])
def test_odd_count_strings_get_the_count_error_not_a_crash(app, count):
    status, data = save(app, app.order[0], [item("W-2", count)])
    if count.strip() in ("7", "007"):
        assert status == 200 and data["row"]["documents"] == "W-2 x7"
    else:
        assert status == 422 and "whole number" in data["problems"][0]["message"]


def test_file_errors_point_at_the_file_not_the_header(app):
    app.labels_path.write_text(HEADER + f"{app.order[0]},W-2,roanuk,a, b\n", encoding="utf-8")
    _, data = call(app, "GET", "/api/state")
    assert data["error"].endswith("Fix the file by hand, then reload.") and "header" not in data["error"]
