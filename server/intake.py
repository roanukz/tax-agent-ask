"""Intake state for the tax server: a household profile in, a frozen draft out.

Each intake lives in memory under a server-minted intake_id and is written to
state_dir/{intake_id}.json after every change, for inspection. The document
list always comes from rules.engine applied to the recorded facts with the
statuses the server was started with; the model can only add flagged custom
requests next to it.

The only data files this module opens are household profiles, and only by a
household ID of H plus three digits, as a regular file directly inside the
profiles folder, never through a link. Nothing here can reach a client: the
last step freezes the draft for the preparer.
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import stat
import threading
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from rules import engine
from rules.engine import FactError, FactSpec, Rule

HOUSEHOLD_ID = re.compile(r"H[0-9]{3}")
INTAKE_ID = re.compile(r"in_[0-9a-f]{6}")
PRIOR_YEAR_PREFIX = "prior_year:"
TRAP_DOCUMENT = "No document needed: this credit ended"
COUNTED_STATUSES = ("verified", "to_verify")
OPEN = "open"
PENDING_REVIEW = "pending_review"
MINT_ATTEMPTS = 50
PUBLIC_INTAKE_FIELDS = ("intake_id", "household_id", "client_name", "tax_year", "prior_year_summary")
TRUTH_DIR = Path(__file__).resolve().parent.parent / "data" / "truth"


class IntakeError(ValueError):
    """A call the server refuses, with a message the model can act on."""


def new_intake_id() -> str:
    return "in_" + secrets.token_hex(3)


def checked_statuses(statuses: str | Iterable[str]) -> tuple[str, ...]:
    """Rule statuses that count: verified, to_verify, or both. Excluded rules never count."""
    items = statuses.split(",") if isinstance(statuses, str) else list(statuses)
    out = tuple(dict.fromkeys(s.strip() for s in items if s.strip()))
    bad = [s for s in out if s not in COUNTED_STATUSES]
    if not out or bad:
        raise ValueError(f"statuses must be one or more of {list(COUNTED_STATUSES)}, got {list(items)}")
    return out


def prior_year_fields(summary: Mapping[str, Any]) -> set[str]:
    """Top-level fields of last year's summary, plus documents_on_file.<rule_id> for each rule on file."""
    fields = set(summary)
    for doc in summary.get("documents_on_file") or []:
        if isinstance(doc, Mapping) and isinstance(doc.get("rule_id"), str):
            fields.add(f"documents_on_file.{doc['rule_id']}")
    return fields


def _plain(value: Any) -> Any:
    """Rule conditions as JSON: dates from YAML become ISO strings."""
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


def _is_under(path: Path, folder: Path) -> bool:
    """True when path or one of its parents is the same folder on disk as folder, however either is spelled."""
    try:
        target = folder.stat()
    except OSError:
        return False
    for candidate in (path, *path.parents):
        try:
            found = candidate.stat()
        except OSError:
            continue
        if (found.st_dev, found.st_ino) == (target.st_dev, target.st_ino):
            return True
    return False


def _refuse_truth(path: Path, what: str) -> None:
    """Refuses a folder named truth in any letter case (macOS ignores case), or the repo's data/truth by identity."""
    if any(part.casefold() == "truth" for part in path.parts) or _is_under(path, TRUTH_DIR):
        raise ValueError(f"the {what} folder can't be inside a truth folder: {path}")


def _read_no_follow(path: str, flags: int) -> int:
    return os.open(path, flags | os.O_NOFOLLOW)


def _write_no_follow(path: str, flags: int) -> int:
    return os.open(path, flags | os.O_NOFOLLOW, 0o644)


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntakeError(f"{name} is empty")
    return value.strip()


def fact_view(spec: FactSpec) -> dict[str, Any]:
    view: dict[str, Any] = {"key": spec.key, "type": spec.type}
    if spec.values is not None:
        view["values"] = list(spec.values)
    if spec.min is not None:
        view["min"] = spec.min
    if spec.max is not None:
        view["max"] = spec.max
    view.update(
        topic=spec.topic,
        plain_question_hint=spec.plain_question_hint,
        asked_if=_plain(spec.asked_if),
        determinable_from_prior_year=spec.determinable_from_prior_year,
        prior_year_field=spec.prior_year_field,
    )
    return view


def rule_view(rule: Rule) -> dict[str, Any]:
    """A rule as the agent sees it. A trap names no document and gives no client reason."""
    view: dict[str, Any] = {
        "rule_id": rule.id,
        "when": _plain(rule.when),
        "document": TRAP_DOCUMENT if rule.trap else rule.document,
        "count_from": rule.count_from,
    }
    if not rule.trap:
        view["client_reason"] = rule.client_reason
    view.update(
        source_url=rule.source_url,
        source_title=rule.source_title,
        source_version=rule.source_version,
        status=rule.status,
    )
    return view


