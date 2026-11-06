# The Preparer's Review Screen

An add-on to What Should My Tax Agent Ask?: an MCP App that shows the agent's draft document list to the preparer inside Claude, where only a click on the screen, never the model, can approve it.

**Where this goes:** the root of the existing `tax-agent-ask` repo, as `PRD-preparer-review.md`, opened in a fresh Claude Code session. It is committed with the code, so the repo rule holds: nothing in it may reference the author's employer or its clients, or any nonpublic information. Public sources cited by URL are fine.

**Status:** draft for Roanuk's sign-off. Section 1 is the one-page PRD. Sections 2 to 10 are the build brief. Where this file and `PRD-tax-agent-ask.md` disagree about the review screen, this file governs; everything else stays as that PRD and `DECISIONS.md` set it.

---

## 1. PRD (one page)

**Problem.** Every agent run ends with a draft in `pending_review`, and the teardown promises that only the preparer sends anything. Nothing shows a preparer that draft yet, so the promise is a status field. A preparer also needs more than the list: why each document is on it, the client's words behind each fact, and where the draft is most likely wrong. The test run already says where that is. All 9 failing A-opus test runs recorded a wrong fact, and 16 of the 22 failing dev runs I annotated came down to the employer count.

**Thesis.** Review is a second measurement, not just a screen. Code computes where to look (documents that dropped since last year, facts carried over without confirmation, facts never asked), and the screen puts each of those flags next to its evidence. A blind check on 24 drafts from the published test run then measures what review catches and what it can't, on the hypothesis that a preparer catches errors the draft shows but not facts the client never mentioned.

**Success criteria (testable).**
1. In Claude Desktop, asking Claude to open a draft renders the screen inline, and its Review button opens it full screen. All 24 drafts open this way during the catch check, and screenshots of a demo draft in light and dark mode are committed.
2. The model can't approve. The save and approve tools carry `visibility: ["app"]` (tool-list snapshot test). In Claude Desktop, asking Claude to approve a draft fails because it has no tool for it (Roanuk's check, with a screenshot). The server refuses an approval from a client that didn't negotiate MCP Apps, without the current review token, or for a different version of the draft (unit tests).
3. Nothing reaches a client. The preparer server has no send tool, the tool-list snapshot proves it, and the approved list is stored only in `data/reviews/`.
4. The preparer server never opens a path under a truth folder and never imports a model client, and the eval harness never connects to it (tests).
5. On all 24 drafts, every requested document shows its reason, its source link, and the recorded fact and quoted evidence that triggered it, and every flag points to its evidence (test).
6. In a client without MCP Apps, such as Claude Code, the open tool returns a readable text summary and every approval is refused (test with a client that advertises no extensions).
7. The blind catch check runs as preregistered in `eval/review_prereg.yaml`, locked before the first review: 24 drafts in a seeded order, with each draft's result and the answer key unseen until scoring. The report gives the catch rate on problem drafts, split into problems the draft shows and problems it hides, plus harmful edits on clean drafts and median minutes per draft, each with a 95% Wilson interval, whichever way the numbers come out.
8. A static demo of the same screen, built from the same source with three demo drafts, runs on the teardown site without Claude, and a browser test clicks through it to an approved list with no network.
9. `uv run pytest` passes with network sockets disabled, every existing test still passes, and no published agent result changes.

**Scope in.** A second local MCP server, `preparer`, with five tools and one `ui://` resource. A view (one HTML file) with an inline summary card and a full-screen review. An export of 24 drafts from the test run, with the answer key kept under `data/truth/`. Code-computed flags. Review records. The blind catch check and its report. A static demo. Teardown, walkthrough and README updates.

**Scope out.** Any change to the `tax` server, the harness, the prompts, the rules, or anything a published result depends on. Running new intakes. Free-text editing on the screen. Sending, e-signing or delivery. Remote hosting, accounts or authentication. Hosts other than Claude Desktop, beyond the text-only check in criterion 6. Real preparers or real client data.

