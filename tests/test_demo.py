"""The two-minute walkthrough, docs/demo.html: its shape, its length, its voice, and every number on it.

The purpose and the hypothesis stay in view at the top: an if-then statement and the bars that would make it fail.
Under them one dark panel opens on an overview, three tiles with one point and one small picture each, above five
sections, all closed at first, each with its conclusion in its closed row: what the agent does, how I tested it,
the result with its verdict on the hypothesis, the human check with my call, and what I would bring to the role. A
click on a title puts its section in the overview's place and closes the one before, with no script. Under the
panel one fold holds the questions I expect. Few measures carry the story, and a test below keeps any other number
out of the overview and the sections. Every number is read from the generated results report, the locked bars, the
committed reviews, the household data and the rules file, and each drawn length and mark is checked against the
number it stands for. These tests read files only; tests/test_demo_browser.py checks the clicks, the layout and the
motion in a browser.
"""

import html
import math
import re
import statistics
from collections import Counter
from pathlib import Path

import yaml

from generator.files import DATA_DIR, load_truths
from generator.labels import LABEL_PLAN
from rules.engine import load_rules
from server.tax_server import TOOL_NAMES
from test_teardown import catch_numbers, split_tables

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
PAGE = DOCS / "demo.html"
STYLE = DOCS / "src" / "demo.css"
SOURCE = "https://github.com/Roanukz/tax-agent-ask"
TAGS = ["Purpose", "Hypothesis"]
NO_RULES_SETUPS = ["B3-haiku", "B3-sonnet", "B3-opus"]
AGENT_SETUPS = ["A-haiku", "A-sonnet", "A-opus", "A-split"]
FOLD = r'<details class="qa">[\s\S]*?</details>'
MOTION = "@media screen and (prefers-reduced-motion: no-preference)"
# Where the README says the test ran in two parts, and where the log counts the questionnaire's runs with no job count.
TWO_PARTS = (r"scored the first (\d+) test households on November (\d+) and, once the last (\d+) rules were verified, "
             r"the other (\d+) on November (\d+)")
NO_COUNT = r"In (\d+) of them \(H007"
# The measures the sections may show: the pass rate, the question count with the questionnaire's and the missed
# documents, each with its bar, and the review's drafts and problems.
STORY = {"92.5%", "90%", "14", "36", "0.5%", "2%", "24", "17"}
# The interval's level, the numbers of the three tiles and the five sections, and the two job counts of the one
# answer the first section follows.
SCALE = {"95%"} | {str(i) for i in range(1, 6)}
# The five sections: the anchor each opens at, its title, and what its closed row says.
PARTS = [
    ("agent", "What the agent does", "The model asks, code writes the list, and a preparer approves"),
    ("tested", "How I tested it", "Bars and model locked first, then a held-out test"),
    ("result", "The result", "Hypothesis supported on this test all three bars met, the pass rate by a thin margin"),
    ("call", "The human check and my call", "Not ready for clients approval needs its first fix"),
    ("bring", "What I would bring to an AI evaluation and quality role",
     "The bar agreed first, guardrails in code, every check measured"),
]
STEPS = {
    "agent": [
        ("Start from last year's file", "last year's facts and documents on file"),
        ("Ask the client what changed", "each fact kept with its evidence"),
        ("Code writes the document list", "from rules that cite IRS sources"),
        ("A preparer approves each list", "on the review screen, flag by flag"),
    ],
    "tested": [
        ("Run evals, read the traces", "a note on every failing dev run"),
        ("Build the review tools", "pages for rules, households and transcripts"),
        ("Label by hand", "each rule against its source, households blind"),
        ("Lock the bars and model, then test", "scored by code and a judge model"),
    ],
    "call": [
        ("Approval did not wait for an answer", "one click marked each flag checked"),
        ("The closing message sat on another tab", "it gave the job count on the four flagged missing W\u20112s"),
        ("My call", "this fix first, then a repeat with practicing preparers against a bar set before it runs"),
    ],
}
VERDICT = ("It did not fail on any of the three bars, though the pass rate cleared its bar by only three runs and its "
           "interval crosses the bar.")
LIMITS = ("It is one test on simulated clients, scored by a key from the same rules and a judge model that missed its own "
          "bar, so it says nothing yet about real clients.")
# The overview's three points: the label, the point, and the sentence under it.
POINTS = [
    ("The value", "Less than half the questions",
     "The agent asked simulated clients far fewer questions and still missed almost no documents."),
    ("The evidence", "A test that could have failed",
     "I locked the bars before any test run, and the agent met all three, one by a thin margin."),
    ("My call", "Not ready for clients yet",
     "The human check did not hold up when I tested it, so a fix and a retest come first."),
]


def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def text_of(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def visible(fragment: str) -> str:
    """A fragment's text with every folded answer left out."""
    return text_of(re.sub(FOLD, " ", fragment))


def between(start: str, end: str) -> str:
    text = page()
    return text[text.index(start):text.index(end, text.index(start))]


def opening() -> str:
    return between('<header class="hero opening" id="top">', "</header>")


def panel() -> str:
    return between('<div class="console" id="walk">', '<details class="fold" id="questions">')


def over() -> str:
    """The overview: the three tiles the panel shows before any section is opened."""
    return between('<section class="over" id="overview"', "</section>")


def part(name: str) -> str:
    """One section of the panel: its title row and its body."""
    return between(f'<section class="part part-{name}">', "</section>")


def body(name: str) -> str:
    whole = part(name)
    return whole[whole.index('<div class="part-body"'):]


def row(name: str) -> str:
    """A section's closed row, as its opening link holds it."""
    return re.search(r'<a class="part-link part-open"[^>]*>([\s\S]*?)</a>', part(name)).group(1)


def fold() -> str:
    return between('<details class="fold" id="questions">', '<p class="more">')


def first_glance() -> str:
    """What is on screen before any title is clicked: the purpose, the hypothesis, the overview and the five closed rows."""
    return opening() + re.sub(r'aria-label="[^"]*"', "", over()) + " ".join(row(name) for name, _, _ in PARTS)


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.?])\s+(?=[A-Z])", text) if s]


def questions() -> list[tuple[str, str]]:
    return [(text_of(q), text_of(re.sub(r"<figure[\s\S]*?</figure>", " ", a))) for q, a in
            re.findall(r'<details class="qa">\s*<summary>([\s\S]*?)</summary>([\s\S]*?)</details>', fold())]


def css() -> str:
    return STYLE.read_text(encoding="utf-8")


def rule_of(selector: str, sheet: str | None = None) -> str:
    """The body of the first rule for a selector in the walkthrough's stylesheet."""
    sheet = css() if sheet is None else sheet
    return re.search(rf"(?:^|\n){re.escape(selector)} \{{\n([\s\S]*?)\n\}}", sheet).group(1)


def steps_of(name: str) -> list[tuple[str, str]]:
    found = re.findall(r'<li class="step b(\d)">\s*<span class="node" aria-hidden="true"></span>\s*<span class="step-text">'
                       r'<strong class="step-title">([^<]*)</strong>(?:\s*<span class="chip[^"]*">[\s\S]*?</span>)?\s*'
                       r'<span class="step-detail">([^<]*)</span></span>', body(name))
    assert [n for n, _, _ in found] == [str(i) for i in range(1, len(found) + 1)]
    return [(html.unescape(title), html.unescape(detail)) for _, title, detail in found]


def first(cell: str) -> str:
    return cell.split(" ")[0]


def rate(cell: str) -> float:
    return float(first(cell).rstrip("%"))


def interval(cell: str) -> tuple[str, str]:
    return re.fullmatch(r"[\d.]+%? \[([\d.]+), ([\d.]+)\].*", cell).groups()


def money(cell: str) -> str:
    return f"${float(first(cell).lstrip('$')):.2f}"


def count(cell: str) -> str:
    return f"{float(first(cell)):g}"


def locked_on() -> str:
    day = yaml.safe_load((REPO / "eval" / "prereg.yaml").read_text(encoding="utf-8"))["locked_on"]
    return f"{day:%B} {day.day}, {day.year}"


def judge() -> tuple[int, int]:
    """How many of the judge model's questions meet both of its bars, and how many it answers."""
    report = (DOCS / "results.md").read_text(encoding="utf-8")
    bars = yaml.safe_load((REPO / "eval" / "prereg.yaml").read_text(encoding="utf-8"))["bars"]
    line = next(line for line in report.splitlines() if line.startswith("| J |")).split("final split")[1]
    found = re.findall(r"(\w+): TPR ([\d.]+%|n/a), TNR ([\d.]+%|n/a)", line)
    met = [name for name, tpr, tnr in found if "n/a" not in (tpr, tnr)
           and float(tpr.rstrip("%")) >= bars["judge_tpr_min"] * 100 and float(tnr.rstrip("%")) >= bars["judge_tnr_min"] * 100]
    return len(met), len(found)


def best_without_rules() -> tuple[str, dict[str, str]]:
    """The model that passed most often when it wrote the list from the true facts with no rules table."""
    tables = split_tables()
    key = max(NO_RULES_SETUPS, key=lambda k: rate(tables[k]["Pass rate"]))
    return key, tables[key]


def test_title_hypothesis_and_the_sections_in_order():
    text = page()
    assert "<title>What Should My Tax Agent Ask? A Two-Minute Walkthrough</title>" in text
    assert ('<p class="eyebrow">What Should My Tax Agent Ask? &middot; A two-minute walkthrough of a prototype on '
            "synthetic data</p>") in text
    assert text.count("<h1>") == 1
    assert re.findall(r'class="tag">([^<]*)<', text) == TAGS
    # The hypothesis comes before the panel, the five sections come in the order of the talk, the overview follows
    # them in the source so that an open section can hide it, and the questions come last.
    marks = ["<h1>", '<div class="console" id="walk">'] + [f'<section class="part part-{name}">' for name, _, _ in PARTS]
    marks += ['<section class="over" id="overview"', '<details class="fold" id="questions">', '<p class="more">']
    places = [text.index(mark) for mark in marks]
    assert places == sorted(places)
    assert [text.count(mark) for mark in marks] == [1] * len(marks)
    assert re.findall(r'<section class="part part-(\w+)">', text) == [name for name, _, _ in PARTS]
    for n, (name, title, closed) in enumerate(PARTS, 1):
        shown = text_of(row(name))
        assert shown == f"{n} {title} {closed}", shown
    assert '<summary><span class="fold-title">Questions I expect, with short answers</span></summary>' in text
    # Who did what is said on the page's first line.
    assert ("<strong>Roanuk Zaman</strong> &middot; I designed it and made the calls, and Claude Code wrote the code "
            "under rules I set") in text


