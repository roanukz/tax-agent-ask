"""Review state for the preparer server: decisions and their check, review tokens, and review records.

A review moves from pending_review (no record yet) to in_review (saved) to approved, which is
final. The view sends structured decisions only, never free text:

    {"documents":       [{"rule_id": "R001", "action": "keep", "count": 2},
                         {"rule_id": "R023", "action": "remove", "reason": "not_needed"}],
     "custom_requests": [{"index": 0, "action": "keep"}],
     "added":           [{"rule_id": "R012"}],
     "follow_ups":      [{"fact": "w2_employer_count"}],
     "flags_checked":   ["dropped:R001", "client_question:0"]}

A save may be partial. An approval needs one decision for every document and custom request
and every flag checked. A count appears only on a rule with count_from, at least 1 and at most
the fact's maximum, because the rules engine drops a counted rule at 0.

open_review mints a review token for one draft and its SHA-256, kept in memory only, and
reopening a draft retires the old token. The server, not the view, keeps every time, so
seconds_on_screen never trusts the view's clock: it adds up, for each opening that saved or
approved, the time from opening to the last save or approval.

Records are written to the reviews folder, which may not be a truth folder, as regular files
named {draft_id}.json, replaced atomically and never through a link. Nothing here sends
anything to anyone: the approved list is stored for the preparer to send through their own system.
"""

from __future__ import annotations

import fcntl
import hmac
import json
import os
import secrets
import stat
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from preparer.drafts import DRAFT_ID, Reference, dump
from server.intake import _read_no_follow, _refuse_truth

APP_VERSION = "0.1.0"
PENDING = "pending_review"
IN_REVIEW = "in_review"
APPROVED = "approved"
ACTIONS = ("keep", "remove")
REASONS = ("not_needed", "duplicate", "client_said_no")
DECISION_KEYS = ("documents", "custom_requests", "added", "follow_ups", "flags_checked")
RECORD_KEYS = (
    "draft_id", "draft_sha256", "status", "decisions", "approved_list", "follow_up_questions", "changes",
    "opened_at", "saved_at", "approved_at", "seconds_on_screen", "screen_time", "host", "client", "app_version",
)
NAME_LIMIT = 200
VERSION_LIMIT = 100