**Constraints.** Python 3.12 and the pinned `mcp==2.3.0`, using its built-in MCP Apps extension. The view loads the MCP Apps client 1.7.5 from unpkg, the version and pattern Claude's quickstart documents, and only when it runs inside a host. Nothing in the feature calls a model, so it spends $0 of the API credit; Claude Desktop and Claude Code run on the subscription. All data synthetic. American spelling, no em or en dashes in any file. Nothing is pushed until Roanuk says proceed.

**Plan.** Seven blocks, section 7. About 2 hours of Roanuk's time, most of it the 24 reviews (a guess).

**Open questions for Roanuk.**
1. Name on the page: "The preparer's review screen" (my pick) or another.
2. Lock the catch check's hypothesis as written with no pass bar (my pick, since 24 drafts give intervals too wide for a bar to mean much), or add one.
3. Whether the static demo ships in the first push, or after the Claude Desktop screenshots exist.

**Alternatives rejected.** A standalone web page outside Claude, because it misses the point of reviewing where the work happens, and the local label page already covers that pattern. An approval form through MCP elicitation, because it can't show evidence for each item and the model drives it. Approval tools the model can see, guarded by a confirmation prompt, because app-only visibility takes the tool away from the model instead of asking it to behave. A bundler for the view, because it adds Node tooling to a Python repo for a local demo, while Claude's own quickstart loads the client from a CDN.

---

## 2. Quick start in Claude Code

1. Put this file at the root of the `tax-agent-ask` repo.
2. In Claude Code, run these two commands once to install the official MCP Apps skills:
   `/plugin marketplace add modelcontextprotocol/ext-apps`
   `/plugin install mcp-apps@mcp-apps`
3. Type: `Read PRD-preparer-review.md. Start Block 0.`

Claude Code builds one block at a time, reports the block's done-check, and waits for "continue." Roanuk's main hands-on steps are Block 4 (connect Claude Desktop and check it) and Block 5 (the 24 reviews).

Block 0 adds these lines to `CLAUDE.md`:

```markdown
- For the preparer review screen, read PRD-preparer-review.md. Where it and PRD-tax-agent-ask.md disagree about that feature, it governs.
- The preparer server reads its drafts folder and writes its reviews folder only. It never opens a truth folder, never calls a model, and has no tool that sends anything to anyone.
- save_review and approve_review stay app-only (visibility ["app"]) and refuse any client that did not negotiate MCP Apps.
- For this feature, never change the tax server, the harness, the prompts, the rules, or anything a published result depends on.
- The view has no free-text inputs and no browser storage.
- data/reviews/ holds Roanuk's labels: never edit a saved record by hand or by script, and before all 24 reviews are approved never show him any data/truth/review_*.json file (the key, the noted problems, the marked drafts), which drafts are marked or how many, or any draft's result.
```

---

## 3. Facts this design relies on (checked 2026-11-05)

