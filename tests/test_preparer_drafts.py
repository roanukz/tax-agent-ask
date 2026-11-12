"""Preparer review screen, Block 1: drafts, their flags, the export and the answer key.

The unit tests build drafts from synthetic runs written to tmp_path, checked against
the repo's rules. The tests at the end check the committed drafts in data/drafts and
preparer/app/demo_drafts; the answer key under data/truth is gitignored until scoring,
so its checks skip where it is absent, and the check that a fresh export reproduces
every file skips where eval/runs is absent. No assertion message prints a draft's class.
"""

from __future__ import annotations

import copy
import json
import os
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

import pytest

from eval import export_drafts as ex
from preparer import drafts as dr
from preparer.drafts import DraftError, DraftFolder, Reference, check_draft, compute_flags, draft_digest
from rules import engine

REPO = Path(__file__).resolve().parent.parent
REF = Reference.load()
COMMIT = "449776a" + "0" * 33


# Synthetic runs.


def profile(hid: str, filed: list[dict[str, Any]] | None = None, **summary: Any) -> dict[str, Any]:
    prior = {"tax_year": 2025, "filing_status": "single", "dependents_count": 0, "itemized": False, "documents_on_file": filed or []}
    prior.update(summary)
    return {"household_id": hid, "tax_year": 2026, "client_name": f"Mock Household {hid[1:]}", "prior_year_summary": prior}


def write_run(
    runs: Path,
    profiles: Path,
    hid: str,
    config: str = "A-haiku",
    trial: int = 1,
    *,
    split: str = "test",
    facts: dict[str, tuple[Any, str]],
    filed: list[dict[str, Any]] | None = None,
    missed: list[dict[str, Any]] = (),
    wrong: list[str] = (),
    unnecessary: list[dict[str, Any]] = (),
    fail_reasons: list[str] | None = None,
    notes: list[str] = (),
    custom: list[tuple[str, str]] = (),
    commit: str = COMMIT,
    request_list: list[dict[str, Any]] | None = None,
) -> str:
    runs.mkdir(parents=True, exist_ok=True)
    profiles.mkdir(parents=True, exist_ok=True)
    prof = profile(hid, filed)
    (profiles / f"{hid}.json").write_text(json.dumps(prof))
    recorded = {k: v for k, (v, _) in facts.items()}
    rules = list(REF.rules.values())
    run_id = f"{split}-{hid}-{config}-t{trial}"
    reasons = fail_reasons if fail_reasons is not None else (
        (["missed_documents"] if missed else []) + (["wrong_facts"] if wrong else [])
    )
    summary = {
        "run_id": run_id,
        "household_id": hid,
        "config": config,
        "trial": trial,
        "split": split,
        "status": "pending_review",
        "facts_recorded": {k: {"value": v, "evidence": e} for k, (v, e) in facts.items()},
        "request_list": request_list if request_list is not None else engine.required_documents(recorded, rules, REF.specs),
        "not_asked": dr.computed_not_asked(recorded, REF.specs),
        "custom_requests": [{"document": d, "reason": r, "needs_preparer_review": True} for d, r in custom],
        "notes_for_preparer": [{"text": t} for t in notes],
        "final_message": f"I submitted the draft list for {hid} ({prof['client_name']}) for your review.",
    }
    score = {
        "run_id": run_id,
        "pass": not reasons,
        "required": [],
        "missed": list(missed),
        "unnecessary": list(unnecessary),
        "trap_requests": [],
        "wrong_facts": [{"fact": f, "recorded": None, "true": None, "evidence": ""} for f in wrong],
        "fail_reasons": reasons,
        "prereg_commit": commit,
    }
    (runs / f"{run_id}.summary.json").write_text(json.dumps(summary))
    (runs / f"{run_id}.score.json").write_text(json.dumps(score))
    if request_list is None:
        write_trace(runs, ex.Run(run_id, hid, config, summary, score))
    return run_id


def write_trace(runs: Path, run: ex.Run, change=None) -> None:
    """A trace whose last attempt submitted the draft the summary describes, as the tax server answered it."""
    submitted = ex.draft_fields(run, REF)
    for doc in submitted["documents"]:
        del doc["triggered_by"]
    if change:
        change(submitted)
    events = [
        {"type": "attempt", "n": 1},
        {"type": "tool_call", "id": "t1", "name": "mcp__tax__submit_for_preparer_review", "input": {}},
        {"type": "tool_result", "tool_use_id": "t1", "is_error": False, "text": json.dumps(submitted)},
    ]
    (runs / f"{run.run_id}.trace.json").write_text(json.dumps({"meta": {}, "events": events}))


W2_ONE = {"w2_employer_count": (1, "Just the one job this year")}


def shows_run(runs, profiles, hid, config="A-haiku", trial=1, split="test"):
    """Last year two W-2s, this year one: the dropped flag shows the miss and the wrong count behind it."""
    return write_run(
        runs, profiles, hid, config, trial, split=split, facts=dict(W2_ONE),
        filed=[{"rule_id": "R001", "label": "Form W-2", "count": 2}],
        missed=[{"rule_id": "R001", "count": 1}], wrong=["w2_employer_count"],
    )


