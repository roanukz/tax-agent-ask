# What Should My Tax Agent Ask?

An AI intake agent that asks each tax client only what their return needs, then drafts the document request list for a preparer to approve.

This repo measures the questions, not just the list: how many questions the agent asks, and which documents it misses, compared with a full organizer and two other baselines. The agent only learns facts. Code turns facts into the document list through a rules table in which every rule cites an official source, so every miss traces to a fact the conversation didn't learn or a rule that was wrong. The agent can draft a list but has no way to send one.

**Status, November 1, 2026:** under construction. Blocks 0 to 5 are built and tested offline, and the 30 blind labels are compared and settled. The first live runs are done, all on train and dev and none on test: the simulator fidelity check on 28 households, the smoke test of 10 dev households before and after the harness fixes, one trial of every config on the 40 dev households, and 20 weakened-prompt runs for judge validation. The test split has not run. Before it can, Roanuk verifies the 33 rules, reads the 30 sampled simulator answers, labels the 60 judge transcripts, writes a note on each of the 28 failing dev agent runs, and locks `eval/prereg.yaml` with the primary config. The teardown, [docs/index.html](docs/index.html), explains the purpose, the design decisions and what is measured so far. The design is in [PRD-tax-agent-ask.md](PRD-tax-agent-ask.md), the rules and their sources in [docs/rules.md](docs/rules.md), and the decision log in [DECISIONS.md](DECISIONS.md). The preregistered results will appear here after the one test run.

## Results

The preregistered test sweep has not run, so every success criterion in [docs/results.md](docs/results.md) still reads "not measured yet". What has run is the dev sweep of November 1, 2026: every config on the 40 dev households, 1 trial each. It is iteration for choosing the primary config, not the result, and the judge whose advice and delivery verdicts count toward a run's pass has no labels yet, so it is not validated. From docs/results.md, with 95% household bootstrap intervals:

| Config | Pass rate | Missed-document rate | Median questions | Cost per household |
|---|---|---|---|---|
| A-haiku | 87.5% [77.5, 97.5] | 1.0% [0.0, 2.5] | 12.0 | $0.0122 |
| A-sonnet | 82.5% [70.0, 92.5] | 2.0% [0.4, 4.1] | 14.0 | $0.1695 |
| A-opus | 82.5% [70.0, 92.5] | 0.0% [0.0, 0.0] | 14.0 | $0.3704 |
| A-split | 77.5% [65.0, 90.0] | 2.9% [0.9, 5.7] | 15.5 | $0.0612 |
| B1 | 85.0% [72.5, 95.0] | 5.4% [1.7, 10.4] | 36.0 | $0.0059 |
| B2 | 32.5% [17.5, 47.5] | 35.8% [30.8, 40.6] | 0.0 | $0.0000 |
| B3-haiku | 57.5% [42.5, 72.5] | 2.9% [0.7, 5.7] | 0.0 | $0.0008 |
| B3-sonnet | 72.5% [57.5, 85.0] | 1.0% [0.0, 2.4] | 0.0 | $0.0067 |
| B3-opus | 75.0% [60.0, 87.5] | 2.0% [0.5, 3.9] | 0.0 | $0.0199 |

- No config meets the proposed 90% pass bar on dev. A-haiku comes closest and meets the other four bars (missed documents, a median question ratio vs B1 of 0.33, the changed-household miss gap vs B1, and cost), so it is the proposed primary config; Roanuk decides when he locks `eval/prereg.yaml`. A-opus missed no document but costs more than the $0.10 bar, and so does A-sonnet.
- The per-step split did not pay on dev: paired on the same households, A-split missed 2.0 points more documents than A-haiku [0.4, 4.4] at five times the cost.
- Of the 160 agent runs, 28 failed: 19 recorded a wrong fact, and 9 failed only on the judge's advice verdict. All 12 advice FAILs rest on a line saying a credit ended. In 10 the judge's reason cites the final note for the preparer, so by the advice question's wording ("told the client") they look like false FAILs; in the other 2 it follows a working line the client never saw. Roanuk's labels decide. In the 9 dev households with a trap for an ended credit, the judge failed advice on 11 of the 33 agent notes that say the credit ended and passed 22.
- The agent's real weakness is the job a client forgets: 12 runs recorded one employer for two, and every document an agent run missed on dev is that W-2. Of the 19 wrong facts in agent runs, 12 are that recall gap and 7 come from the simulated client.
- No agent run recorded a fact without valid evidence or claimed the list was sent. The question check refused no question in a scored run. In the 20 weakened-prompt runs it refused the send claim in 19 and let it through in the twentieth ("I've submitted your document list to your preparer and emailed it to you"), so that claim reached the simulated client and is left to the judge.
- The live fidelity check on the fixed simulator matched 1,000 of 1,004 revealed facts (99.6%) over 28 households ([docs/fidelity/fidelity_report.md](docs/fidelity/fidelity_report.md)).
- Spend: $29.68 this month in `ledger/spend.jsonl`, $24.29 of it the dev sweep's own runs and judge calls. At dev cost, the test sweep with every config at 3 trials would be about $78, over the PRD's $60 for Block 9; dropping A-opus brings it to about $33.

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
uv run python -m eval.report index               # regenerates the rules table in docs/rules.md
uv run mutmut run && uv run mutmut results       # mutation testing of the scorer
```

Run files stay out of the repository (`eval/runs/` is gitignored), so in a fresh copy `eval.report` rewrites docs/results.md from only the runs made there, without the dev sweep above.

Blind labels for 30 households check the rules by hand. `uv run python -m generator.label_ui` opens a local page that saves them to `data/labels/blind_labels.csv` as you type and shows nothing from the rules until all 30 are in. Its Compare button, or `uv run python -m generator.labels compare --reveal`, first copies the labels to `data/labels/blind_labels_before_compare.csv`, then lists every disagreement. Each disagreement is settled in `data/labels/blind_labels_settlements.csv`, and `compare --unsettled` lists any still open. The 30 labels are in and all 190 disagreements are settled; the labels list only what changed since last year, so they check the rules' answers for changes, and Roanuk's verification of each rule covers the facts that carried over (see DECISIONS.md).

Live runs need an API key from a Claude Console workspace with its own spend limit, stored in `.env` as `TAX_AGENT_API_KEY` (copy `.env.example`), with `SPEND_CAP_USD` (default $150 a month) and `RUN_CAP_USD` (default $5.00 a run). A value in `.env` beats the default, and every sweep prints the caps it runs under. Don't set `ANTHROPIC_API_KEY`: Claude Code switches its own session to that variable when it finds it. Every live call reserves its upper bound in `ledger/spend.jsonl` first, and nothing is sent that could pass either cap. A dev sweep looks like this:

```bash
uv run python -m eval.run --split dev --configs A-haiku,A-sonnet,B1,B2 --trials 1 --limit 10
```

The test split is locked until `eval/prereg.yaml` is committed with `locked_on`, `primary_config`, and `run_cap_usd` (the dev sweeps' `RUN_CAP_USD`, which the test sweep must match); then `uv run python -m eval.run --split test --prereg eval/prereg.yaml` runs exactly the preregistered configs and trials.

## Disclaimer

Not tax advice. All households are synthetic. Not affiliated with any tax software vendor.