class ReviewError(ValueError):
    """A save or approval the server refuses, with a plain message the view shows as it is."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# Decisions.


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _exact(obj: Any, required: Sequence[str], optional: Sequence[str], where: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ReviewError(f"{where} must be an object")
    keys = set(obj)
    missing = [k for k in required if k not in keys]
    extra = sorted(keys - set(required) - set(optional))
    if missing or extra:
        allowed = list(required) + [f"{k} (optional)" for k in optional]
        raise ReviewError(f"{where} must have the keys {allowed}; missing {missing}, unexpected {extra}")
    return obj


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReviewError(f"{where} must be a list")
    return value


def _count(value: Any, rule_id: str, ref: Reference, where: str) -> None:
    rule = ref.rules[rule_id]
    if rule.count_from is None:
        raise ReviewError(f"{where}: {rule_id} has no count; only a W-2 or K-1 rule takes one")
    spec = ref.specs[rule.count_from]
    top = spec.max if spec.max is not None else 10
    if not _whole(value) or not 1 <= value <= top:
        raise ReviewError(f"{where}: the count for {rule_id} must be a whole number from 1 to {top}")


def check_decisions(
    decisions: Any,
    draft: Mapping[str, Any],
    flags: Sequence[Mapping[str, Any]],
    ref: Reference,
    *,
    final: bool,
) -> dict[str, Any]:
    """Raises ReviewError on the first way the decisions break the schema; with final, also when one is missing."""
    _exact(decisions, DECISION_KEYS, (), "decisions")
    on_draft = {d["rule_id"]: d for d in draft["documents"]}

    seen: set[str] = set()
    for i, item in enumerate(_list(decisions["documents"], "decisions.documents")):
        where = f"decisions.documents[{i}]"
        if not isinstance(item, dict) or item.get("action") not in ACTIONS:
            raise ReviewError(f"{where} must be an object whose action is keep or remove")
        if item["action"] == "keep":
            _exact(item, ("rule_id", "action"), ("count",), where)
        else:
            _exact(item, ("rule_id", "action", "reason"), (), where)
            if item["reason"] not in REASONS:
                raise ReviewError(f"{where}: the reason must be one of {list(REASONS)}")
        rule_id = item["rule_id"]
        if not isinstance(rule_id, str) or rule_id not in on_draft:
            raise ReviewError(f"{where}: {rule_id!r} is not a document on this draft")
        if rule_id in seen:
            raise ReviewError(f"{where}: {rule_id} has more than one decision")
        seen.add(rule_id)
        if "count" in item:
            _count(item["count"], rule_id, ref, where)

    customs = draft["custom_requests"]
    seen_custom: set[int] = set()
    for i, item in enumerate(_list(decisions["custom_requests"], "decisions.custom_requests")):
        where = f"decisions.custom_requests[{i}]"
        if not isinstance(item, dict) or item.get("action") not in ACTIONS:
            raise ReviewError(f"{where} must be an object whose action is keep or remove")
        if item["action"] == "keep":
            _exact(item, ("index", "action"), (), where)
        else:
            _exact(item, ("index", "action", "reason"), (), where)
            if item["reason"] not in REASONS:
                raise ReviewError(f"{where}: the reason must be one of {list(REASONS)}")
        index = item["index"]
        if not _whole(index) or not 0 <= index < len(customs):
            raise ReviewError(f"{where}: index must name one of this draft's {len(customs)} custom requests")
        if index in seen_custom:
            raise ReviewError(f"{where}: custom request {index} has more than one decision")
        seen_custom.add(index)

    addable = {r.id for r in ref.request_rules} - set(on_draft)
    seen_added: set[str] = set()
    for i, item in enumerate(_list(decisions["added"], "decisions.added")):
        where = f"decisions.added[{i}]"
        _exact(item, ("rule_id",), ("count",), where)
        rule_id = item["rule_id"]
        if not isinstance(rule_id, str) or rule_id not in addable:
            raise ReviewError(f"{where}: {rule_id!r} is not a request rule missing from this draft")
        if rule_id in seen_added:
            raise ReviewError(f"{where}: {rule_id} is added more than once")
        seen_added.add(rule_id)
        if "count" in item:
            _count(item["count"], rule_id, ref, where)

    seen_facts: set[str] = set()
    for i, item in enumerate(_list(decisions["follow_ups"], "decisions.follow_ups")):
        where = f"decisions.follow_ups[{i}]"
        _exact(item, ("fact",), (), where)
        if not isinstance(item["fact"], str) or item["fact"] not in ref.specs:
            raise ReviewError(f"{where}: {item['fact']!r} is not a fact key")
        if item["fact"] in seen_facts:
            raise ReviewError(f"{where}: {item['fact']} is asked more than once")
        seen_facts.add(item["fact"])

    flag_ids = [f["id"] for f in flags]
    checked = _list(decisions["flags_checked"], "decisions.flags_checked")
    for i, flag_id in enumerate(checked):
        if not isinstance(flag_id, str) or flag_id not in flag_ids:
            raise ReviewError(f"decisions.flags_checked[{i}]: {flag_id!r} is not a flag on this draft")
    if len(set(checked)) != len(checked):
        raise ReviewError("decisions.flags_checked names a flag more than once")

    if final:
        undecided = [rid for rid in on_draft if rid not in seen]
        undecided_custom = [i for i in range(len(customs)) if i not in seen_custom]
        unchecked = [f for f in flag_ids if f not in checked]
        missing = []
        if undecided:
            missing.append(f"a decision on {', '.join(undecided)}")
        if undecided_custom:
            missing.append(f"a decision on custom request {', '.join(map(str, undecided_custom))}")
        if unchecked:
            missing.append(f"a check on {', '.join(unchecked)}")
        if missing:
            raise ReviewError("approval needs " + "; ".join(missing))
    return json.loads(json.dumps(decisions))


def approved_list(draft: Mapping[str, Any], decisions: Mapping[str, Any], ref: Reference) -> list[dict[str, Any]]:
    """The kept and added documents in rule ID order, then the kept custom requests (rule_id null) in order."""
    counts = {d["rule_id"]: d["count"] for d in draft["documents"]}
    out = []
    for item in decisions["documents"]:
        if item["action"] == "keep":
            out.append((item["rule_id"], item.get("count", counts[item["rule_id"]])))
    for item in decisions["added"]:
        out.append((item["rule_id"], item.get("count", 1)))
    listed = [{"rule_id": rid, "document": ref.rules[rid].document, "count": n} for rid, n in sorted(out)]
    kept = sorted(i["index"] for i in decisions["custom_requests"] if i["action"] == "keep")
    listed += [{"rule_id": None, "document": draft["custom_requests"][i]["document"], "count": 1} for i in kept]
    return listed


def follow_up_questions(decisions: Mapping[str, Any], ref: Reference) -> list[dict[str, str]]:
    return [{"fact": f["fact"], "question": ref.specs[f["fact"]].plain_question_hint} for f in decisions["follow_ups"]]


def change_summary(draft: Mapping[str, Any], decisions: Mapping[str, Any]) -> dict[str, int]:
    counts = {d["rule_id"]: d["count"] for d in draft["documents"]}
    removed = sum(1 for d in decisions["documents"] if d["action"] == "remove")
    removed += sum(1 for c in decisions["custom_requests"] if c["action"] == "remove")
    changed = sum(
        1 for d in decisions["documents"] if d["action"] == "keep" and d.get("count", counts[d["rule_id"]]) != counts[d["rule_id"]]
    )
    return {"removed": removed, "added": len(decisions["added"]), "count_changed": changed, "follow_ups": len(decisions["follow_ups"])}


def checked_host(host: Any) -> dict[str, str] | None:
    """The host's name and version as the view read them from ui/initialize, or None when the view sent none."""
    if host is None:
        return None
    _exact(host, ("name", "version"), (), "host")
    name, version = host["name"], host["version"]
    if not isinstance(name, str) or not 0 < len(name) <= NAME_LIMIT or not isinstance(version, str) or len(version) > VERSION_LIMIT:
        raise ReviewError(f"host must give a name of 1 to {NAME_LIMIT} characters and a version of at most {VERSION_LIMIT}")
    return {"name": name, "version": version}


