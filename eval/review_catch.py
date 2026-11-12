"""Scores the blind catch check and writes docs/review-check.md (PRD-preparer-review.md, sections 5 and 6.4).

    uv run python -m eval.review_catch

Reads eval/review_prereg.yaml, the answer key, the noted-problem annotation and the marked drafts under
data/truth, the 24 drafts in data/drafts, and Roanuk's review records in data/reviews. Makes no model call.

It refuses to score until the prereg is locked (locked_on set, and the file committed with no
change since) before the first opening of any draft, saved or not, as the server's openings log
records it, and until every one of the 24 drafts has an approved record for the draft's current
SHA-256. It also refuses when the marked drafts (those committed text could let a reader single out)
don't match the salted SHA-256 the prereg committed to, and unless every opening names the local review
page (preparer/local_review.py) as its client and every record names it as both client and host. That
rules out openings and approvals through the Claude Desktop and Claude Code preparer entries, whose client
names differ. Names are self-reported, so it can't catch a client that calls itself preparer-local-host,
or an opening logged to another reviews folder; CLAUDE.md rules those out. It reports whichever way the
numbers come out.

The rules it applies are the prereg's own words. A problem draft is caught when the approved list
adds a missed document (at any count) or raises its count to the required count, or a follow-up asks
about a fact behind a missed document or about a wrong fact. Each problem is also scored on its own. A
harmful edit removes a required document, lowers a required document's count below the required count,
adds a document the household doesn't need, or raises a count above the required count; custom
requests are never scored as harmful. Each rate gets a Wilson interval at the prereg's level. Before
scoring it also checks the key against itself and against the drafts' own flags: each missed document's
drafted and missed counts add up to the required count, each problem's class follows class_rule from the
flags the screen showed, the class counts are the prereg's, and every problem has its noted annotation.
"""

from __future__ import annotations

import argparse
from decimal import ROUND_HALF_UP, Decimal
import hashlib
import json
import math
import statistics
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import NormalDist
from typing import Any, Callable, Mapping, Sequence

import yaml

from preparer.drafts import DraftFolder, Reference, compute_flags, rule_fact_keys
from preparer.reviews import APPROVED, ReviewFolder, parse_stamp

REPO = Path(__file__).resolve().parent.parent
PREREG = REPO / "eval" / "review_prereg.yaml"
KEY = REPO / "data" / "truth" / "review_key.json"
NOTED = REPO / "data" / "truth" / "review_noted.json"
MARKED = REPO / "data" / "truth" / "review_inferable.json"
DRAFTS = REPO / "data" / "drafts"
REVIEWS = REPO / "data" / "reviews"
OUT = REPO / "docs" / "review-check.md"
REVIEW_HOST = "preparer-local-host"  # the local review page's client and host name (preparer/local_review.py)


class CatchError(RuntimeError):
    """The check can't be scored yet."""


def wilson(k: int, n: int, level: float = 0.95) -> tuple[float | None, float | None]:
    """The Wilson score interval for k successes in n, or (None, None) when n is 0."""
    if n == 0:
        return None, None
    z = NormalDist().inv_cdf(1 - (1 - level) / 2)
    p = k / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, center - half), min(1.0, center + half)


# Scoring one review.


