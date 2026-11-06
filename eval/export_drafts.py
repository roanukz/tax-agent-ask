"""Exports the review drafts, the three demo drafts and the answer key (PRD-preparer-review.md, section 6.4).

    uv run python -m eval.export_drafts

Reads eval/runs and data/profiles, and writes data/drafts/d01.json to d24.json,
preparer/app/demo_drafts/demo1.json to demo3.json, and data/truth/review_key.json.
Makes no model call. Refuses to run once data/reviews/ holds a record or the server's
openings log, so no draft can change under a review. It never prints which review draft is
which, but it names each demo draft's source run, config included, so the reviewer runs none
of it before all 24 are approved (reviewer_runs_none_of in eval/review_prereg.yaml).

The pool is the test run as completed on 2026-11-04: every A config, every trial,
set-aside runs excluded. Each run's draft is rebuilt from its summary and the rules
engine, the way the tax server built it, and the export refuses a run whose request
list differs from the engine's. Each of the 24 is also checked against the draft the
agent submitted, read from its trace, and refused on any difference. The three demo
drafts come from dev runs, made before some rule wording changed, so they carry the
current rule text and their agent notes may quote an older wording. A problem draft has a missed required document or a
wrong fact. A clean draft passed with nothing missed, no wrong fact and no unnecessary
request, so any removal on it is a harmful edit; a run that failed only on advice is
neither and stays out.

Each problem shows or hides on the screen. A missed document shows when its rule was
on file last year with a higher count than the draft requests, which is the dropped
flag. A wrong fact shows when it was recorded with prior_year: evidence, the carried
flag, or when it is a fact the dropped flag of a missed document displays. A draft
shows when all its problems show and hides when none do; a draft with some of each is
left out of the draw, since it would measure neither.

The draw (seed 11) takes at most one draft per household, aims for 6 problems that
show and 6 that hide as the run allows, mixes configs by taking the least used config
in each household, and then shuffles the 24 into d01 to d24.

Drafts carry no household ID, and the client's name is replaced by Client 01 to
Client 24, in the name field and wherever the agent's or the client's text names the
household, because a name or ID would let a reviewer who knows the run tell a problem
draft from a clean one. The answer key keeps each draft's run, household and results.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from preparer.drafts import (
    DRAFT_ID,
    DRAFT_STATUSES,
    Reference,
    check_draft,
    compute_flags,
    computed_not_asked,
    draft_digest,
    dump,
    triggered_by,
)
from rules import engine
from server.intake import TRAP_DOCUMENT

REPO = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO / "eval" / "runs"
PROFILES_DIR = REPO / "data" / "profiles"
DRAFTS_DIR = REPO / "data" / "drafts"
REVIEWS_DIR = REPO / "data" / "reviews"
DEMO_DIR = REPO / "preparer" / "app" / "demo_drafts"
KEY_PATH = REPO / "data" / "truth" / "review_key.json"

SEED = 11
PROBLEM_DRAFTS = 12
CLEAN_DRAFTS = 12
SHOW_TARGET = 6
DEMO_KINDS = ("dropped", "client_question", "clean")
SOURCE = (
    "test split, every A config, the test run as completed on 2026-11-04 (40 households, 3 trials each), "
    "set-aside runs excluded"
)
CLASS_RULE = (
    "A missed document shows when the dropped flag names its rule. A wrong fact shows when the carried flag names it, "
    "or when a dropped flag for a missed document displays it. A draft shows when all its problems show, hides when "
    "none do, and is left out when some do."
)
SUMMARY_SUFFIX = ".summary.json"


class ExportError(RuntimeError):
    """The run files or the folders don't allow an export."""


