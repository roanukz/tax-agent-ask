"""Preparer review screen, Block 5 scoring: the catch check's rules, its refusals and its report, on made-up reviews.

Every review here is synthetic, built from the three demo drafts with a made-up key; no test reads the real
answer key or Roanuk's review records.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from eval import review_catch as rc
from preparer import reviews as rv
from preparer.drafts import DraftFolder, Reference, compute_flags, dump

REPO = Path(__file__).resolve().parent.parent
REF = Reference.load()
DEMO = REPO / "preparer" / "app" / "demo_drafts"
DRAFTS = DraftFolder(DEMO, REF).load_all()
T0 = datetime(2026, 11, 8, 15, 0, 0, tzinfo=timezone.utc)
PAGE = {"name": "preparer-local-host", "version": "0.1.0"}


def docs(draft_id: str) -> list[dict[str, Any]]:
    return [{"rule_id": d["rule_id"], "count": d["count"]} for d in DRAFTS[draft_id]["documents"]]


def key_entries() -> list[dict[str, Any]]:
    """demo1 misses one W-2 behind a wrong count (shown), demo2 misses a 1098-T (hidden), demo3 is clean."""
    d1 = docs("demo1")
    r001 = next(d for d in d1 if d["rule_id"] == "R001")
    return [
        {"draft_id": "demo1", "problem_class": "shows", "required": [{**d, "count": d["count"] + 1} if d["rule_id"] == "R001" else d for d in d1],
         "missed": [{"rule_id": "R001", "count": 1}], "unnecessary": [],
         "wrong_facts": [{"fact": "w2_employer_count", "recorded": r001["count"], "true": r001["count"] + 1, "evidence": ""}],
         "problems": [{"kind": "missed", "rule_id": "R001", "count": 1, "shows_because": "dropped:R001"},
                      {"kind": "wrong_fact", "fact": "w2_employer_count", "shows_because": "dropped:R001"}]},
        {"draft_id": "demo2", "problem_class": "hides", "required": docs("demo2") + [{"rule_id": "R017", "count": 1}],
         "missed": [{"rule_id": "R017", "count": 1}], "unnecessary": [], "wrong_facts": [],
         "problems": [{"kind": "missed", "rule_id": "R017", "count": 1, "shows_because": None}]},
        {"draft_id": "demo3", "problem_class": "clean", "required": docs("demo3"), "missed": [], "unnecessary": [],
         "wrong_facts": [], "problems": []},
    ]


def decisions(draft_id: str, **changes: Any) -> dict[str, Any]:
    draft = DRAFTS[draft_id]
    out = {
        "documents": [{"rule_id": d["rule_id"], "action": "keep"} for d in draft["documents"]],
        "custom_requests": [{"index": i, "action": "keep"} for i, _ in enumerate(draft["custom_requests"])],
        "added": [], "follow_ups": [], "flags_checked": [f["id"] for f in compute_flags(draft, REF)],
    }
    out.update(changes)
    return out


def record(draft_id: str, minutes: int = 3, **changes: Any) -> dict[str, Any]:
    dec = rv.check_decisions(decisions(draft_id, **changes), DRAFTS[draft_id], compute_flags(DRAFTS[draft_id], REF), REF, final=True)
    opened = rv.OpenReview(draft_id, "t", DRAFTS[draft_id]["draft_sha256"], T0)
    return rv.build_record(draft=DRAFTS[draft_id], decisions=dec, opened=opened, existing=None,
                           now=T0 + timedelta(minutes=minutes), approve=True, host=PAGE, client=PAGE, ref=REF)


@pytest.fixture
def setup(tmp_path):
    drafts = tmp_path / "drafts"
    shutil.copytree(DEMO, drafts)
    reviews = rv.ReviewFolder(tmp_path / "reviews")
    prereg = yaml.safe_load((REPO / "eval" / "review_prereg.yaml").read_text())
    prereg.update(locked_on="2026-11-07", drafts=3, show_drafts=1, hide_drafts=1, clean_drafts=1, example_draft="demo3",
                  inferable_sha256=rc.marked_digest("test-salt", ["demo1"]))
    (tmp_path / "marked.json").write_text(dump({"salt": "test-salt", "drafts": ["demo1"]}))
    (tmp_path / "prereg.yaml").write_text(yaml.safe_dump(prereg))
    (tmp_path / "key.json").write_text(dump({"drafts": key_entries()}))
    (tmp_path / "noted.json").write_text(dump({"drafts": [
        {"draft_id": "demo1", "problems": [{"kind": "missed", "rule_id": "R001", "noted_in": ["agent_note"]},
                                           {"kind": "wrong_fact", "fact": "w2_employer_count", "noted_in": []}]},
        {"draft_id": "demo2", "problems": [{"kind": "missed", "rule_id": "R017", "noted_in": []}]},
    ]}))
    paths = dict(prereg_path=tmp_path / "prereg.yaml", key_path=tmp_path / "key.json", noted_path=tmp_path / "noted.json",
                 marked_path=tmp_path / "marked.json",
                 drafts_dir=drafts, reviews_dir=tmp_path / "reviews", lock_time=lambda p: T0 - timedelta(days=1), ref=REF)
    return paths, reviews


def entry(draft_id: str) -> dict[str, Any]:
    return next(e for e in key_entries() if e["draft_id"] == draft_id)


# Wilson intervals.


def test_wilson_matches_known_values():
    assert rc.wilson(0, 0) == (None, None)
    low, high = rc.wilson(5, 10)
    assert round(low, 3) == 0.237 and round(high, 3) == 0.763
    low, high = rc.wilson(10, 10)
    assert round(low, 3) == 0.722 and high == 1.0
    assert rc.wilson(0, 4)[0] == 0.0


# What counts as caught.


def test_raising_the_w2_count_catches_the_miss_and_the_count_behind_it():
    r001 = next(d for d in DRAFTS["demo1"]["documents"] if d["rule_id"] == "R001")
    rec = record("demo1", documents=[{"rule_id": d["rule_id"], "action": "keep", **({"count": r001["count"] + 1} if d["rule_id"] == "R001" else {})}
                                     for d in DRAFTS["demo1"]["documents"]])
    assert [rc.problem_caught(p, entry("demo1"), rec, REF) for p in entry("demo1")["problems"]] == [True, True]


def test_a_follow_up_on_the_fact_catches_both_and_keeping_everything_catches_neither():
    asked = record("demo1", follow_ups=[{"fact": "w2_employer_count"}])
    assert [rc.problem_caught(p, entry("demo1"), asked, REF) for p in entry("demo1")["problems"]] == [True, True]
    kept = record("demo1")
    assert [rc.problem_caught(p, entry("demo1"), kept, REF) for p in entry("demo1")["problems"]] == [False, False]


def test_adding_a_missed_counted_document_catches_it_at_any_count():
    entry_r020 = {**entry("demo2"), "required": docs("demo2") + [{"rule_id": "R020", "count": 2}],
                  "missed": [{"rule_id": "R020", "count": 2}],
                  "problems": [{"kind": "missed", "rule_id": "R020", "count": 2, "shows_because": None}]}
    rec = record("demo2", added=[{"rule_id": "R020", "count": 1}])
    assert rc.problem_caught(entry_r020["problems"][0], entry_r020, rec, REF)
    assert rc.harmful_edits(DRAFTS["demo2"], entry_r020, rec) == []


def test_raising_a_drafted_count_short_of_the_requirement_is_not_a_catch():
    r001 = next(d for d in DRAFTS["demo1"]["documents"] if d["rule_id"] == "R001")
    e = {**entry("demo1"), "required": [{"rule_id": "R001", "count": r001["count"] + 2}]}
    rec = record("demo1", documents=[{"rule_id": d["rule_id"], "action": "keep", **({"count": r001["count"] + 1} if d["rule_id"] == "R001" else {})}
                                     for d in DRAFTS["demo1"]["documents"]])
    assert not rc.problem_caught(e["problems"][0], e, rec, REF)


def test_a_hidden_miss_is_caught_by_adding_it_or_asking_about_its_fact():
    problem = entry("demo2")["problems"][0]
    assert rc.problem_caught(problem, entry("demo2"), record("demo2", added=[{"rule_id": "R017"}]), REF)
    assert rc.problem_caught(problem, entry("demo2"), record("demo2", follow_ups=[{"fact": "tuition_paid"}]), REF)
    assert not rc.problem_caught(problem, entry("demo2"), record("demo2", follow_ups=[{"fact": "has_mortgage"}]), REF)


# Harmful edits.


def test_keeping_a_clean_draft_as_it_is_is_not_harmful():
    assert rc.harmful_edits(DRAFTS["demo3"], entry("demo3"), record("demo3")) == []


def test_removing_adding_or_changing_counts_on_a_clean_draft_is_harmful():
    first = DRAFTS["demo3"]["documents"][0]["rule_id"]
    removed = record("demo3", documents=[{"rule_id": first, "action": "remove", "reason": "not_needed"}]
                     + [{"rule_id": d["rule_id"], "action": "keep"} for d in DRAFTS["demo3"]["documents"][1:]])
    assert rc.harmful_edits(DRAFTS["demo3"], entry("demo3"), removed) == [f"removed {first}, which is required"]
    added = record("demo3", added=[{"rule_id": "R017"}])
    assert rc.harmful_edits(DRAFTS["demo3"], entry("demo3"), added) == ["added R017, which the household doesn't need"]
    w2 = next(d for d in DRAFTS["demo3"]["documents"] if d["rule_id"] == "R001")
    for count, expected in ((w2["count"] + 1, "raised R001"), (w2["count"] - 1, "lowered R001")):
        rec = record("demo3", documents=[{"rule_id": d["rule_id"], "action": "keep", **({"count": count} if d["rule_id"] == "R001" else {})}
                                         for d in DRAFTS["demo3"]["documents"]])
        assert rc.harmful_edits(DRAFTS["demo3"], entry("demo3"), rec)[0].startswith(expected)


def test_fixing_a_miss_is_not_harmful():
    rec = record("demo2", added=[{"rule_id": "R017"}])
    assert rc.harmful_edits(DRAFTS["demo2"], entry("demo2"), rec) == []


# Refusals.


def write_all(reviews, **records):
    for draft_id in ("demo1", "demo2", "demo3"):
        reviews.write(records.get(draft_id) or record(draft_id))
        reviews.log_opening(draft_id, DRAFTS[draft_id]["draft_sha256"], T0, client=PAGE)


def test_scoring_refuses_an_unlocked_or_changed_prereg(setup):
    paths, reviews = setup
    write_all(reviews)
    prereg = yaml.safe_load(paths["prereg_path"].read_text())
    paths["prereg_path"].write_text(yaml.safe_dump({**prereg, "locked_on": None}))
    with pytest.raises(rc.CatchError, match="not locked"):
        rc.load(**paths)
    paths["prereg_path"].write_text(yaml.safe_dump(prereg))
    with pytest.raises(rc.CatchError, match="changes since its lock commit"):
        rc.load(**{**paths, "lock_time": lambda p: None})
    with pytest.raises(rc.CatchError, match="does not predate the first opening"):
        rc.load(**{**paths, "lock_time": lambda p: T0 + timedelta(seconds=1)})


def test_scoring_refuses_when_any_draft_was_opened_before_the_lock(setup):
    paths, reviews = setup
    reviews.log_opening("demo2", DRAFTS["demo2"]["draft_sha256"], T0 - timedelta(days=2), client=PAGE)
    write_all(reviews)
    with pytest.raises(rc.CatchError, match="does not predate the first opening"):
        rc.load(**paths)


CLAUDE_CODE = {"name": "claude-code", "version": "2.1.321"}


@pytest.mark.parametrize("change", [
    "opening_client", "opening_none", "record_client", "record_host", "record_host_none", "record_client_version_only",
])
def test_scoring_refuses_any_opening_or_record_not_from_the_local_review_page(setup, change):
    paths, reviews = setup
    write_all(reviews)
    assert rc.load(**paths).records["demo1"]["host"] == PAGE
    if change == "opening_client":
        reviews.log_opening("demo2", DRAFTS["demo2"]["draft_sha256"], T0 + timedelta(hours=1), client=CLAUDE_CODE)
        match = r"an opening of demo2 names 'claude-code' as its client"
    elif change == "opening_none":
        reviews.log_opening("demo3", DRAFTS["demo3"]["draft_sha256"], T0 + timedelta(hours=1))
        match = r"an opening of demo3 names None as its client"
    else:
        rec = record("demo2")
        if change == "record_client":
            rec["client"] = CLAUDE_CODE
            match = r"demo2's record names 'claude-code' as its client"
        elif change == "record_host":
            rec["host"] = {"name": "Claude", "version": "1"}
            match = r"demo2's record names 'Claude' as its host"
        elif change == "record_host_none":
            rec["host"] = None
            match = r"demo2's record names None as its host"
        else:
            rec["client"] = {"version": "0.1.0"}
            match = r"demo2's record names None as its client"
        (paths["reviews_dir"] / "demo2.json").write_text(dump(rec))
    with pytest.raises(rc.CatchError, match=match):
        rc.load(**paths)


def test_the_page_and_the_check_agree_on_the_name():
    from preparer import local_review
    assert rc.REVIEW_HOST == local_review.NAME == PAGE["name"]


def test_scoring_refuses_without_the_openings_log_or_the_key(setup):
    paths, reviews = setup
    for draft_id in ("demo1", "demo2", "demo3"):
        reviews.write(record(draft_id))
    with pytest.raises(rc.CatchError, match="no log of openings"):
        rc.load(**paths)
    with pytest.raises(rc.CatchError, match="key.json is missing"):
        rc.load(**{**paths, "key_path": paths["key_path"].with_name("absent-key.json")})


def test_scoring_refuses_until_every_draft_is_approved_for_its_version(setup):
    paths, reviews = setup
    reviews.write(record("demo1"))
    reviews.write(record("demo2"))
    with pytest.raises(rc.CatchError, match="demo3 is not approved yet"):
        rc.load(**paths)
    saved = record("demo3")
    reviews.write({**saved, "status": "in_review"})
    with pytest.raises(rc.CatchError, match="demo3 is not approved yet"):
        rc.load(**paths)
    reviews.write({**saved, "draft_sha256": "0" * 64})
    with pytest.raises(rc.CatchError, match="another version"):
        rc.load(**paths)


def test_scoring_refuses_marked_drafts_that_break_the_commitment(setup, tmp_path):
    paths, reviews = setup
    write_all(reviews)
    assert rc.load(**paths).marked == ("demo1",)
    with pytest.raises(rc.CatchError, match="marked.json is missing"):
        rc.load(**{**paths, "marked_path": tmp_path / "absent" / "marked.json"})
    for changed in ({"salt": "other", "drafts": ["demo1"]}, {"salt": "test-salt", "drafts": ["demo2"]},
                    {"salt": "test-salt", "drafts": []}):
        paths["marked_path"].write_text(dump(changed))
        with pytest.raises(rc.CatchError, match="don't match the prereg's inferable_sha256"):
            rc.load(**paths)
    prereg = yaml.safe_load(paths["prereg_path"].read_text())
    paths["prereg_path"].write_text(yaml.safe_dump({**prereg, "inferable_sha256": rc.marked_digest("x", ["d99"])}))
    paths["marked_path"].write_text(dump({"salt": "x", "drafts": ["d99"]}))
    with pytest.raises(rc.CatchError, match="marks a draft that isn't one of the 3"):
        rc.load(**paths)


def test_the_marked_digest_is_order_free_and_salted():
    assert rc.marked_digest("s", ["d02", "d01"]) == rc.marked_digest("s", ["d01", "d02"])
    assert rc.marked_digest("s", ["d01"]) != rc.marked_digest("t", ["d01"])
    import hashlib
    assert rc.marked_digest("s", ["d02", "d01"]) == hashlib.sha256(b"s:d01,d02").hexdigest()


def _marked_matches(expected: str) -> bool:
    """Whether the local marked file gives the prereg's hash. The file is read only here, so no test frame holds it."""
    marked = json.loads(rc.MARKED.read_text())
    return rc.marked_digest(marked["salt"], marked["drafts"]) == expected


