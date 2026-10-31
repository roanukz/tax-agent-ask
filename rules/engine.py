"""Facts in, document list out.

Rule conditions are structured data, never evaluated as code:

    {fact: <key>, eq: <value>}      equal
    {fact: <key>, in: [<values>]}   one of
    {fact: <key>, gte: <value>}     greater than or equal
    {fact: <key>, lte: <value>}     less than or equal
    {all: [<condition>, ...]}       every condition holds
    {any: [<condition>, ...]}       at least one holds

A fact that was never recorded counts as false for bool, 0 for int, and
unknown (None) for enum, date, and follow-up keys. An unknown value never satisfies eq, in,
gte, or lte, so a rule that needs it does not apply.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

_INT_TEXT = re.compile(r"-?[0-9]+")
_DATE_TEXT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

RULES_DIR = Path(__file__).resolve().parent
FACTS_PATH = RULES_DIR / "facts.yaml"
DOCUMENT_RULES_PATH = RULES_DIR / "document_rules.yaml"
IGNORE_ALIASES_PATH = RULES_DIR / "ignore_aliases.yaml"

FACT_TYPES = ("bool", "int", "enum", "date")
COMPARISONS = ("eq", "in", "gte", "lte")
STATUSES = ("to_verify", "verified", "excluded")
SOURCE_VERSIONS = ("final_instructions", "draft_instructions", "guidance")

# Which rule statuses count, by split. Test scoring uses verified rules only.
STATUSES_FOR_SPLIT = {
    "train": ("verified", "to_verify"),
    "dev": ("verified", "to_verify"),
    "test": ("verified",),
}


class FactError(ValueError):
    """A value doesn't fit its fact key's type."""


@dataclass(frozen=True)
class FactSpec:
    key: str
    type: str
    topic: str
    plain_question_hint: str
    determinable_from_prior_year: bool
    asked_if: Mapping[str, Any] | None = None
    values: tuple[str, ...] | None = None
    min: int | None = None
    max: int | None = None
    prior_year_field: str | None = None

    @property
    def is_follow_up(self) -> bool:
        return self.asked_if is not None


@dataclass(frozen=True)
class Rule:
    id: str
    when: Mapping[str, Any]
    document: str | None
    count_from: str | None
    aliases: tuple[str, ...]
    client_reason: str | None
    source_url: str
    source_title: str
    source_version: str
    tax_years: tuple[int, ...]
    trap: bool
    status: str
    verified_by: str | None
    verified_on: str | None


def load_facts(path: Path = FACTS_PATH) -> dict[str, FactSpec]:
    with open(path) as f:
        raw = yaml.safe_load(f)
    specs: dict[str, FactSpec] = {}
    for item in raw:
        values = item.get("values")
        specs[item["key"]] = FactSpec(
            key=item["key"],
            type=item["type"],
            topic=item["topic"],
            plain_question_hint=item["plain_question_hint"],
            determinable_from_prior_year=bool(item["determinable_from_prior_year"]),
            asked_if=item.get("asked_if"),
            values=tuple(values) if values is not None else None,
            min=item.get("min"),
            max=item.get("max"),
            prior_year_field=item.get("prior_year_field"),
        )
    return specs


def load_rules(path: Path = DOCUMENT_RULES_PATH) -> list[Rule]:
    with open(path) as f:
        raw = yaml.safe_load(f)
    return [
        Rule(
            id=item["id"],
            when=item["when"],
            document=item.get("document"),
            count_from=item.get("count_from"),
            aliases=tuple(item.get("aliases") or ()),
            client_reason=item.get("client_reason"),
            source_url=item.get("source_url") or "",
            source_title=item.get("source_title") or "",
            source_version=item.get("source_version") or "",
            tax_years=tuple(item.get("tax_years") or ()),
            trap=bool(item.get("trap", False)),
            status=item.get("status") or "to_verify",
            verified_by=item.get("verified_by"),
            verified_on=str(item["verified_on"]) if item.get("verified_on") is not None else None,
        )
        for item in raw
    ]


def load_ignore_aliases(path: Path = IGNORE_ALIASES_PATH) -> list[str]:
    with open(path) as f:
        return list(yaml.safe_load(f)["ignore"])


def coerce(spec: FactSpec, value: Any) -> Any:
    """Checks a value against its fact type and returns the stored form."""
    if spec.type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise FactError(f"{spec.key} needs true or false, got {value!r}")
    if spec.type == "int":
        if isinstance(value, bool):
            raise FactError(f"{spec.key} needs a whole number, got {value!r}")
        if isinstance(value, str) and _INT_TEXT.fullmatch(value.strip()):
            value = int(value.strip())
        if not isinstance(value, int):
            raise FactError(f"{spec.key} needs a whole number, got {value!r}")
        if spec.min is not None and value < spec.min:
            raise FactError(f"{spec.key} must be at least {spec.min}, got {value}")
        if spec.max is not None and value > spec.max:
            raise FactError(f"{spec.key} must be at most {spec.max}, got {value}")
        return value
    if spec.type == "enum":
        if value not in (spec.values or ()):
            raise FactError(f"{spec.key} must be one of {list(spec.values or ())}, got {value!r}")
        return value
    if spec.type == "date":
        if not isinstance(value, str) or not _DATE_TEXT.fullmatch(value.strip()):
            raise FactError(f"{spec.key} needs a date as YYYY-MM-DD, got {value!r}")
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError as e:
            raise FactError(f"{spec.key} needs a date as YYYY-MM-DD, got {value!r}") from e
    raise FactError(f"{spec.key} has unknown type {spec.type!r}")


def default_value(spec: FactSpec) -> Any:
    if spec.is_follow_up:
        return None
    if spec.type == "bool":
        return False
    if spec.type == "int":
        return 0
    return None


def complete_facts(recorded: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> dict[str, Any]:
    """Every fact key, with defaults for the ones never recorded."""
    unknown = set(recorded) - set(specs)
    if unknown:
        raise FactError(f"unknown fact keys: {sorted(unknown)}")
    return {key: recorded[key] if key in recorded else default_value(spec) for key, spec in specs.items()}


def _comparable(spec: FactSpec, value: Any) -> Any:
    if value is None:
        return None
    if spec.type == "date":
        return date.fromisoformat(str(value))
    return value


def condition_holds(cond: Mapping[str, Any], facts: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> bool:
    if "all" in cond:
        return all(condition_holds(c, facts, specs) for c in cond["all"])
    if "any" in cond:
        return any(condition_holds(c, facts, specs) for c in cond["any"])
    key = cond["fact"]
    spec = specs[key]
    actual = _comparable(spec, facts.get(key, default_value(spec)))
    if actual is None:
        return False
    ops = [op for op in COMPARISONS if op in cond]
    if len(ops) != 1:
        raise ValueError(f"condition on {key} needs exactly one of {COMPARISONS}: {dict(cond)}")
    op = ops[0]
    target = cond[op]
    if op == "eq":
        return actual == _comparable(spec, target)
    if op == "in":
        return actual in [_comparable(spec, t) for t in target]
    if op == "gte":
        return actual >= _comparable(spec, target)
    return actual <= _comparable(spec, target)


def rules_with_status(rules: Iterable[Rule], statuses: Iterable[str]) -> list[Rule]:
    allowed = set(statuses)
    return [r for r in rules if r.status in allowed]


def applies(rule: Rule, facts: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> bool:
    return condition_holds(rule.when, facts, specs)


def rule_count(rule: Rule, facts: Mapping[str, Any]) -> int:
    if rule.count_from is None:
        return 1
    return int(facts.get(rule.count_from) or 0)


def required_documents(
    facts: Mapping[str, Any],
    rules: Iterable[Rule],
    specs: Mapping[str, FactSpec],
    statuses: Iterable[str] = ("verified",),
) -> list[dict[str, Any]]:
    """The documents a household needs, as [{rule_id, count}] sorted by rule ID.

    Traps never produce a document. A counted rule whose count is 0 doesn't apply.
    """
    full = complete_facts(facts, specs)
    out = []
    for rule in rules_with_status(rules, statuses):
        if rule.trap or not applies(rule, full, specs):
            continue
        count = rule_count(rule, full)
        if count >= 1:
            out.append({"rule_id": rule.id, "count": count})
    return sorted(out, key=lambda d: d["rule_id"])


def triggered_traps(
    facts: Mapping[str, Any],
    rules: Iterable[Rule],
    specs: Mapping[str, FactSpec],
    statuses: Iterable[str] = ("verified",),
) -> list[str]:
    full = complete_facts(facts, specs)
    return sorted(r.id for r in rules_with_status(rules, statuses) if r.trap and applies(r, full, specs))


def as_multiset(documents: Iterable[Mapping[str, Any]]) -> Counter[str]:
    """[{rule_id, count}] to a Counter keyed by rule ID."""
    bag: Counter[str] = Counter()
    for d in documents:
        bag[d["rule_id"]] += int(d.get("count", 1))
    return bag


def rule_facts(cond: Mapping[str, Any]) -> set[str]:
    """Every fact key a condition reads."""
    if "all" in cond:
        return set().union(*(rule_facts(c) for c in cond["all"]))
    if "any" in cond:
        return set().union(*(rule_facts(c) for c in cond["any"]))
    return {cond["fact"]}
