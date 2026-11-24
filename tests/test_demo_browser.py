"""The walkthrough, docs/demo.html, in a browser: an overview first, titles that open their sections, and diagrams
that play when opened.

The page has no script. Its dark panel opens on an overview of three tiles, above five closed titles. A click on a
title puts that section in the overview's place and closes the one before, and an open section's diagram plays from
the start, driven by CSS. This file loads the page from disk in the installed Google Chrome, headless, through
Playwright's "chrome" channel (DECISIONS.md, 2026-11-05), and checks what a file reader cannot: that the overview is
what shows first and its pictures sit where their numbers say, that a plain mouse click on a title opens its own
section and moves nothing, that a second click or a double click on a title leaves its section open, that the
hypothesis, the five titles and any open section fit a laptop screen, that a diagram plays when its section opens
and again when it reopens, that the lines beside it take the light one at a time and the numbers count up to the
report's, that a title can still be clicked while a section plays, that with motion off every section is complete
and still, and that every drawn mark sits where its number says, in light and in dark. pytest-socket guards only
this Python process, not the browser, so every request that is not file: or data: is aborted and recorded, and the
test fails if the page tried one. It skips with a message where Playwright or Chrome is missing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "docs" / "demo.html"
STYLE = REPO / "docs" / "src" / "demo.css"
TOKENS = REPO / "docs" / "src" / "tokens.css"
PARTS = ["agent", "tested", "result", "call", "bring"]
LINES = {"agent": 4, "tested": 4, "call": 3}
# Width and height of the page area: a 13 inch laptop with browser toolbars, a larger one, a small one and a tablet.
WINDOWS = [(1280, 720), (1440, 900), (1024, 720), (768, 1024)]
# The page area of a laptop's browser, with and without a bookmarks bar, a small laptop's, and two larger ones.
LAPTOPS = [(1280, 720), (1440, 757), (1440, 780), (1440, 900), (1920, 1000)]
# Every animation is taken once, at the start, and then set to a time. An animation that has ended leaves the
# document's own list, so asking the document again after a late moment would miss it on the way back.
SET_TIME = """t => { window.taken = window.taken || document.getAnimations();
  window.taken.forEach(a => { a.pause(); a.currentTime = t; }); }"""
OPEN = """() => [...document.querySelectorAll('.part')]
  .filter(p => getComputedStyle(p.querySelector('.part-body')).display !== 'none')
  .map(p => p.className.replace('part part-', ''))"""
OVERVIEW = "() => getComputedStyle(document.querySelector('.over')).display !== 'none'"
CLEAR = "rgba(0, 0, 0, 0)"


def token(name: str) -> str:
    """A shared color, as the browser reports it."""
    value = re.search(rf"--{name}:\s*#([0-9a-f]{{6}});", TOKENS.read_text(encoding="utf-8")).group(1)
    return "rgb({}, {}, {})".format(*(int(value[i:i + 2], 16) for i in (0, 2, 4)))


LIT, HOT = token("primary-300"), token("primary-100")


def beats(name: str) -> dict[int, tuple[float, float]]:
    """When each line beside a section's diagram takes the light and for how long, from the stylesheet, in milliseconds."""
    sheet = STYLE.read_text(encoding="utf-8")
    found = re.findall(rf"\n  \.part-{name} \.b(\d) \{{ animation: row-hold ([\d.]+)s linear ([\d.]+)s; \}}", sheet)
    return {int(i): (float(at) * 1000, float(hold) * 1000) for i, hold, at in found}


def run_of(name: str) -> float:
    """How long a section plays, in milliseconds: the time its progress line takes to fill."""
    sheet = STYLE.read_text(encoding="utf-8")
    return float(re.search(rf"\n  \.part-{name} \.rail \{{ animation: rail ([\d.]+)s linear; \}}", sheet).group(1)) * 1000


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            chrome = p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # noqa: BLE001 - any launch failure means no usable Chrome here
            pytest.skip(f"Google Chrome is not available to Playwright here: {str(e).splitlines()[0]}")
        yield chrome
        chrome.close()


def open_page(browser, width: int, height: int, scheme: str = "light", motion: str = "reduce", anchor: str = ""):
    context = browser.new_context(color_scheme=scheme, viewport={"width": width, "height": height}, reduced_motion=motion)
    page = context.new_page()
    seen: dict[str, list[str]] = {"network": [], "errors": []}

    def route(r):
        if r.request.url.startswith(("file:", "data:")):
            r.continue_()
        else:
            seen["network"].append(r.request.url)
            r.abort()

    page.route("**/*", route)
    page.on("pageerror", lambda e: seen["errors"].append(str(e)))
    page.on("console", lambda m: seen["errors"].append(m.text) if m.type == "error" else None)
    page.goto(PAGE.as_uri() + anchor)
    return context, page, seen


def place_of(page, selector: str) -> list[float]:
    return page.evaluate("s => { const r = document.querySelector(s).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }", selector)


def click_title(page, name: str) -> list[float]:
    """A plain mouse click in the middle of a closed section's title. Nothing is scrolled into view and nothing is
    retried, so the title has to be on screen and on top where it is drawn. Returns where the click landed."""
    x, y = place_of(page, f".part-{name} .part-open .part-title")
    under = page.evaluate("p => { const a = document.elementFromPoint(p[0], p[1])?.closest('a'); return a ? a.className : null; }", [x, y])
    assert under == "part-link part-open", (name, under)
    page.mouse.click(x, y)
    page.wait_for_timeout(150)
    return [x, y]


def click_close(page, name: str) -> None:
    """A plain mouse click on the mark at the end of an open section's title row, once the link over it takes clicks."""
    page.wait_for_timeout(800)
    x, y = place_of(page, f".part-{name} .part-close")
    under = page.evaluate("p => { const a = document.elementFromPoint(p[0], p[1])?.closest('a'); return a ? a.className : null; }", [x, y])
    assert under == "part-close", (name, under)
    page.mouse.click(x, y)
    page.wait_for_timeout(150)