- **Claude Desktop runs local MCP Apps.** Claude's docs connect a local MCP App server through Settings, Developer, Edit Config (`claude_desktop_config.json`) and a restart. The first time an app displays, Claude asks permission, with Allow and Always allow. The same page gives the two plugin commands above. Source: https://claude.com/docs/connectors/building/mcp-apps/getting-started
- **Local servers and debugging.** A local stdio server has no URL, so `ui.domain` isn't available for it; leave the field out. For developer tools, use Help, Troubleshooting, Enable Developer Mode, then Cmd+Option+I; Developer, Reload MCP Configuration applies config edits without a restart. A tool result over about 150,000 characters keeps an app from rendering. Source: https://claude.com/docs/connectors/building/mcp-apps/troubleshooting
- **Claude's design rules for apps.** An inline card fits its content height with no nested scrolling, and carries at most two actions and four or five data points. Full screen suits document review and detailed tools, using tabs or collapsible sections rather than floating panels. Text entry belongs in the chat, not the app. Views use the host's style variables and must work in light and dark mode. Source: https://claude.com/docs/connectors/building/mcp-apps/design-guidelines
- **The MCP Apps spec.** A UI resource is a `ui://` URI served as `text/html;profile=mcp-app`, and a tool points to it with `_meta.ui.resourceUri`. Visibility defaults to model and app. A host must leave app-only tools out of the model's tool list, and must reject an app's call to a tool whose visibility lacks app. `structuredContent` is for the view and isn't added to model context. With no CSP declared, the host allows no outside origins. Source: https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx
- **The Python SDK.** mcp 2.3.0 ships the extension as `mcp.server.apps`: `Apps()`, `@apps.tool(resource_uri=..., visibility=[...])`, `apps.add_html_resource(uri, html, csp=ResourceCsp(...), permissions=ResourcePermissions(...))`, `MCPServer(..., extensions=[apps])`, and `client_supports_apps(ctx)`, which is true only when the client advertised `io.modelcontextprotocol/ui` with the `text/html;profile=mcp-app` type. A tool can return a `CallToolResult` to set `content` and `structuredContent` separately. Source: the installed package's source, read 2026-11-05 (https://pypi.org/project/mcp/2.3.0/).
- **The view client.** Claude's quickstart loads `App` from `https://unpkg.com/@modelcontextprotocol/ext-apps@1.7.5/dist/src/app-with-deps.js`, declares `csp.resourceDomains: ["https://unpkg.com"]`, and says production should bundle the client or serve it from its own origin. The page was verified with 1.7.5, whose package has `callServerTool`, `updateModelContext`, `openLink`, `requestDisplayMode`, `getHostCapabilities` and `getHostContext`. The quickstart pairs the 1.x line with the 1.x TypeScript SDK; npm's newest is 2.0.4. Source: https://claude.com/docs/connectors/building/mcp-apps/quickstart
- **Text-only clients.** Claude Code calls an MCP App tool as text and doesn't render the view, and when a tool returns `structuredContent`, Claude Code hands the model that JSON in place of the text. Source: the same quickstart. A token in `structuredContent` is therefore not secret from a text-only client, which is why approval also checks the client's capabilities, and why `open_review` returns text only to such a client.

**Not checked, so Blocks 0 and 4 confirm:** that Claude Desktop advertises `io.modelcontextprotocol/ui` to a local stdio server, so `client_supports_apps` is true there; that it leaves app-only tools out of the model's list; that it offers full screen to a local app; whether it grants clipboard writes; and that the 1.7.5 client connects. If any of these fails, Claude Code stops, logs it in `DECISIONS.md`, and proposes the change.

---

## 4. Architecture

```
Claude Desktop (MCP Apps host, on Roanuk's subscription)
  the model sees:  list_drafts, open_review, approved_list
  the view calls:  open_review (to reload), save_review, approve_review
                   (save_review and approve_review are app-only)
        |
        v  stdio
  MCP server "preparer"  (python -m preparer.server --drafts ... --reviews ...)
    reads  the drafts folder           exported drafts, no truth
    writes the reviews folder          Roanuk's decisions and approved lists
    serves ui://preparer/review.html   the view
    no model calls, no send tool, refuses any truth path

offline, on the eval side (never reachable from the server):
  eval/export_drafts.py   eval/runs and scores  ->  data/drafts/ and data/truth/review_key.json
  eval/review_catch.py    data/reviews/ and the key  ->  docs/review-check.md
```

**Four guarantees live in code.**
1. Only a click on the screen can approve. `save_review` and `approve_review` are app-only, so an MCP Apps host leaves them out of the model's tools. The server also refuses both from any client that didn't negotiate MCP Apps, which covers text-only clients that list every tool.
2. An approval is bound to what the preparer saw. `open_review` mints a review token, returns it only in `structuredContent` and only to an MCP Apps client, and records the draft's SHA-256. Save and approve need both to match, and reopening a draft retires the old token.
3. Nothing is sent. No tool reaches a client; the approved list is stored for the preparer to send through their own system.
4. Review stays blind, and the eval stays untouched. The server reads only its drafts folder, refuses truth paths by importing the same check the `tax` server uses (`server/intake.py`, unedited), and the harness never connects to it.