class IntakeBook:
    """Every intake this server process has started, keyed by intake_id."""

    def __init__(
        self,
        profiles_dir: str | Path,
        *,
        statuses: str | Iterable[str],
        state_dir: str | Path,
        facts_path: str | Path = engine.FACTS_PATH,
        rules_path: str | Path = engine.DOCUMENT_RULES_PATH,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.profiles_dir = Path(profiles_dir).resolve()
        if not self.profiles_dir.is_dir():
            raise ValueError(f"profiles folder not found: {self.profiles_dir}")
        _refuse_truth(self.profiles_dir, "profiles")
        self.state_dir = Path(state_dir).resolve()
        _refuse_truth(self.state_dir, "state")
        self.statuses = checked_statuses(statuses)
        self.specs = engine.load_facts(Path(facts_path))
        self.rules = engine.load_rules(Path(rules_path))
        self.rules_by_id = {r.id: r for r in self.rules}
        self.id_factory = id_factory or new_intake_id
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._intakes: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    # Read-only views of the facts and rules.

    def fact_keys(self) -> list[dict[str, Any]]:
        return [fact_view(spec) for spec in self.specs.values()]

    def document_rules(self, fact_key: str | None = None) -> list[dict[str, Any]]:
        if fact_key is not None and fact_key not in self.specs:
            raise IntakeError(f"unknown fact key {fact_key!r}; list_fact_keys gives every key")
        rules = engine.rules_with_status(self.rules, self.statuses)
        if fact_key is not None:
            rules = [r for r in rules if fact_key in engine.rule_facts(r.when)]
        return [rule_view(r) for r in rules]

    # Intake lifecycle.

    def start_intake(self, household_id: Any) -> dict[str, Any]:
        profile = self._load_profile(household_id)
        with self._lock:
            intake_id = self._mint_id()
            intake = {
                "intake_id": intake_id,
                "household_id": household_id,
                "client_name": profile.get("client_name"),
                "tax_year": profile["tax_year"],
                "prior_year_summary": profile["prior_year_summary"],
                "statuses": list(self.statuses),
                "status": OPEN,
                "facts": {},
                "fact_history": [],
                "custom_requests": [],
                "notes_for_preparer": [],
                "draft": None,
            }
            self._intakes[intake_id] = intake
            self._save(intake)
            return copy.deepcopy({k: intake[k] for k in PUBLIC_INTAKE_FIELDS})

    def record_fact(self, intake_id: str, key: str, value: Any, evidence: Any) -> dict[str, Any]:
        with self._lock:
            intake = self._open_intake(intake_id)
            spec = self.specs.get(key)
            if spec is None:
                raise IntakeError(f"unknown fact key {key!r}; list_fact_keys gives every key")
            stored = self._coerced(spec, value)
            text = self._checked_evidence(intake, evidence)
            entry = {"value": stored, "evidence": text}
            intake["facts"][key] = entry
            intake["fact_history"].append({"key": key, **entry})
            self._save(intake)
            times = sum(1 for h in intake["fact_history"] if h["key"] == key)
            return {"intake_id": intake_id, "key": key, "value": stored, "evidence": text, "times_recorded": times}

    def request_list(self, intake_id: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._computed(self._intake(intake_id)))

    def add_custom_request(self, intake_id: str, document: Any, reason: Any) -> dict[str, Any]:
        with self._lock:
            intake = self._open_intake(intake_id)
            request = {
                "document": _required_text(document, "document"),
                "reason": _required_text(reason, "reason"),
                "needs_preparer_review": True,
            }
            intake["custom_requests"].append(request)
            self._save(intake)
            return copy.deepcopy(request)

    def note_for_preparer(self, intake_id: str, text: Any) -> dict[str, Any]:
        with self._lock:
            intake = self._open_intake(intake_id)
            note = {"text": _required_text(text, "text")}
            intake["notes_for_preparer"].append(note)
            self._save(intake)
            return copy.deepcopy(note)

    def submit_for_preparer_review(self, intake_id: str) -> dict[str, Any]:
        with self._lock:
            intake = self._open_intake(intake_id)
            computed = self._computed(intake)
            draft = {
                "intake_id": intake["intake_id"],
                "household_id": intake["household_id"],
                "status": PENDING_REVIEW,
                "documents": computed["documents"],
                "custom_requests": computed["custom_requests"],
                "not_asked": computed["not_asked"],
                "no_document_needed": computed["no_document_needed"],
                "notes_for_preparer": copy.deepcopy(intake["notes_for_preparer"]),
                "facts_recorded": copy.deepcopy(intake["facts"]),
            }
            intake["status"] = PENDING_REVIEW
            intake["draft"] = draft
            self._save(intake)
            return copy.deepcopy(draft)

    # Internals.

    def _load_profile(self, household_id: Any) -> dict[str, Any]:
        if not isinstance(household_id, str) or not HOUSEHOLD_ID.fullmatch(household_id):
            raise IntakeError(f"household_id must be H followed by three digits, such as H017; got {household_id!r}")
        path = self.profiles_dir / f"{household_id}.json"
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            raise IntakeError(f"no profile for household {household_id}") from None
        except OSError:
            raise IntakeError(f"the profile for household {household_id} could not be read") from None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or path.resolve().parent != self.profiles_dir:
            raise IntakeError(f"the profile for household {household_id} is not a regular file in the profiles folder")
        try:
            with open(path, encoding="utf-8", opener=_read_no_follow) as f:
                profile = json.load(f)
        except (OSError, ValueError):
            raise IntakeError(f"the profile for household {household_id} could not be read") from None
        self._check_profile(profile, household_id)
        return profile

    @staticmethod
    def _check_profile(profile: Any, household_id: str) -> None:
        ok = (
            isinstance(profile, dict)
            and profile.get("household_id") == household_id
            and isinstance(profile.get("tax_year"), int)
            and isinstance(profile.get("prior_year_summary"), dict)
            and isinstance(profile["prior_year_summary"].get("documents_on_file", []), list)
            and isinstance(profile.get("client_name"), (str, type(None)))
        )
        if not ok:
            raise IntakeError(f"the profile for household {household_id} is malformed")

    def _mint_id(self) -> str:
        for _ in range(MINT_ATTEMPTS):
            candidate = self.id_factory()
            if not isinstance(candidate, str) or not INTAKE_ID.fullmatch(candidate):
                raise ValueError(f"id_factory must return in_ plus six lowercase hex digits, got {candidate!r}")
            if candidate not in self._intakes and not self._state_path(candidate).exists():
                return candidate
        raise IntakeError("could not mint a new intake_id; try start_intake again")

    def _intake(self, intake_id: Any) -> dict[str, Any]:
        intake = self._intakes.get(intake_id) if isinstance(intake_id, str) else None
        if intake is None:
            raise IntakeError(f"unknown intake_id {intake_id!r}; start_intake returns one")
        return intake

    def _open_intake(self, intake_id: Any) -> dict[str, Any]:
        intake = self._intake(intake_id)
        if intake["status"] != OPEN:
            raise IntakeError(f"intake {intake_id} was already submitted for preparer review and can't change")
        return intake

    @staticmethod
    def _coerced(spec: FactSpec, value: Any) -> Any:
        """engine.coerce, with any other ValueError it lets through ('²' or '--3') reported the same way."""
        try:
            return engine.coerce(spec, value)
        except FactError:
            raise
        except ValueError:
            raise FactError(f"{spec.key} doesn't accept {value!r} as a value of type {spec.type}") from None

    def _checked_evidence(self, intake: Mapping[str, Any], evidence: Any) -> str:
        if not isinstance(evidence, str) or not evidence.strip():
            raise IntakeError("evidence is empty; give the client's exact words, or prior_year:<field>")
        text = evidence.strip()
        if text.startswith(PRIOR_YEAR_PREFIX):
            name = text[len(PRIOR_YEAR_PREFIX):].strip()
            allowed = prior_year_fields(intake["prior_year_summary"])
            if name not in allowed:
                choices = ", ".join(PRIOR_YEAR_PREFIX + f for f in sorted(allowed))
                raise IntakeError(f"last year's summary has no field {name!r}; use one of: {choices}")
        return text

    def _computed(self, intake: Mapping[str, Any]) -> dict[str, Any]:
        recorded = {key: entry["value"] for key, entry in intake["facts"].items()}
        documents = []
        for item in engine.required_documents(recorded, self.rules, self.specs, self.statuses):
            rule = self.rules_by_id[item["rule_id"]]
            documents.append({
                "rule_id": rule.id,
                "document": rule.document,
                "count": item["count"],
                "client_reason": rule.client_reason,
                "source_url": rule.source_url,
                "source_title": rule.source_title,
            })
        no_document_needed = []
        for rule_id in engine.triggered_traps(recorded, self.rules, self.specs, self.statuses):
            rule = self.rules_by_id[rule_id]
            no_document_needed.append({
                "rule_id": rule.id,
                "reason": TRAP_DOCUMENT,
                "source_url": rule.source_url,
                "source_title": rule.source_title,
            })
        return {
            "documents": documents,
            "custom_requests": copy.deepcopy(intake["custom_requests"]),
            "not_asked": self._not_asked(recorded),
            "no_document_needed": no_document_needed,
        }

    def _not_asked(self, recorded: Mapping[str, Any]) -> list[str]:
        """Top-level keys never recorded, plus follow-ups whose parent condition holds but were never recorded."""
        return [
            key
            for key, spec in self.specs.items()
            if key not in recorded
            and (spec.asked_if is None or engine.condition_holds(spec.asked_if, recorded, self.specs))
        ]

    def _state_path(self, intake_id: str) -> Path:
        return self.state_dir / f"{intake_id}.json"

    def _save(self, intake: Mapping[str, Any]) -> None:
        path = self._state_path(intake["intake_id"])
        tmp = path.with_name(f".{path.name}.tmp")
        with open(tmp, "w", encoding="utf-8", opener=_write_no_follow) as f:
            f.write(json.dumps(intake, indent=2) + "\n")
        os.replace(tmp, path)