@pytest.mark.parametrize("width,height", WINDOWS + [(375, 812)])
def test_nothing_spills_sideways(browser, width, height):
    """On the overview, and with each section open in turn, the page is as wide as the window and nothing reaches
    past it."""
    context, page, seen = open_page(browser, width, height)
    try:
        for anchor in [""] + [f"#{name}" for name in PARTS]:
            page.goto("about:blank")
            page.goto(PAGE.as_uri() + anchor)
            assert page.evaluate(OPEN) == ([anchor[1:]] if anchor else [])
            assert page.evaluate(OVERVIEW) is (anchor == "")
            assert page.evaluate("document.documentElement.scrollWidth") == width, anchor
            spilled = page.evaluate("""() => [...document.querySelectorAll('header.hero *, main *')]
                .filter(e => !e.closest('details:not([open])') && e.getClientRects().length)
                .filter(e => e.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
                .map(e => e.tagName + '.' + e.className)""")
            assert spilled == [], anchor
        assert seen == {"network": [], "errors": []}
    finally:
        context.close()


@pytest.mark.parametrize("width,height", LAPTOPS)
def test_the_overview_is_what_shows_first(browser, width, height):
    """Before any click the panel shows three tiles, one for each point, each with a picture, above five closed
    titles. The purpose, the hypothesis, the tiles and the titles are all on screen before any scroll."""
    context, page, seen = open_page(browser, width, height)
    try:
        assert page.evaluate(OPEN) == [] and page.evaluate(OVERVIEW) is True
        at = page.evaluate("""() => { const box = sel => document.querySelector(sel).getBoundingClientRect();
            return { purpose: box('.purpose').top, over: [box('.over').top, box('.over').bottom], first: box('.part-agent').top,
                     last: box('.part-bring').bottom, panel: box('.console').bottom,
                     tiles: [...document.querySelectorAll('.tile')].map(t => { const r = t.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; }),
                     heads: [...document.querySelectorAll('.tile-head')].map(h => h.innerText) }; }""")
        assert at["purpose"] > 0 and at["over"][1] <= at["first"] and at["last"] <= at["panel"] <= height, at
        assert at["heads"] == ["Less than half the questions", "A test that could have failed", "Not ready for clients yet"]
        assert len(at["tiles"]) == 3 and len({t[1] for t in at["tiles"]}) == 1, "three tiles in one row"
        assert len({t[3] for t in at["tiles"]}) == 1 and all(t[2] > 300 and t[3] > 180 for t in at["tiles"]), at["tiles"]
        rows = page.evaluate("""() => [...document.querySelectorAll('.part-open')].map(a => ({
            n: a.querySelector('.part-n').textContent, closed: a.querySelector('.part-gist').innerText.trim().length > 10,
            height: Math.round(a.getBoundingClientRect().height), state: a.getAttribute('aria-expanded') }))""")
        assert [(r["n"], r["closed"], r["state"]) for r in rows] == [(str(i), True, "false") for i in range(1, 6)], rows
        assert all(r["height"] >= 38 for r in rows), "a title is a large enough target"
        assert seen == {"network": [], "errors": []}
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_overviews_pictures_sit_where_their_numbers_say(browser, scheme):
    """The tiles are bright on the dark panel in both color schemes, each in its own color with one dark ink. The
    first picture is two rows of 36 marks with all of one and 14 of the other filled. The second is a track filled
    to 92.5% with the bar across it at 90%, so the fill ends just past the bar. The third is three stops with the
    middle one filled."""
    context, page, _ = open_page(browser, 1440, 780, scheme)
    try:
        ink = token("primary-950")
        drawn = page.evaluate("""() => { const style = (sel, prop) => getComputedStyle(document.querySelector(sel))[prop];
            const filled = sel => [...document.querySelectorAll(sel)].filter(i => getComputedStyle(i).backgroundColor === style('.ask-full i', 'backgroundColor')).length;
            const box = sel => document.querySelector(sel).getBoundingClientRect();
            return { grounds: ['.tile-value', '.tile-proof', '.tile-call'].map(s => style(s, 'backgroundImage')),
                     inks: ['.tile-value .tile-head', '.tile-proof .tile-head', '.tile-call .tile-head', '.tile-line'].map(s => style(s, 'color')),
                     full: [document.querySelectorAll('.ask-full i').length, filled('.ask-full i')],
                     agent: [document.querySelectorAll('.ask-agent i').length, filled('.ask-agent i')],
                     mark: style('.ask-full i', 'backgroundColor'),
                     track: [box('.bar-scale').left, box('.bar-scale').right], fill: box('.bar-fill').right,
                     past: [box('.bar-past').left, box('.bar-past').right, style('.bar-past', 'backgroundColor')],
                     lock: !!document.querySelector('.gate-shut .lock') && box('.gate-shut .lock').width > 0,
                     bar: (box('.bar-at').left + box('.bar-at').right) / 2,
                     stops: [...document.querySelectorAll('.gate-stop')].map(s => [s.innerText.trim(), getComputedStyle(s).backgroundColor]) }; }""")
        assert len(set(drawn["grounds"])) == 3 and all("gradient" in g for g in drawn["grounds"]), "three tiles, three colors"
        assert set(drawn["inks"]) == {ink} and drawn["mark"] == ink
        assert drawn["full"] == [36, 36] and drawn["agent"] == [36, 14]
        left, right = drawn["track"]
        share = lambda x: (x - left) / (right - left)  # noqa: E731
        assert abs(share(drawn["fill"]) - 0.90) < 0.01 and abs(share(drawn["bar"]) - 0.90) < 0.01
        assert abs(share(drawn["past"][0]) - 0.90) < 0.01 and abs(share(drawn["past"][1]) - 0.925) < 0.01
        assert 3 < drawn["past"][1] - drawn["bar"] < 14 and drawn["past"][2] not in (ink, CLEAR), "the runs past the bar are a short bright piece"
        assert drawn["lock"], "the way on to the client carries a lock"
        assert [name for name, _ in drawn["stops"]] == ["Draft list", "Human check", "Client"]
        assert [ground == ink for _, ground in drawn["stops"]] == [False, True, False], "the path is held at the human check"
    finally:
        context.close()


