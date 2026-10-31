# What Should My Tax Agent Ask?

An AI intake agent that asks each tax client only what their return needs, then drafts the document request list for a preparer to approve.

This repo measures the questions, not just the list: how many questions the agent asks, and which documents it misses, compared with a full organizer and two other baselines. The agent only learns facts. Code turns facts into the document list through a rules table in which every rule cites an official source, so every miss traces to a fact the conversation didn't learn or a rule that was wrong. The agent can draft a list but has no way to send one.

**Status:** under construction. The design is in [PRD-tax-agent-ask.md](PRD-tax-agent-ask.md) and the decision log is in [DECISIONS.md](DECISIONS.md). Results will appear here after the one preregistered test run.

## Results

Not run yet.

## How to run

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run python -m agent.ping --dry-run
```

`pytest` runs offline with network sockets disabled. The dry-run ping starts the agent harness with a placeholder key against a closed local port, so it spends nothing.

Live runs need an API key from a Claude Console workspace with its own spend limit, stored in `.env` as `TAX_AGENT_API_KEY` (copy `.env.example`). Don't set `ANTHROPIC_API_KEY`: Claude Code switches its own session to that variable when it finds it.

## Disclaimer

Not tax advice. All households are synthetic. Not affiliated with any tax software vendor.