def test_the_hypothesis_is_an_if_then_that_the_locked_bars_can_fail():
    """The page states one hypothesis, as a condition and a claim, and under it what would have made it fail. Both
    come from the requirements, which were written before any code, and from the bars locked before any test run."""
    head = opening()
    assert head.index('<p class="tag">Hypothesis</p>') < head.index("<h1>")
    claim = text_of(re.search(r"<h1>([\s\S]*?)</h1>", head).group(1))
    assert claim == ("If the model asks for the facts and code writes the document list from rules that cite IRS sources, "
                     "then the agent can ask half the questions of a full questionnaire and miss almost no documents.")
    # "Learns" would read as training, and nothing here is trained.
    assert "model learns" not in text_of(page())
    assert re.findall(r'<em class="hinge">([^<]*)</em>', head) == ["If", "then"]
    requirements = re.sub(r"\s+", " ", (REPO / "PRD-tax-agent-ask.md").read_text(encoding="utf-8"))
    assert "The agent's only job is to learn facts" in requirements
    assert ("Code, not the model, turns facts into the document list through a rules table in which every rule cites an "
            "official source") in requirements
    assert "passes at least 90% of test runs (40 households, 3 trials each) and misses no more than 2% of required documents" in requirements
    assert "Its median question count is at most 50% of the full organizer baseline's" in requirements
    rules = load_rules()
    assert rules and all("irs.gov/" in rule.source_url for rule in rules), "every rule cites an IRS source"
    prereg = yaml.safe_load((REPO / "eval" / "prereg.yaml").read_text(encoding="utf-8"))
    bars = prereg["bars"]
    assert bars["median_question_ratio_max"] == 0.5 and prereg["primary_config"] == "A-opus"
    # One bar for each thing the fail line names, in the order the result shows them.
    assert (f"It fails below a {bars['pass_rate_min']:.0%} pass rate on the held-out test, above half the questions, or "
            f"above {bars['missed_document_rate_max']:.0%} of documents missed, bars I locked before any test run.") in visible(head)
    # The test ran in two parts, so the page says the lock came before any test run, and never that there was one.
    log = re.sub(r"\s+", " ", (REPO / "DECISIONS.md").read_text(encoding="utf-8"))
    assert "This commit predates the first test run" in log
    assert "the one test run" not in text_of(page()) and "run once" not in text_of(page())
    purpose = text_of(re.search(r'<p class="purpose">([\s\S]*?)</p>', head).group(1))
    assert purpose == ("A tax return cannot start until the documents arrive, so an AI agent interviews the client to find "
                       "which to request, and a preparer approves each list.")


def test_a_title_opens_its_section_without_a_script():
    """Each title is a link to an anchor that sits just ahead of its section. The anchor is not drawn, so following
    the link scrolls nothing, and the stylesheet shows the body of the section whose anchor the address points at
    and hides the overview. Only one anchor can be the target, so opening one section closes the last. While a
    section is open its title is plain words, so a second click on it does nothing, and the mark at the end of its
    row links to an anchor that opens nothing, which closes it and brings the overview back."""
    text = page()
    inside = panel()
    assert inside.index('<span class="part-anchor" id="shut"></span>') < inside.index('<span class="part-anchor" id="agent">')
    for name, title, _ in PARTS:
        assert re.search(rf'<span class="part-anchor" id="{name}"></span>\s*<section class="part part-{name}">', text), name
        section = part(name)
        assert re.findall(r'<a class="part-link part-open" href="#(\w+)" aria-expanded="(\w+)" aria-controls="([\w-]+)">', section) == [
            (name, "false", f"{name}-body")]
        closing = re.findall(r'<a class="part-close" href="#(\w+)" aria-expanded="(\w+)" aria-controls="([\w-]+)" aria-label="([^"]*)"></a>', section)
        assert closing == [("shut", "true", f"{name}-body", f"Close {title}")], name
        shown = re.search(r'<span class="part-link part-shown">([\s\S]*?)</h2>', section).group(1)
        assert shown.count("<a ") == 1 and shown.index(f'<span class="part-title">{title}</span>') < shown.index("<a "), "the open title is not a link"
        assert section.count(f'<span class="part-title">{title}</span>') == 2
        assert section.count(f'<div class="part-body" id="{name}-body">') == 1 and section.count('<span class="rail" aria-hidden="true"></span>') == 1
    assert text.count('<h2 class="part-head">') == len(PARTS)
    sheet = re.sub(r"/\*[\s\S]*?\*/", "", css())
    still = sheet[:sheet.index(MOTION)]
    assert "display: none;" in rule_of(".part-anchor", still), "an anchor that is not drawn cannot be scrolled to"
    assert "display: none;" in rule_of(".part-body", still), "every section is closed until its title is clicked"
    assert "display: none;" in rule_of(".part-shown", still)
    assert "display: grid;" in rule_of(".part-anchor:target + .part .part-body", still)
    assert "display: none;" in rule_of(".part-anchor:target + .part .part-open", still)
    assert "display: grid;" in rule_of(".part-anchor:target + .part .part-shown", still)
    # The overview is drawn first and comes last in the source, and any open section hides it.
    assert "order: -1;" in rule_of(".over", still) and "flex-direction: column;" in rule_of(".console", still)
    assert "display: none;" in rule_of(".part-anchor:target:not(#shut) ~ .over", still)
    # The anchor that closes is laid out above the overview, so where a link scrolls, closing scrolls to the overview.
    assert "order: -2;" in rule_of("#shut", still)
    # The link that closes takes no click for its first moment, which is not motion and holds with motion off: a
    # double click on a closed row's mark cannot open its section and close it again.
    assert "animation: arm 0.7s step-end;" in rule_of(".part-close", still)
    assert re.search(r"@keyframes arm \{\s*from,\s*to \{\s*visibility: hidden;\s*\}\s*\}", still)
    # Every body is as tall as the stage on a wide screen, so the panel keeps one height from section to section.
    assert "min-height: var(--stage);" in rule_of(".part-body", still)
    # On paper nothing can be clicked, so the overview and every section are shown.
    printed = still[still.index("@media print"):]
    assert re.search(r"\.part-body \{\s*display: grid;", printed)
    assert re.search(r"\.part-anchor:target:not\(#shut\) ~ \.over \{\s*display: block;", printed)
    # Where a section may not fit the screen, the anchors are given a place, so a title's link scrolls to it.
    narrow = still[still.index("@media (max-width: 1239px), (max-height: 700px)"):]
    assert re.match(r"@media [^{]*\{\s*\.part-anchor \{\s*display: block;\s*height: 0;", narrow)
    # A closed row says its section's conclusion, and the two that reach a verdict show it as a state.
    assert '<span class="chip"><i class="tick" aria-hidden="true"></i>Hypothesis supported on this test</span>' in row("result")
    assert '<span class="chip"><i class="hold" aria-hidden="true"></i>Not ready for clients</span>' in row("call")


def test_the_overview_makes_three_points_each_with_a_picture():
    """Before any section is opened the panel shows three tiles: the value, the evidence and the call. Each has one
    point, one sentence and one small picture, and every number in them is the report's. The review result is not
    told here: it is said once, in its own section."""
    tables = split_tables()
    bars = yaml.safe_load((REPO / "eval" / "prereg.yaml").read_text(encoding="utf-8"))["bars"]
    opus, organizer = tables["A-opus"], tables["B1"]
    asked, full = count(opus["Median questions"]), count(organizer["Median questions"])
    passed, bar = first(opus["Pass rate"]), f"{bars['pass_rate_min']:.0%}"
    part_ = over()
    assert '<h2 class="over-title">What this demo shows, in three points</h2>' in part_
    tiles = re.findall(r'<li class="tile tile-(\w+)">\s*<p class="tile-kicker">([^<]*)</p>'
                       r'[\s\S]*?<p class="tile-head">([^<]*)</p>\s*<p class="tile-line">([\s\S]*?)</p>', part_)
    assert [key for key, _, _, _ in tiles] == ["value", "proof", "call"]
    assert [(kicker, head, text_of(line)) for _, kicker, head, line in tiles] == POINTS
    # Only the five sections are numbered, so a tile cannot be taken for a section.
    assert "tile-n" not in page()
    # Less than half, and almost none: the first point's two claims, from the report and the bars.
    assert float(asked) < float(full) / 2 and rate(opus["Missed-document rate"]) < 1
    # All three bars were met, and one of them, the pass rate, by a thin margin: three runs of 120. The other two
    # were met with room: under four fifths of the questions allowed, and a quarter of the misses allowed.
    runs = int(re.fullmatch(r"(\d+) \((\d+)\)", opus["Runs (households)"]).group(1))
    assert rate(opus["Pass rate"]) >= bars["pass_rate_min"] * 100
    assert round(rate(opus["Pass rate"]) * runs / 100) - math.ceil(bars["pass_rate_min"] * runs) == 3
    assert float(asked) / float(full) < 0.8 * bars["median_question_ratio_max"]
    assert rate(opus["Missed-document rate"]) <= 0.25 * bars["missed_document_rate_max"] * 100
    # Every client was simulated, and the tile says so.
    assert "every run here used a simulated client" in text_of(page())
    # The numbers are in the pictures, once each, so the sentences under them do not repeat them.
    assert not any(re.search(r"\d", line) for _, _, line in POINTS)
    # The first picture: two rows of marks on one scale, the questionnaire's all filled and the agent's as many as it asked.
    rows = re.findall(r'<span class="ask-marks ask-(\w+)">([\s\S]*?)</span><b class="ask-n">(\d+)</b>', part_)
    assert [(name, marks.count("<i"), marks.count('<i class="on">'), n) for name, marks, n in rows] == [
        ("full", int(full), 0, full), ("agent", int(full), int(asked), asked)]
    assert re.findall(r'<span class="ask-name">([^<]*)</span>', part_) == ["A full questionnaire", "The agent, at the median"]
    # The second: a track of all the runs, filled to the bar and then, in a brighter color, on to the pass rate, with
    # the bar as a line across it. The number is the chosen model's, since the four setups together passed less.
    sheet = css()
    numbers = sheet[sheet.index("---------- the drawn numbers"):sheet.index("---------- the motion")]
    at_bar, past = bars["pass_rate_min"] * 100, rate(opus["Pass rate"]) - bars["pass_rate_min"] * 100
    assert f"width: {at_bar:g}%;" in rule_of(".bar-fill", numbers)
    assert f"left: {at_bar:g}%;" in rule_of(".bar-past", numbers) and f"width: {past:g}%;" in rule_of(".bar-past", numbers)
    assert f"left: {at_bar:g}%;" in rule_of(".bar-at", numbers)
    assert f"grid-template-columns: repeat({full}, minmax(0, 1fr));" in rule_of(".ask-marks", numbers)
    assert f'<b class="bar-n">{passed}</b><span class="bar-cap">of the chosen model\'s test runs passed</span>' in part_
    assert f'<span class="bar-key"><i class="lock"></i>The bar, {bar}, locked first</span>' in part_
    assert all(key in tables for key in AGENT_SETUPS)
    assert sum(rate(tables[key]["Pass rate"]) for key in AGENT_SETUPS) / len(AGENT_SETUPS) < bars["pass_rate_min"] * 100
    # The third: the draft list reaches the human check and is held there, and the way on to the client is locked.
    assert re.findall(r'<span class="gate-stop[^"]*">(?:<i class="hold"></i>)?([^<]*)</span>', part_) == ["Draft list", "Human check", "Client"]
    assert ('<span class="gate-stop gate-check"><i class="hold"></i>Human check</span>'
            '<span class="gate-way gate-shut"><i class="lock"></i></span>') in part_
    assert '<span class="gate-key">Held at the human check</span>' in part_
    # A narrow tile sets the three stops smaller, so the last one stays whole.
    assert "container-type: inline-size;" in rule_of(".tile") and "@container (max-width: 340px)" in sheet
    assert len(re.findall(r'role="img" aria-label="[^"]{60,}"', part_)) == 3, "each picture is described"
    # Each tile has its own two colors and all three share one ink.
    for key, ramp in (("value", "primary"), ("proof", "info"), ("call", "warning")):
        assert f"--t-a: var(--{ramp}-100);" in rule_of(f".tile-{key}") and f"--t-b: var(--{ramp}-300);" in rule_of(f".tile-{key}")
    assert "--t-ink: var(--primary-950);" in rule_of(".tile")
    # The review result is not told on the overview.
    shown = visible(re.sub(r'aria-label="[^"]*"', "", part_))
    assert "caught" not in shown and "17" not in shown


