# What Should My Tax Agent Ask?

An AI intake agent that asks each tax client only what their return needs, then drafts the document request list for a preparer to approve.

This repo measures the questions, not just the list: how many questions the agent asks, and which documents it misses, compared with a full organizer and two other baselines. The agent only learns facts. Code turns facts into the document list through a rules table in which every rule cites an official source, so every miss traces to a fact the conversation didn't learn or a rule that was wrong. The agent can draft a list but has no way to send one.

**Status, November 4, 2026:** the test run is done, on all 40 test households. Blocks 0 to 10 are done: Block 7's judge fix is in, though the judge still misses its advice and summary bars, and the pages went public on November 2. Roanuk verified all 33 rules, labeled 60 judge transcripts and noted every failing dev run, and `eval/prereg.yaml` was locked and committed before the test run. The run scored the first 25 test households on November 2 and, once the last 4 rules were verified, the other 15 on November 4, in a run declared in a commit before it started. The teardown, [docs/index.html](docs/index.html), explains the purpose, the design decisions and the results. The design is in [PRD-tax-agent-ask.md](PRD-tax-agent-ask.md), the rules and their sources in [docs/rules.md](docs/rules.md), and the decision log in [DECISIONS.md](DECISIONS.md).

## Results

Test run on all 40 test households at 3 trials each (25 households on November 2, 15 on November 4), from [docs/results.md](docs/results.md), with 95% household bootstrap intervals. A-opus meets criteria 1 to 6 and 8 (its pass-rate interval reaches below 90%); the judge is validated on delivery but not on advice or summary, so the advice verdicts inside these pass rates are an unvalidated model's.

| Config | Pass rate | Missed-document rate | Cost per household |
|---|---|---|---|
| A-opus (primary) | 92.5% [85.8, 98.3] | 0.5% [0.0, 1.1] | $0.3980 |
| A-sonnet | 90.8% [83.3, 96.7] | 1.4% [0.3, 3.0] | $0.1765 |
| A-haiku | 86.7% [77.5, 94.2] | 1.9% [0.4, 4.0] | $0.0133 |
| A-split | 86.7% [78.3, 94.2] | 1.4% [0.3, 3.0] | $0.0596 |
| B1 | 86.7% [75.8, 95.8] | 4.1% [1.1, 8.5] | $0.0060 |
| B2 | 32.5% [17.5, 47.5] | 35.5% [29.6, 40.8] | $0.0000 |

On the 10 complex households, which the first 25 held only 1 of, A-opus passed 27 of 30 runs and missed 1 of 309 documents. The network dropped during the November 4 run, and 17 agent runs never reached the model (no tokens, no tool call); under the rule for runs that measured no agent behavior, `eval.run` set them aside as `{run_id}.unreached.json` and ran them again. Scored as failures, they would put A-opus at 89.2%.

The dev sweep of November 1, every config on the 40 dev households at 1 trial, chose the primary config. It is kept below as it was, judged under transcript format 3:

| Config | Pass rate | Missed-document rate | Median questions | Cost per household |
|---|---|---|---|---|
| A-opus (primary) | 92.5% [82.5, 100.0] | 0.0% [0.0, 0.0] | 14.0 | $0.3704 |
| A-haiku | 87.5% [77.5, 97.5] | 1.0% [0.0, 2.5] | 12.0 | $0.0122 |
| A-sonnet | 85.0% [72.5, 95.0] | 2.0% [0.4, 4.1] | 14.0 | $0.1695 |
| A-split | 80.0% [67.5, 92.5] | 2.9% [0.9, 5.7] | 15.5 | $0.0612 |
| B1 | 85.0% [72.5, 95.0] | 5.4% [1.7, 10.4] | 36.0 | $0.0059 |
| B2 | 32.5% [17.5, 47.5] | 35.8% [30.8, 40.6] | 0.0 | $0.0000 |
| B3-haiku | 57.5% [42.5, 72.5] | 2.9% [0.7, 5.7] | 0.0 | $0.0008 |
| B3-sonnet | 72.5% [57.5, 85.0] | 1.0% [0.0, 2.4] | 0.0 | $0.0067 |
| B3-opus | 75.0% [60.0, 87.5] | 2.0% [0.5, 3.9] | 0.0 | $0.0199 |

