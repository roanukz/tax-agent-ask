# Results: What Should My Tax Agent Ask?

Generated on 2026-11-02 by `uv run python -m eval.report` from 380 run summaries and 380 scores in eval/runs/. Intervals: household bootstrap, 10000 draws, seed 7, level 0.95. Preregistration: locked_on null, primary_config A-opus.

## Executive summary

This project measures how many questions an AI tax intake agent asks and which documents it misses. The agent only learns facts; code turns recorded facts into the document request list through a rules table with an official source for every rule, so every miss traces to a fact or a rule. All households are synthetic.

- Scored runs: 380 (dev 360, train 20).
- Success criteria (the 8 in the PRD plus the judge bars): 0 met, 0 not met, 9 not measured yet. The table is at the top of Results.
- A-opus on dev: pass rate 92.5% [82.5, 100.0], missed-document rate 0.0% [0.0, 0.0], over 40 runs.
- A-haiku on dev: pass rate 87.5% [77.5, 97.5], missed-document rate 1.0% [0.0, 2.5], over 40 runs.
- A-sonnet on dev: pass rate 85.0% [72.5, 95.0], missed-document rate 2.0% [0.4, 4.1], over 40 runs.
- A-split on dev: pass rate 80.0% [67.5, 92.5], missed-document rate 2.9% [0.9, 5.7], over 40 runs.
- B1 on dev: pass rate 85.0% [72.5, 95.0], missed-document rate 5.4% [1.7, 10.4], over 40 runs.
- B2 on dev: pass rate 32.5% [17.5, 47.5], missed-document rate 35.8% [30.8, 40.6], over 40 runs.
- B3-haiku on dev: pass rate 57.5% [42.5, 72.5], missed-document rate 2.9% [0.7, 5.7], over 40 runs.
- B3-sonnet on dev: pass rate 72.5% [57.5, 85.0], missed-document rate 1.0% [0.0, 2.4], over 40 runs.
- B3-opus on dev: pass rate 75.0% [60.0, 87.5], missed-document rate 2.0% [0.5, 3.9], over 40 runs.

Not run yet:

- The test split has not run: eval/prereg.yaml is not locked.

## Data

### Generator

100 synthetic households from seed 20261030: 30 simple, 45 medium, 25 complex. 70 changed since last year. Regenerating with the same seed is byte-identical (tested). Names are obviously fake, and nothing in the data describes a real person.

### Splits

| Split | Households | simple | medium | complex | Changed | Advice probes | Send probes |
|---|---|---|---|---|---|---|---|
| train | 20 | 6 | 9 | 5 | 14 | 4 | 2 |
| dev | 40 | 12 | 18 | 10 | 28 | 8 | 4 |
| test | 40 | 12 | 18 | 10 | 28 | 8 | 4 |

Ratio 20/40/40, stratified by complexity, frozen on 2026-10-31. Train holds prompt examples, dev is for iteration, and test is touched once, after the bars are locked.

### Rule verification

| Status | Rules |
|---|---|
| verified | 10 |
| to_verify | 23 |
| excluded | 0 |

33 rules: 30 document requests and 3 traps for credits that ended. Every rule cites an official source; source versions: 3 draft_instructions, 6 final_instructions, 24 guidance. Train and dev scoring counts verified and to_verify rules; test scoring counts verified rules only. Under the current statuses, 40 of 40 test households depend on a rule or trap that is not verified and would drop out of test scoring.

### Label agreement

30 of 30 households labeled; 190 disagreements with the rules and 0 notes (run uv run python -m generator.labels compare).

Compare rows settled in data/labels/blind_labels_settlements.csv: 78 are label items and the rule documents they name in other words (39 label items); 15 were checked against the cited source, and the rules stand; 2 ask for something the household's facts don't call for; 95 are documents, or parts of a count, for facts that did not change, which the labels leave out, so the labels don't check those rule answers. Every disagreement is logged.

Judge labels: 0 of 60 dev runs labeled in data/labels/judge_labels.csv.

### Simulator fidelity

Live check on claude-haiku-5-5: 1000 of 1004 revealed facts match the truth (99.6%) over 28 households and 308 answers, and 703 of 705 (99.7%) over the 20 drawn by complexity; 6 leaks code caught; 0 unscored answers. Bar (98%, every answer scored): met. Facts that ended, in 9 households: 0 revealed as true this year in the answers kept, 0 in first replies the simulator sent back. Answers the simulator sent back for contradicting its facts: 2 of 308.

## Experiments

### Baselines

- Goal: Reference points for the question count and the list.
- What was tried: B1 asks every fact key through the simulator; B2 sends last year's list without questions; B3 hands each model the true facts and lets it write the list, with no tools and no rules table.
- Method: PRD 6.5. B1 and B3 are scored like agent runs on the list and facts; B3 items map to rules through aliases.
- Findings: B1 on dev: pass rate 85.0% [72.5, 95.0], missed-document rate 5.4% [1.7, 10.4] (40 runs). B2 on dev: pass rate 32.5% [17.5, 47.5], missed-document rate 35.8% [30.8, 40.6] (40 runs). B3-haiku on dev: pass rate 57.5% [42.5, 72.5], missed-document rate 2.9% [0.7, 5.7] (40 runs). B3-sonnet on dev: pass rate 72.5% [57.5, 85.0], missed-document rate 1.0% [0.0, 2.4] (40 runs). B3-opus on dev: pass rate 75.0% [60.0, 87.5], missed-document rate 2.0% [0.5, 3.9] (40 runs). B2 on train: pass rate 35.0% [15.0, 55.0], missed-document rate 29.7% [21.4, 37.0] (20 runs).

### Single-model agents

- Goal: Does the agent learn the facts that matter while asking fewer questions?
- What was tried: The same harness and prompt on Haiku 5.5, Sonnet 5.5, and Opus 5.5.
- Method: PRD 6.2: Claude Agent SDK harness, no built-in tools, the evidence hook, the client simulator, and deterministic scoring.
- Findings: A-haiku on dev: pass rate 87.5% [77.5, 97.5], missed-document rate 1.0% [0.0, 2.5] (40 runs). A-sonnet on dev: pass rate 85.0% [72.5, 95.0], missed-document rate 2.0% [0.4, 4.1] (40 runs). A-opus on dev: pass rate 92.5% [82.5, 100.0], missed-document rate 0.0% [0.0, 0.0] (40 runs).

### Per-step models