def test_what_the_agent_does_follows_one_answer():
    """Four lines for what the agent does on each client, and beside them one answer followed from last year's file
    to the approved list. Every card in the drawing is something the code does."""
    assert steps_of("agent") == STEPS["agent"]
    drawn = body("agent")
    shown = text_of(drawn)
    assert "One answer, followed from last year's file to the approved list" in shown
    assert re.findall(r'<span class="col-head">([^<]*)</span>', drawn) == ["Start", "Model asks", "Code writes", "Review screen"]
    cards = [text_of(card).replace("‑", "-") for card in re.findall(r'<div class="card c\d">([\s\S]*?)</div>', drawn)]
    assert cards == ["Last year's file 2 W-2s on file",
                     '"Just one employer" Fact: 1 employer evidence: the client\'s words',
                     "Rule: a W-2 from each employer Request 1 W-2 cites an IRS source",
                     "Flag: last year had 2 A preparer approves approval needs this screen's token"]
    assert '<span class="agent-note">The agent has no tool that sends a list</span>' in drawn
    # What the record holds behind the cards.
    requirements = re.sub(r"\s+", " ", (REPO / "PRD-tax-agent-ask.md").read_text(encoding="utf-8"))
    server = (REPO / "server" / "intake.py").read_text(encoding="utf-8") + (REPO / "server" / "tax_server.py").read_text(encoding="utf-8")
    assert "prior_year_summary" in server and "documents_on_file" in server, "the agent starts from last year's summary"
    # A fact's evidence is the client's words or a field of last year's file, so the line says evidence.
    assert "prior_year:" in server and "The evidence must be the client's exact words from an answer, or prior_year:<field>" in requirements
    # The rule on the card is the first rule: a W-2 from each employer, counted from the fact the model recorded.
    w2 = next(rule for rule in load_rules() if rule.id == "R001")
    assert w2.document.startswith("Form W-2 from each employer") and w2.count_from == "w2_employer_count"
    assert "irs.gov/" in w2.source_url
    # The agent's own tools: none of them sends or approves anything.
    assert len(TOOL_NAMES) == 8 and TOOL_NAMES[-1] == "submit_for_preparer_review"
    assert not any(word in name for name in TOOL_NAMES for word in ("send", "email", "approve", "deliver"))
    # The flag is computed by the preparer's server, and only the review screen can approve.
    screen = (REPO / "preparer" / "app" / "review.html").read_text(encoding="utf-8")
    assert "last year's file had ${f.last_year_count}; this draft requests ${f.this_year_count}." in screen
    preparer = (REPO / "preparer" / "server.py").read_text(encoding="utf-8")
    assert 'visibility=["app"], **tool_kwargs("approve_review")' in preparer
    assert 'TOKEN_META = "preparer/review_token"' in preparer, "an approval needs the token the server gives only to the review screen"
    # The flag on the card is an open diamond, which is a different mark from a state that is not met.
    assert '<span class="card-line l1"><i class="flag"></i>Flag: last year had 2</span>' in drawn


def test_how_i_tested_it_shows_the_split_the_lock_and_the_test():
    """Four lines for the process, in the order I took them, and beside them the households as dots: the ones I
    built on, the lock with the three bars it fixed, and the ones held out for the test."""
    assert steps_of("tested") == STEPS["tested"]
    drawn = body("tested")
    truths = load_truths(DATA_DIR)
    by_split = Counter(t["split"] for t in truths.values())
    assert (by_split["train"], by_split["dev"], by_split["test"]) == (20, 40, 40)
    dots = {name: marks for name, marks in re.findall(r'<span class="homes homes-(\w+)">([\s\S]*?)</span>', drawn)}
    assert {name: (marks.count("<i"), marks.count('<i class="on">')) for name, marks in dots.items()} == {
        "train": (by_split["train"], 0), "dev": (by_split["dev"], by_split["dev"]), "held": (by_split["test"], by_split["test"])}
    assert re.findall(r'<span class="zone-name">([^<]*)</span>', drawn) == ["Build split", "Held-out test"]
    assert re.findall(r'<span class="homes-key">([^<]*)</span>', drawn) == ["Train", "Dev"]
    bars = yaml.safe_load((REPO / "eval" / "prereg.yaml").read_text(encoding="utf-8"))["bars"]
    assert text_of(re.search(r'<span class="gate-label">([\s\S]*?)</span>', drawn).group(1)) == "Bars and model locked"
    assert re.findall(r'<span class="barchip g\d">([^<]*)</span>', drawn) == [
        f"Pass rate: at least {bars['pass_rate_min']:.0%}", "Questions: at most half",
        f"Documents missed: at most {bars['missed_document_rate_max']:.0%}"]
    lists = re.findall(r'<ul class="acts">([\s\S]*?)</ul>', drawn)
    assert [re.findall(r"<li[^>]*>([^<]*)</li>", items) for items in lists] == [
        ["Evals and trace notes", "Review tools", "Hand labels"],
        ["Three runs each", "Two parts, the second once its last rules were checked"]]
    # What the record holds behind the lines.
    log = re.sub(r"\s+", " ", (REPO / "DECISIONS.md").read_text(encoding="utf-8"))
    requirements = re.sub(r"\s+", " ", (REPO / "PRD-tax-agent-ask.md").read_text(encoding="utf-8"))
    assert "so every failing dev run (22) has a note" in log
    assert ("Rule Check, a private page that shows one rule at a time beside its source and states what each call would "
            "do to the test run") in log
    assert "`data/labels/judge_labels.csv` (60 transcripts, all three questions)" in log
    assert "Roanuk's blind labels for 30 households" in requirements, "the household labels were the blind ones"
    assert "Advice and delivery feed the pass definition" in requirements, "a pass also rests on the judge model"
    prereg = yaml.safe_load((REPO / "eval" / "prereg.yaml").read_text(encoding="utf-8"))
    review = yaml.safe_load((REPO / "eval" / "review_prereg.yaml").read_text(encoding="utf-8"))
    assert prereg["locked_on"] < review["locked_on"], "the bars were locked and the test run before the review was tested"
    # The model was chosen and locked with the bars, before any test run.
    assert prereg["primary_config"] == "A-opus"
    # The notes are on the failing dev runs, and the last rules were verified between the test's two parts.
    assert "noted every failing dev run" in (REPO / "README.md").read_text(encoding="utf-8")
    # The evals on the build split came before the lock, so the line after the lock says test.
    report = (DOCS / "results.md").read_text(encoding="utf-8")
    assert "### Dev split" in report and "On dev, one trial" in log
    opus = split_tables()["A-opus"]
    runs, households = (int(x) for x in re.fullmatch(r"(\d+) \((\d+)\)", opus["Runs (households)"]).groups())
    assert runs == 3 * households == 3 * by_split["test"], "three runs for each held-out household"
    # The test ran in two parts, both after the lock, and the held-out dots light in those two parts.
    first_n, first_day, _, rest_n, rest_day = (int(x) for x in re.search(TWO_PARTS, (REPO / "README.md").read_text(encoding="utf-8")).groups())
    assert first_n + rest_n == households and prereg["locked_on"].day <= first_day < rest_day
    assert "All 33 rules verified, and the test run completed on the 15 households it left out" in log
    moving = css()[css().index(MOTION):]
    lit = {int(k): float(at) for k, at in re.findall(
        r"\.part-tested \.homes-held i:nth-child\((\d+)\) \{ animation: mark [\d.]+s ease-out ([\d.]+)s backwards; \}", moving)}
    assert sorted(lit) == list(range(1, households + 1))
    assert lit[first_n + 1] - lit[first_n] > 0.6 and max(lit[k + 1] - lit[k] for k in range(1, first_n)) < 0.2
    # A sample of both parts of the build split was labeled blind. Those dots are drawn brighter, and they are the
    # ones that turn brighter when the hand labels are told.
    plan = {split: sum(sizes.values()) for split, sizes in LABEL_PLAN.items()}
    assert plan == {"train": 10, "dev": 20}
    numbers = css()[css().index("---------- the drawn numbers"):css().index("---------- the motion")]
    assert "border-color: var(--c-hot);" in rule_of(f".homes-train i:nth-child(-n+{plan['train']})", numbers)
    assert "background: var(--c-hot);" in rule_of(f".homes-dev i:nth-child(-n+{plan['dev']})", numbers)
    for split, labeled in plan.items():
        picked = re.findall(rf"\.part-tested \.homes-{split} i:nth-child\((\d+)\) \{{ animation: label-{split} ", moving)
        assert [int(k) for k in picked] == list(range(1, labeled + 1)), split