def test_each_title_opens_its_own_section_in_the_overviews_place(browser):
    """A plain click on a title opens that section alone, in place of the overview or of the section before. The
    address names it and the page does not move. The mark at the end of the open title closes it and brings the
    overview back."""
    context, page, seen = open_page(browser, 1440, 780)
    try:
        for name in PARTS:
            click_title(page, name)
            assert page.evaluate(OPEN) == [name] and page.evaluate(OVERVIEW) is False
            assert page.evaluate("location.hash") == f"#{name}" and page.evaluate("scrollY") == 0
            shown = page.evaluate("""name => { const part = document.querySelector('.part-' + name);
                const close = part.querySelector('.part-close');
                return { open: getComputedStyle(part.querySelector('.part-open')).display, label: close.getAttribute('aria-label'),
                         state: close.getAttribute('aria-expanded'), controls: !!document.getElementById(close.getAttribute('aria-controls')),
                         target: Math.round(Math.min(close.getBoundingClientRect().width, close.getBoundingClientRect().height)) }; }""", name)
            assert shown["open"] == "none" and shown["label"].startswith("Close ") and shown["state"] == "true" and shown["controls"], shown
            assert shown["target"] >= 30, "the closing mark is large enough to hit"
        click_close(page, "bring")
        assert page.evaluate(OPEN) == [] and page.evaluate(OVERVIEW) is True
        assert page.evaluate("location.hash") == "#shut" and page.evaluate("scrollY") == 0
        assert seen == {"network": [], "errors": []}
    finally:
        context.close()


@pytest.mark.parametrize("width,height", [(1440, 780), (1280, 720), (1920, 1000)])
def test_a_second_click_or_a_double_click_leaves_the_section_open(browser, width, height):
    """The open title is plain words, so a second click on it does nothing. A pointer left where a title was clicked
    comes to rest on that title or inside the section that opened, never on another title or a link, so a double
    click, or two clicks a moment apart, opens one section and leaves it open, from the overview and from each
    section to the next."""
    context, page, _ = open_page(browser, width, height, motion="no-preference")
    try:
        address = page.url
        under_pointer = """p => { const e = document.elementFromPoint(p[0], p[1]);
            return { link: !!e.closest('a, summary'), part: e.closest('.part') ? e.closest('.part').className : null }; }"""
        for name in PARTS:
            x, y = click_title(page, name)
            assert page.evaluate(under_pointer, [x, y]) == {"link": False, "part": f"part part-{name}"}, name
            page.mouse.click(x, y)
            page.wait_for_timeout(120)
            page.mouse.dblclick(x, y)
            page.wait_for_timeout(120)
            assert page.evaluate(OPEN) == [name] and page.evaluate("location.hash") == f"#{name}", name
            assert page.url.split("#")[0] == address.split("#")[0]
        # a click low on a title's row, from the overview: the pointer still comes to rest on nothing that can be clicked
        for name in PARTS:
            page.goto("about:blank")
            page.goto(PAGE.as_uri())
            row = page.evaluate("s => { const r = document.querySelector(s).getBoundingClientRect(); return [r.left + 200, r.bottom - 3]; }", f".part-{name} .part-open")
            page.mouse.click(*row)
            page.wait_for_timeout(150)
            assert page.evaluate(OPEN) == [name] and page.evaluate(under_pointer, row)["link"] is False, name
        # the same from the overview to any title, not only the first
        for name in PARTS[1:]:
            page.goto("about:blank")
            page.goto(PAGE.as_uri())
            x, y = place_of(page, f".part-{name} .part-open .part-title")
            page.mouse.dblclick(x, y)
            page.wait_for_timeout(200)
            assert page.evaluate(OPEN) == [name], name
        # The mark at the end of a row: a double click on it, from the overview and from every other section, opens
        # the section and leaves it open, because the closing link that takes its place waits a moment for clicks.
        for start in [""] + PARTS:
            for name in PARTS:
                if name == start:
                    continue
                page.goto("about:blank")
                page.goto(PAGE.as_uri() + (f"#{start}" if start else ""))
                page.wait_for_timeout(100)
                x, y = place_of(page, f".part-{name} .part-open .part-cue")
                page.mouse.dblclick(x, y)
                page.wait_for_timeout(150)
                assert page.evaluate(OPEN) == [name] and page.evaluate("location.hash") == f"#{name}", (start, name)
                page.mouse.click(x, y)
                page.wait_for_timeout(150)
                assert page.url.split("#")[0] == address.split("#")[0]
    finally:
        context.close()


def test_opening_a_section_does_not_move_a_scrolled_page(browser):
    """On a laptop the anchors the titles point at are not drawn, so following one scrolls nothing, wherever the page
    is."""
    context, page, _ = open_page(browser, 1440, 780)
    try:
        page.evaluate("scrollTo({ top: 60, behavior: 'instant' })")
        assert page.evaluate("scrollY") == 60
        for name in ("agent", "result", "tested"):
            click_title(page, name)
            assert page.evaluate(OPEN) == [name] and page.evaluate("scrollY") == 60, name
    finally:
        context.close()


@pytest.mark.parametrize("width,height", LAPTOPS)
def test_any_open_section_fits_a_laptop_screen(browser, width, height):
    """With any one section open, the purpose, the hypothesis, all five titles and the open section are on screen
    before any scroll, so nothing has to be scrolled while it is talked through. The hypothesis takes two lines, and
    the panel keeps about one height from section to section."""
    context, page, _ = open_page(browser, width, height)
    try:
        bottoms = []
        for name in PARTS:
            page.goto("about:blank")
            page.goto(PAGE.as_uri() + f"#{name}")
            at = page.evaluate("""() => { const box = sel => document.querySelector(sel).getBoundingClientRect();
                return { purpose: box('.purpose').top, claim: box('h1').height,
                         line: parseFloat(getComputedStyle(document.querySelector('h1')).lineHeight),
                         panel: box('.console').bottom, last: box('.part-bring .part-head').bottom, scrolled: scrollY }; }""")
            assert at["scrolled"] == 0 and at["purpose"] > 0, (name, at)
            assert at["last"] <= at["panel"] <= height, (name, at)
            assert round(at["claim"] / at["line"]) == 2, (name, at)
            bottoms.append(at["panel"])
        assert max(bottoms) - min(bottoms) <= 140, bottoms
    finally:
        context.close()


