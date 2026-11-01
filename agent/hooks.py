"""Guarantee 2: a fact is accepted only with valid evidence.

Evidence is either a quote from one of this run's client answers, or
prior_year:<field> naming the field of last year's summary that holds the
fact being recorded.

A quote is whole words of one answer, in a row and in order: at least
MIN_QUOTE_WORDS of them, or one word that is a whole sentence of the answer,
such as "Yes." or "Two.". Case, spacing, typographic quotes, and the
punctuation between words are ignored. A word keeps an apostrophe or hyphen
between its letters or digits (it's, part-time, W-2), and a number keeps
the commas, periods, and slashes between its digits (1,200.50), so "time
job" is not a quote of "a part-time job". A sentence ends at . ! or ?, and
any closing quotes or brackets after it, before a space, or at a line
break, so "No" is a whole sentence of "No. But my wife did." and not of
"No, but my wife did.". The answers are the text ask_client returned, a
probe the harness appended included: the agent sees it as the client's
words, and the check is for presence, not support.

A field holds a fact when rules/facts.yaml names it as the key's
prior_year_field, or when it is documents_on_file.<rule_id> for a rule on
file, not a trap, whose condition or count_from reads the key, unless the
key has a prior_year_field of its own, which gives last year's value
directly (on file, R011 reflects itemizing the year before last). tax_year
and the bare documents_on_file hold nothing. A denial says what would pass,
and names a field only with "if last year's value still holds", since a
fact carried over is a lead to confirm.

The hook checks that the words or the field are there, not that they
support the value; wrong values are caught by scoring. It runs in the
harness, so it holds in eval runs only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from rules import engine
from rules.engine import FactSpec, Rule

RECORD_FACT_TOOL = "mcp__tax__record_fact"
PRIOR_YEAR_PREFIX = "prior_year:"
DOCUMENTS_ON_FILE = "documents_on_file"
MIN_QUOTE_WORDS = 2
# A reason echoes the agent's own text cut to ECHO_BYTES of UTF-8, so every reason stays within REASON_BYTES
# and a denial, with the CLI's error framing around it, within agent.harness.RESULT_TOKENS.
ECHO_BYTES = 40
REASON_BYTES = 150

_TYPOGRAPHIC = str.maketrans({
    "‘": "'", "’": "'", "‛": "'",
    "“": '"', "”": '"', "‟": '"',
    " ": " ",
})
# A word is letters or digits; an apostrophe or hyphen between two of them stays inside it, and so does a
# period, comma, or slash between two digits. Anything else separates words and is dropped.
_WORD = re.compile(r"[^\W_]+(?:(?:['\-]|(?<=\d)[.,/](?=\d))[^\W_]+)*")
# A sentence ends at . ! or ?, and any closing quotes or brackets after it, before a space; or at a line break.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])[\"')\]]*\s+|\n+")

# Denial reasons. NOT_FOUND is word for word what the scripted fixtures store.
EMPTY_EVIDENCE = "evidence is empty"
NOT_FOUND = "evidence must be the client's exact words from an answer, or prior_year:<field>"
ONE_WORD = ("one word counts only when it is a whole sentence of an answer, such as \"Yes.\"; "
            "give at least two of the client's exact words in a row")
NOT_A_KEY = "{key} is not a fact key; list_fact_keys gives every key"
NOTHING_HOLDS = "nothing in last year's summary holds {key}; ask the client and quote their answer"
NO_FIELD = "no such field; "
WRONG_FIELD = "wrong field; "
USE_FIELD = "if last year's value still holds, use prior_year:{field}; otherwise ask the client and quote their answer"


def normalize(text: str) -> str:
    return " ".join(text.translate(_TYPOGRAPHIC).split()).casefold()


def words(text: str) -> tuple[str, ...]:
    """The words of text, normalized, in order."""
    return tuple(_WORD.findall(normalize(text)))


def sentences(text: str) -> list[tuple[str, ...]]:
    """The words of each sentence of text, split after its typographic quotes are made straight."""
    return [w for w in map(words, _SENTENCE_BREAK.split(text.translate(_TYPOGRAPHIC))) if w]


def _has_run(run: tuple[str, ...], seq: tuple[str, ...]) -> bool:
    return any(seq[i:i + len(run)] == run for i in range(len(seq) - len(run) + 1))


def _echo(text: str) -> str:
    """text cut to at most ECHO_BYTES bytes of UTF-8, on a character boundary, then "..."."""
    raw = text.encode("utf-8", "backslashreplace")
    if len(raw) <= ECHO_BYTES:
        return raw.decode()
    return raw[:ECHO_BYTES].decode("utf-8", "ignore") + "..."


def allowed_prior_fields(prior_year_summary: dict[str, Any]) -> set[str]:
    """Top-level fields, plus documents_on_file.<rule_id> for each rule on file."""
    fields = set(prior_year_summary)
    for doc in prior_year_summary.get("documents_on_file") or []:
        if isinstance(doc, dict) and doc.get("rule_id"):
            fields.add(f"documents_on_file.{doc['rule_id']}")
    return fields


def prior_year_support(
    prior_year_summary: Mapping[str, Any], specs: Mapping[str, FactSpec], rules: Iterable[Rule]
) -> dict[str, frozenset[str]]:
    """The fact keys each field of last year's summary holds. A field that holds none is left out.

    A key's prior_year_field holds it, and documents_on_file.<rule_id> holds
    the keys its rule reads, less any key with a prior_year_field of its own.
    A trap or an unknown rule ID holds nothing.
    """
    own = {spec.key for spec in specs.values() if spec.prior_year_field}
    held: dict[str, set[str]] = {}
    for spec in specs.values():
        if spec.prior_year_field and spec.prior_year_field in prior_year_summary:
            held.setdefault(spec.prior_year_field, set()).add(spec.key)
    by_id = {rule.id: rule for rule in rules if not rule.trap}
    for doc in prior_year_summary.get(DOCUMENTS_ON_FILE) or []:
        rule_id = doc.get("rule_id") if isinstance(doc, dict) else None
        rule = by_id.get(rule_id) if isinstance(rule_id, str) else None
        if rule is None:
            continue
        keys = engine.rule_facts(rule.when) | ({rule.count_from} if rule.count_from else set())
        if keys - own:
            held.setdefault(f"{DOCUMENTS_ON_FILE}.{rule.id}", set()).update(keys - own)
    return {name: frozenset(keys) for name, keys in held.items()}


@cache
def repo_facts_and_rules() -> tuple[Mapping[str, FactSpec], tuple[Rule, ...]]:
    """The repo's fact keys and document rules, read once, for a book that is not given its own."""
    return MappingProxyType(engine.load_facts()), tuple(engine.load_rules())


