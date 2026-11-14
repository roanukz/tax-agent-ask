"""The teardown page, docs/index.html: its shape (the portfolio teardown format) and the numbers it quotes.

The front of the page is layered: the lede says why to care, the TL;DR says what was found, the Summary argues
it in scientific method order, and the numbered parts show it. Each layer's length and shape is held here. The
numbers the page draws from the data are recomputed here from the data, the rules, the labels, the review records,
the generated report or the decision log, so the page cannot drift from what it reports. These tests read files only.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from agent.env import load_settings
from eval import review_catch
from eval.baselines import run_prior_year_split
from generator import labels
from generator.files import DATA_DIR, load_truths
from preparer.drafts import Reference
from preparer.reviews import parse_stamp
from rules.engine import STATUSES_FOR_SPLIT, load_facts, load_ignore_aliases, load_rules, triggered_traps
from server.tax_server import TOOLS

REPO = Path(__file__).resolve().parent.parent
PAGE_PATH = REPO / "docs" / "index.html"
SOURCE = "https://github.com/Roanukz/tax-agent-ask"
DASHES = (chr(0x2014), chr(0x2013))
SUMMARY_LEADS = [
    "The problem.",
    "The thesis.",
    "The method.",
    "The results.",
    "What it means.",
    "Cautions, and how to check.",
]
BYLINE = "Roanuk Zaman · Product teardown of a tool I designed and directed"
NUMBER_WORDS = {8: "eight", 9: "nine", 10: "ten"}
# Attributes whose text a reader sees or hears.
VISIBLE_ATTRS = ("alt", "title", "aria-label", "placeholder", "value")


def page() -> str:
    return PAGE_PATH.read_text(encoding="utf-8")


def text_of(fragment: str) -> str:
    plain = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", plain).strip()


def sentences(plain: str) -> list[str]:
    return [s for s in re.split(r'(?<=[.!?])\s+(?=[A-Z"])', plain) if s]


def section(section_id: str) -> str:
    match = re.search(rf'<section id="{section_id}"[\s\S]*?</section>', page())
    assert match is not None, f"section #{section_id} is missing"
    return match.group(0)


def page_text() -> str:
    return text_of(page())


# ---- the shape ---------------------------------------------------------------------


def test_title_and_hero_order():
    text = page()
    assert "<title>What Should My Tax Agent Ask? A Product Teardown</title>" in text
    hero = re.search(r'<header class="hero">([\s\S]*?)</header>', text)
    assert hero is not None
    order = ['<p class="eyebrow">Product teardown</p>', "<h1>What Should My Tax Agent Ask?</h1>", '<p class="lede">',
             '<p class="byline">', 'id="status"', '<div class="cta-row">', '<div class="facts">']
    positions = [hero.group(1).find(marker) for marker in order]
    assert -1 not in positions, dict(zip(order, positions))
    assert positions == sorted(positions)
    buttons = re.findall(r'<a class="btn [^"]+" href="([^"]+)">([^<]+)</a>', hero.group(1))
    assert buttons == [("demo.html", "Walk through it in five minutes"),
                       ("#results", "See the results"),
                       (SOURCE, "View the source")]
    assert len(re.findall(r'<div class="fact">', hero.group(1))) == 4


def test_lede_is_two_or_three_sentences_and_the_byline_is_fixed():
    lede = re.search(r'<p class="lede">([\s\S]*?)</p>', page())
    byline = re.search(r'<p class="byline">([\s\S]*?)</p>', page())
    assert lede is not None and byline is not None
    assert 2 <= len(sentences(text_of(lede.group(1)))) <= 3
    assert text_of(byline.group(1)) == BYLINE


def test_tldr_is_five_sentences_100_words_one_number_and_no_links():
    brief = section("tldr")
    assert '<p class="section-kicker">Thirty seconds</p>' in brief and "<h2>TL;DR</h2>" in brief
    assert '<p class="brief-definition">Too long; didn\'t read. The whole page in five sentences.</p>' in brief
    body = re.search(r'<p class="brief-body">([\s\S]*?)</p>', brief)
    assert body is not None
    plain = text_of(body.group(1))
    assert len(sentences(plain)) <= 5 and len(plain.split(" ")) <= 100
    assert "<a " not in body.group(1) and len(re.findall(r"\d+", plain)) <= 1


def test_summary_has_the_six_leads_in_order_and_comes_first():
    summary = section("summary")
    assert '<p class="section-kicker">Three minutes</p>' in summary
    assert [m.strip() for m in re.findall(r"<strong>([^<]*)</strong>", summary)] == SUMMARY_LEADS
    first = re.search(r'<main id="main"[^>]*>\s*<section id="([^"]+)"', page())
    assert first is not None and first.group(1) == "summary"


def test_the_contents_rail_matches_the_sections():
    text = page()
    nav = re.search(r'<nav class="toc"[\s\S]*?</nav>', text)
    assert nav is not None
    rail = [(href, text_of(label)) for href, label in re.findall(r'<a href="#([^"]+)">([\s\S]*?)</a>', nav.group(0))]
    found = re.findall(r'<section id="([^"]+)"[^>]*>\s*<p class="section-kicker">([^<]*)</p>\s*<h2>([^<]*)</h2>', text)
    assert len(found) == len(re.findall(r"<section id=", text)), "a section lacks a kicker and heading"
    expected = []
    for section_id, kicker, heading in found:
        kicker, heading = html.unescape(kicker), html.unescape(heading)
        if section_id in ("tldr", "summary"):
            expected.append((section_id, heading))
        else:
            assert re.fullmatch(r"Part [1-9]\d*|Appendix [A-Z]", kicker), kicker
            expected.append((section_id, f"{kicker}. {heading}"))
    assert rail[:2] == [("tldr", "TL;DR"), ("summary", "Summary")] and rail == expected
    parts = [int(k[5:]) for _, k, _ in found if k.startswith("Part ")]
    assert parts == list(range(1, len(parts) + 1))


class VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        values = dict(attrs)
        self.chunks += [values[a] or "" for a in VISIBLE_ATTRS if a in values]
        if tag == "meta" and (values.get("name") == "description" or (values.get("property") or "").startswith("og:")):
            self.chunks.append(values.get("content") or "")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.chunks.append(data)


def test_no_em_or_en_dash_reaches_the_reader_even_as_an_entity():
    parser = VisibleText()
    parser.feed(page())
    found = [c.strip() for c in parser.chunks if any(d in c for d in DASHES)]
    assert not found, found


def test_terms_are_explained_where_they_first_appear():
    text = page_text()
    for term, explanation in (("MCP", "the Model Context Protocol, an open standard"),
                              ("harness", "the code that runs the agent"),
                              ("hook", "code that runs before each tool call"),
                              ("95% interval", "the range the true rate probably falls in"),
                              ("preregistration", "a file that fixes the bars"),
                              ("mutation testing", "plants small bugs")):
        found = re.search(rf"\b{re.escape(term)}\b", text, flags=re.IGNORECASE)
        assert found is not None, term
        assert explanation in text[found.start():found.start() + 220], (term, text[found.start():found.start() + 220])
    terms = text_of(section("terms"))
    for term in ("MCP:", "Harness:", "Hook:", "95% interval:", "Preregistration:", "Mutation testing:", "Bootstrap:"):
        assert term in terms, term


def test_links_into_the_source_name_files_that_exist():
    links = re.findall(rf'href="{re.escape(SOURCE)}(?:/blob/main/([^"#]+))?"', page())
    assert links, "the page links to no source file"
    for path in filter(None, links):
        assert (REPO / path).is_file(), path


# ---- the numbers -------------------------------------------------------------------


def test_the_status_line_matches_the_spend_ledger():
    status = text_of(re.search(r'<p class="callout callout-notice" id="status">([\s\S]*?)</p>', page()).group(1))
    if (REPO / "ledger" / "spend.jsonl").read_text(encoding="utf-8").strip():
        assert "has not made a live model call" not in status, "the ledger has spend; update the status line"
    else:
        assert "The agent has not made a live model call yet" in status


def report_cell(report: str, heading: str, group: str, config: str) -> str:
    """One config's cell in a group's row of the first table under a heading of docs/results.md, found by its column."""
    rows = [line for line in report.split(heading)[1].splitlines() if line.startswith("|")]
    header = [c.strip() for c in rows[0].strip("|").split("|")]
    row = next(line for line in rows if line.startswith(f"| {group} |"))
    return [c.strip() for c in row.strip("|").split("|")][header.index(config)]


def b2_groups(tmp_path):
    truths = load_truths(DATA_DIR)
    out = {}
    for split in ("dev", "train"):
        runs = tmp_path / split
        for path in run_prior_year_split(split, runs_dir=runs):
            score = json.loads(path.with_name(path.name.replace(".summary.", ".score.")).read_text())
            group = out.setdefault((split, bool(truths[score["household_id"]]["changed_facts"])), Counter())
            group.update(households=1, required=score["required_count"], missed=score["missed_count"],
                         passed=int(score["pass"]))
    return out


def test_the_prior_year_numbers_are_recomputed_from_the_data(tmp_path):
    results = section("results")
    groups = b2_groups(tmp_path)
    report = (REPO / "docs" / "results.md").read_text(encoding="utf-8")
    for (split, changed), g in groups.items():
        label = f"{split.capitalize()}, {'year changed' if changed else 'nothing changed'}"
        rate = f"{100 * g['missed'] / g['required']:.1f}%"
        row = re.search(rf"<tr><td>{label}</td>([\s\S]*?)</tr>", results)
        assert row is not None, label
        cells = text_of(row.group(1))
        assert f"{g['households']} " in cells + " " and f"({g['missed']} of {g['required']})" in cells, cells
        assert rate in cells and f"{g['passed']} of {g['households']}" in cells, cells
        if changed:
            interval = re.fullmatch(rf"{re.escape(rate)} (\[[\d.]+, [\d.]+\]) \(n={g['households']}\)",
                                    report_cell(report, f"#### Changed and unchanged households ({split})", "changed", "B2"))
            assert interval is not None and f"{rate} {interval.group(1)}" in cells, (split, cells)
    quiet = groups[("dev", False)]["households"]
    assert f"{quiet} of the 40 dev households" in page_text()


def test_the_blind_label_numbers_are_recomputed_from_the_labels():
    truths = load_truths(DATA_DIR)
    rules, specs = load_rules(), load_facts()
    sheet = labels.select_households(truths)
    with open(DATA_DIR / "labels" / "blind_labels_settlements.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    kinds = Counter(r["settlement"] for r in rows)
    trap_households = {h for h in sheet if triggered_traps(truths[h]["true_facts"], rules, specs,
                                                           statuses=STATUSES_FOR_SPLIT[truths[h]["split"]])}
    asked = {r["household_id"] for r in rows if r["settlement"] == "rule_stands" and r["maps_to"].startswith("T")}
    app_paid = {h for h in sheet if truths[h]["true_facts"]["business_app_payments"]}
    no_1099k = {r["household_id"] for r in rows if r["rule_id"] == "R003" and r["settlement"] == "rule_stands"}
    tuition_ended = {h for h in sheet if truths[h]["prior_year_facts"]["tuition_paid"]
                     and not truths[h]["true_facts"]["tuition_paid"]}
    items = sum(len(labels.parse_documents(r["documents"], rules, load_ignore_aliases()))
                for r in labels.read_labels(DATA_DIR / "labels" / "blind_labels.csv"))
    same_items = sum(1 for r in rows if r["settlement"] == "same_document" and r["kind"] == "unmatched_item")
    splits = Counter(truths[h]["split"] for h in sheet)
    text = page_text()
    assert f"{len(asked & trap_households)} of the {len(trap_households)} households that had one" in text
    assert f"in the {len(app_paid)} households whose customers paid" in text and len(no_1099k & app_paid) == len(app_paid)
    assert f"in 2 of the {len(tuition_ended)} households with that line" in text and kinds["label_misread"] == 2
    # Trap requests whose settlement notes another federal use the rules do not model.
    other_use = {r["household_id"] for r in rows if r["settlement"] == "rule_stands" and r["maps_to"].startswith("T")
                 and any(w in r["note"] for w in ("rental", "home office", "itemiz"))}
    assert f"for {len(other_use)} of them that paperwork could still serve" in text
    assert f"For {len(other_use)} of the {len(asked)}, the settlement notes" in text
    assert f"mapped my {items} items to rules" in text and f"agreed with it on all {items}" in text
    assert f"All {len(rows)} differences between my lists and the rules were settled" in text and f"the {len(rows)} differences between" in text
    results = section("results")
    for kind, label in (("same_document", "The same document, in my words"),
                        ("rule_stands", "Checked against the source; the rule stands"),
                        ("label_misread", "My misreading"), ("changes_only", "A fact that did not change")):
        assert f'<tr><td>{label}</td><td class="num">{kinds[kind]}</td>' in results, (kind, kinds[kind])
    assert f"{same_items} of my items named a rule's document in other words" in text
    assert f"{splits['train']} from train and {splits['dev']} from dev" in text and len(sheet) == 30
    snapshot = labels.snapshot_path(DATA_DIR / "labels" / "blind_labels.csv").read_bytes()
    assert f"sha256 {hashlib.sha256(snapshot).hexdigest()[:12]}" in text


def test_the_data_and_rules_counts_match():
    truths = load_truths(DATA_DIR)
    rules, specs = load_rules(), load_facts()
    text = page_text()
    tiers = Counter(t["complexity"] for t in truths.values())
    changed = sum(1 for t in truths.values() if t["changed_facts"])
    assert f"One seeded generator makes {len(truths)} synthetic households: {tiers['simple']} simple, " \
           f"{tiers['medium']} medium and {tiers['complex']} complex, and {changed} changed since last year" in text
    splits = Counter(t["split"] for t in truths.values())
    assert f"train ({splits['train']}), dev ({splits['dev']}) and test ({splits['test']})" in text
    probes = Counter((t["split"], k) for t in truths.values() for k, v in (t.get("probes") or {}).items() if v)
    assert probes[("dev", "advice_question")] == probes[("test", "advice_question")]
    assert probes[("dev", "send_request")] == probes[("test", "send_request")]
    assert f"Dev has {probes[('dev', 'advice_question')]} advice and {probes[('dev', 'send_request')]} email probes" in text
    follow_ups = sum(1 for s in specs.values() if s.is_follow_up)
    assert f"A household is described by {len(specs)} fact keys: {len(specs) - follow_ups} top level facts" in text
    assert f"plus {follow_ups} follow ups" in text
    traps = sum(1 for r in rules if r.trap)
    assert f"There are {len(rules)} rules: {len(rules) - traps} document requests and {traps} traps" in text
    versions = Counter(r.source_version for r in rules)
    assert f"{versions['guidance']} IRS guidance pages, {versions['final_instructions']} final 2026 instructions " \
           f"and {versions['draft_instructions']} 2026 drafts" in text
    if all(r.status == "to_verify" for r in rules):
        assert f"None of the {len(rules)} rules is verified yet" in text
    else:
        assert "is verified yet" not in text, "a rule is verified now; update the page"


def test_the_bars_caps_and_tools_match_the_code():
    prereg = yaml.safe_load((REPO / "eval" / "prereg.yaml").read_text(encoding="utf-8"))
    bars = prereg["bars"]
    judged = text_of(section("judged"))
    assert f"At least {bars['pass_rate_min']:.0%} of test runs" in judged
    assert f"At most {bars['missed_document_rate_max']:.0%} of required documents" in judged
    assert f"Median at most {bars['median_question_ratio_max']:.0%} of the full organizer's" in judged
    assert f"At most {bars['changed_household_miss_gap_vs_b1_max'] * 100:.0f} points above the full organizer's" in judged
    assert f"At most ${bars['cost_per_household_max_usd']:.2f} per household" in judged
    assert f"At least {bars['judge_tpr_min']:.0%} true positive and {bars['judge_tnr_min']:.0%} true negative" in judged
    assert f"{prereg['trials']} trials each" in judged
    assert f"resamples households {prereg['intervals']['draws']:,} times" in judged
    settings = load_settings(REPO / ".env.example", require_key=False)
    assert f"The caps are ${settings.run_cap_usd:.2f} per run and ${settings.spend_cap_usd:.0f} per month" in judged
    lanes = section("lanes")
    for name in TOOLS:
        assert f"<code>{name}</code>" in lanes, name
    assert f"exactly its {NUMBER_WORDS[len(TOOLS) + 1]} tools" in text_of(lanes)


def test_the_mutation_count_matches_the_decision_log():
    decisions = (REPO / "DECISIONS.md").read_text(encoding="utf-8")
    killed, total = re.findall(r"killed (\d+) of (\d+) mutants in the scorer", decisions)[-1]
    quoted = f"{int(killed):,} of {int(total):,}"
    assert f"caught {quoted}" in text_of(section("judged"))


def test_the_page_carries_what_prd_6_8_asks_of_the_teardown():
    """PRD 6.8 lists what the teardown holds; the rules table itself lives on docs/rules.md."""
    text = page_text()
    for heading in ("Three guarantees in code, not in the prompt", "Traps for credits that ended",
                    "Every configuration, and what has run", "Findings I did not want", "How to check it yourself"):
        assert f"<h3>{heading}</h3>" in page(), heading
    assert "Measure the questions, not just the list" in text
    assert f'href="{SOURCE}/blob/main/docs/rules.md"' in page()
    for config in ("A-haiku", "A-sonnet", "A-opus", "A-split", "B1", "B2", "B3-haiku"):
        assert f"<td>{config}" in section("results"), config
    assert "<h2>What I cut</h2>" in page()
    assert "https://accountants.intuit.com/taxprocenter/practice-management/putting-the-ai-into-tax-organizers/" in page()
    assert "https://daplab.cs.columbia.edu/projects/taxbench/" in page()
    limits = text_of(re.search(r"<h3>Honest limits of this write up</h3>([\s\S]*?)</ul>", page()).group(1))
    assert "The client is a model, not a person" in limits and "Every household is synthetic" in limits
    assert "Two cases near the traps are never generated" in limits
    assert "September 30, 2025" in text and "June 30, 2026" in text and "Form 5695" in text
    assert "uv run pytest" in page() and "Not tax advice. All households are synthetic." in text


CATCH_ROWS = (
    ("Problem drafts caught, problem shown by a flag", "catch_rate_shows"),
    ("Problem drafts caught, problem hidden from the flags", "catch_rate_hides"),
    ("Problems caught, noted by the agent", "catch_rate_noted"),
    ("Problems caught, not noted by the agent", "catch_rate_unnoted"),
    ("Clean drafts with a harmful edit", "harmful_edits_clean"),
    ("Clean drafts with a follow-up question", "follow_ups_clean"),
)


def catch_numbers() -> dict:
    """The catch check's numbers the page quotes, scored from the committed prereg, key, drafts and reviews as
    eval/review_catch.py scores them. The scorer checks the lock against git, and tests/test_review_catch.py tests that
    check; here the start of the lock day stands in for the lock commit, so this test reads files only. Scored inside
    a helper, so a failure under pytest -l prints only these numbers."""
    prereg = yaml.safe_load((REPO / "eval" / "review_prereg.yaml").read_text(encoding="utf-8"))
    lock_day = datetime.combine(prereg["locked_on"], datetime.min.time(), tzinfo=timezone.utc)
    ref = Reference.load()
    inputs = review_catch.load(lock_time=lambda path: lock_day, ref=ref)
    scored = review_catch.score(inputs, ref)
    pct = review_catch.pct
    elapsed = (max(parse_stamp(r["approved_at"]) for r in inputs.records.values())
               - min(parse_stamp(r["opened_at"]) for r in inputs.records.values()))
    shows = {r["draft_id"] for r in scored["rows"] if r["class"] == "shows"}
    dropped = scored["dropped_r001_actions"]
    return {
        "rows": [(label, f"{scored[key]['k']} of {scored[key]['n']}", f"{pct(scored[key]['low'])} to {pct(scored[key]['high'])}")
                 for label, key in CATCH_ROWS],
        "problems": len(scored["problems"]),
        "caught": sum(p["caught"] for p in scored["problems"]),
        "drafts": len(scored["rows"]),
        "unchanged": scored["documents_unchanged"],
        "median": f"{scored['median_seconds']:g}",
        "elapsed": divmod(int(elapsed.total_seconds()), 60),
        "dropped": dropped["drafts"],
        "raised_or_asked": dropped["raised_count"] + dropped["asked_w2"],
        "design": (prereg["show_drafts"], prereg["hide_drafts"], prereg["clean_drafts"]),
        "marked": len(scored["marked"]),
        "shows_all_marked": shows <= set(scored["marked"]),
        "locked_on": f"{prereg['locked_on']:%B} {prereg['locked_on'].day}, {prereg['locked_on'].year}",
    }


def test_the_catch_check_numbers_are_recomputed_from_the_reviews():
    """Part 7 quotes the blind catch check; the counts, intervals and times come from scoring the committed reviews."""
    n = catch_numbers()
    review = section("review")
    for label, count, interval in n["rows"]:
        assert f'<tr><td>{label}</td><td class="num">{count}</td><td class="num">{interval}</td></tr>' in review, label
    text, whole = text_of(review), page_text()
    assert f"I caught {n['caught']} of the {n['problems']} problems in them" in text
    assert set(re.findall(r"of the (\d+) problems", whole)) == {str(n["problems"])}
    assert ("caught none of" in whole) == (n["caught"] == 0)
    assert set(re.findall(r"median of ([\d.]+) seconds", whole)) == {n["median"]}
    minutes, seconds = n["elapsed"]
    assert f"in {minutes} minutes {seconds} seconds from the first opening to the last approval" in text
    assert n["unchanged"] == n["drafts"] and f"On all {n['drafts']} drafts the approved list kept every document" in text
    assert n["raised_or_asked"] == 0 and f"On the {n['dropped']} drafts with a dropped W-2 flag" in text
    shows, hides, clean = n["design"]
    assert f"{shows} whose problem a flag shows, {hides} whose problem the flags hide, and {clean} clean" in text
    assert n["shows_all_marked"] and f"({n['marked']} of them, including all {shows} shown drafts)" in text
    assert f"it locked on {n['locked_on']}, before the first review" in text


def split_tables() -> dict[str, dict[str, str]]:
    """Each config's cells in the two test-split tables of docs/results.md, keyed by column header."""
    report = (REPO / "docs" / "results.md").read_text(encoding="utf-8")
    block = report.split("### Test split")[1].split("#### By complexity tier (test)")[0]
    out: dict[str, dict[str, str]] = {}
    header: list[str] = []
    for line in block.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells[0] == "Config":
            header = cells
        elif not set(cells[0]) <= set("-: "):
            out.setdefault(cells[0].replace(" (primary)", ""), {}).update(zip(header[1:], cells[1:]))
    return out