def hides_run(runs, profiles, hid, config="A-haiku", trial=1, split="test"):
    """Tuition recorded as no, so the 1098-T is never asked for and nothing on the screen points to it."""
    return write_run(
        runs, profiles, hid, config, trial, split=split,
        facts={**W2_ONE, "tuition_paid": (False, "No school costs")},
        missed=[{"rule_id": "R017", "count": 1}], wrong=["tuition_paid"],
    )


def clean_run(runs, profiles, hid, config="A-haiku", trial=1, split="test", **kw):
    return write_run(runs, profiles, hid, config, trial, split=split, facts=dict(W2_ONE), **kw)


def a_draft(tmp_path, draft_id="d00", **kw) -> dict[str, Any]:
    runs, profiles = tmp_path / "runs", tmp_path / "profiles"
    facts = kw.pop("facts", {
        **W2_ONE,
        "has_mortgage": (True, "same mortgage as last year"),
        "itemized_last_year": (True, "prior_year:itemized"),
        "clean_energy_installed": (True, "We put in solar panels"),
        "clean_energy_install_year": (2026, "This spring"),
    })
    filed = kw.pop("filed", [
        {"rule_id": "R001", "label": "Form W-2", "count": 2},
        {"rule_id": "R014", "label": "Form 1098", "count": 1},
        {"rule_id": "R017", "label": "Form 1098-T", "count": 1},
    ])
    run_id = write_run(runs, profiles, "H061", facts=facts, filed=filed, notes=["Client asked whether the home office counts."],
                       custom=[("Childcare receipts", "Client mentioned daycare")], **kw)
    run = next(r for r in ex.load_runs(runs, "test")[0] if r.run_id == run_id)
    prof = json.loads((profiles / "H061.json").read_text())
    return ex.build_draft(draft_id, run, prof, REF)


def resign(draft: dict[str, Any]) -> dict[str, Any]:
    draft["draft_sha256"] = draft_digest(draft)
    return draft


# The draft schema and its check.


def test_a_built_draft_has_the_schema_keys_and_checks(tmp_path):
    draft = a_draft(tmp_path)
    assert tuple(draft) == dr.DRAFT_KEYS
    check_draft(draft, REF)
    assert draft["client_name"] == "Client 00"
    assert [d["rule_id"] for d in draft["documents"]] == ["R001", "R014"]
    assert draft["documents"][0]["triggered_by"] == [{"fact": "w2_employer_count", "value": 1, "evidence": "Just the one job this year"}]
    assert draft["no_document_needed"][0]["rule_id"] == "T001"
    assert draft["draft_sha256"] == draft_digest(draft)


def test_the_household_and_client_name_are_masked_everywhere(tmp_path):
    draft = a_draft(tmp_path)
    text = json.dumps(draft)
    assert "H061" not in text and "Mock Household" not in text
    assert draft["agent_note"] == "I submitted the draft list for Client 00 for your review."
    assert ex.client_label("demo2") == "Demo Client 2"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Intake for H061 (Mock Household 061) is done.", "Intake for Client 00 is done."),
        ("Intake for Mock Household 061 (H061, two jobs) is done.", "Intake for Client 00 (two jobs) is done."),
        ("H061 (Mock Household 061; married) and Mock Household 061 (H061)", "Client 00 (married) and Client 00"),
        ("Mock Household 061 (H0612) stays", "Client 00 (H0612) stays"),
        ("Client 00 (Client 001) stays", "Client 00 (Client 001) stays"),
    ],
)
def test_a_name_written_beside_its_household_id_masks_to_one_label(text, expected):
    assert ex.masker("H061", "Mock Household 061", "Client 00")(text) == expected


def test_no_committed_draft_repeats_its_label():
    # A doubled label is the export's artifact, not the agent's words.
    def strings(value: Any) -> Iterator[str]:
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for v in value.values():
                yield from strings(v)
        elif isinstance(value, list):
            for v in value:
                yield from strings(v)

    for path in sorted(DRAFTS.glob("*.json")) + sorted(DEMO.glob("*.json")):
        draft = json.loads(path.read_text())
        label = re.escape(draft["client_name"])
        doubled = re.compile(rf"{label}\W{{1,4}}{label}\b")
        assert not any(doubled.search(text) for text in strings(draft)), path.name


def test_dump_is_ascii_and_round_trips(tmp_path):
    draft = a_draft(tmp_path)
    draft["agent_note"] = "one " + chr(0x2014) + " two"
    text = dr.dump(draft)
    assert text.isascii() and text.endswith("}\n") and "\\u2014" in text
    assert json.loads(text) == draft


def test_the_digest_ignores_its_own_field_and_key_order(tmp_path):
    draft = a_draft(tmp_path)
    shuffled = dict(reversed(list(draft.items())))
    shuffled["draft_sha256"] = "x"
    assert draft_digest(shuffled) == draft["draft_sha256"]


def breaks(draft, ref=REF) -> str:
    with pytest.raises(DraftError) as e:
        check_draft(draft, ref)
    return str(e.value)


