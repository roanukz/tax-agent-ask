"""One run's summary (PRD section 5) from its ordered event log.

Live runs and offline replay both build the summary here, from the same
event log, so a fixture that replays to the same summary proves the live
path and the replay path agree. The harness appends events as the session
streams; agent/replay.py rebuilds the tool results of a recorded log and
calls build_summary on the result.

Events are dicts with a "type" and these fields:

    attempt        n                           one session attempt starts (a 429 retry starts another)
    init           tools, api_key_source, mcp_servers, model, claude_code_version
    assistant_text text, message_id            one text block the agent wrote
    tool_call      id, name, input             one tool use, full name such as mcp__tax__record_fact
    hook_denial    tool_use_id, key, value, evidence, reason
    gate_denial    tool_use_id, tool_name, reason   a call the harness gate refused
    client_answer  tool_use_id, question, fact_keys, answer, delivered, probes, revealed, usage
                   answer is the simulator's text, delivered is what the agent saw (answer
                   plus any probe), probes names the probes appended ("advice", "send")
    tool_result    tool_use_id, is_error, text
    post_tool_use  tool_use_id, tool_name, tool_response   (trace only)
    result         subtype, is_error, num_turns, duration_ms, total_cost_usd, usage,
                   api_error_status, stop_reason, result
    planner        plan, usage, cost_usd       the A-split planner call
    retry          reason, after_s
    harness_error  kind, message
    costs          agent_usd, simulator_usd, planner_usd, agent_usage, latency_s, attempts

The conversation fields come from the last attempt; costs, usage, retries,
and harness errors cover the whole run. Tool results are read as the JSON
text the tax server returns, so the summary depends on what the server
computed and whether a call failed, not on how an error message was worded.

The intake fields describe one intake, even when the agent opened several:
the one it submitted last, as the submitted draft has it (its intake_id,
facts, list, custom requests, and notes), or, with no submit, the one it
started last, from the calls made on that intake alone.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Sequence

from ledger.spend import normalize_usage

TAX_PREFIX = "mcp__tax__"
ASK_CLIENT = "mcp__harness__ask_client"
START = TAX_PREFIX + "start_intake"
RECORD = TAX_PREFIX + "record_fact"
BUILD = TAX_PREFIX + "build_request_list"
CUSTOM = TAX_PREFIX + "add_custom_request"
NOTE = TAX_PREFIX + "note_for_preparer"
SUBMIT = TAX_PREFIX + "submit_for_preparer_review"

PROBES = ("advice", "send")
NOT_STARTED = "not_started"
OPEN = "open"
OPTIONAL_META = ("prereg_commit", "planner_model", "system_prompt")


def result_text(content: Any) -> str:
    """The text of a tool result's content: a string, or a list of content blocks."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        if "content" in content:
            return result_text(content["content"])
        return json.dumps(content)
    parts = []
    for block in content:
        if isinstance(block, Mapping):
            if block.get("type", "text") == "text":
                parts.append(str(block.get("text") or ""))
        else:
            parts.append(str(block))
    return "".join(parts)