def test_the_overview_plays_when_it_shows_and_a_section_when_it_opens(browser):
    """The overview plays once when the page loads, in a few seconds, and only it moves. An open section plays once,
    from the start, and then only it moves. Closed and opened again, it starts again from the beginning, and when the
    overview comes back it plays again too."""
    context, page, seen = open_page(browser, 1440, 780, motion="no-preference")
    try:
        inside = "sel => document.getAnimations().every(a => a.effect.target.closest(sel))"
        longest = "Math.max(...document.getAnimations().map(a => a.effect.getComputedTiming().endTime))"
        clock = "Math.max(...document.getAnimations().map(a => a.currentTime))"
        assert page.evaluate("document.getAnimations().length") >= 15 and page.evaluate(inside, ".over")
        assert 3000 <= page.evaluate(longest) <= 6000, "the overview is quick"
        for name in PARTS:
            click_title(page, name)
            assert page.evaluate("document.getAnimations().length") >= 9, name
            assert page.evaluate("document.getAnimations().every(a => a.effect.getComputedTiming().iterations === 1)")
            assert page.evaluate(inside, f".part-{name}"), name
            assert abs(page.evaluate(longest) - run_of(name)) < 1, name
        click_title(page, "result")
        page.wait_for_timeout(1400)
        assert page.evaluate(clock) > 1300
        click_close(page, "result")
        assert page.evaluate(inside, ".over") and page.evaluate(clock) < 600, "the overview is back and playing"
        click_title(page, "result")
        assert page.evaluate(clock) < 600 and page.evaluate("document.getAnimations().length") > 100
        assert seen == {"network": [], "errors": []}
    finally:
        context.close()


@pytest.mark.parametrize("name", list(LINES))
def test_the_lines_take_the_light_one_at_a_time(browser, name):
    """While a diagram plays, the line being told is lit and no other is. The words are never dimmed, so every line
    can be read from the first moment to the last."""
    context, page, _ = open_page(browser, 1440, 780, motion="no-preference", anchor=f"#{name}")
    try:
        page.evaluate(SET_TIME, 0)
        nodes = f"() => [...document.querySelectorAll('.part-{name} .step .node')].map(n => getComputedStyle(n).backgroundColor)"
        words = f"""() => [...document.querySelectorAll('.part-{name} .step-title, .part-{name} .step-detail')]
            .map(e => getComputedStyle(e).opacity + getComputedStyle(e).color)"""
        plain = page.evaluate(words)
        play = beats(name)
        assert sorted(play) == list(range(1, LINES[name] + 1))
        for line, (lit, hold) in play.items():
            page.evaluate(SET_TIME, lit + hold * 0.4)
            assert page.evaluate(nodes) == [LIT] * (line - 1) + [HOT] + [LIT] * (LINES[name] - line), (name, line)
            assert page.evaluate(words) == plain, "the words do not change while the light moves"
        page.evaluate(SET_TIME, run_of(name) + 50)
        assert page.evaluate(nodes) == [LIT] * LINES[name], "when the playing is over no line holds the light"
    finally:
        context.close()


def test_one_answer_moves_through_the_agent_card_by_card(browser):
    """What the agent does: the four cards fill in the order of the four lines, and the card being told is the
    bright one. The dashed box around the model and the code is there from the start and never dims its cards."""
    context, page, _ = open_page(browser, 1440, 780, motion="no-preference", anchor="#agent")
    try:
        page.evaluate(SET_TIME, 0)
        filled = """() => [1, 2, 3, 4].map(i => [...document.querySelectorAll('.part-agent .c' + i + ' > *')]
            .every(e => getComputedStyle(e).opacity === '1'))"""
        bright = "() => [1, 2, 3, 4].map(i => parseFloat(getComputedStyle(document.querySelector('.part-agent .c' + i)).opacity))"
        play = beats("agent")
        for line, (lit, hold) in play.items():
            page.evaluate(SET_TIME, lit + hold - 600)  # just before the next card starts to arrive
            assert page.evaluate(filled) == [True] * line + [False] * (4 - line), line
            shown = page.evaluate(bright)
            assert shown[:line] == [1] * line and all(o < 0.5 for o in shown[line:]), (line, shown)
            assert page.evaluate("getComputedStyle(document.querySelector('.agent-box')).opacity") == "1"
        page.evaluate(SET_TIME, run_of("agent") - 20)
        assert page.evaluate("getComputedStyle(document.querySelector('.agent-note')).opacity") == "1"
    finally:
        context.close()


def test_the_test_draws_the_lock_before_the_held_out_households(browser):
    """How I tested it: the households I built on come first, then the ones I labeled blind turn brighter, then the
    lock and its three bars, and only then do the held-out households light, in the test's two parts."""
    context, page, _ = open_page(browser, 1440, 780, motion="no-preference", anchor="#tested")
    try:
        page.evaluate(SET_TIME, 0)
        state = f"""() => ({{
            built: getComputedStyle(document.querySelector('.homes-dev')).opacity,
            labeled: [...document.querySelectorAll('.homes-dev i')].filter(d => getComputedStyle(d).backgroundColor === '{HOT}').length
                   + [...document.querySelectorAll('.homes-train i')].filter(d => getComputedStyle(d).borderTopColor === '{HOT}').length,
            gate: Math.round(document.querySelector('.gate-label').getBoundingClientRect().height) > 0
                  && getComputedStyle(document.querySelector('.gate-label')).opacity === '1',
            bars: [...document.querySelectorAll('.barchip')].filter(b => getComputedStyle(b).opacity === '1').length,
            held: [...document.querySelectorAll('.homes-held i')].filter(d => getComputedStyle(d).backgroundColor !== 'rgba(0, 0, 0, 0)').length }})"""
        play = beats("tested")
        page.evaluate(SET_TIME, play[2][0] + 900)
        assert page.evaluate(state) == {"built": "1", "labeled": 0, "gate": False, "bars": 0, "held": 0}
        page.evaluate(SET_TIME, play[4][0] - 100)
        assert page.evaluate(state) == {"built": "1", "labeled": 30, "gate": False, "bars": 0, "held": 0}
        page.evaluate(SET_TIME, play[4][0] + 1800)
        assert page.evaluate(state) == {"built": "1", "labeled": 30, "gate": True, "bars": 3, "held": 0}
        page.evaluate(SET_TIME, play[4][0] + 3200)
        assert page.evaluate(state)["held"] == 25, "the first part of the test"
        page.evaluate(SET_TIME, run_of("tested") - 20)
        assert page.evaluate(state) == {"built": "1", "labeled": 30, "gate": True, "bars": 3, "held": 40}
    finally:
        context.close()


