"""Blind labels: Roanuk lists each household's documents from a plain description, then code compares.

    uv run python -m generator.labels sheet     writes data/labels/blind_label_sheet.md and the empty CSV
    uv run python -m generator.labels compare   prints every disagreement with the rules engine

The sheet describes 30 households (10 train, 20 dev, stratified by complexity)
from their true facts and what changed since last year. It never shows rule
output, a document list, or a form number, so the labels check the rules
rather than copy them. Within each stratum, households are picked greedily to
cover as many kinds of fact and change as possible, using facts only, so a
rule edit never changes which households get labeled. The sheet command
never overwrites a CSV that already holds labels.

compare reads data/labels/blind_labels.csv, where each documents cell is a
list separated by semicolons, such as "W-2 x2; 1099-INT; 1095-A". Each item
maps to the rules whose names appear in it as whole tokens, ignoring case and
keeping hyphens, so "1098-E" never matches the alias "1098". A rule's names
are its aliases, its document text, and the short label the profiles show, so
"Rental income and expense records" maps to its rule. The longest match wins.
A trailing "x2" means two. When names tie (1099-G, closing statement), the
count goes one at a time to the tied rules the engine still needs, then to
the first one it requires, and an ambiguous_item row shows where each went.
Those rows are notes, not disagreements. Items on the ignore list don't
count. Counts are compared only for counted rules (W-2s and K-1s), as in
scoring. Exit status is 1 when there is any disagreement.
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

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

SHEET_FILE = "blind_label_sheet.md"
CSV_FILE = "blind_labels.csv"
CSV_HEADER = ("household_id", "documents", "labeled_by", "notes")
# 10 train and 20 dev households. Dev is exactly proportional (6/9/5 of
# 12/18/10); train rounds 3/4.5/2.5 toward complex, which covers more rules.
LABEL_PLAN = {"train": {"simple": 3, "medium": 4, "complex": 3}, "dev": {"simple": 6, "medium": 9, "complex": 5}}
LABEL_SEED = 20261031
SHAPE_KEYS = ("filing_status", "dependents_count", "w2_employer_count")
FOLLOW_UP_KEYS = ("clean_energy_install_year", "efficiency_improvements_year", "ev_acquired_date")

TOKEN = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)*")
COUNT_SUFFIX = re.compile(r"\s+x\s*(\d+)\s*$", re.IGNORECASE)
# Rows that tell Roanuk how an item was read, without disagreeing with the rules.
NOTE_KINDS = ("ambiguous_item",)
TRAP_DOCUMENT = "nothing federal (credit ended)"

FILING_PLAIN = {
    "single": "single",
    "married_joint": "married, filing jointly",
    "married_separate": "married, filing separately",
    "head_of_household": "head of household",
    "qualifying_surviving_spouse": "qualifying surviving spouse",
}
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
CHANGE_NOUNS = {
    "new_dependent": "a dependent who joined the household",
    "self_employment": "freelance, gig, or small business work",
    "business_app_payments": "customer payments through an app or online marketplace",
    "interest_income": "interest earnings",
    "dividend_income": "dividend earnings",
    "sold_investments": "sales of stocks, bonds, or funds",
    "digital_asset_transactions": "cryptocurrency sales or trades",
    "retirement_distribution": "a retirement account withdrawal",
    "social_security": "Social Security benefits",
    "unemployment": "unemployment benefits",
    "itemized_last_year": "itemizing deductions",
    "state_refund_received": "a state income tax refund",
    "marketplace_coverage": "a health plan bought through HealthCare.gov or a state exchange",
    "hsa_withdrawal": "spending from a health savings account",
    "hsa_direct_contribution": "direct deposits into a health savings account",
    "has_mortgage": "a mortgage",
    "bought_home": "a home purchase",
    "sold_home": "a home sale",
    "student_loan_interest": "student loan interest",
    "tuition_paid": "college tuition payments",
    "dependent_care_paid": "paid child or dependent care",
    "rental_property": "a rental property",
    "estimated_payments_made": "tax payments sent to the IRS during the year",
    "gambling_winnings": "gambling winnings",
    "ip_pin_enrolled": "an IRS identity protection number",
    "charitable_giving": "gifts to charity",
    "tips_income": "tips at work",
    "overtime_pay": "overtime pay",
    "new_vehicle_loan": "interest on a new car loan",
    "clean_energy_installed": "a clean energy installation",
    "efficiency_improvements": "energy-saving home upgrades",
    "ev_purchased": "an electric vehicle purchase",
}
COUNT_CHANGES = {
    "dependents_count": "Dependents",
    "w2_employer_count": "Employers paying wages",
    "k1_count": "Shares of partnerships, small companies, estates, or trusts",
}


class LabelFileError(ValueError):
    """The labels CSV doesn't have the expected header."""


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
            lines.append(f"Installed solar panels or similar clean energy equipment; the work was finished in {f['clean_energy_install_year']}.")
        elif key == "efficiency_improvements" and f[key]:
            lines.append(f"Made energy-saving home upgrades, such as windows, insulation, or a heat pump, put in during {f['efficiency_improvements_year']}.")
        elif key == "ev_purchased" and f[key]:
            lines.append(f"Bought an electric vehicle; signed the contract and made a payment on {f['ev_acquired_date']}.")
    lines.append(
        "Itemized deductions on last year's return." if f["itemized_last_year"]
        else "Took the standard deduction on last year's return."
    )
    return lines