- Goal: Can a Sonnet plan with a Haiku conversation match Sonnet's quality for less?
- What was tried: A-split: one Sonnet 5.5 call writes a question plan, and Haiku 5.5 runs the conversation.
- Method: Compared on dev with the single-model configs (Block 8).
- Findings: A-split on dev: pass rate 80.0% [67.5, 92.5], missed-document rate 2.9% [0.9, 5.7] (40 runs).

### Honesty judge

- Goal: Can Haiku 5.5 grade advice, delivery, and summary honesty reliably?
- What was tried: One judge prompt (PRD 6.7), validated against Roanuk's labels on 60 dev runs.
- Method: A 12/24/24 split into examples, tuning, and final; true positive and true negative rates on the final 24.
- Findings: Not run yet: no judge labels.

### Test sweep

- Goal: The one preregistered test of the success criteria.
- What was tried: Every config in eval/prereg.yaml on the 40 test households, 3 trials each.
- Method: Run once, after the bars and primary config are locked and committed.
- Findings: Not run yet.

## Results

### Success criteria

Judged on the test split only. Before the test sweep, each criterion says what is missing.

| # | Criterion | Bar | Status | Evidence or what is missing |
|---|---|---|---|---|
| 1 | The primary config passes at least 90% of test runs and misses no more than 2% of required documents | pass rate at least 90.0%, missed-document rate at most 2.0% | not measured yet | eval/prereg.yaml is not locked (locked_on is null), so the test sweep has not run. |
| 2 | The primary config's median question count is at most 50% of the full organizer's on the same households | median question ratio vs B1 at most 0.50 | not measured yet | eval/prereg.yaml is not locked (locked_on is null), so the test sweep has not run. |
| 3 | On households whose situation changed, the primary config's missed-document rate is at most 2 points above the full organizer's; the prior-year list's misses are shown too | gap at most 2.0 percentage points | not measured yet | eval/prereg.yaml is not locked (locked_on is null), so the test sweep has not run. |
| 4 | No agent run records a fact without valid evidence or claims the list was sent | zero such runs | not measured yet | No agent runs on test yet. On dev so far: 0 facts without valid evidence and 0 runs claiming the list was sent, over 160 runs. |
| 5 | The primary config costs at most $0.60 per household at list price; cost is reported for every config | at most $0.60 per household | not measured yet | eval/prereg.yaml is not locked (locked_on is null), so the test sweep has not run. |
| 6 | Every rule cites an official source and is verified by Roanuk before it counts in test scoring; blind labels for 30 households match the rules or each disagreement is settled and logged | every rule sourced and verified or excluded; 30 labels, disagreements settled | not measured yet | Every rule has a source URL, but 23 of 33 rules are still to_verify. The labels list only what changed, so 95 rule answers, or parts of a W-2 count, for facts that did not change are checked by rule verification alone. |
| 7 | pytest passes with network sockets disabled, and mutation testing of the scorer kills at least 90% of mutants | pytest passes; at least 90% of mutants killed | not measured yet | The report does not run the tests; run uv run pytest and uv run mutmut run. Last recorded in DECISIONS.md: 1065 of 1067 scorer mutants killed (99.8%). |
| 8 | A teardown page and a results report are published, including the findings that went against the thesis | Roanuk says proceed, then publish | not measured yet | Nothing is published. Publishing waits for Roanuk's explicit proceed (Block 10). |
| J | The honesty judge meets its preregistered bars on the final 24 labeled dev runs (PRD 6.7) | TPR at least 90.0% and TNR at least 90.0% per question | not measured yet | 0 of 60 dev runs labeled in data/labels/judge_labels.csv. |

### Dev split

Baselines against A-opus. Rates carry 95% household bootstrap intervals. For baselines, a pass means the list misses nothing and has at most one unnecessary request (B1 also needs no wrong fact), and a baseline gets no probes.

| Config | Runs (households) | Pass rate | All trials pass | Missed-document rate | Unnecessary per household | Trap requests per household | Probe not delivered |
|---|---|---|---|---|---|---|---|
| A-opus (primary) | 40 (40) | 92.5% [82.5, 100.0] | 92.5% [82.5, 100.0] | 0.0% [0.0, 0.0] | 0.1 [0.0, 0.1] | 0.0 [0.0, 0.0] | 0.0% [0.0, 0.0] |
| A-haiku | 40 (40) | 87.5% [77.5, 97.5] | 87.5% [77.5, 97.5] | 1.0% [0.0, 2.5] | 0.1 [0.0, 0.2] | 0.0 [0.0, 0.0] | 0.0% [0.0, 0.0] |
| A-sonnet | 40 (40) | 85.0% [72.5, 95.0] | 85.0% [72.5, 95.0] | 2.0% [0.4, 4.1] | 0.1 [0.0, 0.2] | 0.0 [0.0, 0.0] | 0.0% [0.0, 0.0] |
| A-split | 40 (40) | 80.0% [67.5, 92.5] | 80.0% [67.5, 92.5] | 2.9% [0.9, 5.7] | 0.0 [0.0, 0.1] | 0.0 [0.0, 0.0] | 0.0% [0.0, 0.0] |
| B1 | 40 (40) | 85.0% [72.5, 95.0] | 85.0% [72.5, 95.0] | 5.4% [1.7, 10.4] | 0.0 [0.0, 0.0] | 0.0 [0.0, 0.0] | n/a (no probes) |
| B2 | 40 (40) | 32.5% [17.5, 47.5] | 32.5% [17.5, 47.5] | 35.8% [30.8, 40.6] | 0.3 [0.1, 0.5] | 0.0 [0.0, 0.0] | n/a (no probes) |
| B3-haiku | 40 (40) | 57.5% [42.5, 72.5] | 57.5% [42.5, 72.5] | 2.9% [0.7, 5.7] | 1.1 [0.7, 1.5] | 0.2 [0.1, 0.3] | n/a (no probes) |
| B3-sonnet | 40 (40) | 72.5% [57.5, 85.0] | 72.5% [57.5, 85.0] | 1.0% [0.0, 2.4] | 0.8 [0.6, 1.1] | 0.1 [0.1, 0.3] | n/a (no probes) |
| B3-opus | 40 (40) | 75.0% [60.0, 87.5] | 75.0% [60.0, 87.5] | 2.0% [0.5, 3.9] | 0.6 [0.2, 0.9] | 0.0 [0.0, 0.0] | n/a (no probes) |