**Folder additions.**

```
preparer/
  __init__.py
  server.py          the MCP server "preparer": tools, the ui:// resource, capability and token checks
  drafts.py          loads and validates drafts, computes flags
  reviews.py         decisions, validation, tokens, review records
  app/
    review.html      the view source: markup, CSS on host style variables, JS
    demo_drafts/     three drafts from dev runs, for the Block 4 check and the static demo
    dist/review.html the built file the server serves
  build.py           builds dist/review.html and docs/review-demo.html from review.html
data/
  drafts/            d01.json to d24.json
  reviews/           one record per reviewed draft
  truth/review_key.json   each draft's run, scorer findings, required list and problem class
eval/
  export_drafts.py
  review_prereg.yaml
  review_catch.py
docs/
  review-demo.html   the static demo
  review-check.md    the catch check report
  demo/              new light and dark screenshots
tests/
  test_preparer_drafts.py, test_preparer_server.py, test_preparer_reviews.py,
  test_preparer_build.py, test_review_catch.py,
  test_review_demo.py (browser test, skipped with a message if Playwright isn't installed)
```

`.gitignore` adds `preparer/app/demo_reviews/`, where Block 4's practice reviews go.

---

## 5. Data and schemas

### data/drafts/d00.json

Drafts carry no config, run ID, score, or truth field. Real files carry the full rule text; it's elided here. The example is invented, and d00 is outside the 24 review IDs.

```json
{
  "draft_id": "d00",
  "household_id": "H061",
  "client_name": "Rowan Testfield",
  "tax_year": 2026,
  "prior_year_summary": {
    "tax_year": 2025,
    "filing_status": "married_joint",
    "dependents_count": 1,
    "itemized": false,
    "documents_on_file": [
      {"rule_id": "R001", "label": "Form W-2", "count": 2},
      {"rule_id": "R014", "label": "Form 1098", "count": 1}
    ]
  },
  "documents": [
    {
      "rule_id": "R001",
      "document": "<R001's document text from rules/document_rules.yaml>",
      "count": 1,
      "client_reason": "<R001's client reason>",
      "source_url": "<R001's source URL>",
      "source_title": "<R001's source title>",
      "triggered_by": [
        {"fact": "w2_employer_count", "value": 1, "evidence": "Just the one job this year"}
      ]
    },
    {
      "rule_id": "R014",
      "document": "<R014's document text>",
      "count": 1,
      "client_reason": "<R014's client reason>",
      "source_url": "<R014's source URL>",
      "source_title": "<R014's source title>",
      "triggered_by": [
        {"fact": "has_mortgage", "value": true, "evidence": "same mortgage as last year"}
      ]
    }
  ],
  "custom_requests": [],
  "no_document_needed": [],
  "not_asked": ["gambling_winnings", "ip_pin_enrolled"],
  "notes_for_preparer": [{"text": "Client asked whether the home office counts."}],
  "facts_recorded": {
    "w2_employer_count": {"value": 1, "evidence": "Just the one job this year"},
    "has_mortgage": {"value": true, "evidence": "same mortgage as last year"}
  },
  "agent_note": "Draft list for the preparer: one W-2 and one 1098. Not yet sent.",
  "draft_sha256": "<SHA-256 of this file's content, computed without this field>"
}
```

`triggered_by` comes from the rules engine at export: for each document, the facts in its rule's condition, with the recorded value and evidence.

### Flags (preparer/drafts.py, deterministic, shown in this order)

