"""Block 3: the MCP server "tax".

Every tool's happy path and refusals; documents that come from the engine and
the server's statuses; not_asked and follow-ups; traps shown as no document
needed; submit freezing the intake; prior_year evidence; the tool-list
snapshot; no tool that offers sending; an MCP client over stdio from an
unrelated working folder; and a path test proving the server opens nothing
outside its profiles and state folders.

The tests use their own profiles and rules in tests/fixtures/server/, never
data/ or the source URLs in rules/document_rules.yaml.

After an intended change to the tool list, regenerate the snapshot with

    UPDATE_SNAPSHOTS=1 uv run pytest tests/test_server.py -k snapshot

and review the diff of tests/snapshots/tool_list.json before committing it.
"""

from __future__ import annotations

import builtins
import io
import itertools
import json
import os
import random
import re
import shutil
import sys
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator

import anyio
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from rules.engine import DOCUMENT_RULES_PATH, FACTS_PATH, load_facts, load_rules, required_documents, triggered_traps
from server import intake, tax_server
from server.intake import PUBLIC_INTAKE_FIELDS, TRAP_DOCUMENT
from server.tax_server import DEFAULT_STATE_DIR, TOOL_NAMES, build_server

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures" / "server"
PROFILES = FIXTURES / "profiles"
RULES_PATH = FIXTURES / "document_rules.yaml"
SNAPSHOT = REPO / "tests" / "snapshots" / "tool_list.json"
SCRIPT = REPO / "server" / "tax_server.py"

BOTH = ("verified", "to_verify")
VERIFIED = ("verified",)
SPECS = load_facts()
RULES = load_rules(RULES_PATH)
RULES_BY_ID = {r.id: r for r in RULES}
TOP_LEVEL_KEYS = [k for k, s in SPECS.items() if not s.is_follow_up]

# PRD 6.1, in listing order.
# The PRD 6.1 table, plus destructiveHint false where it leaves the field blank
# (the MCP spec would default it to true) and openWorldHint false throughout.
_READ = {"readOnlyHint": True, "openWorldHint": False}
_WRITE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
PRD_ANNOTATIONS = {
    "start_intake": _WRITE,
    "list_fact_keys": _READ,
    "get_document_rules": _READ,
    "record_fact": _WRITE,
    "build_request_list": _READ,
    "add_custom_request": _WRITE,
    "note_for_preparer": _WRITE,
    "submit_for_preparer_review": _WRITE,
}
MUTATING = ("record_fact", "add_custom_request", "note_for_preparer", "submit_for_preparer_review")
SENDING_WORDS = re.compile(
    r"\b(send\w*|sent|e-?mail\w*|mail\w*|deliver\w*|dispatch\w*|transmit\w*|forward\w*|notif\w*|"
    r"share\w*|post|posts|posted|posting|fax\w*|upload\w*|text the client|message the client)\b",
    re.IGNORECASE,
)


# Helpers.

def counter_ids():
    n = itertools.count(1)
    return lambda: f"in_{next(n):06x}"


def make_server(tmp_path: Path, statuses=BOTH, profiles: Path = PROFILES, **kw: Any) -> MCPServer:
    kw.setdefault("rules_path", RULES_PATH)
    kw.setdefault("id_factory", counter_ids())
    return build_server(profiles, statuses=statuses, state_dir=tmp_path / "state", **kw)


def call(server: MCPServer, name: str, **arguments: Any) -> dict[str, Any]:
    result = anyio.run(server.call_tool, name, arguments)
    assert not result.is_error
    return result.structured_content


def refused(server: MCPServer, name: str, **arguments: Any) -> str:
    """The message of a deliberate refusal; a crash fails the test."""
    with pytest.raises(ToolError) as info:
        anyio.run(server.call_tool, name, arguments)
    assert not isinstance(info.value, UnexpectedToolError), f"{name} crashed: {info.value.__cause__!r}"
    return str(info.value)


def saved(tmp_path: Path, intake_id: str) -> dict[str, Any]:
    return json.loads((tmp_path / "state" / f"{intake_id}.json").read_text())


def start(server: MCPServer, household_id: str = "H001") -> str:
    return call(server, "start_intake", household_id=household_id)["intake_id"]


def record(server: MCPServer, intake_id: str, key: str, value: Any, evidence: str = "that's right") -> dict[str, Any]:
    return call(server, "record_fact", intake_id=intake_id, key=key, value=value, evidence=evidence)


def listing_entry(tool: Any) -> dict[str, Any]:
    d = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    return {k: d[k] for k in ("name", "title", "description", "annotations", "inputSchema")}


def tool_listing(server: MCPServer) -> list[dict[str, Any]]:
    return [listing_entry(t) for t in anyio.run(server.list_tools)]


def doc_pairs(listing: dict[str, Any]) -> list[tuple[str, int]]:
    return [(d["rule_id"], d["count"]) for d in listing["documents"]]


@pytest.fixture
def anyio_backend():
    return "asyncio"


# Tool list.

def test_eight_tools_in_fixed_order_with_prd_annotations(tmp_path):
    listing = tool_listing(make_server(tmp_path))
    assert list(TOOL_NAMES) == list(PRD_ANNOTATIONS)
    assert [t["name"] for t in listing] == list(PRD_ANNOTATIONS)
    for tool in listing:
        assert tool["title"], tool["name"]
        assert tool["description"], tool["name"]
        assert tool["annotations"] == PRD_ANNOTATIONS[tool["name"]], tool["name"]


def test_tool_list_matches_snapshot(tmp_path):
    listing = tool_listing(make_server(tmp_path))
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(listing, indent=2) + "\n")
    assert SNAPSHOT.exists(), "no snapshot yet; run with UPDATE_SNAPSHOTS=1"
    assert listing == json.loads(SNAPSHOT.read_text()), "tool list changed; see this module's docstring to regenerate"