@pytest.mark.parametrize(
    "change, expected",
    [
        (lambda d: d.update(run_id="test-H061-A-haiku-t1"), "exactly the keys"),
        (lambda d: d.pop("agent_note"), "exactly the keys"),
        (lambda d: d.update(draft_id="d7"), "draft_id must be"),
        (lambda d: d.update(client_name=" "), "client_name must be non-empty"),
        (lambda d: d["documents"][0].update(document="Some other form"), "documents[0].document doesn't match R001"),
        (lambda d: d["documents"][0].update(count=0), "documents[0].count"),
        (lambda d: d["documents"][1].update(count=2), "documents[1].count"),
        (lambda d: d["documents"][1]["triggered_by"].clear(), "triggered_by doesn't match"),
        (lambda d: d["documents"].pop(1), "not the rules engine's list"),
        (lambda d: d["documents"][0].update(rule_id="T001"), "not a request rule"),
        (lambda d: d["no_document_needed"].clear(), "no_document_needed doesn't match"),
        (lambda d: d["no_document_needed"][0].update(rule_id="R001"), "not a trap rule"),
        (lambda d: d["not_asked"].pop(0), "not_asked doesn't match"),
        (lambda d: d["facts_recorded"].update(made_up={"value": 1, "evidence": "x"}), "not a fact key"),
        (lambda d: d["facts_recorded"]["has_mortgage"].update(value="yes"), "is not a stored bool"),
        (lambda d: d["facts_recorded"]["w2_employer_count"].update(value=True), "is not a stored int"),
        (lambda d: d["facts_recorded"]["w2_employer_count"].update(value=11), "is not a stored int"),
        (lambda d: d["facts_recorded"]["itemized_last_year"].update(evidence="prior_year:nothing"), "last year's summary doesn't have"),
        (lambda d: d["custom_requests"][0].update(needs_preparer_review=False), "needs_preparer_review must be true"),
        (lambda d: d["notes_for_preparer"][0].update(text=""), "notes_for_preparer[0].text"),
        (lambda d: d.update(agent_note="Done for H061."), "names a run ID or a household ID"),
        (lambda d: d["notes_for_preparer"][0].update(text="see dev-H002-A-opus-t1"), "names a run ID or a household ID"),
        (lambda d: d["prior_year_summary"]["documents_on_file"].append({"rule_id": "T002", "label": "x", "count": 1}), "not a request rule"),
        (lambda d: d["prior_year_summary"].pop("itemized"), "prior_year_summary must have exactly"),
        (lambda d: d["documents"][0].update(rule_id=["R001"]), "not a request rule"),
        (lambda d: d["prior_year_summary"]["documents_on_file"][0].update(rule_id=["R001"]), "not a request rule"),
        (lambda d: d["no_document_needed"][0].update(rule_id={"T001": 1}), "not a trap rule"),
        (lambda d: d["prior_year_summary"].update(itemized="maybe"), "itemized must be true or false"),
        (lambda d: d["prior_year_summary"].update(filing_status=7), "filing_status must be one of"),
        (lambda d: d["prior_year_summary"].update(dependents_count=[1, 2]), "dependents_count must be a whole number"),
        (lambda d: d["prior_year_summary"].update(dependents_count=11), "dependents_count must be a whole number"),
        (lambda d: d["prior_year_summary"].update(tax_year=None), "the year before tax_year"),
        (lambda d: d["prior_year_summary"].update(tax_year=2024), "the year before tax_year"),
        (lambda d: d.update(tax_year=0), "tax_year must be a whole number of a year"),
        (lambda d: d["documents"][0]["triggered_by"][0].update(value=1.0), "triggered_by doesn't match"),
        (lambda d: d["documents"][0]["triggered_by"][0].update(value=True), "triggered_by doesn't match"),
        (lambda d: d["documents"][0].update(source_url="https://example.com"), "documents[0].source_url doesn't match"),
        (lambda d: d["documents"][0].update(client_reason="Because."), "documents[0].client_reason doesn't match"),
        (lambda d: d["documents"][0].update(source_title="A page"), "documents[0].source_title doesn't match"),
        (lambda d: d["no_document_needed"][0].update(reason="Ask for the form"), "no_document_needed[0] doesn't match T001"),
        (lambda d: d["prior_year_summary"]["documents_on_file"][0].update(count=0), "count must be a whole number of at least 1"),
        (lambda d: d["facts_recorded"]["w2_employer_count"].update(value=1.0), "is not a stored int"),
        (lambda d: d["notes_for_preparer"][0].update(text="Mock Household 061 asked about the office."), "names a run ID or a household ID"),
        (lambda d: d["custom_requests"][0].update(reason="household 061 said so"), "names a run ID or a household ID"),
        (lambda d: d["facts_recorded"]["has_mortgage"].update(evidence=""), "facts_recorded.has_mortgage.evidence must be non-empty"),
        (lambda d: d["custom_requests"][0].update(document=" "), "custom_requests[0].document must be non-empty"),
        (lambda d: d["custom_requests"][0].update(reason=" "), "custom_requests[0].reason must be non-empty"),
        (lambda d: d.update(agent_note=None), "agent_note must be text"),
        (lambda d: d["notes_for_preparer"][0].update(asked_by="client"), "notes_for_preparer[0] must have exactly the keys"),
    ],
)
def test_check_draft_names_each_break(tmp_path, change, expected):
    draft = a_draft(tmp_path)
    change(draft)
    if tuple(draft) == dr.DRAFT_KEYS:
        resign(draft)
    assert expected in breaks(draft)


def test_carried_evidence_may_have_a_space_after_the_prefix_as_the_server_allows(tmp_path):
    draft = a_draft(tmp_path)
    draft["facts_recorded"]["itemized_last_year"]["evidence"] = "prior_year: itemized"
    check_draft(resign(draft), REF)