- A-opus is the primary config. Under the bars as the PRD set them, no config met all five on dev: A-opus was the only one at the 90% pass bar, 92.5% with no document missed, but at $0.3704 a household it was 3.7 times the PRD's original $0.10 cost bar. Roanuk chose it because tax decisions need the highest accuracy possible. After the dev results and before the lock, he raised criterion 5's bar to $0.60, which leaves a buffer above 50 cents so that a test run that goes high does not fail it; $0.60 is 62% above A-opus's dev mean and 28% above its costliest dev run ($0.4691). The bar moved in light of dev results, and DECISIONS.md keeps the original and the reason; the other bars did not change.
- Under the $0.60 bar, A-opus meets all five bars on dev by point estimate (a median question ratio vs B1 of 0.39 and a changed-household miss gap vs B1 of -4.8 points), though its pass interval [82.5, 100.0] reaches below the 90% bar. A-haiku and A-sonnet each miss only the pass bar (87.5% and 85.0%), and A-split misses the pass and missed-document bars. A-haiku stays in the sweep as the cheap config, at $0.0122 a household. Paired on the same households, A-opus passed 5.0 points more often than A-haiku [-5.0, 15.0] and missed 1.0 point fewer documents [-2.5, 0.0]; both intervals reach zero, so one dev trial cannot separate them on quality, and the test sweep, at 3 trials, measures the locked config.
- The per-step split did not pay on dev: paired on the same households, A-split missed 2.0 points more documents than A-haiku [0.4, 4.4] at five times the cost.
- Of the 160 agent runs, 22 failed: 19 recorded a wrong fact, and 3 failed only on the judge's advice verdict. In format 3 of the judge's transcript, text the agent wrote outside `ask_client`, which the client never saw, reads "Assistant (not shown to the client)", and the final note reads "Assistant (final note for the preparer, not shown to the client)"; a refused or failed question keeps "Assistant" and counts as told. Under format 2 the judge failed advice in 12 runs, 10 on the final note and 2 on a working line; judged again under format 3, for $0.1514, it failed 4, each on the final note's line that a credit ended, so by the labeling rule in `eval/judge.py` they look like false FAILs. Roanuk's labels decide. In the 9 dev households with a trap for an ended credit, the judge now fails advice on 4 of the 33 agent notes that say the credit ended and passes 29 (format 2: 11 and 22). It also varies from one judging to the next: 3 of the 4 advice FAILs are runs it had passed, on transcripts that changed only in their labels, and 16 of the 160 summary verdicts flipped. Summary FAILs are 73 (69 under format 2): 72 name a document not on the draft list, and 1 leaves a listed one out.
- The agent's real weakness is the job a client forgets: 12 runs recorded one employer for two, and every document an agent run missed on dev is that W-2. Of the 19 wrong facts in agent runs, 12 are that recall gap and 7 come from the simulated client.
- No agent run recorded a fact without valid evidence or claimed the list was sent. The question check refused no question in a scored run. In the 20 weakened-prompt runs it refused the send claim in 19 and let it through in the twentieth ("I've submitted your document list to your preparer and emailed it to you"), so that claim reached the simulated client and is left to the judge.
- The live fidelity check on the fixed simulator matched 1,000 of 1,004 revealed facts (99.6%) over 28 households ([docs/fidelity/fidelity_report.md](docs/fidelity/fidelity_report.md)).
- Spend before the test run: $29.83 in `ledger/spend.jsonl`, $24.29 of it the dev sweep's own runs and judge calls and $0.15 the format 3 re-judge. At dev cost, the test sweep with every config at 3 trials would be about $78 ($77.99, of which A-opus is $44.55), over the PRD's $60 for Block 9. The primary config cannot be cut, but the PRD's next cut, trials from 3 to 2, would keep every config for about $52 ($51.99); Roanuk kept 3 trials and accepted the full sweep, and the month would stay under the $150 cap, at about $108. Instead the test run took the month to $153.78, about $17 of it booked for runs the network outage stopped before they reached the model; the month reached the $150 cap with 3 runs left, and `SPEND_CAP_USD` went to $160 to finish them.

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

## Share card

`docs/og-image.svg` is the source; `docs/og-image.png` is what the pages point at for link previews. Edit the SVG, then render it at 2x with headless Chrome (from the repo root, with the SVG's comment removed into a small HTML page), as was done on November 2, 2026:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --hide-scrollbars --force-device-scale-factor=2 --window-size=1200,627 --screenshot="$PWD/docs/og-image.png" "file://$PWD/og.html"
```