def test_tool_list_is_the_same_for_any_data(tmp_path):
    other = make_server(tmp_path / "b", statuses=VERIFIED, rules_path=DOCUMENT_RULES_PATH)
    assert tool_listing(make_server(tmp_path / "a")) == tool_listing(other)


def test_no_tool_offers_sending(tmp_path):
    listing = tool_listing(make_server(tmp_path))
    assert len(listing) == 8
    hits = {t["name"]: SENDING_WORDS.findall(json.dumps(t)) for t in listing}
    assert not any(hits.values()), hits


# start_intake

def test_start_intake_returns_handle_and_last_year(tmp_path):
    server = make_server(tmp_path)
    out = call(server, "start_intake", household_id="H002")
    profile = json.loads((PROFILES / "H002.json").read_text())
    assert list(out) == list(PUBLIC_INTAKE_FIELDS)
    assert out == {
        "intake_id": "in_000001",
        "household_id": "H002",
        "client_name": "Robin and Sam Testcase",
        "tax_year": 2026,
        "prior_year_summary": profile["prior_year_summary"],
    }
    state = saved(tmp_path, "in_000001")
    assert state["status"] == "open"
    assert state["statuses"] == list(BOTH)
    assert state["facts"] == {} and state["fact_history"] == []


def test_default_intake_ids_are_server_minted_hex(tmp_path):
    server = make_server(tmp_path, id_factory=None)
    ids = {start(server) for _ in range(5)}
    assert len(ids) == 5
    assert all(re.fullmatch(r"in_[0-9a-f]{6}", i) for i in ids)


def test_minting_skips_an_id_with_a_state_file(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "in_000001.json").write_text("{}")
    assert start(make_server(tmp_path)) == "in_000002"


def test_a_bad_id_factory_is_a_server_fault_not_a_refusal(tmp_path):
    server = make_server(tmp_path, id_factory=lambda: "../escape")
    with pytest.raises(UnexpectedToolError):
        anyio.run(server.call_tool, "start_intake", {"household_id": "H001"})
    assert not list((tmp_path / "state").iterdir())


@pytest.mark.parametrize("household_id", [
    "../truth/H017", "H017/../../x", "/etc/passwd", "H17", "H0171", "h017", "H017.json", "H017\n",
    " H017", "", "..", "H01a", "H١٢٣", "H017/", "H017\x00", "data/profiles/H001",
])
def test_start_intake_refuses_household_ids_that_are_not_h_and_three_digits(tmp_path, household_id):
    message = refused(make_server(tmp_path), "start_intake", household_id=household_id)
    assert "household_id must be H followed by three digits" in message


def test_start_intake_refuses_a_missing_profile(tmp_path):
    assert "no profile for household H999" in refused(make_server(tmp_path), "start_intake", household_id="H999")


def test_start_intake_refuses_a_non_string_household_id(tmp_path):
    refused(make_server(tmp_path), "start_intake", household_id=17)


@pytest.mark.parametrize("household_id,content,message", [
    ("H010", "{not json", "could not be read"),
    ("H011", {"household_id": "H012", "tax_year": 2026, "prior_year_summary": {}}, "malformed"),
    ("H012", {"household_id": "H012", "tax_year": 2026}, "malformed"),
    ("H013", {"household_id": "H013", "tax_year": "2026", "prior_year_summary": {}}, "malformed"),
    ("H014", {"household_id": "H014", "tax_year": 2026, "prior_year_summary": {"documents_on_file": {}}}, "malformed"),
    ("H015", ["H015"], "malformed"),
])
def test_start_intake_refuses_malformed_profiles(tmp_path, household_id, content, message):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    text = content if isinstance(content, str) else json.dumps(content)
    (profiles / f"{household_id}.json").write_text(text)
    assert message in refused(make_server(tmp_path, profiles=profiles), "start_intake", household_id=household_id)


def test_start_intake_refuses_directories_and_links_in_the_profiles_folder(tmp_path):
    profiles = tmp_path / "profiles"
    shutil.copytree(PROFILES, profiles)
    (profiles / "H020.json").mkdir()
    (profiles / "H021.json").symlink_to(profiles / "H001.json")
    server = make_server(tmp_path, profiles=profiles)
    for household_id in ("H020", "H021"):
        assert "not a regular file" in refused(server, "start_intake", household_id=household_id)


def test_a_profile_without_a_client_name_starts_with_none(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "H030.json").write_text(json.dumps({"household_id": "H030", "tax_year": 2026, "prior_year_summary": {}}))
    assert call(make_server(tmp_path, profiles=profiles), "start_intake", household_id="H030")["client_name"] is None


# list_fact_keys

def test_list_fact_keys_gives_every_key_in_order(tmp_path):
    keys = call(make_server(tmp_path), "list_fact_keys")["fact_keys"]
    assert [k["key"] for k in keys] == list(SPECS)
    by_key = {k["key"]: k for k in keys}
    for entry in keys:
        assert {"key", "type", "topic", "plain_question_hint", "asked_if",
                "determinable_from_prior_year", "prior_year_field"} <= set(entry)
    assert by_key["filing_status"]["values"] == list(SPECS["filing_status"].values)
    assert by_key["filing_status"]["prior_year_field"] == "filing_status"
    assert by_key["filing_status"]["determinable_from_prior_year"] is True
    assert (by_key["dependents_count"]["min"], by_key["dependents_count"]["max"]) == (0, 10)
    assert not {"values", "min", "max"} & set(by_key["self_employment"])
    assert by_key["self_employment"]["prior_year_field"] is None
    assert by_key["clean_energy_install_year"]["asked_if"] == {"fact": "clean_energy_installed", "eq": True}
    assert by_key["w2_employer_count"]["asked_if"] is None


# get_document_rules

def test_get_document_rules_follows_the_server_statuses(tmp_path):
    verified = call(make_server(tmp_path / "v", statuses=VERIFIED), "get_document_rules")["rules"]
    both = call(make_server(tmp_path / "b"), "get_document_rules")["rules"]
    assert [r["rule_id"] for r in verified] == ["R001", "R004", "R011", "R014", "T001"]
    assert [r["rule_id"] for r in both] == ["R001", "R004", "R011", "R012", "R014", "R020", "T001", "T003"]
    assert all(r["status"] == "verified" for r in verified)
    assert "R024" not in {r["rule_id"] for r in both}, "an excluded rule is never shown"


