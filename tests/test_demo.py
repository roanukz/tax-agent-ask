"""The build walkthrough, docs/demo.html: its shape, its screens, and the rule facts it quotes."""

import re
from pathlib import Path

from rules.engine import load_rules

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
PAGE = DOCS / "demo.html"


def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_title_hero_and_seven_steps():
    text = page()
    assert "<title>What Should My Tax Agent Ask? The Build, Step by Step</title>" in text
    assert '<p class="eyebrow">Build walkthrough</p>' in text
    assert 'id="status"' in text
    steps = re.findall(r'<section id="step-(\d)">([\s\S]*?)</section>', text)
    assert [n for n, _ in steps] == ["1", "2", "3", "4", "5", "6", "7"]
    for n, body in steps:
        assert f"Step {n} of 7" in body
        assert body.count("<h2>") == 1
        assert '<div class="who">' in body and '<div class="thread">' in body


def test_the_teardown_and_the_walkthrough_link_each_other():
    assert 'href="demo.html">View a step by step demo of the build process</a>' in (DOCS / "index.html").read_text(encoding="utf-8")
    assert 'href="index.html"' in page()


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


def test_rule_facts_match_the_rules_file():
    rules = load_rules()
    traps = [r for r in rules if r.trap]
    assert len(rules) == 33 and len(traps) == 3
    text = re.sub(r"\s+", " ", page())
    assert f"Each of the {len(rules)} rules, {len(rules) - len(traps)} that request a document and {len(traps)} traps" in text
    r012 = next(r for r in rules if r.id == "R012")
    assert r012.document == "Form 1095-A, Health Insurance Marketplace Statement"
    assert r012.document in text
    assert r012.source_title.startswith("Reconciling your advance payments of the Premium Tax Credit")
