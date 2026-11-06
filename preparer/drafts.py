"""Review drafts: check them, load them from the drafts folder, and compute the flags a preparer checks.

A draft is one agent run's submitted document list as eval/export_drafts.py writes it: each
document with its rule's text and the recorded facts that triggered it, the custom requests,
the ended credits, the facts never asked, the notes for the preparer, the facts recorded, and
the agent's closing message. It carries no config, run ID, score, household ID or answer.
Those stay in the answer key under data/truth/, and nothing here opens a truth folder: the
drafts folder is refused if it is one, by the same check the tax server uses.

Flags come from the draft and last year's summary alone, computed by code in this order:

    dropped          a rule on file last year has no request this year, or a lower count
    carried          a fact was recorded with prior_year: evidence, so the client didn't confirm it
    custom           the agent added a custom request
    client_question  the agent left a note for the preparer
    ended_credit     a rule for a credit that ended fired, so no document is needed

Each flag has an ID the view sends back in flags_checked: dropped:R001, carried:filing_status,
custom:0, client_question:0, ended_credit:T001. The facts never asked are not a flag.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from rules import engine
from rules.engine import FactError, FactSpec, Rule
from server.intake import PRIOR_YEAR_PREFIX, TRAP_DOCUMENT, _read_no_follow, _refuse_truth, prior_year_fields

DRAFT_ID = re.compile(r"d[0-9]{2}|demo[0-9]")
DRAFT_KEYS = (
    "draft_id",
    "client_name",
    "tax_year",
    "prior_year_summary",
    "documents",
    "custom_requests",
    "no_document_needed",
    "not_asked",
    "notes_for_preparer",
    "facts_recorded",
    "agent_note",
    "draft_sha256",
)
DOCUMENT_KEYS = ("rule_id", "document", "count", "client_reason", "source_url", "source_title", "triggered_by")
TRIGGER_KEYS = ("fact", "value", "evidence")
CUSTOM_KEYS = ("document", "reason", "needs_preparer_review")
NO_DOCUMENT_KEYS = ("rule_id", "reason", "source_url", "source_title")
SUMMARY_KEYS = ("tax_year", "filing_status", "dependents_count", "itemized", "documents_on_file")
FILED_KEYS = ("rule_id", "label", "count")
FLAG_KINDS = ("dropped", "carried", "custom", "client_question", "ended_credit")

# Rules of either status a draft could have been built with: test drafts used verified rules,
# dev drafts verified and to_verify. Excluded rules never produce a document.
DRAFT_STATUSES = ("verified", "to_verify")

# Field names that would carry a result or the run's identity into a draft. A draft's key sets are
# fixed, so none can appear; the check is kept separate so the done-check can say so on its own.
RESULT_KEYS = frozenset({
    "run_id", "config", "trial", "model", "household_id", "intake_id",
    "pass", "scorer_pass", "score", "fail_reasons", "judge",
    "required", "missed", "unnecessary", "wrong_facts", "stale_carryovers",
    "problem_class", "problems", "shows_because", "truth",
})
RUN_ID_TEXT = re.compile(r"\b(?:train|dev|test)-H[0-9]{3}-")
HOUSEHOLD_TEXT = re.compile(r"\bH[0-9]{3}\b|\b(?:client|household|filer)\s+[0-9]{3}\b", re.IGNORECASE)
SHA256_TEXT = re.compile(r"[0-9a-f]{64}")


class DraftError(ValueError):
    """A draft file that is missing, unreadable, or doesn't match the schema and the rules."""


@dataclass(frozen=True)
class Reference:
    """The rules and fact keys a draft is checked against."""

    rules: Mapping[str, Rule]
    specs: Mapping[str, FactSpec]

    @classmethod
    def load(cls, rules_path: str | Path = engine.DOCUMENT_RULES_PATH, facts_path: str | Path = engine.FACTS_PATH) -> "Reference":
        return cls(rules={r.id: r for r in engine.load_rules(Path(rules_path))}, specs=engine.load_facts(Path(facts_path)))

    @property
    def request_rules(self) -> list[Rule]:
        """Rules that can put a document on a list: not a trap, not excluded, in rule ID order."""
        return sorted(
            (r for r in self.rules.values() if not r.trap and r.status in DRAFT_STATUSES), key=lambda r: r.id
        )