def test_get_document_rules_shows_sources_and_traps_as_no_document_needed(tmp_path):
    rules = {r["rule_id"]: r for r in call(make_server(tmp_path), "get_document_rules")["rules"]}
    r001 = rules["R001"]
    assert r001 == {
        "rule_id": "R001",
        "when": {"all": [{"fact": "w2_employer_count", "gte": 1}]},
        "document": "Form W-2 from each employer",
        "count_from": "w2_employer_count",
        "client_reason": "You were paid wages, so we need the year-end wage statement from each employer.",
        "source_url": "https://www.irs.gov/forms-pubs/about-form-w-2",
        "source_title": "About Form W-2, Wage and Tax Statement",
        "source_version": "guidance",
        "status": "verified",
    }
    for trap_id in ("T001", "T003"):
        trap = rules[trap_id]
        assert trap["document"] == TRAP_DOCUMENT == "No document needed: this credit ended"
        assert "client_reason" not in trap
        assert trap["source_title"] == "IRS FS-2025-05"
        assert trap["source_url"].startswith("https://www.irs.gov/")
    assert rules["T003"]["when"]["all"][1] == {"fact": "ev_acquired_date", "gte": "2026-01-01"}


@pytest.mark.parametrize("fact_key,statuses,expected", [
    ("itemized_last_year", BOTH, ["R011"]),
    ("state_refund_received", BOTH, ["R011"]),
    ("clean_energy_install_year", BOTH, ["T001"]),
    ("ev_acquired_date", VERIFIED, []),
    ("ev_acquired_date", BOTH, ["T003"]),
    ("k1_count", VERIFIED, []),
    ("k1_count", BOTH, ["R020"]),
    ("ip_pin_enrolled", BOTH, []),
    ("filing_status", BOTH, []),
])
def test_get_document_rules_filters_by_the_fact_a_condition_reads(tmp_path, fact_key, statuses, expected):
    rules = call(make_server(tmp_path, statuses=statuses), "get_document_rules", fact_key=fact_key)["rules"]
    assert [r["rule_id"] for r in rules] == expected


def test_get_document_rules_refuses_an_unknown_fact_key(tmp_path):
    for bad in ("not_a_fact", "", "../truth/H017"):
        assert "unknown fact key" in refused(make_server(tmp_path), "get_document_rules", fact_key=bad)


def test_the_real_rules_file_loads_without_depending_on_its_urls(tmp_path):
    real = load_rules()
    server = make_server(tmp_path, rules_path=DOCUMENT_RULES_PATH)
    rules = call(server, "get_document_rules")["rules"]
    assert [r["rule_id"] for r in rules] == [r.id for r in real if r.status in BOTH]
    assert {r["rule_id"] for r in rules if r["document"] == TRAP_DOCUMENT} == {r.id for r in real if r.trap and r.status in BOTH}
    verified_only = make_server(tmp_path / "v", statuses=VERIFIED, rules_path=DOCUMENT_RULES_PATH)
    assert [r["rule_id"] for r in call(verified_only, "get_document_rules")["rules"]] == [
        r.id for r in real if r.status == "verified"]


# record_fact

@pytest.mark.parametrize("key,value,stored", [
    ("w2_employer_count", 2, 2),
    ("w2_employer_count", "3", 3),
    ("self_employment", True, True),
    ("self_employment", "false", False),
    ("filing_status", "married_joint", "married_joint"),
    ("ev_acquired_date", "2026-03-04", "2026-03-04"),
    ("clean_energy_install_year", 2026, 2026),
])
def test_record_fact_stores_the_coerced_value(tmp_path, key, value, stored):
    server = make_server(tmp_path)
    intake_id = start(server)
    out = call(server, "record_fact", intake_id=intake_id, key=key, value=value, evidence="  I said so  ")
    assert out == {"intake_id": intake_id, "key": key, "value": stored, "evidence": "I said so", "times_recorded": 1}
    assert saved(tmp_path, intake_id)["facts"] == {key: {"value": stored, "evidence": "I said so"}}


def test_recording_a_key_again_replaces_the_value_and_keeps_history(tmp_path):
    server = make_server(tmp_path)
    intake_id = start(server)
    record(server, intake_id, "interest_income", True, "yes, same bank")
    record(server, intake_id, "w2_employer_count", 1, "one job")
    out = record(server, intake_id, "interest_income", False, "actually I closed that account")
    assert out["times_recorded"] == 2
    state = saved(tmp_path, intake_id)
    assert state["facts"]["interest_income"] == {"value": False, "evidence": "actually I closed that account"}
    assert state["fact_history"] == [
        {"key": "interest_income", "value": True, "evidence": "yes, same bank"},
        {"key": "w2_employer_count", "value": 1, "evidence": "one job"},
        {"key": "interest_income", "value": False, "evidence": "actually I closed that account"},
    ]
    assert call(server, "build_request_list", intake_id=intake_id)["documents"][0]["rule_id"] == "R001"
    assert "R004" not in [d["rule_id"] for d in call(server, "build_request_list", intake_id=intake_id)["documents"]]


@pytest.mark.parametrize("key,value", [
    ("w2_employer_count", "two"),
    ("w2_employer_count", True),
    ("w2_employer_count", 11),
    ("w2_employer_count", -1),
    ("w2_employer_count", "²"),
    ("w2_employer_count", "--3"),
    ("self_employment", "yes"),
    ("self_employment", 1),
    ("filing_status", "married"),
    ("ev_acquired_date", "March 2026"),
    ("ev_acquired_date", 20260304),
    ("clean_energy_install_year", 2019),
])
def test_record_fact_refuses_a_value_of_the_wrong_type(tmp_path, key, value):
    server = make_server(tmp_path)
    intake_id = start(server)
    message = refused(server, "record_fact", intake_id=intake_id, key=key, value=value, evidence="client said")
    assert key in message
    assert saved(tmp_path, intake_id)["facts"] == {}