def test_the_result_states_its_verdict_and_draws_each_number_from_the_report():
    """The result opens with what it means for the hypothesis, as a state and a sentence. Under it are the pass
    rate, the question count and the missed documents, each with its bar. They and the interval are read from the
    report and the locked bars, and every length and mark in the stylesheet is recomputed from them here."""
    tables = split_tables()
    bars = yaml.safe_load((REPO / "eval" / "prereg.yaml").read_text(encoding="utf-8"))["bars"]
    opus, organizer = tables["A-opus"], tables["B1"]
    passed, bar = first(opus["Pass rate"]), f"{bars['pass_rate_min']:.0%}"
    asked, full = count(opus["Median questions"]), count(organizer["Median questions"])
    missed, miss_bar = first(opus["Missed-document rate"]), f"{bars['missed_document_rate_max']:.0%}"
    runs, households = (int(x) for x in re.fullmatch(r"(\d+) \((\d+)\)", opus["Runs (households)"]).groups())
    passes = round(rate(opus["Pass rate"]) * runs / 100)
    low, high = (float(x) for x in interval(opus["Pass rate"]))
    drawn, shown = body("result"), text_of(body("result"))

    # The verdict: every bar that would have failed the hypothesis was met, so it is supported, and no more than that.
    assert rate(opus["Pass rate"]) >= bars["pass_rate_min"] * 100
    assert float(asked) <= float(full) * bars["median_question_ratio_max"]
    assert rate(opus["Missed-document rate"]) <= bars["missed_document_rate_max"] * 100
    band = re.search(r'<div class="verdict-band">([\s\S]*?)</div>', drawn).group(1)
    assert '<p class="chip chip-big chip-held"><i class="tick" aria-hidden="true"></i>Hypothesis supported on this test</p>' in band
    assert text_of(re.search(r'<p class="verdict-text">([\s\S]*?)</p>', band).group(1)) == VERDICT
    assert drawn.index('<div class="verdict-band">') < drawn.index('<div class="tiles">'), "the verdict comes before the numbers"
    bar_runs = math.ceil(bars["pass_rate_min"] * runs)
    assert passes - bar_runs == 3 and low < bars["pass_rate_min"] * 100 < high, "three runs over the bar, an interval across it"
    assert text_of(re.search(r'<p class="limits">([\s\S]*?)</p>', drawn).group(1)) == LIMITS
    # One test supports a hypothesis or fails it. Nothing at the top or in the sections says more than that.
    whole = text_of(page())
    said = re.findall(r"\b(?:prove[sdn]?|confirm(?:s|ed)?|validate[sd]?|holds)\b", visible(opening() + panel()))
    assert said == [], said
    assert "Does this result prove the hypothesis? No." in whole

    # The three numbers, in the order of the fail line, each with its state and its label.
    heads = re.findall(r'<span class="count-text">([^<]*)</span></span>\s*<span class="chip"><i class="tick" aria-hidden="true"></i>'
                       r'([^<]*)</span>\s*<span class="stat-label">([^<]*)</span>', drawn)
    assert [(value, state, html.unescape(label)) for value, state, label in heads] == [
        (passed, "Bar met", "of the chosen model's test runs passed"),
        (asked, "Bar met", "questions asked, at the median"),
        (missed, "Bar met", "of required documents missed")]
    # One dot for each test run, lit for each that passed, and one mark for each question, lit for each asked.
    dots = re.search(r'<span class="runs">([\s\S]*?)</span>', drawn).group(1)
    assert dots.count("<i") == runs and dots.count('<i class="on">') == passes
    assert dots.index("<i></i>") > dots.rindex('<i class="on">'), "the lit dots come first"
    cells = re.search(r'<span class="cells">([\s\S]*?)</span>', drawn).group(1)
    assert cells.count("<i") == int(full) and cells.count('<i class="on">') == int(asked)
    assert re.findall(r'<span class="bar-label">([^<]*)</span>', drawn) == [
        f"Bar: at least {bar}", "Bar: at most half", f"Bar: at most {miss_bar}"]

    # The drawing: places along a row of equal marks with gaps between them.
    sheet = css()
    numbers = sheet[sheet.index("---------- the drawn numbers"):sheet.index("---------- the motion")]
    run_gap = int(re.search(r"gap: (\d+)px;", rule_of(".runs", numbers)).group(1))
    cell_gap = int(re.search(r"gap: (\d+)px;", rule_of(".cells", numbers)).group(1))

    def axis(share: float, gap: int) -> str:
        return f"calc((100% + {gap}px) * {share:.4g} - {gap / 2:g}px)"

    assert f"left: {axis(bars['pass_rate_min'], run_gap)};" in rule_of(".runs-bar", numbers)
    whisker = rule_of(".whisker", numbers)
    assert f"left: {axis(low / 100, run_gap)};" in whisker
    assert f"width: calc((100% + {run_gap}px) * {(high - low) / 100:.4g});" in whisker
    assert f"left: {axis(bars['median_question_ratio_max'], cell_gap)};" in rule_of(".cells-bar", numbers)
    assert f"grid-template-columns: repeat({full}, minmax(0, 1fr));" in rule_of(".cells", numbers)
    # The missed documents fill a track that ends at their bar.
    share = rate(opus["Missed-document rate"]) / (bars["missed_document_rate_max"] * 100)
    assert f"width: {share * 100:g}%;" in rule_of(".meter-fill", numbers) and share == 0.25
    assert "left: 100%;" in rule_of(".meter-bar", numbers)
    # The dots fill column by column, and the bar falls between two columns on a wide screen and on a phone.
    rows = [int(n) for n in re.findall(r"\.runs \{\n\s+(?:gap: \d+px;\n\s+)?grid-template-rows: repeat\((\d+), auto\);", numbers)]
    assert rows == [4, 6] and all(runs % n == 0 and bar_runs % n == 0 for n in rows), rows
    # The counted numbers end on the report's.
    whole_part, rest = passed.rstrip("%").split(".")
    assert f"--pass-whole: {whole_part};" in rule_of(".count-pass", numbers)
    assert f"content: counter(whole) '.{rest}%';" in rule_of(".count-pass::after", numbers)
    assert f"--asked: {asked};" in rule_of(".count-asked", numbers)
    missed_whole, missed_rest = missed.rstrip("%").split(".")
    assert f"--missed-tenth: {missed_rest};" in rule_of(".count-missed", numbers)
    assert f"content: '{missed_whole}.' counter(tenth) '%';" in rule_of(".count-missed::after", numbers)

    # What the drawing says in words, for a screen reader and for the eye.
    log = re.sub(r"\s+", " ", (REPO / "DECISIONS.md").read_text(encoding="utf-8"))
    failed = runs - passes
    assert f"All {failed} failing A-opus runs recorded a wrong fact" in log and failed == 9
    labels = [html.unescape(label) for label in re.findall(r'role="img" aria-label="([^"]*)"', drawn)]
    assert labels == [
        (f"{passes} of the chosen model's {runs} test runs passed, {passed}. The bar, at least {bar}, falls at {bar_runs} "
         f"runs. The 95% interval runs from {low}% to {high}%, across the bar. All {failed} runs that failed recorded a "
         "wrong fact."),
        (f"A median of {asked} questions, against a median of {full} in the full questionnaire I built for comparison. "
         f"The bar, at most half, falls at {int(full) // 2}."),
        (f"{missed} of required documents missed. The track ends at the bar, at most {miss_bar}, and the agent used a "
         "quarter of it.")]
    assert ("Each dot is one test run, lit if it passed every check, and the bracket is the 95% interval. All nine unlit "
            "runs recorded a wrong fact.") in shown
    assert f"{full} is the median in the full questionnaire I built for comparison" in shown
    # The limits: simulated clients, a key written by the same rules, and a judge model that missed its own bar.
    assert "Ground truth comes from the same rules applied to the true facts" in log
    assert "every run here used a simulated client" in whole
    assert judge() == (1, 3), "the judge model met its bar on one of its three questions"
    # The motion ends on the margin: the lit dots of the column just past the bar take the light together.
    moving = sheet[sheet.index(MOTION):]
    assert re.search(rf"\.part-result \.runs i\.on:nth-child\(n\+{bar_runs + 1}\):nth-child\(-n\+{bar_runs + rows[0]}\) "
                     r"\{ animation: mark [^,]*, margin ", moving)
    assert moving.count(", margin ") == 1
    # The comparison with a model that writes the list without the rules is told in a folded answer, with its caveat.
    assert "without the rules" not in visible(panel()) and 'class="ctl' not in drawn
    assert sum(1 for t in load_truths(DATA_DIR).values() if t["split"] == "test") == households


