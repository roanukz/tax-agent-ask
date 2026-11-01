"""The MCP server "tax": eight intake tools over stdio, and no way to reach a client.

    python server/tax_server.py --profiles data/profiles --statuses verified,to_verify --state-dir server/state

The agent under test learns facts and records them here with evidence. Code,
not the model, turns recorded facts into the document request list through
rules.engine, counting only rules whose status the server was started with.
The last tool freezes the draft as pending_review for the preparer.

Tools are registered and listed in the fixed order of TOOLS, each with a title
and the annotations from PRD section 6.1. Following the 2026-07-28 spec, state
across calls travels as a server-minted intake_id in ordinary tool arguments.
The server opens household profiles in the profiles folder, the facts and
rules YAML it loads at startup, and its own state files; nothing else.

Claude Code launches it by absolute path from an empty temp folder with
PYTHONPATH set to the repo root. Run as a script without PYTHONPATH, it adds
the repo root itself.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Annotated, Any, Callable, Iterable, Sequence

REPO = Path(__file__).resolve().parent.parent
if __package__ in (None, "") and str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402
from pydantic import Field, WithJsonSchema  # noqa: E402

from rules import engine  # noqa: E402
from rules.engine import FactError  # noqa: E402
from server.intake import IntakeBook, IntakeError  # noqa: E402

SERVER_NAME = "tax"
SERVER_VERSION = "0.1.0"
DEFAULT_STATE_DIR = REPO / "server" / "state"

# Annotations from the PRD 6.1 table. The table leaves destructiveHint blank for
# add_custom_request and note_for_preparer, but the MCP spec defaults a blank
# destructiveHint to true, so every writing tool says false. No tool reaches
# anything outside this server, so openWorldHint is false throughout.
READ_ONLY = {"read_only_hint": True, "open_world_hint": False}
ADDITIVE = {"read_only_hint": False, "destructive_hint": False, "open_world_hint": False}
WRITES = ADDITIVE

# name: (title, annotations, description), in the order tools are listed.
TOOLS: dict[str, tuple[str, dict[str, bool], str]] = {
    "start_intake": ("Start intake", ADDITIVE, (
        "Opens an intake for one household. Returns a new intake_id, which every other tool takes, "
        "and last year's summary. Last year's information is a lead to confirm, not a fact about this year."
    )),
    "list_fact_keys": ("List fact keys", READ_ONLY, (
        "Lists every fact key you can record: its type, allowed values or range, topic, a plain-language "
        "question hint, when a follow-up applies, and whether last year's summary can answer it. "
        "A fact you never record counts as no, or zero."
    )),
    "get_document_rules": ("Get document rules", READ_ONLY, (
        "Lists the rules that turn facts into documents, each with its official source. A rule for a credit "
        "that ended shows that no document is needed. Pass fact_key to see only the rules that read that fact."
    )),
    "record_fact": ("Record a fact", ADDITIVE, (
        "Records one fact for this intake, checked against the key's type. Evidence must be two or more of "
        "the client's exact words in a row from one answer, or a one-word answer sentence such as \"Yes.\", "
        "or prior_year:<field> naming the field of last year's summary that holds this fact. "
        "Recording a key again replaces its value and keeps the history."
    )),
    "build_request_list": ("Build the request list", READ_ONLY, (
        "Computes the document request list from the recorded facts and the rules, with each document's "
        "count, reason, and source. Also lists the facts never recorded and the credits that ended, which "
        "need no document. Code writes this list; use add_custom_request for anything no rule covers."
    )),
    "add_custom_request": ("Add a custom request", WRITES, (
        "Adds a request for something the client described that no rule covers, with the reason. "
        "Every custom request is flagged for the preparer to review."
    )),
    "note_for_preparer": ("Note for the preparer", WRITES, (
        "Leaves a note for the preparer, such as a tax question the client asked that the preparer should answer."
    )),
    "submit_for_preparer_review": ("Submit for preparer review", ADDITIVE, (
        "Freezes the draft request list with status pending_review so the preparer can review it. "
        "After this the intake can't change."
    )),
}
TOOL_NAMES = tuple(TOOLS)

IntakeId = Annotated[str, Field(description="The intake_id that start_intake returned.")]
HouseholdId = Annotated[str, Field(description="The household ID: H followed by three digits, such as H017.")]
FactKey = Annotated[str, Field(description="A fact key from list_fact_keys.")]
FactValue = Annotated[
    Any,
    WithJsonSchema({"anyOf": [{"type": "boolean"}, {"type": "integer"}, {"type": "string"}]}),
    Field(description=(
        "The fact's value for its type: true or false, a whole number, one of the key's values, "
        "or a date as YYYY-MM-DD."
    )),
]
Evidence = Annotated[str, Field(description=(
    "Two or more of the client's exact words in a row from one answer, or a one-word answer sentence such as "
    "\"Yes.\"; or prior_year:<field> naming the field of last year's summary that holds this fact: the key's "
    "prior_year_field from list_fact_keys if it has one, or else documents_on_file.<rule_id> for a rule on file "
    "that reads the key, such as prior_year:filing_status for filing_status or "
    "prior_year:documents_on_file.R001 for w2_employer_count."
))]
RuleFilter = Annotated[str | None, Field(description="Only return rules whose condition reads this fact key.")]
Document = Annotated[str, Field(description="The document to ask the client for, in plain language.")]
Reason = Annotated[str, Field(description="Why this client needs it: what they described that no rule covers.")]
NoteText = Annotated[str, Field(description="The note, such as the client's tax question in their own words.")]


def _answer(call: Callable[..., Any], *args: Any) -> Any:
    """Runs one intake call and turns a refusal into a tool error the model can read."""
    try:
        return call(*args)
    except (IntakeError, FactError) as e:
        raise ToolError(str(e)) from None


def build_server(
    profiles_dir: str | Path,
    *,
    statuses: str | Iterable[str],
    state_dir: str | Path,
    facts_path: str | Path = engine.FACTS_PATH,
    rules_path: str | Path = engine.DOCUMENT_RULES_PATH,
    id_factory: Callable[[], str] | None = None,
) -> MCPServer:
    book = IntakeBook(
        profiles_dir,
        statuses=statuses,
        state_dir=state_dir,
        facts_path=facts_path,
        rules_path=rules_path,
        id_factory=id_factory,
    )

    def start_intake(household_id: HouseholdId) -> dict[str, Any]:
        return _answer(book.start_intake, household_id)

    def list_fact_keys() -> dict[str, Any]:
        return {"fact_keys": book.fact_keys()}

    def get_document_rules(fact_key: RuleFilter = None) -> dict[str, Any]:
        return {"rules": _answer(book.document_rules, fact_key)}

    def record_fact(intake_id: IntakeId, key: FactKey, value: FactValue, evidence: Evidence) -> dict[str, Any]:
        return _answer(book.record_fact, intake_id, key, value, evidence)

    def build_request_list(intake_id: IntakeId) -> dict[str, Any]:
        return _answer(book.request_list, intake_id)

    def add_custom_request(intake_id: IntakeId, document: Document, reason: Reason) -> dict[str, Any]:
        return _answer(book.add_custom_request, intake_id, document, reason)

    def note_for_preparer(intake_id: IntakeId, text: NoteText) -> dict[str, Any]:
        return _answer(book.note_for_preparer, intake_id, text)

    def submit_for_preparer_review(intake_id: IntakeId) -> dict[str, Any]:
        return _answer(book.submit_for_preparer_review, intake_id)

    handlers = {fn.__name__: fn for fn in (
        start_intake, list_fact_keys, get_document_rules, record_fact,
        build_request_list, add_custom_request, note_for_preparer, submit_for_preparer_review,
    )}
    server = MCPServer(name=SERVER_NAME, version=SERVER_VERSION, log_level="WARNING")
    for name, (title, annotations, description) in TOOLS.items():
        server.add_tool(
            handlers[name],
            name=name,
            title=title,
            description=description,
            annotations=ToolAnnotations(**annotations),
        )
    return server


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="The tax intake MCP server, over stdio.")
    parser.add_argument("--profiles", type=Path, required=True,
                        help="folder of household profiles, the only household data the server reads")
    parser.add_argument("--statuses", default="verified",
                        help="comma-separated rule statuses that count: verified, to_verify (default: verified)")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR,
                        help="folder for per-intake state files (default: server/state)")
    parser.add_argument("--facts", type=Path, default=engine.FACTS_PATH,
                        help="facts YAML (default: rules/facts.yaml)")
    parser.add_argument("--rules", type=Path, default=engine.DOCUMENT_RULES_PATH,
                        help="document rules YAML (default: rules/document_rules.yaml)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    build_server(
        args.profiles,
        statuses=args.statuses,
        state_dir=args.state_dir,
        facts_path=args.facts,
        rules_path=args.rules,
    ).run("stdio")


if __name__ == "__main__":
    main()