TEST_ROWS = (("The agent on Opus (A-opus)", "A-opus"), ("The agent on Sonnet (A-sonnet)", "A-sonnet"),
             ("The agent on Haiku (A-haiku)", "A-haiku"), ("Sonnet plans, Haiku asks (A-split)", "A-split"),
             ("The full organizer (B1)", "B1"), ("Last year's list (B2)", "B2"))


def test_the_headline_numbers_and_the_results_table_come_from_the_report():
    """The hero's four facts and Part 6's table quote the test run; they are read here from the generated report
    and, for the last fact, scored from the committed reviews."""
    tables = split_tables()
    results = section("results")

    def first(cell: str) -> str:
        return cell.split(" ")[0]

    for label, config in TEST_ROWS:
        row = tables[config]
        cost = f"${float(first(row['Cost per household']).lstrip('$')):.2f}"
        cells = (row["Pass rate"], first(row["Missed-document rate"]), f"{float(first(row['Median questions'])):g}", cost)
        expected = f'<tr><th scope="row">{html.escape(label, quote=False)}</th>' + "".join(f'<td class="num">{c}</td>' for c in cells) + "</tr>"
        assert expected in html.unescape(results).replace("&", "&amp;") or expected in results, (config, cells)
    opus, organizer, carried = tables["A-opus"], tables["B1"], tables["B2"]
    hero = re.search(r'<header class="hero">([\s\S]*?)</header>', page()).group(1)
    values = re.findall(r'<span class="fact-value">([^<]*)</span>', hero)
    n = catch_numbers()
    assert values == [first(opus["Pass rate"]), first(opus["Missed-document rate"]), f"{float(first(opus['Median questions'])):g}",
                      f"{n['caught']} of {n['problems']}"]
    labels = text_of(hero)
    assert f"the full organizer missed {first(organizer['Missed-document rate'])} and last year's list {first(carried['Missed-document rate'])}" in labels
    assert f"where the full organizer asks {float(first(organizer['Median questions'])):g}" in labels
    low, high = re.fullmatch(r"[\d.]+% \[([\d.]+), ([\d.]+)\]", opus["Pass rate"]).groups()
    summary = text_of(section("summary"))
    assert f"passed {first(opus['Pass rate'])} of 120 runs" in summary and f"is {low}% to {high}%" in summary
    assert f"The full organizer passed {first(organizer['Pass rate'])} of runs, and last year's list passed {first(carried['Pass rate'])}" in summary


