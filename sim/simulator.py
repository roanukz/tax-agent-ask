"""Client simulator: Haiku plays one household's client through the Messages API.

The simulator takes data, not paths. The caller loads one household's truth
file and passes the dict; the simulator keeps a private copy of that
household's true facts and persona traits and nothing else (no probes, no
changed facts, no split). It never opens a file or builds a path while
answering, so it can't read another household, a profile, or anything else on
disk. The only file it can touch is the public fact key list in
rules/facts.yaml, and only at construction when the caller passes no specs.

Each question is a user turn and each reply an assistant turn, so later
answers stay consistent with earlier ones. The model replies in JSON with its
answer and the facts that answer revealed. Only the answer text is meant for
the agent under test; the revealed facts are checked against facts.yaml and
kept for scoring and for baseline B1.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from ledger.spend import normalize_usage
from rules.engine import FactSpec, coerce, load_facts

DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_MAX_TOKENS = 1024
# Answering from a fact sheet is a simple task, so the simulator thinks little.
DEFAULT_EFFORT = "low"

# PRD section 6.4, word for word. Filled by substitution, not str.format,
# because the JSON example has its own braces.
SYSTEM_PROMPT_TEMPLATE = """You are playing a tax client in a software test. The facts below are true for you this year. Stay in character and never mention the test.

Rules:
- Answer only what the question asks, truthfully, in one to three short sentences of everyday language.
- Do not mention facts the question didn't ask about, unless volunteers_info is true.
- If jargon_tolerance is low and the question uses a form number or tax term, say you aren't sure what that means and describe your situation instead.
- Apply your recall_gap if you have one.

Respond only with JSON: {"answer": "<what you say>", "revealed": {"<fact_key>": <value>}}, where revealed lists every fact your answer disclosed.

Your facts: {true_facts_json}
Your persona: {persona_json}"""

PLACEHOLDER = re.compile(r"\{(true_facts_json|persona_json)\}")

# Persona fields the model plays. Anything else in the persona (such as the
# generator's label) is internal and stays out of the prompt.
PERSONA_TRAITS = ("communication_style", "jargon_tolerance", "volunteers_info", "recall_gap")

CORRECTION = (
    "Your last reply could not be used: {reason}. Reply again with only the JSON object, "
    'in the form {{"answer": "<what you say>", "revealed": {{"<fact_key>": <value>}}}}, '
    "with nothing before or after it."
)

FENCE = re.compile(r"^```[A-Za-z0-9_-]*[ \t]*\n?(.*?)\n?[ \t]*```$", re.DOTALL)


class SimulatorError(RuntimeError):
    """The model gave an unusable reply twice in a row."""

    def __init__(self, message: str, *, reasons: list[str], raw_texts: list[str], usage: Usage) -> None:
        super().__init__(message)
        self.reasons = reasons
        self.raw_texts = raw_texts
        self.usage = usage


class ReplyError(ValueError):
    """One reply that can't be used; the simulator retries once."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )

    @property
    def prompt_tokens(self) -> int:
        """Prompt size of one call, counting cache reads and writes."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }

    @classmethod
    def from_api(cls, usage: Any) -> Usage:
        """Messages API usage (an object or a dict) in the ledger's names."""
        if usage is None:
            return cls()
        if not isinstance(usage, Mapping):
            names = (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            )
            usage = {name: getattr(usage, name, None) for name in names}
        return cls(**normalize_usage(dict(usage)))


@dataclass(frozen=True)
class SimAnswer:
    """One answer. Only `answer` may reach the agent under test."""

    answer: str
    revealed: dict[str, Any]
    usage: Usage
    raw_text: str
    retried: bool
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    raw_revealed: Any = None
    retry_reason: str | None = None


def prompt_facts(true_facts: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> dict[str, Any]:
    """True facts in facts.yaml order, coerced, without null values.

    A null is a follow-up whose parent is no, so it says nothing to the model.
    """
    unknown = sorted(set(true_facts) - set(specs))
    if unknown:
        raise ValueError(f"truth has unknown fact keys: {unknown}")
    out: dict[str, Any] = {}
    for key, spec in specs.items():
        value = true_facts.get(key)
        if value is None:
            continue
        try:
            out[key] = coerce(spec, value)
        except ValueError as e:
            raise ValueError(f"truth value is invalid: {e}") from e
    return out


def prompt_persona(persona: Mapping[str, Any]) -> dict[str, Any]:
    return {trait: persona[trait] for trait in PERSONA_TRAITS if trait in persona}


def build_system_prompt(truth: Mapping[str, Any], specs: Mapping[str, FactSpec]) -> str:
    values = {
        "true_facts_json": json.dumps(prompt_facts(truth["true_facts"], specs)),
        "persona_json": json.dumps(prompt_persona(truth.get("persona") or {})),
    }
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], SYSTEM_PROMPT_TEMPLATE)


def reply_text(response: Any) -> str:
    """The text blocks of a response, skipping thinking blocks."""
    parts = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", "text") == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts)


def _strip_fence(text: str) -> str:
    body = text.strip()
    match = FENCE.match(body)
    return match.group(1).strip() if match else body


def parse_reply(text: str) -> dict[str, Any]:
    """The reply's JSON object, from bare JSON, a fenced block, or JSON inside prose."""
    body = _strip_fence(text)
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        start = body.find("{")
        if start < 0:
            raise ReplyError("the reply is not valid JSON") from None
        try:
            obj, _ = json.JSONDecoder().raw_decode(body[start:])
        except json.JSONDecodeError:
            raise ReplyError("the reply is not valid JSON") from None
    if not isinstance(obj, dict):
        raise ReplyError("the reply is not a JSON object")
    answer = obj.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise ReplyError('the reply has no "answer" text')
    if "{" in answer or "}" in answer:
        raise ReplyError("the answer text contains JSON; put facts only in revealed")
    return obj


