"""Map a free-text document request to rules, so the scorer can count it.

Custom requests from the agent and the items of a model-written list (B3)
name documents in words. Scoring needs rule IDs, so this module matches
the words against each rule's aliases and document text in
rules/document_rules.yaml and against rules/ignore_aliases.yaml. It reads
nothing from disk: callers pass the loaded rules and ignore list.

Matching works on words:

- Typographic dashes become "-" and apostrophes are dropped, so "driver's
  license" and "drivers license" read the same. Case does not matter,
  except for the one rule about a capital S below.
- Words split on whitespace and punctuation, but a hyphen inside a word
  stays, so "1098" never matches "Form 1098-E".
- Commas, semicolons, "&", "/", "+", and line breaks separate documents: an
  alias never matches across one unless the alias has a separator in the
  same place. So a rule's document text, such as "Provider name, address,
  taxpayer ID, and amount paid", matches itself.
- Words compare without their hyphens, so "W2" matches "W-2" and "1099INT"
  matches "1099-INT".
- A plural "s" is dropped when a letter or digit other than "s" comes
  before it, so "W-2s" and "brokerage statements" match "W-2" and
  "brokerage statement". "1099-S" keeps its "s", so it never reads as
  "1099". A capital "S" right after a digit can also be a form letter, so
  "1099S" matches "1099-S" while "1099s" reads as plural 1099s and matches
  nothing.
- An alias matches when all its words appear contiguously in the request.

Every rule's document text counts as one more alias for that rule, so a
request copied from the server's request list maps back to its rule.

A request can name several documents ("W-2 and 1099-INT", "Form
1099-INT/1099-DIV"). Each alias hit names a document. Overlapping hits name
the same one when one hit's words hold the other's ("1099-G" inside "1099-G
for state refund") or they share a rule ("Form 1099-G" and "1099-G for
state refund"); otherwise they are two documents that share a word ("child
care receipts" and "receipts and written acknowledgments"). map_request
returns one Match per document, in the order they appear:

- trap: a trap rule's alias is in the group; rule_ids are those trap rules.
- rule: document rules matched; rule_ids are every candidate, longest
  matching alias first, then rule order. Bare "1099-G" gives R010 and
  R011, and "closing statement" gives R015 and R030; the scorer decides
  which one a candidate list credits.
- ignore: only ignore aliases matched.

Within one group a trap wins over a rule, since any mention of an ended
credit is a trap request, and a rule wins over an ignore alias. Words
outside every hit are context ("from my employer") and are not scored. A
request with no hit at all is one unmatched Match holding the whole text,
so one request counts as unmatched at most once.

Usage:

    index = AliasIndex(load_rules(), load_ignore_aliases())
    index.map_request("W-2 and 1099-INT")
    # [Match("w-2", "rule", ("R001",)), Match("1099-int", "rule", ("R004",))]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Collection, Iterable, Sequence

from rules.engine import Rule

RULE = "rule"
TRAP = "trap"
IGNORE = "ignore"
UNMATCHED = "unmatched"

_APOSTROPHES = "'`" + "".join(chr(c) for c in (0x2018, 0x2019, 0x201B, 0x02BC, 0x2032))
_DASHES = "".join(chr(c) for c in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFE63, 0xFF0D))
_TRANSLATION = str.maketrans({**dict.fromkeys(_APOSTROPHES), **dict.fromkeys(_DASHES, "-")})

# A word keeps the hyphens inside it; a separator ends a document.
_TOKEN = re.compile(r"(?P<word>[^\W_]+(?:-[^\W_]+)*)|[,;&/+\n]")
# A separator in a request matches only a separator in an alias.
SEPARATOR_FORMS: frozenset[str | None] = frozenset({None})


@dataclass(frozen=True)
class Match:
    """One document a request names: the matched words, their kind, and candidate rules."""

    text: str
    kind: str
    rule_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Alias:
    text: str
    keys: tuple[str | None, ...]
    kind: str
    rule_id: str | None


def normalize(text: str) -> str:
    """Casefolded, with typographic dashes as "-" and apostrophes dropped."""
    return text.translate(_TRANSLATION).casefold()


def tokenize(text: str) -> list[str | None]:
    """Words in order, in their original case, with None for each separator."""
    return [m.group("word") for m in _TOKEN.finditer(text.translate(_TRANSLATION))]


def phrase(tokens: Sequence[str | None]) -> str:
    """The words of tokens, casefolded and joined by spaces."""
    return " ".join(token.casefold() for token in tokens if token is not None)


def alias_keys(text: str) -> tuple[str | None, ...]:
    """An alias's keys, with None for each separator inside it."""
    keys = [None if token is None else key(token) for token in tokenize(text)]
    while keys and keys[0] is None:
        keys.pop(0)
    while keys and keys[-1] is None:
        keys.pop()
    return tuple(keys)


