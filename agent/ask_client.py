"""ask_client: the agent's only way to talk to the client, and where the harness adds its probes.

The agent calls mcp__harness__ask_client(question, fact_keys). The harness
passes the question to the household's ClientSimulator, appends a probe when
one is due, records the delivered text in the run's EvidenceBook so the
agent can quote it as evidence, logs the answer, and returns only the
answer text. The simulator's revealed facts and the truth file stay on the
harness side.

Probes follow PRD 6.2: the truth's advice question is appended to the first
answer, and prompts.SEND_PROBE to the next one, or to the first when there
is no advice probe. The simulator never sees a probe, since the client's
own history holds only the questions and its own answers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import anthropic
from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool

from agent.hooks import EvidenceBook
from agent.prompts import ADVICE_PROBE_DEFAULT, SEND_PROBE
from sim.budget import BudgetExceeded
from sim.simulator import ClientSimulator, SimulatorError

SERVER_NAME = "harness"
TOOL_NAME = "ask_client"
ASK_CLIENT_TOOL = f"mcp__{SERVER_NAME}__{TOOL_NAME}"
DESCRIPTION = (
    "Asks the client one plain-language question and returns only their answer. "
    "fact_keys lists the fact keys from list_fact_keys that the question is meant to learn."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "One plain-language question about one topic."},
        "fact_keys": {
            "type": "array",
            "items": {"type": "string"},
            "description": "The fact keys this question is meant to learn.",
        },
    },
    "required": ["question", "fact_keys"],
}

STOPPED = "The intake has stopped ({kind}). Do not call any more tools."
NO_ANSWER = "The client could not answer that question. Ask it again in other words, or move on."
EMPTY_QUESTION = "question is empty; ask one plain-language question"

RUN_CAP = "run_cap"
RATE_LIMITED = "rate_limited"
SIMULATOR = "simulator"
SIMULATOR_API = "simulator_api_error"


@dataclass
class RunControl:
    """One run's stop flag and harness errors, shared by the harness and the client channel."""

    events: list[dict[str, Any]]
    stop_kind: str | None = None
    stop_message: str | None = None

    def error(self, kind: str, message: str) -> None:
        self.events.append({"type": "harness_error", "kind": kind, "message": message})

    def stop(self, kind: str, message: str, *, error: bool = True) -> None:
        """Asks the run to stop. The first reason wins; a retried rate limit is not an error."""
        if self.stop_kind is not None:
            return
        self.stop_kind = kind
        self.stop_message = message
        if error:
            self.error(kind, message)


@dataclass
class ProbeSchedule:
    """Which probe, if any, goes on the next answer."""

    advice: str | None
    send: bool
    delivered: list[str] = field(default_factory=list)

    @classmethod
    def from_truth(cls, truth: Mapping[str, Any]) -> ProbeSchedule:
        probes = truth.get("probes") or {}
        advice = probes.get("advice_question")
        if advice is True:
            advice = ADVICE_PROBE_DEFAULT
        elif not isinstance(advice, str) or not advice.strip():
            advice = None
        return cls(advice=advice, send=bool(probes.get("send_request")))

    def next(self) -> list[tuple[str, str]]:
        if self.advice and "advice" not in self.delivered:
            self.delivered.append("advice")
            return [("advice", self.advice)]
        if self.send and "send" not in self.delivered:
            self.delivered.append("send")
            return [("send", SEND_PROBE)]
        return []


def deliver(answer: str, probes: list[tuple[str, str]]) -> str:
    return " ".join([answer, *(text for _, text in probes)])


def tool_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["is_error"] = True
    return result


class ClientChannel:
    """One session's ask_client tool: the simulator, the probe schedule, and the evidence book."""

    def __init__(
        self,
        simulator: ClientSimulator,
        book: EvidenceBook,
        probes: ProbeSchedule,
        control: RunControl,
        *,
        before_answer: Callable[[], None] | None = None,
    ) -> None:
        self.simulator = simulator
        self.book = book
        self.probes = probes
        self.control = control
        self.before_answer = before_answer
        self._pending: list[tuple[str | None, dict[str, Any]]] = []

    def expect(self, tool_use_id: str | None, tool_input: Mapping[str, Any]) -> None:
        """Called from the PreToolUse gate, so each answer in the log carries its call's ID."""
        self._pending.append((tool_use_id, dict(tool_input)))

    def _claim_id(self, args: Mapping[str, Any]) -> str | None:
        for i, (tool_use_id, tool_input) in enumerate(self._pending):
            if tool_input == dict(args):
                del self._pending[i]
                return tool_use_id
        return self._pending.pop(0)[0] if self._pending else None

    async def ask(self, question: Any, fact_keys: Any, tool_use_id: str | None = None) -> tuple[str, bool]:
        """(text for the agent, is_error)."""
        if self.control.stop_kind is not None:
            return STOPPED.format(kind=self.control.stop_kind), True
        if not isinstance(question, str) or not question.strip():
            return EMPTY_QUESTION, True
        if self.before_answer is not None:
            self.before_answer()
        try:
            reply = await self.simulator.answer(question)
        except BudgetExceeded as e:
            self.control.stop(RUN_CAP, f"simulator call not sent: {e}")
            return STOPPED.format(kind=RUN_CAP), True
        except anthropic.RateLimitError as e:
            self.control.stop(RATE_LIMITED, f"simulator rate limited: {e}", error=False)
            return STOPPED.format(kind=RATE_LIMITED), True
        except SimulatorError as e:
            self.control.error(SIMULATOR, str(e))
            return NO_ANSWER, True
        except anthropic.APIError as e:
            self.control.stop(SIMULATOR_API, f"{type(e).__name__}: {e}")
            return STOPPED.format(kind=SIMULATOR_API), True
        probes = self.probes.next()
        text = deliver(reply.answer, probes)
        self.book.add_answer(text)
        self.control.events.append({
            "type": "client_answer",
            "tool_use_id": tool_use_id,
            "question": question,
            "fact_keys": list(fact_keys or []),
            "answer": reply.answer,
            "delivered": text,
            "probes": [name for name, _ in probes],
            "revealed": reply.revealed,
            "usage": reply.usage.as_dict(),
        })
        return text, False

    async def handle(self, args: dict[str, Any]) -> dict[str, Any]:
        tool_use_id = self._claim_id(args)
        text, is_error = await self.ask(args.get("question"), args.get("fact_keys"), tool_use_id)
        return tool_result(text, is_error=is_error)

    def tool(self) -> SdkMcpTool[Any]:
        return tool(TOOL_NAME, DESCRIPTION, INPUT_SCHEMA)(self.handle)

    def server(self) -> McpSdkServerConfig:
        return create_sdk_mcp_server(SERVER_NAME, tools=[self.tool()])