# Tokens.


@dataclass(frozen=True)
class OpenReview:
    draft_id: str
    token: str
    draft_sha256: str
    opened_at: datetime
    number: int = 1


class Tokens:
    """One current review token per draft, in memory only."""

    def __init__(self, clock: Callable[[], datetime] = utc_now, factory: Callable[[], str] | None = None) -> None:
        self.clock = clock
        self.factory = factory or (lambda: secrets.token_urlsafe(24))
        self._current: dict[str, OpenReview] = {}
        self._minted: dict[str, int] = {}

    def mint(self, draft_id: str, draft_sha256: str) -> OpenReview:
        """A new token for the draft, numbered so a screen can tell a newer opening from an older one."""
        self._minted[draft_id] = self._minted.get(draft_id, 0) + 1
        opened = OpenReview(draft_id, self.factory(), draft_sha256, self.clock(), self._minted[draft_id])
        self._current[draft_id] = opened
        return opened

    def current(self, draft_id: str, token: Any) -> OpenReview | None:
        opened = self._current.get(draft_id)
        if opened is None or not isinstance(token, str) or not hmac.compare_digest(opened.token.encode(), token.encode()):
            return None
        return opened


# The reviews folder.


class ReviewFolder:
    """Where review records live: regular files named {draft_id}.json, never in a truth folder."""

    LOCK_NAME = ".lock"
    OPENINGS_NAME = "openings.jsonl"

    def __init__(self, folder: str | Path) -> None:
        path = Path(folder).resolve()
        _refuse_truth(path, "reviews")
        path.mkdir(parents=True, exist_ok=True)
        self.folder = path.resolve()
        _refuse_truth(self.folder, "reviews")
        self._lock = threading.Lock()

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Holds this folder for one read, check and write: across threads of this server, and across server
        processes through an advisory lock on a file in the folder."""
        with self._lock:
            fd = os.open(self.folder / self.LOCK_NAME, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _path(self, draft_id: str) -> Path:
        if not isinstance(draft_id, str) or not DRAFT_ID.fullmatch(draft_id):
            raise ReviewError(f"draft_id must be d plus two digits, such as d07; got {draft_id!r}")
        return self.folder / f"{draft_id}.json"

    def read(self, draft_id: str) -> dict[str, Any] | None:
        path = self._path(draft_id)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ReviewError(f"the review record for {draft_id} is not a regular file in the reviews folder")
        try:
            with open(path, encoding="utf-8", opener=_read_no_follow) as f:
                record = json.load(f)
        except (OSError, ValueError):
            raise ReviewError(f"the review record for {draft_id} could not be read") from None
        if not isinstance(record, dict) or record.get("draft_id") != draft_id or record.get("status") not in (IN_REVIEW, APPROVED):
            raise ReviewError(f"the review record for {draft_id} is malformed")
        return record

    def write(self, record: Mapping[str, Any]) -> None:
        """Writes through a new, uniquely named file in the folder, then replaces the record in one step."""
        path = self._path(record["draft_id"])
        fd, tmp = tempfile.mkstemp(dir=self.folder, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(dump(dict(record)))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            if os.path.lexists(tmp):
                os.unlink(tmp)
            raise

    def ids(self) -> list[str]:
        return sorted(p.stem for p in self.folder.glob("*.json") if DRAFT_ID.fullmatch(p.stem))

    def log_opening(
        self, draft_id: str, draft_sha256: str, at: datetime, client: dict[str, str] | None = None,
    ) -> None:
        """Appends one line per opening, saved or not, so scoring can check the prereg lock against the first look.
        The line names the client that asked, so an opening from a Claude Code session inside Claude Desktop can be
        told apart from the preparer's screen; any opening still counts for the lock."""
        line = json.dumps(
            {"draft_id": draft_id, "draft_sha256": draft_sha256, "opened_at": stamp(at), "client": client}
        ) + "\n"
        with self.locked():
            fd = os.open(self.folder / self.OPENINGS_NAME, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
            try:
                os.write(fd, line.encode("ascii"))
            finally:
                os.close(fd)

    def openings(self) -> list[dict[str, Any]]:
        path = self.folder / self.OPENINGS_NAME
        if not path.exists():
            return []
        with open(path, encoding="utf-8", opener=_read_no_follow) as f:
            return [json.loads(line) for line in f if line.strip()]


def status_of(record: Mapping[str, Any] | None) -> str:
    return PENDING if record is None else record["status"]


def build_record(
    *,
    draft: Mapping[str, Any],
    decisions: Mapping[str, Any],
    opened: OpenReview,
    existing: Mapping[str, Any] | None,
    now: datetime,
    approve: bool,
    host: dict[str, str] | None,
    client: dict[str, str] | None,
    ref: Reference,
) -> dict[str, Any]:
    screen = [dict(s) for s in (existing or {}).get("screen_time", [])]
    opened_at = stamp(opened.opened_at)
    for entry in screen:
        if entry["opened_at"] == opened_at:
            entry["last_at"] = stamp(now)
            break
    else:
        screen.append({"opened_at": opened_at, "last_at": stamp(now)})
    seconds = sum(max(0, int((parse_stamp(s["last_at"]) - parse_stamp(s["opened_at"])).total_seconds())) for s in screen)
    saved = list((existing or {}).get("saved_at", []))
    if not approve:
        saved.append(stamp(now))
    return {
        "draft_id": draft["draft_id"],
        "draft_sha256": draft["draft_sha256"],
        "status": APPROVED if approve else IN_REVIEW,
        "decisions": dict(decisions),
        "approved_list": approved_list(draft, decisions, ref) if approve else None,
        "follow_up_questions": follow_up_questions(decisions, ref) if approve else None,
        "changes": change_summary(draft, decisions) if approve else None,
        "opened_at": (existing or {}).get("opened_at") or screen[0]["opened_at"],
        "saved_at": saved,
        "approved_at": stamp(now) if approve else None,
        "seconds_on_screen": seconds,
        "screen_time": screen,
        "host": host if host is not None else (existing or {}).get("host"),
        "client": client,
        "app_version": APP_VERSION,
    }