def test_the_real_prereg_commits_to_the_marked_drafts():
    prereg = yaml.safe_load((REPO / "eval" / "review_prereg.yaml").read_text())
    assert len(prereg["inferable_sha256"]) == 64 and int(prereg["inferable_sha256"], 16) >= 0
    assert prereg["example_draft"] == "d07" and "catch_rate_shows_unmarked" in prereg["report"]
    if rc.MARKED.exists():
        assert _marked_matches(prereg["inferable_sha256"]), "review_inferable.json doesn't match the prereg (its contents are not printed)"


def test_an_unlocked_copy_of_the_real_prereg_is_refused(tmp_path):
    prereg = yaml.safe_load((REPO / "eval" / "review_prereg.yaml").read_text())
    (tmp_path / "prereg.yaml").write_text(yaml.safe_dump({**prereg, "locked_on": None}))
    with pytest.raises(rc.CatchError, match="is not locked"):
        rc.load(prereg_path=tmp_path / "prereg.yaml")


def test_main_writes_nothing_when_refused(tmp_path, monkeypatch, capsys):
    def refuse(**kw):
        raise rc.CatchError("not yet")

    monkeypatch.setattr(rc, "load", refuse)
    assert rc.main(["--out", str(tmp_path / "out.md")]) == 1
    assert "refused: not yet" in capsys.readouterr().out and not (tmp_path / "out.md").exists()