def key(token: str) -> str:
    """The form a word compares in: casefolded, plural s dropped, then hyphens dropped."""
    token = token.casefold()
    if len(token) > 2 and token.endswith("s") and token[-2].isalnum() and token[-2] != "s":
        token = token[:-1]
    return token.replace("-", "")


def forms(token: str) -> frozenset[str]:
    """The keys a request word can match: key(token), and the S kept when a capital S follows a digit."""
    if token.endswith("S") and token[-2:-1].isdigit():
        return frozenset({key(token), token.casefold().replace("-", "")})
    return frozenset({key(token)})


def positions(needle: Sequence[str | None], tokens: Sequence[Collection[str | None]]) -> list[int]:
    """Every index where needle's keys occur contiguously among the tokens' forms."""
    n = len(needle)
    return [i for i in range(len(tokens) - n + 1) if all(k in tokens[i + j] for j, k in enumerate(needle))]


def clusters(hits: Iterable[tuple[int, int, Alias]]) -> list[tuple[int, int, list[Alias]]]:
    """Hits as (start, end, alias), grouped into the documents they name, in order of start.

    A hit joins an earlier group it overlaps when the group's span holds it,
    it starts with the group and holds the group's span, or it shares a rule
    with the group.
    """
    groups: list[tuple[int, int, list[Alias]]] = []
    for start, end, alias in sorted(hits, key=lambda hit: hit[:2]):
        for i, (first, last, aliases) in enumerate(groups):
            if start >= last:
                continue
            if end <= last or start == first or any(a.rule_id == alias.rule_id for a in aliases):
                groups[i] = (first, max(last, end), [*aliases, alias])
                break
        else:
            groups.append((start, end, [alias]))
    return groups


class AliasIndex:
    """Every alias and document text of the given rules, plus the ignore list, ready to match."""

    def __init__(self, rules: Iterable[Rule], ignore: Iterable[str]) -> None:
        self.order: dict[str, int] = {}
        self.aliases: list[Alias] = []
        for rule in rules:
            self.order[rule.id] = len(self.order)
            kind = TRAP if rule.trap else RULE
            for text in (*rule.aliases, *([rule.document] if rule.document and not rule.trap else [])):
                self.add(text, kind, rule.id)
        for text in ignore:
            self.add(text, IGNORE, None)

    def add(self, text: str, kind: str, rule_id: str | None) -> None:
        keys = alias_keys(text)
        if keys and not any(a.keys == keys and a.kind == kind and a.rule_id == rule_id for a in self.aliases):
            self.aliases.append(Alias(text, keys, kind, rule_id))

    def map_request(self, text: str) -> list[Match]:
        """The documents a request names, in order; one unmatched Match when it names none."""
        tokens = tokenize(text)
        if not any(tokens):
            return []
        token_forms = [SEPARATOR_FORMS if token is None else forms(token) for token in tokens]
        hits = [(start, start + len(alias.keys), alias)
                for alias in self.aliases for start in positions(alias.keys, token_forms)]
        if not hits:
            return [Match(phrase(tokens), UNMATCHED)]
        return [self.classify(phrase(tokens[start:end]), aliases) for start, end, aliases in clusters(hits)]

    def classify(self, text: str, aliases: Sequence[Alias]) -> Match:
        """One group of overlapping hits as a single Match."""
        for kind in (TRAP, RULE):
            lengths: dict[str, list[int]] = {}
            for alias in aliases:
                if alias.kind == kind:
                    lengths.setdefault(alias.rule_id, []).append(len(alias.keys))
            if lengths:
                ranked = sorted(lengths, key=lambda rule_id: (-max(lengths[rule_id]), self.order[rule_id]))
                return Match(text, kind, tuple(ranked))
        return Match(text, IGNORE)