@pytest.mark.parametrize("value", [2.5, None, [1], {"a": 1}])
def test_record_fact_refuses_values_that_are_not_bool_int_or_text(tmp_path, value):
    server = make_server(tmp_path)
    intake_id = start(server)
    refused(server, "record_fact", intake_id=intake_id, key="w2_employer_count", value=value, evidence="client said")


def test_record_fact_refuses_unknown_intakes_and_keys(tmp_path):
    server = make_server(tmp_path)
    intake_id = start(server)
    for bad in ("in_ffffff", "../truth/H017", ""):
        message = refused(server, "record_fact", intake_id=bad, key="unemployment", value=True, evidence="yes")
        assert "unknown intake_id" in message
    message = refused(server, "record_fact", intake_id=intake_id, key="lottery_ticket", value=True, evidence="yes")
    assert "unknown fact key 'lottery_ticket'" in message


@pytest.mark.parametrize("evidence", ["", "   ", "\n"])
def test_record_fact_refuses_empty_evidence(tmp_path, evidence):
    server = make_server(tmp_path)
    intake_id = start(server)
    message = refused(server, "record_fact", intake_id=intake_id, key="unemployment", value=True, evidence=evidence)
    assert "evidence is empty" in message


@pytest.mark.parametrize("household_id,evidence", [
    ("H001", "prior_year:filing_status"),
    ("H001", "prior_year:itemized"),
    ("H001", "prior_year:tax_year"),
    ("H001", "prior_year:documents_on_file"),
    ("H001", "prior_year:documents_on_file.R001"),
    ("H001", "  prior_year: dependents_count "),
    ("H002", "prior_year:documents_on_file.R004"),
    ("H002", "prior_year:documents_on_file.R014"),
])
def test_record_fact_accepts_prior_year_fields_in_that_intakes_summary(tmp_path, household_id, evidence):
    server = make_server(tmp_path)
    intake_id = start(server, household_id)
    out = record(server, intake_id, "itemized_last_year", False, evidence)
    assert out["evidence"] == evidence.strip()


@pytest.mark.parametrize("household_id,evidence", [
    ("H001", "prior_year:"),
    ("H001", "prior_year:marketplace_coverage"),
    ("H001", "prior_year:filing"),
    ("H001", "prior_year:documents_on_file.R004"),
    ("H001", "prior_year:documents_on_file.R999"),
    ("H001", "prior_year:documents_on_file."),
    ("H001", "prior_year:../truth/H001"),
    ("H002", "prior_year:documents_on_file.R012"),
    ("H002", "prior_year:Filing_Status"),
])
def test_record_fact_refuses_prior_year_evidence_naming_no_field(tmp_path, household_id, evidence):
    server = make_server(tmp_path)
    intake_id = start(server, household_id)
    message = refused(server, "record_fact", intake_id=intake_id, key="filing_status", value="single", evidence=evidence)
    assert "last year's summary has no field" in message
    assert "prior_year:filing_status" in message, "the refusal lists the fields that would work"
    assert saved(tmp_path, intake_id)["facts"] == {}


def test_record_fact_leaves_quote_checks_to_the_harness_hook(tmp_path):
    server = make_server(tmp_path)
    intake_id = start(server)
    out = record(server, intake_id, "w2_employer_count", 2, "I also picked up a part-time job in October")
    assert out["evidence"] == "I also picked up a part-time job in October"


# build_request_list

def test_an_empty_intake_lists_nothing_and_every_top_level_key_as_not_asked(tmp_path):
    server = make_server(tmp_path)
    out = call(server, "build_request_list", intake_id=start(server))
    assert out == {"documents": [], "custom_requests": [], "not_asked": TOP_LEVEL_KEYS, "no_document_needed": []}
    assert len(TOP_LEVEL_KEYS) == 36


def test_documents_follow_the_engine_and_exclude_to_verify_rules_when_verified_only(tmp_path):
    facts = {"w2_employer_count": 3, "marketplace_coverage": True, "k1_count": 2, "has_mortgage": True}
    lists = {}
    for statuses in (VERIFIED, BOTH):
        server = make_server(tmp_path / "-".join(statuses), statuses=statuses)
        intake_id = start(server)
        for key, value in facts.items():
            record(server, intake_id, key, value)
        lists[statuses] = call(server, "build_request_list", intake_id=intake_id)
    assert doc_pairs(lists[VERIFIED]) == [("R001", 3), ("R014", 1)]
    assert doc_pairs(lists[BOTH]) == [("R001", 3), ("R012", 1), ("R014", 1), ("R020", 2)]
    w2 = lists[BOTH]["documents"][0]
    assert w2 == {
        "rule_id": "R001",
        "document": "Form W-2 from each employer",
        "count": 3,
        "client_reason": "You were paid wages, so we need the year-end wage statement from each employer.",
        "source_url": "https://www.irs.gov/forms-pubs/about-form-w-2",
        "source_title": "About Form W-2, Wage and Tax Statement",
    }


def test_a_rule_that_needs_two_facts_waits_for_both(tmp_path):
    server = make_server(tmp_path, statuses=VERIFIED)
    intake_id = start(server, "H002")
    record(server, intake_id, "state_refund_received", True, "we got a state refund")
    assert doc_pairs(call(server, "build_request_list", intake_id=intake_id)) == []
    record(server, intake_id, "itemized_last_year", True, "prior_year:itemized")
    assert doc_pairs(call(server, "build_request_list", intake_id=intake_id)) == [("R011", 1)]


def test_an_excluded_rule_never_produces_a_document(tmp_path):
    server = make_server(tmp_path)
    intake_id = start(server)
    record(server, intake_id, "ip_pin_enrolled", True, "yes, I get a PIN every year")
    assert doc_pairs(call(server, "build_request_list", intake_id=intake_id)) == []