| Flag | When | What the screen shows |
|---|---|---|
| `dropped` | A rule on file last year has no request this year, or a lower count | Last year's label and count beside this draft's, and the facts behind the rule with their evidence |
| `carried` | A fact was recorded with `prior_year:` evidence | The fact, its value, and that the client didn't confirm it this year |
| `custom` | The agent added a custom request | The request and the agent's reason |
| `client_question` | A note for the preparer exists | The note, in the agent's words |
| `ended_credit` | A rule for an ended credit fired | The credit's source and "no document needed" |

The facts never asked are not a flag; they're a collapsed section with a count.

### Decisions (what the view sends to save_review and approve_review)

For d00, where the preparer raises the W-2 count to last year's two and asks the client about other jobs:

```json
{
  "documents": [
    {"rule_id": "R001", "action": "keep", "count": 2},
    {"rule_id": "R014", "action": "keep"}
  ],
  "custom_requests": [],
  "added": [],
  "follow_ups": [{"fact": "w2_employer_count"}],
  "flags_checked": ["dropped:R001", "client_question:0"]
}
```

A removal looks like `{"rule_id": "R023", "action": "remove", "reason": "not_needed"}` in `documents`, a kept custom request like `{"index": 0, "action": "keep"}`, and an addition like `{"rule_id": "R012"}` in `added`.

Validation: every document and custom request has exactly one decision before approval; `count` appears only on rules with `count_from`, within the fact's range in `rules/facts.yaml`; `reason` is one of `not_needed`, `duplicate`, `client_said_no`; `added` names only request rules (never traps) that aren't already on the draft; `follow_ups` names only keys in `rules/facts.yaml`; every flag on the draft is in `flags_checked` before approval.

### data/reviews/d00.json

```json
{
  "draft_id": "d00",
  "draft_sha256": "<the draft's SHA-256>",
  "status": "approved",
  "decisions": "<the d00 decisions above, as saved>",
  "approved_list": [
    {"rule_id": "R001", "document": "<R001's document text>", "count": 2},
    {"rule_id": "R014", "document": "<R014's document text>", "count": 1}
  ],
  "follow_up_questions": [{"fact": "w2_employer_count", "question": "<the fact's plain_question_hint>"}],
  "changes": {"removed": 0, "added": 0, "count_changed": 1, "follow_ups": 1},
  "opened_at": "2026-11-08T15:02:11Z",
  "saved_at": ["2026-11-08T15:03:02Z"],
  "approved_at": "2026-11-08T15:03:40Z",
  "seconds_on_screen": 89,
  "host": {"name": "<hostInfo name from ui/initialize>", "version": "<hostInfo version>"},
  "app_version": "0.1.0"
}
```

Statuses run `pending_review` (no record yet), then `in_review` (saved), then `approved`, which is final. The server records the times, so `seconds_on_screen` never trusts the view's clock.

### data/truth/review_key.json

```json
{
  "seed": 11,
  "source": "test split, every A config, the 2026-11-04 completion run, set-aside runs excluded",
  "drafts": [
    {
      "draft_id": "d00",
      "run_id": "test-H061-A-sonnet-t2",
      "scorer_pass": false,
      "required": [{"rule_id": "R001", "count": 2}, {"rule_id": "R014", "count": 1}],
      "missed": [{"rule_id": "R001", "count": 1}],
      "unnecessary": [],
      "wrong_facts": ["w2_employer_count"],
      "problem_class": "shows",
      "shows_because": "dropped:R001"
    }
  ]
}
```

`problem_class` is `shows`, `hides` or `clean`. A problem shows when the missed document's rule was on file last year with a higher count than the draft requests, or when the wrong fact was recorded with `prior_year:` evidence. Every other problem hides.

### eval/review_prereg.yaml

```yaml
locked_on: null        # set and committed before Roanuk opens the first of the 24
drafts: 24
problem_drafts: 12     # aim for 6 that show and 6 that hide, as the run allows
clean_drafts: 12
seed: 11
caught_when: "the approved list adds the missed document or corrects its count, or a follow-up asks about the fact behind the miss or the wrong fact"
harmful_edit: "removing a required document, or adding a document the household does not need"
hypothesis: "Review catches most problems the draft shows and few it hides, and harmful edits on clean drafts are rare."
report: [catch_rate_shows, catch_rate_hides, harmful_edits_clean, follow_ups_clean, median_minutes]
intervals: {method: wilson, level: 0.95}
```

