"""Schema and source checks for the rules files.

    uv run python -m rules.validate          errors fail; warnings print
    uv run python -m rules.validate --strict  warnings fail too
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from rules.engine import (
    COMPARISONS,
    DOCUMENT_RULES_PATH,
    FACT_TYPES,
    FACTS_PATH,
    IGNORE_ALIASES_PATH,
    SOURCE_VERSIONS,
    STATUSES,
    rule_facts,
)

EXPECTED_FACT_COUNT = 39
EXPECTED_RULE_IDS = [f"R{n:03d}" for n in range(1, 31)] + ["T001", "T002", "T003"]
OFFICIAL_SUFFIXES = (".gov",)
DASHES = re.compile("[" + chr(0x2013) + chr(0x2014) + "]")
RULE_FIELDS = (
    "id", "when", "document", "count_from", "aliases", "client_reason", "source_url",
    "source_title", "source_version", "tax_years", "trap", "status", "verified_by", "verified_on",
)


def _check_condition(cond: Any, facts: dict[str, dict], where: str, errors: list[str]) -> None:
    if not isinstance(cond, dict):
        errors.append(f"{where}: condition must be a mapping")
        return
    if "all" in cond or "any" in cond:
        group = "all" if "all" in cond else "any"
        if set(cond) != {group} or not isinstance(cond[group], list) or not cond[group]:
            errors.append(f"{where}: {group} must be the only key and hold a non-empty list")
            return
        for i, sub in enumerate(cond[group]):
            _check_condition(sub, facts, f"{where}.{group}[{i}]", errors)
        return
    key = cond.get("fact")
    if key not in facts:
        errors.append(f"{where}: unknown fact {key!r}")
        return
    ops = [k for k in cond if k != "fact"]
    if len(ops) != 1 or ops[0] not in COMPARISONS:
        errors.append(f"{where}: needs exactly one of {COMPARISONS}, got {ops}")
        return
    op, target = ops[0], cond[ops[0]]
    ftype = facts[key]["type"]
    values = target if op == "in" else [target]
    if op == "in" and not isinstance(target, list):
        errors.append(f"{where}: in needs a list")
        return
    if op in ("gte", "lte") and ftype in ("bool", "enum"):
        errors.append(f"{where}: {op} doesn't apply to {ftype} fact {key}")
    for v in values:
        ok = {
            "bool": isinstance(v, bool),
            "int": isinstance(v, int) and not isinstance(v, bool),
            "enum": v in (facts[key].get("values") or []),
            "date": isinstance(v, (str, date)) and _is_iso_date(v),
        }[ftype]
        if not ok:
            errors.append(f"{where}: value {v!r} doesn't fit {ftype} fact {key}")


def _is_iso_date(v: Any) -> bool:
    if isinstance(v, date):
        return True
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def _official(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host.endswith(s) for s in OFFICIAL_SUFFIXES)


def validate(
    facts_path: Path = FACTS_PATH,
    rules_path: Path = DOCUMENT_RULES_PATH,
    ignore_path: Path = IGNORE_ALIASES_PATH,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    raw_facts = yaml.safe_load(Path(facts_path).read_text())
    facts: dict[str, dict] = {}
    for i, f in enumerate(raw_facts):
        key = f.get("key")
        where = f"facts[{i}] {key}"
        if not key or key in facts:
            errors.append(f"{where}: missing or duplicate key")
            continue
        facts[key] = f
        if f.get("type") not in FACT_TYPES:
            errors.append(f"{where}: type must be one of {FACT_TYPES}")
        for field in ("topic", "plain_question_hint"):
            if not f.get(field):
                errors.append(f"{where}: missing {field}")
        if not isinstance(f.get("determinable_from_prior_year"), bool):
            errors.append(f"{where}: determinable_from_prior_year must be true or false")
        if f.get("determinable_from_prior_year") and not f.get("prior_year_field"):
            errors.append(f"{where}: determinable from prior year but no prior_year_field")
        if f.get("type") == "enum" and not f.get("values"):
            errors.append(f"{where}: enum needs values")
    if len(facts) != EXPECTED_FACT_COUNT:
        errors.append(f"expected {EXPECTED_FACT_COUNT} fact keys, found {len(facts)}")
    for key, f in facts.items():
        parent = f.get("asked_if")
        if parent is None:
            continue
        pkey = parent.get("fact")
        if pkey not in facts or facts[pkey]["type"] != "bool" or parent.get("eq") is not True:
            errors.append(f"facts {key}: asked_if must be {{fact: <bool key>, eq: true}}")
        elif facts[pkey].get("asked_if") is not None:
            errors.append(f"facts {key}: follow-ups can't hang off another follow-up")

    raw_rules = yaml.safe_load(Path(rules_path).read_text())
    seen: dict[str, dict] = {}
    used_facts: set[str] = set()
    for i, r in enumerate(raw_rules):
        rid = r.get("id")
        where = f"rules[{i}] {rid}"
        if not rid or rid in seen:
            errors.append(f"{where}: missing or duplicate id")
            continue
        seen[rid] = r
        missing = [k for k in RULE_FIELDS if k not in r]
        extra = [k for k in r if k not in RULE_FIELDS]
        if missing:
            errors.append(f"{where}: missing fields {missing}")
        if extra:
            errors.append(f"{where}: unknown fields {extra}")
        _check_condition(r.get("when"), facts, f"{where}.when", errors)
        if isinstance(r.get("when"), dict):
            try:
                used_facts |= rule_facts(r["when"])
            except (KeyError, TypeError):
                pass
        trap = r.get("trap")
        if not isinstance(trap, bool):
            errors.append(f"{where}: trap must be true or false")
        if trap is True:
            if r.get("document") is not None or r.get("client_reason") is not None:
                errors.append(f"{where}: a trap has document and client_reason null")
            if r.get("count_from") is not None:
                errors.append(f"{where}: a trap has no count_from")
            if not rid.startswith("T"):
                errors.append(f"{where}: trap IDs start with T")
        elif trap is False:
            if not r.get("document") or not r.get("client_reason"):
                errors.append(f"{where}: needs document and client_reason")
            if not rid.startswith("R"):
                errors.append(f"{where}: document rule IDs start with R")
        cf = r.get("count_from")
        if cf is not None and (cf not in facts or facts[cf]["type"] != "int"):
            errors.append(f"{where}: count_from must name an int fact")
        aliases = r.get("aliases")
        if not isinstance(aliases, list) or not aliases or not all(isinstance(a, str) and a.strip() for a in aliases):
            errors.append(f"{where}: aliases must be a non-empty list of strings")
        url = r.get("source_url") or ""
        if not url:
            errors.append(f"{where}: missing source_url")
        elif not _official(url):
            errors.append(f"{where}: source_url must be https on an official .gov domain: {url}")
        if not r.get("source_title"):
            errors.append(f"{where}: missing source_title")
        if r.get("source_version") not in SOURCE_VERSIONS:
            errors.append(f"{where}: source_version must be one of {SOURCE_VERSIONS}")
        if 2026 not in (r.get("tax_years") or []):
            errors.append(f"{where}: tax_years must include 2026")
        status = r.get("status")
        if status not in STATUSES:
            errors.append(f"{where}: status must be one of {STATUSES}")
        if status == "verified":
            if not r.get("verified_by") or not r.get("verified_on"):
                errors.append(f"{where}: verified rules need verified_by and verified_on")
            elif not _is_iso_date(r["verified_on"]):
                errors.append(f"{where}: verified_on must be a date")
        elif status == "to_verify" and (r.get("verified_by") or r.get("verified_on")):
            errors.append(f"{where}: a rule still to verify can't have verified_by or verified_on")
        for field in ("document", "client_reason", "source_title"):
            if isinstance(r.get(field), str) and DASHES.search(r[field]):
                errors.append(f"{where}: {field} has an em or en dash")
        if status == "to_verify":
            warnings.append(f"{rid}: still to verify")

    if list(seen) != EXPECTED_RULE_IDS:
        missing_ids = [x for x in EXPECTED_RULE_IDS if x not in seen]
        extra_ids = [x for x in seen if x not in EXPECTED_RULE_IDS]
        if missing_ids or extra_ids:
            errors.append(f"rule IDs: missing {missing_ids}, unexpected {extra_ids}")
        else:
            errors.append("rules must be listed in ID order, R001 to R030 then T001 to T003")
    follow_up_parents = {f["asked_if"]["fact"] for f in facts.values() if f.get("asked_if")}
    for key in facts:
        if key not in used_facts and key not in follow_up_parents:
            warnings.append(f"fact {key} isn't used by any rule")

    ignore = yaml.safe_load(Path(ignore_path).read_text())
    if not isinstance(ignore, dict) or not isinstance(ignore.get("ignore"), list) or not ignore["ignore"]:
        errors.append("ignore_aliases.yaml needs a non-empty ignore list")
    else:
        rule_aliases = {a.lower() for r in seen.values() for a in (r.get("aliases") or [])}
        for a in ignore["ignore"]:
            if str(a).lower() in rule_aliases:
                errors.append(f"ignore alias {a!r} is also a rule alias")

    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    errors, warnings = validate()
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    statuses: dict[str, int] = {}
    for r in yaml.safe_load(DOCUMENT_RULES_PATH.read_text()):
        statuses[r.get("status")] = statuses.get(r.get("status"), 0) + 1
    print(f"{len(errors)} errors, {len(warnings)} warnings; rule statuses: {statuses}")
    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
