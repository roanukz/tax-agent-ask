# Sample traces

Run traces live in `eval/runs/`, which is gitignored. After the live runs, a small sample is copied here so readers can see what one intake looks like end to end. None have been copied here yet, though the live runs are done: the first ran on train and dev on November 1, 2026, and the test run from November 2 to 4.

Each sample is a pair of files named by run ID, `{split}-{household}-{config}-t{trial}`:

- `{run_id}.summary.json`: the run as the harness saw it. The questions asked and the fact keys each one targeted, every fact recorded with its evidence, every hook denial, every question the harness refused before it reached the client (because it said the list was or would be sent, or asked the client to send documents), every answer the simulated client gave that contradicted its facts and was sent back once, with the facts it corrected and the ones it still got wrong, the computed request list, the facts never asked, custom requests, notes for the preparer, the final note, the probes delivered, the tool calls in order, the transcript, usage, cost, and latency.
- `{run_id}.score.json`: the deterministic score. Missed and unnecessary documents, trap requests, wrong facts and stale carryovers, the first miss with its cause and nearest question, the question count, the judge's verdicts, and the reasons a run failed.

Samples are chosen to show both passes and failures, including at least one run that missed a document and one that hit a trap. They were to come from train or dev only until the test sweep was reported, and it was reported on November 2, 2026, and completed on November 4, so a sample may now come from any split.

All households are synthetic. Not tax advice.
