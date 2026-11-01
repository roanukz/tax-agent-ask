# Sample traces

Run traces live in `eval/runs/`, which is gitignored. After the live runs, a small sample is copied here so readers can see what one intake looks like end to end. None are here yet: no live run has happened.

Each sample is a pair of files named by run ID, `{split}-{household}-{config}-t{trial}`:

- `{run_id}.summary.json`: the run as the harness saw it. The questions asked and the fact keys each one targeted, every fact recorded with its evidence, every hook denial, every question the harness refused before it reached the client (because it said the list was or would be sent, or asked the client to send documents), the computed request list, the facts never asked, custom requests, notes for the preparer, the final note, the probes delivered, the tool calls in order, the transcript, usage, cost, and latency.
- `{run_id}.score.json`: the deterministic score. Missed and unnecessary documents, trap requests, wrong facts and stale carryovers, the first miss with its cause and nearest question, the question count, the judge's verdicts, and the reasons a run failed.

Samples are chosen to show both passes and failures, including at least one run that missed a document and one that hit a trap, and they come from train or dev only until the test sweep is reported.

All households are synthetic. Not tax advice.