def test_a_counted_rule_with_count_zero_does_not_apply(tmp_path):
    server = make_server(tmp_path)
    intake_id = start(server)
    record(server, intake_id, "w2_employer_count", 0, "no job this year")
    record(server, intake_id, "k1_count", 0, "none")
    assert doc_pairs(call(server, "build_request_list", intake_id=intake_id)) == []


def _random_facts(rng: random.Random) -> dict[str, Any]:
    pool = {
        "w2_employer_count": lambda: rng.randint(0, 3),
        "interest_income": lambda: rng.random() < 0.5,
        "state_refund_received": lambda: rng.random() < 0.5,
        "itemized_last_year": lambda: rng.random() < 0.5,
        "marketplace_coverage": lambda: rng.random() < 0.5,
        "has_mortgage": lambda: rng.random() < 0.5,
        "k1_count": lambda: rng.randint(0, 3),
        "ip_pin_enrolled": lambda: rng.random() < 0.5,
        "clean_energy_installed": lambda: rng.random() < 0.6,
        "clean_energy_install_year": lambda: rng.randint(2024, 2026),
        "ev_purchased": lambda: rng.random() < 0.6,
        "ev_acquired_date": lambda: (date(2025, 6, 1) + timedelta(days=rng.randint(0, 600))).isoformat(),
    }
    return {key: make() for key, make in pool.items() if rng.random() < 0.7}


@pytest.mark.parametrize("statuses", [VERIFIED, BOTH], ids=["verified", "both"])
def test_documents_and_traps_always_equal_the_engine_on_the_recorded_facts(tmp_path, statuses):
    rng = random.Random(3)
    server = make_server(tmp_path, statuses=statuses)
    for _ in range(40):
        facts = _random_facts(rng)
        intake_id = start(server)
        for key, value in facts.items():
            record(server, intake_id, key, value)
        out = call(server, "build_request_list", intake_id=intake_id)
        expected = required_documents(facts, RULES, SPECS, statuses)
        assert doc_pairs(out) == [(d["rule_id"], d["count"]) for d in expected], facts
        assert [t["rule_id"] for t in out["no_document_needed"]] == triggered_traps(facts, RULES, SPECS, statuses), facts


def test_not_asked_lists_follow_ups_only_when_their_parent_was_recorded_yes(tmp_path):
    server = make_server(tmp_path)
    intake_id = start(server)

    def not_asked() -> list[str]:
        return call(server, "build_request_list", intake_id=intake_id)["not_asked"]

    assert "clean_energy_install_year" not in not_asked()
    record(server, intake_id, "clean_energy_installed", True, "we put in solar")
    assert "clean_energy_installed" not in not_asked()
    assert "clean_energy_install_year" in not_asked()
    record(server, intake_id, "clean_energy_install_year", 2026, "finished in June")
    assert "clean_energy_install_year" not in not_asked()

    record(server, intake_id, "ev_purchased", False, "no")
    assert "ev_acquired_date" not in not_asked()
    record(server, intake_id, "ev_purchased", True, "oh wait, yes we did")
    assert "ev_acquired_date" in not_asked()

    record(server, intake_id, "efficiency_improvements_year", 2025, "2025")
    assert "efficiency_improvements" in not_asked(), "a follow-up never answers its parent"
    assert "efficiency_improvements_year" not in not_asked()
    assert not_asked() == [k for k in SPECS if k not in {
        "clean_energy_installed", "clean_energy_install_year", "ev_purchased", "efficiency_improvements_year"}
        and (not SPECS[k].is_follow_up or k == "ev_acquired_date")]


@pytest.mark.parametrize("year,expected", [(2026, ["T001"]), (2025, [])])
def test_a_clean_energy_install_in_2026_needs_no_document(tmp_path, year, expected):
    server = make_server(tmp_path, statuses=VERIFIED)
    intake_id = start(server)
    record(server, intake_id, "clean_energy_installed", True, "we put in solar panels")
    record(server, intake_id, "clean_energy_install_year", year, "finished that year")
    out = call(server, "build_request_list", intake_id=intake_id)
    assert out["documents"] == []
    assert [t["rule_id"] for t in out["no_document_needed"]] == expected
    if expected:
        assert out["no_document_needed"][0] == {
            "rule_id": "T001",
            "reason": "No document needed: this credit ended",
            "source_url": RULES_BY_ID["T001"].source_url,
            "source_title": "IRS FS-2025-05",
        }


def test_a_to_verify_trap_counts_only_when_the_server_counts_to_verify(tmp_path):
    for statuses, expected in ((VERIFIED, []), (BOTH, ["T003"])):
        server = make_server(tmp_path / statuses[-1], statuses=statuses)
        intake_id = start(server)
        record(server, intake_id, "ev_purchased", True, "we bought an electric car")
        record(server, intake_id, "ev_acquired_date", "2026-05-01", "signed in May")
        assert [t["rule_id"] for t in call(server, "build_request_list", intake_id=intake_id)["no_document_needed"]] == expected


def test_build_request_list_refuses_an_unknown_intake(tmp_path):
    assert "unknown intake_id" in refused(make_server(tmp_path), "build_request_list", intake_id="in_abcdef")


def test_build_request_list_does_not_write_state(tmp_path):
    server = make_server(tmp_path)
    intake_id = start(server)
    path = tmp_path / "state" / f"{intake_id}.json"
    before = (path.read_text(), path.stat().st_mtime_ns)
    call(server, "build_request_list", intake_id=intake_id)
    call(server, "list_fact_keys")
    call(server, "get_document_rules")
    assert (path.read_text(), path.stat().st_mtime_ns) == before


# add_custom_request and note_for_preparer

