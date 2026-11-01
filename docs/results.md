# Results: What Should My Tax Agent Ask?

Generated on 2026-10-31 by `uv run python -m eval.report` from 60 run summaries and 60 scores in eval/runs/. Intervals: household bootstrap, 10000 draws, seed 7, level 0.95. Preregistration: locked_on null, primary_config null.

## Executive summary

**B2 baseline only.** Every number in this report comes from baseline B2 (last year's documents, no questions) on the dev and train splits. No agent, B1, or B3 run has been scored yet. It was generated offline, before any live model run: the spend ledger is empty. The test split has not been touched.

This project measures how many questions an AI tax intake agent asks and which documents it misses. The agent only learns facts; code turns recorded facts into the document request list through a rules table with an official source for every rule, so every miss traces to a fact or a rule. All households are synthetic.

- Scored runs: 60 (dev 40, train 20).
- Success criteria (the 8 in the PRD plus the judge bars): 0 met, 0 not met, 9 not measured yet. The table is at the top of Results.
- B2 on dev: pass rate 32.5% [17.5, 47.5], missed-document rate 35.8% [30.8, 40.6], over 40 runs.

Not run yet:

- No dev runs yet for A-haiku, A-sonnet, A-opus, A-split, B1, B3-haiku, B3-sonnet, and B3-opus.
- The test split has not run: eval/prereg.yaml is not locked and has no primary_config.
- The honesty judge has not graded any run, and it is not validated yet.
- The live simulator fidelity check has not run.
- No model call has been made: the spend ledger is empty.

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
| verified | 0 |
| to_verify | 33 |
| excluded | 0 |

33 rules: 30 document requests and 3 traps for credits that ended. Every rule cites an official source; source versions: 3 draft_instructions, 6 final_instructions, 24 guidance. Train and dev scoring counts verified and to_verify rules; test scoring counts verified rules only. Under the current statuses, 40 of 40 test households depend on a rule or trap that is not verified and would drop out of test scoring.

### Label agreement

30 of 30 households labeled; 190 disagreements with the rules and 0 notes (run uv run python -m generator.labels compare).

Compare rows settled in data/labels/blind_labels_settlements.csv: 78 are label items and the rule documents they name in other words (39 label items); 15 were checked against the cited source, and the rules stand; 2 ask for something the household's facts don't call for; 95 are documents, or parts of a count, for facts that did not change, which the labels leave out, so the labels don't check those rule answers. Every disagreement is logged.

Judge labels: 0 of 60 dev runs labeled in data/labels/judge_labels.csv.

### Simulator fidelity

Not run yet: docs/fidelity/ does not exist. The live check (uv run python -m sim.fidelity) needs the API key.

## Experiments

### Baselines

- Goal: Reference points for the question count and the list.
- What was tried: B1 asks every fact key through the simulator; B2 sends last year's list without questions; B3 hands each model the true facts and lets it write the list, with no tools and no rules table.
- Method: PRD 6.5. B1 and B3 are scored like agent runs on the list and facts; B3 items map to rules through aliases.
- Findings: B2 on dev: pass rate 32.5% [17.5, 47.5], missed-document rate 35.8% [30.8, 40.6] (40 runs). B2 on train: pass rate 35.0% [15.0, 55.0], missed-document rate 29.7% [21.4, 37.0] (20 runs).

### Single-model agents

- Goal: Does the agent learn the facts that matter while asking fewer questions?
- What was tried: The same harness and prompt on Haiku 5.5, Sonnet 5.5, and Opus 5.5.
- Method: PRD 6.2: Claude Agent SDK harness, no built-in tools, the evidence hook, the client simulator, and deterministic scoring.
- Findings: Not run yet.

### Per-step models

- Goal: Can a Sonnet plan with a Haiku conversation match Sonnet's quality for less?
- What was tried: A-split: one Sonnet 5.5 call writes a question plan, and Haiku 5.5 runs the conversation.
- Method: Compared on dev with the single-model configs (Block 8).
- Findings: Not run yet.

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
| 4 | No agent run records a fact without valid evidence or claims the list was sent | zero such runs | not measured yet | No agent runs on test yet. |
| 5 | The primary config costs at most $0.10 per household at list price; cost is reported for every config | at most $0.10 per household | not measured yet | eval/prereg.yaml is not locked (locked_on is null), so the test sweep has not run. |
| 6 | Every rule cites an official source and is verified by Roanuk before it counts in test scoring; blind labels for 30 households match the rules or each disagreement is settled and logged | every rule sourced and verified or excluded; 30 labels, disagreements settled | not measured yet | Every rule has a source URL, but 33 of 33 rules are still to_verify. The labels list only what changed, so 95 rule answers, or parts of a W-2 count, for facts that did not change are checked by rule verification alone. |
| 7 | pytest passes with network sockets disabled, and mutation testing of the scorer kills at least 90% of mutants | pytest passes; at least 90% of mutants killed | not measured yet | The report does not run the tests; run uv run pytest and uv run mutmut run. Last recorded in DECISIONS.md: 1065 of 1067 scorer mutants killed (99.8%). |
| 8 | A teardown page and a results report are published, including the findings that went against the thesis | Roanuk says proceed, then publish | not measured yet | Nothing is published. Publishing waits for Roanuk's explicit proceed (Block 10). |
| J | The honesty judge meets its preregistered bars on the final 24 labeled dev runs (PRD 6.7) | TPR at least 90.0% and TNR at least 90.0% per question | not measured yet | 0 of 60 dev runs labeled in data/labels/judge_labels.csv. |

### Dev split

Baselines against the primary config (not chosen yet). Rates carry 95% household bootstrap intervals. For baselines, a pass means the list misses nothing and has at most one unnecessary request (B1 also needs no wrong fact), and a baseline gets no probes.

| Config | Runs (households) | Pass rate | All trials pass | Missed-document rate | Unnecessary per household | Trap requests per household | Probe not delivered |
|---|---|---|---|---|---|---|---|
| B2 | 40 (40) | 32.5% [17.5, 47.5] | 32.5% [17.5, 47.5] | 35.8% [30.8, 40.6] | 0.3 [0.1, 0.5] | 0.0 [0.0, 0.0] | n/a (no probes) |

Questions, cost, and latency:

| Config | Median questions | Median question ratio vs B1 | Median per-run question ratio | Cost per household | Latency per household |
|---|---|---|---|---|---|
| B2 | 0.0 [0.0, 0.0] | n/a (no B1 runs) | n/a (no B1 runs) | $0.0000 [0.0000, 0.0000] | 0.0 s [0.0, 0.0] |

Median question ratio vs B1 is the config's median question count over B1's on the households both ran on this split; for the primary config on test, it is the number criterion 2 judges. Median per-run question ratio (PRD 6.6) is the median of each run's question count over B1's median for that household. Both come from the B1 runs in this report, and n is the households in each ratio.

#### By complexity tier (dev)

Missed-document rate:

| Group | B2 |
|---|---|
| simple | 0.0% [0.0, 0.0] (n=12) |
| medium | 43.8% [35.5, 51.3] (n=18) |
| complex | 34.9% [29.7, 40.9] (n=10) |

Pass rate:

| Group | B2 |
|---|---|
| simple | 100.0% [100.0, 100.0] (n=12) |
| medium | 5.6% [0.0, 16.7] (n=18) |
| complex | 0.0% [0.0, 0.0] (n=10) |

#### Changed and unchanged households (dev)

Missed-document rate:

| Group | B2 |
|---|---|
| changed | 38.6% [33.9, 44.1] (n=28) |
| unchanged | 0.0% [0.0, 0.0] (n=12) |

Pass rate:

| Group | B2 |
|---|---|
| changed | 3.6% [0.0, 10.7] (n=28) |
| unchanged | 100.0% [100.0, 100.0] (n=12) |

#### By changed-fact type (dev)

Missed-document rate:

| Group | B2 |
|---|---|
| bought_home | 41.4% [34.2, 50.0] (n=8) |
| business_app_payments | 40.8% [36.5, 47.7] (n=6) |
| clean_energy_install_year | 28.6% [25.0, 36.4] (n=3) |
| clean_energy_installed | 28.6% [25.0, 36.4] (n=3) |
| dependents_count | 45.0% [42.9, 50.0] (n=3) |
| digital_asset_transactions | 46.2% [35.7, 58.3] (n=4) |
| efficiency_improvements | 39.4% [36.4, 45.5] (n=4) |
| efficiency_improvements_year | 39.4% [36.4, 45.5] (n=4) |
| ev_acquired_date | 33.3% [23.1, 66.7] (n=3) |
| ev_purchased | 33.3% [23.1, 66.7] (n=3) |
| gambling_winnings | 36.5% [29.7, 44.8] (n=6) |
| has_mortgage | 46.7% [39.1, 56.1] (n=6) |
| marketplace_coverage | 29.9% [25.7, 35.7] (n=6) |
| new_dependent | 45.0% [42.9, 50.0] (n=3) |
| none | 0.0% [0.0, 0.0] (n=12) |
| retirement_distribution | 35.7% [29.2, 42.4] (n=5) |
| self_employment | 38.8% [31.2, 46.7] (n=11) |
| social_security | 47.6% [33.3, 66.7] (n=3) |
| sold_home | 50.0% [33.3, 57.1] (n=3) |
| tuition_paid | 38.2% [31.7, 47.2] (n=6) |
| unemployment | 40.0% [26.2, 58.3] (n=4) |
| w2_employer_count | 39.6% [33.6, 47.6] (n=13) |

Pass rate:

| Group | B2 |
|---|---|
| bought_home | 0.0% [0.0, 0.0] (n=8) |
| business_app_payments | 0.0% [0.0, 0.0] (n=6) |
| clean_energy_install_year | 0.0% [0.0, 0.0] (n=3) |
| clean_energy_installed | 0.0% [0.0, 0.0] (n=3) |
| dependents_count | 0.0% [0.0, 0.0] (n=3) |
| digital_asset_transactions | 0.0% [0.0, 0.0] (n=4) |
| efficiency_improvements | 0.0% [0.0, 0.0] (n=4) |
| efficiency_improvements_year | 0.0% [0.0, 0.0] (n=4) |
| ev_acquired_date | 0.0% [0.0, 0.0] (n=3) |
| ev_purchased | 0.0% [0.0, 0.0] (n=3) |
| gambling_winnings | 0.0% [0.0, 0.0] (n=6) |
| has_mortgage | 0.0% [0.0, 0.0] (n=6) |
| marketplace_coverage | 0.0% [0.0, 0.0] (n=6) |
| new_dependent | 0.0% [0.0, 0.0] (n=3) |
| none | 100.0% [100.0, 100.0] (n=12) |
| retirement_distribution | 0.0% [0.0, 0.0] (n=5) |
| self_employment | 9.1% [0.0, 27.3] (n=11) |
| social_security | 0.0% [0.0, 0.0] (n=3) |
| sold_home | 0.0% [0.0, 0.0] (n=3) |
| tuition_paid | 0.0% [0.0, 0.0] (n=6) |
| unemployment | 0.0% [0.0, 0.0] (n=4) |
| w2_employer_count | 0.0% [0.0, 0.0] (n=13) |

#### Evidence and honesty (dev)

No agent runs on this split, so there is nothing to check for evidence or honesty.

### Train split

Baselines against the primary config (not chosen yet). Rates carry 95% household bootstrap intervals. For baselines, a pass means the list misses nothing and has at most one unnecessary request (B1 also needs no wrong fact), and a baseline gets no probes.

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
| dev | B2 | 40 | $0.0000 | $0.0000 [0.0000, 0.0000] | 0.0 s [0.0, 0.0] |
| train | B2 | 20 | $0.0000 | $0.0000 [0.0000, 0.0000] | 0.0 s [0.0, 0.0] |

Spend ledger: empty. No model call has been made through the harness, so every cost above is zero.

## What I learned

- On dev, last year's list alone misses 38.6% [33.9, 44.1] of required documents on households that changed, against 0.0% [0.0, 0.0] on households that did not. That gap is what the questions have to close.
- On dev, B2 also carries over 0.3 [0.1, 0.5] documents per household that this year's return no longer needs.
- On train, last year's list alone misses 31.9% [23.8, 39.6] of required documents on households that changed, against 0.0% [0.0, 0.0] on households that did not. That gap is what the questions have to close.
- On train, B2 also carries over 0.4 [0.1, 0.8] documents per household that this year's return no longer needs.
- Nothing yet about the agent itself: no agent run exists.

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

1. Roanuk verifies or excludes the 33 rules still marked to_verify (Block 1).
2. Run the live simulator fidelity check (28 households, about $0.27), read 30 answers for leaks and the Facts that ended table (Block 4).
3. Smoke test: 10 dev households on Haiku and Sonnet, 1 trial, under $2 (Block 6).
4. Error analysis on dev, and judge labels for 60 dev runs, with weakened-prompt transcripts where FAIL cases are scarce (Block 7).
5. Run A-split against the single-model configs on dev and propose a primary config (Block 8).
6. Roanuk locks the bars and primary config in eval/prereg.yaml, then the one test sweep (Block 9).
7. Publish the teardown and this report only after Roanuk says proceed (Block 10).

## AI tools used

- Claude Code wrote this repo from the PRD, one block at a time. Roanuk reviews each block, verifies the rules, and writes the labels. Rule sources were researched and then re-fetched by an independent skeptic pass.
- The agent under test runs in the Claude Agent SDK on Haiku 5.5, Sonnet 5.5, or Opus 5.5. The client simulator and the honesty judge run on Haiku 5.5 through the Messages API; A-split plans on Sonnet 5.5; B3 runs on all three models.
- This report is generated by eval/report.py from the run files, with no model call.
- Models seen in these runs: none yet.

Not tax advice. All households are synthetic. Not affiliated with any tax software vendor.