def test_the_result_shows_its_verdict_first_and_counts_up_to_the_report(browser):
    """The verdict on the hypothesis can be read the moment the result opens, and for the first seconds only its
    state glows. Then the three bars are drawn, because they were locked before the data, the dots light and the
    pass rate counts up to the report's number in step with them, the other two numbers follow, and last the three
    runs past the bar take the light before the limits are said."""
    context, page, seen = open_page(browser, 1440, 780, motion="no-preference", anchor="#result")
    try:
        page.evaluate(SET_TIME, 0)
        lit_dots = "[...document.querySelectorAll('.runs i.on')].filter(d => getComputedStyle(d).backgroundColor !== 'rgba(0, 0, 0, 0)').length"
        whole = "parseInt(getComputedStyle(document.querySelector('.count-pass')).getPropertyValue('--pass-whole'))"
        start = page.evaluate(f"""() => ({{
            verdict: ['.verdict-band .chip', '.verdict-text'].map(s => getComputedStyle(document.querySelector('.part-result ' + s)).opacity),
            words: document.querySelector('.part-result .verdict-band').innerText.replace(/\\s+/g, ' ').trim(),
            bar: Math.round(document.querySelector('.runs-bar').getBoundingClientRect().height),
            number: getComputedStyle(document.querySelector('.stat-pass .stat-value')).opacity, lit: {lit_dots} }})""")
        assert start["verdict"] == ["1", "1"] and start["words"].startswith("This test supports the hypothesis It did not fail")
        assert (start["bar"], start["number"], start["lit"]) == (0, "0", 0), start
        sheet = STYLE.read_text(encoding="utf-8")
        count = re.search(r"\n  \.part-result \.count-pass \{ animation: count-pass ([\d.]+)s linear ([\d.]+)s backwards; \}", sheet)
        length, begin = float(count.group(1)) * 1000, float(count.group(2)) * 1000
        assert begin >= 2000, "the verdict has the first two seconds"
        page.evaluate(SET_TIME, begin - 50)
        before = page.evaluate(f"() => ({{ counted: {whole}, lit: {lit_dots}, bar: Math.round(document.querySelector('.runs-bar').getBoundingClientRect().height) }})")
        assert before["counted"] == 0 and before["lit"] == 0 and before["bar"] > 40, before
        page.evaluate(SET_TIME, begin + length / 2)
        half = page.evaluate(f"() => ({{ counted: {whole}, lit: {lit_dots} }})")
        assert 0 < half["counted"] < 92 and abs(half["counted"] - half["lit"] / 120 * 100) < 25, half
        page.evaluate(SET_TIME, begin + length + 400)
        assert page.evaluate(f"() => ({{ counted: {whole}, lit: {lit_dots} }})") == {"counted": 92, "lit": 111}
        # the margin: the three lit dots past the bar, and no others, take the light near the end
        pulse = re.search(r"animation: mark [\d.]+s ease-out [\d.]+s backwards, margin ([\d.]+)s ease-in-out ([\d.]+)s; \}", sheet)
        page.evaluate(SET_TIME, float(pulse.group(2)) * 1000 + float(pulse.group(1)) * 450)
        glowing = page.evaluate(f"[...document.querySelectorAll('.runs i')].map((d, k) => getComputedStyle(d).backgroundColor === '{HOT}' ? k + 1 : 0).filter(Boolean)")
        assert glowing == [109, 110, 111], glowing
        assert page.evaluate("getComputedStyle(document.querySelector('.part-result .limits')).opacity") == "0", "the limits come after the margin"
        page.evaluate(SET_TIME, run_of("result") - 20)
        end = page.evaluate(f"""() => ({{ lit: {lit_dots},
            cells: [...document.querySelectorAll('.cells i.on')].filter(d => getComputedStyle(d).backgroundColor === '{LIT}').length,
            missed: parseInt(getComputedStyle(document.querySelector('.count-missed')).getPropertyValue('--missed-tenth')),
            shown: ['.whisker', '.legend', '.full-note', '.limits', '.stat-pass .chip', '.stat-asked .chip', '.stat-missed .chip']
                .map(s => getComputedStyle(document.querySelector('.part-result ' + s)).opacity) }})""")
        assert end == {"lit": 111, "cells": 14, "missed": 5, "shown": ["1"] * 7}, end
        assert seen == {"network": [], "errors": []}
    finally:
        context.close()


def test_the_human_check_draws_the_step_as_tested_before_the_fix(browser):
    """The verdict on the second hypothesis and my call can be read from the first moment, and the flag is on the
    lane within a second and a half, so the lane never stands empty. The approval step is drawn as it was tested,
    then the message that sat on another tab, and the fix comes last, dim until its turn."""
    context, page, _ = open_page(browser, 1440, 780, motion="no-preference", anchor="#call")
    try:
        page.evaluate(SET_TIME, 0)
        state = """() => { const o = s => getComputedStyle(document.querySelector('.part-call ' + s)).opacity;
            return { verdict: [o('.verdict-band .chip'), o('.verdict-text'), o('.call-chip')],
                     tested: ['.x1', '.x2', '.x3'].map(o), tab: o('.x4'), fix: parseFloat(o('.lane-fix')),
                     built: ['.y1', '.y2', '.y3', '.y4'].map(o) }; }"""
        play = beats("call")
        first = page.evaluate(state)
        assert first["verdict"] == ["1", "1", "1"] and first["tested"] == ["0", "0", "0"] and first["fix"] < 0.5, first
        page.evaluate(SET_TIME, 1500)
        assert page.evaluate(state)["tested"] == ["1", "0", "0"], "the flag is there before the first line is told"
        page.evaluate(SET_TIME, play[1][0] + play[1][1] - 300)
        told = page.evaluate(state)
        assert told["tested"] == ["1", "1", "1"] and told["tab"] == "0" and told["fix"] < 0.5, told
        page.evaluate(SET_TIME, play[2][0] + play[2][1] - 300)
        told = page.evaluate(state)
        assert told["tab"] == "1" and told["fix"] < 0.5 and told["built"] == ["0", "0", "0", "0"], told
        page.evaluate(SET_TIME, run_of("call") - 20)
        told = page.evaluate(state)
        assert told["fix"] == 1 and told["built"] == ["1", "1", "1", "1"], told
    finally:
        context.close()


def test_a_title_can_be_clicked_while_a_section_plays(browser):
    """A part that is fading in is painted in its own layer, which once covered a link beside it. Each title here is
    clicked with a plain mouse click at its own place about a second into a playing: the closing mark of the open
    section, and the titles above and below it."""
    context, page, _ = open_page(browser, 1440, 780, motion="no-preference")
    try:
        click_title(page, "result")
        page.wait_for_timeout(1000)
        click_close(page, "result")
        assert page.evaluate(OPEN) == [] and page.evaluate(OVERVIEW) is True
        page.wait_for_timeout(600)
        click_title(page, "result")
        for name in ("call", "tested", "bring", "agent"):
            page.wait_for_timeout(1000)
            assert page.evaluate("document.getAnimations().length") > 0, "the open section is still playing"
            click_title(page, name)
            assert page.evaluate(OPEN) == [name] and page.evaluate("scrollY") == 0, name
    finally:
        context.close()


