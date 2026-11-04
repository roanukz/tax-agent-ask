"""Blind labels: Roanuk lists each household's documents from a plain description, then code compares.

    uv run python -m generator.labels sheet     writes data/labels/blind_label_sheet.md and the empty CSV
    uv run python -m generator.label_ui         a local page for entering the labels (see generator.label_ui)
    uv run python -m generator.labels compare   prints every disagreement with the rules engine

compare shows the rules' answers, and a label written after seeing them is no
longer blind. So compare runs only once the labels are on record: the first
time, it needs --reveal (or the label page's Compare button) and a label for
every household on the sheet, and it copies the labels file byte for byte to
<name>_before_compare.csv beside it (blind_labels_before_compare.csv for the
default file) before showing anything. After that, compare runs freely while
disagreements are settled.

The sheet describes 30 households (10 train, 20 dev, stratified by complexity)
from their true facts and what changed since last year. It never shows rule
output, a document list, or a form number, so the labels check the rules
rather than copy them. Within each stratum, households are picked greedily to
cover as many kinds of fact and change as possible, using facts only, so a
rule edit never changes which households get labeled. The sheet command
never overwrites a CSV that already holds labels. The lines on what changed
since last year come from sim/simulator.py, which gives the simulated client
the same lines.

compare reads data/labels/blind_labels.csv, where each documents cell is a
list separated by semicolons, such as "W-2 x2; 1099-INT; 1095-A", or "none"
when the preparer should ask for nothing. Each item
maps to the rules whose names appear in it as whole words, compared as the
scorer compares them (eval.aliases key and forms): case, a plural s, and
hyphens inside a word don't matter, so "W-2s" and "W2" read as "W-2", but
"1098-E" never matches the alias "1098". A rule's names are its aliases, its
document text, and the short label the profiles show, so "Rental income and
expense records" maps to its rule. The longest match wins. A count of one or
two digits can follow an item ("W-2 x2", "W-2 (x2)", "W-2 (2)") or come first
("2 W-2s", "two W-2s"); an item with neither counts once, and a longer number
such as "K-1 (1065)" stays part of the name. When names tie (1099-G, closing
statement), the count goes one at a time to the tied rules the engine still
needs, then to the first one it requires, and an ambiguous_item row shows
where each went. Those rows are notes, not disagreements. Items on the ignore
list don't count. Counts are compared only for counted rules (W-2s and K-1s),
as in scoring. Exit status is 1 when there is any disagreement.

On a counted rule, an item whose words hint at a count compare can't read is
still read with the count it found (one if none) and gets an ambiguous_count
row, a disagreement, so the label gets rewritten with a count. The hints are
a number or count word left in the name ("W-2 for two jobs", "W-2s from both
jobs", "W-2 (2nd job)") and, when no count was read, a plural of the rule's
name ("W-2s", "K-1 forms"). Other wording can still hide a count ("W-2 from my
jobs"), which is why the sheet asks for x2 or x3 after an item.

Each disagreement is settled in <name>_settlements.csv beside the labels file
(blind_labels_settlements.csv for the default file), one row per compare row,
matched on household, kind, rule, both counts, and the label item, so a rule
edit that changes a row leaves it unsettled again. compare shows how each row
was settled, and with --unsettled only the rows still to settle; its exit
status is then 1 only while a disagreement is unsettled.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Collection, Mapping, Sequence

from eval.aliases import forms, key, positions, tokenize
from generator.files import DATA_DIR, LABELS, load_truths, write_text
from generator.generate import SHORT_LABELS
from rules.engine import (
    STATUSES_FOR_SPLIT,
    FactSpec,
    Rule,
    as_multiset,
    load_facts,
    load_ignore_aliases,
    load_rules,
    required_documents,
    triggered_traps,
)
from sim.simulator import FILING_PLAIN, describe_changes

SHEET_FILE = "blind_label_sheet.md"
CSV_FILE = "blind_labels.csv"
SNAPSHOT_SUFFIX = "_before_compare"
SETTLEMENTS_SUFFIX = "_settlements"
CSV_HEADER = ("household_id", "documents", "labeled_by", "notes")
# A documents cell, or an item, that says the preparer should ask this household for nothing.
NOTHING = "none"
SETTLEMENT_HEADER = ("household_id", "kind", "rule_id", "rules_say", "label_says", "item", "maps_to", "settlement",
                     "note")
# How a disagreement was settled. changes_only is logged but not checked: the label left that document out
# because the fact behind it did not change, so nothing was compared against the rule's source.
SETTLEMENTS = {
    "same_document": "the label names the rule's document in other words",
    "rule_stands": "checked against the cited source: the rule is right and the label differs",
    "label_misread": "the label asks for something the household's facts don't call for",
    "changes_only": "a document, or part of a count, for a fact that did not change; the label lists only changes",
}
# 10 train and 20 dev households. Dev is exactly proportional (6/9/5 of
# 12/18/10); train rounds 3/4.5/2.5 toward complex, which covers more rules.
LABEL_PLAN = {"train": {"simple": 3, "medium": 4, "complex": 3}, "dev": {"simple": 6, "medium": 9, "complex": 5}}
LABEL_SEED = 20261031
SHAPE_KEYS = ("filing_status", "dependents_count", "w2_employer_count")
FOLLOW_UP_KEYS = ("clean_energy_install_year", "efficiency_improvements_year", "ev_acquired_date")

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10}
# A count after an item ("W-2 (x2)", "W-2 (2)", "W-2 x2"), tried in this order, else one before it ("2 W-2s",
# "two W-2s"). A count has one or two digits, so a form number or a year, as in "1098 from the lender" or
# "K-1 (1065)", is never a count.
COUNT_AFTER = (
    re.compile(r"\s*\(\s*x?\s*(\d{1,2})\s*\)\s*$", re.IGNORECASE),
    re.compile(r"\s+x\s*(\d{1,2})\s*$", re.IGNORECASE),
)
COUNT_BEFORE = re.compile(rf"^(\d{{1,2}}|{'|'.join(NUMBER_WORDS)})\s+(?=\S)", re.IGNORECASE)
# Words that say "more than one" without a number. "third" is left out because "third party" names a rule.
COUNT_HINTS = ("both", "couple", "pair", "several", "multiple", "few", "first", "second", "fourth", "fifth", "sixth",
               "seventh", "eighth", "ninth", "tenth")
# A word left in an item's name that reads like a count. No rule name holds one, so it is never part of a match.
COUNT_WORD = re.compile(
    rf"x?\d{{1,2}}|\d{{1,2}}x|\d{{1,2}}(?:st|nd|rd|th)|{'|'.join((*NUMBER_WORDS, *COUNT_HINTS))}", re.IGNORECASE)
# Rows that tell Roanuk how an item was read, without disagreeing with the rules.
NOTE_KINDS = ("ambiguous_item",)
TRAP_DOCUMENT = "nothing federal (credit ended)"

# One plain sentence per yes fact. No form numbers and no document names.
YES_LINES = {
    "self_employment": "Did freelance or gig work, or ran a small business of their own.",
    "business_app_payments": "Some customers paid for that work through a payment app or an online marketplace.",
    "interest_income": "Earned interest from a bank, credit union, or bonds.",
    "dividend_income": "Received dividends from stocks or mutual funds.",
    "sold_investments": "Sold some stocks, bonds, or mutual funds.",
    "digital_asset_transactions": "Sold or traded cryptocurrency.",
    "retirement_distribution": "Took money out of a retirement account.",
    "social_security": "Received Social Security benefits.",
    "unemployment": "Received unemployment benefits.",
    "state_refund_received": "Got a state income tax refund this year.",
    "marketplace_coverage": "Bought health insurance through HealthCare.gov or a state exchange.",
    "hsa_withdrawal": "Spent or withdrew money from a health savings account.",
    "hsa_direct_contribution": "Put money into a health savings account directly, outside their paycheck.",
    "has_mortgage": "Has a mortgage on their home.",
    "bought_home": "Bought a home this year.",
    "sold_home": "Sold a home this year.",
    "student_loan_interest": "Paid interest on a student loan.",
    "tuition_paid": "Paid college tuition or fees for themselves or someone they support.",
    "dependent_care_paid": "Paid for child care or other dependent care so they could work.",
    "rental_property": "Rented out a property.",
    "estimated_payments_made": "Sent the IRS tax payments during the year, apart from paycheck withholding.",
    "gambling_winnings": "Won money gambling.",
    "ip_pin_enrolled": "The IRS gives them a six-digit identity protection number each year.",
    "charitable_giving": "Gave money or goods to charity.",
    "tips_income": "Earned tips at work.",
    "overtime_pay": "Was paid overtime at work.",
    "new_vehicle_loan": "Pays interest on a loan for a new car bought for personal use.",
}


class LabelFileError(ValueError):
    """The labels CSV can't be read safely: a wrong header, a row with more cells than the header, or not UTF-8."""