# Scoring and the report.


def test_the_scores_and_the_report(setup):
    paths, reviews = setup
    first = DRAFTS["demo3"]["documents"][0]["rule_id"]
    write_all(
        reviews,
        demo1=record("demo1", minutes=4, follow_ups=[{"fact": "w2_employer_count"}]),
        demo2=record("demo2", minutes=2),
        demo3=record("demo3", minutes=6, follow_ups=[{"fact": "k1_count"}],
                     documents=[{"rule_id": first, "action": "remove", "reason": "duplicate"}]
                     + [{"rule_id": d["rule_id"], "action": "keep"} for d in DRAFTS["demo3"]["documents"][1:]]),
    )
    inputs = rc.load(**paths)
    scored = rc.score(inputs, REF)
    assert (scored["catch_rate_shows"]["k"], scored["catch_rate_shows"]["n"]) == (1, 1)
    assert (scored["catch_rate_hides"]["k"], scored["catch_rate_hides"]["n"]) == (0, 1)
    assert (scored["catch_rate_noted"]["k"], scored["catch_rate_noted"]["n"]) == (1, 1)
    assert (scored["catch_rate_unnoted"]["k"], scored["catch_rate_unnoted"]["n"]) == (1, 2)
    assert (scored["harmful_edits_clean"]["k"], scored["follow_ups_clean"]["k"]) == (1, 1)
    assert scored["median_minutes"] == 4
    assert scored["dropped_r001_actions"] == {"drafts": 1, "raised_count": 0, "asked_w2": 1, "neither": 0}
    assert (scored["catch_rate_shows_unmarked"]["k"], scored["catch_rate_shows_unmarked"]["n"]) == (0, 0)
    assert scored["marked"] == ["demo1"] and scored["example_draft_actions"]["draft_id"] == "demo3"
    text = rc.render(scored, inputs)
    assert "| The same, leaving out the marked drafts | 0 of 0 | n/a | n/a |" in text
    assert "All 1 drafts whose problem a flag shows are among the 1 marked drafts" in text
    assert "- W-2 count raised: 0\n- W-2 employers asked about: 1\n- Neither: 0" in text
    assert "| 95% Wilson interval |" in text and "| Follow-ups | Seconds |" in text and "| 240 |" in text
    assert "Marked: demo1. Salt: `test-salt`" in text and rc.marked_digest("test-salt", ["demo1"]) in text
    assert "On demo3, the ID the PRD's worked example used, it was a clean draft; harmful edits: removed " in text
    assert "## What the reviewer knew, and the marked drafts (prereg text, as locked)" in text
    assert f"**reviewer_rule.** {inputs.prereg['reviewer_rule']}" in text
    assert "| Problem drafts caught, problem shown by a flag | 1 of 1 | 100% |" in text
    assert "| demo3 | clean |  | removed " in text
    assert "| demo2 | missed R017 | nothing | no | no |" in text
    assert "Median time on screen: 240 seconds (4.00 minutes) per draft. On 2 of 3 drafts the approved list kept" in text
    assert scored["median_seconds"] == 240 and scored["documents_unchanged"] == 2


