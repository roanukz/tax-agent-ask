# Decisions

Dated and append-only: add new entries at the bottom and never edit old ones. A later entry can supersede an earlier one by saying so.

## 2026-10-30: design decisions from PRD section 8

| Decision | Why | Cost |
|---|---|---|
| Code computes the document list from recorded facts | Every miss traces to a fact or a rule, and the list can be audited | Documents outside the rules need custom requests, which are harder to score |
| Facts need verbatim evidence, enforced by a hook | Every recorded fact points to words the client said or a field from last year | The hook checks that the quote exists, not that it supports the fact; paraphrased but correct facts get denied and cost a retry |
| Ground truth comes from the same rules applied to the true facts | Isolates the agent's fact gathering | A wrong rule is invisible to scoring, so blind labels check the rules separately |
| Profiles and truth live in separate folders, and the server reads profiles only | The agent can't read the answer key, by construction | Two files per household |
| Code assertions for core metrics; one validated judge for honesty, starting on Haiku | Deterministic, cheap, and reproducible | Tone and clarity go unmeasured; a weaker judge may need a logged switch to Sonnet |
| Follow the 2026-07-28 spec: no Sampling, and intake state as a server-minted handle | Sampling is deprecated, and the core is stateless | Simulator spend comes from the credit, and every tool carries one more argument |
| Local stdio server only, no hosted endpoint | Hosted inference would spend the credit per visitor and stop when it runs out | No one-click public demo |
| Claude Agent SDK harness, not a hand-written Messages API loop | Hooks, tool controls, and caching come built in | A heavier runtime; if fixed overhead passes 6,000 tokens per run, find out why and log it |
| Key stored as `TAX_AGENT_API_KEY` | Claude Code switches its own session to `ANTHROPIC_API_KEY` when it finds it | One mapping step in the harness |
| Python, not TypeScript | Generator, statistics, and mutation testing tooling | Departs from earlier TypeScript agent repos |
| Federal only, tax year 2026 | Fits the 2027 season and keeps the rules checkable | Real firms also need state documents, and some 2026 rules wait on final instructions |
| No send tool at all, rather than a denied one | The agent can't send, rather than shouldn't | No end-to-end delivery demo |
| Baseline B3 hands each model the true facts and lets it write the list | Isolates the cost of model-written lists, including stale credit knowledge | Unrealistic on purpose: no real agent gets perfect facts |
| Traps for credits that ended | Tests whether older model knowledge leaks into custom requests and model-written lists | Depends on rules verified against IRS guidance |
| Bars and the primary config locked before one test sweep | Prevents moving goalposts and picking the best of several configs on test | A public miss is possible, and it gets reported |
| Name: What Should My Tax Agent Ask? | Fits the family of earlier projects and says tax | "Tax agent" can read as a human preparer, so the tagline says AI |

## 2026-10-30: Block 0 environment and SDK checks

Checked against the installed packages, which are the most exact reference for the pinned versions.

- **Wheels only.** The development machine is an Intel Mac. Several packages no longer publish x86_64 macOS wheels, and building them needs a Rust toolchain. `pyproject.toml` sets `no-build = true` and lists `required-environments`, so uv resolves to versions with wheels: cryptography 48.0.1 (pulled in by mcp through pyjwt), libcst 1.8.6, and mutmut 3.7.0. mutmut 3.8.x needs libcst 1.9, which has no x86_64 macOS wheel. PRD section 4 listed mutmut without a version.
- **Claude Agent SDK.** Pinned at 0.2.175, the version PRD section 3 checked. Version 0.2.176 appeared on PyPI on 2026-10-31 (UTC), less than a day before this entry, so the pin stays on the checked version. The bundled Claude Code CLI is 2.1.314.
- **Agent SDK option names match the PRD.** `ClaudeAgentOptions` has `tools`, `allowed_tools`, `disallowed_tools`, `setting_sources`, `env`, `cwd`, `max_turns`, `mcp_servers`, `hooks`, and `system_prompt`. In the transport, `tools=[]` becomes `--tools ""` (no built-in tools) and `setting_sources=[]` becomes `--setting-sources=` (nothing loads). `env` is merged over the inherited environment, and explicit values win.
- **Two options the PRD did not name.** The harness also sets `strict_mcp_config=True`, so the run ignores MCP servers configured anywhere else, and `max_budget_usd`, a CLI-side per-run cap that backs up `RUN_CAP_USD`.
- **Init and result fields.** The init frame is `SystemMessage(subtype="init")`, and its `data` has `tools` (a list of tool names) and `apiKeySource`. The harness aborts if any tool falls outside the allowed set, and also if `apiKeySource` is not `"ANTHROPIC_API_KEY"`, which means the child did not use the workspace key. `ResultMessage` has `total_cost_usd`, `usage`, `model_usage`, `num_turns`, and `duration_ms`.
- **Child process isolation.** Each run sets `CLAUDE_CONFIG_DIR` to a new empty temp folder, so the child cannot read the developer's Claude Code login, settings, plugins, or MCP config. It also blanks the other credential variables (`CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_AUTH_TOKEN`) and provider switches (Bedrock, Vertex, Foundry), so the workspace key is the only credential the child can use.
- **MCP.** In mcp 2.3.0, `mcp.types.LATEST_PROTOCOL_VERSION` is `2026-07-28`. The bundled CLI 2.1.314 implements the 2025-11-25 and 2026-07-28 eras and prefers 2026-07-28. The shared version is 2026-07-28, as the PRD planned. mcp 2.x renamed FastMCP to `MCPServer` (`mcp.server.mcpserver`); the server uses `MCPServer`.
- **Prices.** `ledger/prices.yaml` copies PRD section 3. That section gives no cache write price, so the file assumes 1.25 times the input price, marked to verify. Haiku is priced at the listed rate only for prompts up to 100,000 tokens. Above that, the ledger charges Sonnet rates as an upper bound until the long-prompt price is checked.
- **Ledger format.** Because the file is append-only, each run writes two lines: `reserve` before its first call and `settle` after its last. Month to date counts each run once: its actual cost if settled, otherwise its reservation. The month boundary is UTC.
- **Offline tests.** `pytest` runs with `--disable-socket --allow-unix-socket` from `pyproject.toml`.