@dataclass
class EvidenceBook:
    """What one run's client has said so far, and which facts last year's summary holds."""

    prior_year_summary: dict[str, Any]
    answers: list[str] = field(default_factory=list)
    denials: list[dict[str, Any]] = field(default_factory=list)
    specs: Mapping[str, FactSpec] | None = field(default=None, repr=False)
    rules: Sequence[Rule] | None = field(default=None, repr=False)
    support: dict[str, frozenset[str]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        repo_specs, repo_rules = repo_facts_and_rules()
        self.specs = repo_specs if self.specs is None else self.specs
        self.rules = repo_rules if self.rules is None else self.rules
        self.support = prior_year_support(self.prior_year_summary, self.specs, self.rules)

    def add_answer(self, text: str) -> None:
        self.answers.append(text)

    def check(self, evidence: Any, *, key: Any) -> tuple[bool, str]:
        """(ok, reason) for the evidence of a record_fact on key; only a prior_year field depends on the key."""
        if not isinstance(evidence, str) or not evidence.strip():
            return False, EMPTY_EVIDENCE
        raw = evidence.strip()
        if raw.startswith(PRIOR_YEAR_PREFIX):
            return self._check_field(raw[len(PRIOR_YEAR_PREFIX):].strip(), key)
        return self._check_quote(raw)

    def _check_quote(self, evidence: str) -> tuple[bool, str]:
        quote = words(evidence)
        if not quote:
            return False, EMPTY_EVIDENCE
        if len(quote) >= MIN_QUOTE_WORDS:
            if any(_has_run(quote, words(a)) for a in self.answers):
                return True, ""
            return False, NOT_FOUND
        if any(quote in sentences(a) for a in self.answers):
            return True, ""
        if any(quote[0] in words(a) for a in self.answers):
            return False, ONE_WORD
        return False, NOT_FOUND

    def _check_field(self, name: str, key: Any) -> tuple[bool, str]:
        if not isinstance(key, str) or key not in self.specs:
            return False, NOT_A_KEY.format(key=_echo(repr(key)))
        if key in self.support.get(name, ()):
            return True, ""
        holders = sorted(f for f, keys in self.support.items() if key in keys)
        if not holders:
            return False, NOTHING_HOLDS.format(key=_echo(key))
        problem = WRONG_FIELD if name in allowed_prior_fields(self.prior_year_summary) else NO_FIELD
        return False, problem + USE_FIELD.format(field=holders[0])


HookCallback = Callable[[dict[str, Any], str | None, Any], Awaitable[dict[str, Any]]]


def record_fact_hook(book: EvidenceBook) -> HookCallback:
    """A PreToolUse callback that denies record_fact without valid evidence."""

    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        if input_data.get("tool_name") != RECORD_FACT_TOOL:
            return {}
        tool_input = input_data.get("tool_input") or {}
        ok, reason = book.check(tool_input.get("evidence"), key=tool_input.get("key"))
        if ok:
            return {}
        book.denials.append({
            "tool_use_id": tool_use_id,
            "key": tool_input.get("key"),
            "value": tool_input.get("value"),
            "evidence": tool_input.get("evidence"),
            "reason": reason,
        })
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return hook