@dataclass(frozen=True)
class Run:
    run_id: str
    household_id: str
    config: str
    summary: Mapping[str, Any]
    score: Mapping[str, Any]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_runs(runs_dir: Path, split: str) -> tuple[list[Run], list[str]]:
    """A config runs on one split with a summary and a score, from the top of runs_dir only. Set-aside runs
    are named {run_id}.unreached.*, so the summary glob never matches them. Returns the runs and the run IDs
    left out for having no score (the weakened-prompt dev runs)."""
    runs, unscored = [], []
    for path in sorted(runs_dir.glob(f"{split}-H[0-9][0-9][0-9]-A-*{SUMMARY_SUFFIX}")):
        run_id = path.name[: -len(SUMMARY_SUFFIX)]
        score_path = runs_dir / f"{run_id}.score.json"
        if "-weak-" in run_id or not score_path.is_file():
            unscored.append(run_id)
            continue
        summary, score = read_json(path), read_json(score_path)
        if summary.get("run_id") != run_id or score.get("run_id") != run_id:
            raise ExportError(f"{run_id}: the summary or score names another run")
        if summary.get("split") != split or not str(summary.get("config", "")).startswith("A-"):
            raise ExportError(f"{run_id}: not an A config run on the {split} split")
        if summary.get("status") != "pending_review":
            raise ExportError(f"{run_id}: the draft was never submitted for preparer review")
        runs.append(Run(run_id, summary["household_id"], summary["config"], summary, score))
    return runs, unscored


# Building a draft.


def client_label(draft_id: str) -> str:
    if draft_id.startswith("demo"):
        return f"Demo Client {draft_id[4:]}"
    return f"Client {draft_id[1:]}"


def masker(household_id: str, client_name: str | None, label: str):
    """Replaces the client's name and the household ID with the draft's label, everywhere in a text.

    Where the agent wrote the name and the ID together, as in "HNNN (Mock Household NNN)", masking
    each gives "Client NN (Client NN)". That doubled label is the export's artifact, not the agent's words,
    so it collapses to one label: "Client NN", or "Client NN (" when more text follows.
    """
    hid = re.compile(rf"\b{re.escape(household_id)}\b")
    doubled = re.compile(rf"{re.escape(label)} \({re.escape(label)}(\)|[,;:]\s*)")

    def mask(text: str) -> str:
        if client_name:
            text = text.replace(client_name, label)
        text = hid.sub(label, text)
        return doubled.sub(lambda m: label if m.group(1) == ")" else f"{label} (", text)

    return mask


def draft_fields(run: Run, ref: Reference, mask=lambda text: text) -> dict[str, Any]:
    """The draft's fields rebuilt from the run's summary and the rules engine, with mask applied to every text
    the client or the agent wrote. Refuses a run whose request list or not_asked differs from the engine's."""
    summary = run.summary
    facts = {key: {"value": e["value"], "evidence": mask(e["evidence"])} for key, e in summary["facts_recorded"].items()}
    recorded = {key: e["value"] for key, e in facts.items()}
    rules = list(ref.rules.values())
    listed = engine.required_documents(recorded, rules, ref.specs, DRAFT_STATUSES)
    if listed != summary["request_list"]:
        raise ExportError(f"{run.run_id}: the request list differs from the rules engine's, {listed}")
    documents = []
    for item in listed:
        rule = ref.rules[item["rule_id"]]
        documents.append({
            "rule_id": rule.id,
            "document": rule.document,
            "count": item["count"],
            "client_reason": rule.client_reason,
            "source_url": rule.source_url,
            "source_title": rule.source_title,
            "triggered_by": triggered_by(rule, facts),
        })
    not_asked = computed_not_asked(recorded, ref.specs)
    if not_asked != summary["not_asked"]:
        raise ExportError(f"{run.run_id}: not_asked differs from the facts never recorded")
    return {
        "documents": documents,
        "custom_requests": [
            {"document": mask(r["document"]), "reason": mask(r["reason"]), "needs_preparer_review": r["needs_preparer_review"]}
            for r in summary["custom_requests"]
        ],
        "no_document_needed": [
            {"rule_id": rid, "reason": TRAP_DOCUMENT, "source_url": ref.rules[rid].source_url, "source_title": ref.rules[rid].source_title}
            for rid in engine.triggered_traps(recorded, rules, ref.specs, DRAFT_STATUSES)
        ],
        "not_asked": not_asked,
        "notes_for_preparer": [{"text": mask(n["text"])} for n in summary["notes_for_preparer"]],
        "facts_recorded": facts,
    }