def check_revealed(raw: Any, specs: Mapping[str, FactSpec]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Valid revealed facts, coerced, plus anomalies for everything else.

    A null value is kept as null: it means not applicable, which matches a
    follow-up whose parent is no.
    """
    if raw is None:
        return {}, [{"kind": "missing_revealed"}]
    if not isinstance(raw, dict):
        return {}, [{"kind": "revealed_not_object", "value": raw}]
    revealed: dict[str, Any] = {}
    anomalies: list[dict[str, Any]] = []
    for key, value in raw.items():
        spec = specs.get(key)
        if spec is None:
            anomalies.append({"kind": "unknown_key", "key": key, "value": value})
            continue
        if value is None:
            revealed[key] = None
            continue
        try:
            revealed[key] = coerce(spec, value)
        except ValueError as e:
            # FactError is a ValueError; coerce can also raise a bare one, as for "--3".
            anomalies.append({"kind": "uncoercible", "key": key, "value": value, "error": str(e)})
    return revealed, anomalies


class ClientSimulator:
    """One household's client for one conversation."""

    def __init__(
        self,
        truth: Mapping[str, Any],
        client: Any,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str | None = DEFAULT_EFFORT,
        specs: Mapping[str, FactSpec] | None = None,
    ) -> None:
        if not isinstance(truth, Mapping):
            raise TypeError("ClientSimulator takes a loaded truth dict, not a path or a string")
        if not isinstance(truth.get("true_facts"), Mapping):
            raise ValueError("truth needs a true_facts object")
        self._specs = dict(specs) if specs is not None else load_facts()
        persona = truth.get("persona") or {}
        if not isinstance(persona, Mapping):
            raise ValueError("truth persona must be an object")
        kept = {"true_facts": copy.deepcopy(dict(truth["true_facts"])), "persona": copy.deepcopy(dict(persona))}
        self.household_id = str(truth.get("household_id", ""))
        self._system = build_system_prompt(kept, self._specs)
        self._client = client
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self._history: list[dict[str, str]] = []
        self._total = Usage()
        self._max_prompt_tokens = 0
        self._calls = 0
        self._lock = asyncio.Lock()

    @property
    def system_prompt(self) -> str:
        return self._system

    @property
    def history(self) -> list[dict[str, str]]:
        return [dict(turn) for turn in self._history]

    @property
    def total_usage(self) -> Usage:
        """Every call in this conversation, including failed attempts."""
        return self._total

    @property
    def max_prompt_tokens(self) -> int:
        return self._max_prompt_tokens

    @property
    def calls(self) -> int:
        return self._calls

    def _request(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": self._system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
        }
        if self.effort is not None:
            kwargs["output_config"] = {"effort": self.effort}
        return kwargs

    async def _call(self, messages: list[dict[str, str]]) -> tuple[str, Usage, Any]:
        response = await self._client.messages.create(**self._request(messages))
        usage = Usage.from_api(getattr(response, "usage", None))
        self._calls += 1
        self._total = self._total + usage
        self._max_prompt_tokens = max(self._max_prompt_tokens, usage.prompt_tokens)
        return reply_text(response), usage, getattr(response, "stop_reason", None)

    def _read(self, text: str, stop_reason: Any) -> dict[str, Any]:
        if stop_reason == "max_tokens":
            raise ReplyError("the reply was cut off at max_tokens")
        if stop_reason == "refusal":
            raise ReplyError("the model declined to answer")
        return parse_reply(text)

    async def answer(self, question: str) -> SimAnswer:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("a question must be non-empty text")
        async with self._lock:
            return await self._answer(question)

    async def _answer(self, question: str) -> SimAnswer:
        asked = {"role": "user", "content": question}
        messages = [*self._history, asked]
        text, usage, stop = await self._call(messages)
        reasons: list[str] = []
        texts = [text]
        try:
            obj = self._read(text, stop)
        except ReplyError as first:
            reasons.append(str(first))
            retry = [*messages]
            if text.strip():
                retry.append({"role": "assistant", "content": text})
            retry.append({"role": "user", "content": CORRECTION.format(reason=first)})
            text, second_usage, stop = await self._call(retry)
            usage = usage + second_usage
            texts.append(text)
            try:
                obj = self._read(text, stop)
            except ReplyError as second:
                reasons.append(str(second))
                raise SimulatorError(
                    f"{self.household_id}: unusable reply twice ({'; '.join(reasons)})",
                    reasons=reasons,
                    raw_texts=texts,
                    usage=usage,
                ) from second

        raw_revealed = obj.get("revealed")
        revealed, anomalies = check_revealed(raw_revealed, self._specs)
        answer = obj["answer"].strip()
        # The assistant turn is the clean JSON, so later turns see the format kept.
        clean = json.dumps({"answer": answer, "revealed": raw_revealed if isinstance(raw_revealed, dict) else {}})
        self._history.extend([asked, {"role": "assistant", "content": clean}])
        return SimAnswer(
            answer=answer,
            revealed=revealed,
            usage=usage,
            raw_text=text,
            retried=bool(reasons),
            anomalies=anomalies,
            raw_revealed=raw_revealed,
            retry_reason=reasons[0] if reasons else None,
        )
