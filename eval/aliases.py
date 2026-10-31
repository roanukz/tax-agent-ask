"""Map a free-text document request to rules, so the scorer can count it.

Custom requests from the agent and the items of a model-written list (B3)
name documents in words. Scoring needs rule IDs, so this module matches
the words against each rule's aliases and document text in
rules/document_rules.yaml and against rules/ignore_aliases.yaml. It reads
nothing from disk: callers pass the loaded rules and ignore list.

Matching works on words and their pieces:

- Typographic dashes become "-" and apostrophes are dropped, so "driver's
  license" and "drivers license" read the same. Case does not matter,
  except for the one rule about a capital S below.
- Words split on whitespace and punctuation, but a hyphen inside a word
  stays, so "1098" never matches "Form 1098-E".
- A word compares as its pieces: it splits at each hyphen and wherever
  digits meet letters. An alias's pieces may spread over several request
  words or share one, so "W2", "W 2", and "W-2" match one another, as do
  "1099 INT" and "1099-INT", "SSA 1099" and "SSA-1099", and "prior year"
  and "prior-year". An alias still matches only whole words, so "W-2"
  never matches "W-2G".
- A plural "s" is dropped when a letter or digit other than "s" comes
  before it, so "W-2s" and "brokerage statements" match "W-2" and
  "brokerage statement". "1099-S" keeps its "s", so it never reads as
  "1099". A capital "S" right after a digit can also be a form letter, so
  "1099S" matches "1099-S" while "1099s" reads as plural 1099s and matches
  nothing.
- Commas, semicolons, "&", "/", "+", and line breaks are separators. An
  alias never matches across a separator in a request unless the alias
  has one in the same place, and a separator inside an alias matches one
  separator or none. So a rule's document text, such as "Provider name,
  address, taxpayer ID, and amount paid", matches itself with or without
  the last comma.
- An alias matches when all its pieces appear contiguously in the request.

Every rule's document text counts as one more alias for that rule, so a
request copied from the server's request list maps back to its rule.

A request can name several documents ("W-2 and 1099-INT", "Form
1099-INT/1099-DIV"). Each alias hit names a document. Overlapping hits name
the same one when one hit's words hold the other's ("1099-G" inside "1099-G
for state refund") or they share a rule ("Form 1099-G" and "1099-G for
state refund"); otherwise they are two documents that share a word ("child
care receipts" and "receipts and written acknowledgments"). map_request
returns one Match per group of hits, and one for each stretch of words
outside every hit described below, in the order they appear:

- trap: a trap rule's alias is in the group; rule_ids are those trap rules.
- rule: document rules matched; rule_ids are every candidate, longest
  matching alias first, then rule order, where an alias's length is its
  number of pieces. The first tied of them share the longest length, and
  only those are the Match's best candidates. Bare "1099-G" gives R010 and
  R011 tied, and "closing statement" gives R015 and R030 tied; the scorer
  decides which one a list credits. "1099-G for state refund" gives R011
  ahead of R010, with R011 its only best candidate.
- ignore: only ignore aliases matched.
- unmatched: a part of the request with no hit.
- context: a segment with no hit, in a part that has one.

Within one group a trap wins over a rule, since any mention of an ended
credit is a trap request, and a rule wins over an ignore alias.

Separators decide what the words outside every hit are. Semicolons and
line breaks cut a request into parts, and the other separators cut each
part into segments. A part with no hit names a document no rule knows: it
is one unmatched Match holding the part's words, however many segments it
has, so "Mileage log, and receipts for the rental repairs" counts once and
"divorce decree; car title" counts twice. In a part with a hit, each
segment with no hit is a context Match ("from each employer" in "Form
W-2, from each employer"), which the scorer records for audit and never
counts. Words in the same segment as a hit ("from my employer") are
context too and are not recorded.

Hits that do not overlap can still name the same document: a form number
and its title ("Form W-2 (Wage and Tax Statement)"), or two aliases of one
trap ("Form 8936, Clean Vehicle Credit"). merge() joins those Matches, so
the scorer credits a rule or records a trap at most once per request.

Usage:

    index = AliasIndex(load_rules(), load_ignore_aliases())
    merge(index.map_request("W-2 and 1099-INT"))
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
CONTEXT = "context"

_APOSTROPHES = "'`" + "".join(chr(c) for c in (0x2018, 0x2019, 0x201B, 0x02BC, 0x2032))
_DASHES = "".join(chr(c) for c in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFE63, 0xFF0D))
_TRANSLATION = str.maketrans({**dict.fromkeys(_APOSTROPHES), **dict.fromkeys(_DASHES, "-")})

# A word keeps the hyphens inside it; a separator ends a document.
_TOKEN = re.compile(r"(?P<word>[^\W_]+(?:-[^\W_]+)*)|[,;&/+\n]")
# A word's pieces are its runs of digits and its runs of letters.
_PIECE = re.compile(r"\d+|[^\W\d_]+")
# The separators that cut a request into parts.
BREAKS = frozenset(";\n")


@dataclass(frozen=True)
class Match:
    """One document, unmatched part, or context segment of a request: its words, kind, and candidate rules.

    rule_ids[:tied] are the candidates tied on the longest matching alias.
    """

    text: str
    kind: str
    rule_ids: tuple[str, ...] = ()
    tied: int = 1

    @property
    def best(self) -> tuple[str, ...]:
        """The candidates tied on the longest matching alias, the only ones the scorer credits."""
        return self.rule_ids[:self.tied]


@dataclass(frozen=True)
class Alias:
    text: str
    keys: tuple[str | None, ...]
    kind: str
    rule_id: str | None

    @property
    def length(self) -> int:
        """The alias's number of pieces, which ranks the candidates of one document."""
        return sum(k is not None for k in self.keys)