def approved_counts(record: Mapping[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in record["approved_list"]:
        if item["rule_id"] is not None:
            out[item["rule_id"]] = out.get(item["rule_id"], 0) + int(item["count"])
    return out


def required_counts(entry: Mapping[str, Any]) -> dict[str, int]:
    return {r["rule_id"]: int(r["count"]) for r in entry["required"]}


def follow_ups(record: Mapping[str, Any]) -> set[str]:
    return {f["fact"] for f in record["decisions"]["follow_ups"]}


def missed_fixed(rule_id: str, entry: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    """The approved list adds the missed document (at any count), or raises its count to the required count."""
    approved = approved_counts(record).get(rule_id, 0)
    added = any(item["rule_id"] == rule_id for item in record["decisions"]["added"])
    return (added and approved >= 1) or approved >= required_counts(entry)[rule_id]


def problem_caught(problem: Mapping[str, Any], entry: Mapping[str, Any], record: Mapping[str, Any], ref: Reference) -> bool:
    asked = follow_ups(record)
    if problem["kind"] == "missed":
        rule = ref.rules[problem["rule_id"]]
        return missed_fixed(rule.id, entry, record) or bool(asked & set(rule_fact_keys(rule)))
    fact = problem["fact"]
    if fact in asked:
        return True
    return any(
        missed_fixed(m["rule_id"], entry, record) and fact in rule_fact_keys(ref.rules[m["rule_id"]]) for m in entry["missed"]
    )


def harmful_edits(draft: Mapping[str, Any], entry: Mapping[str, Any], record: Mapping[str, Any]) -> list[str]:
    """Each change the reviewer made that removes something required or adds something unneeded."""
    required = required_counts(entry)
    drafted = {d["rule_id"]: d["count"] for d in draft["documents"]}
    out = []
    for item in record["decisions"]["documents"]:
        rule_id, before = item["rule_id"], drafted[item["rule_id"]]
        need = required.get(rule_id, 0)
        if item["action"] == "remove":
            if need:
                out.append(f"removed {rule_id}, which is required")
            continue
        after = item.get("count", before)
        if after < before and after < need:
            out.append(f"lowered {rule_id} to {after}, below the required {need}")
        if after > before and after > need:
            out.append(f"raised {rule_id} to {after}, above the {need} needed")
    for item in record["decisions"]["added"]:
        count = item.get("count", 1)
        if count > required.get(item["rule_id"], 0):
            out.append(f"added {item['rule_id']}, which the household doesn't need" if not required.get(item["rule_id"])
                       else f"added {item['rule_id']} with {count}, above the {required[item['rule_id']]} needed")
    return out


# Loading and checking what was saved.


def marked_digest(salt: str, draft_ids: Sequence[str]) -> str:
    """The prereg's inferable_sha256: the SHA-256 of the salt, a colon, and the sorted marked IDs joined by commas."""
    return hashlib.sha256(f"{salt}:{','.join(sorted(draft_ids))}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Inputs:
    prereg: Mapping[str, Any]
    key: Mapping[str, Any]
    noted: Mapping[str, Any]
    drafts: Mapping[str, Mapping[str, Any]]
    records: Mapping[str, Mapping[str, Any]]
    marked: tuple[str, ...] = ()
    salt: str = ""


def problem_identity(problem: Mapping[str, Any]) -> tuple[str, str]:
    """A problem's kind and its rule or fact, the same in the key and in the noted annotation."""
    return problem["kind"], problem["rule_id"] if problem["kind"] == "missed" else problem["fact"]


def noted_by_problem(noted: Mapping[str, Any]) -> dict[tuple[str, str, str], bool]:
    return {(d["draft_id"], *problem_identity(p)): bool(p["noted_in"]) for d in noted["drafts"] for p in d["problems"]}


def shown_by(problem: Mapping[str, Any], entry: Mapping[str, Any], flag_ids: set[str], ref: Reference) -> list[str]:
    """The flags that show a problem under class_rule: a missed document shows when the dropped flag names its rule;
    a wrong fact shows when the carried flag names it, or when a dropped flag for a missed document displays it."""
    if problem["kind"] == "missed":
        return [f"dropped:{problem['rule_id']}"] if f"dropped:{problem['rule_id']}" in flag_ids else []
    fact = problem["fact"]
    out = [f"carried:{fact}"] if f"carried:{fact}" in flag_ids else []
    out += [f"dropped:{m['rule_id']}" for m in entry["missed"]
            if f"dropped:{m['rule_id']}" in flag_ids and fact in rule_fact_keys(ref.rules[m["rule_id"]])]
    return out


def check_key(prereg: Mapping[str, Any], key: Mapping[str, Any], noted: Mapping[str, Any],
              drafts: Mapping[str, Mapping[str, Any]], ref: Reference) -> None:
    """Refuses a key that disagrees with itself, with the drafts' own flags, with the prereg's design or with the
    noted annotation, and an interval method other than Wilson's."""
    if prereg.get("intervals", {}).get("method") != "wilson":
        raise CatchError("the prereg names an interval method other than wilson")
    classes: dict[str, int] = {}
    annotated = noted_by_problem(noted)
    for entry in key["drafts"]:
        draft_id, draft = entry["draft_id"], drafts[entry["draft_id"]]
        required = required_counts(entry)
        drafted = {d["rule_id"]: int(d["count"]) for d in draft["documents"]}
        for m in entry["missed"]:
            if m["rule_id"] not in required or drafted.get(m["rule_id"], 0) + int(m["count"]) != required[m["rule_id"]]:
                raise CatchError(f"{draft_id}'s key entry is inconsistent for {m['rule_id']}")
        flag_ids = {f["id"] for f in compute_flags(draft, ref)}
        shown = []
        for p in entry["problems"]:
            flags = shown_by(p, entry, flag_ids, ref)
            if bool(flags) != bool(p["shows_because"]) or (p["shows_because"] and p["shows_because"] not in flags):
                raise CatchError(f"{draft_id}'s key says a problem shows by {p['shows_because']!r}, but its flags give {flags}")
            if (draft_id, *problem_identity(p)) not in annotated:
                raise CatchError(f"{NOTED.name} has no annotation for a problem on {draft_id}")
            shown.append(bool(flags))
        cls = "clean" if not shown else "shows" if all(shown) else "hides" if not any(shown) else None
        if cls != entry["problem_class"]:
            raise CatchError(f"{draft_id}'s class in the key doesn't follow class_rule from its flags")
        classes[cls] = classes.get(cls, 0) + 1
    want = {"shows": prereg["show_drafts"], "hides": prereg["hide_drafts"], "clean": prereg["clean_drafts"]}
    if classes != {k: v for k, v in want.items() if v}:
        raise CatchError(f"the key's classes {classes} are not the prereg's {want}")


def git_lock_time(path: Path) -> datetime | None:
    """When the prereg was last committed, or None when it has uncommitted changes or was never committed."""
    rel = str(path.relative_to(REPO))
    dirty = subprocess.run(["git", "status", "--porcelain", "--", rel], cwd=REPO, capture_output=True, text=True, check=True)
    if dirty.stdout.strip():
        return None
    out = subprocess.run(["git", "log", "-1", "--format=%cI", "--", rel], cwd=REPO, capture_output=True, text=True, check=True)
    return datetime.fromisoformat(out.stdout.strip()) if out.stdout.strip() else None


def load(
    prereg_path: Path = PREREG,
    key_path: Path = KEY,
    noted_path: Path = NOTED,
    marked_path: Path = MARKED,
    drafts_dir: Path = DRAFTS,
    reviews_dir: Path = REVIEWS,
    lock_time: Callable[[Path], datetime | None] = git_lock_time,
    ref: Reference | None = None,
) -> Inputs:
    prereg = yaml.safe_load(prereg_path.read_text())
    if not prereg.get("locked_on"):
        raise CatchError("eval/review_prereg.yaml is not locked: set locked_on and commit it before the first review")
    locked = lock_time(prereg_path)
    if locked is None:
        raise CatchError("eval/review_prereg.yaml has changes since its lock commit, or was never committed")
    for path in (key_path, noted_path, marked_path):
        if not path.is_file():
            raise CatchError(f"{path.name} is missing; it lives only on the machine that exported the drafts")
    key = json.loads(key_path.read_text())
    noted = json.loads(noted_path.read_text())
    marked_file = json.loads(marked_path.read_text())
    salt, marked = str(marked_file["salt"]), tuple(sorted(marked_file["drafts"]))
    if marked_digest(salt, marked) != prereg.get("inferable_sha256"):
        raise CatchError(f"the marked drafts in {marked_path.name} don't match the prereg's inferable_sha256")
    drafts = DraftFolder(drafts_dir, ref).load_all()
    reviews = ReviewFolder(reviews_dir)
    ids = [e["draft_id"] for e in key["drafts"]]
    if sorted(drafts) != sorted(ids) or len(ids) != prereg["drafts"]:
        raise CatchError(f"the drafts folder and the key must both hold the {prereg['drafts']} preregistered drafts")
    if not set(marked) <= set(ids):
        raise CatchError(f"{marked_path.name} marks a draft that isn't one of the {len(ids)}")
    records = {}
    for draft_id in ids:
        record = reviews.read(draft_id)
        if record is None or record["status"] != APPROVED:
            raise CatchError(f"{draft_id} is not approved yet; all {len(ids)} must be before scoring")
        if record["draft_sha256"] != drafts[draft_id]["draft_sha256"]:
            raise CatchError(f"{draft_id}'s review was for another version of the draft")
        records[draft_id] = record
    openings = reviews.openings()
    if not openings:
        raise CatchError("the reviews folder has no log of openings, so the lock can't be checked against the first look")
    first = min([parse_stamp(o["opened_at"]) for o in openings] + [parse_stamp(r["opened_at"]) for r in records.values()])
    if locked >= first:
        raise CatchError(f"the prereg lock commit ({locked.isoformat()}) does not predate the first opening ({first.isoformat()})")
    named = [(f"an opening of {o.get('draft_id')}", "client", o.get("client")) for o in openings]
    for draft_id, record in records.items():
        named += [(f"{draft_id}'s record", "client", record.get("client")), (f"{draft_id}'s record", "host", record.get("host"))]
    for what, role, who in named:
        name = who.get("name") if isinstance(who, dict) else None
        if name != REVIEW_HOST:
            raise CatchError(f"{what} names {name!r} as its {role}, not {REVIEW_HOST!r}: "
                             "every opening and approval must come from the local review page")
    check_key(prereg, key, noted, drafts, ref or Reference.load())
    return Inputs(prereg, key, noted, drafts, records, marked, salt)


# The numbers.


def rate(k: int, n: int, level: float) -> dict[str, Any]:
    low, high = wilson(k, n, level)
    return {"k": k, "n": n, "rate": k / n if n else None, "low": low, "high": high}


def score(inputs: Inputs, ref: Reference) -> dict[str, Any]:
    level = float(inputs.prereg["intervals"]["level"])
    noted = noted_by_problem(inputs.noted)
    rows, problems = [], []
    for entry in inputs.key["drafts"]:
        draft_id = entry["draft_id"]
        draft, record = inputs.drafts[draft_id], inputs.records[draft_id]
        caught_each = [problem_caught(p, entry, record, ref) for p in entry["problems"]]
        for p, caught in zip(entry["problems"], caught_each):
            problems.append({"draft_id": draft_id, "class": entry["problem_class"], "problem": p, "caught": caught,
                             "noted": noted[(draft_id, *problem_identity(p))]})
        flags = {f["id"] for f in compute_flags(draft, ref)}
        rows.append({
            "draft_id": draft_id,
            "class": entry["problem_class"],
            "caught": any(caught_each) if entry["problems"] else None,
            "harmful": harmful_edits(draft, entry, record),
            "follow_ups": sorted(follow_ups(record)),
            "seconds": record["seconds_on_screen"],
            "minutes": record["seconds_on_screen"] / 60,
            "dropped_r001": "dropped:R001" in flags,
            "r001_raised": approved_counts(record).get("R001", 0) > next(
                (d["count"] for d in draft["documents"] if d["rule_id"] == "R001"), 0),
            "asked_w2": "w2_employer_count" in follow_ups(record),
            "changes": record["changes"],
        })

    def drafts_of(cls: str) -> list[dict[str, Any]]:
        return [r for r in rows if r["class"] == cls]

    clean = drafts_of("clean")
    shows_unmarked = [r for r in drafts_of("shows") if r["draft_id"] not in inputs.marked]
    example = inputs.prereg.get("example_draft")
    noted_p = [p for p in problems if p["noted"]]
    unnoted_p = [p for p in problems if not p["noted"]]
    dropped = [r for r in rows if r["dropped_r001"]]
    return {
        "rows": rows,
        "problems": problems,
        "catch_rate_shows": rate(sum(r["caught"] for r in drafts_of("shows")), len(drafts_of("shows")), level),
        "catch_rate_shows_unmarked": rate(sum(r["caught"] for r in shows_unmarked), len(shows_unmarked), level),
        "catch_rate_hides": rate(sum(r["caught"] for r in drafts_of("hides")), len(drafts_of("hides")), level),
        "catch_rate_noted": rate(sum(p["caught"] for p in noted_p), len(noted_p), level),
        "catch_rate_unnoted": rate(sum(p["caught"] for p in unnoted_p), len(unnoted_p), level),
        "harmful_edits_clean": rate(sum(bool(r["harmful"]) for r in clean), len(clean), level),
        "follow_ups_clean": rate(sum(bool(r["follow_ups"]) for r in clean), len(clean), level),
        "median_minutes": statistics.median(r["minutes"] for r in rows),
        "median_seconds": statistics.median(r["seconds"] for r in rows),
        "documents_unchanged": sum(not (r["changes"]["removed"] or r["changes"]["added"] or r["changes"]["count_changed"]) for r in rows),
        "dropped_r001_actions": {
            "drafts": len(dropped),
            "raised_count": sum(r["r001_raised"] for r in dropped),
            "asked_w2": sum(r["asked_w2"] for r in dropped),
            "neither": sum(not r["r001_raised"] and not r["asked_w2"] for r in dropped),
        },
        "marked": list(inputs.marked),
        "example_draft_actions": next((r for r in rows if r["draft_id"] == example), None),
    }


# The report.


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def rate_line(name: str, r: Mapping[str, Any]) -> str:
    interval = "n/a" if not r["n"] else f"{pct(r['low'])} to {pct(r['high'])}"
    return f"| {name} | {r['k']} of {r['n']} | {pct(r['rate'])} | {interval} |"


def minutes_text(seconds: float) -> str:
    """Seconds as minutes to two places, rounded half up in decimal, so 10.5 seconds reads 0.18, not 0.17."""
    return str((Decimal(str(seconds)) / 60).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def seconds_text(seconds: float) -> str:
    """Whole seconds as the server stamps them, or a half second for a median between two."""
    return f"{seconds:g} seconds"


def render(scored: Mapping[str, Any], inputs: Inputs) -> str:
    p = inputs.prereg
    lines = [
        "# The preparer's review screen: blind catch check",
        "",
        f"One reviewer, Roanuk, reviewed {p['drafts']} drafts from the published test run on the review screen, blind to "
        f"each draft's result, under the preregistration in `eval/review_prereg.yaml` (locked {p['locked_on']}). "
        "Generated by `eval/review_catch.py`; not tax advice; all households are synthetic.",
        "",
        f"**Hypothesis, as locked:** {p['hypothesis']}",
        "",
        "## Results",
        "",
        f"| Measure | Count | Rate | {float(p['intervals']['level']):.0%} Wilson interval |",
        "|---|---|---|---|",
        rate_line("Problem drafts caught, problem shown by a flag", scored["catch_rate_shows"]),
        rate_line("The same, leaving out the marked drafts", scored["catch_rate_shows_unmarked"]),
        rate_line("Problem drafts caught, problem hidden from the flags", scored["catch_rate_hides"]),
        rate_line("Problems caught, noted by the agent", scored["catch_rate_noted"]),
        rate_line("Problems caught, not noted by the agent", scored["catch_rate_unnoted"]),
        rate_line("Clean drafts with a harmful edit", scored["harmful_edits_clean"]),
        rate_line("Clean drafts with a follow-up question", scored["follow_ups_clean"]),
        "",
    ]
    if not scored["catch_rate_shows_unmarked"]["n"]:
        lines += [f"All {scored['catch_rate_shows']['n']} drafts whose problem a flag shows are among the "
                  f"{len(scored['marked'])} marked drafts, so the shows rate without the marked drafts has no drafts to score.", ""]
    lines += [
        f"Median time on screen: {seconds_text(scored['median_seconds'])} ({minutes_text(scored['median_seconds'])} minutes) per draft. "
        f"On {scored['documents_unchanged']} of {len(scored['rows'])} drafts the approved list kept every document at the "
        "draft's own count, with none removed, added or recounted.",
        "",
    ]
    d = scored["dropped_r001_actions"]
    lines += [
        "## Drafts with a dropped W-2 flag",
        "",
        f"On the {d['drafts']} drafts with a dropped:R001 flag:",
        "",
        f"- W-2 count raised: {d['raised_count']}",
        f"- W-2 employers asked about: {d['asked_w2']}",
        f"- Neither: {d['neither']}",
        "",
    ]
    ex = scored["example_draft_actions"]
    if ex is not None:
        caught = "a clean draft" if ex["caught"] is None else ("a problem draft, caught" if ex["caught"] else "a problem draft, not caught")
        lines += [
            "## The PRD's example ID",
            "",
            f"On {ex['draft_id']}, the ID the PRD's worked example used, it was {caught}; harmful edits: "
            f"{'; '.join(ex['harmful']) or 'none'}; documents removed {ex['changes']['removed']}, added "
            f"{ex['changes']['added']}, recounted {ex['changes']['count_changed']}; follow-ups: "
            f"{', '.join(ex['follow_ups']) or 'none'}.",
            "",
        ]
    lines += [
        "## What the reviewer knew, and the marked drafts (prereg text, as locked)",
        "",
        f"**reviewer_knew.** {p['reviewer_knew']}",
        "",
        f"**reviewer_rule.** {p['reviewer_rule']}",
        "",
        f"**inferable_rule.** {p['inferable_rule']} Marked: {', '.join(scored['marked']) or 'none'}. Salt: "
        f"`{inputs.salt}`, which with the marked IDs gives the prereg's `inferable_sha256` (`{p['inferable_sha256']}`).",
        "",
    ]
    lines += [
        "## Each draft",
        "",
        "| Draft | Class | Caught | Harmful edits | Follow-ups | Seconds |",
        "|---|---|---|---|---|---|",
    ]
    for r in scored["rows"]:
        caught = "" if r["caught"] is None else ("yes" if r["caught"] else "no")
        lines.append(f"| {r['draft_id']} | {r['class']} | {caught} | {'; '.join(r['harmful']) or 'none'} | "
                     f"{', '.join(r['follow_ups']) or 'none'} | {r['seconds']} |")
    lines += ["", "## Each problem", "", "| Draft | Problem | Shown by | Noted by the agent | Caught |", "|---|---|---|---|---|"]
    for q in scored["problems"]:
        prob = q["problem"]
        what = f"missed {prob['rule_id']}" if prob["kind"] == "missed" else f"wrong {prob['fact']}"
        lines.append(f"| {q['draft_id']} | {what} | {prob['shows_because'] or 'nothing'} | {'yes' if q['noted'] else 'no'} | "
                     f"{'yes' if q['caught'] else 'no'} |")
    lines += [
        "",
        "## Limits",
        "",
        "- One reviewer, who also built the project, and 24 drafts, so every interval is wide.",
        f"- {p['show_drafts']} drafts show their problem, because only that many households in the test run have one.",
        "- The reviewer knew the shape of the shown problems before reviewing (above).",
        "- Time on screen adds up, for each opening that saved or approved, the time from the server's opening, made once "
        "the screen had loaded, to that opening's last save or approval. The server stamps whole seconds.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    ref = Reference.load()
    try:
        inputs = load(ref=ref)
    except CatchError as e:
        print(f"refused: {e}")
        return 1
    args.out.write_text(render(score(inputs, ref), inputs), encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