def test_deeply_nested_json_is_a_read_error_not_a_crash(tmp_path):
    folder = tmp_path / "drafts"
    folder.mkdir()
    (folder / "d01.json").write_text("[" * 200_000)
    with pytest.raises(DraftError, match="could not be read"):
        DraftFolder(folder).load("d01")


def test_a_draft_nested_deep_enough_to_load_but_not_to_check_is_a_read_error(tmp_path):
    folder = tmp_path / "drafts"
    folder.mkdir()
    draft = a_draft(tmp_path / "w", "d02")
    nested: Any = "x"
    for _ in range(1500):
        nested = [nested]
    draft["agent_note"] = nested
    (folder / "d02.json").write_text(json.dumps(draft))
    with pytest.raises(DraftError, match="could not be read"):
        DraftFolder(folder).load("d02")


def test_check_draft_refuses_a_stale_digest(tmp_path):
    draft = a_draft(tmp_path)
    draft["agent_note"] += " More."
    assert "draft_sha256 doesn't match" in breaks(draft)
    draft["draft_sha256"] = "ABC"
    assert "64 lowercase hex" in breaks(draft)


def test_check_draft_refuses_a_non_object():
    assert "must be an object" in breaks([])


def test_result_fields_finds_nested_names():
    assert dr.result_fields({"a": [{"missed": 1}], "config": "A-opus"}) == ["draft.a[0].missed", "draft.config"]
    assert dr.result_fields({"documents": [{"rule_id": "R001"}]}) == []


# Flags.


def test_flags_come_in_order_with_their_ids(tmp_path):
    draft = a_draft(tmp_path)
    flags = compute_flags(draft, REF)
    assert [f["id"] for f in flags] == [
        "dropped:R001", "dropped:R017", "carried:itemized_last_year", "custom:0", "client_question:0", "ended_credit:T001",
    ]
    assert [f["kind"] for f in flags] == ["dropped", "dropped", "carried", "custom", "client_question", "ended_credit"]


def test_a_dropped_flag_shows_both_years_and_the_facts_behind_the_rule(tmp_path):
    flags = {f["id"]: f for f in compute_flags(a_draft(tmp_path), REF)}
    w2 = flags["dropped:R001"]
    assert (w2["label"], w2["last_year_count"], w2["this_year_count"]) == ("Form W-2", 2, 1)
    assert w2["facts"] == [{"fact": "w2_employer_count", "recorded": True, "value": 1, "evidence": "Just the one job this year"}]
    tuition = flags["dropped:R017"]
    assert tuition["this_year_count"] == 0
    assert tuition["facts"] == [{"fact": "tuition_paid", "recorded": False, "value": None, "evidence": None}]


def test_no_dropped_flag_when_the_count_holds_or_rises(tmp_path):
    draft = a_draft(tmp_path, facts={"w2_employer_count": (3, "three jobs")}, filed=[{"rule_id": "R001", "label": "Form W-2", "count": 2}])
    assert not [f for f in compute_flags(draft, REF) if f["kind"] == "dropped"]


def test_filed_counts_add_up_for_a_rule_listed_twice():
    summary = {"documents_on_file": [{"rule_id": "R001", "label": "Form W-2", "count": 1}, {"rule_id": "R001", "label": "W-2", "count": 1}]}
    assert dr.filed_last_year(summary) == {"R001": {"label": "Form W-2", "count": 2}}


def test_addable_rules_leave_out_the_draft_and_every_trap(tmp_path):
    draft = a_draft(tmp_path)
    ids = [r["rule_id"] for r in dr.addable_rules(draft, REF)]
    assert "R001" not in ids and "R014" not in ids and not any(i.startswith("T") for i in ids)
    assert ids == sorted(ids) and len(ids) == 28


def test_condition_facts_keep_order_and_walk_any():
    cond = {"all": [{"fact": "b", "eq": 1}, {"any": [{"fact": "a", "eq": 1}, {"fact": "b", "eq": 2}]}]}
    assert dr.condition_facts(cond) == ["b", "a"]
    assert dr.rule_fact_keys(REF.rules["R001"]) == ["w2_employer_count"]


# The drafts folder.


def test_the_folder_refuses_a_truth_folder(tmp_path):
    with pytest.raises(ValueError, match="truth"):
        DraftFolder(REPO / "data" / "truth")
    (tmp_path / "Truth").mkdir()
    with pytest.raises(ValueError, match="truth"):
        DraftFolder(tmp_path / "Truth")
    with pytest.raises(ValueError, match="not found"):
        DraftFolder(tmp_path / "missing")


def test_the_folder_loads_only_regular_draft_files(tmp_path):
    folder = tmp_path / "drafts"
    folder.mkdir()
    draft = a_draft(tmp_path / "w", "d03")
    (folder / "d03.json").write_text(dr.dump(draft))
    (folder / "notes.json").write_text("{}")
    (folder / "d04.json").symlink_to(folder / "d03.json")
    f = DraftFolder(folder)
    assert f.ids() == ["d03"]
    assert f.load("d03") == draft
    with pytest.raises(DraftError, match="not a regular file"):
        f.load("d04")
    with pytest.raises(DraftError, match="no draft d05"):
        f.load("d05")
    with pytest.raises(DraftError, match="draft_id must be"):
        f.load("../d03")
    (folder / "d06.json").write_text(dr.dump(draft))
    with pytest.raises(DraftError, match="holds draft d03"):
        f.load("d06")
    (folder / "d07.json").write_text("{not json")
    with pytest.raises(DraftError, match="could not be read"):
        f.load("d07")