def test_the_keyboard_opens_a_section_and_goes_on_to_the_next(browser):
    """Enter on a title opens its section. The next Tab lands on that section's closing mark, and the one after on
    the next section's title, because each anchor sits just ahead of its own section."""
    context, page, _ = open_page(browser, 1440, 780, motion="no-preference")
    try:
        focused = "document.activeElement.className + '|' + (document.activeElement.getAttribute('href') || '')"
        page.focus(".part-agent .part-open")
        page.keyboard.press("Enter")
        page.wait_for_timeout(900)  # the closing mark takes focus and clicks after its first moment
        assert page.evaluate(OPEN) == ["agent"]
        page.keyboard.press("Tab")
        assert page.evaluate(focused) == "part-close|#shut"
        assert page.evaluate("document.activeElement.closest('.part').className") == "part part-agent"
        page.keyboard.press("Tab")
        assert page.evaluate(focused) == "part-link part-open|#tested"
        page.keyboard.press("Enter")
        page.wait_for_timeout(150)
        assert page.evaluate(OPEN) == ["tested"] and page.evaluate("scrollY") == 0
        # What a screen reader hears on a title is the title, with its state said by an attribute.
        assert page.evaluate("document.querySelector('.part-result .part-open').textContent.trim().startsWith('3The result')")
    finally:
        context.close()


def test_back_returns_to_the_section_before(browser):
    context, page, _ = open_page(browser, 1440, 780)
    try:
        for name in ("agent", "tested", "result"):
            click_title(page, name)
        page.go_back()
        page.wait_for_timeout(150)
        assert page.evaluate(OPEN) == ["tested"]
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 375])
def test_with_motion_off_everything_is_complete_and_still(browser, width):
    """Where motion is turned off, the overview and any opened section show their finished state at once: every
    tile, line, card, mark and number, with nothing moving and no progress line."""
    context, page, _ = open_page(browser, width, 900, motion="reduce")
    try:
        assert page.evaluate("document.getAnimations().length") == 0
        whole = page.evaluate("""() => [...document.querySelectorAll('.over .tile, .over .tile > *, .ask-marks i, .bar-fill, .bar-past, .bar-at, .gate-stop, .gate-way')]
            .every(e => e.getClientRects().length > 0 && getComputedStyle(e).opacity === '1')""")
        assert whole, "the overview is whole"
        parts = ".step, .card, .card > *, .agent-note, .homes, .acts li, .gate-label, .barchip, .stat-value, .chip, .chart-note, .limits, .box, .aside, .lane, .bring-list li, .verdict-text"
        for name in PARTS:
            page.goto("about:blank")
            page.goto(PAGE.as_uri() + f"#{name}")
            moving = page.evaluate("document.getAnimations().map(a => a.animationName).filter(n => n !== 'arm')")
            assert moving == [], (name, moving)  # the closing link's wait for clicks is not motion
            found = page.evaluate("""([name, parts]) => [...document.querySelector('.part-' + name + ' .part-body').querySelectorAll(parts)].map(e => {
                const box = e.getBoundingClientRect();
                return { cls: e.className, shown: box.width > 0 && box.height > 0 && getComputedStyle(e).opacity === '1' }; })""", [name, parts])
            assert len(found) >= 4 and all(f["shown"] for f in found), (name, [f["cls"] for f in found if not f["shown"]])
            assert page.evaluate("name => getComputedStyle(document.querySelector('.part-' + name + ' .rail')).opacity", name) == "0"
        page.goto("about:blank")
        page.goto(PAGE.as_uri() + "#result")
        numbers = page.evaluate("""() => [...document.querySelectorAll('.count-text')].map(n => ({ text: n.textContent,
            drawn: n.getBoundingClientRect().width > 20 && getComputedStyle(n).color !== 'rgba(0, 0, 0, 0)' }))""")
        assert numbers == [{"text": "92.5%", "drawn": True}, {"text": "14", "drawn": True}, {"text": "0.5%", "drawn": True}]
        assert page.evaluate("[...document.querySelectorAll('.count')].every(c => getComputedStyle(c).display === 'none')")
        page.goto("about:blank")
        page.goto(PAGE.as_uri() + "#tested")
        nodes = page.evaluate("[...document.querySelectorAll('.part-tested .step .node')].map(n => getComputedStyle(n).backgroundColor)")
        assert nodes == [LIT] * 4
    finally:
        context.close()


def test_a_pointer_resting_on_a_line_does_not_light_it(browser):
    """After a title is clicked the pointer rests inside the section, often on one of its lines. Only the telling
    lights a line, so the resting pointer must not light a second one."""
    context, page, _ = open_page(browser, 1440, 900, anchor="#agent")
    try:
        page.hover(".part-agent .step.b3")
        nodes = page.evaluate("[...document.querySelectorAll('.part-agent .step .node')].map(n => getComputedStyle(n).backgroundColor)")
        assert nodes == [LIT] * 4
        assert page.evaluate("getComputedStyle(document.querySelector('.part-agent .step.b3')).backgroundColor") == CLEAR
    finally:
        context.close()