def build_draft(draft_id: str, run: Run, profile: Mapping[str, Any], ref: Reference) -> dict[str, Any]:
    label = client_label(draft_id)
    mask = masker(run.household_id, profile.get("client_name"), label)
    fields = draft_fields(run, ref, mask)
    draft = {
        "draft_id": draft_id,
        "client_name": label,
        "tax_year": profile["tax_year"],
        "prior_year_summary": profile["prior_year_summary"],
        "documents": fields["documents"],
        "custom_requests": fields["custom_requests"],
        "no_document_needed": fields["no_document_needed"],
        "not_asked": fields["not_asked"],
        "notes_for_preparer": fields["notes_for_preparer"],
        "facts_recorded": fields["facts_recorded"],
        "agent_note": mask(run.summary["final_message"]),
    }
    draft["draft_sha256"] = draft_digest(draft)
    check_draft(draft, ref)
    return draft


SUBMITTED_KEYS = ("documents", "custom_requests", "no_document_needed", "not_asked", "notes_for_preparer", "facts_recorded")


def submitted_draft(runs_dir: Path, run_id: str) -> Mapping[str, Any] | None:
    """The draft the agent submitted, as the run's trace recorded the tax server's answer, or None without a trace."""
    path = runs_dir / f"{run_id}.trace.json"
    if not path.is_file():
        return None
    from agent.summary import SUBMIT, Conversation, last_attempt

    return Conversation(last_attempt(read_json(path)["events"])).last_value(SUBMIT)


def check_submitted(run: Run, ref: Reference, submitted: Mapping[str, Any] | None) -> None:
    """Refuses a test draft whose rebuilt fields differ from what the agent submitted, before any masking."""
    if submitted is None:
        raise ExportError(f"{run.run_id}: no submitted draft in its trace")
    fields = draft_fields(run, ref)
    fields["documents"] = [{k: v for k, v in d.items() if k != "triggered_by"} for d in fields["documents"]]
    for key in SUBMITTED_KEYS:
        if fields[key] != submitted.get(key):
            raise ExportError(f"{run.run_id}: the rebuilt {key} differs from the draft the agent submitted")


# Classes.