def normalize(text: str) -> str:
    """Casefolded, with typographic dashes as "-" and apostrophes dropped."""
    return text.translate(_TRANSLATION).casefold()


def tokenize(text: str) -> list[str | None]:
    """Words in order, in their original case, with None for each separator."""
    return [m.group("word") for m in _TOKEN.finditer(text.translate(_TRANSLATION))]


def breaks(text: str) -> set[int]:
    """The indexes in tokenize(text) of its semicolons and line breaks."""
    return {i for i, m in enumerate(_TOKEN.finditer(text.translate(_TRANSLATION))) if m.group() in BREAKS}


def phrase(tokens: Iterable[str | None]) -> str:
    """The words of tokens, casefolded and joined by spaces."""
    return " ".join(token.casefold() for token in tokens if token is not None)


def alias_keys(text: str) -> tuple[str | None, ...]:
    """An alias's pieces in order, with None for each separator inside it."""
    keys: list[str | None] = []
    for token in tokenize(text):
        keys.extend((None,) if token is None else key(token))
    while keys and keys[0] is None:
        keys.pop(0)
    while keys and keys[-1] is None:
        keys.pop()
    return tuple(keys)


def key(token: str) -> tuple[str, ...]:
    """The pieces a word compares as: casefolded, plural s dropped, then split at hyphens and between digits and letters."""
    token = token.casefold()
    if len(token) > 2 and token.endswith("s") and token[-2].isalnum() and token[-2] != "s":
        token = token[:-1]
    return tuple(_PIECE.findall(token))


def forms(token: str) -> frozenset[tuple[str, ...]]:
    """The pieces a request word can match: key(token), and its pieces with the S kept when a capital S follows a digit."""
    if token.endswith("S") and token[-2:-1].isdigit():
        return frozenset({key(token), tuple(_PIECE.findall(token.casefold()))})
    return frozenset({key(token)})