def test_add_custom_request_is_flagged_for_the_preparer(tmp_path):
    server = make_server(tmp_path)
    intake_id = start(server)
    out = call(server, "add_custom_request", intake_id=intake_id,
               document="Statement for the community garden plot", reason="The client rents a garden plot to neighbors.")
    assert out == {
        "document": "Statement for the community garden plot",
        "reason": "The client rents a garden plot to neighbors.",
        "needs_preparer_review": True,
    }
    assert call(server, "build_request_list", intake_id=intake_id)["custom_requests"] == [out]
    assert saved(tmp_path, intake_id)["custom_requests"] == [out]


@pytest.mark.parametrize("document,reason,missing", [("", "a reason", "document"), ("a doc", "  ", "reason")])
def test_add_custom_request_refuses_empty_text(tmp_path, document, reason, missing):
    server = make_server(tmp_path)
    intake_id = start(server)
    message = refused(server, "add_custom_request", intake_id=intake_id, document=document, reason=reason)
    assert f"{missing} is empty" in message


def test_note_for_preparer_is_stored(tmp_path):
    server = make_server(tmp_path)
    intake_id = start(server)
    out = call(server, "note_for_preparer", intake_id=intake_id, text="Client asked: can I write off my home office?")
    assert out == {"text": "Client asked: can I write off my home office?"}
    assert saved(tmp_path, intake_id)["notes_for_preparer"] == [out]
    assert "text is empty" in refused(server, "note_for_preparer", intake_id=intake_id, text="")


def test_custom_requests_and_notes_refuse_unknown_intakes(tmp_path):
    server = make_server(tmp_path)
    assert "unknown intake_id" in refused(server, "add_custom_request", intake_id="in_123456", document="d", reason="r")
    assert "unknown intake_id" in refused(server, "note_for_preparer", intake_id="in_123456", text="t")
    assert "unknown intake_id" in refused(server, "submit_for_preparer_review", intake_id="in_123456")


# submit_for_preparer_review

def _filled_intake(server: MCPServer) -> str:
    intake_id = start(server, "H002")
    record(server, intake_id, "w2_employer_count", 2, "both of us still work there")
    record(server, intake_id, "interest_income", True, "prior_year:documents_on_file.R004")
    record(server, intake_id, "clean_energy_installed", True, "we added solar")
    record(server, intake_id, "clean_energy_install_year", 2026, "it was done in April")
    call(server, "add_custom_request", intake_id=intake_id, document="Receipt for the garden plot", reason="Client rents it out.")
    call(server, "note_for_preparer", intake_id=intake_id, text="Client asked whether solar is deductible.")
    return intake_id


def test_submit_returns_the_frozen_draft(tmp_path):
    server = make_server(tmp_path, statuses=VERIFIED)
    intake_id = _filled_intake(server)
    built = call(server, "build_request_list", intake_id=intake_id)
    draft = call(server, "submit_for_preparer_review", intake_id=intake_id)
    assert list(draft) == ["intake_id", "household_id", "status", "documents", "custom_requests", "not_asked",
                           "no_document_needed", "notes_for_preparer", "facts_recorded"]
    assert draft["intake_id"] == intake_id and draft["household_id"] == "H002"
    assert draft["status"] == "pending_review"
    for part in ("documents", "custom_requests", "not_asked", "no_document_needed"):
        assert draft[part] == built[part]
    assert doc_pairs(draft) == [("R001", 2), ("R004", 1)]
    assert [t["rule_id"] for t in draft["no_document_needed"]] == ["T001"]
    assert draft["notes_for_preparer"] == [{"text": "Client asked whether solar is deductible."}]
    assert draft["facts_recorded"]["interest_income"] == {"value": True, "evidence": "prior_year:documents_on_file.R004"}
    assert list(draft["facts_recorded"]) == [
        "w2_employer_count", "interest_income", "clean_energy_installed", "clean_energy_install_year"]
    state = saved(tmp_path, intake_id)
    assert state["status"] == "pending_review"
    assert state["draft"] == draft


def test_after_submit_every_mutating_tool_refuses(tmp_path):
    server = make_server(tmp_path)
    intake_id = _filled_intake(server)
    draft = call(server, "submit_for_preparer_review", intake_id=intake_id)
    before = saved(tmp_path, intake_id)
    attempts = {
        "record_fact": {"key": "unemployment", "value": True, "evidence": "yes"},
        "add_custom_request": {"document": "d", "reason": "r"},
        "note_for_preparer": {"text": "one more thing"},
        "submit_for_preparer_review": {},
    }
    assert set(attempts) == set(MUTATING)
    for name, arguments in attempts.items():
        message = refused(server, name, intake_id=intake_id, **arguments)
        assert "already submitted for preparer review" in message, name
    assert saved(tmp_path, intake_id) == before
    rebuilt = call(server, "build_request_list", intake_id=intake_id)
    assert {k: draft[k] for k in rebuilt} == rebuilt
    other = start(server)
    record(server, other, "unemployment", True, "yes, for a month")


# State on disk.

def test_state_is_written_after_every_mutation(tmp_path):
    server = make_server(tmp_path)
    intake_id = start(server)
    assert saved(tmp_path, intake_id)["intake_id"] == intake_id
    record(server, intake_id, "unemployment", True, "yes, in the spring")
    assert saved(tmp_path, intake_id)["facts"] == {"unemployment": {"value": True, "evidence": "yes, in the spring"}}
    call(server, "add_custom_request", intake_id=intake_id, document="d", reason="r")
    assert len(saved(tmp_path, intake_id)["custom_requests"]) == 1
    call(server, "note_for_preparer", intake_id=intake_id, text="t")
    assert len(saved(tmp_path, intake_id)["notes_for_preparer"]) == 1
    call(server, "submit_for_preparer_review", intake_id=intake_id)
    assert saved(tmp_path, intake_id)["status"] == "pending_review"
    assert sorted(p.name for p in (tmp_path / "state").iterdir()) == [f"{intake_id}.json"], "no temp files left"