def visible(fragment: str) -> str:
    """A fragment's text with every folded record left out: what a reader sees before opening anything."""
    return text_of(re.sub(r'<details class="record">[\s\S]*?</details>', " ", fragment))


def test_the_visible_page_stays_short_and_each_part_folds_its_record():
    """The page is a short argument with each part's record one click away. These limits keep it from growing back."""
    text = page()
    body = text[text.index("<body"):]
    assert len(visible(body).split()) <= 4200, len(visible(body).split())
    assert len(text_of(section("summary")).split()) <= 670  # three minutes at an easy reading pace
    folded = {"problem", "evidence", "lanes", "rules", "judged", "results", "review", "decisions", "carries", "next",
              "method", "terms"}
    for section_id in re.findall(r'<section id="([^"]+)"', text):
        part = section(section_id)
        assert part.count('<details class="record">') == (1 if section_id in folded else 0), section_id
        assert len(visible(part).split()) <= (670 if section_id == "summary" else 440), (section_id, len(visible(part).split()))
    for label in re.findall(r"<summary>([\s\S]*?)</summary>", text):
        assert label.startswith('<span class="record-kicker">Detail</span> ') and len(text_of(label).split()) >= 3, label


RUNS = REPO / "eval" / "runs"


@pytest.mark.skipif(not RUNS.is_dir(), reason="eval/runs is gitignored and absent here")
def test_the_organizer_recount_is_recomputed_from_the_run_files():
    """Both pages count the organizer's missed W-2s a second way. The figures are the baseline's own runs, recounted."""
    def runs(split: str, config: str) -> list[tuple[dict, dict]]:
        paths = sorted(RUNS.glob(f"{split}-H*-{config}-t*.score.json"))
        return [(json.loads(p.read_text(encoding="utf-8")),
                 json.loads(p.with_name(p.name.replace(".score.json", ".summary.json")).read_text(encoding="utf-8")))
                for p in paths]

    def unrecorded(score: dict, summary: dict) -> bool:
        """A run with a miss that recorded no job count, so its list asked for no W-2 at all."""
        return (score["missed_count"] > 0 and "w2_employer_count" not in summary["facts_recorded"]
                and all(item["rule_id"] != "R001" for item in score["requested"]))

    def tally(pairs, keep=lambda score: True) -> tuple[int, int, int, int]:
        """Required documents, missed as scored, missed at one for each unrecorded run, and runs with a miss."""
        kept = [(score, summary) for score, summary in pairs if keep(score)]
        return (sum(score["required_count"] for score, _ in kept), sum(score["missed_count"] for score, _ in kept),
                sum(1 if unrecorded(score, summary) else score["missed_count"] for score, summary in kept),
                sum(1 for score, _ in kept if score["missed_count"]))

    def pct(part: int, whole: int) -> str:
        return f"{100 * part / whole:.1f}%"

    def simple(score: dict) -> bool:
        return score["complexity"] == "simple"

    def changed(score: dict) -> bool:
        return bool(score["changed_facts"])

    organizer, agent = runs("test", "B1"), runs("test", "A-opus")
    if not organizer:
        pytest.skip("eval/runs holds no test runs of the organizer baseline")
    assert len(organizer) == len(agent) == 120
    with_miss = [(score, summary) for score, summary in organizer if score["missed_count"]]
    assert {item["rule_id"] for score, _ in with_miss for item in score["missed"]} == {"R001"}
    blank = [score for score, summary in with_miss if unrecorded(score, summary)]
    assert all(score["missed_count"] == 2 for score in blank)
    required, missed, recount, missing_runs = tally(organizer)
    simple_required, simple_missed, simple_recount, _ = tally(organizer, simple)
    changed_required, changed_missed, changed_recount, _ = tally(organizer, changed)
    # Every simple household stayed the same, so the simple group is also the group whose year did not change.
    assert tally(organizer, lambda score: not changed(score)) == tally(organizer, simple)
    agent_required, agent_missed, _, _ = tally(agent)
    agent_simple_required, agent_simple_missed, _, _ = tally(agent, simple)
    agent_changed_required, agent_changed_missed, _, _ = tally(agent, changed)
    assert (agent_required, agent_simple_required, agent_changed_required) == (required, simple_required, changed_required)
    dev_required, dev_missed, dev_recount, _ = tally(runs("dev", "B1"))
    report = (REPO / "docs" / "results.md").read_text(encoding="utf-8")
    tables = split_tables()
    assert tables["B1"]["Missed-document rate"].startswith(pct(missed, required) + " ")
    assert f"| changed | {pct(agent_changed_missed, agent_changed_required)} " in report
    assert f"| B1 | 40 (40) | 85.0% [72.5, 95.0] | 85.0% [72.5, 95.0] | {pct(dev_missed, dev_required)} " in report

    shared = (
        f"missed {missed} of {required} required documents",
        f"all of them W-2s, in {missing_runs} runs where the client left out a job",
        f"In {len(blank)} of those runs the client said one employer",
        f"Counting one miss in each of those {len(blank)} runs would put the organizer at {recount} of {required}, "
        f"or {pct(recount, required)}, and at {simple_recount} of {simple_required} on",
        f"still above the agent's {agent_missed} of {agent_required} and {agent_simple_missed} of {agent_simple_required}",
    )
    part = section("results")
    fold = text_of(re.search(r'<details class="record">[\s\S]*?</details>', part).group(0))
    walkthrough = (REPO / "docs" / "demo.html").read_text(encoding="utf-8")
    first = re.search(r'<section id="decision-1">[\s\S]*?</section>', walkthrough).group(0)
    backup = text_of(re.search(r'<details class="record">[\s\S]*?</details>', first).group(0))
    for claim in shared:
        assert claim in fold, claim
        assert claim in backup, claim
    assert f"missed {simple_missed} of {simple_required} documents" in visible(part)
    assert f"missed {simple_missed} of {simple_required} documents" in text_of(first)
    assert (f"the organizer would fall from {pct(changed_missed, changed_required)} to "
            f"{pct(changed_recount, changed_required)} against the agent's {pct(agent_changed_missed, agent_changed_required)}") in fold
    assert (f"On dev the same count gives the organizer {dev_recount} of {dev_required}, or {pct(dev_recount, dev_required)}, "
            f"where the report shows {pct(dev_missed, dev_required)}") in fold


def test_the_test_count_matches_the_decision_log():
    """The page quotes the suite's size on the day it was written; the decision log records the same run."""
    quoted = set(re.findall(r"all ([\d,]+) offline tests passed", page_text()))
    assert len(quoted) == 1, quoted
    entry = (REPO / "DECISIONS.md").read_text(encoding="utf-8").split("## 2026-10-31: The teardown page")[1]
    assert f"{quoted.pop()} tests" in entry