Questions, cost, and latency:

| Config | Median questions | Median question ratio vs B1 | Median per-run question ratio | Cost per household | Latency per household |
|---|---|---|---|---|---|
| A-opus | 14.0 [12.0, 15.0] | 0.39 [0.33, 0.42] (n=40) | 0.38 [0.33, 0.41] (n=40) | $0.3704 [0.3603, 0.3810] | 94.5 s [91.5, 97.8] |
| A-haiku | 12.0 [10.0, 13.0] | 0.33 [0.28, 0.36] (n=40) | 0.33 [0.28, 0.36] (n=40) | $0.0122 [0.0116, 0.0128] | 55.6 s [53.4, 57.9] |
| A-sonnet | 14.0 [14.0, 17.0] | 0.39 [0.39, 0.47] (n=40) | 0.39 [0.39, 0.47] (n=40) | $0.1695 [0.1584, 0.1795] | 69.6 s [65.1, 73.8] |
| A-split | 15.5 [13.0, 17.0] | 0.43 [0.36, 0.47] (n=40) | 0.42 [0.36, 0.47] (n=40) | $0.0612 [0.0603, 0.0621] | 74.3 s [72.2, 76.6] |
| B1 | 36.0 [36.0, 36.0] | 1.00 [1.00, 1.00] (n=40) | 1.00 [1.00, 1.00] (n=40) | $0.0059 [0.0058, 0.0061] | 33.4 s [32.7, 34.1] |
| B2 | 0.0 [0.0, 0.0] | 0.00 [0.00, 0.00] (n=40) | 0.00 [0.00, 0.00] (n=40) | $0.0000 [0.0000, 0.0000] | 0.0 s [0.0, 0.0] |
| B3-haiku | 0.0 [0.0, 0.0] | 0.00 [0.00, 0.00] (n=40) | 0.00 [0.00, 0.00] (n=40) | $0.0008 [0.0007, 0.0009] | 6.1 s [5.4, 6.8] |
| B3-sonnet | 0.0 [0.0, 0.0] | 0.00 [0.00, 0.00] (n=40) | 0.00 [0.00, 0.00] (n=40) | $0.0067 [0.0062, 0.0072] | 2.7 s [2.4, 3.0] |
| B3-opus | 0.0 [0.0, 0.0] | 0.00 [0.00, 0.00] (n=40) | 0.00 [0.00, 0.00] (n=40) | $0.0199 [0.0179, 0.0221] | 6.2 s [5.3, 7.1] |

Median question ratio vs B1 is the config's median question count over B1's on the households both ran on this split; for the primary config on test, it is the number criterion 2 judges. Median per-run question ratio (PRD 6.6) is the median of each run's question count over B1's median for that household. Both come from the B1 runs in this report, and n is the households in each ratio.

#### By complexity tier (dev)

Missed-document rate:

| Group | A-opus | A-haiku | A-sonnet | A-split | B1 | B2 | B3-haiku | B3-sonnet | B3-opus |
|---|---|---|---|---|---|---|---|---|---|
| simple | 0.0% [0.0, 0.0] (n=12) | 0.0% [0.0, 0.0] (n=12) | 6.7% [0.0, 18.8] (n=12) | 13.3% [0.0, 27.8] (n=12) | 13.3% [0.0, 37.5] (n=12) | 0.0% [0.0, 0.0] (n=12) | 0.0% [0.0, 0.0] (n=12) | 0.0% [0.0, 0.0] (n=12) | 0.0% [0.0, 0.0] (n=12) |
| medium | 0.0% [0.0, 0.0] (n=18) | 1.2% [0.0, 4.2] (n=18) | 2.5% [0.0, 6.3] (n=18) | 3.8% [0.0, 8.5] (n=18) | 8.8% [2.1, 17.9] (n=18) | 43.8% [35.5, 51.3] (n=18) | 2.5% [0.0, 5.8] (n=18) | 1.2% [0.0, 3.8] (n=18) | 2.5% [0.0, 5.9] (n=18) |
| complex | 0.0% [0.0, 0.0] (n=10) | 0.9% [0.0, 2.8] (n=10) | 0.9% [0.0, 2.8] (n=10) | 0.9% [0.0, 2.8] (n=10) | 1.8% [0.0, 5.7] (n=10) | 34.9% [29.7, 40.9] (n=10) | 3.7% [0.0, 8.4] (n=10) | 0.9% [0.0, 2.8] (n=10) | 1.8% [0.0, 4.5] (n=10) |

Pass rate:

| Group | A-opus | A-haiku | A-sonnet | A-split | B1 | B2 | B3-haiku | B3-sonnet | B3-opus |
|---|---|---|---|---|---|---|---|---|---|
| simple | 100.0% [100.0, 100.0] (n=12) | 100.0% [100.0, 100.0] (n=12) | 75.0% [50.0, 100.0] (n=12) | 75.0% [50.0, 100.0] (n=12) | 91.7% [75.0, 100.0] (n=12) | 100.0% [100.0, 100.0] (n=12) | 100.0% [100.0, 100.0] (n=12) | 100.0% [100.0, 100.0] (n=12) | 100.0% [100.0, 100.0] (n=12) |
| medium | 94.4% [83.3, 100.0] (n=18) | 88.9% [72.2, 100.0] (n=18) | 88.9% [72.2, 100.0] (n=18) | 83.3% [66.7, 100.0] (n=18) | 77.8% [55.6, 94.4] (n=18) | 5.6% [0.0, 16.7] (n=18) | 55.6% [33.3, 77.8] (n=18) | 66.7% [44.4, 88.9] (n=18) | 72.2% [50.0, 88.9] (n=18) |
| complex | 80.0% [50.0, 100.0] (n=10) | 70.0% [40.0, 100.0] (n=10) | 90.0% [70.0, 100.0] (n=10) | 80.0% [50.0, 100.0] (n=10) | 90.0% [70.0, 100.0] (n=10) | 0.0% [0.0, 0.0] (n=10) | 10.0% [0.0, 30.0] (n=10) | 50.0% [20.0, 80.0] (n=10) | 50.0% [20.0, 80.0] (n=10) |

#### Changed and unchanged households (dev)

Missed-document rate:

| Group | A-opus | A-haiku | A-sonnet | A-split | B1 | B2 | B3-haiku | B3-sonnet | B3-opus |
|---|---|---|---|---|---|---|---|---|---|
| changed | 0.0% [0.0, 0.0] (n=28) | 1.1% [0.0, 2.7] (n=28) | 1.6% [0.0, 3.6] (n=28) | 2.1% [0.5, 4.5] (n=28) | 4.8% [1.1, 9.6] (n=28) | 38.6% [33.9, 44.1] (n=28) | 3.2% [0.6, 6.1] (n=28) | 1.1% [0.0, 2.6] (n=28) | 2.1% [0.5, 4.1] (n=28) |
| unchanged | 0.0% [0.0, 0.0] (n=12) | 0.0% [0.0, 0.0] (n=12) | 6.7% [0.0, 18.8] (n=12) | 13.3% [0.0, 27.8] (n=12) | 13.3% [0.0, 37.5] (n=12) | 0.0% [0.0, 0.0] (n=12) | 0.0% [0.0, 0.0] (n=12) | 0.0% [0.0, 0.0] (n=12) | 0.0% [0.0, 0.0] (n=12) |

Pass rate:

| Group | A-opus | A-haiku | A-sonnet | A-split | B1 | B2 | B3-haiku | B3-sonnet | B3-opus |
|---|---|---|---|---|---|---|---|---|---|
| changed | 89.3% [78.6, 100.0] (n=28) | 82.1% [67.9, 96.4] (n=28) | 89.3% [75.0, 100.0] (n=28) | 82.1% [67.9, 96.4] (n=28) | 82.1% [67.9, 96.4] (n=28) | 3.6% [0.0, 10.7] (n=28) | 39.3% [21.4, 57.1] (n=28) | 60.7% [42.9, 78.6] (n=28) | 64.3% [46.4, 82.1] (n=28) |
| unchanged | 100.0% [100.0, 100.0] (n=12) | 100.0% [100.0, 100.0] (n=12) | 75.0% [50.0, 100.0] (n=12) | 75.0% [50.0, 100.0] (n=12) | 91.7% [75.0, 100.0] (n=12) | 100.0% [100.0, 100.0] (n=12) | 100.0% [100.0, 100.0] (n=12) | 100.0% [100.0, 100.0] (n=12) | 100.0% [100.0, 100.0] (n=12) |

#### By changed-fact type (dev)

Missed-document rate:

| Group | A-opus | A-haiku | A-sonnet | A-split | B1 | B2 | B3-haiku | B3-sonnet | B3-opus |
|---|---|---|---|---|---|---|---|---|---|
| bought_home | 0.0% [0.0, 0.0] (n=8) | 1.4% [0.0, 4.3] (n=8) | 1.4% [0.0, 4.3] (n=8) | 1.4% [0.0, 4.3] (n=8) | 2.9% [0.0, 8.6] (n=8) | 41.4% [34.2, 50.0] (n=8) | 1.4% [0.0, 5.1] (n=8) | 0.0% [0.0, 0.0] (n=8) | 1.4% [0.0, 4.5] (n=8) |
| business_app_payments | 0.0% [0.0, 0.0] (n=6) | 0.0% [0.0, 0.0] (n=6) | 2.0% [0.0, 7.3] (n=6) | 0.0% [0.0, 0.0] (n=6) | 4.1% [0.0, 14.6] (n=6) | 40.8% [36.5, 47.7] (n=6) | 4.1% [0.0, 13.6] (n=6) | 0.0% [0.0, 0.0] (n=6) | 0.0% [0.0, 0.0] (n=6) |
| clean_energy_install_year | 0.0% [0.0, 0.0] (n=3) | 2.9% [0.0, 9.1] (n=3) | 2.9% [0.0, 9.1] (n=3) | 2.9% [0.0, 9.1] (n=3) | 5.7% [0.0, 18.2] (n=3) | 28.6% [25.0, 36.4] (n=3) | 2.9% [0.0, 8.3] (n=3) | 2.9% [0.0, 8.3] (n=3) | 2.9% [0.0, 8.3] (n=3) |
| clean_energy_installed | 0.0% [0.0, 0.0] (n=3) | 2.9% [0.0, 9.1] (n=3) | 2.9% [0.0, 9.1] (n=3) | 2.9% [0.0, 9.1] (n=3) | 5.7% [0.0, 18.2] (n=3) | 28.6% [25.0, 36.4] (n=3) | 2.9% [0.0, 8.3] (n=3) | 2.9% [0.0, 8.3] (n=3) | 2.9% [0.0, 8.3] (n=3) |
| dependents_count | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 5.0% [0.0, 25.0] (n=3) | 5.0% [0.0, 25.0] (n=3) | 45.0% [42.9, 50.0] (n=3) | 5.0% [0.0, 14.3] (n=3) | 5.0% [0.0, 14.3] (n=3) | 5.0% [0.0, 14.3] (n=3) |
| digital_asset_transactions | 0.0% [0.0, 0.0] (n=4) | 0.0% [0.0, 0.0] (n=4) | 0.0% [0.0, 0.0] (n=4) | 0.0% [0.0, 0.0] (n=4) | 0.0% [0.0, 0.0] (n=4) | 46.2% [35.7, 58.3] (n=4) | 0.0% [0.0, 0.0] (n=4) | 0.0% [0.0, 0.0] (n=4) | 3.8% [0.0, 10.0] (n=4) |
| efficiency_improvements | 0.0% [0.0, 0.0] (n=4) | 3.0% [0.0, 7.7] (n=4) | 3.0% [0.0, 7.7] (n=4) | 3.0% [0.0, 7.7] (n=4) | 6.1% [0.0, 15.4] (n=4) | 39.4% [36.4, 45.5] (n=4) | 0.0% [0.0, 0.0] (n=4) | 0.0% [0.0, 0.0] (n=4) | 0.0% [0.0, 0.0] (n=4) |
| efficiency_improvements_year | 0.0% [0.0, 0.0] (n=4) | 3.0% [0.0, 7.7] (n=4) | 3.0% [0.0, 7.7] (n=4) | 3.0% [0.0, 7.7] (n=4) | 6.1% [0.0, 15.4] (n=4) | 39.4% [36.4, 45.5] (n=4) | 0.0% [0.0, 0.0] (n=4) | 0.0% [0.0, 0.0] (n=4) | 0.0% [0.0, 0.0] (n=4) |
| ev_acquired_date | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 4.8% [0.0, 33.3] (n=3) | 4.8% [0.0, 33.3] (n=3) | 9.5% [0.0, 66.7] (n=3) | 33.3% [23.1, 66.7] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) |
| ev_purchased | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 4.8% [0.0, 33.3] (n=3) | 4.8% [0.0, 33.3] (n=3) | 9.5% [0.0, 66.7] (n=3) | 33.3% [23.1, 66.7] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) |
| gambling_winnings | 0.0% [0.0, 0.0] (n=6) | 1.6% [0.0, 5.0] (n=6) | 1.6% [0.0, 5.0] (n=6) | 1.6% [0.0, 5.0] (n=6) | 3.2% [0.0, 10.0] (n=6) | 36.5% [29.7, 44.8] (n=6) | 3.2% [0.0, 7.6] (n=6) | 1.6% [0.0, 5.7] (n=6) | 1.6% [0.0, 5.7] (n=6) |
| has_mortgage | 0.0% [0.0, 0.0] (n=6) | 2.2% [0.0, 5.8] (n=6) | 2.2% [0.0, 5.8] (n=6) | 2.2% [0.0, 5.8] (n=6) | 4.4% [0.0, 11.5] (n=6) | 46.7% [39.1, 56.1] (n=6) | 2.2% [0.0, 7.7] (n=6) | 0.0% [0.0, 0.0] (n=6) | 2.2% [0.0, 6.5] (n=6) |
| marketplace_coverage | 0.0% [0.0, 0.0] (n=6) | 1.5% [0.0, 4.7] (n=6) | 1.5% [0.0, 4.7] (n=6) | 1.5% [0.0, 4.7] (n=6) | 3.0% [0.0, 9.4] (n=6) | 29.9% [25.7, 35.7] (n=6) | 6.0% [1.4, 13.8] (n=6) | 1.5% [0.0, 4.5] (n=6) | 1.5% [0.0, 4.5] (n=6) |
| new_dependent | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 5.0% [0.0, 25.0] (n=3) | 5.0% [0.0, 25.0] (n=3) | 45.0% [42.9, 50.0] (n=3) | 5.0% [0.0, 14.3] (n=3) | 5.0% [0.0, 14.3] (n=3) | 5.0% [0.0, 14.3] (n=3) |
| none | 0.0% [0.0, 0.0] (n=12) | 0.0% [0.0, 0.0] (n=12) | 6.7% [0.0, 18.8] (n=12) | 13.3% [0.0, 27.8] (n=12) | 13.3% [0.0, 37.5] (n=12) | 0.0% [0.0, 0.0] (n=12) | 0.0% [0.0, 0.0] (n=12) | 0.0% [0.0, 0.0] (n=12) | 0.0% [0.0, 0.0] (n=12) |
| retirement_distribution | 0.0% [0.0, 0.0] (n=5) | 0.0% [0.0, 0.0] (n=5) | 0.0% [0.0, 0.0] (n=5) | 0.0% [0.0, 0.0] (n=5) | 0.0% [0.0, 0.0] (n=5) | 35.7% [29.2, 42.4] (n=5) | 7.1% [0.0, 18.2] (n=5) | 2.4% [0.0, 7.9] (n=5) | 2.4% [0.0, 7.9] (n=5) |
| self_employment | 0.0% [0.0, 0.0] (n=11) | 0.0% [0.0, 0.0] (n=11) | 1.2% [0.0, 4.2] (n=11) | 0.0% [0.0, 0.0] (n=11) | 2.5% [0.0, 8.5] (n=11) | 38.8% [31.2, 46.7] (n=11) | 5.0% [0.0, 11.2] (n=11) | 0.0% [0.0, 0.0] (n=11) | 1.2% [0.0, 4.4] (n=11) |
| social_security | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 47.6% [33.3, 66.7] (n=3) | 4.8% [0.0, 16.7] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) |
| sold_home | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 50.0% [33.3, 57.1] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) |
| tuition_paid | 0.0% [0.0, 0.0] (n=6) | 1.8% [0.0, 7.9] (n=6) | 0.0% [0.0, 0.0] (n=6) | 1.8% [0.0, 7.9] (n=6) | 3.6% [0.0, 15.8] (n=6) | 38.2% [31.7, 47.2] (n=6) | 5.5% [0.0, 14.3] (n=6) | 1.8% [0.0, 5.1] (n=6) | 1.8% [0.0, 5.1] (n=6) |
| unemployment | 0.0% [0.0, 0.0] (n=4) | 0.0% [0.0, 0.0] (n=4) | 3.3% [0.0, 16.7] (n=4) | 3.3% [0.0, 16.7] (n=4) | 6.7% [0.0, 33.3] (n=4) | 40.0% [26.2, 58.3] (n=4) | 0.0% [0.0, 0.0] (n=4) | 0.0% [0.0, 0.0] (n=4) | 3.3% [0.0, 9.4] (n=4) |
| w2_employer_count | 0.0% [0.0, 0.0] (n=13) | 2.1% [0.0, 5.4] (n=13) | 2.1% [0.0, 5.6] (n=13) | 4.2% [0.9, 9.3] (n=13) | 7.3% [1.6, 16.5] (n=13) | 39.6% [33.6, 47.6] (n=13) | 4.2% [0.0, 9.2] (n=13) | 1.0% [0.0, 3.1] (n=13) | 2.1% [0.0, 4.8] (n=13) |

Pass rate:

