"""Preparer review screen, Block 2: decisions and their check, the approved list, tokens and review records.

The drafts are the committed demo drafts in preparer/app/demo_drafts, copied where a test changes one.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from preparer import reviews as rv
from preparer.drafts import DraftFolder, Reference, compute_flags
from preparer.reviews import ReviewError, ReviewFolder, Tokens, check_decisions

REPO = Path(__file__).resolve().parent.parent
REF = Reference.load()
DEMO = DraftFolder(REPO / "preparer" / "app" / "demo_drafts", REF).load_all()
T0 = datetime(2026, 11, 8, 15, 0, 0, tzinfo=timezone.utc)


def with_custom(draft: dict[str, Any]) -> dict[str, Any]:
    """A copy of a draft with two custom requests, for the decisions on them (not re-signed; not written)."""
    d = json.loads(json.dumps(draft))
    d["custom_requests"] = [
        {"document": "Daycare receipts", "reason": "Client mentioned daycare", "needs_preparer_review": True},
        {"document": "Letter from the school", "reason": "Client asked", "needs_preparer_review": True},
    ]
    return d


DRAFT = with_custom(DEMO["demo1"])
FLAGS = compute_flags(DRAFT, REF)
DOC_IDS = [d["rule_id"] for d in DRAFT["documents"]]
COUNTED = next(r for r in DOC_IDS if REF.rules[r].count_from)
PLAIN = next(r for r in DOC_IDS if not REF.rules[r].count_from)
ADDABLE = next(r.id for r in REF.request_rules if r.id not in DOC_IDS and not r.count_from)


def full(**changes: Any) -> dict[str, Any]:
    decisions = {
        "documents": [{"rule_id": r, "action": "keep"} for r in DOC_IDS],
        "custom_requests": [{"index": 0, "action": "keep"}, {"index": 1, "action": "remove", "reason": "duplicate"}],
        "added": [],
        "follow_ups": [],
        "flags_checked": [f["id"] for f in FLAGS],
    }
    decisions.update(changes)
    return decisions


def refused(decisions: Any, final: bool = True) -> str:
    with pytest.raises(ReviewError) as e:
        check_decisions(decisions, DRAFT, FLAGS, REF, final=final)
    return str(e.value)


def test_the_demo_draft_has_a_counted_rule_and_flags():
    assert COUNTED == "R001" and FLAGS and any(f["kind"] == "dropped" for f in FLAGS)


def test_complete_decisions_pass_and_come_back_unchanged():
    decisions = full(added=[{"rule_id": ADDABLE}], follow_ups=[{"fact": "w2_employer_count"}])
    assert check_decisions(decisions, DRAFT, FLAGS, REF, final=True) == decisions


def test_a_save_may_be_partial_but_an_approval_may_not():
    partial = full(documents=[], custom_requests=[], flags_checked=[])
    assert check_decisions(partial, DRAFT, FLAGS, REF, final=False) == partial
    message = refused(partial)
    assert message.startswith("approval needs a decision on " + ", ".join(DOC_IDS))
    assert "a decision on custom request 0, 1" in message and "a check on " + FLAGS[0]["id"] in message


@pytest.mark.parametrize(
    "decisions, expected",
    [
        ([], "decisions must be an object"),
        ({"documents": []}, "missing ['custom_requests', 'added', 'follow_ups', 'flags_checked']"),
        (lambda: {**full(), "notes": "free text"}, "unexpected ['notes']"),
        (lambda: full(documents="all"), "decisions.documents must be a list"),
        (lambda: full(documents=[{"rule_id": PLAIN, "action": "edit"}]), "action is keep or remove"),
        (lambda: full(documents=[{"rule_id": PLAIN, "action": "remove"}]), "missing ['reason']"),
        (lambda: full(documents=[{"rule_id": PLAIN, "action": "remove", "reason": "too costly"}]), "the reason must be one of"),
        (lambda: full(documents=[{"rule_id": PLAIN, "action": "keep", "reason": "duplicate"}]), "unexpected ['reason']"),
        (lambda: full(documents=[{"rule_id": PLAIN, "action": "keep", "count": 2}]), "has no count"),
        (lambda: full(documents=[{"rule_id": COUNTED, "action": "keep", "count": 0}]), "from 1 to 10"),
        (lambda: full(documents=[{"rule_id": COUNTED, "action": "keep", "count": 11}]), "from 1 to 10"),
        (lambda: full(documents=[{"rule_id": COUNTED, "action": "keep", "count": True}]), "from 1 to 10"),
        (lambda: full(documents=[{"rule_id": COUNTED, "action": "keep", "count": "2"}]), "from 1 to 10"),
        (lambda: full(documents=[{"rule_id": "R099", "action": "keep"}]), "not a document on this draft"),
        (lambda: full(documents=[{"rule_id": ["R001"], "action": "keep"}]), "not a document on this draft"),
        (lambda: full(documents=[{"rule_id": PLAIN, "action": "keep"}, {"rule_id": PLAIN, "action": "keep"}]), "more than one decision"),
        (lambda: full(custom_requests=[{"index": 2, "action": "keep"}]), "one of this draft's 2 custom requests"),
        (lambda: full(custom_requests=[{"index": False, "action": "keep"}]), "one of this draft's 2 custom requests"),
        (lambda: full(custom_requests=[{"index": 0, "action": "keep"}, {"index": 0, "action": "keep"}]), "more than one decision"),
        (lambda: full(custom_requests=[{"index": 0, "action": "remove", "reason": "nope"}]), "the reason must be one of"),
        (lambda: full(added=[{"rule_id": PLAIN}]), "not a request rule missing from this draft"),
        (lambda: full(added=[{"rule_id": "T001"}]), "not a request rule missing from this draft"),
        (lambda: full(added=[{"rule_id": ADDABLE}, {"rule_id": ADDABLE}]), "added more than once"),
        (lambda: full(added=[{"rule_id": ADDABLE, "count": 2}]), "has no count"),
        (lambda: full(added=[{"rule_id": ADDABLE, "why": "x"}]), "unexpected ['why']"),
        (lambda: full(follow_ups=[{"fact": "favorite_color"}]), "not a fact key"),
        (lambda: full(follow_ups=[{"fact": {"x": 1}}]), "not a fact key"),
        (lambda: full(follow_ups=[{"fact": "k1_count"}, {"fact": "k1_count"}]), "asked more than once"),
        (lambda: full(follow_ups=[{"fact": "k1_count", "question": "Any K-1s?"}]), "unexpected ['question']"),
        (lambda: full(flags_checked=["dropped:R999"]), "not a flag on this draft"),
        (lambda: full(flags_checked=[["x"]]), "not a flag on this draft"),
        (lambda: full(flags_checked=[FLAGS[0]["id"], FLAGS[0]["id"]]), "more than once"),
    ],
)
def test_each_bad_decision_is_refused_with_a_plain_message(decisions, expected):
    decisions = decisions() if callable(decisions) else decisions
    assert expected in refused(decisions, final=False)


def test_a_counted_rule_can_be_added_with_a_count():
    draft = DEMO["demo2"]
    flags = compute_flags(draft, REF)
    assert "R020" not in [d["rule_id"] for d in draft["documents"]]
    decisions = {
        "documents": [{"rule_id": d["rule_id"], "action": "keep"} for d in draft["documents"]],
        "custom_requests": [], "added": [{"rule_id": "R020", "count": 3}], "follow_ups": [],
        "flags_checked": [f["id"] for f in flags],
    }
    assert check_decisions(decisions, draft, flags, REF, final=True) == decisions


def test_the_approved_list_keeps_counts_adds_rules_and_drops_removals():
    decisions = full(
        documents=[{"rule_id": COUNTED, "action": "keep", "count": 3}]
        + [{"rule_id": r, "action": "remove", "reason": "not_needed"} for r in DOC_IDS if r != COUNTED],
        added=[{"rule_id": ADDABLE}],
        follow_ups=[{"fact": "w2_employer_count"}],
    )
    listed = rv.approved_list(DRAFT, decisions, REF)
    assert listed == sorted(
        [{"rule_id": COUNTED, "document": REF.rules[COUNTED].document, "count": 3},
         {"rule_id": ADDABLE, "document": REF.rules[ADDABLE].document, "count": 1}],
        key=lambda d: d["rule_id"],
    ) + [{"rule_id": None, "document": "Daycare receipts", "count": 1}]
    assert rv.change_summary(DRAFT, decisions) == {"removed": len(DOC_IDS) - 1 + 1, "added": 1, "count_changed": 1, "follow_ups": 1}
    assert rv.follow_up_questions(decisions, REF) == [
        {"fact": "w2_employer_count", "question": REF.specs["w2_employer_count"].plain_question_hint}
    ]


def test_keeping_a_count_unchanged_is_not_a_change():
    count = next(d["count"] for d in DRAFT["documents"] if d["rule_id"] == COUNTED)
    decisions = full(documents=[{"rule_id": COUNTED, "action": "keep", "count": count}])
    assert rv.change_summary(DRAFT, decisions)["count_changed"] == 0


@pytest.mark.parametrize(
    "host, ok",
    [
        (None, True), ({"name": "Claude", "version": "4.2"}, True), ({"name": "Claude", "version": ""}, True),
        ({"name": "", "version": "1"}, False), ({"name": "x" * 201, "version": "1"}, False),
        ({"name": "Claude"}, False), ({"name": "Claude", "version": 4}, False), ("Claude", False),
    ],
)
def test_host_info_is_checked(host, ok):
    if ok:
        assert rv.checked_host(host) == host
    else:
        with pytest.raises(ReviewError):
            rv.checked_host(host)


# Tokens.


def test_reopening_retires_the_old_token():
    ticks = iter(range(100))
    tokens = Tokens(clock=lambda: T0, factory=lambda: f"tok{next(ticks)}")
    first = tokens.mint("d01", "a" * 64)
    assert tokens.current("d01", "tok0") == first
    second = tokens.mint("d01", "a" * 64)
    assert tokens.current("d01", "tok0") is None and tokens.current("d01", "tok1") == second
    assert tokens.current("d02", "tok1") is None
    assert tokens.current("d01", None) is None and tokens.current("d01", 1) is None


def test_default_tokens_are_long_and_fresh():
    tokens = Tokens()
    a, b = tokens.mint("d01", "x").token, tokens.mint("d02", "x").token
    assert a != b and len(a) >= 32


# The reviews folder.


def test_the_reviews_folder_refuses_truth_folders(tmp_path):
    with pytest.raises(ValueError, match="truth"):
        ReviewFolder(REPO / "data" / "truth" / "reviews")
    with pytest.raises(ValueError, match="truth"):
        ReviewFolder(tmp_path / "TRUTH" / "r")
    link = tmp_path / "innocent"
    link.symlink_to(REPO / "data" / "truth")
    with pytest.raises(ValueError, match="truth"):
        ReviewFolder(link)


def test_records_round_trip_and_replace_atomically(tmp_path):
    folder = ReviewFolder(tmp_path / "reviews")
    assert folder.read("d01") is None and folder.ids() == []
    record = {"draft_id": "d01", "status": "in_review", "note": chr(0x2014)}
    folder.write(record)
    assert folder.read("d01") == record and folder.ids() == ["d01"]
    text = (tmp_path / "reviews" / "d01.json").read_text()
    assert text.isascii() and text.endswith("}\n")
    assert sorted(os.listdir(tmp_path / "reviews")) == ["d01.json"]


def test_a_linked_or_malformed_record_is_refused(tmp_path):
    folder = ReviewFolder(tmp_path / "reviews")
    (tmp_path / "elsewhere.json").write_text(json.dumps({"draft_id": "d02", "status": "approved"}))
    (tmp_path / "reviews" / "d02.json").symlink_to(tmp_path / "elsewhere.json")
    with pytest.raises(ReviewError, match="not a regular file"):
        folder.read("d02")
    (tmp_path / "reviews" / "d03.json").write_text(json.dumps({"draft_id": "d04", "status": "approved"}))
    with pytest.raises(ReviewError, match="malformed"):
        folder.read("d03")
    (tmp_path / "reviews" / "d05.json").write_text("{")
    with pytest.raises(ReviewError, match="could not be read"):
        folder.read("d05")
    with pytest.raises(ReviewError, match="draft_id must be"):
        folder.read("../d01")


def test_writing_never_follows_a_link_left_at_the_temporary_name(tmp_path):
    folder = ReviewFolder(tmp_path / "reviews")
    target = tmp_path / "target.txt"
    target.write_text("untouched")
    (tmp_path / "reviews" / ".d01.json.tmp").symlink_to(target)
    folder.write({"draft_id": "d01", "status": "in_review"})
    assert target.read_text() == "untouched" and folder.read("d01")["status"] == "in_review"


# Records.


def opened_at(minutes: int) -> rv.OpenReview:
    return rv.OpenReview("demo1", "tok", DRAFT["draft_sha256"], T0 + timedelta(minutes=minutes))


def record(**kw: Any) -> dict[str, Any]:
    args = dict(draft=DRAFT, decisions=full(), opened=opened_at(0), existing=None, now=T0 + timedelta(seconds=90),
                approve=False, host={"name": "Claude", "version": "1"}, client={"name": "claude-ai", "version": "0.1"}, ref=REF)
    args.update(kw)
    return rv.build_record(**args)


def test_a_save_then_an_approval_keep_the_server_times():
    saved = record()
    assert tuple(saved) == rv.RECORD_KEYS
    assert saved["status"] == "in_review" and saved["approved_list"] is None and saved["changes"] is None
    assert saved["opened_at"] == "2026-11-08T15:00:00Z" and saved["saved_at"] == ["2026-11-08T15:01:30Z"]
    assert saved["seconds_on_screen"] == 90 and saved["app_version"] == rv.APP_VERSION
    approved = record(existing=saved, now=T0 + timedelta(seconds=150), approve=True, host=None)
    assert approved["status"] == "approved" and approved["approved_at"] == "2026-11-08T15:02:30Z"
    assert approved["saved_at"] == saved["saved_at"] and approved["seconds_on_screen"] == 150
    assert approved["host"] == {"name": "Claude", "version": "1"}
    assert approved["approved_list"] == rv.approved_list(DRAFT, full(), REF)
    assert approved["changes"] == {"removed": 1, "added": 0, "count_changed": 0, "follow_ups": 0}


def test_screen_time_adds_up_across_openings():
    first = record()
    later = record(existing=first, opened=opened_at(60), now=T0 + timedelta(minutes=60, seconds=30), approve=True)
    assert [s["opened_at"] for s in later["screen_time"]] == ["2026-11-08T15:00:00Z", "2026-11-08T16:00:00Z"]
    assert later["seconds_on_screen"] == 90 + 30 and later["opened_at"] == "2026-11-08T15:00:00Z"