# The export: loading, classes and the draw.


def test_load_runs_skips_set_aside_and_unscored_runs(tmp_path):
    runs, profiles = tmp_path / "runs", tmp_path / "profiles"
    clean_run(runs, profiles, "H001")
    (runs / "test-H002-A-haiku-t1.unreached.json").write_text("{}")
    clean_run(runs, profiles, "H003", split="dev")
    (runs / "dev-H003-A-haiku-t1.score.json").unlink()
    write_run(runs, profiles, "H004", "A-haiku-weak", split="dev", facts=dict(W2_ONE))
    (runs / "sub").mkdir()
    (runs / "sub" / "test-H005-A-haiku-t1.summary.json").write_text("{}")
    got, unscored = ex.load_runs(runs, "test")
    assert [r.run_id for r in got] == ["test-H001-A-haiku-t1"]
    assert ex.load_runs(runs, "dev") == ([], ["dev-H003-A-haiku-t1", "dev-H004-A-haiku-weak-t1"])


def test_load_runs_refuses_an_unsubmitted_draft(tmp_path):
    runs, profiles = tmp_path / "runs", tmp_path / "profiles"
    run_id = clean_run(runs, profiles, "H001")
    path = runs / f"{run_id}.summary.json"
    summary = json.loads(path.read_text())
    path.write_text(json.dumps({**summary, "status": "open"}))
    with pytest.raises(ex.ExportError, match="never submitted"):
        ex.load_runs(runs, "test")


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("run_id", "test-H999-A-haiku-t1", "names another run"),
        ("split", "dev", "not an A config run on the test split"),
        ("config", "B1", "not an A config run on the test split"),
    ],
)
def test_load_runs_refuses_a_summary_that_does_not_fit_its_file(tmp_path, field, value, expected):
    runs, profiles = tmp_path / "runs", tmp_path / "profiles"
    run_id = clean_run(runs, profiles, "H001")
    path = runs / f"{run_id}.summary.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), field: value}))
    with pytest.raises(ex.ExportError, match=expected):
        ex.load_runs(runs, "test")


def test_build_draft_refuses_not_asked_the_engine_would_not_give(tmp_path):
    runs, profiles = tmp_path / "runs", tmp_path / "profiles"
    run_id = clean_run(runs, profiles, "H001")
    path = runs / f"{run_id}.summary.json"
    summary = json.loads(path.read_text())
    path.write_text(json.dumps({**summary, "not_asked": summary["not_asked"][1:]}))
    run = ex.load_runs(runs, "test")[0][0]
    with pytest.raises(ex.ExportError, match="not_asked differs"):
        ex.build_draft("d00", run, json.loads((profiles / "H001.json").read_text()), REF)


def test_build_draft_refuses_a_list_the_engine_would_not_give(tmp_path):
    runs, profiles = tmp_path / "runs", tmp_path / "profiles"
    write_run(runs, profiles, "H001", facts=dict(W2_ONE), request_list=[{"rule_id": "R001", "count": 2}])
    run = ex.load_runs(runs, "test")[0][0]
    with pytest.raises(ex.ExportError, match="differs from the rules engine"):
        ex.build_draft("d00", run, json.loads((profiles / "H001.json").read_text()), REF)


def test_the_trace_check_refuses_a_draft_the_agent_did_not_submit(tmp_path):
    runs, profiles = tmp_path / "runs", tmp_path / "profiles"
    run_id = clean_run(runs, profiles, "H001")
    run = ex.load_runs(runs, "test")[0][0]
    ex.check_submitted(run, REF, ex.submitted_draft(runs, run_id))
    write_trace(runs, run, change=lambda d: d["documents"][0].update(document="Old wording"))
    with pytest.raises(ex.ExportError, match="rebuilt documents differs"):
        ex.check_submitted(run, REF, ex.submitted_draft(runs, run_id))
    write_trace(runs, run, change=lambda d: d["notes_for_preparer"].append({"text": "extra"}))
    with pytest.raises(ex.ExportError, match="rebuilt notes_for_preparer differs"):
        ex.check_submitted(run, REF, ex.submitted_draft(runs, run_id))
    (runs / f"{run_id}.trace.json").unlink()
    with pytest.raises(ex.ExportError, match="no submitted draft"):
        ex.check_submitted(run, REF, ex.submitted_draft(runs, run_id))