# The key's own checks.


def _rewrite(path: Path, edit) -> None:
    data = json.loads(path.read_text())
    edit(data)
    path.write_text(dump(data))


@pytest.mark.parametrize("edit, reason", [
    (lambda k: k["drafts"][0]["missed"][0].update(count=2), "inconsistent for R001"),
    (lambda k: k["drafts"][1].update(problem_class="shows"), "doesn't follow class_rule"),
    (lambda k: k["drafts"][0]["problems"][0].update(shows_because=None), "shows by None"),
    (lambda k: k["drafts"][2].update(problem_class="hides", problems=[]), "doesn't follow class_rule"),
])
def test_a_key_that_disagrees_with_itself_or_the_flags_is_refused(setup, edit, reason):
    paths, reviews = setup
    write_all(reviews, demo1=record("demo1"), demo2=record("demo2"), demo3=record("demo3"))
    _rewrite(paths["key_path"], lambda k: edit(k))
    with pytest.raises(rc.CatchError, match=reason):
        rc.load(**paths)


def test_a_problem_without_its_noted_annotation_is_refused(setup):
    paths, reviews = setup
    write_all(reviews, demo1=record("demo1"), demo2=record("demo2"), demo3=record("demo3"))
    _rewrite(paths["noted_path"], lambda n: n["drafts"][1].update(problems=[]))
    with pytest.raises(rc.CatchError, match="no annotation for a problem on demo2"):
        rc.load(**paths)


def test_class_counts_other_than_the_prereg_s_and_another_interval_method_are_refused(setup):
    paths, reviews = setup
    write_all(reviews, demo1=record("demo1"), demo2=record("demo2"), demo3=record("demo3"))
    prereg = yaml.safe_load(paths["prereg_path"].read_text())
    paths["prereg_path"].write_text(yaml.safe_dump({**prereg, "hide_drafts": 2, "clean_drafts": 0}))
    with pytest.raises(rc.CatchError, match="are not the prereg's"):
        rc.load(**paths)
    paths["prereg_path"].write_text(yaml.safe_dump({**prereg, "intervals": {"method": "wald", "level": 0.95}}))
    with pytest.raises(rc.CatchError, match="other than wilson"):
        rc.load(**paths)


def test_minutes_round_half_up_in_decimal():
    assert rc.minutes_text(10.5) == "0.18" and rc.minutes_text(240) == "4.00" and rc.minutes_text(9) == "0.15"
    assert rc.seconds_text(10.5) == "10.5 seconds" and rc.seconds_text(240) == "240 seconds"