# Writing and hashing.


def dump(obj: Any) -> str:
    """The preparer's JSON file format: indent 2, keys in insertion order, a trailing newline, and ASCII only,
    so a dash in the client's or the agent's own words is stored as a \\u escape and never as a raw character."""
    return json.dumps(obj, indent=2, ensure_ascii=True) + "\n"


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def draft_digest(draft: Mapping[str, Any]) -> str:
    """SHA-256 of the draft's content without its draft_sha256 field, as canonical JSON."""
    return hashlib.sha256(canonical({k: v for k, v in draft.items() if k != "draft_sha256"})).hexdigest()


# What a rule reads, and what triggered it.


def condition_facts(cond: Mapping[str, Any]) -> list[str]:
    """Every fact key a condition reads, in the order the condition names them, each once."""
    out: list[str] = []

    def walk(c: Mapping[str, Any]) -> None:
        for group in ("all", "any"):
            if group in c:
                for sub in c[group]:
                    walk(sub)
                return
        if c["fact"] not in out:
            out.append(c["fact"])

    walk(cond)
    return out


def rule_fact_keys(rule: Rule) -> list[str]:
    """The facts behind a rule: its condition's facts, then the fact its count comes from."""
    keys = condition_facts(rule.when)
    if rule.count_from and rule.count_from not in keys:
        keys.append(rule.count_from)
    return keys