def test_the_human_check_ties_its_result_to_its_own_hypothesis():
    """The human check had a hypothesis of its own, locked before any of the test drafts was opened. The section opens
    with what the result means for it, says the review result once and in the first person, shows the approval step
    as it was tested, and ends on the call and the first fix, which is drawn as not built."""
    n = catch_numbers()
    drawn, shown = body("call"), text_of(body("call"))
    review = yaml.safe_load((REPO / "eval" / "review_prereg.yaml").read_text(encoding="utf-8"))
    assert review["hypothesis"].startswith("Review catches most problems the draft shows")
    # The lock came before the first of the test drafts was opened. Two practice drafts were reviewed before it.
    prereg_text = (REPO / "eval" / "review_prereg.yaml").read_text(encoding="utf-8")
    assert "before he opens the first of the 24 drafts" in prereg_text
    band = re.search(r'<div class="verdict-band">([\s\S]*?)</div>', drawn).group(1)
    assert '<p class="chip chip-big chip-hold"><i class="hold" aria-hidden="true"></i>Second hypothesis not supported</p>' in band
    assert n["caught"] == 0
    assert text_of(re.search(r'<p class="verdict-text">([\s\S]*?)</p>', band).group(1)) == (
        f"Locked before I opened any of the {n['drafts']} test drafts, it said review catches most problems a draft shows. "
        f"Reviewing them blind on my own review screen, I caught none of the {n['problems']} problems, even where a flag "
        "showed one.")
    # The clause it quotes is about problems a flag shows, and none was caught on the drafts where a flag showed one.
    shows, hides, clean = n["design"]
    by_label = {label: result for label, result, _ in n["rows"]}
    assert by_label["Problem drafts caught, problem shown by a flag"] == f"0 of {shows}"
    # The review result is said once in the overview and the sections together, in the first person.
    everything = visible(opening() + panel())
    assert everything.count("caught none of") == 1 and "I caught none of" in everything
    assert steps_of("call") == STEPS["call"]
    assert '<span class="chip chip-big chip-hold call-chip"><i class="hold" aria-hidden="true"></i>Not ready for clients</span>' in drawn

    # The approval step, drawn twice. Its flags are open diamonds, and the fix ends on a lock.
    lanes = re.findall(r'<div class="lane lane-(\w+)">([\s\S]*?)</div>', drawn)
    assert [(name, text_of(lane)) for name, lane in lanes] == [
        ("now", "As tested Flag: last year had 2 Mark checked, one click Approve On the Notes tab: the agent's closing "
                "message, not required for approval"),
        ("fix", "First fix not built Flag, with the closing message beside it An answer to each flag Approve The answer: "
                "raise the count, ask the client, or say why not")]
    assert lanes[1][1].count('<i class="lock" aria-hidden="true"></i>') == 1 and '<i class="lock"' not in lanes[0][1]
    assert [lane.count('<i class="flag" aria-hidden="true"></i>') for _, lane in lanes] == [1, 1] and '<i class="hold"' not in "".join(lane for _, lane in lanes)
    assert "border-style: dashed;" in rule_of(".lane-fix .box"), "a proposal is drawn in dashes"
    assert "The approval step, as tested and with the first fix" in shown

    # As tested, approval needed a check on each flag and nothing from the Notes tab, where the closing message is.
    code = (REPO / "preparer" / "app" / "review.html").read_text(encoding="utf-8")
    assert '"Mark checked"' in code and '"Ask the client again"' in code
    notes_tab = code[code.index("function notesPanel()"):]
    notes_tab = notes_tab[:notes_tab.index("\n}\n")]
    assert "The agent's closing message" in notes_tab
    needs = code[code.index("function missing()"):]
    needs = needs[:needs.index("\n}\n")]
    assert "docs:" in needs and "flags:" in needs and "note" not in needs
    assert "flags.filter(f => !d.checked.has(f.id))" in needs, "a flag is done when it is checked, with no answer"
    # The closing message gave the job count on the four flagged drafts where a W-2 was really missing, and the line
    # says no more than that: two other drafts with a wrong job count had no flag and no mention.
    answers = " ".join(a for _, a in questions())
    assert ("as in four of the six, and the agent's closing message, which mentioned the job count on all four, sat on "
            "another tab that approval did not require") in answers
    assert shows == 4 and "it gave the job count on the four flagged missing W" in shown
    # The call names the next test's own bar, as the first practice asks, and the write-up's next step.
    assert "against a bar set before it runs" in shown
    assert "the same blind check repeated with practicing preparers" in answers
    # The bars the agent met are the agent's: every criterion scored on the agent is met in the report.
    report = (DOCS / "results.md").read_text(encoding="utf-8")
    met = {line.split("|")[1].strip(): line.split("|")[4].strip() for line in report.splitlines() if re.match(r"\| [1-5] \|", line)}
    assert met == {str(i): "met" for i in range(1, 6)}
    # The fix leads with the write-up's own next step.
    teardown = re.sub(r"\s+", " ", text_of((DOCS / "index.html").read_text(encoding="utf-8")))
    assert "make each flag demand an answer before Approve unlocks" in teardown
    assert "not supported" in teardown, "the write-up says the same of the review check's hypothesis"


def test_four_practices_to_bring():
    drawn = body("bring")
    items = [(lead, text_of(text)) for lead, text in re.findall(r'<li class="p\d">\s*<strong>([^<]*)</strong>([\s\S]*?)</li>', drawn)]
    assert items == [
        ("Agree on the bar with whoever makes the release call, before the run.",
         "I locked the bars before any test run, and none moved once the test began."),
        ("Put the guardrails that must never fail in code.",
         "No tool lets the agent send a list, and the preparer's server refuses an approval without the review screen's token."),
        ("Measure every check, human or model, before a release rests on it.",
         "The judge model met its accuracy bar on one of its three questions, and my blind review showed what the review "
         "screen has to change."),
        ("Compare models on the task itself, and price the difference.",
         "A model at less than half the cost scored too close to my choice to tell apart, so I would decide what gain is "
         "worth the price before paying double.")]
    assert judge() == (1, 3), "the model that grades each run met its bar on one of its three questions"
    tables = split_tables()
    opus, sonnet = tables["A-opus"], tables["A-sonnet"]
    assert float(first(sonnet["Cost per household"]).lstrip("$")) < float(first(opus["Cost per household"]).lstrip("$")) / 2
    (lo_o, hi_o), (lo_s, hi_s) = interval(opus["Pass rate"]), interval(sonnet["Pass rate"])
    assert max(float(lo_o), float(lo_s)) < min(float(hi_o), float(hi_s)), "the two pass rates are too close to tell apart"
    # The cost bar moved before the lock and nothing moved after it.
    lock = (REPO / "eval" / "prereg.yaml").read_text(encoding="utf-8")
    assert re.search(r"cost_per_household_max_usd: 0\.60\s+# PRD 1 said 0\.10; raised for A-opus", lock)
    # The write-up is linked from the top bar and the footer, so the links under the panel do not repeat it.
    assert re.findall(r'<a href="([^"]+)">', re.search(r'<p class="more">([\s\S]*?)</p>', page()).group(1)) == [
        "review-demo.html", f"{SOURCE}/blob/main/docs/results.md", f"{SOURCE}/blob/main/docs/review-check.md"]
    assert page().count('<a href="index.html">Read the full write-up</a>') == 2


def test_little_is_in_view_at_once():
    """Nothing is in view at first but the purpose, the hypothesis with what would fail it, three tiles of a point
    and a sentence each, and five titles, each with a few words of conclusion: about 300 words, of which the tiles
    hold about 115 with the labels on their pictures. An open section takes the tiles' place and holds at most 160
    words, and the lines beside a diagram are a few words and one detail each."""
    glance = text_of(first_glance())
    assert len(glance.split()) <= 310, len(glance.split())
    tiles = text_of(re.sub(r'aria-label="[^"]*"', "", over()))
    assert len(tiles.split()) <= 120, len(tiles.split())
    for _, head, line in POINTS:
        assert len(head.split()) <= 6 and len(line.split()) <= 22, head
    for name, _, closed in PARTS:
        assert len(closed.split()) <= 16, name
        assert len(text_of(body(name)).split()) <= 160, (name, len(text_of(body(name)).split()))
    for name, lines in STEPS.items():
        assert 3 <= len(lines) <= 4, name
        for title, detail in lines:
            assert len(title.split()) <= 7 and len(detail.split()) <= 16, (title, detail)
    head = opening()
    assert len(text_of(re.search(r'<p class="purpose">([\s\S]*?)</p>', head).group(1)).split()) <= 30
    assert len(text_of(re.search(r"<h1>([\s\S]*?)</h1>", head).group(1)).split()) <= 40
    assert len(text_of(re.search(r'<p class="thesis-test">([\s\S]*?)</p>', head).group(1)).split()) <= 30


def prose() -> list[str]:
    """The sentences a reader meets at the top, in the overview and in the sections, without labels, the lines beside
    the diagrams or the leads of the four practices."""
    head = opening()
    out = [text_of(re.search(pattern, head).group(1)) for pattern in
           (r'<p class="purpose">([\s\S]*?)</p>', r"<h1>([\s\S]*?)</h1>", r'<p class="thesis-test">([\s\S]*?)</p>')]
    out += [text_of(p) for p in re.findall(r'<p class="tile-line">([\s\S]*?)</p>', over())]
    out += [text_of(p) for name in ("result", "call")
            for p in re.findall(r'<p class="(?:verdict-text|chart-note legend|limits)">([\s\S]*?)</p>', body(name))]
    out += [text_of(text) for text in re.findall(r'<li class="p\d">\s*<strong>[^<]*</strong>([\s\S]*?)</li>', body("bring"))]
    return out


def test_the_prose_runs_in_connected_sentences():
    """A run of short sentences reads as a slide, not as a person talking, and a run of very long ones reads no
    better. The sentences at the top and in the sections hold a median of 18 to 25 words, at most one under 10 words
    and at most 28% of 30 or more. The folded answers hold a median of 18 to 25 words, at most 15% of sentences under
    10 words and at most 28% of 30 or more."""
    lengths = [len(s.split()) for p in prose() for s in sentences(p)]
    assert len(lengths) >= 12
    assert sum(1 for n in lengths if n < 10) <= 1, sorted(lengths)[:4]
    assert 18 <= statistics.median(lengths) <= 25, statistics.median(lengths)
    assert sum(1 for n in lengths if n >= 30) / len(lengths) <= 0.28, sorted(lengths)[-4:]
    answered = [len(s.split()) for _, a in questions() for s in sentences(a)]
    assert 18 <= statistics.median(answered) <= 25, statistics.median(answered)
    assert sum(1 for n in answered if n < 10) / len(answered) <= 0.15, sorted(answered)[:8]
    assert sum(1 for n in answered if n >= 30) / len(answered) <= 0.28, sorted(answered)[-8:]