def classes(tmp_path) -> dict[str, str]:
    runs, profiles = tmp_path / "runs", tmp_path / "profiles"
    shows_run(runs, profiles, "H001")
    hides_run(runs, profiles, "H002")
    clean_run(runs, profiles, "H003")
    clean_run(runs, profiles, "H004", unnecessary=[{"kind": "rule", "rule_id": "R016", "count": 1}])
    clean_run(runs, profiles, "H005", fail_reasons=["advice_not_routed"])
    # A shown miss plus a hidden wrong fact: some of each.
    write_run(runs, profiles, "H006", facts={**W2_ONE, "tuition_paid": (False, "no")},
              filed=[{"rule_id": "R001", "label": "Form W-2", "count": 2}],
              missed=[{"rule_id": "R001", "count": 1}, {"rule_id": "R017", "count": 1}], wrong=["w2_employer_count", "tuition_paid"])
    # A dropped R001 that wasn't missed doesn't show a wrong fact it reads: the miss is the 1098-T, which nothing shows.
    write_run(runs, profiles, "H008", facts={**W2_ONE, "tuition_paid": (False, "no")},
              filed=[{"rule_id": "R001", "label": "Form W-2", "count": 2}],
              missed=[{"rule_id": "R017", "count": 1}], wrong=["w2_employer_count"])
    # A pass that requested a trap is not clean.
    run_id = clean_run(runs, profiles, "H009")
    score_path = runs / f"{run_id}.score.json"
    score_path.write_text(json.dumps({**json.loads(score_path.read_text()), "trap_requests": ["T001"]}))
    # A wrong fact recorded with prior_year evidence shows through the carried flag.
    write_run(runs, profiles, "H007", facts={**W2_ONE, "itemized_last_year": (True, "prior_year:itemized")}, wrong=["itemized_last_year"])
    pool, tally = ex.candidates(ex.load_runs(runs, "test")[0], profiles, REF)
    assert tally == {"shows": 2, "hides": 2, "clean": 1, "passed_with_extras": 2, "failed_without_problem": 1, "mixed": 1}
    return {c.run.household_id: c for c in pool}


def test_each_run_gets_its_class(tmp_path):
    pool = classes(tmp_path)
    assert {h: c.cls for h, c in pool.items()} == {"H001": "shows", "H002": "hides", "H003": "clean", "H007": "shows", "H008": "hides"}
    assert [p["shows_because"] for p in pool["H008"].problems] == [None, None]
    assert pool["H001"].problems == [
        {"kind": "missed", "rule_id": "R001", "count": 1, "shows_because": "dropped:R001"},
        {"kind": "wrong_fact", "fact": "w2_employer_count", "shows_because": "dropped:R001"},
    ]
    assert pool["H002"].problems[0]["shows_because"] is None
    assert pool["H007"].problems == [{"kind": "wrong_fact", "fact": "itemized_last_year", "shows_because": "carried:itemized_last_year"}]


def test_draft_class_rule():
    def p(*shown):
        return [{"shows_because": s} for s in shown]

    assert ex.draft_class([]) == "clean"
    assert ex.draft_class(p("dropped:R001")) == "shows"
    assert ex.draft_class(p(None, None)) == "hides"
    assert ex.draft_class(p("dropped:R001", None)) == "mixed"


def big_pool(tmp_path, shows=3, hides=6, clean=8):
    runs, profiles = tmp_path / "runs", tmp_path / "profiles"
    n = 0
    configs = ["A-haiku", "A-sonnet", "A-opus", "A-split"]
    for make, count in ((shows_run, shows), (hides_run, hides), (clean_run, clean)):
        for _ in range(count):
            n += 1
            for trial, config in enumerate(configs[: 1 + n % 4], start=1):
                make(runs, profiles, f"H{n:03d}", config, trial)
    pool, _ = ex.candidates(ex.load_runs(runs, "test")[0], profiles, REF)
    return pool


def test_the_draw_aims_for_the_show_target_and_fills_with_hides(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "PROBLEM_DRAFTS", 6)
    monkeypatch.setattr(ex, "CLEAN_DRAFTS", 4)
    monkeypatch.setattr(ex, "SHOW_TARGET", 3)
    pool = big_pool(tmp_path, shows=2, hides=6, clean=6)
    chosen = ex.draw_review_set(pool, random.Random(11))
    assert Counter(c.cls for c in chosen) == {"shows": 2, "hides": 4, "clean": 4}
    assert len({c.run.household_id for c in chosen}) == 10
    again = ex.draw_review_set(pool, random.Random(11))
    assert [c.run.run_id for c in again] == [c.run.run_id for c in chosen]


def test_the_draw_fills_with_shows_when_hides_run_short(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "PROBLEM_DRAFTS", 6)
    monkeypatch.setattr(ex, "CLEAN_DRAFTS", 2)
    monkeypatch.setattr(ex, "SHOW_TARGET", 3)
    pool = big_pool(tmp_path, shows=5, hides=1, clean=2)
    assert Counter(c.cls for c in ex.draw_review_set(pool, random.Random(11))) == {"shows": 5, "hides": 1, "clean": 2}


def test_the_draw_refuses_when_the_run_is_short(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "PROBLEM_DRAFTS", 6)
    monkeypatch.setattr(ex, "CLEAN_DRAFTS", 4)
    pool = big_pool(tmp_path, shows=1, hides=2, clean=6)
    with pytest.raises(ex.ExportError, match="1 show, 2 hide and 4 clean"):
        ex.draw_review_set(pool, random.Random(11))


