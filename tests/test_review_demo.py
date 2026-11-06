"""Preparer review screen, Block 3: a browser drives the static demo to an approved list with the keyboard only.

It runs docs/review-demo.html from disk in the installed Google Chrome, headless, through Playwright's
"chrome" channel, since Playwright can't download its own browsers on macOS 13 (DECISIONS.md, 2026-11-05).
pytest-socket guards only this Python process, not the browser, so every request that is not file: or
data: is aborted and recorded, and the test fails if the page tried one. It skips with a message where
Playwright or Chrome is missing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

playwright = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")

REPO = Path(__file__).resolve().parent.parent
DEMO = REPO / "docs" / "review-demo.html"
FREE_TEXT = "input, textarea, select, [contenteditable]"
STORAGE_SPY = """
window.__storage = [];
for (const name of ["localStorage", "sessionStorage", "indexedDB"]) {
  try {
    Object.defineProperty(window, name, { get() { window.__storage.push(name); return undefined; } });
  } catch (e) { window.__storage.push("could not watch " + name); }
}
"""


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            chrome = p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # noqa: BLE001 - any launch failure means no usable Chrome here
            pytest.skip(f"Google Chrome is not available to Playwright here: {str(e).splitlines()[0]}")
        yield chrome
        chrome.close()


def open_demo(browser, scheme: str = "light", width: int = 900):
    context = browser.new_context(color_scheme=scheme, viewport={"width": width, "height": 800})
    page = context.new_page()
    page.add_init_script(STORAGE_SPY)
    seen: dict[str, list[Any]] = {"network": [], "errors": []}

    def route(r):
        if r.request.url.startswith(("file:", "data:")):
            r.continue_()
        else:
            seen["network"].append(r.request.url)
            r.abort()

    page.route("**/*", route)
    page.on("dialog", lambda dialog: (seen["errors"].append(f"dialog: {dialog.type}"), dialog.dismiss()))
    page.on("pageerror", lambda e: seen["errors"].append(str(e)))
    page.on("console", lambda m: seen["errors"].append(m.text) if m.type == "error" else None)
    page.goto(DEMO.as_uri())
    page.wait_for_selector("#review-button")
    return page, seen


def focused(page) -> dict[str, Any]:
    return page.evaluate(
        "() => { const e = document.activeElement; return { tag: e.tagName, text: (e.textContent || '').trim(), "
        "id: e.id, role: e.getAttribute('role'), pressed: e.getAttribute('aria-pressed'), disabled: !!e.disabled }; }"
    )


def tab_to(page, want, limit: int = 300) -> dict[str, Any]:
    """Presses Tab until the focused element satisfies want; fails after limit presses."""
    for _ in range(limit):
        page.keyboard.press("Tab")
        f = focused(page)
        if want(f):
            return f
    raise AssertionError("the keyboard never reached the control")


def no_free_text(page) -> None:
    assert page.locator(FREE_TEXT).count() == 0


def small_targets(page) -> list[str]:
    return page.evaluate(
        "() => [...document.querySelectorAll('button, a.button')].filter(b => b.offsetParent)"
        ".filter(b => { const r = b.getBoundingClientRect(); return r.height < 44 || r.width < 44; })"
        ".map(b => b.textContent.trim())"
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_demo_draft_is_approved_with_the_keyboard_only(browser, scheme):
    page, seen = open_demo(browser, scheme)
    no_free_text(page)
    tab_to(page, lambda f: f["text"] == "Demo Client 2")
    page.keyboard.press("Enter")
    page.wait_for_selector("text=Demo Client 2: draft document list")
    tab_to(page, lambda f: f["id"] == "review-button")
    page.keyboard.press("Enter")
    page.wait_for_selector('[role="tab"]')
    assert focused(page)["role"] == "tab"
    assert page.locator("#missing").inner_text().startswith("To approve:")
    assert page.locator("button:has-text('Approve list')").is_disabled()
    no_free_text(page)

    # Flags: check every flag with the space bar.
    while page.locator("button:has-text('Mark checked')").count():
        tab_to(page, lambda f: f["text"] == "Mark checked")
        page.keyboard.press("Space")

    # Documents: arrow to the tab, then keep each document and decide each custom request.
    tab_to(page, lambda f: f["role"] == "tab" and f["text"].startswith("Flags"))
    page.keyboard.press("ArrowRight")
    assert focused(page)["text"].startswith("Documents")
    no_free_text(page)
    while page.locator("button[aria-pressed='false']:text-is('Keep')").count():
        tab_to(page, lambda f: f["text"] == "Keep" and f["pressed"] == "false")
        page.keyboard.press("Enter")
    assert small_targets(page) == []

    # Facts: ask the client again about one fact, then save and approve.
    tab_to(page, lambda f: f["role"] == "tab" and f["text"].startswith("Documents"))
    page.keyboard.press("ArrowRight")
    assert focused(page)["text"].startswith("Facts")
    tab_to(page, lambda f: f["text"] == "Ask the client again")
    page.keyboard.press("Enter")
    assert page.locator("#missing").inner_text() == "Ready to approve."
    tab_to(page, lambda f: f["text"] == "Save")
    page.keyboard.press("Enter")
    page.wait_for_selector("text=Saved at")
    tab_to(page, lambda f: f["text"] == "Approve list" and not f["disabled"])
    page.keyboard.press("Enter")
    page.wait_for_selector("#approved-text")
    text = page.locator("#approved-text").inner_text()
    assert text.startswith("Documents to request from Demo Client 2 for tax year 2026:")
    assert "Questions to ask the client:" in text
    assert focused(page)["id"] == "approved-text"
    assert "nothing was sent to the client" in page.locator("body").inner_text()
    no_free_text(page)
    assert seen == {"network": [], "errors": []}
    assert page.evaluate("window.__storage") == []


def test_removing_needs_a_reason_and_approval_waits_for_every_decision(browser):
    page, _ = open_demo(browser)
    page.locator("#review-button").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector('[role="tab"]')
    page.keyboard.press("ArrowRight")
    tab_to(page, lambda f: f["text"] == "Remove")
    page.keyboard.press("Enter")
    assert page.locator("text=Pick a reason:").count() == 1
    missing = page.locator("#missing").inner_text()
    assert "decide 5 documents" in missing and "check 2 flags" in missing
    tab_to(page, lambda f: f["text"] == "Duplicate")
    page.keyboard.press("Enter")
    assert "decide 4 documents" in page.locator("#missing").inner_text()
    assert page.locator("button:has-text('Approve list')").is_disabled()


def test_sections_stay_open_and_focus_stays_put_while_deciding(browser):
    page, seen = open_demo(browser)
    tab_to(page, lambda f: f["text"] == "Demo Client 2")
    page.keyboard.press("Enter")
    page.wait_for_selector("text=Demo Client 2: draft document list")
    page.locator("#review-button").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector('[role="tab"]')
    page.keyboard.press("ArrowRight")
    tab_to(page, lambda f: f["tag"] == "SUMMARY" and f["text"].startswith("Add a document"))
    page.keyboard.press("Enter")
    assert page.locator("details:has(summary:text-matches('^Add a document')) ").get_attribute("open") is not None
    tab_to(page, lambda f: f["text"].startswith("Add R020"))
    page.keyboard.press("Enter")
    assert focused(page)["text"].startswith("Added R020") and focused(page)["pressed"] == "true"
    assert page.locator("details:has(summary:text-matches('^Add a document'))").get_attribute("open") is not None
    tab_to(page, lambda f: f["text"] == "-")
    page.keyboard.press("Enter")  # at the minimum: nothing changes and focus stays on the button
    assert focused(page)["text"] == "-"
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    assert focused(page)["text"] == "+" and page.locator(".stepper output").inner_text() == "2"
    # Facts: open Not asked and ask about one fact; the section stays open.
    tab_to(page, lambda f: f["role"] == "tab" and f["text"].startswith("Documents"))
    page.keyboard.press("ArrowRight")
    tab_to(page, lambda f: f["tag"] == "SUMMARY" and f["text"].startswith("Not asked"))
    page.keyboard.press("Enter")
    count_before = page.locator("button:text-is('Will ask the client')").count()
    tab_to(page, lambda f: f["text"] == "Ask the client again")
    page.keyboard.press("Enter")
    assert page.locator("details:has(summary:text-matches('^Not asked'))").get_attribute("open") is not None
    assert page.locator("button:text-is('Will ask the client')").count() == count_before + 1
    assert focused(page)["text"] == "Will ask the client"
    assert seen == {"network": [], "errors": []}


def test_pressing_a_chosen_remove_again_keeps_its_reason(browser):
    page, _ = open_demo(browser)
    page.locator("#review-button").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector('[role="tab"]')
    page.keyboard.press("ArrowRight")
    tab_to(page, lambda f: f["text"] == "Remove")
    page.keyboard.press("Enter")
    tab_to(page, lambda f: f["text"] == "Not needed")
    page.keyboard.press("Enter")
    page.locator("button.remove[aria-pressed='true']").first.focus()
    page.keyboard.press("Enter")
    assert page.locator("button[aria-pressed='true']:text-is('Not needed')").count() == 1
    assert "decide 4 documents" in page.locator("#missing").inner_text()


def test_the_bar_never_covers_the_focused_control(browser):
    page, _ = open_demo(browser, width=375)
    page.set_viewport_size({"width": 375, "height": 480})
    page.locator("#review-button").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector('[role="tab"]')
    page.keyboard.press("ArrowRight")
    covered = 0
    for _ in range(120):
        page.keyboard.press("Tab")
        covered += page.evaluate(
            "() => { const bar = document.querySelector('.bar'); const e = document.activeElement;"
            " if (!bar || bar.contains(e) || !e.matches('button, a, summary')) return 0;"
            " return e.getBoundingClientRect().bottom > bar.getBoundingClientRect().top + 1 ? 1 : 0; }"
        )
    assert covered == 0


def test_the_closing_message_counts_as_a_note_to_read(browser):
    page, _ = open_demo(browser)
    assert page.locator("dt:text-is('Notes to read') + dd").inner_text() == "1"
    page.locator("#review-button").focus()
    page.keyboard.press("Enter")
    assert page.locator("#tab-notes").inner_text() == "Notes (1)"


KEEP_UNDER_BAR = (
    "(top) => { for (const b of document.querySelectorAll('button')) { const r = b.getBoundingClientRect();"
    " if (r.top < top - 8 && r.bottom > top + 4 && b.textContent.trim() === 'Keep') return b.dataset.k; } return null; }"
)


def test_a_click_on_a_control_under_the_bar_lands_without_scrolling(browser):
    page, _ = open_demo(browser, width=375)
    page.set_viewport_size({"width": 375, "height": 480})
    page.locator("#review-button").click()
    page.locator("#tab-documents").click()
    target = None
    for _ in range(30):
        target = page.evaluate(KEEP_UNDER_BAR, page.evaluate("document.querySelector('.bar').getBoundingClientRect().top"))
        if target:
            break
        page.mouse.wheel(0, 37)
        page.wait_for_timeout(50)
    assert target, "no Keep button sat partly under the bar to click"
    box = page.locator(f"[data-k='{target}']").bounding_box()
    y_before = page.evaluate("window.scrollY")
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + 4)
    assert page.locator(f"[data-k='{target}']").get_attribute("aria-pressed") == "true"
    assert page.evaluate("window.scrollY") == y_before


def test_the_demo_fits_a_phone_and_says_nothing_is_saved(browser):
    page, seen = open_demo(browser, width=375)
    assert page.locator("text=Demo: nothing is saved.").count() == 1
    disclaimer = page.get_by_text("Not tax advice. All households are synthetic. Not affiliated with any tax software vendor.", exact=True)
    assert disclaimer.count() == 1 and disclaimer.is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.locator("#review-button").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector('[role="tab"]')
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert seen["network"] == [] and seen["errors"] == []