def test_a_refused_call_writes_nothing(tmp_path):
    server = make_server(tmp_path)
    intake_id = start(server)
    path = tmp_path / "state" / f"{intake_id}.json"
    before = (path.read_text(), path.stat().st_mtime_ns)
    refused(server, "record_fact", intake_id=intake_id, key="unemployment", value="maybe", evidence="hmm")
    refused(server, "record_fact", intake_id=intake_id, key="unemployment", value=True, evidence="prior_year:nope")
    refused(server, "add_custom_request", intake_id=intake_id, document="", reason="r")
    assert (path.read_text(), path.stat().st_mtime_ns) == before


# Startup and CLI.

@pytest.mark.parametrize("statuses", ["excluded", "", "verified,bogus", [], ["verified", "excluded"]])
def test_build_server_refuses_statuses_other_than_verified_and_to_verify(tmp_path, statuses):
    with pytest.raises(ValueError, match="statuses"):
        make_server(tmp_path, statuses=statuses)


@pytest.mark.parametrize("statuses,stored", [
    ("verified", ["verified"]),
    ("verified,to_verify", ["verified", "to_verify"]),
    (" to_verify , verified ,", ["to_verify", "verified"]),
    (["verified", "verified"], ["verified"]),
])
def test_build_server_accepts_status_lists(tmp_path, statuses, stored):
    server = make_server(tmp_path, statuses=statuses)
    assert saved(tmp_path, start(server))["statuses"] == stored


def test_build_server_refuses_a_missing_profiles_folder_or_a_truth_folder(tmp_path):
    with pytest.raises(ValueError, match="profiles folder not found"):
        make_server(tmp_path, profiles=tmp_path / "nowhere")
    truth = tmp_path / "data" / "truth"
    truth.mkdir(parents=True)
    with pytest.raises(ValueError, match="truth"):
        make_server(tmp_path, profiles=truth)
    with pytest.raises(ValueError, match="truth"):
        build_server(PROFILES, statuses=BOTH, state_dir=truth / "state", rules_path=RULES_PATH)


@pytest.mark.parametrize("spelling", ["Truth", "TRUTH", "tRuTh"])
def test_build_server_refuses_a_truth_folder_in_any_letter_case(tmp_path, spelling):
    folder = tmp_path / spelling / "data"
    shutil.copytree(PROFILES, folder)
    with pytest.raises(ValueError, match="truth folder"):
        make_server(tmp_path, profiles=folder)
    with pytest.raises(ValueError, match="truth folder"):
        build_server(PROFILES, statuses=BOTH, state_dir=tmp_path / spelling / "state", rules_path=RULES_PATH)


def test_build_server_refuses_another_spelling_of_an_existing_truth_folder(tmp_path):
    truth = tmp_path / "data" / "truth"
    truth.mkdir(parents=True)
    alias = tmp_path / "data" / "Truth"
    if not alias.is_dir():
        pytest.skip("this filesystem is case-sensitive, so Truth is not the truth folder")
    with pytest.raises(ValueError, match="truth folder"):
        make_server(tmp_path, profiles=alias)


def test_build_server_refuses_the_known_truth_folder_by_identity(tmp_path, monkeypatch):
    answers = tmp_path / "answers"
    shutil.copytree(PROFILES, answers / "inner")
    monkeypatch.setattr(intake, "TRUTH_DIR", answers)
    for profiles in (answers, answers / "inner", tmp_path / "answers" / "." / "inner"):
        with pytest.raises(ValueError, match="truth folder"):
            make_server(tmp_path, profiles=profiles)
    with pytest.raises(ValueError, match="truth folder"):
        build_server(PROFILES, statuses=BOTH, state_dir=answers / "state", rules_path=RULES_PATH)
    make_server(tmp_path, profiles=PROFILES)


def test_cli_defaults_and_bad_statuses(tmp_path):
    args = tax_server.parse_args(["--profiles", str(PROFILES)])
    assert args.statuses == "verified"
    assert args.state_dir == DEFAULT_STATE_DIR
    with pytest.raises(ValueError, match="statuses"):
        tax_server.main(["--profiles", str(PROFILES), "--statuses", "excluded", "--state-dir", str(tmp_path)])
    with pytest.raises(SystemExit):
        tax_server.parse_args([])


# Over MCP.

@pytest.mark.anyio
async def test_in_process_client_sees_refusals_as_tool_errors(tmp_path):
    server = make_server(tmp_path)
    async with Client(server) as client:
        ok = await client.call_tool("start_intake", {"household_id": "H001"})
        assert not ok.is_error
        assert ok.structured_content["intake_id"] == "in_000001"
        assert json.loads(ok.content[0].text)["household_id"] == "H001"
        bad = await client.call_tool("record_fact", {
            "intake_id": "in_000001", "key": "filing_status", "value": "single", "evidence": "prior_year:nope"})
        assert bad.is_error
        assert "last year's summary has no field 'nope'" in bad.content[0].text


@pytest.mark.anyio
@pytest.mark.parametrize("with_pythonpath", [True, False], ids=["pythonpath", "script-fallback"])
async def test_stdio_client_from_an_unrelated_folder(tmp_path, with_pythonpath):
    elsewhere = tmp_path / "empty-cwd"
    elsewhere.mkdir()
    state_dir = tmp_path / "state"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SCRIPT), "--profiles", str(PROFILES), "--statuses", "verified,to_verify",
              "--state-dir", str(state_dir), "--rules", str(RULES_PATH)],
        env={"PYTHONPATH": str(REPO)} if with_pythonpath else None,
        cwd=elsewhere,
    )
    with open(tmp_path / "server-stderr.txt", "w") as errlog:
        with anyio.fail_after(60):
            async with Client(stdio_client(params, errlog=errlog)) as client:
                listed = (await client.list_tools()).tools
                started = await client.call_tool("start_intake", {"household_id": "H001"})
                intake_id = started.structured_content["intake_id"]
                recorded = await client.call_tool("record_fact", {
                    "intake_id": intake_id, "key": "w2_employer_count", "value": 2, "evidence": "two jobs this year"})
                built = await client.call_tool("build_request_list", {"intake_id": intake_id})
    assert [t.name for t in listed] == list(TOOL_NAMES)
    assert [listing_entry(t) for t in listed] == json.loads(SNAPSHOT.read_text())
    assert not started.is_error and started.structured_content["client_name"] == "Avery Placeholder"
    assert re.fullmatch(r"in_[0-9a-f]{6}", intake_id)
    assert not recorded.is_error and recorded.structured_content["value"] == 2
    assert doc_pairs(built.structured_content) == [("R001", 2)]
    assert json.loads((state_dir / f"{intake_id}.json").read_text())["facts"]["w2_employer_count"]["value"] == 2
    assert list(elsewhere.iterdir()) == [], "the server wrote nothing into its working folder"


