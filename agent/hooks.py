"""Guarantee 2: a fact is accepted only with valid evidence.

Evidence is either a quote that appears verbatim in one of this run's client
answers, after whitespace, case, and typographic-quote normalization, or
prior_year:<field> naming a field that exists in last year's summary. The
hook checks that the quote exists, not that it supports the fact; wrong
values are caught by scoring. It runs in the harness, so it holds in eval
runs only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

RECORD_FACT_TOOL = "mcp__tax__record_fact"
PRIOR_YEAR_PREFIX = "prior_year:"

_TYPOGRAPHIC = str.maketrans({
    "‘": "'", "’": "'", "‛": "'",
    "“": '"', "”": '"', "‟": '"',
    " ": " ",
})
_WRAPPING_QUOTES = "\"'"


def normalize(text: str) -> str:
    return " ".join(text.translate(_TYPOGRAPHIC).split()).casefold()


def allowed_prior_fields(prior_year_summary: dict[str, Any]) -> set[str]:
    """Top-level fields, plus documents_on_file.<rule_id> for each rule on file."""
    fields = set(prior_year_summary)
    for doc in prior_year_summary.get("documents_on_file") or []:
        if isinstance(doc, dict) and doc.get("rule_id"):
            fields.add(f"documents_on_file.{doc['rule_id']}")
    return fields


@dataclass
class EvidenceBook:
    """What one run's client has said so far, and what last year's summary holds."""

    prior_year_summary: dict[str, Any]
    answers: list[str] = field(default_factory=list)
    denials: list[dict[str, Any]] = field(default_factory=list)

    def add_answer(self, text: str) -> None:
        self.answers.append(text)

    def check(self, evidence: Any) -> tuple[bool, str]:
        if not isinstance(evidence, str) or not evidence.strip():
            return False, "evidence is empty"
        raw = evidence.strip()
        if raw.startswith(PRIOR_YEAR_PREFIX):
            name = raw[len(PRIOR_YEAR_PREFIX):].strip()
            if name in allowed_prior_fields(self.prior_year_summary):
                return True, ""
            return False, f"last year's summary has no field {name!r}"
        quote = normalize(raw.translate(_TYPOGRAPHIC).strip(_WRAPPING_QUOTES))
        if not quote:
            return False, "evidence is empty"
        if any(quote in normalize(a) for a in self.answers):
            return True, ""
        return False, "evidence must be the client's exact words from an answer, or prior_year:<field>"


HookCallback = Callable[[dict[str, Any], str | None, Any], Awaitable[dict[str, Any]]]


def record_fact_hook(book: EvidenceBook) -> HookCallback:
    """A PreToolUse callback that denies record_fact without valid evidence."""

    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        if input_data.get("tool_name") != RECORD_FACT_TOOL:
            return {}
        tool_input = input_data.get("tool_input") or {}
        ok, reason = book.check(tool_input.get("evidence"))
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