def triggered_by(rule: Rule, facts_recorded: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The recorded facts behind a rule, with their values and evidence. A fact never recorded is left out."""
    return [
        {"fact": key, "value": facts_recorded[key]["value"], "evidence": facts_recorded[key]["evidence"]}
        for key in rule_fact_keys(rule)
        if key in facts_recorded
    ]


def is_carried(evidence: Any) -> bool:
    return isinstance(evidence, str) and evidence.startswith(PRIOR_YEAR_PREFIX)


def computed_not_asked(recorded: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> list[str]:
    """Top-level keys never recorded, plus follow-ups whose parent condition holds, as the tax server lists them."""
    return [
        key
        for key, spec in specs.items()
        if key not in recorded and (spec.asked_if is None or engine.condition_holds(spec.asked_if, recorded, specs))
    ]


# Flags.


def filed_last_year(summary: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Last year's documents on file by rule ID, with the first label given and the counts added up."""
    out: dict[str, dict[str, Any]] = {}
    for doc in summary.get("documents_on_file") or []:
        entry = out.setdefault(doc["rule_id"], {"label": doc["label"], "count": 0})
        entry["count"] += int(doc["count"])
    return out


def requested_counts(draft: Mapping[str, Any]) -> dict[str, int]:
    return {d["rule_id"]: int(d["count"]) for d in draft["documents"]}


def compute_flags(draft: Mapping[str, Any], ref: Reference) -> list[dict[str, Any]]:
    """Every flag on a draft, in the order the screen shows them."""
    facts = draft["facts_recorded"]
    flags: list[dict[str, Any]] = []
    requested = requested_counts(draft)
    for rule_id, filed in sorted(filed_last_year(draft["prior_year_summary"]).items()):
        this_year = requested.get(rule_id, 0)
        if this_year >= filed["count"]:
            continue
        rule = ref.rules[rule_id]
        flags.append({
            "id": f"dropped:{rule_id}",
            "kind": "dropped",
            "rule_id": rule_id,
            "label": filed["label"],
            "last_year_count": filed["count"],
            "this_year_count": this_year,
            "document": rule.document,
            "facts": [
                {
                    "fact": key,
                    "recorded": key in facts,
                    "value": facts[key]["value"] if key in facts else None,
                    "evidence": facts[key]["evidence"] if key in facts else None,
                }
                for key in rule_fact_keys(rule)
            ],
        })
    for key in ref.specs:
        if key in facts and is_carried(facts[key]["evidence"]):
            flags.append({
                "id": f"carried:{key}",
                "kind": "carried",
                "fact": key,
                "value": facts[key]["value"],
                "evidence": facts[key]["evidence"],
            })
    for i, req in enumerate(draft["custom_requests"]):
        flags.append({"id": f"custom:{i}", "kind": "custom", "index": i, "document": req["document"], "reason": req["reason"]})
    for i, note in enumerate(draft["notes_for_preparer"]):
        flags.append({"id": f"client_question:{i}", "kind": "client_question", "index": i, "text": note["text"]})
    for entry in draft["no_document_needed"]:
        flags.append({
            "id": f"ended_credit:{entry['rule_id']}",
            "kind": "ended_credit",
            "rule_id": entry["rule_id"],
            "reason": entry["reason"],
            "source_url": entry["source_url"],
            "source_title": entry["source_title"],
        })
    return flags


def addable_rules(draft: Mapping[str, Any], ref: Reference) -> list[dict[str, Any]]:
    """Request rules not on the draft, which the preparer can add. Traps are never offered."""
    on_draft = {d["rule_id"] for d in draft["documents"]}
    return [
        {
            "rule_id": r.id,
            "document": r.document,
            "client_reason": r.client_reason,
            "source_url": r.source_url,
            "source_title": r.source_title,
            "count_from": r.count_from,
        }
        for r in ref.request_rules
        if r.id not in on_draft
    ]


# Checking a draft.


def _keys(obj: Any, keys: Iterable[str], where: str, problems: list[str]) -> bool:
    keys = tuple(keys)
    if not isinstance(obj, dict):
        problems.append(f"{where} must be an object")
        return False
    if tuple(obj) != keys:
        problems.append(f"{where} must have exactly the keys {list(keys)} in that order, got {list(obj)}")
        return False
    return True


def _text(value: Any, where: str, problems: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where} must be non-empty text")


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _rule(ref: Reference, rule_id: Any) -> Rule | None:
    return ref.rules.get(rule_id) if isinstance(rule_id, str) else None


def result_fields(obj: Any, path: str = "draft") -> list[str]:
    """Paths of any field named for a result or a run's identity, anywhere in obj."""
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in RESULT_KEYS:
                found.append(f"{path}.{k}")
            found.extend(result_fields(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(result_fields(v, f"{path}[{i}]"))
    return found


def identifying_text(obj: Any, path: str = "draft") -> list[str]:
    """Paths of any text that names a run ID or a household ID."""
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.extend(identifying_text(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(identifying_text(v, f"{path}[{i}]"))
    elif isinstance(obj, str) and (RUN_ID_TEXT.search(obj) or HOUSEHOLD_TEXT.search(obj)):
        found.append(path)
    return found


def check_draft(draft: Any, ref: Reference) -> None:
    """Raises DraftError naming every way the draft breaks the schema, the rules or the blind."""
    problems: list[str] = []
    if not _keys(draft, DRAFT_KEYS, "the draft", problems):
        raise DraftError("; ".join(problems))
    if not isinstance(draft["draft_id"], str) or not DRAFT_ID.fullmatch(draft["draft_id"]):
        problems.append(f"draft_id must be d plus two digits or demo plus one digit, got {draft['draft_id']!r}")
    _text(draft["client_name"], "client_name", problems)
    if not _whole(draft["tax_year"]) or not 2000 <= draft["tax_year"] <= 2100:
        problems.append("tax_year must be a whole number of a year")

    summary = draft["prior_year_summary"]
    before = len(problems)
    if _keys(summary, SUMMARY_KEYS, "prior_year_summary", problems):
        if not _whole(summary["tax_year"]) or not _whole(draft["tax_year"]) or summary["tax_year"] != draft["tax_year"] - 1:
            problems.append("prior_year_summary.tax_year must be the year before tax_year")
        status = ref.specs.get("filing_status")
        if status is not None and summary["filing_status"] not in (status.values or ()):
            problems.append("prior_year_summary.filing_status must be one of the filing_status values")
        dependents = ref.specs.get("dependents_count")
        low = dependents.min if dependents and dependents.min is not None else 0
        high = dependents.max if dependents and dependents.max is not None else 10
        if not _whole(summary["dependents_count"]) or not low <= summary["dependents_count"] <= high:
            problems.append(f"prior_year_summary.dependents_count must be a whole number from {low} to {high}")
        if not isinstance(summary["itemized"], bool):
            problems.append("prior_year_summary.itemized must be true or false")
        if not isinstance(summary["documents_on_file"], list):
            problems.append("prior_year_summary.documents_on_file must be a list")
        else:
            for i, doc in enumerate(summary["documents_on_file"]):
                where = f"prior_year_summary.documents_on_file[{i}]"
                if not _keys(doc, FILED_KEYS, where, problems):
                    continue
                rule = _rule(ref, doc["rule_id"])
                if rule is None or rule.trap:
                    problems.append(f"{where} names {doc['rule_id']!r}, which is not a request rule")
                _text(doc["label"], f"{where}.label", problems)
                if not _whole(doc["count"]) or doc["count"] < 1:
                    problems.append(f"{where}.count must be a whole number of at least 1")
    summary_ok = len(problems) == before

    facts = draft["facts_recorded"]
    recorded: dict[str, Any] = {}
    before = len(problems)
    if not isinstance(facts, dict):
        problems.append("facts_recorded must be an object")
        facts = {}
    for key, entry in facts.items():
        where = f"facts_recorded.{key}"
        spec = ref.specs.get(key)
        if spec is None:
            problems.append(f"{where} is not a fact key in rules/facts.yaml")
            continue
        if not _keys(entry, ("value", "evidence"), where, problems):
            continue
        try:
            ok = engine.coerce(spec, entry["value"]) == entry["value"] and type(entry["value"]) is type(
                engine.coerce(spec, entry["value"])
            )
        except (FactError, ValueError):
            ok = False
        if not ok:
            problems.append(f"{where}.value {entry['value']!r} is not a stored {spec.type} for this key")
        _text(entry["evidence"], f"{where}.evidence", problems)
        if is_carried(entry["evidence"]) and summary_ok:
            field = entry["evidence"][len(PRIOR_YEAR_PREFIX):].strip()
            if field not in prior_year_fields(summary):
                problems.append(f"{where}.evidence names {field!r}, which last year's summary doesn't have")
        recorded[key] = entry["value"]
    facts_ok = len(problems) == before

    if not isinstance(draft["documents"], list):
        problems.append("documents must be a list")
    else:
        for i, doc in enumerate(draft["documents"]):
            where = f"documents[{i}]"
            if not _keys(doc, DOCUMENT_KEYS, where, problems):
                continue
            rule = _rule(ref, doc["rule_id"])
            if rule is None or rule.trap:
                problems.append(f"{where} names {doc['rule_id']!r}, which is not a request rule")
                continue
            for field in ("document", "client_reason", "source_url", "source_title"):
                if doc[field] != getattr(rule, field):
                    problems.append(f"{where}.{field} doesn't match {rule.id} in the rules file")
            if not _whole(doc["count"]) or doc["count"] < 1 or (rule.count_from is None and doc["count"] != 1):
                problems.append(f"{where}.count must be 1, or at least 1 on a counted rule")
            if facts_ok and canonical(doc["triggered_by"]) != canonical(triggered_by(rule, facts)):
                problems.append(f"{where}.triggered_by doesn't match the facts recorded for {rule.id}")

    if not isinstance(draft["custom_requests"], list):
        problems.append("custom_requests must be a list")
    else:
        for i, req in enumerate(draft["custom_requests"]):
            where = f"custom_requests[{i}]"
            if _keys(req, CUSTOM_KEYS, where, problems):
                _text(req["document"], f"{where}.document", problems)
                _text(req["reason"], f"{where}.reason", problems)
                if req["needs_preparer_review"] is not True:
                    problems.append(f"{where}.needs_preparer_review must be true")

    if not isinstance(draft["no_document_needed"], list):
        problems.append("no_document_needed must be a list")
    else:
        for i, entry in enumerate(draft["no_document_needed"]):
            where = f"no_document_needed[{i}]"
            if not _keys(entry, NO_DOCUMENT_KEYS, where, problems):
                continue
            rule = _rule(ref, entry["rule_id"])
            if rule is None or not rule.trap:
                problems.append(f"{where} names {entry['rule_id']!r}, which is not a trap rule")
                continue
            if entry["reason"] != TRAP_DOCUMENT or entry["source_url"] != rule.source_url or entry["source_title"] != rule.source_title:
                problems.append(f"{where} doesn't match {rule.id} in the rules file")

    if not isinstance(draft["notes_for_preparer"], list):
        problems.append("notes_for_preparer must be a list")
    else:
        for i, note in enumerate(draft["notes_for_preparer"]):
            if _keys(note, ("text",), f"notes_for_preparer[{i}]", problems):
                _text(note["text"], f"notes_for_preparer[{i}].text", problems)

    if not isinstance(draft["not_asked"], list) or not all(isinstance(k, str) for k in draft["not_asked"]):
        problems.append("not_asked must be a list of fact keys")
    if not isinstance(draft["agent_note"], str):
        problems.append("agent_note must be text")

    # The draft must be exactly what the rules engine gives for its recorded facts.
    if not problems:
        rules = list(ref.rules.values())
        expected = engine.required_documents(recorded, rules, ref.specs, DRAFT_STATUSES)
        got = [{"rule_id": d["rule_id"], "count": d["count"]} for d in draft["documents"]]
        if got != expected:
            problems.append(f"documents {got} are not the rules engine's list for the facts recorded, {expected}")
        traps = engine.triggered_traps(recorded, rules, ref.specs, DRAFT_STATUSES)
        if [e["rule_id"] for e in draft["no_document_needed"]] != traps:
            problems.append(f"no_document_needed doesn't match the traps the facts recorded trigger, {traps}")
        if draft["not_asked"] != computed_not_asked(recorded, ref.specs):
            problems.append("not_asked doesn't match the facts never recorded")

    leaks = result_fields(draft)
    if leaks:
        problems.append(f"the draft holds result or run fields: {leaks}")
    named = identifying_text(draft)
    if named:
        problems.append(f"text names a run ID or a household ID at: {named}")
    if not isinstance(draft["draft_sha256"], str) or not SHA256_TEXT.fullmatch(draft["draft_sha256"]):
        problems.append("draft_sha256 must be 64 lowercase hex digits")
    elif draft["draft_sha256"] != draft_digest(draft):
        problems.append("draft_sha256 doesn't match the draft's content")
    if problems:
        raise DraftError("; ".join(problems))


# Loading drafts from a folder.


class DraftFolder:
    """The folder a preparer server reads drafts from. It refuses a truth folder and opens only regular
    files named {draft_id}.json directly inside it, never through a link."""

    def __init__(self, folder: str | Path, ref: Reference | None = None) -> None:
        self.folder = Path(folder).resolve()
        _refuse_truth(self.folder, "drafts")
        if not self.folder.is_dir():
            raise ValueError(f"drafts folder not found: {self.folder}")
        self.ref = ref or Reference.load()

    def ids(self) -> list[str]:
        out = []
        for path in self.folder.iterdir():
            if path.suffix == ".json" and DRAFT_ID.fullmatch(path.stem) and stat.S_ISREG(os.lstat(path).st_mode):
                out.append(path.stem)
        return sorted(out)

    def load(self, draft_id: Any) -> dict[str, Any]:
        if not isinstance(draft_id, str) or not DRAFT_ID.fullmatch(draft_id):
            raise DraftError(f"draft_id must be d plus two digits, such as d07; got {draft_id!r}")
        path = self.folder / f"{draft_id}.json"
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            raise DraftError(f"no draft {draft_id}") from None
        except OSError:
            raise DraftError(f"draft {draft_id} could not be read") from None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or path.resolve().parent != self.folder:
            raise DraftError(f"draft {draft_id} is not a regular file in the drafts folder")
        try:
            with open(path, encoding="utf-8", opener=_read_no_follow) as f:
                draft = json.load(f)
        except (OSError, ValueError, RecursionError):
            raise DraftError(f"draft {draft_id} could not be read") from None
        try:
            check_draft(draft, self.ref)
        except RecursionError:
            raise DraftError(f"draft {draft_id} could not be read") from None
        if draft["draft_id"] != draft_id:
            raise DraftError(f"the file {draft_id}.json holds draft {draft['draft_id']}")
        return draft

    def load_all(self) -> dict[str, dict[str, Any]]:
        return {draft_id: self.load(draft_id) for draft_id in self.ids()}