# The server never opens a path outside its profiles and state folders.

@contextmanager
def watching_opens(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Path]]:
    """Records every path passed to open, io.open, os.open, and Path's open and read and write methods."""
    seen: list[Path] = []

    def note(target: Any) -> None:
        if not isinstance(target, int):
            seen.append(Path(os.fsdecode(os.fspath(target))))

    def wrap(real):
        def opener(target, *args, **kwargs):
            note(target)
            return real(target, *args, **kwargs)
        return opener

    with monkeypatch.context() as m:
        m.setattr(builtins, "open", wrap(builtins.open))
        m.setattr(io, "open", wrap(io.open))
        m.setattr(os, "open", wrap(os.open))
        for name in ("open", "read_text", "read_bytes", "write_text", "write_bytes"):
            m.setattr(Path, name, wrap(getattr(Path, name)))
        yield seen


def _outside(paths: list[Path], roots: tuple[Path, ...]) -> list[Path]:
    return [p for p in paths if not any(p.resolve().is_relative_to(root.resolve()) for root in roots)]


@pytest.fixture
def sandbox(tmp_path: Path) -> dict[str, Path]:
    """data/profiles with links out to data/truth, whose files look like valid profiles."""
    data = tmp_path / "data"
    profiles, truth = data / "profiles", data / "truth"
    shutil.copytree(PROFILES, profiles)
    truth.mkdir()
    for household_id in ("H017", "H090", "H091"):
        canary = {"household_id": household_id, "tax_year": 2026, "client_name": "Canary Truthfile",
                  "prior_year_summary": {}, "true_facts": {"w2_employer_count": 9}}
        (truth / f"{household_id}.json").write_text(json.dumps(canary))
    (profiles / "H090.json").symlink_to(Path("..") / "truth" / "H090.json")
    (profiles / "H091.json").symlink_to(truth / "H091.json")
    (profiles / "H092").symlink_to(truth, target_is_directory=True)
    (profiles / "H093.json").symlink_to(profiles / "H001.json")
    return {"profiles": profiles, "truth": truth, "state": tmp_path / "state", "outside": tmp_path}


HOSTILE_HOUSEHOLD_IDS = [
    "../truth/H017", "H017/../../x", "H092/H090", "/etc/passwd", "H017", "H090", "H091", "H093",
    "..", "H0901", "H090.json", "H090\x00", "data/truth/H017",
]


def test_startup_opens_only_the_facts_and_rules_files(sandbox, monkeypatch):
    with watching_opens(monkeypatch) as seen:
        build_server(sandbox["profiles"], statuses=BOTH, state_dir=sandbox["state"], rules_path=RULES_PATH)
    assert {p.resolve() for p in seen} == {FACTS_PATH.resolve(), RULES_PATH.resolve()}


def test_no_tool_call_opens_a_path_outside_profiles_or_state(sandbox, monkeypatch):
    absolute_truth = str(sandbox["truth"] / "H017.json")
    server = build_server(sandbox["profiles"], statuses=BOTH, state_dir=sandbox["state"],
                          rules_path=RULES_PATH, id_factory=counter_ids())
    with watching_opens(monkeypatch) as seen:
        for household_id in HOSTILE_HOUSEHOLD_IDS + [absolute_truth, str(sandbox["truth"] / "H017")]:
            refused(server, "start_intake", household_id=household_id)
        intake_id = start(server, "H001")
        call(server, "list_fact_keys")
        call(server, "get_document_rules")
        call(server, "get_document_rules", fact_key="w2_employer_count")
        refused(server, "get_document_rules", fact_key="../truth/H017")
        record(server, intake_id, "w2_employer_count", 2, "two jobs")
        record(server, intake_id, "filing_status", "single", "prior_year:filing_status")
        refused(server, "record_fact", intake_id=intake_id, key="filing_status", value="single",
                evidence="prior_year:../truth/H017")
        refused(server, "record_fact", intake_id=intake_id, key="../truth/H017", value=True, evidence="yes")
        call(server, "build_request_list", intake_id=intake_id)
        call(server, "add_custom_request", intake_id=intake_id, document="d", reason="r")
        call(server, "note_for_preparer", intake_id=intake_id, text="t")
        call(server, "submit_for_preparer_review", intake_id=intake_id)
        for bad_intake in ("../truth/H017", absolute_truth, "in_000001/../../truth/H017"):
            refused(server, "build_request_list", intake_id=bad_intake)
            refused(server, "submit_for_preparer_review", intake_id=bad_intake)
    assert sandbox["profiles"] / "H001.json" in seen, "the watcher saw the one profile that should open"
    assert any(p.parent == sandbox["state"] for p in seen), "the watcher saw the state writes"
    assert _outside(seen, (sandbox["profiles"], sandbox["state"])) == []
    assert not any("truth" in p.resolve().parts for p in seen)
    assert sorted(p.name for p in sandbox["state"].iterdir()) == ["in_000001.json"]


@pytest.mark.parametrize("household_id", ["H090", "H091", "H093"])
def test_links_in_the_profiles_folder_are_never_followed(sandbox, household_id):
    server = build_server(sandbox["profiles"], statuses=BOTH, state_dir=sandbox["state"], rules_path=RULES_PATH)
    assert "not a regular file" in refused(server, "start_intake", household_id=household_id)
