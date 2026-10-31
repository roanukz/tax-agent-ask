"""Offline stand-ins for the async Anthropic client, for dry runs and tests.

Each fake has the one method the simulator calls, await
client.messages.create(**kwargs), and returns an object shaped like a
Messages API response: .content[0].text, .usage with the API's token names,
and .stop_reason. Nothing here opens a socket or spends anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from rules.engine import FactSpec, coerce

CHARS_PER_TOKEN = 4


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeResponse:
    content: list[Any]
    usage: FakeUsage = field(default_factory=FakeUsage)
    stop_reason: str = "end_turn"


def text_response(text: str, usage: FakeUsage | None = None, stop_reason: str = "end_turn") -> FakeResponse:
    return FakeResponse(content=[FakeTextBlock(text)], usage=usage or FakeUsage(), stop_reason=stop_reason)


class _Messages:
    def __init__(self, handler: Callable[[dict[str, Any]], Awaitable[FakeResponse]]) -> None:
        self._handler = handler

    async def create(self, **kwargs: Any) -> FakeResponse:
        return await self._handler(kwargs)


Reply = str | FakeResponse | Callable[[dict[str, Any]], "str | FakeResponse"]


class ScriptedClient:
    """Replies from a queue, in order, and records every request's kwargs."""

    def __init__(self, replies: Iterable[Reply], usage: FakeUsage | None = None) -> None:
        self._replies = list(replies)
        self._usage = usage or FakeUsage(input_tokens=100, output_tokens=20)
        self.calls: list[dict[str, Any]] = []
        self.messages = _Messages(self._create)

    async def _create(self, kwargs: dict[str, Any]) -> FakeResponse:
        self.calls.append(kwargs)
        if not self._replies:
            raise AssertionError("ScriptedClient ran out of replies")
        reply = self._replies.pop(0)
        if callable(reply):
            reply = reply(kwargs)
        if isinstance(reply, FakeResponse):
            return reply
        return text_response(reply, FakeUsage(**vars(self._usage)))


def _tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _say(spec: FactSpec, value: Any) -> str:
    if spec.type == "bool":
        return "yes" if value else "no"
    return str(value).replace("_", " ")


class TruthEchoClient:
    """Answers scripted questions straight from one household's truth.

    It reveals the true value of every fact the question asks about (skipping
    follow-ups that don't apply) and nothing else, so a dry run of the
    fidelity check should match 100% with no leaks. The answer text avoids
    fact key names and form numbers. Usage is estimated from text length, with
    the system prompt written to the cache on the first call and read after.
    """

    def __init__(
        self,
        true_facts: Mapping[str, Any],
        question_keys: Mapping[str, Sequence[str]],
        specs: Mapping[str, FactSpec],
    ) -> None:
        self._facts = dict(true_facts)
        self._question_keys = {text: tuple(keys) for text, keys in question_keys.items()}
        self._specs = specs
        self._cached = False
        self.calls: list[dict[str, Any]] = []
        self.messages = _Messages(self._create)

    def reply_for(self, question: str) -> str:
        revealed: dict[str, Any] = {}
        for key in self._question_keys.get(question, ()):
            value = self._facts.get(key)
            if value is not None:
                revealed[key] = coerce(self._specs[key], value)
        if revealed:
            words = ", ".join(_say(self._specs[k], v) for k, v in revealed.items())
            answer = f"Dry run answer: {words}."
        else:
            answer = "Dry run answer: I'm not sure."
        return json.dumps({"answer": answer, "revealed": revealed})

    async def _create(self, kwargs: dict[str, Any]) -> FakeResponse:
        self.calls.append(kwargs)
        question = kwargs["messages"][-1]["content"]
        reply = self.reply_for(question)
        system_tokens = sum(_tokens(block["text"]) for block in kwargs.get("system", []))
        history_tokens = sum(_tokens(m["content"]) for m in kwargs["messages"])
        usage = FakeUsage(input_tokens=history_tokens, output_tokens=_tokens(reply))
        if self._cached:
            usage.cache_read_input_tokens = system_tokens
        else:
            usage.cache_creation_input_tokens = system_tokens
            self._cached = True
        return text_response(reply, usage)