class RevealRefused(ValueError):
    """compare would show the rules' answers before the blind labels are complete or on record."""


# The sheet.


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {plural or word + 's'}"


def describe_this_year(f: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> list[str]:
    deps = f["dependents_count"]
    lines = [f"Filing status: {FILING_PLAIN[f['filing_status']]}."]
    if f["new_dependent"]:
        lines.append(f"Dependents: {deps}, one of them new this year (a birth, an adoption, or someone newly supported).")
    else:
        lines.append(f"Dependents: {deps}." if deps else "Dependents: none.")
    jobs = f["w2_employer_count"]
    lines.append(
        f"Paid wages by {_plural(jobs, 'employer')}, counting any job that ended during the year."
        if jobs else "No wages from an employer."
    )
    for key in specs:
        if key in YES_LINES and f[key]:
            lines.append(YES_LINES[key])
        elif key == "k1_count" and f[key]:
            lines.append(f"Owns a share of {_plural(f[key], 'partnership, small company, estate, or trust', 'partnerships, small companies, estates, or trusts')}.")
        elif key == "clean_energy_installed" and f[key]:
            lines.append(f"Installed solar panels or similar clean energy equipment on the home they live in; the work was finished in {f['clean_energy_install_year']}.")
        elif key == "efficiency_improvements" and f[key]:
            lines.append(f"Made energy-saving upgrades to the home they live in, such as windows, insulation, or a heat pump, put in during {f['efficiency_improvements_year']}.")
        elif key == "ev_purchased" and f[key]:
            lines.append(f"Bought an electric vehicle; signed the contract and made a payment on {f['ev_acquired_date']}.")
    lines.append(
        "Itemized deductions on last year's return." if f["itemized_last_year"]
        else "Took the standard deduction on last year's return."
    )
    return lines


def household_description(truth: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> dict[str, Any]:
    """What the sheet shows for one household, and all the label page shows: its ID, split, tier, and plain lines."""
    current, prior = truth["true_facts"], truth["prior_year_facts"]
    return {
        "household_id": truth["household_id"],
        "split": truth["split"],
        "complexity": truth["complexity"],
        "this_year": describe_this_year(current, specs),
        "changed": describe_changes(prior, current, truth["changed_facts"], specs),
    }


def household_section(truth: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> str:
    d = household_description(truth, specs)
    parts = [f"## {d['household_id']} ({d['split']}, {d['complexity']})", "", "This year:", ""]
    parts += [f"- {line}" for line in d["this_year"]]
    parts += ["", "Changed since last year:", ""]
    parts += [f"- {line}" for line in d["changed"]]
    return "\n".join(parts) + "\n"


def coverage_items(truth: Mapping[str, Any]) -> set[str]:
    """What a household adds to the labeled set: its yes facts, follow-up years, changes, and basic shape.

    Facts only, never rule output, so editing a rule can't change which households get labeled.
    """
    facts = truth["true_facts"]
    items = {
        f"filing:{facts['filing_status']}",
        f"jobs:{min(facts['w2_employer_count'], 3)}",
        f"dependents:{min(facts['dependents_count'], 2)}",
    }
    for key, value in facts.items():
        if key in FOLLOW_UP_KEYS and value is not None:
            items.add(f"{key}:{str(value)[:4]}")
        elif key not in SHAPE_KEYS and value not in (None, False, 0):
            items.add(f"fact:{key}")
    if facts["state_refund_received"] and facts["itemized_last_year"]:
        items.add("both:state_refund_received+itemized_last_year")
    return items | {f"changed:{k}" for k in truth["changed_facts"]}


def select_households(truths: Mapping[str, Mapping[str, Any]], plan: Mapping[str, Mapping[str, int]] = LABEL_PLAN,
                      seed: int = LABEL_SEED) -> list[str]:
    rng = random.Random(seed)
    items = {h: coverage_items(t) for h, t in truths.items()}
    covered: set[str] = set()
    chosen: list[str] = []
    for split, tiers in plan.items():
        for tier, k in tiers.items():
            pool = sorted(h for h, t in truths.items() if t["split"] == split and t["complexity"] == tier)
            rng.shuffle(pool)
            if len(pool) < k:
                raise ValueError(f"{split} {tier}: need {k} households, have {len(pool)}")
            local: set[str] = set()
            for _ in range(k):
                best = max(pool, key=lambda h: (len(items[h] - covered), len(items[h] - local)))
                pool.remove(best)
                chosen.append(best)
                covered |= items[best]
                local |= items[best]
    return sorted(chosen)


def build_sheet(truths: Mapping[str, Mapping[str, Any]], specs: Mapping[str, FactSpec]) -> str:
    chosen = select_households(truths)
    counts = Counter(truths[h]["split"] for h in chosen)
    intro = [
        "# Blind label sheet",
        "",
        f"{len(chosen)} synthetic households ({counts['train']} train, {counts['dev']} dev, stratified by complexity) "
        "for checking the rules by hand. Each description comes from the household's true facts and what changed "
        "since last year. Nothing the rules produce appears here, on purpose.",
        "",
        "How to label: for each household, write every item the preparer should ask the client for, for the 2026 "
        "federal return only, in the documents column of data/labels/blind_labels.csv, one row per household. "
        "Separate items with semicolons, and add x2 or x3 after an item when the household needs more than one of "
        "the same kind. Use whatever short name you would put on a request list. Leave out what every client brings, "
        "such as a photo ID or bank details, and write none if the preparer should ask for nothing. Put your name in "
        "labeled_by and anything worth noting in notes.",
        "",
        "The label page does the same with a box per item and a count box: `uv run python -m generator.label_ui`.",
        "",
        "When every household has a label, compare them with the rules, with the label page's Compare button or "
        "`uv run python -m generator.labels compare --reveal`. Either first copies the labels to "
        "data/labels/blind_labels_before_compare.csv, since labels written after seeing the rules' answers are no "
        "longer blind.",
        "",
        "Not tax advice. All households are synthetic.",
        "",
    ]
    return "\n".join(intro) + "\n" + "\n".join(household_section(truths[h], specs) for h in chosen)


def csv_has_labels(path: Path) -> bool:
    if not path.exists():
        return False
    with open(path, newline="", encoding="utf-8") as f:
        return any(row and any(cell.strip() for cell in row) for row in list(csv.reader(f))[1:])


def write_sheet(data_dir: Path = DATA_DIR) -> tuple[Path, Path, bool]:
    """Writes the sheet and, unless it already holds labels, the CSV header. Returns whether the CSV was written."""
    truths = load_truths(data_dir)
    sheet = data_dir / LABELS / SHEET_FILE
    write_text(sheet, build_sheet(truths, load_facts()))
    labels = data_dir / LABELS / CSV_FILE
    if csv_has_labels(labels):
        return sheet, labels, False
    write_text(labels, ",".join(CSV_HEADER) + "\n")
    return sheet, labels, True


# Compare.


def words(text: str) -> list[str]:
    """The words of text as eval.aliases splits them, without the punctuation between them."""
    return [w for w in tokenize(text) if w is not None]


def tokens(text: str) -> tuple[str, ...]:
    """A rule name's words as eval.aliases keys: casefolded, a plural s dropped, then hyphens dropped."""
    return tuple(key(w) for w in words(text))


def item_forms(text: str) -> tuple[frozenset[str], ...]:
    """A label item's words as the keys each can match (eval.aliases forms), so "1099S" can read as 1099-S."""
    return tuple(forms(w) for w in words(text))


def contains(haystack: Sequence[Collection[str]], needle: Sequence[str]) -> bool:
    """Whether needle's keys occur, in order and side by side, among the haystack's forms."""
    return bool(needle) and bool(positions(needle, haystack))


def match_names(rule: Rule) -> tuple[str, ...]:
    """Names that map a label item to a rule: its aliases, its document text, and the short label profiles show."""
    names = [*rule.aliases, rule.document or "", SHORT_LABELS.get(rule.id, "")]
    return tuple(dict.fromkeys(n for n in names if n))


def best_matches(name: str, rules: list[Rule]) -> tuple[str, ...]:
    """Rule IDs whose longest matching name is the longest of any rule, in rule order."""
    toks = item_forms(name)
    scores = {}
    for rule in rules:
        score = max((len(tokens(a)) for a in match_names(rule) if contains(toks, tokens(a))), default=0)
        if score:
            scores[rule.id] = score
    if not scores:
        return ()
    top = max(scores.values())
    return tuple(rid for rid, s in scores.items() if s == top)


@dataclass(frozen=True)
class LabelItem:
    text: str
    name: str
    count: int
    candidates: tuple[str, ...]
    ignored: bool
    # Words that hint at a count compare couldn't read: count-like words left in the name, such as "two" in
    # "W-2 for two jobs", and with no count read, a plural of a counted rule's name, such as "W-2s".
    unread_counts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Disagreement:
    household_id: str
    kind: str
    rule_id: str
    document: str
    engine_count: int
    label_count: int
    item: str


def read_count(text: str) -> tuple[str, int | None]:
    """An item's name and count: a count after the name, else one before it, else None for no count written."""
    for pattern in COUNT_AFTER:
        m = pattern.search(text)
        if m:
            return text[:m.start()].strip(), int(m.group(1))
    m = COUNT_BEFORE.match(text)
    if m:
        n = m.group(1).casefold()
        return text[m.end():].strip(), NUMBER_WORDS[n] if n in NUMBER_WORDS else int(n)
    return text, None


def is_plural(word: str) -> bool:
    """Whether eval.aliases key reads the word as a plural, dropping its s."""
    return key(word) != word.casefold().replace("-", "")


def counted_plurals(name: str, candidates: Collection[str], rules: list[Rule]) -> tuple[str, ...]:
    """Plural words in an item that spell a counted candidate rule's name, such as "W-2s" for the W-2 rule."""
    keys = {k for r in rules if r.id in candidates and r.count_from for n in match_names(r) for k in tokens(n)}
    return tuple(w for w in words(name) if is_plural(w) and key(w) in keys)


def parse_documents(cell: str, rules: list[Rule], ignore_aliases: Sequence[str] = ()) -> list[LabelItem]:
    items = []
    for raw in cell.split(";"):
        text = raw.strip()
        if not text or text.casefold() == NOTHING:
            continue
        name, count = read_count(text)
        candidates = best_matches(name, rules)
        ignored = not candidates and any(contains(item_forms(name), tokens(a)) for a in ignore_aliases)
        unread = tuple(w for w in words(name) if COUNT_WORD.fullmatch(w))
        if count is None:
            unread += counted_plurals(name, candidates, rules)
        items.append(LabelItem(text, name, 1 if count is None else count, candidates, ignored, unread))
    return items


def _pick(candidates: Sequence[str], required: Counter[str], labeled: Counter[str], traps: Sequence[str]) -> str:
    still_needed = [c for c in candidates if labeled[c] < required[c]]
    expected = [c for c in candidates if required[c] or c in traps]
    return (still_needed or expected or list(candidates))[0]


def credit(candidates: Sequence[str], count: int, required: Counter[str], labeled: Counter[str],
           traps: Sequence[str]) -> Counter[str]:
    """Where an item's count goes: one at a time, so a tied item covers each tied rule still needed before stacking."""
    got: Counter[str] = Counter()
    for _ in range(count):
        got[_pick(candidates, required, labeled + got, traps)] += 1
    return got


def is_disagreement(row: Disagreement) -> bool:
    return row.kind not in NOTE_KINDS


def compare_household(household_id: str, cell: str, truth: Mapping[str, Any], rules: list[Rule],
                      specs: Mapping[str, FactSpec], ignore_aliases: Sequence[str] = ()) -> list[Disagreement]:
    statuses = STATUSES_FOR_SPLIT[truth["split"]]
    facts = truth["true_facts"]
    required = as_multiset(required_documents(facts, rules, specs, statuses=statuses))
    traps = triggered_traps(facts, rules, specs, statuses=statuses)
    by_id = {r.id: r for r in rules}
    labeled: Counter[str] = Counter()
    texts: dict[str, list[str]] = {}
    out = []
    for item in parse_documents(cell, rules, ignore_aliases):
        if not item.candidates:
            if not item.ignored:
                out.append(Disagreement(household_id, "unmatched_item", "", "", 0, item.count, item.text))
            continue
        for rid, n in credit(item.candidates, item.count, required, labeled, traps).items():
            rule = by_id[rid]
            if len(item.candidates) > 1:
                note = f"{item.text} (matches {', '.join(item.candidates)})"
                out.append(Disagreement(household_id, "ambiguous_item", rid, rule.document or TRAP_DOCUMENT,
                                        required[rid], n, note))
            if item.unread_counts and rule.count_from:
                note = f"{item.text} (count not read: {', '.join(item.unread_counts)})"
                out.append(Disagreement(household_id, "ambiguous_count", rid, rule.document or TRAP_DOCUMENT,
                                        required[rid], n, note))
            if rule.trap:
                out.append(Disagreement(household_id, "trap_in_label", rid, TRAP_DOCUMENT, 0, n, item.text))
                continue
            labeled[rid] += n
            texts.setdefault(rid, []).append(item.text)
    for rid in sorted(set(required) | set(labeled)):
        rule, want, got = by_id[rid], required[rid], labeled[rid]
        if want and not got:
            kind = "missing_from_label"
        elif got and not want:
            kind = "extra_in_label"
        elif rule.count_from and want != got:
            kind = "count_differs"
        else:
            continue
        out.append(Disagreement(household_id, kind, rid, rule.document or "", want, got, "; ".join(texts.get(rid, []))))
    return sorted(out, key=lambda d: (d.rule_id == "", d.rule_id, d.kind, d.item))


def read_labels(path: Path) -> list[dict[str, str]]:
    """The rows of a labels CSV. A missing trailing cell reads as empty; an extra cell is an error, since a
    rewrite would drop it."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if tuple(reader.fieldnames or ()) != CSV_HEADER:
                raise LabelFileError(f"{path}: header must be {','.join(CSV_HEADER)}, got {reader.fieldnames}")
            rows = []
            for row in reader:
                if None in row:
                    raise LabelFileError(f"{path}: line {reader.line_num} has more than {len(CSV_HEADER)} cells; "
                                         "put quotes around any cell that holds a comma")
                rows.append({k: v or "" for k, v in row.items()})
            return rows
    except UnicodeDecodeError:
        raise LabelFileError(f"{path} isn't UTF-8 text; save it as UTF-8") from None


def has_items(cell: str) -> bool:
    """Whether a documents cell names at least one item or says none, so a cell of only semicolons is no label."""
    return any(piece.strip() for piece in cell.split(";"))


def snapshot_path(labels_path: Path) -> Path:
    """Where the labels are kept as they were before compare first showed the rules' answers."""
    labels_path = Path(labels_path)
    return labels_path.with_name(f"{labels_path.stem}{SNAPSHOT_SUFFIX}{labels_path.suffix}")


def is_snapshot(path: Path) -> bool:
    """Whether path is a snapshot itself, which nothing may open as a labels file to edit or reveal."""
    return Path(path).stem.endswith(SNAPSHOT_SUFFIX)


def labeled_households(labels_path: Path) -> set[str]:
    return {row["household_id"].strip() for row in read_labels(labels_path) if has_items(row["documents"])}


def reveal(labels_path: Path, households: Sequence[str]) -> Path:
    """Copies the labels file byte for byte to its snapshot, once, if every household on the sheet has a label.

    The copy is written to a temp file and linked into place, so the snapshot is never partial and an existing
    one is never replaced. Returns the snapshot path; raises RevealRefused when a household has no label.
    """
    labels_path = Path(labels_path)
    snapshot = snapshot_path(labels_path)
    if snapshot.exists():
        return snapshot
    done = labeled_households(labels_path)
    missing = [h for h in households if h not in done]
    if missing:
        raise RevealRefused(f"{len(households) - len(missing)} of {len(households)} households have labels "
                            f"(none yet for {', '.join(missing[:5])}{', ...' if len(missing) > 5 else ''}). "
                            "Label every household before comparing.")
    data = labels_path.read_bytes()
    fd, tmp = tempfile.mkstemp(dir=snapshot.parent, prefix=f".{snapshot.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, snapshot)
        except FileExistsError:
            pass
    finally:
        Path(tmp).unlink(missing_ok=True)
    return snapshot


def compare_file(labels_path: Path, data_dir: Path = DATA_DIR) -> tuple[list[Disagreement], int]:
    """Every disagreement and note across the labeled rows, and how many rows had labels."""
    truths = load_truths(data_dir)
    rules, specs, ignore = load_rules(), load_facts(), load_ignore_aliases()
    out: list[Disagreement] = []
    labeled = 0
    for row in read_labels(labels_path):
        hid, cell = (row.get("household_id") or "").strip(), (row.get("documents") or "").strip()
        if not has_items(cell):
            continue
        labeled += 1
        if hid not in truths:
            out.append(Disagreement(hid, "unknown_household", "", "", 0, 0, cell))
            continue
        out.extend(compare_household(hid, cell, truths[hid], rules, specs, ignore))
    return out, labeled


# Settlements.

SettlementKey = tuple[str, str, str, int, int, str]


def settlements_path(labels_path: Path) -> Path:
    """Where each disagreement with a labels file is settled."""
    labels_path = Path(labels_path)
    return labels_path.with_name(f"{labels_path.stem}{SETTLEMENTS_SUFFIX}{labels_path.suffix}")


def settlement_key(d: Disagreement) -> SettlementKey:
    return (d.household_id, d.kind, d.rule_id, d.engine_count, d.label_count, d.item)


def read_settlements(path: Path, rule_ids: Collection[str] = ()) -> dict[SettlementKey, dict[str, str]]:
    """The settlements file's rows by the compare row each settles; none when the file doesn't exist.

    Every row needs a known settlement and a note, and maps_to, when given, names a rule in rule_ids or says none.
    """
    path = Path(path)
    if not path.exists():
        return {}
    out: dict[SettlementKey, dict[str, str]] = {}
    try:
        # utf-8-sig drops the byte order mark a spreadsheet's "CSV UTF-8" save puts first.
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if tuple(reader.fieldnames or ()) != SETTLEMENT_HEADER:
                raise LabelFileError(f"{path}: header must be {','.join(SETTLEMENT_HEADER)}, got {reader.fieldnames}")
            for row in reader:
                where = f"{path}: line {reader.line_num}"
                if None in row:
                    raise LabelFileError(f"{where} has more than {len(SETTLEMENT_HEADER)} cells")
                row = {k: (v or "").strip() for k, v in row.items()}
                if row["settlement"] not in SETTLEMENTS:
                    raise LabelFileError(f"{where}: settlement must be one of {', '.join(SETTLEMENTS)}")
                if not row["note"]:
                    raise LabelFileError(f"{where}: every settlement needs a note saying why")
                if row["maps_to"] and rule_ids and row["maps_to"] not in {*rule_ids, NOTHING}:
                    raise LabelFileError(f"{where}: maps_to {row['maps_to']} is not a rule ID or {NOTHING}")
                try:
                    k = (row["household_id"], row["kind"], row["rule_id"], int(row["rules_say"]),
                         int(row["label_says"]), row["item"])
                except ValueError:
                    raise LabelFileError(f"{where}: rules_say and label_says must be whole numbers") from None
                if k in out:
                    raise LabelFileError(f"{where} settles the same compare row as an earlier line")
                out[k] = row
    except UnicodeDecodeError:
        raise LabelFileError(f"{path} isn't UTF-8 text; save it as UTF-8") from None
    return out


@dataclass(frozen=True)
class Settled:
    settled: tuple[tuple[Disagreement, Mapping[str, str]], ...]
    unsettled: tuple[Disagreement, ...]
    # Settlement rows that match no current disagreement, such as after a rule edit changed the compare row.
    stale: tuple[Mapping[str, str], ...]

    def by_kind(self) -> Counter[str]:
        return Counter(s["settlement"] for _, s in self.settled)


def settle(rows: Sequence[Disagreement], settlements: Mapping[SettlementKey, Mapping[str, str]]) -> Settled:
    """Pairs each disagreement with its settlement. Notes need none, so a settlement for one is stale."""
    done, open_rows, used = [], [], set()
    for d in rows:
        if not is_disagreement(d):
            continue
        k = settlement_key(d)
        if k in settlements:
            done.append((d, settlements[k]))
            used.add(k)
        else:
            open_rows.append(d)
    stale = tuple(s for k, s in settlements.items() if k not in used)
    return Settled(tuple(done), tuple(open_rows), stale)


def settlement_summary(s: Settled) -> str:
    """One line on how the disagreements were settled, such as "170 settled (52 same_document, ...), 6 unsettled"."""
    kinds = s.by_kind()
    parts = [f"{n} {k}" for k in SETTLEMENTS if (n := kinds[k])]
    text = f"{len(s.settled)} settled" + (f" ({', '.join(parts)})" if parts else "") + f", {len(s.unsettled)} unsettled"
    return text + (f", and {len(s.stale)} stale settlement {'row' if len(s.stale) == 1 else 'rows'}" if s.stale else "")


TABLE_COLUMNS = ("household", "kind", "rule", "document", "rules say", "label says", "label item")


def format_table(rows: Sequence[Disagreement], settled_as: Mapping[SettlementKey, str] | None = None) -> str:
    """The rows as a text table, with a settled-as column when settled_as is given."""
    columns = TABLE_COLUMNS + (("settled as",) if settled_as is not None else ())
    cells = [columns] + [
        (d.household_id, d.kind, d.rule_id, d.document, str(d.engine_count), str(d.label_count), d.item)
        + ((settled_as.get(settlement_key(d), "" if not is_disagreement(d) else "unsettled"),)
           if settled_as is not None else ())
        for d in rows
    ]
    cells = [tuple(c.replace("|", "/") for c in row) for row in cells]
    widths = [max(len(row[i]) for row in cells) for i in range(len(columns))]

    def line(row: Sequence[str]) -> str:
        return "| " + " | ".join(c.ljust(w) for c, w in zip(row, widths)) + " |"

    rule = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    return "\n".join([line(cells[0]), rule] + [line(row) for row in cells[1:]])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sheet = sub.add_parser("sheet", help="write the blind label sheet and the empty labels CSV")
    sheet.add_argument("--data", type=Path, default=DATA_DIR)
    comp = sub.add_parser("compare", help="print every disagreement between the labels and the rules")
    comp.add_argument("--data", type=Path, default=DATA_DIR)
    comp.add_argument("--labels", type=Path, default=None, help="labels CSV (default data/labels/blind_labels.csv)")
    comp.add_argument("--reveal", action="store_true",
                      help="the first time: copy the labels to their snapshot, then show the rules' answers")
    comp.add_argument("--unsettled", action="store_true",
                      help="show only the disagreements the settlements file does not settle yet")
    args = parser.parse_args(argv)
    if args.command == "sheet":
        sheet_path, csv_path, wrote_csv = write_sheet(args.data)
        print(f"wrote {sheet_path}")
        print(f"wrote {csv_path}" if wrote_csv else f"kept {csv_path}: it already holds labels")
        return 0
    labels_path = args.labels or args.data / LABELS / CSV_FILE
    if is_snapshot(labels_path):
        print(f"error: {labels_path} is the record of the blind labels; compare the labels file it was copied from",
              file=sys.stderr)
        return 2
    try:
        if not snapshot_path(labels_path).exists():
            sheet = select_households(load_truths(args.data))
            if not args.reveal:
                done = len(labeled_households(labels_path) & set(sheet)) if labels_path.exists() else 0
                print(f"{done} of {len(sheet)} households have labels. compare shows the rules' answers, and labels "
                      "written after seeing them aren't blind. Once every household has a label, run compare "
                      f"--reveal (or the label page's Compare button): it first copies {labels_path} to "
                      f"{snapshot_path(labels_path)}.", file=sys.stderr)
                return 2
            if not labels_path.exists():
                raise RevealRefused(f"{labels_path} does not exist")
            print(f"kept the blind labels in {reveal(labels_path, sheet)}")
        rows, labeled = compare_file(labels_path, args.data)
        settlements_file = settlements_path(labels_path)
        settled = settle(rows, read_settlements(settlements_file, {r.id for r in load_rules()}))
    except (LabelFileError, RevealRefused) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not labeled:
        print(f"no labels yet in {labels_path}")
        return 0
    disagreements = sum(1 for d in rows if is_disagreement(d))
    shown = list(settled.unsettled) if args.unsettled else rows
    settled_as = {settlement_key(d): s["settlement"] for d, s in settled.settled} if settlements_file.exists() else None
    if shown:
        print(format_table(shown, settled_as))
    notes = len(rows) - disagreements
    tail = f" and {notes} {'note' if notes == 1 else 'notes'}" if notes else ""
    print(f"{disagreements or 'no'} disagreements{tail} across {labeled} labeled households")
    if settlements_file.exists():
        print(f"{settlement_summary(settled)} in {settlements_file}")
    return 1 if settled.unsettled else 0


if __name__ == "__main__":
    sys.exit(main())