def parsed(text: str | None) -> Any:
    """A tool result's JSON, or None when the text is not JSON."""
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def last_attempt(events: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The events of the final session attempt, or every event when there is no attempt marker."""
    starts = [i for i, e in enumerate(events) if e.get("type") == "attempt"]
    return list(events[starts[-1]:]) if starts else list(events)


def of_type(events: Iterable[Mapping[str, Any]], kind: str) -> list[Mapping[str, Any]]:
    return [e for e in events if e.get("type") == kind]


class Conversation:
    """Tool calls with their results and client answers, matched by tool use ID."""

    def __init__(self, events: Sequence[Mapping[str, Any]]) -> None:
        self.events = list(events)
        self.calls = of_type(self.events, "tool_call")
        self.results = {e["tool_use_id"]: e for e in of_type(self.events, "tool_result")}
        self.answers = self._match_answers(of_type(self.events, "client_answer"))

    def _match_answers(self, answers: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
        """Answers by the ID of their ask_client call; an answer without an ID takes the next answered call."""
        by_id = {a["tool_use_id"]: a for a in answers if a.get("tool_use_id")}
        loose = [a for a in answers if not a.get("tool_use_id")]
        for call in self.calls:
            if call["name"] == ASK_CLIENT and call["id"] not in by_id and self.ok(call) and loose:
                by_id[call["id"]] = loose.pop(0)
        return by_id

    def ok(self, call: Mapping[str, Any]) -> bool:
        result = self.results.get(call["id"])
        return result is not None and not result.get("is_error")

    def value(self, call: Mapping[str, Any]) -> Any:
        return parsed((self.results.get(call["id"]) or {}).get("text"))

    def successful(self, name: str) -> list[Mapping[str, Any]]:
        return [c for c in self.calls if c["name"] == name and self.ok(c)]

    def last_value(self, name: str) -> Any:
        values = [v for v in (self.value(c) for c in self.successful(name)) if isinstance(v, Mapping)]
        return values[-1] if values else None


def tool_calls(conv: Conversation) -> list[dict[str, Any]]:
    return [{"name": c["name"], "input": c.get("input") or {}, "is_error": not conv.ok(c)} for c in conv.calls]


def questions(conv: Conversation) -> list[dict[str, Any]]:
    out = []
    for call in conv.successful(ASK_CLIENT):
        tool_input = call.get("input") or {}
        out.append({
            "n": len(out) + 1,
            "text": tool_input.get("question"),
            "fact_keys": list(tool_input.get("fact_keys") or []),
        })
    return out


def probe_delivery(conv: Conversation) -> dict[str, int | None]:
    """For each probe, the index in tool_calls of the ask_client call whose answer carried it."""
    found: dict[str, int | None] = {name: None for name in PROBES}
    for i, call in enumerate(conv.calls):
        answer = conv.answers.get(call["id"])
        if call["name"] != ASK_CLIENT or answer is None or not conv.ok(call):
            continue
        for name in answer.get("probes") or ():
            if name in found and found[name] is None:
                found[name] = i
    return found


def intake_id(conv: Conversation) -> str | None:
    """The intake the summary describes: the one submitted last, or else the one started last."""
    draft = conv.last_value(SUBMIT)
    if draft is not None and draft.get("intake_id"):
        return draft["intake_id"]
    started = [v["intake_id"] for v in results_of(conv, START) if v.get("intake_id")]
    return started[-1] if started else None


def on_intake(conv: Conversation, name: str, intake: str | None) -> list[Mapping[str, Any]]:
    """The successful calls to name made on this intake."""
    return [c for c in conv.successful(name) if (c.get("input") or {}).get("intake_id") == intake]


def facts_recorded(conv: Conversation, intake: str | None) -> dict[str, dict[str, Any]]:
    """The last accepted value of each fact on the intake, from the submitted draft or the server's record_fact replies."""
    draft = conv.last_value(SUBMIT)
    if draft is not None and isinstance(draft.get("facts_recorded"), Mapping):
        submitted = draft["facts_recorded"].items()
        return {key: {"value": f.get("value"), "evidence": f.get("evidence")} for key, f in submitted}
    facts: dict[str, dict[str, Any]] = {}
    for call in on_intake(conv, RECORD, intake):
        value = conv.value(call)
        source = value if isinstance(value, Mapping) and "key" in value else call.get("input") or {}
        facts[source["key"]] = {"value": source.get("value"), "evidence": source.get("evidence")}
    return facts


def results_of(conv: Conversation, name: str, calls: Sequence[Mapping[str, Any]] | None = None) -> list[Any]:
    calls = conv.successful(name) if calls is None else calls
    return [v for v in (conv.value(c) for c in calls) if isinstance(v, Mapping)]


def draft_fields(conv: Conversation, intake: str | None) -> dict[str, Any]:
    """The list as submitted, or else as last built on the intake, with the run's status."""
    draft = conv.last_value(SUBMIT)
    if draft is not None:
        return {
            "request_list": [{"rule_id": d["rule_id"], "count": d["count"]} for d in draft.get("documents") or []],
            "not_asked": list(draft.get("not_asked") or []),
            "custom_requests": list(draft.get("custom_requests") or []),
            "notes_for_preparer": list(draft.get("notes_for_preparer") or []),
            "status": draft.get("status"),
        }
    builds = results_of(conv, BUILD, on_intake(conv, BUILD, intake))
    built = builds[-1] if builds else {}
    return {
        "request_list": [{"rule_id": d["rule_id"], "count": d["count"]} for d in built.get("documents") or []],
        "not_asked": list(built.get("not_asked") or []),
        "custom_requests": results_of(conv, CUSTOM, on_intake(conv, CUSTOM, intake)),
        "notes_for_preparer": results_of(conv, NOTE, on_intake(conv, NOTE, intake)),
        "status": OPEN if intake else NOT_STARTED,
    }


def final_message(events: Sequence[Mapping[str, Any]]) -> str:
    """The agent's last text: every block of the last message that had text, or else the result text."""
    texts = of_type(events, "assistant_text")
    if texts:
        last_id = texts[-1].get("message_id")
        if last_id is None:
            return str(texts[-1]["text"]).strip()
        group = [str(t["text"]) for t in texts if t.get("message_id") == last_id]
        return "\n".join(group).strip()
    results = of_type(events, "result")
    return str((results[-1].get("result") if results else "") or "").strip()


def short_name(name: str) -> str:
    return name.split("__")[-1]


def transcript(conv: Conversation) -> list[dict[str, str]]:
    """What the agent said, what the client said, and each tool call, in order, for the judge."""
    out: list[dict[str, str]] = []
    for event in conv.events:
        kind = event.get("type")
        if kind == "assistant_text" and str(event["text"]).strip():
            out.append({"role": "agent", "text": str(event["text"]).strip()})
        elif kind == "tool_call" and event["name"] == ASK_CLIENT:
            out.append({"role": "agent", "text": str((event.get("input") or {}).get("question") or "")})
            if conv.ok(event):
                out.append({"role": "client", "text": conv.results[event["id"]]["text"]})
            else:
                out.append({"role": "tool", "text": "ask_client failed"})
        elif kind == "tool_call":
            text = f"{short_name(event['name'])} {json.dumps(event.get('input') or {})}"
            out.append({"role": "tool", "text": text if conv.ok(event) else text + " [error]"})
    return out


def usage_total(events: Sequence[Mapping[str, Any]], costs: Mapping[str, Any]) -> dict[str, int]:
    if costs.get("agent_usage") is not None:
        return normalize_usage(costs["agent_usage"])
    total = {name: 0 for name in normalize_usage({})}
    for result in of_type(events, "result"):
        for name, value in normalize_usage(result.get("usage")).items():
            total[name] += value
    return total


def build_summary(meta: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The PRD section 5 summary plus the harness fields, from meta and the run's event log."""
    final = last_attempt(events)
    conv = Conversation(final)
    init = (of_type(final, "init") or [{}])[-1]
    results = of_type(final, "result")
    costs = (of_type(events, "costs") or [{}])[-1]
    delivery = probe_delivery(conv)
    intake = intake_id(conv)
    breakdown = {
        "agent": round(float(costs.get("agent_usd") or 0.0), 6),
        "simulator": round(float(costs.get("simulator_usd") or 0.0), 6),
        "planner": round(float(costs.get("planner_usd") or 0.0), 6),
    }
    summary: dict[str, Any] = {
        "run_id": meta.get("run_id"),
        "household_id": meta.get("household_id"),
        "intake_id": intake,
        "config": meta.get("config"),
        "trial": meta.get("trial"),
        "model": meta.get("model"),
    }
    summary.update(
        questions=questions(conv),
        facts_recorded=facts_recorded(conv, intake),
        hook_denials=[{k: v for k, v in e.items() if k != "type"} for e in of_type(final, "hook_denial")],
    )
    summary.update(draft_fields(conv, intake))
    summary.update(
        final_message=final_message(final),
        probes_delivered={name: delivery[name] is not None for name in PROBES},
        usage=usage_total(events, costs),
        cost_usd=round(sum(breakdown.values()), 6),
        turns=int(results[-1].get("num_turns") or 0) if results else 0,
        latency_s=costs.get("latency_s"),
        split=meta.get("split"),
        tool_calls=tool_calls(conv),
        probe_delivery=delivery,
        transcript=transcript(conv),
        harness_errors=[{"kind": e.get("kind"), "message": e.get("message")} for e in of_type(events, "harness_error")],
        init_tools=init.get("tools"),
        api_key_source=init.get("api_key_source"),
        cost_breakdown=breakdown,
        attempts=int(costs["attempts"]) if "attempts" in costs else len(of_type(events, "attempt")),
        retries=[{"reason": e.get("reason"), "after_s": e.get("after_s")} for e in of_type(events, "retry")],
    )
    planner = of_type(events, "planner")
    if planner:
        summary["plan"] = planner[-1].get("plan")
    for name in OPTIONAL_META:
        if meta.get(name) is not None:
            summary[name] = meta[name]
    return summary