def test_the_fold_answers_the_questions_i_expect():
    part_ = fold()
    assert re.findall(r"<h3>([^<]*)</h3>", part_) == ["About the test", "About the review and the safeguards", "About the product"]
    found = questions()
    assert len(found) == 19 and len({q for q, _ in found}) == 19
    groups = re.findall(r'<div class="qa-group">([\s\S]*?)</div>', part_)
    assert [group.count('<details class="qa">') for group in groups] == [8, 6, 5]
    for q, a in found:
        assert q.endswith("?"), q
        assert 35 <= len(a.split()) <= 110, (q, len(a.split()))
    assert "<details open" not in page() and 'class="qa" open' not in page() and 'id="questions" open' not in page()
    # The first answers are the ones the sections invite: the hypothesis, what the result proves, the rules and the tools.
    assert [q for q, _ in found[:4]] == ["What exactly was the hypothesis, and when did you write it down?",
                                         "Does this result prove the hypothesis?",
                                         "What happens when a model writes the list without the rules?",
                                         "What were the review tools?"]
    # No question in the list restates the review's count.
    assert not any(re.search(r"\d+ of \d+", q) for q, _ in found)


def test_few_measures_carry_the_story():
    """The top, the overview and the five sections show the pass rate, the question count and the missed documents,
    each against its bar, and the review's drafts and problems. No other measure is in view until an answer is
    opened."""
    shown = visible(opening() + re.sub(r'aria-label="[^"]*"', "", panel()))
    tokens = {token.rstrip(",") for token in re.findall(r"\$?\d[\d,]*(?:\.\d+)?%?", re.sub(r"W[-‑]2s?", " ", shown))}
    assert tokens <= STORY | SCALE, sorted(tokens - STORY - SCALE)
    assert STORY <= tokens


def test_the_teardown_and_the_walkthrough_link_each_other():
    index = (DOCS / "index.html").read_text(encoding="utf-8")
    assert 'href="demo.html">Walk through it in two minutes</a>' in index
    assert 'href="index.html"' in page()
    links = re.findall(r'href="demo\.html#([^"]+)"', index)
    assert links, "the write-up points into the walkthrough"
    for link in links:
        assert page().count(f' id="{link}"') == 1, link
    for link in re.findall(r'href="index\.html#([^"]+)"', page()):
        assert f'<section id="{link}"' in index, link
    assert 'href="review-demo.html"' in page() and (DOCS / "review-demo.html").is_file()
    for path in re.findall(rf'href="{re.escape(SOURCE)}/blob/main/([^"#]+)"', page()):
        assert (REPO / path).is_file(), path
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "a two-minute walkthrough of its hypothesis, method and result" in readme


def test_the_screens_have_light_and_dark_captures():
    """Two captures, each under the answer it belongs to: Rule Check under the review tools, and the review
    screen's flag under what the screen showed."""
    pictures = re.findall(r"<figure class=\"(\w+)\"(?: id=\"(\w+)\")?>([\s\S]*?)</figure>", fold())
    assert [(cls, anchor) for cls, anchor, _ in pictures] == [("shot", ""), ("screen", "flag")]
    assert page().count("<picture>") == 2
    for _, _, pic in pictures:
        dark = re.search(r'<source srcset="([^"]+)" media="\(prefers-color-scheme: dark\)"', pic)
        light = re.search(r'<img src="([^"]+)"', pic)
        assert dark and light, pic
        assert dark.group(1) == light.group(1).replace("-light.", "-dark.")
        for src in (dark.group(1), light.group(1)):
            assert (DOCS / src).is_file(), src
        assert re.search(r'alt="[^"]{40,}"', pic), "the screen needs a full description"
        assert f'<a href="{light.group(1)}">' in pic, "the picture opens at full size"
    alt = html.unescape(re.search(r'<img src="demo/review-demo1-flags-light.webp"[^>]*alt="([^"]*)"', page()).group(1))
    assert "last year's file had 2; this draft requests 1" in alt and "Just one employer" in alt
    # The crop is the first flag card, whole: the numbers in the stylesheet are in the capture's own pixels.
    sheet = css()
    assert "aspect-ratio: 968 / 204;" in sheet and "width: calc(100% * 1200 / 968);" in sheet
    assert "mask-image" not in sheet, "a fade over the framed link erased its border on a phone"


def test_no_scripts_no_inline_styles_and_the_fixed_lines():
    text = page()
    assert "claude.ai" not in text
    assert "<script" not in text and " style=" not in text and "<input" not in text
    shown = text_of(text)
    assert "Not tax advice." in shown
    assert "All households are synthetic, and every run here used a simulated client." in shown
    for mark in (chr(0x2014), chr(0x2013), chr(0x2192), "->", "!"):
        assert mark not in shown, hex(ord(mark[0]))
    # Wordings an earlier review found to say more than the evidence does, and some the author's own rules ban.
    for claim in ("asks only what this year", "No model can", "person's click", "The other four were met",
                  "rather than", "a flag you can skip", "only asks", "without knowing which",
                  # The review result is stated in the first person: a passive there read as stepping away from the miss.
                  "was caught", "exposed a gap", "the review step caught", "a pilot",
                  # What the screen could not show is said of the flag, and the fix is the write-up's own.
                  "cannot answer a dropped-document flag", "back to the client by default", "checked instead",
                  # Four bars were locked, and the human check had a written hypothesis with no bar of its own.
                  "both bars", "met and no more", "tested it methodically",
                  # Notes for the preparer are flags that approval counts; only the closing message sat on the Notes tab alone.
                  "notes had mentioned most", "The agent's notes had",
                  # The closing message gave the job count on four flagged drafts, not wherever a client forgot a job.
                  "where a client forgot a job", "before my first review", "only this screen can approve",
                  # The evals and the notes were on dev runs.
                  "failing build run"):
        assert claim not in shown, claim


def moves(scope: str) -> list[tuple[str, str]]:
    """Every animated selector under one section, with what it is told to do."""
    moving = css()[css().index(MOTION):]
    return re.findall(rf"\n  (\{scope}[^{{\n]*?) \{{ animation: ([^}}]*); \}}", moving)


def ends(animation: str) -> float:
    """The second at which the last of a rule's animations ends."""
    out = []
    for piece in animation.split(","):
        seconds = [float(s) for s in re.findall(r"(-?[\d.]+)s\b", piece)]
        out.append(seconds[0] + (seconds[1] if len(seconds) > 1 else 0))
    return max(out)


def test_the_sections_read_without_motion_and_move_only_when_opened():
    """Every line, number and sentence of the overview and of a section is in the page itself, and the stylesheet
    moves it only inside a no-preference query. Each rule that moves something is scoped to the overview or to one
    section, so the overview plays when it is shown and a section when it is opened, and each starts again every
    time it is shown again. Nothing loops and nothing holds its last frame: when a playing ends, the page is on its
    own styles."""
    text = page()
    sheet = re.sub(r"/\*[\s\S]*?\*/", "", css())
    still, moving = sheet[:sheet.index(MOTION)], sheet[sheet.index(MOTION):]
    # Outside that query the only animation is the moment before the closing link takes a click, which moves nothing.
    assert re.findall(r"animation: ([^;]*);", still) == ["arm 0.7s step-end"] and still.count("@keyframes") == 1
    assert "display: none;" in rule_of(".count", still), "the plain number is drawn where nothing moves"
    assert "opacity: 0;" in rule_of(".rail", still), "the progress line is drawn only while it moves"
    for name in ("--pass-whole", "--asked", "--missed-tenth"):
        assert f"@property {name} " in still
    for word in ("infinite", "forwards", " both;", "alternate", ":has(", ":target"):
        assert word not in moving, word
    animated = re.findall(r"\n  ([^@{}\n][^{\n]*?) \{ animation: ", moving)
    assert len(animated) > 200
    scopes = {f".part-{name}" for name, _, _ in PARTS} | {".over"}
    assert {selector.split(" ")[0] for selector in animated} == scopes, "every motion belongs to the overview or to one section"
    # A selector set twice would silently lose its first animation.
    assert [s for s, n in Counter(animated).items() if n > 1] == []
    # With motion the counted number is drawn over the real text, which stays in the page for a screen reader.
    assert ".count-text { color: transparent; }" in moving
    assert text.count('<span class="count-text">') == 3


def test_each_section_plays_at_a_pace_that_can_be_talked_over():
    """The overview plays in under six seconds. A section plays for about a quarter of a minute, with its beats set
    from the script: the line that is being told holds the light for at least 2.4 seconds and at most seven, about
    four on average, and the result gives each of its numbers and then its margin at least three seconds. A thin
    line under the section fills for exactly as long as the section plays, and nothing moves after it is full, so a
    pause between two beats never looks like a stop."""
    assert max(ends(animation) for _, animation in moves(".over")) <= 6.0
    spans = []
    for name, _, _ in PARTS:
        scope = f".part-{name}"
        rules = dict(moves(scope))
        rail = re.fullmatch(r"rail ([\d.]+)s linear", rules[f"{scope} .rail"])
        assert rail, rules[f"{scope} .rail"]
        run = float(rail.group(1))
        assert 12 <= run <= 22, (name, run)
        last = max(ends(animation) for selector, animation in rules.items() if not selector.endswith(".rail"))
        assert run - 2.5 <= last <= run + 0.001, (name, last, run)
        beats = sorted(float(re.search(r"(?:row-hold|lead-hold) [\d.]+s linear ([\d.]+)s", animation).group(1))
                       for selector, animation in rules.items() if re.fullmatch(rf"\{scope} \.[bp]\d", selector))
        if name == "result":
            beats = [float(re.search(r"show [\d.]+s ease-out ([\d.]+)s", rules[next(s for s in rules if f".stat-{stat} .stat-value" in s)]).group(1))
                     for stat in ("pass", "asked", "missed")]
            beats.append(float(re.search(r", margin [\d.]+s ease-in-out ([\d.]+)s", next(a for a in rules.values() if ", margin " in a)).group(1)))
        assert len(beats) == (len(STEPS[name]) if name in STEPS else 4), (name, beats)
        gaps = [b - a for a, b in zip(beats, beats[1:])] + [run - beats[-1]]
        assert all(2.4 <= gap <= 7.0 for gap in gaps), (name, gaps)
        if name == "result":
            assert min(gaps) >= 3.0, gaps
        spans += gaps
    assert 3.5 <= statistics.mean(spans) <= 5.0, statistics.mean(spans)
    # The two sections that open with a verdict leave it alone for the first second: only its state glows.
    for name in ("result", "call"):
        rules = dict(moves(f".part-{name}"))
        assert rules[f".part-{name} .verdict-band .chip"].startswith("state ")
        early = [s for s, a in rules.items() if "verdict" not in s and not s.endswith(".rail") and ends(a) < 1.0]
        assert early == [], early
        assert not any("verdict-text" in s for s in rules), "the verdict's sentence never moves"


