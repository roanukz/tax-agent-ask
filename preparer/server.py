"""The MCP server "preparer": the preparer's review screen as an MCP App, over stdio.

    uv run --directory <repo> python -m preparer.server --drafts data/drafts --reviews data/reviews

Five tools, listed in this fixed order, and one ui:// resource, the view:

    list_drafts     model        each draft's ID, client, document and flag counts, and status
    open_review     model, app   renders the view; text for the model, and for an MCP Apps
                                 client the draft, its flags and a fresh review token
    save_review     app only     stores the preparer's decisions so far
    approve_review  app only     approves the list; final
    approved_list   model        the approved list and follow-up questions as text

There is no tool that reaches a client. The approved list is stored in the reviews folder for
the preparer to pass on through their own system.

save_review and approve_review check, in this order, that the client negotiated MCP Apps, that
the review token is current for this draft, that the draft's SHA-256 matches the one the
token was minted for and the file on disk, that the decisions are valid, and that the draft
isn't already approved. Each refusal comes back as a plain message for the view to show, and
goes to stderr with the tool and draft. An MCP Apps host leaves the two app-only tools out of
the model's tool list; the capability check also refuses a text-only client, which lists
every tool. The token is returned only in structuredContent and only to an MCP Apps client,
because a text-only client such as Claude Code hands structuredContent to the model.

The server reads its drafts folder, the rules and fact keys, and the view's HTML, and writes
its reviews folder; both folders are refused if they are truth folders, by the same check the
tax server uses. It calls no model. On its first tool call it writes the client's name and
advertised extensions to stderr, never stdout, for the Claude Desktop check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Annotated, Any, Callable, Sequence

from mcp.server.apps import Apps, ResourceCsp, ResourcePermissions, client_supports_apps
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.tools.base import Tool
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from preparer.drafts import DraftError, DraftFolder, Reference, addable_rules, compute_flags, is_carried
from server.intake import _refuse_truth
from preparer.reviews import (
    APP_VERSION,
    APPROVED,
    REASONS,
    ReviewError,
    ReviewFolder,
    Tokens,
    build_record,
    check_decisions,
    checked_host,
    status_of,
    utc_now,
)

SERVER_NAME = "preparer"
VIEW_URI = "ui://preparer/review.html"
DIST_HTML = Path(__file__).resolve().parent / "app" / "dist" / "review.html"
VIEW_CLIENT_ORIGIN = "https://unpkg.com"
TOOL_NAMES = ("list_drafts", "open_review", "save_review", "approve_review", "approved_list")
NOT_APPS = (
    "Refused: saving or approving a review needs the review screen, in a client that supports MCP Apps "
    "(such as Claude Desktop). This client did not say it supports them."
)
STALE_TOKEN = (
    "Refused: this review screen is out of date, because the draft was opened again or the server restarted. "
    "Open the draft again to keep reviewing."
)
CHANGED = "Refused: the draft changed since this screen opened it. Open the draft again."
FINAL = "Refused: this draft is already approved, and approval is final."
OTHER_VERSION = "Refused: the saved review was for another version of this draft."

DraftId = Annotated[str, Field(description="The draft's ID, d plus two digits, such as d07; list_drafts gives them.")]
Token = Annotated[str, Field(description="The review token open_review gave the review screen.")]
Sha = Annotated[str, Field(description="The draft's SHA-256, as open_review gave it to the review screen.")]
Decisions = Annotated[dict[str, Any], Field(description=(
    "The preparer's decisions: documents, custom_requests, added, follow_ups and flags_checked."
))]
Host = Annotated[dict[str, str] | None, Field(description="The host's name and version from ui/initialize.")]

TOOLS = {
    "list_drafts": (
        "List drafts",
        "Lists the agent's draft document lists awaiting the preparer's review: each draft's ID, client, "
        "number of documents, number of flags to check, and review status.",
        {"readOnlyHint": True, "openWorldHint": False},
    ),
    "open_review": (
        "Open a draft for review",
        "Opens one draft on the preparer's review screen. Returns a readable summary of its documents, flags "
        "and notes. Only the preparer can approve, on the screen.",
        {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    ),
    "save_review": (
        "Save review",
        "Stores the preparer's decisions so far. Used by the review screen only.",
        {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    ),
    "approve_review": (
        "Approve list",
        "Approves the preparer's document list for a draft. Final. Used by the review screen only.",
        {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    ),
    "approved_list": (
        "Approved list",
        "Gives a draft's approved document list and follow-up questions as text, or says it is not approved yet. "
        "The list stays with the preparer.",
        {"readOnlyHint": True, "openWorldHint": False},
    ),
}


def text_result(text: str, structured: dict[str, Any] | None = None, *, error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=structured, is_error=error)


def client_identity(ctx: Context) -> dict[str, str] | None:
    try:
        info = ctx.request_context.session.client_params.client_info
    except (AttributeError, ValueError):
        return None
    if info is None:
        return None
    return {"name": str(info.name), "version": str(info.version)}


def client_extensions(ctx: Context) -> Any:
    try:
        caps = ctx.client_capabilities
    except (AttributeError, ValueError):
        return None
    return caps.extensions if caps else None


# Text the model and text-only clients read.


def _value(v: Any) -> str:
    return json.dumps(v)


def summary_text(draft: dict[str, Any], flags: list[dict[str, Any]], record: dict[str, Any] | None, apps: bool) -> str:
    lines = [
        f"Draft {draft['draft_id']} for {draft['client_name']}, tax year {draft['tax_year']}. "
        f"Status: {status_of(record)}.",
        "",
        f"Documents requested ({len(draft['documents'])}):",
    ]
    for i, doc in enumerate(draft["documents"], start=1):
        why = "; ".join(
            f"{t['fact']} = {_value(t['value'])} ("
            + ("carried from last year, not confirmed" if is_carried(t["evidence"]) else f"client said: \"{t['evidence']}\"")
            + ")"
            for t in doc["triggered_by"]
        )
        lines.append(f"{i}. {doc['rule_id']}: {doc['document']} (count {doc['count']}). Because {why or 'of the facts recorded'}.")
    if draft["custom_requests"]:
        lines += ["", f"Custom requests the agent added ({len(draft['custom_requests'])}):"]
        lines += [f"{i}. {r['document']} Reason: {r['reason']}" for i, r in enumerate(draft["custom_requests"], start=1)]
    if draft["no_document_needed"]:
        lines += ["", "No document needed, because the credit ended: " + ", ".join(e["rule_id"] for e in draft["no_document_needed"]) + "."]
    lines += ["", f"Flags to check ({len(flags)}):"]
    for f in flags:
        if f["kind"] == "dropped":
            lines.append(
                f"- {f['id']}: last year's file had {f['label']} (count {f['last_year_count']}); "
                f"this draft requests {f['this_year_count']}."
            )
        elif f["kind"] == "carried":
            lines.append(f"- {f['id']}: {f['fact']} = {_value(f['value'])}, carried from last year ({f['evidence']}), not confirmed this year.")
        elif f["kind"] == "custom":
            lines.append(f"- {f['id']}: custom request \"{f['document']}\".")
        elif f["kind"] == "client_question":
            lines.append(f"- {f['id']}: note for the preparer: \"{f['text']}\"")
        else:
            lines.append(f"- {f['id']}: {f['rule_id']}, no document needed.")
    if not flags:
        lines.append("- none")
    lines += ["", f"Facts never asked: {len(draft['not_asked'])}."]
    if record and record["status"] == APPROVED:
        lines += ["", f"Approved at {record['approved_at']}. approved_list gives the approved list."]
    if apps:
        lines += ["", "The preparer reviews and approves this draft on the review screen. Approval can't happen in chat."]
    else:
        lines += ["", "This client can't show the review screen, so this draft can be read here but not approved."]
    return "\n".join(lines)


def approved_text(draft: dict[str, Any], record: dict[str, Any]) -> str:
    lines = [
        f"Approved list for {draft['client_name']} ({draft['draft_id']}), approved {record['approved_at']}. "
        "It stays with the preparer, who passes it on through their own system.",
        "",
    ]
    for i, item in enumerate(record["approved_list"], start=1):
        tag = item["rule_id"] or "custom"
        lines.append(f"{i}. {item['document']} ({tag}, count {item['count']})")
    if record["follow_up_questions"]:
        lines += ["", "Follow-up questions for the client:"]
        lines += [f"- {q['question']} ({q['fact']})" for q in record["follow_up_questions"]]
    c = record["changes"]
    lines += ["", f"Changes from the draft: {c['removed']} removed, {c['added']} added, {c['count_changed']} count changed, "
              f"{c['follow_ups']} follow-up questions."]
    return "\n".join(lines)


def count_rules(ref: Reference) -> dict[str, dict[str, Any]]:
    """Each request rule with a count, the fact its count comes from, and the range a kept count may take."""
    out = {}
    for rule in ref.request_rules:
        if rule.count_from:
            spec = ref.specs[rule.count_from]
            out[rule.id] = {"fact": rule.count_from, "min": 1, "max": spec.max if spec.max is not None else 10}
    return out


def fact_keys(ref: Reference) -> dict[str, dict[str, Any]]:
    return {
        key: {"topic": s.topic, "type": s.type, "question": s.plain_question_hint, "min": s.min, "max": s.max}
        for key, s in ref.specs.items()
    }


def review_payload(
    draft: dict[str, Any], flags: list[dict[str, Any]], ref: Reference, record: dict[str, Any] | None, token: str,
    number: int = 1,
) -> dict[str, Any]:
    """What the view needs to show one draft: open_review's structuredContent, and each draft in the static demo."""
    return {
        "draft": draft,
        "flags": flags,
        "addable": addable_rules(draft, ref),
        "facts": fact_keys(ref),
        "count_rules": count_rules(ref),
        "reasons": list(REASONS),
        "status": status_of(record),
        "record": record,
        "review_token": token,
        "open_number": number,
        "draft_sha256": draft["draft_sha256"],
        "app_version": APP_VERSION,
    }