def positions(needle: Sequence[str | None], tokens: Sequence[Collection[tuple[str, ...]] | None]) -> list[tuple[int, int]]:
    """Every (start, end) where needle's pieces occur contiguously among the tokens, by start then end.

    tokens holds each request word's forms, and None for each separator. A
    word matches when one of its forms is the needle's next pieces, so it
    never matches in part. A None in the needle matches one separator or
    none, and a separator matches only a None in the needle.
    """
    found = []
    for start in range(len(tokens)):
        ends = set()
        states = [(start, 0)]
        while states:
            at, done = states.pop()
            if done == len(needle):
                ends.add(at)
            elif needle[done] is None:
                states.append((at, done + 1))
                if at < len(tokens) and tokens[at] is None:
                    states.append((at + 1, done + 1))
            elif at < len(tokens) and tokens[at] is not None:
                states.extend((at + 1, done + len(form)) for form in tokens[at]
                              if tuple(needle[done:done + len(form)]) == form)
        found.extend((start, end) for end in sorted(ends))
    return found


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


def split(indexes: Iterable[int], cuts: Collection[int]) -> list[list[int]]:
    """indexes cut into runs at each index in cuts, which belongs to neither run; empty runs are left out."""
    runs: list[list[int]] = [[]]
    for i in indexes:
        if i in cuts:
            runs.append([])
        else:
            runs[-1].append(i)
    return [run for run in runs if run]


def merge(matches: Iterable[Match]) -> list[Match]:
    """One request's Matches, with the Matches that name one rule or trap joined, in order.

    A Match joins the first earlier Match of its kind whose best candidates
    share a rule with its own, so "Form W-2 (Wage and Tax Statement)" is one
    W-2, "Form 8936, Clean Vehicle Credit" is one trap request, and "Form
    1099-G (unemployment compensation statement)" is the unemployment
    1099-G alone. The joined Match keeps the shared best candidates, in the
    earlier Match's order and all tied, and lists both texts. Only rule and
    trap Matches have best candidates, so no other kind joins. Two documents
    of one rule in one request ("W-2 from Acme and W-2 from Beta") also join
    and count once; an item's count says how many there are.
    """
    merged: list[Match] = []
    for match in matches:
        for i, earlier in enumerate(merged):
            shared = tuple(r for r in earlier.best if r in match.best) if earlier.kind == match.kind else ()
            if shared:
                merged[i] = Match(f"{earlier.text}, {match.text}", earlier.kind, shared, len(shared))
                break
        else:
            merged.append(match)
    return merged


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
        """The documents a request names, its unmatched parts, and its context segments, in order."""
        tokens = tokenize(text)
        token_forms = [None if token is None else forms(token) for token in tokens]
        hits = [(start, end, alias) for alias in self.aliases for start, end in positions(alias.keys, token_forms)]
        # Each Match by the index of its first word, which no other Match shares.
        found = {start: self.classify(phrase(tokens[start:end]), aliases) for start, end, aliases in clusters(hits)}
        covered = {i for start, end, _ in hits for i in range(start, end)}
        separators = {i for i, token in enumerate(tokens) if token is None}
        for part in split(range(len(tokens)), breaks(text)):
            segments = split(part, separators)
            if not covered.isdisjoint(part):
                found.update((s[0], Match(phrase(tokens[i] for i in s), CONTEXT)) for s in segments if covered.isdisjoint(s))
            elif segments:
                found[part[0]] = Match(phrase(tokens[i] for i in part), UNMATCHED)
        return [found[start] for start in sorted(found)]

    def classify(self, text: str, aliases: Sequence[Alias]) -> Match:
        """One group of overlapping hits as a single Match."""
        for kind in (TRAP, RULE):
            lengths: dict[str, list[int]] = {}
            for alias in aliases:
                if alias.kind == kind:
                    lengths.setdefault(alias.rule_id, []).append(alias.length)
            if lengths:
                ranked = sorted(lengths, key=lambda rule_id: (-max(lengths[rule_id]), self.order[rule_id]))
                tied = sum(max(lengths[rule_id]) == max(lengths[ranked[0]]) for rule_id in ranked)
                return Match(text, kind, tuple(ranked), tied)
        return Match(text, IGNORE)