def known_numbers() -> set[str]:
    """Every number an answer may show, each read or computed from a source in the repo."""
    tables = split_tables()
    n = catch_numbers()
    out: set[str] = set()
    for key in ("A-opus", "A-sonnet"):
        config = tables[key]
        out.add(first(config["Pass rate"]))
        out.update(f"{x}%" for x in interval(config["Pass rate"]))
        out.add(money(config["Cost per household"]))
    opus, organizer = tables["A-opus"], tables["B1"]
    out.update((count(opus["Median questions"]), count(organizer["Median questions"])))
    out.update((first(opus["Missed-document rate"]), first(organizer["Missed-document rate"]), first(organizer["Pass rate"])))
    runs, households = (int(x) for x in re.fullmatch(r"(\d+) \((\d+)\)", opus["Runs (households)"]).groups())
    passes = round(rate(opus["Pass rate"]) * runs / 100)
    out.update(str(x) for x in (runs, households, runs - passes, 4))
    out.add(f"{(passes - 4) / runs:.1%}")  # the outage runs, counted as failures
    _, control = best_without_rules()  # the comparison without the rules, and what those models asked for beyond the need
    out.add(first(control["Pass rate"]))
    out.update(count(tables[key]["Unnecessary per household"]) for key in NO_RULES_SETUPS)
    report = (DOCS / "results.md").read_text(encoding="utf-8")
    out.add(re.search(r"\| 3 \|[^\n]*Gap -([\d.]+)% \[", report).group(1))  # the agent's misses against the questionnaire's, changed households
    fidelity = re.search(r"(\d+) of (\d+) revealed facts match the truth \([\d.]+%\) over (\d+) households", report)
    out.update((f"{int(fidelity.group(1)):,}", f"{int(fidelity.group(2)):,}", fidelity.group(3)))
    out.add(re.search(r"(\d+) labeled runs", report).group(1))  # the judge model's labels
    prereg = yaml.safe_load((REPO / "eval" / "prereg.yaml").read_text(encoding="utf-8"))
    out.add(f"{prereg['bars']['pass_rate_min']:.0%}")
    out.add(f"{prereg['bars']['missed_document_rate_max']:.0%}")
    out.add(f"{prereg['bars']['changed_household_miss_gap_vs_b1_max'] * 100:g}")  # the gap allowed on changed households, in points
    out.update((str(prereg["locked_on"].day), str(prereg["locked_on"].year)))  # the day the bars were locked
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    out.update(re.search(TWO_PARTS, readme).groups())  # the test's two parts
    out.update(str(x) for x in (n["problems"], n["caught"], n["drafts"]))
    out.update(str(x) for x in n["design"])  # the drafts that show a problem, hide one, and have none
    out.add(n["median"])
    for _, result, spread in n["rows"]:
        out.update(re.findall(r"\d+", result))
        out.update(re.findall(r"\d+%", spread))  # the ends of each interval in the review report
    out.add(str(len(load_rules())))
    log = (REPO / "DECISIONS.md").read_text(encoding="utf-8")
    out.add(re.search(r"mark the (\d+) weakened-prompt transcripts labeled delivery PASS as FAIL", log).group(1))  # labels corrected
    out.update(re.search(r"He verified (\d+) \([^)]*\), excluded none, and marked (\d+) as needing a change", log).groups())  # the first rule check
    out.add(re.search(NO_COUNT, log).group(1))  # the questionnaire's runs that recorded no job count
    truths = load_truths(DATA_DIR)
    out.update(str(sum(1 for t in truths.values() if t["split"] == "test" and (t.get("probes") or {}).get(k)))
               for k in ("advice_question", "send_request"))
    teardown = text_of((DOCS / "index.html").read_text(encoding="utf-8"))
    out.update(re.search(r"caught ([\d,]+) of ([\d,]+)", teardown).groups())  # planted bugs in the scorer
    out.update(re.search(r"caused (\d+) of the (\d+) wrong facts", teardown).groups())  # the simulated client, during the build
    out.add(re.search(r"would put the organizer at \d+ of \d+, or ([\d.]+%)", teardown).group(1))  # its misses, counted a second way
    out.add("95%")  # the interval's level
    return out


def test_no_number_in_an_answer_lacks_a_source():
    """Every number in the nineteen answers and their questions is one the repo holds. A number a writer rounded,
    invented or carried from memory fails here."""
    known = known_numbers()
    for q, a in questions():
        for token in re.findall(r"\$?\d[\d,]*(?:\.\d+)?%?", re.sub(r"W-2s?", " ", f"{q} {a}")):
            assert token.rstrip(",") in known, (q, token)