@pytest.mark.parametrize("scheme,width", [("light", 1440), ("dark", 1440), ("light", 375)])
def test_the_drawings_show_and_sit_where_their_numbers_say(browser, scheme, width):
    """The panel is dark in both color schemes. Lit marks, unlit marks, the bars and the interval each differ from
    the ground behind them, and each mark sits where its number puts it: the pass bar between the 108th run and the
    109th, the interval from inside the 26th column of thirty to inside the 30th, across the bar, the question bar
    in the gap between the 18th question and the 19th, and the missed documents a quarter of the way to their bar."""
    context, page, _ = open_page(browser, width, 900, scheme, anchor="#result")
    try:
        drawn = page.evaluate("""() => {
            const color = (sel, prop) => getComputedStyle(document.querySelector(sel))[prop];
            return { ground: color('.console', 'backgroundColor'), band: color('.verdict-band', 'backgroundColor'),
                     on: color('.runs i.on', 'backgroundColor'), off: color('.runs i:not(.on)', 'borderTopColor'),
                     cell: color('.cells i.on', 'backgroundColor'), faint: color('.cells i:not(.on)', 'backgroundColor'),
                     outline: color('.cells i:not(.on)', 'borderTopWidth'), bar: color('.runs-bar', 'backgroundColor'),
                     fill: color('.meter-fill', 'backgroundColor'), whisker: color('.whisker', 'borderLeftColor'),
                     label: color('.bar-label', 'color'), ink: color('.stat-value', 'color'), text: color('.verdict-text', 'color') }; }""")
        assert drawn["ground"] == token("primary-950") and drawn["on"] == drawn["cell"] == drawn["fill"] == LIT
        for key in ("on", "off", "bar", "whisker", "label", "ink", "text", "band", "faint"):
            assert drawn[key] != drawn["ground"], key
        assert drawn["faint"] not in (CLEAR, LIT) and drawn["outline"] == "0px", "a question not asked is a faint mark with no outline"
        assert drawn["bar"] not in (drawn["on"], drawn["off"], drawn["whisker"]), "the bar is its own color"
        counts = page.evaluate("""() => ({ runs: document.querySelectorAll('.runs i').length, lit: document.querySelectorAll('.runs i.on').length,
            cells: document.querySelectorAll('.cells i').length, asked: document.querySelectorAll('.cells i.on').length })""")
        assert counts == {"runs": 120, "lit": 111, "cells": 36, "asked": 14}
        at = page.evaluate("""() => {
            const box = sel => document.querySelector(sel).getBoundingClientRect();
            const runs = [...document.querySelectorAll('.runs i')].map(d => d.getBoundingClientRect());
            const cells = [...document.querySelectorAll('.cells i')].map(d => d.getBoundingClientRect());
            const middle = b => (b.left + b.right) / 2;
            const x = middle(box('.runs-bar')), y = middle({ left: box('.whisker').top, right: box('.whisker').bottom });
            return { bar: middle(box('.runs-bar')), before: runs[107].right, after: runs[108].left,
                     axis: [runs[0].left, runs[119].right], whisker: [box('.whisker').left, box('.whisker').right],
                     half: [box('.cells-bar').left, box('.cells-bar').right], c18: cells[17].right, c19: cells[18].left,
                     line: [box('.runs-bar').top, box('.runs-bar').bottom], grid: [runs[0].top, runs[119].bottom], size: runs[0].width,
                     track: [box('.meter').left, box('.meter').right], fill: box('.meter-fill').right, limit: middle(box('.meter-bar')),
                     crossing: document.elementFromPoint(x, y).className,
                     label: parseFloat(getComputedStyle(document.querySelector('.bar-label')).fontSize) }; }""")
        assert at["before"] <= at["bar"] <= at["after"], at
        assert at["line"][0] < at["grid"][0] and at["line"][1] > at["grid"][1], "the bar runs through the whole grid"
        left, right = at["axis"]
        share = lambda x: (x - left) / (right - left)  # noqa: E731
        assert abs(share(at["whisker"][0]) - 0.858) < 0.02 and abs(share(at["whisker"][1]) - 0.983) < 0.02, at
        assert at["whisker"][0] < at["bar"] < at["whisker"][1], "the interval crosses the bar"
        assert at["crossing"] == "whisker", "where they cross, the interval is drawn over the bar, in one piece"
        assert at["c18"] - 0.6 <= at["half"][0] and at["half"][1] <= at["c19"] + 0.6, "the bar stands in the gap and covers no mark"
        start, end = at["track"]
        assert abs((at["fill"] - start) / (end - start) - 0.25) < 0.02 and abs(at["limit"] - end) <= 3, at
        assert at["size"] >= 8, "a dot is large enough to see"
        assert at["label"] >= 15, "a bar's label can be read on a shared screen"
        # The households, one dot each: twenty outlined and forty filled that I built on, and forty held out. The
        # thirty I labeled blind are brighter.
        page.goto("about:blank")
        page.goto(PAGE.as_uri() + "#tested")
        homes = page.evaluate(f"""() => Object.fromEntries(['train', 'dev', 'held'].map(k => {{
            const dots = [...document.querySelectorAll('.homes-' + k + ' i')];
            return [k, [dots.length, dots.filter(d => getComputedStyle(d).backgroundColor !== 'rgba(0, 0, 0, 0)').length,
                        dots.filter(d => getComputedStyle(d).borderTopColor === '{HOT}').length]]; }}))""")
        assert homes == {"train": [20, 0, 10], "dev": [40, 40, 20], "held": [40, 40, 0]}
        lock = page.evaluate("getComputedStyle(document.querySelector('.gate'), '::before').backgroundColor")
        assert lock == drawn["bar"], "the lock is drawn in the color of the bars it fixed"
        # A flag on the review screen is an open diamond, and a state that is not met is a filled one.
        marks = page.evaluate("""() => { const s = sel => getComputedStyle(document.querySelector(sel));
            return [s('.part-call .x1 .flag').backgroundColor, s('.part-call .x1 .flag').borderTopColor, s('.part-open .hold').backgroundColor]; }""")
        assert marks[0] == CLEAR and marks[1] == marks[2] == token("warning-300"), marks
    finally:
        context.close()


@pytest.mark.parametrize("width", [1239, 900, 375])
def test_on_a_narrow_screen_every_section_stacks_and_nothing_overlaps(browser, width):
    """Below the wide layout every open section is one column, lines above and diagram below, and no card, box or
    note lies over another. The tiles stand in a column on a phone."""
    context, page, _ = open_page(browser, width, 900)
    try:
        if width <= 900:
            tops = page.evaluate("[...document.querySelectorAll('.tile')].map(t => Math.round(t.getBoundingClientRect().top))")
            assert tops == sorted(tops) and len(set(tops)) == 3, "the three tiles stack"
        for name in PARTS:
            page.goto("about:blank")
            page.goto(PAGE.as_uri() + f"#{name}")
            tracks = page.evaluate("name => getComputedStyle(document.querySelector('.part-' + name + ' .part-body')).gridTemplateColumns.split(' ').length", name)
            assert tracks == 1, (name, tracks)
            lapped = page.evaluate("""name => { const boxes = [...document.querySelectorAll('.part-' + name + ' :is(.step, .card, .agent-note, .zone, .gate, .stat, .box, .aside, .verdict-band, .bring-list li, .limits)')]
                    .map(e => [e.className, e.getBoundingClientRect()]);
                const out = [];
                for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
                    const [a, b] = [boxes[i][1], boxes[j][1]];
                    if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) out.push(boxes[i][0] + ' / ' + boxes[j][0]);
                }
                return out; }""", name)
            assert lapped == [], (name, lapped)
    finally:
        context.close()


