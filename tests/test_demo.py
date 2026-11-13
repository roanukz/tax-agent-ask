"""The five-minute walkthrough, docs/demo.html: its shape, its length, its screens, and the numbers it quotes.

The page is five decisions to talk through in about a minute each, with the longer working folded under each one
as backup. Its shape and its length are held here so it cannot grow back, and the numbers its visible layer quotes
are read from the generated results report, the committed reviews and the rules file. These tests read files only.
"""

import html
import re
from pathlib import Path

from rules.engine import load_rules
from test_teardown import catch_numbers, split_tables

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
PAGE = DOCS / "demo.html"
DECISIONS = 5
TRADEOFF = ["The call", "Rejected", "Cost"]


def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def text_of(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def visible(fragment: str) -> str:
    """A fragment's text with every folded backup left out: what is on screen before anything is opened."""
    return text_of(re.sub(r'<details class="record">[\s\S]*?</details>', " ", fragment))


def decisions() -> list[tuple[str, str]]:
    return re.findall(r'<section id="decision-(\d)">([\s\S]*?)</section>', page())


def test_title_hero_and_five_decisions():
    text = page()
    assert "<title>What Should My Tax Agent Ask? Five Decisions in Five Minutes</title>" in text
    assert '<p class="eyebrow">Five-minute walkthrough</p>' in text
    assert "<h1>Five decisions behind the tax agent</h1>" in text
    found = decisions()
    assert [n for n, _ in found] == [str(i) for i in range(1, DECISIONS + 1)]
    rail = re.search(r'<nav class="toc"[\s\S]*?</nav>', text).group(0)
    assert re.findall(r'<a href="#([^"]+)">', rail) == [f"decision-{i}" for i in range(1, DECISIONS + 1)] + ["next"]
    for n, body in found:
        assert f"Decision {n} of {DECISIONS}" in body
        assert body.count("<h2>") == 1
        shown = re.sub(r'<details class="record">[\s\S]*?</details>', " ", body)
        assert re.findall(r'<span class="k">([^<]*)</span>', shown) == TRADEOFF, n
        assert shown.count('class="table-scroll proof"') + shown.count('class="facts proof"') + shown.count('class="screen proof"') == 1, n
        assert body.count('<details class="record">') == 1 and '<span class="record-kicker">Backup</span>' in body
        target, word = (f"#decision-{int(n) + 1}", "Next") if int(n) < DECISIONS else ("#next", "Last")
        assert f'<p class="step-next"><a href="{target}">{word}: ' in body


def test_it_can_be_talked_through_in_five_minutes():
    """About a minute a decision: each one's visible text stays near what fits one screen, and the whole visible
    page stays under what a person can take in while someone talks for five minutes."""
    text = page()
    body = text[text.index("<body"):]
    assert len(visible(body).split()) <= 1600, len(visible(body).split())
    for n, part in decisions():
        prose = re.search(r"</h2>\s*<p>([\s\S]*?)</p>", part)
        assert prose is not None and len(text_of(prose.group(1)).split()) <= 65, (n, len(text_of(prose.group(1)).split()))
        assert len(visible(part).split()) <= 250, (n, len(visible(part).split()))
    close = re.search(r'<section id="next">([\s\S]*?)</section>', text).group(1)
    assert len(visible(close).split()) <= 190


def test_the_teardown_and_the_walkthrough_link_each_other():
    assert 'href="demo.html">Walk through it in five minutes</a>' in (DOCS / "index.html").read_text(encoding="utf-8")
    assert 'href="index.html"' in page()
    for link in re.findall(r'href="(demo\.html#[^"]+)"', (DOCS / "index.html").read_text(encoding="utf-8")):
        assert f'id="{link.split("#")[1]}"' in page(), link
    for link in re.findall(r'href="index\.html#([^"]+)"', page()):
        assert f'<section id="{link}"' in (DOCS / "index.html").read_text(encoding="utf-8"), link
    assert 'href="review-demo.html"' in page() and (DOCS / "review-demo.html").is_file()


def test_every_screen_has_a_light_and_a_dark_capture():
    text = page()
    pictures = re.findall(r"<picture>([\s\S]*?)</picture>", text)
    assert pictures
    for pic in pictures:
        dark = re.search(r'<source srcset="([^"]+)" media="\(prefers-color-scheme: dark\)"', pic)
        light = re.search(r'<img src="([^"]+)"', pic)
        assert dark and light, pic
        assert dark.group(1) == light.group(1).replace("-light.", "-dark.")
        for src in (dark.group(1), light.group(1)):
            assert (DOCS / src).is_file(), src
        assert re.search(r'alt="[^"]{40,}"', pic), "each screen needs a full description"


def test_no_links_to_private_pages_and_no_scripts():
    text = page()
    assert "claude.ai" not in text
    assert "<script" not in text
    assert "Not tax advice." in text
    assert not re.search(r"\b[Ss]teps? \d", text_of(text)), "the old step numbers are gone; point at decisions"


def test_the_visible_numbers_come_from_the_report_and_the_reviews():
    tables = split_tables()
    n = catch_numbers()

    def first(cell: str) -> str:
        return cell.split(" ")[0]

    def money(cell: str) -> str:
        return f"${float(first(cell).lstrip('$')):.2f}"

    def count(cell: str) -> str:
        return f"{float(first(cell)):g}"

    opus, sonnet, haiku, organizer, carried = (tables[k] for k in ("A-opus", "A-sonnet", "A-haiku", "B1", "B2"))
    text = page()
    hero = re.search(r'<header class="hero">([\s\S]*?)</header>', text).group(1)
    assert re.findall(r'<span class="fact-value">([^<]*)</span>', hero) == [
        first(opus["Pass rate"]), first(opus["Missed-document rate"]), count(opus["Median questions"]),
        f"{n['caught']} of {n['problems']}"]
    parts = dict(decisions())
    cell = '<td class="num">{}</td>'
    row = '<tr><th scope="row">{}</th>'
    one = parts["1"]
    assert row.format("The full organizer") + cell.format(count(organizer["Median questions"])) + cell.format(first(organizer["Missed-document rate"])) in one
    assert row.format("Last year's list") + cell.format(count(carried["Median questions"])) + cell.format(first(carried["Missed-document rate"])) in one
    assert f"the same {count(organizer['Median questions'])} questions" in text_of(one)
    three = parts["3"]
    for label, value in (("At least 90% of runs pass", first(opus["Pass rate"])),
                         ("At most 2% of required documents missed", first(opus["Missed-document rate"])),
                         (f"At most half the organizer's {count(organizer['Median questions'])} questions", count(opus["Median questions"])),
                         ("At most $0.60 a household, raised from $0.10 before the lock", money(opus["Cost per household"]))):
        assert row.format(html.escape(label, quote=False)) + cell.format(value) in three, label
    low, high = re.fullmatch(r"[\d.]+% \[([\d.]+), ([\d.]+)\]", opus["Pass rate"]).groups()
    assert f"pass rate is {low}% to {high}%" in text_of(three)
    four = parts["4"]
    for label, config in (("Opus, the one I chose", opus), ("Sonnet", sonnet), ("Haiku", haiku)):
        assert row.format(label) + cell.format(first(config["Pass rate"])) + cell.format(money(config["Cost per household"])) in four, label
    assert f"{money(opus['Cost per household'])} a household" in text_of(four)
    five = visible(parts["5"])
    assert n["caught"] == 0 and f"caught none of the {n['problems']} problems" in five
    assert f"reviewed {n['drafts']} test drafts blind" in five and f"{n['dropped']} drafts carried a flag like this one" in five
    assert n["raised_or_asked"] == 0 and f"My median time on a draft was {n['median']} seconds" in five


def test_rule_facts_match_the_rules_file():
    rules = load_rules()
    traps = [r for r in rules if r.trap]
    assert len(rules) == 33 and len(traps) == 3
    text = re.sub(r"\s+", " ", page())
    assert f"through {len(rules)} rules, each citing an IRS source" in visible(text)
    assert f"Each of the {len(rules)} rules, {len(rules) - len(traps)} that request a document and {len(traps)} traps" in text
    r012 = next(r for r in rules if r.id == "R012")
    assert r012.document == "Form 1095-A, Health Insurance Marketplace Statement"
    assert r012.document in text
    assert r012.source_title.startswith("Reconciling your advance payments of the Premium Tax Credit")