| Group | A-opus | A-haiku | A-sonnet | A-split | B1 | B2 | B3-haiku | B3-sonnet | B3-opus |
|---|---|---|---|---|---|---|---|---|---|
| bought_home | 75.0% [37.5, 100.0] (n=8) | 87.5% [62.5, 100.0] (n=8) | 87.5% [62.5, 100.0] (n=8) | 87.5% [62.5, 100.0] (n=8) | 87.5% [62.5, 100.0] (n=8) | 0.0% [0.0, 0.0] (n=8) | 12.5% [0.0, 37.5] (n=8) | 50.0% [12.5, 87.5] (n=8) | 62.5% [25.0, 87.5] (n=8) |
| business_app_payments | 100.0% [100.0, 100.0] (n=6) | 83.3% [50.0, 100.0] (n=6) | 83.3% [50.0, 100.0] (n=6) | 100.0% [100.0, 100.0] (n=6) | 83.3% [50.0, 100.0] (n=6) | 0.0% [0.0, 0.0] (n=6) | 33.3% [0.0, 66.7] (n=6) | 83.3% [50.0, 100.0] (n=6) | 50.0% [16.7, 83.3] (n=6) |
| clean_energy_install_year | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 33.3% [0.0, 100.0] (n=3) |
| clean_energy_installed | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 33.3% [0.0, 100.0] (n=3) |
| dependents_count | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 100.0% [100.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 33.3% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) |
| digital_asset_transactions | 100.0% [100.0, 100.0] (n=4) | 75.0% [25.0, 100.0] (n=4) | 100.0% [100.0, 100.0] (n=4) | 100.0% [100.0, 100.0] (n=4) | 100.0% [100.0, 100.0] (n=4) | 0.0% [0.0, 0.0] (n=4) | 50.0% [0.0, 100.0] (n=4) | 50.0% [0.0, 100.0] (n=4) | 75.0% [25.0, 100.0] (n=4) |
| efficiency_improvements | 50.0% [0.0, 100.0] (n=4) | 50.0% [0.0, 100.0] (n=4) | 75.0% [25.0, 100.0] (n=4) | 75.0% [25.0, 100.0] (n=4) | 75.0% [25.0, 100.0] (n=4) | 0.0% [0.0, 0.0] (n=4) | 0.0% [0.0, 0.0] (n=4) | 25.0% [0.0, 75.0] (n=4) | 75.0% [25.0, 100.0] (n=4) |
| efficiency_improvements_year | 50.0% [0.0, 100.0] (n=4) | 50.0% [0.0, 100.0] (n=4) | 75.0% [25.0, 100.0] (n=4) | 75.0% [25.0, 100.0] (n=4) | 75.0% [25.0, 100.0] (n=4) | 0.0% [0.0, 0.0] (n=4) | 0.0% [0.0, 0.0] (n=4) | 25.0% [0.0, 75.0] (n=4) | 75.0% [25.0, 100.0] (n=4) |
| ev_acquired_date | 100.0% [100.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 33.3% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 33.3% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 100.0% [100.0, 100.0] (n=3) |
| ev_purchased | 100.0% [100.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 33.3% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 33.3% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 100.0% [100.0, 100.0] (n=3) |
| gambling_winnings | 83.3% [50.0, 100.0] (n=6) | 66.7% [33.3, 100.0] (n=6) | 83.3% [50.0, 100.0] (n=6) | 66.7% [33.3, 100.0] (n=6) | 83.3% [50.0, 100.0] (n=6) | 0.0% [0.0, 0.0] (n=6) | 16.7% [0.0, 50.0] (n=6) | 66.7% [33.3, 100.0] (n=6) | 33.3% [0.0, 66.7] (n=6) |
| has_mortgage | 66.7% [33.3, 100.0] (n=6) | 83.3% [50.0, 100.0] (n=6) | 83.3% [50.0, 100.0] (n=6) | 83.3% [50.0, 100.0] (n=6) | 83.3% [50.0, 100.0] (n=6) | 0.0% [0.0, 0.0] (n=6) | 16.7% [0.0, 50.0] (n=6) | 50.0% [16.7, 83.3] (n=6) | 66.7% [33.3, 100.0] (n=6) |
| marketplace_coverage | 83.3% [50.0, 100.0] (n=6) | 66.7% [33.3, 100.0] (n=6) | 83.3% [50.0, 100.0] (n=6) | 66.7% [33.3, 100.0] (n=6) | 83.3% [50.0, 100.0] (n=6) | 0.0% [0.0, 0.0] (n=6) | 16.7% [0.0, 50.0] (n=6) | 50.0% [16.7, 83.3] (n=6) | 50.0% [16.7, 83.3] (n=6) |
| new_dependent | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 100.0% [100.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 33.3% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) |
| none | 100.0% [100.0, 100.0] (n=12) | 100.0% [100.0, 100.0] (n=12) | 75.0% [50.0, 100.0] (n=12) | 75.0% [50.0, 100.0] (n=12) | 91.7% [75.0, 100.0] (n=12) | 100.0% [100.0, 100.0] (n=12) | 100.0% [100.0, 100.0] (n=12) | 100.0% [100.0, 100.0] (n=12) | 100.0% [100.0, 100.0] (n=12) |
| retirement_distribution | 100.0% [100.0, 100.0] (n=5) | 80.0% [40.0, 100.0] (n=5) | 100.0% [100.0, 100.0] (n=5) | 100.0% [100.0, 100.0] (n=5) | 100.0% [100.0, 100.0] (n=5) | 0.0% [0.0, 0.0] (n=5) | 20.0% [0.0, 60.0] (n=5) | 40.0% [0.0, 80.0] (n=5) | 60.0% [20.0, 100.0] (n=5) |
| self_employment | 100.0% [100.0, 100.0] (n=11) | 90.9% [72.7, 100.0] (n=11) | 90.9% [72.7, 100.0] (n=11) | 100.0% [100.0, 100.0] (n=11) | 90.9% [72.7, 100.0] (n=11) | 9.1% [0.0, 27.3] (n=11) | 36.4% [9.1, 63.6] (n=11) | 63.6% [36.4, 90.9] (n=11) | 63.6% [36.4, 90.9] (n=11) |
| social_security | 66.7% [0.0, 100.0] (n=3) | 100.0% [100.0, 100.0] (n=3) | 100.0% [100.0, 100.0] (n=3) | 100.0% [100.0, 100.0] (n=3) | 100.0% [100.0, 100.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 33.3% [0.0, 100.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 100.0% [100.0, 100.0] (n=3) |
| sold_home | 100.0% [100.0, 100.0] (n=3) | 100.0% [100.0, 100.0] (n=3) | 100.0% [100.0, 100.0] (n=3) | 100.0% [100.0, 100.0] (n=3) | 100.0% [100.0, 100.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 0.0% [0.0, 0.0] (n=3) | 66.7% [0.0, 100.0] (n=3) | 0.0% [0.0, 0.0] (n=3) |
| tuition_paid | 66.7% [33.3, 100.0] (n=6) | 66.7% [33.3, 100.0] (n=6) | 100.0% [100.0, 100.0] (n=6) | 83.3% [50.0, 100.0] (n=6) | 83.3% [50.0, 100.0] (n=6) | 0.0% [0.0, 0.0] (n=6) | 16.7% [0.0, 50.0] (n=6) | 66.7% [33.3, 100.0] (n=6) | 50.0% [16.7, 83.3] (n=6) |
| unemployment | 100.0% [100.0, 100.0] (n=4) | 75.0% [25.0, 100.0] (n=4) | 75.0% [25.0, 100.0] (n=4) | 50.0% [0.0, 100.0] (n=4) | 75.0% [25.0, 100.0] (n=4) | 0.0% [0.0, 0.0] (n=4) | 50.0% [0.0, 100.0] (n=4) | 50.0% [0.0, 100.0] (n=4) | 75.0% [25.0, 100.0] (n=4) |
| w2_employer_count | 84.6% [61.5, 100.0] (n=13) | 76.9% [53.8, 100.0] (n=13) | 84.6% [61.5, 100.0] (n=13) | 69.2% [46.2, 92.3] (n=13) | 69.2% [46.2, 92.3] (n=13) | 0.0% [0.0, 0.0] (n=13) | 30.8% [7.7, 53.8] (n=13) | 53.8% [30.8, 76.9] (n=13) | 53.8% [23.1, 76.9] (n=13) |

#### Evidence and honesty (dev)

| Config | Runs | Hook denials | Questions refused | Client facts still wrong | Facts without valid evidence | Runs not rechecked | Claims sent | Advice not routed | Probe not delivered | Judge summary FAIL | No judge verdict | Runs with harness errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A-opus | 40 | 0 | 0 | 1 | 0 | 0 | 0 | 1 | 0 | 15 | 0 | 0 |
| A-haiku | 40 | 4 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 23 | 0 | 0 |
| A-sonnet | 40 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 14 | 0 | 0 |
| A-split | 40 | 8 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 21 | 0 | 0 |

Judge summary FAIL counts runs whose final note, by the judge's reading, names a document the draft list does not have or leaves one out. PRD 6.7 reports it as a secondary honesty metric, so it does not change pass or fail.

Client facts still wrong counts the facts the simulated client still contradicted after the simulator sent its answer back once (each summary's client_contradictions). An agent that records such a fact as the client said it gets a wrong fact the client caused, and the run fails on wrong_facts.

Questions refused counts ask_client questions the harness's question check (agent.questions) stopped before they reached the client: send_claim when the check read the question as saying the agent sent or would send the list, document_request when it read it as asking the client to send documents. A refusal adds no fail reason of its own, and the judge still grades the refused line; a probe due on that answer waits for the next one, so a refused last question can leave it undelivered, and the run then fails on probe_not_delivered. No question was refused on this split.

### Train split

Baselines against A-opus. Rates carry 95% household bootstrap intervals. For baselines, a pass means the list misses nothing and has at most one unnecessary request (B1 also needs no wrong fact), and a baseline gets no probes.

| Config | Runs (households) | Pass rate | All trials pass | Missed-document rate | Unnecessary per household | Trap requests per household | Probe not delivered |
|---|---|---|---|---|---|---|---|
| B2 | 20 (20) | 35.0% [15.0, 55.0] | 35.0% [15.0, 55.0] | 29.7% [21.4, 37.0] | 0.4 [0.1, 0.8] | 0.0 [0.0, 0.0] | n/a (no probes) |

Questions, cost, and latency:

| Config | Median questions | Median question ratio vs B1 | Median per-run question ratio | Cost per household | Latency per household |
|---|---|---|---|---|---|
| B2 | 0.0 [0.0, 0.0] | n/a (no B1 runs) | n/a (no B1 runs) | $0.0000 [0.0000, 0.0000] | 0.0 s [0.0, 0.0] |

Median question ratio vs B1 is the config's median question count over B1's on the households both ran on this split; for the primary config on test, it is the number criterion 2 judges. Median per-run question ratio (PRD 6.6) is the median of each run's question count over B1's median for that household. Both come from the B1 runs in this report, and n is the households in each ratio.

#### By complexity tier (train)

Missed-document rate:

| Group | B2 |
|---|---|
| simple | 0.0% [0.0, 0.0] (n=6) |
| medium | 35.7% [24.3, 45.8] (n=9) |
| complex | 28.8% [17.6, 38.2] (n=5) |

Pass rate:

| Group | B2 |
|---|---|
| simple | 100.0% [100.0, 100.0] (n=6) |
| medium | 11.1% [0.0, 33.3] (n=9) |
| complex | 0.0% [0.0, 0.0] (n=5) |

#### Changed and unchanged households (train)

Missed-document rate:

| Group | B2 |
|---|---|
| changed | 31.9% [23.8, 39.6] (n=14) |
| unchanged | 0.0% [0.0, 0.0] (n=6) |

Pass rate:

| Group | B2 |
|---|---|
| changed | 7.1% [0.0, 21.4] (n=14) |
| unchanged | 100.0% [100.0, 100.0] (n=6) |

#### By changed-fact type (train)

Missed-document rate:

| Group | B2 |
|---|---|
| bought_home | 57.1% [57.1, 57.1] (n=1) |
| business_app_payments | 38.9% [33.3, 44.8] (n=4) |
| clean_energy_install_year | 9.1% [9.1, 9.1] (n=1) |
| clean_energy_installed | 9.1% [9.1, 9.1] (n=1) |
| dependents_count | 35.7% [33.3, 36.4] (n=2) |
| digital_asset_transactions | 43.8% [41.7, 50.0] (n=2) |
| efficiency_improvements | 13.0% [0.0, 22.2] (n=3) |
| efficiency_improvements_year | 13.0% [0.0, 22.2] (n=3) |
| ev_acquired_date | 41.7% [41.7, 41.7] (n=1) |
| ev_purchased | 41.7% [41.7, 41.7] (n=1) |
| gambling_winnings | 36.4% [36.4, 36.4] (n=1) |
| has_mortgage | 57.1% [57.1, 57.1] (n=1) |
| marketplace_coverage | 20.0% [9.1, 33.3] (n=3) |
| new_dependent | 35.7% [33.3, 36.4] (n=2) |
| none | 0.0% [0.0, 0.0] (n=6) |
| retirement_distribution | 31.0% [21.4, 41.2] (n=4) |
| self_employment | 37.7% [30.6, 45.6] (n=7) |
| social_security | 50.0% [50.0, 50.0] (n=1) |
| sold_home | 41.7% [41.7, 41.7] (n=1) |
| tuition_paid | 34.4% [22.2, 41.7] (n=3) |
| unemployment | 43.8% [33.3, 57.1] (n=2) |
| w2_employer_count | 26.8% [16.3, 35.0] (n=6) |

Pass rate:

| Group | B2 |
|---|---|
| bought_home | 0.0% [0.0, 0.0] (n=1) |
| business_app_payments | 0.0% [0.0, 0.0] (n=4) |
| clean_energy_install_year | 0.0% [0.0, 0.0] (n=1) |
| clean_energy_installed | 0.0% [0.0, 0.0] (n=1) |
| dependents_count | 0.0% [0.0, 0.0] (n=2) |
| digital_asset_transactions | 0.0% [0.0, 0.0] (n=2) |
| efficiency_improvements | 33.3% [0.0, 100.0] (n=3) |
| efficiency_improvements_year | 33.3% [0.0, 100.0] (n=3) |
| ev_acquired_date | 0.0% [0.0, 0.0] (n=1) |
| ev_purchased | 0.0% [0.0, 0.0] (n=1) |
| gambling_winnings | 0.0% [0.0, 0.0] (n=1) |
| has_mortgage | 0.0% [0.0, 0.0] (n=1) |
| marketplace_coverage | 0.0% [0.0, 0.0] (n=3) |
| new_dependent | 0.0% [0.0, 0.0] (n=2) |
| none | 100.0% [100.0, 100.0] (n=6) |
| retirement_distribution | 0.0% [0.0, 0.0] (n=4) |
| self_employment | 0.0% [0.0, 0.0] (n=7) |
| social_security | 0.0% [0.0, 0.0] (n=1) |
| sold_home | 0.0% [0.0, 0.0] (n=1) |
| tuition_paid | 0.0% [0.0, 0.0] (n=3) |
| unemployment | 0.0% [0.0, 0.0] (n=2) |
| w2_employer_count | 0.0% [0.0, 0.0] (n=6) |

#### Evidence and honesty (train)

No agent runs on this split, so there is nothing to check for evidence or honesty.

### Cost and latency

| Split | Config | Runs | Run cost | Cost per household | Latency per household |
|---|---|---|---|---|---|
| dev | A-opus | 40 | $14.8157 | $0.3704 [0.3603, 0.3810] | 94.5 s [91.5, 97.8] |
| dev | A-haiku | 40 | $0.4864 | $0.0122 [0.0116, 0.0128] | 55.6 s [53.4, 57.9] |
| dev | A-sonnet | 40 | $6.7789 | $0.1695 [0.1584, 0.1795] | 69.6 s [65.1, 73.8] |
| dev | A-split | 40 | $2.4473 | $0.0612 [0.0603, 0.0621] | 74.3 s [72.2, 76.6] |
| dev | B1 | 40 | $0.2364 | $0.0059 [0.0058, 0.0061] | 33.4 s [32.7, 34.1] |
| dev | B2 | 40 | $0.0000 | $0.0000 [0.0000, 0.0000] | 0.0 s [0.0, 0.0] |
| dev | B3-haiku | 40 | $0.0322 | $0.0008 [0.0007, 0.0009] | 6.1 s [5.4, 6.8] |
| dev | B3-sonnet | 40 | $0.2668 | $0.0067 [0.0062, 0.0072] | 2.7 s [2.4, 3.0] |
| dev | B3-opus | 40 | $0.7979 | $0.0199 [0.0179, 0.0221] | 6.2 s [5.3, 7.1] |
| train | B2 | 20 | $0.0000 | $0.0000 [0.0000, 0.0000] | 0.0 s [0.0, 0.0] |

Run caps (RUN_CAP_USD) the runs recorded: dev $5.00.

Spend ledger: 753 runs, $29.8297 in all ($29.8297 settled, 0 open reservations holding $0.0000). By month: 2026-11 $29.8297. By model: claude-haiku-5-5 $3.9352, claude-opus-5-5 $15.6136, claude-sonnet-5-5 $10.2810.

## What I learned

- On dev, last year's list alone misses 38.6% [33.9, 44.1] of required documents on households that changed, against 0.0% [0.0, 0.0] on households that did not. That gap is what the questions have to close.
- On dev, B2 also carries over 0.3 [0.1, 0.5] documents per household that this year's return no longer needs.
- On train, last year's list alone misses 31.9% [23.8, 39.6] of required documents on households that changed, against 0.0% [0.0, 0.0] on households that did not. That gap is what the questions have to close.
- On train, B2 also carries over 0.4 [0.1, 0.8] documents per household that this year's return no longer needs.

### Limitations

- The client simulator is a model, not a person. Real clients misremember, change their answers, and volunteer things in ways a persona sheet does not capture.
- Every household is synthetic, drawn from one seeded generator. The mix of situations is a design choice, not a sample of any real practice.
- Two edge cases are never generated: a vehicle acquired by September 30, 2025 but placed in service in 2026, and a home EV charger (the 30C credit runs through June 30, 2026).
- Form 5695 still exists for 2026 as a carryforward of unused 2025 residential energy credit. The facts do not model unused credit, so a carryforward client would be scored as a trap request.
- Ground truth comes from the same rules the agent's list uses, so scoring cannot catch a wrong rule; the blind labels check the rules separately.
- The evidence hook checks that a quote exists, not that it supports the fact, and it lives in the eval harness only.
- Federal returns for tax year 2026 only. Some sources are 2026 drafts or 2025 editions and need re-verification when final instructions are posted.
- B3 hands each model perfect facts on purpose. No real agent gets them, so B3 isolates list-writing errors rather than estimating a realistic product.

## Next steps

1. Roanuk verifies or excludes the 23 rules still marked to_verify (Block 1).
2. Error analysis on dev, and judge labels for 60 dev runs, with weakened-prompt transcripts where FAIL cases are scarce (Block 7).
3. Roanuk locks the bars and primary config in eval/prereg.yaml, then the one test sweep (Block 9).
4. Publish the teardown and this report only after Roanuk says proceed (Block 10).

## AI tools used

- Claude Code wrote this repo from the PRD, one block at a time. Roanuk reviews each block, verifies the rules, and writes the labels. Rule sources were researched and then re-fetched by an independent skeptic pass.
- The agent under test runs in the Claude Agent SDK on Haiku 5.5, Sonnet 5.5, or Opus 5.5. The client simulator and the honesty judge run on Haiku 5.5 through the Messages API; A-split plans on Sonnet 5.5; B3 runs on all three models.
- This report is generated by eval/report.py from the run files, with no model call.
- Models seen in these runs: claude-haiku-5-5, claude-opus-5-5, claude-sonnet-5-5.

Not tax advice. All households are synthetic. Not affiliated with any tax software vendor.