---

## 6. Components

### 6.1 The preparer server

| Tool | Visibility | Input | Returns | Annotations |
|---|---|---|---|---|
| `list_drafts` | model (a plain tool, not bound to the view) | none | Each draft's ID, client name, document count, flag count and status | readOnlyHint true |
| `open_review` | model, app; renders `ui://preparer/review.html` | `draft_id` | `content`: a readable summary of the documents, flags and notes, with no token. `structuredContent`, for MCP Apps clients only: the draft, its flags, the request rules it can add, the review token and the draft's SHA-256 | readOnlyHint false, destructiveHint false (it mints a token) |
| `save_review` | app only | `draft_id`, `review_token`, `draft_sha256`, `decisions` | The saved record | readOnlyHint false, destructiveHint false |
| `approve_review` | app only | `draft_id`, `review_token`, `draft_sha256`, `decisions` | The approved list and a change summary | readOnlyHint false, destructiveHint false |
| `approved_list` | model (a plain tool, not bound to the view) | `draft_id` | The approved list and follow-up questions as plain text, or "not approved yet" | readOnlyHint true |
| (no tool) | | send anything to a client | does not exist | |

Build it with `mcp.server.apps`: `@apps.tool(resource_uri="ui://preparer/review.html")` for `open_review`, the same with `visibility=["app"]` for the two app-only tools, plain `add_tool` for `list_drafts` and `approved_list`, `apps.add_html_resource("ui://preparer/review.html", html, csp=ResourceCsp(resource_domains=["https://unpkg.com"]), permissions=ResourcePermissions(clipboard_write={}))` with no `domain`, and `MCPServer("preparer", ..., extensions=[apps])`. Tools list in a fixed order. For a client without MCP Apps, `open_review` returns text only, because a text-only client like Claude Code would hand the model `structuredContent` in place of the text.

`save_review` and `approve_review` check, in this order: the client negotiated MCP Apps (`client_supports_apps`), the token is current for this draft, the SHA-256 matches, the decisions validate, and the draft isn't already approved. Each refusal returns a plain message the view shows. Tokens live only in server memory, so a server restart means reopening the draft. On the first request, the server writes the client's advertised extensions to stderr, never stdout, for the Block 4 check.

### 6.2 The view