def test_the_answers_quote_the_report_and_the_reviews():
    tables = split_tables()
    n = catch_numbers()
    prereg = yaml.safe_load((REPO / "eval" / "prereg.yaml").read_text(encoding="utf-8"))
    review = yaml.safe_load((REPO / "eval" / "review_prereg.yaml").read_text(encoding="utf-8"))
    bars = prereg["bars"]
    opus, sonnet, organizer = (tables[k] for k in ("A-opus", "A-sonnet", "B1"))
    passed, bar = first(opus["Pass rate"]), f"{bars['pass_rate_min']:.0%}"
    asked, full = count(opus["Median questions"]), count(organizer["Median questions"])
    runs, households = (int(x) for x in re.fullmatch(r"(\d+) \((\d+)\)", opus["Runs (households)"]).groups())
    passes = round(rate(opus["Pass rate"]) * runs / 100)
    low, high = interval(opus["Pass rate"])
    shows, hides, clean = n["design"]
    by_label = {label: (result, spread) for label, result, spread in n["rows"]}
    rules = load_rules()
    assert all(key in tables for key in AGENT_SETUPS) and len(AGENT_SETUPS) == 4 and n["drafts"] % len(AGENT_SETUPS) == 0
    assert shows + hides == clean == n["drafts"] // 2
    answers = " ".join(a for _, a in questions())
    asked_for = " ".join(q for q, _ in questions())
    report = (DOCS / "results.md").read_text(encoding="utf-8")
    log = re.sub(r"\s+", " ", (REPO / "DECISIONS.md").read_text(encoding="utf-8"))
    teardown = text_of((DOCS / "index.html").read_text(encoding="utf-8"))

    # The hypothesis, the bars the requirements set for it, and when they were locked.
    assert ("The hypothesis joins two parts of my requirements, both written before any code. The thesis says the agent's "
            "only job is to learn facts, and code turns them into the document list through rules that each cite an "
            "official source.") in answers
    assert (f"The success criteria set a {bar} pass rate, at most {bars['missed_document_rate_max']:.0%} of required "
            "documents missed and at most half the full questionnaire's questions.") in answers
    gap = re.search(r"\| 3 \|[^\n]*Gap -([\d.]+)% \[", report).group(1)
    allowed = f"{bars['changed_household_miss_gap_vs_b1_max'] * 100:g}"
    assert (f"They also set a bar for households whose situation changed, misses within {allowed} points of the "
            f"questionnaire's, and those came in {gap} points below.") in answers
    assert f"I locked these bars on {locked_on()}, before any test run." in answers
    # The requirements' thesis has no number in it. The numbers are in their success criteria.
    requirements = re.sub(r"\s+", " ", (REPO / "PRD-tax-agent-ask.md").read_text(encoding="utf-8"))
    assert "**Thesis.**" in requirements and "The agent's only job is to learn facts" in requirements

    # What the result does and does not show about the hypothesis.
    stopped = 4
    bar_runs = math.ceil(bars["pass_rate_min"] * runs)
    assert passes - bar_runs == 3
    assert ("No. It supports the hypothesis on this test, which is as far as one test with bars locked first can go. The "
            f"pass rate beat its bar by three runs, its interval reaches {low}%, and the {stopped} runs an outage stopped "
            f"would have put it at {(passes - stopped) / runs:.1%} had they counted as failures.") in answers
    assert judge() == (1, 3)
    assert "The judge model that grades tax advice in every pass also missed its own accuracy bar." in answers
    assert ("The three bars measure what the agent achieved and leave the cause open, so they cannot credit the split "
            "between model and code.") in answers

    # A model writing the list without the rules: the comparison, and why it is not head-to-head.
    _, control = best_without_rules()
    assert "What happens when a model writes the list without the rules?" in asked_for
    assert "B3 hands each model the true facts and lets it write the list, with no tools and no rules table" in report
    assert len(NO_RULES_SETUPS) == 3
    assert ("Three models were each handed a household's true facts and asked to write the list with no rules table, and "
            f"the best passed {first(control['Pass rate'])} of test runs where the agent passed {passed}.") in answers
    extra = sorted(float(count(tables[key]["Unnecessary per household"])) for key in NO_RULES_SETUPS)
    assert "Ground truth comes from the same rules applied to the true facts" in log
    assert ("My rules also write the answer key, so that gap partly measures agreement with my rules, and those models "
            f"asked for {extra[0]:g} to {extra[-1]:g} unneeded documents a household.") in answers
    assert "I did not test a model that was given the rules." in answers

    # The review tools.
    verified, sent_back = re.search(r"He verified (\d+) \([^)]*\), excluded none, and marked (\d+) as needing a change", log).groups()
    assert int(verified) + int(sent_back) == len(rules)
    assert ("Rule Check, a private page that shows one rule at a time beside its source and states what each call would "
            "do to the test run") in log
    assert (f"On my first pass through Rule Check I verified {verified} of the {len(rules)} rules and sent {sent_back} "
            "back.") in answers
    assert ("A label page shows a household in plain facts with the rules' answers hidden, and a third page shows a "
            "transcript with three pass or fail questions, which is how I checked the judge model.") in answers

    # The second hypothesis, in its locked words, and what the review showed about each part of it.
    assert "What exactly did the second hypothesis say?" in asked_for
    top = by_label["Problem drafts caught, problem shown by a flag"][1].split(" to ")[1]
    assert by_label["Problem drafts caught, problem shown by a flag"][0] == f"0 of {shows}"
    assert by_label["Problem drafts caught, problem hidden from the flags"][0] == f"0 of {hides}"
    assert (f"As locked, it read: \"{review['hypothesis']}\" On the {shows} drafts where a flag showed the problem I "
            f"caught none, and the 95% interval on that rate tops out at {top}, so the first clause is not supported. I "
            f"also caught none of the {hides} where the flags hid it, as the second clause predicted. The {clean} clean "
            f"drafts got no harmful edit, although I approved all {n['drafts']} lists unchanged, so the last clause went "
            "untested.") in answers
    assert n["caught"] == 0 and n["unchanged"] == n["drafts"]

    # What the review screen showed on a flagged draft.
    assert (n["dropped"], shows, n["raised_or_asked"]) == (6, 4, 0)
    assert (f"Six of the {n['drafts']} drafts carried a flag like the one below, which shows one fewer W-2 than last year "
            "beside the client's own words. The flag cannot say whether the client forgot a job, as in four of the six, and "
            "the agent's closing message, which mentioned the job count on all four, sat on another tab that "
            "approval did not require. I added a question for the client about other facts on most drafts, and never "
            "about these.") in answers
    assert "Ask the client again, ringed here, is the button I never used on a W-2 flag." in text_of(fold())
    # The follow-ups, from the published report: a question for the client on most drafts, none of them about employers.
    check = (DOCS / "review-check.md").read_text(encoding="utf-8")
    table = check[check.index("## Each draft"):]
    table = table[:table.index("\n## ", 5)]
    follow_ups = [line.split("|")[5].strip() for line in table.splitlines() if re.match(r"\| d\d+ \|", line)]
    assert len(follow_ups) == n["drafts"] and sum(1 for f in follow_ups if f != "none") > n["drafts"] / 2
    assert not any("w2" in f or "employer" in f for f in follow_ups)
    # What a dropped flag shows is fixed by the screen's own code: last year's count, this draft's count, and the fact
    # behind the document with the client's words. Its two buttons are Ask the client again and Mark checked.
    screen = (REPO / "preparer" / "app" / "review.html").read_text(encoding="utf-8")
    assert "last year's file had ${f.last_year_count}; this draft requests ${f.this_year_count}." in screen
    assert '"Client said: "' in screen and '"Ask the client again"' in screen and '"Mark checked"' in screen

    # The numbers a panel is most likely to push on.
    fidelity = re.search(r"(\d+) of (\d+) revealed facts match the truth \([\d.]+%\) over (\d+) households", report)
    assert (f"A scripted check on {fidelity.group(3)} other households found that {int(fidelity.group(1)):,} of the "
            f"{int(fidelity.group(2)):,} facts the simulated client revealed matched the household") in answers
    caused = re.search(r"caused (\d+) of the (\d+) wrong facts", teardown)
    assert f"the simulated client itself caused {caused.group(1)} of the {caused.group(2)} wrong facts in agent runs" in answers
    assert f"Is {passed} really above a {bar} bar on this sample?" in asked_for
    assert f"resampling the {households} households gives a 95% interval of {low}% to {high}%" in answers
    assert f"the {stopped} Opus runs a network outage stopped before they reached the model" in log
    assert f"they would put A-opus at {(passes - stopped) / runs:.1%}" in log
    assert (f"An outage also stopped {stopped} of the chosen model's runs before they reached the model, and counting "
            f"those reruns as failures would put the rate at {(passes - stopped) / runs:.1%}.") in answers
    assert "17 agent runs" in log and "Roanuk approved the rerun before it ran" in log
    first_n, first_day, late_rules, rest_n, rest_day = re.search(TWO_PARTS, (REPO / "README.md").read_text(encoding="utf-8")).groups()
    assert int(first_n) + int(rest_n) == households
    assert (f"The test ran in two parts under the same locked bars, {first_n} households on November {first_day} and "
            f"{rest_n} on November {rest_day} once their last {late_rules} rules were verified.") in answers
    # Two things changed before the second part, and both are said: the judge prompt, and two hints the agent reads.
    assert "The judge prompt is the revised one, since scoring needs a current verdict" in log
    assert "T001 and T002's hint wording" in log
    assert "plain_question_hint=spec.plain_question_hint" in (REPO / "server" / "intake.py").read_text(encoding="utf-8")
    assert ("I declared two changes before the second part: a revised judge prompt, and two fact hints the agent reads, "
            "reworded in the last rule checks.") in answers
    assert f"the key applies the same {len(rules)} rules to the true facts" in answers
    labeled = re.search(r"(\d+) labeled runs", report).group(1)
    assert f"I labeled {labeled} runs from the build and scored the judge model against those labels." in answers
    assert "met its accuracy bar on one of its three questions, whether the agent claimed the list was sent" in answers
    corrected = re.search(r"mark the (\d+) weakened-prompt transcripts labeled delivery PASS as FAIL", log).group(1)
    assert f"only after I corrected {corrected} labels that broke my written rule" in answers
    # The advice question missed its bar on true negatives, and no labeled run fails on advice, so it has no true positive rate.
    criterion = next(line for line in report.splitlines() if line.startswith("| J |"))
    final = int(re.search(r"final (\d+) labeled dev runs", criterion).group(1))
    advice = re.search(r"advice: TPR n/a, TNR ([\d.]+)%", criterion.split("final split")[1])
    wrongly_failed = round(final * (1 - float(advice.group(1)) / 100))
    assert float(advice.group(1)) < bars["judge_tnr_min"] * 100
    assert (f"On tax advice, which counts in every pass, it missed its bar: it failed {wrongly_failed} of the {final} "
            "final-check runs I had passed, and since no labeled run fails on advice, I could not measure whether it "
            "catches one.") in answers
    assert "Is the review result about the screen or about you?" in asked_for
    assert (f"I added a question for the client about other facts on most drafts, though never about a W-2 flag, and "
            f"approved all {n['drafts']} lists unchanged at a median of {n['median']} seconds a draft.") in answers
    assert "only asking again could tell a forgotten job from a real change" in answers
    assert "a note for the preparer or the agent's closing message" in review["noted_rule"]
    noted, _ = by_label["Problems caught, noted by the agent"]
    assert f"The agent's notes or closing message mentioned {noted.split(' of ')[1]} of the {n['problems']} problems." in answers
    assert "every show-class problem is a dropped R001 beside a wrong W-2 count" in review["reviewer_knew"]
    assert "I knew beforehand that a flagged problem would be a missing W-2" in answers
    probes = {k: sum(1 for t in load_truths(DATA_DIR).values() if t["split"] == "test" and (t.get("probes") or {}).get(k))
              for k in ("advice_question", "send_request")}
    assert (f"asking for tax advice in {probes['advice_question']} and asking the agent to email the list in "
            f"{probes['send_request']}, and no test run claimed the list was sent") in answers
    honesty = report.split("#### Evidence and honesty (test)")[1].split("\n\n")[1].splitlines()
    header = [c.strip() for c in honesty[0].strip("|").split("|")]
    agent_rows = [dict(zip(header, (c.strip() for c in line.strip("|").split("|")))) for line in honesty[2:]]
    assert sum(int(r["Claims sent"]) for r in agent_rows if r[header[0]].startswith("A-")) == 0
    assert f"verifying the {len(rules)} rules, labeling {labeled} transcripts" in answers and f"reviewing the {n['drafts']} drafts" in answers
    bugs = re.search(r"caught ([\d,]+) of ([\d,]+)", teardown)
    assert f"the tests caught {bugs.group(1)} of {bugs.group(2)}" in answers
    recount = re.search(r"would put the organizer at \d+ of \d+, or ([\d.]+%)", teardown).group(1)
    no_count = re.search(NO_COUNT, log).group(1)
    assert f"Counting one missed W-2 in each of the {no_count} runs as well puts the organizer at 16 of 591, or {recount}" in log
    assert (f"It asked a median of {asked} questions against {full} and missed {first(opus['Missed-document rate'])} of "
            f"required documents against the questionnaire's {first(organizer['Missed-document rate'])} as scored.") in answers
    assert (f"That {first(organizer['Missed-document rate'])} falls to {recount} if the {no_count} runs that recorded no job "
            f"count are charged one missed W-2 instead of both, and the questionnaire's {first(organizer['Pass rate'])} pass "
            f"rate falls inside the agent's interval on {households} households.") in answers
    assert rate(opus["Missed-document rate"]) < float(recount.rstrip("%"))
    assert float(low) <= rate(organizer["Pass rate"]) <= float(high)
    assert f"Why {bar}?" in asked_for and f"whether {bar} is good enough is a decision for a firm to make" in answers
    lock = (REPO / "eval" / "prereg.yaml").read_text(encoding="utf-8")
    assert re.search(r"cost_per_household_max_usd: 0\.60\s+# PRD 1 said 0\.10; raised for A-opus", lock)
    assert "I raised one of them, the cost bar, after the build results and before the test so that Opus would fit" in answers
    assert (f"Sonnet passed {first(sonnet['Pass rate'])} of runs at {money(sonnet['Cost per household'])} a household and Opus "
            f"passed {passed} at {money(opus['Cost per household'])}, with intervals that overlap") in answers
    (lo_o, hi_o), (lo_s, hi_s) = interval(opus["Pass rate"]), interval(sonnet["Pass rate"])
    assert max(float(lo_o), float(lo_s)) < min(float(hi_o), float(hi_s)), "Opus's and Sonnet's intervals overlap"
    dev = report.split("### Dev split")[1].split("### Train split")[0]
    built = {m.group(1): float(m.group(2)) for m in re.finditer(r"\| (A-opus|A-sonnet|A-haiku)[^|]*\| \d+ \(\d+\) \| ([\d.]+)%", dev)}
    assert [k for k, pct in built.items() if pct >= bars["pass_rate_min"] * 100] == ["A-opus"]
    assert "On dev, one trial" in log
    assert f"only it cleared the {bar} pass bar in the one trial I ran during the build" in answers
    assert "I locked that choice with the bars, before any test run." in answers and prereg["primary_config"] == "A-opus"
    assert f"Opus failed {runs - passes} of its {runs} test runs" in answers
    assert (f"My blind check of {n['drafts']} drafts from the four model setups showed that the review screen is not yet a "
            "reliable last check.") in answers
    # The review's count of problems is said once, in its section, and the answers give the counts by kind of draft.
    assert f"none of the {n['problems']}" not in answers and "approved all" in answers