# The server.


def build_server(
    drafts_dir: str | Path,
    reviews_dir: str | Path,
    *,
    html: str | None = None,
    html_path: str | Path = DIST_HTML,
    clock: Callable[[], Any] = utc_now,
    token_factory: Callable[[], str] | None = None,
    log: Callable[[str], None] | None = None,
    ref: Reference | None = None,
) -> MCPServer:
    ref = ref or Reference.load()
    drafts = DraftFolder(drafts_dir, ref)
    reviews = ReviewFolder(reviews_dir)
    tokens = Tokens(clock, token_factory)
    if html is None:
        _refuse_truth(Path(html_path).resolve(), "view")
    view = html if html is not None else Path(html_path).read_text(encoding="utf-8")
    emit = log or (lambda line: print(line, file=sys.stderr, flush=True))
    seen_client = {"logged": False}

    def first_call(ctx: Context) -> None:
        if not seen_client["logged"]:
            seen_client["logged"] = True
            emit(f"preparer: client {json.dumps(client_identity(ctx))} advertised extensions {json.dumps(client_extensions(ctx))}")

    def refuse(tool: str, draft_id: Any, message: str) -> CallToolResult:
        emit(f"preparer: refused {tool} for {draft_id!r}: {message}")
        return text_result(message, error=True)

    def load(tool: str, draft_id: Any) -> dict[str, Any] | CallToolResult:
        try:
            return drafts.load(draft_id)
        except (DraftError, ReviewError) as e:
            return refuse(tool, draft_id, f"Refused: {e}")

    def record_for(draft: dict[str, Any]) -> dict[str, Any] | None:
        """The draft's review record, refused when it was saved for another version of the draft."""
        record = reviews.read(draft["draft_id"])
        if record is not None and record["draft_sha256"] != draft["draft_sha256"]:
            raise ReviewError("the saved review was for another version of this draft")
        return record

    def list_drafts(ctx: Context) -> CallToolResult:
        first_call(ctx)
        lines = []
        for draft_id in drafts.ids():
            try:
                draft = drafts.load(draft_id)
                status = status_of(record_for(draft))
            except (DraftError, ReviewError) as e:
                lines.append(f"{draft_id}: can't be read ({e})")
                continue
            flags = compute_flags(draft, ref)
            lines.append(
                f"{draft_id}: {draft['client_name']}, {len(draft['documents'])} documents, {len(flags)} flags, {status}"
            )
        return text_result("\n".join(lines) if lines else "No drafts in the drafts folder.")

    def open_review(draft_id: DraftId, ctx: Context) -> CallToolResult:
        first_call(ctx)
        draft = load("open_review", draft_id)
        if isinstance(draft, CallToolResult):
            return draft
        try:
            record = record_for(draft)
        except ReviewError as e:
            return refuse("open_review", draft_id, f"Refused: {e}")
        flags = compute_flags(draft, ref)
        apps = client_supports_apps(ctx)
        reviews.log_opening(draft_id, draft["draft_sha256"], clock())
        text = summary_text(draft, flags, record, apps)
        if not apps:
            return text_result(text)
        opened = tokens.mint(draft_id, draft["draft_sha256"])
        return text_result(text, review_payload(draft, flags, ref, record, opened.token, opened.number))

    def store(tool: str, draft_id: Any, token: Any, sha: Any, decisions: Any, host: Any, ctx: Context, approve: bool) -> CallToolResult:
        first_call(ctx)
        if not client_supports_apps(ctx):
            return refuse(tool, draft_id, NOT_APPS)
        opened = tokens.current(draft_id, token) if isinstance(draft_id, str) else None
        if opened is None:
            return refuse(tool, draft_id, STALE_TOKEN)
        draft = load(tool, draft_id)
        if isinstance(draft, CallToolResult):
            return draft
        if sha != opened.draft_sha256 or sha != draft["draft_sha256"]:
            return refuse(tool, draft_id, CHANGED)
        try:
            checked = check_decisions(decisions, draft, compute_flags(draft, ref), ref, final=approve)
            host_info = checked_host(host)
        except ReviewError as e:
            return refuse(tool, draft_id, f"Refused: {e}")
        # The approved check and the write are one step, so two calls at once can't both pass the check.
        with reviews.locked():
            try:
                existing = reviews.read(draft_id)
            except ReviewError as e:
                return refuse(tool, draft_id, f"Refused: {e}")
            if existing is not None and existing["status"] == APPROVED:
                return refuse(tool, draft_id, FINAL)
            if existing is not None and existing["draft_sha256"] != draft["draft_sha256"]:
                return refuse(tool, draft_id, OTHER_VERSION)
            record = build_record(
                draft=draft, decisions=checked, opened=opened, existing=existing, now=clock(), approve=approve,
                host=host_info, client=client_identity(ctx), ref=ref,
            )
            reviews.write(record)
        if approve:
            return text_result(approved_text(draft, record), {"record": record})
        return text_result(f"Saved the review of {draft_id} at {record['saved_at'][-1]}.", {"record": record})

    def save_review(
        draft_id: DraftId, review_token: Token, draft_sha256: Sha, decisions: Decisions, ctx: Context, host: Host = None,
    ) -> CallToolResult:
        return store("save_review", draft_id, review_token, draft_sha256, decisions, host, ctx, approve=False)

    def approve_review(
        draft_id: DraftId, review_token: Token, draft_sha256: Sha, decisions: Decisions, ctx: Context, host: Host = None,
    ) -> CallToolResult:
        return store("approve_review", draft_id, review_token, draft_sha256, decisions, host, ctx, approve=True)

    def approved_list(draft_id: DraftId, ctx: Context) -> CallToolResult:
        first_call(ctx)
        draft = load("approved_list", draft_id)
        if isinstance(draft, CallToolResult):
            return draft
        try:
            record = record_for(draft)
        except ReviewError as e:
            return refuse("approved_list", draft_id, f"Refused: {e}")
        if record is None or record["status"] != APPROVED:
            return text_result(f"Draft {draft_id} is not approved yet (status: {status_of(record)}).")
        return text_result(approved_text(draft, record))

    def tool_kwargs(name: str) -> dict[str, Any]:
        title, description, annotations = TOOLS[name]
        return {"name": name, "title": title, "description": description, "annotations": ToolAnnotations(**annotations)}

    apps = Apps()
    apps.tool(resource_uri=VIEW_URI, **tool_kwargs("open_review"))(open_review)
    apps.tool(resource_uri=VIEW_URI, visibility=["app"], **tool_kwargs("save_review"))(save_review)
    apps.tool(resource_uri=VIEW_URI, visibility=["app"], **tool_kwargs("approve_review"))(approve_review)
    apps.add_html_resource(
        VIEW_URI,
        view,
        name="review",
        title="Preparer review",
        description="The preparer's review screen for one draft document list.",
        csp=ResourceCsp(resource_domains=[VIEW_CLIENT_ORIGIN]),
        permissions=ResourcePermissions(clipboard_write={}),
    )
    server = MCPServer(
        name=SERVER_NAME,
        version=APP_VERSION,
        log_level="WARNING",
        tools=[Tool.from_function(list_drafts, **tool_kwargs("list_drafts"))],
        extensions=[apps],
    )
    server.add_tool(approved_list, **tool_kwargs("approved_list"))
    return server


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="The preparer's review screen, an MCP App server over stdio.")
    parser.add_argument("--drafts", type=Path, required=True, help="folder of drafts to review, d01.json and so on")
    parser.add_argument("--reviews", type=Path, required=True, help="folder the review records are written to")
    parser.add_argument("--html", type=Path, default=DIST_HTML, help="the built view (default: preparer/app/dist/review.html)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    build_server(args.drafts, args.reviews, html_path=args.html).run("stdio")


if __name__ == "__main__":
    main()