def test_the_draw_mixes_configs_within_a_class(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "PROBLEM_DRAFTS", 2)
    monkeypatch.setattr(ex, "CLEAN_DRAFTS", 8)
    runs, profiles = tmp_path / "runs", tmp_path / "profiles"
    for n in range(1, 3):
        shows_run(runs, profiles, f"H{n:03d}")
    for n in range(3, 11):
        for trial, config in enumerate(["A-haiku", "A-sonnet", "A-opus", "A-split"], start=1):
            clean_run(runs, profiles, f"H{n:03d}", config, trial)
    pool, _ = ex.candidates(ex.load_runs(runs, "test")[0], profiles, REF)
    configs = Counter(c.run.config for c in ex.draw_review_set(pool, random.Random(11)))
    assert set(configs) == {"A-haiku", "A-sonnet", "A-opus", "A-split"}
    assert max(configs.values()) - min(configs.values()) <= 1


def demo_pool(runs, profiles):
    shows_run(runs, profiles, "H090", split="dev")
    write_run(runs, profiles, "H091", split="dev", facts=dict(W2_ONE), notes=["Asked about the home office."])
    clean_run(runs, profiles, "H092", split="dev")


def export_fixture(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(ex, "PROBLEM_DRAFTS", 2)
    monkeypatch.setattr(ex, "CLEAN_DRAFTS", 2)
    monkeypatch.setattr(ex, "SHOW_TARGET", 1)
    runs, profiles = tmp_path / "runs", tmp_path / "profiles"
    shows_run(runs, profiles, "H001", "A-sonnet")
    hides_run(runs, profiles, "H002", "A-opus")
    clean_run(runs, profiles, "H003", "A-split")
    clean_run(runs, profiles, "H004", "A-haiku")
    demo_pool(runs, profiles)
    out = tmp_path / "out"
    paths = dict(runs_dir=runs, profiles_dir=profiles, drafts_dir=out / "drafts", reviews_dir=out / "reviews",
                 demo_dir=out / "demo", key_path=out / "truth" / "review_key.json")
    paths.update(kw)
    return paths


def test_export_writes_drafts_demo_drafts_and_the_key(tmp_path, monkeypatch):
    paths = export_fixture(tmp_path, monkeypatch)
    result = ex.export(**paths)
    assert result["drafts"] == 4
    assert {k: v[0] for k, v in result["demo"].items()} == {"demo1": "dropped", "demo2": "client_question", "demo3": "clean"}
    folder = DraftFolder(paths["drafts_dir"])
    assert folder.ids() == ["d01", "d02", "d03", "d04"]
    drafts = folder.load_all()
    assert {d["client_name"] for d in drafts.values()} == {"Client 01", "Client 02", "Client 03", "Client 04"}
    demo = DraftFolder(paths["demo_dir"]).load_all()
    assert [f["kind"] for f in compute_flags(demo["demo1"], REF)][:1] == ["dropped"]
    key = json.loads(paths["key_path"].read_text())
    assert [e["draft_id"] for e in key["drafts"]] == ["d01", "d02", "d03", "d04"]
    assert key["drawn"] == {"clean": 2, "hides": 1, "shows": 1}
    assert key["prereg_commit"] == COMMIT and key["seed"] == 11
    for entry in key["drafts"]:
        assert entry["problem_class"] in ("shows", "hides", "clean")
        assert (entry["problem_class"] == "clean") == (not entry["missed"] and not entry["wrong_facts"])
    # The same inputs give the same bytes.
    before = {p.name: p.read_bytes() for p in paths["drafts_dir"].iterdir()}
    ex.export(**paths)
    assert {p.name: p.read_bytes() for p in paths["drafts_dir"].iterdir()} == before


def test_export_replaces_old_drafts_but_keeps_other_files(tmp_path, monkeypatch):
    paths = export_fixture(tmp_path, monkeypatch)
    paths["drafts_dir"].mkdir(parents=True)
    (paths["drafts_dir"] / "d24.json").write_text("{}")
    (paths["drafts_dir"] / "keep.txt").write_text("x")
    ex.export(**paths)
    assert sorted(p.name for p in paths["drafts_dir"].iterdir()) == ["d01.json", "d02.json", "d03.json", "d04.json", "keep.txt"]


def test_export_refuses_once_a_review_exists(tmp_path, monkeypatch):
    paths = export_fixture(tmp_path, monkeypatch)
    paths["reviews_dir"].mkdir(parents=True)
    (paths["reviews_dir"] / "d01.json").write_text("{}")
    with pytest.raises(ex.ExportError, match="already holds 1 review record"):
        ex.export(**paths)
    assert not paths["drafts_dir"].exists() and not paths["key_path"].exists()
    assert ex.main(["--runs", str(paths["runs_dir"]), "--reviews", str(paths["reviews_dir"]), "--drafts", str(tmp_path / "x"),
                    "--demo", str(tmp_path / "y"), "--key", str(tmp_path / "k.json"), "--profiles", str(paths["profiles_dir"])]) == 1


def test_export_refuses_once_any_draft_has_been_opened(tmp_path, monkeypatch):
    paths = export_fixture(tmp_path, monkeypatch)
    paths["reviews_dir"].mkdir(parents=True)
    (paths["reviews_dir"] / "openings.jsonl").write_text('{"draft_id": "d01"}\n')
    with pytest.raises(ex.ExportError, match="already holds 1 review record"):
        ex.export(**paths)


def test_export_refuses_runs_from_two_prereg_commits(tmp_path, monkeypatch):
    paths = export_fixture(tmp_path, monkeypatch)
    clean_run(paths["runs_dir"], paths["profiles_dir"], "H005", commit="1234567" + "0" * 33)
    with pytest.raises(ex.ExportError, match="one prereg commit"):
        ex.export(**paths)


def test_export_prints_counts_and_never_a_class(tmp_path, monkeypatch, capsys):
    paths = export_fixture(tmp_path, monkeypatch)
    args = ["--runs", paths["runs_dir"], "--profiles", paths["profiles_dir"], "--drafts", paths["drafts_dir"],
            "--reviews", paths["reviews_dir"], "--demo", paths["demo_dir"], "--key", paths["key_path"]]
    assert ex.main([str(a) for a in args]) == 0
    out = capsys.readouterr().out
    assert "wrote 4 drafts" in out and "(not shown)" in out
    for word in ("shows", "hides", "test-H00", "H001", "H002"):
        assert word not in out


# The committed drafts.

DRAFTS = REPO / "data" / "drafts"
DEMO = REPO / "preparer" / "app" / "demo_drafts"
KEY = REPO / "data" / "truth" / "review_key.json"
REVIEW_IDS = [f"d{i:02d}" for i in range(1, 25)]


def test_the_24_committed_drafts_validate_and_hold_no_result():
    folder = DraftFolder(DRAFTS)
    assert folder.ids() == REVIEW_IDS
    assert sorted(p.name for p in DRAFTS.iterdir()) == [f"{i}.json" for i in REVIEW_IDS]
    for draft_id, draft in folder.load_all().items():
        assert dr.result_fields(draft) == [] and dr.identifying_text(draft) == [], draft_id
        assert draft["client_name"] == f"Client {draft_id[1:]}"
        text = (DRAFTS / f"{draft_id}.json").read_text()
        assert text == dr.dump(draft) and text.isascii(), draft_id


def test_the_three_demo_drafts_validate_and_show_their_flags():
    demo = DraftFolder(DEMO).load_all()
    assert sorted(demo) == ["demo1", "demo2", "demo3"]
    kinds = {i: {f["kind"] for f in compute_flags(d, REF)} for i, d in demo.items()}
    assert "dropped" in kinds["demo1"]
    assert "client_question" in kinds["demo2"] and "dropped" not in kinds["demo2"]
    assert not kinds["demo3"] & {"dropped", "client_question", "custom"}


def _key_failures() -> list[str]:
    """The names of the key checks that fail. The key is read only here, so no test frame ever holds it."""
    key = json.loads(KEY.read_text())
    entries = key["drafts"]
    failures = []
    if [e["draft_id"] for e in entries] != REVIEW_IDS:
        failures.append("ids")
    if key["drawn"] != {"clean": 12, "hides": 8, "shows": 4} or key["seed"] != 11:
        failures.append("counts")
    if len({e["household_id"] for e in entries}) != 24:
        failures.append("households")

    def consistent(e: dict[str, Any]) -> bool:
        problem = bool(e["missed"] or e["wrong_facts"])
        if e["problem_class"] not in (("shows", "hides") if problem else ("clean",)):
            return False
        shown = [p["shows_because"] is not None for p in e["problems"]]
        return all(shown) if e["problem_class"] == "shows" else not any(shown)

    if any(not consistent(e) for e in entries):
        failures.append("inconsistent")

    # Each problem comes from the key's own score fields, and each flag that shows one is on the committed draft.
    folder = DraftFolder(DRAFTS)
    unmatched = 0
    for e in entries:
        flags = {f["id"] for f in compute_flags(folder.load(e["draft_id"]), REF)}
        if len(e["problems"]) != len(e["missed"]) + len(e["wrong_facts"]):
            unmatched += 1
        elif any(p["shows_because"] is not None and p["shows_because"] not in flags for p in e["problems"]):
            unmatched += 1
        elif sorted({p["shows_because"] for p in e["problems"] if p["shows_because"]}) != e["shows_because"]:
            unmatched += 1
    if unmatched:
        failures.append("unmatched")
    return failures


@pytest.mark.skipif(not KEY.exists(), reason="the answer key is gitignored until scoring and is absent here")
def test_the_key_covers_every_draft_and_classes_every_problem():
    # A failure names the checks that failed, never a draft or its class, even under pytest -l.
    assert _key_failures() == []


def test_the_key_is_gitignored():
    ignore = (REPO / ".gitignore").read_text().splitlines()
    assert {"data/truth/review_key.json", "data/truth/review_noted.json", "data/truth/review_inferable.json",
            "data/truth/review_window.json", "preparer/app/demo_reviews/"} <= set(ignore)


@pytest.mark.skipif(not (REPO / "eval" / "runs").is_dir(), reason="eval/runs is gitignored and absent here")
def test_a_fresh_export_reproduces_every_committed_file(tmp_path):
    out = tmp_path / "out"
    ex.export(drafts_dir=out / "drafts", reviews_dir=out / "reviews", demo_dir=out / "demo", key_path=out / "key.json")
    for folder, fresh in ((DRAFTS, out / "drafts"), (DEMO, out / "demo")):
        differ = sorted(p.name for p in fresh.iterdir() if not (folder / p.name).exists() or (folder / p.name).read_bytes() != p.read_bytes())
        assert differ == [] and len(list(folder.iterdir())) == len(list(fresh.iterdir()))
    if KEY.exists():
        same_key = KEY.read_bytes() == (out / "key.json").read_bytes()
        assert same_key, "the answer key differs from a fresh export (its contents are not printed)"