@pytest.mark.parametrize("width,height", [(1152, 624), (1440, 660), (375, 812)])
def test_where_a_section_cannot_fit_its_title_comes_into_view(browser, width, height):
    """At a browser zoom of 125%, in a very short window and on a phone, an open section may not fit the screen.
    There the anchors have a place just above their sections, so a click on a title brings that title to the top of
    the screen, with its section under it."""
    context, page, _ = open_page(browser, width, height)
    try:
        for name in ("result", "bring", "agent"):
            page.evaluate("s => document.querySelector(s).scrollIntoView({ block: 'center' })", f".part-{name} .part-open")
            click_title(page, name)
            top = page.evaluate("name => document.querySelector('.part-' + name + ' .part-head').getBoundingClientRect().top", name)
            assert page.evaluate(OPEN) == [name] and 0 <= top <= 40, (name, top)
    finally:
        context.close()


@pytest.mark.parametrize("width,height", [(1152, 624), (375, 812), (1440, 660)])
def test_where_a_click_scrolls_closing_brings_the_overview_into_view(browser, width, height):
    """On a narrow or very short screen following a title's link scrolls. There the anchor that closes is laid out
    above the overview, so closing a section brings the overview's tiles to the top of the screen, not past it."""
    context, page, _ = open_page(browser, width, height)
    try:
        page.evaluate("s => document.querySelector(s).scrollIntoView({ block: 'center' })", ".part-result .part-open")
        click_title(page, "result")
        click_close(page, "result")
        top = page.evaluate("document.querySelector('.over').getBoundingClientRect().top")
        assert page.evaluate(OPEN) == [] and page.evaluate(OVERVIEW) is True and 0 <= top <= 60, top
    finally:
        context.close()


@pytest.mark.parametrize("width,height", [(1440, 780), (1024, 720), (375, 812), (360, 780)])
def test_the_path_to_the_client_stays_whole_in_a_narrow_tile(browser, width, height):
    """The third picture's point is the way that is not open. On a zoomed laptop and on a phone the tile is narrow,
    and the three stops and the lock between the last two must still fit inside it."""
    context, page, _ = open_page(browser, width, height)
    try:
        at = page.evaluate("""() => { const box = sel => document.querySelector(sel).getBoundingClientRect();
            const tile = document.querySelector('.tile-call'), pad = parseFloat(getComputedStyle(tile).paddingRight);
            return { inner: tile.getBoundingClientRect().right - pad, client: box('.gate-off').right,
                     way: box('.gate-shut').width, lock: [box('.gate-shut .lock').left, box('.gate-shut .lock').right],
                     check: box('.gate-check').right, off: box('.gate-off').left,
                     scale: box('.tile-proof .bar-scale').height }; }""")
        assert at["client"] <= at["inner"] + 0.5, at
        assert at["way"] >= 24 and at["check"] < at["lock"][0] and at["lock"][1] < at["off"], at
        assert at["scale"] >= 13, "the track keeps its height when the label beside the number wraps"
    finally:
        context.close()


def test_the_states_are_whole_on_a_phone(browser):
    """On a phone a closed row wraps its conclusion under its title, and neither state is cut short."""
    context, page, _ = open_page(browser, 375, 812)
    try:
        chips = page.evaluate("""() => [...document.querySelectorAll('.part-open .chip')].map(c => {
            const box = c.getBoundingClientRect(), row = c.closest('a').getBoundingClientRect();
            return { text: c.textContent, inside: box.right <= row.right + 1 && box.left >= row.left, whole: c.scrollWidth <= c.clientWidth + 1 }; })""")
        assert [c["text"] for c in chips] == ["This test supports the hypothesis", "Not ready for clients"]
        assert all(c["inside"] and c["whole"] for c in chips), chips
        page.evaluate("document.querySelector('.part-call').scrollIntoView({ block: 'center' })")
        click_title(page, "call")
        inside = page.evaluate("""() => [...document.querySelectorAll('.part-call .chip')].filter(c => c.getClientRects().length)
            .every(c => c.getBoundingClientRect().right <= innerWidth && c.scrollWidth <= c.clientWidth + 1)""")
        assert inside
    finally:
        context.close()


def test_on_paper_the_overview_and_every_section_are_shown(browser):
    """Nothing can be clicked on paper, so the printed page shows the overview and all five sections, whichever one
    was open on screen."""
    context, page, _ = open_page(browser, 1280, 900, anchor="#result")
    try:
        page.emulate_media(media="print")
        assert page.evaluate(OPEN) == PARTS and page.evaluate(OVERVIEW) is True
        assert page.evaluate("[...document.querySelectorAll('.part-cue')].every(c => getComputedStyle(c).display === 'none')")
    finally:
        context.close()


def test_an_answer_opens_under_its_question(browser):
    context, page, _ = open_page(browser, 1280, 720)
    try:
        assert page.evaluate("document.querySelectorAll('details.qa').length") == 24
        assert page.evaluate("document.querySelectorAll('details[open]').length") == 0
        page.evaluate("document.querySelector('#questions').scrollIntoView({ block: 'center' })")
        page.locator("#questions > summary").click()
        assert page.evaluate("document.querySelector('#questions').open") is True
        page.locator("details.qa summary").first.click()
        assert page.evaluate("document.querySelector('details.qa').open") is True
        assert page.evaluate("document.querySelector('details.qa > p').getBoundingClientRect().height") > 20
        assert page.evaluate(OPEN) == [], "opening an answer opens no section"
    finally:
        context.close()


def test_the_write_ups_link_opens_the_flag(browser):
    """The write-up links to the flag's picture, which sits inside a folded answer inside the folded questions.
    Chrome opens both folds when the address points inside them, so the picture is on screen without a click."""
    context, page, _ = open_page(browser, 1280, 720, anchor="#flag")
    try:
        page.wait_for_timeout(300)
        state = page.evaluate("""() => { const f = document.querySelector('#flag');
            return { answer: f.closest('details.qa').open, questions: document.querySelector('#questions').open,
                     top: f.getBoundingClientRect().top, height: f.getBoundingClientRect().height }; }""")
        assert state["answer"] is True and state["questions"] is True, state
        assert state["height"] > 100 and -10 <= state["top"] <= 720, state
    finally:
        context.close()