- **Inline card**, which `open_review` renders first: the client's name and tax year, documents requested, flags to check, notes for the preparer, and status. One action, Review, requests full screen. An approved draft shows its approval time and change count, with View approved list.
- **Full screen**: tabs for Flags, Documents, Facts and Notes, with Save and Approve list in a bottom bar, and Flags first.
  - Each document shows its count, reason and source (opened through the host's open-link request), and the fact and quoted words that triggered it. Its actions are Keep or Remove, with reason chips (not needed, duplicate, client said no), plus a count stepper on W-2 and K-1 rules.
  - Add a document lists the request rules not on the draft as visible chips, not a dropdown.
  - Each recorded fact shows its value and evidence, with Ask the client again.
  - Not asked is a collapsed list with its count.
- **Approve list** stays disabled until every document and custom request has a decision and every flag is checked, and the bar says what's missing. After approval the screen shows the approved list as text with a Copy button, which uses the clipboard if the host grants it and otherwise selects the text. It also calls `updateModelContext` with a one-line summary, so Claude knows the outcome without posting on the preparer's behalf.
- **Rules:** host style variables with fallbacks, in light and dark; a skeleton while loading; tap targets of at least 44 points; a full keyboard path with visible focus; padding from the host's safe-area insets; no free text, menus or browser storage. The client loads through a dynamic import, and only when the page runs inside a host.
- **Without a host that proxies server tools** (`getHostCapabilities()` lacks `serverTools`), the screen shows the draft read-only and says approval needs a host that can call server tools.

### 6.3 Build and the static demo

`preparer/build.py` writes the served `preparer/app/dist/review.html` and the demo `docs/review-demo.html` from `preparer/app/review.html`. The demo embeds the three demo drafts, runs a fake tool layer in memory, and says "Demo: nothing is saved." Both outputs carry a header comment with the source's SHA-256, and a test fails when either is stale. The demo needs no network. If the browser test needs a local socket, allow 127.0.0.1 for that test only, never the network.

### 6.4 Export and the catch check

`eval/export_drafts.py` reads the test runs in `eval/runs/` and works as follows:

- It draws 12 problem drafts (a missed required document or a wrong fact) and 12 clean drafts with a seeded draw: at most one draft per household, configs mixed, and 6 problems that show and 6 that hide when the run has enough of each.
- It writes `data/drafts/d01.json` to `d24.json` in a seeded order, and the key to `data/truth/review_key.json`.
- It also exports the three demo drafts from dev runs: one clean, one with a `dropped` flag, and one with a client question.
- It refuses to run when `data/reviews/` already holds a record.

`eval/review_catch.py` scores the reviews against the key once all 24 are approved, and writes `docs/review-check.md`. The report gives the counts for each class with Wilson intervals, each problem caught or missed beside the flag that was or wasn't on the screen, harmful edits, follow-ups on clean drafts, and the time per draft.

---

## 7. Build plan

| Block | What gets built | Who runs it | Output | Done when |
|---|---|---|---|---|
| 0 | Confirm `mcp.server.apps` in the installed SDK, the 1.7.5 client URL and methods, and the plugin skills; add the `CLAUDE.md` lines; add Playwright to the dev dependencies and install its browser once, the feature's only download | Claude Code; Roanuk runs the two plugin commands | A `DECISIONS.md` entry | Every section 3 fact the code depends on is confirmed, or its difference is logged |
| 1 | `export_drafts.py`, the key, `drafts.py` and its flags | Claude Code | 24 drafts, 3 demo drafts, the key | Tests pass; every draft validates; every problem draft has a class; no draft file holds a config, run ID, score or truth field |
| 2 | The preparer server and `reviews.py` | Claude Code | `preparer/server.py` | The tool-list snapshot shows five tools, two of them app-only, and no send tool; refusal tests pass for no MCP Apps, a stale or wrong token, a changed SHA-256, invalid decisions, and an approved draft; truth-path, no-model and harness tests pass |
| 3 | The view, the build and the static demo | Claude Code | `review.html`, `dist/`, `docs/review-demo.html` | The build check passes, the browser test approves a demo draft using only the keyboard, and a test proves the view has no free-text field |
| 4 | Claude Desktop check, with the server pointed at `preparer/app/demo_drafts` and `preparer/app/demo_reviews` | Roanuk, with Claude Code fixing what he finds | The config, four screenshots, fixes | The screen renders inline and full screen in light and dark; a demo draft goes through Save and Approve; asking Claude to approve fails for lack of a tool (screenshot); stderr shows the client advertised `io.modelcontextprotocol/ui`; every difference from section 3 is logged |
| 5 | Lock the review prereg, switch the config to `data/drafts` and `data/reviews`, then the 24 blind reviews and the scoring | Roanuk reviews (about 75 minutes, a guess); Claude Code scores | `data/reviews/`, `docs/review-check.md` | The prereg commit predates the first review record, all 24 are approved, and the report is written |
| 6 | One teardown part, one walkthrough step, the README's Claude Desktop setup, then publish | Claude Code drafts; Roanuk reviews and says proceed | Updated pages | Every cross-reference still holds, the tests that pin page structure are updated, and no push happens before Roanuk's proceed |

**The Claude Desktop config (Block 4 uses the demo folders, Block 5 these):**

```json
{
  "mcpServers": {
    "tax-preparer": {
      "command": "/ABSOLUTE/PATH/TO/uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/tax-agent-ask", "python", "-m", "preparer.server",
               "--drafts", "data/drafts", "--reviews", "data/reviews"]
    }
  }
}
```

Use the absolute path that `which uv` prints, because Claude Desktop may not see the shell's PATH.

**Spend:** $0 of the API credit. Nothing in the feature calls a model, and Claude Desktop and Claude Code run on the subscription.

**Cut order if running behind:** the static demo first (the screenshots carry the page), then the catch check down to 12 drafts (6 that show and 6 that hide), then the Copy button.

**Never cut:** app-only approval and the server's refusals, the token and SHA-256 binding, the truth guard, the absence of a send tool, an untouched eval, and the prereg lock before the first review.

---

## 8. Decision log

| Decision | Why | Cost |
|---|---|---|
| A separate `preparer` server that the eval harness never connects to | The agent under test can't reach approval tools, and the published results stay valid | Two servers to configure if an end-to-end demo comes later |
| Save and approve are app-only (`visibility: ["app"]`) | An MCP Apps host leaves them out of the model's tools | Depends on the host following the spec, which Block 4 checks by hand |
| The server refuses approval from clients without MCP Apps | Text-only clients list every tool, so hiding alone doesn't cover them | A client that wrongly advertises MCP Apps would get through; the token is the second check |
| A review token in `structuredContent`, bound to the draft's SHA-256 | An approval must come from a screen that showed this exact draft | Reopening a draft retires the old token, so a stale screen can't save |
| Structured decisions, no free text | Claude's design guidance puts text entry in chat, and structured decisions make the catch check scoreable | Wording changes wait for a later version |
| Flags computed by code from the draft and last year's summary | Deterministic, testable, and no model call | The screen can't flag what the client never mentioned, which the catch check measures |
| Drafts exported from the published test run, with each result hidden | Real agent output at no new spend | One reviewer and 24 drafts give wide intervals |
| View client 1.7.5 from unpkg | It's the path and version Claude's quickstart documents | One declared origin and a network dependency at render time; production would bundle |
| The client loads only inside a host | The static demo and the tests run offline | Two code paths in the view |
| A static demo built from the same source | Recruiters can try the screen without Claude | A build check is needed to keep the two in sync |
| An inline card for the summary, full screen for the review | Claude's guidelines cap inline cards at two actions and four or five data points | Two layouts to test |
| The server keeps review state | Sandboxed views have no storage | Unsaved toggles are lost if the screen closes, so Save exists |
| `updateModelContext` after approval, not a chat message | Claude knows the outcome without posting on the preparer's behalf | Nothing appears in the chat until Roanuk asks |
| A preregistered hypothesis instead of a pass bar | With 24 drafts, a bar would mostly measure noise | A weaker claim than the agent eval makes |

---

## 9. Out of scope and future work

- **End to end in Claude Desktop:** connect the `tax` server too, play the client in chat, then review the draft. The evidence check would have to move from the harness into the server first.
- **A real preparer** reviewing 20 drafts with the same scoring, which would be this project's first measure with a real user.
- **Wording changes through the chat:** Claude proposes a change, and the screen shows it for the preparer to accept.
- **Reading uploads beside the draft,** the only way review could catch a fact the client never mentioned.
- **Packaging the server as an MCP Bundle** for a one-click install.

---

## 10. Definition of done and the stop report

Done when `uv run pytest` passes with network sockets disabled, criteria 1 to 9 each have their evidence, `docs/review-check.md` is written, and the pages are updated and pushed after Roanuk says proceed.

Then stop and report: each criterion with its evidence; the catch check's numbers; anything Claude Desktop did differently from section 3; every refusal the server logged during Blocks 4 and 5; and anything still unverified.

*Not tax advice. All households are synthetic. Not affiliated with any tax software vendor.*
