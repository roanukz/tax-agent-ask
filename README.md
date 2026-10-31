# What Should My Tax Agent Ask?

An AI intake agent that asks each tax client only what their return needs, then drafts the document request list for a preparer to approve.

This repo measures the questions, not just the list: how many questions the agent asks, and which documents it misses, compared with a full organizer and two other baselines. The agent only learns facts. Code turns facts into the document list through a rules table in which every rule cites an official source, so every miss traces to a fact the conversation didn't learn or a rule that was wrong. The agent can draft a list but has no way to send one.

**Status:** under construction. Blocks 0 to 5 are built and tested offline; no live model call has been made yet. The design is in [PRD-tax-agent-ask.md](PRD-tax-agent-ask.md) and the decision log is in [DECISIONS.md](DECISIONS.md). Results will appear here after the one preregistered test run.

## Results

Not run yet. The report page, [docs/results.md](docs/results.md), is generated from the runs and says "not measured yet" for each success criterion until the test sweep is complete.

## How to run

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run python -m agent.ping --dry-run
```

`pytest` runs offline with network sockets disabled. The dry-run ping starts the agent harness with a placeholder key against a closed local port, so it spends nothing.

Other checks that spend nothing:

```bash
uv run python -m rules.validate                  # every rule has a source and a status
uv run python -m agent.wiring_check              # the real bundled CLI against a closed port and a local fake API
uv run python -m agent.replay check              # scripted transcripts reproduce their summary and score
uv run python -m sim.fidelity --dry-run          # the simulator's scoring, with a stand-in client
uv run python -m eval.baselines prior-year --split train --split dev
uv run python -m eval.report                     # regenerates docs/results.md from eval/runs
uv run mutmut run && uv run mutmut results       # mutation testing of the scorer
```

Blind labels for 30 households check the rules by hand. `uv run python -m generator.label_ui` opens a local page that saves them to `data/labels/blind_labels.csv` as you type and shows nothing from the rules until all 30 are in. Its Compare button, or `uv run python -m generator.labels compare --reveal`, first copies the labels to `data/labels/blind_labels_before_compare.csv`, then lists every disagreement. Each disagreement is settled in `data/labels/blind_labels_settlements.csv`, and `compare --unsettled` lists any still open. The 30 labels are in and all 190 disagreements are settled; the labels list only what changed since last year, so they check the rules' answers for changes and not for facts that carried over (see DECISIONS.md).

Live runs need an API key from a Claude Console workspace with its own spend limit, stored in `.env` as `TAX_AGENT_API_KEY` (copy `.env.example`), with `SPEND_CAP_USD` (default $150 a month) and `RUN_CAP_USD` (default $2.00 a run). A value in `.env` beats the default, and every sweep prints the caps it runs under. Don't set `ANTHROPIC_API_KEY`: Claude Code switches its own session to that variable when it finds it. Every live call reserves its upper bound in `ledger/spend.jsonl` first, and nothing is sent that could pass either cap. A dev sweep looks like this:

```bash
uv run python -m eval.run --split dev --configs A-haiku,A-sonnet,B1,B2 --trials 1 --limit 10
```

The test split is locked until `eval/prereg.yaml` is committed with `locked_on`, `primary_config`, and `run_cap_usd` (the dev sweeps' `RUN_CAP_USD`, which the test sweep must match); then `uv run python -m eval.run --split test --prereg eval/prereg.yaml` runs exactly the preregistered configs and trials.

## Disclaimer

Not tax advice. All households are synthetic. Not affiliated with any tax software vendor.