def problems_of(run: Run, flags: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each missed document and wrong fact, with the flag that shows it on the screen, or None."""
    ids = {f["id"] for f in flags}
    missed = run.score["missed"]
    shown_misses = {m["rule_id"] for m in missed if f"dropped:{m['rule_id']}" in ids}
    behind_shown = {
        fact["fact"]: f["id"] for f in flags if f["kind"] == "dropped" and f["rule_id"] in shown_misses for fact in f["facts"]
    }
    out: list[dict[str, Any]] = []
    for m in missed:
        flag = f"dropped:{m['rule_id']}"
        out.append({"kind": "missed", "rule_id": m["rule_id"], "count": m["count"], "shows_because": flag if flag in ids else None})
    for w in run.score["wrong_facts"]:
        flag = f"carried:{w['fact']}"
        shows = flag if flag in ids else behind_shown.get(w["fact"])
        out.append({"kind": "wrong_fact", "fact": w["fact"], "shows_because": shows})
    return out


def draft_class(problems: Sequence[Mapping[str, Any]]) -> str:
    if not problems:
        return "clean"
    shown = [p["shows_because"] is not None for p in problems]
    if all(shown):
        return "shows"
    return "hides" if not any(shown) else "mixed"


def is_clean_pass(score: Mapping[str, Any]) -> bool:
    return (
        score["pass"] is True
        and not score["missed"]
        and not score["wrong_facts"]
        and not score["unnecessary"]
        and not score["trap_requests"]
        and not score["fail_reasons"]
    )


@dataclass(frozen=True)
class Candidate:
    run: Run
    cls: str
    problems: list[dict[str, Any]]
    flags: list[dict[str, Any]]


def candidates(runs: Sequence[Run], profiles_dir: Path, ref: Reference) -> tuple[list[Candidate], Counter[str]]:
    """Every run in a class the draw can use, and a count of every class including the ones left out."""
    out, tally = [], Counter()
    for run in runs:
        profile = read_json(profiles_dir / f"{run.household_id}.json")
        flags = compute_flags(build_draft("d00", run, profile, ref), ref)
        problems = problems_of(run, flags)
        if problems:
            cls = draft_class(problems)
        elif is_clean_pass(run.score):
            cls = "clean"
        else:
            cls = "passed_with_extras" if run.score["pass"] else "failed_without_problem"
        tally[cls] += 1
        if cls in ("shows", "hides", "clean"):
            out.append(Candidate(run, cls, problems, flags))
    return out, tally


# The draw.


def draw(pool: Sequence[Candidate], n: int, rng: random.Random, used: set[str], configs: Counter[str]) -> list[Candidate]:
    """Up to n candidates from households not used yet, one per household, households in a seeded order,
    and in each household a run of the config used least so far."""
    by_household: dict[str, list[Candidate]] = defaultdict(list)
    for c in pool:
        if c.run.household_id not in used:
            by_household[c.run.household_id].append(c)
    households = sorted(by_household)
    rng.shuffle(households)
    picked: list[Candidate] = []
    for household in households[:n]:
        options = by_household[household]
        least = min(configs[c.run.config] for c in options)
        choice = rng.choice(sorted((c for c in options if configs[c.run.config] == least), key=lambda c: c.run.run_id))
        picked.append(choice)
        used.add(household)
        configs[choice.run.config] += 1
    return picked


def draw_review_set(pool: Sequence[Candidate], rng: random.Random) -> list[Candidate]:
    used: set[str] = set()
    configs: Counter[str] = Counter()
    by_class = {cls: [c for c in pool if c.cls == cls] for cls in ("shows", "hides", "clean")}
    shows = draw(by_class["shows"], SHOW_TARGET, rng, used, configs)
    hides = draw(by_class["hides"], PROBLEM_DRAFTS - len(shows), rng, used, configs)
    if len(shows) + len(hides) < PROBLEM_DRAFTS:
        shows += draw(by_class["shows"], PROBLEM_DRAFTS - len(shows) - len(hides), rng, used, configs)
    clean = draw(by_class["clean"], CLEAN_DRAFTS, rng, used, configs)
    if len(shows) + len(hides) < PROBLEM_DRAFTS or len(clean) < CLEAN_DRAFTS:
        raise ExportError(
            f"the run gives {len(shows)} show, {len(hides)} hide and {len(clean)} clean drafts from separate households; "
            f"{PROBLEM_DRAFTS} problem and {CLEAN_DRAFTS} clean are needed"
        )
    chosen = shows + hides + clean
    rng.shuffle(chosen)
    return chosen


def demo_kind(c: Candidate) -> set[str]:
    kinds = {f["kind"] for f in c.flags}
    out = set()
    if "dropped" in kinds and (c.cls == "shows" or c.cls == "clean"):
        out.add("dropped")
    if "client_question" in kinds and "dropped" not in kinds:
        out.add("client_question")
    if c.cls == "clean" and not kinds & {"dropped", "client_question", "custom"}:
        out.add("clean")
    return out


def draw_demo_set(pool: Sequence[Candidate], rng: random.Random) -> list[Candidate]:
    """One dev draft for each demo kind, from three households: a dropped flag (one that points at a real miss
    when the run has one), a client question without a dropped flag, and a clean draft with neither."""
    used: set[str] = set()
    picked = []
    for kind in DEMO_KINDS:
        options = [c for c in pool if kind in demo_kind(c) and c.run.household_id not in used]
        if kind == "dropped" and any(c.cls == "shows" for c in options):
            options = [c for c in options if c.cls == "shows"]
        if not options:
            raise ExportError(f"no dev draft for the {kind} demo")
        choice = rng.choice(sorted(options, key=lambda c: c.run.run_id))
        used.add(choice.run.household_id)
        picked.append(choice)
    return picked


# Writing.


def key_entry(draft_id: str, c: Candidate) -> dict[str, Any]:
    shown = [p["shows_because"] for p in c.problems if p["shows_because"]]
    return {
        "draft_id": draft_id,
        "run_id": c.run.run_id,
        "household_id": c.run.household_id,
        "config": c.run.config,
        "scorer_pass": c.run.score["pass"],
        "required": c.run.score["required"],
        "missed": c.run.score["missed"],
        "unnecessary": c.run.score["unnecessary"],
        "wrong_facts": c.run.score["wrong_facts"],
        "problem_class": c.cls,
        "shows_because": sorted(set(shown)),
        "problems": c.problems,
    }


def write_folder(folder: Path, files: Mapping[str, str]) -> None:
    """Writes {name: text} into folder after removing the draft files a previous export left there."""
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.json"):
        if DRAFT_ID.fullmatch(old.stem) and old.is_file() and not old.is_symlink():
            old.unlink()
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8", newline="\n")


def review_records(reviews_dir: Path) -> list[str]:
    """Review records, and the server's log of openings, which exists once any draft has been opened."""
    if not reviews_dir.is_dir():
        return []
    return sorted(p.name for p in reviews_dir.iterdir() if p.suffix == ".json" or p.name == "openings.jsonl")


def export(
    runs_dir: Path = RUNS_DIR,
    profiles_dir: Path = PROFILES_DIR,
    drafts_dir: Path = DRAFTS_DIR,
    reviews_dir: Path = REVIEWS_DIR,
    demo_dir: Path = DEMO_DIR,
    key_path: Path = KEY_PATH,
    seed: int = SEED,
    ref: Reference | None = None,
) -> dict[str, Any]:
    records = review_records(reviews_dir)
    if records:
        raise ExportError(f"{reviews_dir} already holds {len(records)} review record(s); the drafts can't change now")
    ref = ref or Reference.load()

    test_runs, _ = load_runs(runs_dir, "test")
    commits = {r.score.get("prereg_commit") for r in test_runs}
    if len(commits) != 1 or None in commits:
        raise ExportError(f"the test runs don't share one prereg commit: {sorted(map(str, commits))}")
    test_pool, test_tally = candidates(test_runs, profiles_dir, ref)
    chosen = draw_review_set(test_pool, random.Random(seed))

    dev_runs, dev_unscored = load_runs(runs_dir, "dev")
    dev_pool, _ = candidates(dev_runs, profiles_dir, ref)
    demo = draw_demo_set(dev_pool, random.Random(seed))

    for c in chosen:
        check_submitted(c.run, ref, submitted_draft(runs_dir, c.run.run_id))
    drafts, entries = {}, []
    for i, c in enumerate(chosen, start=1):
        draft_id = f"d{i:02d}"
        draft = build_draft(draft_id, c.run, read_json(profiles_dir / f"{c.run.household_id}.json"), ref)
        drafts[f"{draft_id}.json"] = dump(draft)
        entries.append(key_entry(draft_id, c))
    demo_files = {}
    for i, c in enumerate(demo, start=1):
        draft_id = f"demo{i}"
        demo_files[f"{draft_id}.json"] = dump(build_draft(draft_id, c.run, read_json(profiles_dir / f"{c.run.household_id}.json"), ref))

    key = {
        "seed": seed,
        "source": SOURCE,
        "prereg_commit": commits.pop(),
        "class_rule": CLASS_RULE,
        "pool": dict(sorted(test_tally.items())),
        "drawn": dict(sorted(Counter(c.cls for c in chosen).items())),
        "drafts": entries,
    }
    write_folder(drafts_dir, drafts)
    write_folder(demo_dir, demo_files)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(dump(key), encoding="utf-8", newline="\n")
    return {
        "drafts": len(drafts),
        "demo": {f"demo{i}": (kind, c.run.run_id) for i, (kind, c) in enumerate(zip(DEMO_KINDS, demo), start=1)},
        "dev_unscored": len(dev_unscored),
        "test_runs": len(test_runs),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=Path, default=RUNS_DIR)
    parser.add_argument("--profiles", type=Path, default=PROFILES_DIR)
    parser.add_argument("--drafts", type=Path, default=DRAFTS_DIR)
    parser.add_argument("--reviews", type=Path, default=REVIEWS_DIR)
    parser.add_argument("--demo", type=Path, default=DEMO_DIR)
    parser.add_argument("--key", type=Path, default=KEY_PATH)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    try:
        out = export(args.runs, args.profiles, args.drafts, args.reviews, args.demo, args.key, args.seed)
    except ExportError as e:
        print(f"refused: {e}")
        return 1
    print(f"read {out['test_runs']} test runs; left out {out['dev_unscored']} dev runs with no score")
    print(f"wrote {out['drafts']} drafts to {args.drafts} and the answer key to {args.key} (not shown)")
    for draft_id, (kind, run_id) in out["demo"].items():
        print(f"wrote {draft_id} ({kind}) from {run_id} to {args.demo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
