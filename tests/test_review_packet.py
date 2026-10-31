"""review/rules-to-verify.md matches rules/document_rules.yaml, so Roanuk verifies the aliases and sources the scorer uses."""

import re
from pathlib import Path

import pytest

from rules.engine import load_rules

PACKET = Path(__file__).resolve().parent.parent / "review" / "rules-to-verify.md"
LINES = PACKET.read_text(encoding="utf-8").splitlines()
RULES = load_rules()

ROW = re.compile(r"^\| ([RT]\d{3}) \|")
HEADING = re.compile(r"^## ([RT]\d{3})$")
LINK = re.compile(r"^\[(?P<title>.+?)\]\((?P<url>https://\S+)\)$")
SOURCE = re.compile(r"^- \*\*Source:\*\* \[(?P<title>.+?)\]\((?P<url>https://\S+)\) \((?P<version>\w+)\)$")
ALIASES = "- **Aliases:** "
# The summary table shortens a long title to its first words and this.
SHORTENED = " [...]"


def table_rows():
    """Each summary table row's cells, by rule ID, split the way GFM splits them: on every pipe."""
    return {ROW.match(line).group(1): [c.strip() for c in line.strip("|").split("|")]
            for line in LINES if ROW.match(line)}


def sections():
    """The lines of each rule's section, by rule ID."""
    found, current = {}, None
    for line in LINES:
        m = HEADING.match(line)
        if m:
            current = found.setdefault(m.group(1), [])
        elif line.startswith("## "):
            current = None
        elif current is not None:
            current.append(line)
    return found


ROWS = table_rows()
SECTIONS = sections()


def test_every_rule_has_one_row_and_one_section_in_rule_order():
    ids = [r.id for r in RULES]
    assert list(ROWS) == ids
    assert list(SECTIONS) == ids
    assert sum(bool(ROW.match(line)) for line in LINES) == len(ids)
    assert sum(bool(HEADING.match(line)) for line in LINES) == len(ids)


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r.id)
def test_aliases_line_matches_the_rules_file(rule):
    lines = [line for line in SECTIONS[rule.id] if line.startswith(ALIASES)]
    assert lines == [ALIASES + ", ".join(rule.aliases)]


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r.id)
def test_source_line_matches_the_rules_file(rule):
    lines = [line for line in SECTIONS[rule.id] if line.startswith("- **Source:**")]
    assert len(lines) == 1
    m = SOURCE.match(lines[0])
    assert m, lines[0]
    assert (m["title"], m["url"], m["version"]) == (rule.source_title, rule.source_url, rule.source_version)


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r.id)
def test_table_row_matches_the_rules_file(rule):
    cells = ROWS[rule.id]
    assert len(cells) == 5, f"a pipe in the row splits it into {len(cells)} cells"
    m = LINK.match(cells[2])
    assert m, cells[2]
    title = m["title"]
    if title.endswith(SHORTENED):
        assert rule.source_title.startswith(title.removesuffix(SHORTENED) + " "), title
    else:
        assert title == rule.source_title
    assert (m["url"], cells[3]) == (rule.source_url, rule.source_version)


def test_shortened_titles_are_recognized():
    assert ROWS["R009"][2].startswith("[Topic no. 423, Social Security and equivalent railroad retirement [...]](")
    assert ROWS["R017"][2] == ("[Education credits: AOTC and LLC]"
                               "(https://www.irs.gov/credits-deductions/individuals/education-credits-aotc-llc)")