def describe_changes(prior: Mapping[str, Any], current: Mapping[str, Any], changed: Sequence[str],
                     specs: Mapping[str, FactSpec]) -> list[str]:
    lines = []
    for key in changed:
        if specs[key].is_follow_up:
            continue
        if key in COUNT_CHANGES:
            lines.append(f"{COUNT_CHANGES[key]}: {prior[key]} last year, {current[key]} this year.")
        elif key == "business_app_payments" and "self_employment" in changed:
            continue
        elif key == "filing_status":
            lines.append(f"Filing status: {FILING_PLAIN[prior[key]]} last year, {FILING_PLAIN[current[key]]} this year.")
        else:
            noun = CHANGE_NOUNS[key]
            if key == "self_employment" and prior["business_app_payments"] != current["business_app_payments"]:
                noun += ", with some customers paying through an app or online marketplace"
            lines.append(f"New this year: {noun}." if current[key] else f"Last year only: {noun}.")
    return lines or ["Nothing changed since last year."]


def household_section(truth: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> str:
    current, prior = truth["true_facts"], truth["prior_year_facts"]
    parts = [f"## {truth['household_id']} ({truth['split']}, {truth['complexity']})", "", "This year:", ""]
    parts += [f"- {line}" for line in describe_this_year(current, specs)]
    parts += ["", "Changed since last year:", ""]
    parts += [f"- {line}" for line in describe_changes(prior, current, truth["changed_facts"], specs)]
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
        "such as a photo ID or bank details. Put your name in labeled_by and anything worth noting in notes.",
        "",
        "Then run `uv run python -m generator.labels compare` to list every disagreement with the rules.",
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


def tokens(text: str) -> tuple[str, ...]:
    return tuple(TOKEN.findall(text.lower()))


def contains(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    n = len(needle)
    return n > 0 and any(tuple(haystack[i:i + n]) == tuple(needle) for i in range(len(haystack) - n + 1))


def match_names(rule: Rule) -> tuple[str, ...]:
    """Names that map a label item to a rule: its aliases, its document text, and the short label profiles show."""
    names = [*rule.aliases, rule.document or "", SHORT_LABELS.get(rule.id, "")]
    return tuple(dict.fromkeys(n for n in names if n))


def best_matches(name: str, rules: list[Rule]) -> tuple[str, ...]:
    """Rule IDs whose longest matching name is the longest of any rule, in rule order."""
    toks = tokens(name)
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


@dataclass(frozen=True)
class Disagreement:
    household_id: str
    kind: str
    rule_id: str
    document: str
    engine_count: int
    label_count: int
    item: str


def parse_documents(cell: str, rules: list[Rule], ignore_aliases: Sequence[str] = ()) -> list[LabelItem]:
    items = []
    for raw in cell.split(";"):
        text = raw.strip()
        if not text:
            continue
        m = COUNT_SUFFIX.search(text)
        count = int(m.group(1)) if m else 1
        name = text[:m.start()].strip() if m else text
        candidates = best_matches(name, rules)
        ignored = not candidates and any(contains(tokens(name), tokens(a)) for a in ignore_aliases)
        items.append(LabelItem(text, name, count, candidates, ignored))
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
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != CSV_HEADER:
            raise LabelFileError(f"{path}: header must be {','.join(CSV_HEADER)}, got {reader.fieldnames}")
        return [row for row in reader]


def compare_file(labels_path: Path, data_dir: Path = DATA_DIR) -> tuple[list[Disagreement], int]:
    """Every disagreement and note across the labeled rows, and how many rows had labels."""
    truths = load_truths(data_dir)
    rules, specs, ignore = load_rules(), load_facts(), load_ignore_aliases()
    out: list[Disagreement] = []
    labeled = 0
    for row in read_labels(labels_path):
        hid, cell = (row.get("household_id") or "").strip(), (row.get("documents") or "").strip()
        if not cell:
            continue
        labeled += 1
        if hid not in truths:
            out.append(Disagreement(hid, "unknown_household", "", "", 0, 0, cell))
            continue
        out.extend(compare_household(hid, cell, truths[hid], rules, specs, ignore))
    return out, labeled


TABLE_COLUMNS = ("household", "kind", "rule", "document", "rules say", "label says", "label item")


def format_table(rows: Sequence[Disagreement]) -> str:
    cells = [TABLE_COLUMNS] + [
        (d.household_id, d.kind, d.rule_id, d.document, str(d.engine_count), str(d.label_count), d.item)
        for d in rows
    ]
    cells = [tuple(c.replace("|", "/") for c in row) for row in cells]
    widths = [max(len(row[i]) for row in cells) for i in range(len(TABLE_COLUMNS))]

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
    args = parser.parse_args(argv)
    if args.command == "sheet":
        sheet_path, csv_path, wrote_csv = write_sheet(args.data)
        print(f"wrote {sheet_path}")
        print(f"wrote {csv_path}" if wrote_csv else f"kept {csv_path}: it already holds labels")
        return 0
    labels_path = args.labels or args.data / LABELS / CSV_FILE
    try:
        rows, labeled = compare_file(labels_path, args.data)
    except LabelFileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not labeled:
        print(f"no labels yet in {labels_path}")
        return 0
    disagreements = sum(1 for d in rows if is_disagreement(d))
    if rows:
        print(format_table(rows))
    notes = len(rows) - disagreements
    tail = f" and {notes} {'note' if notes == 1 else 'notes'}" if notes else ""
    print(f"{disagreements or 'no'} disagreements{tail} across {labeled} labeled households")
    return 1 if disagreements else 0


if __name__ == "__main__":
    sys.exit(main())
