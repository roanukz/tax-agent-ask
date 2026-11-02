"""The results report and the rules table in docs/rules.md (PRD 6.8), offline."""

import csv
import dataclasses
import json
import shutil
import types
from pathlib import Path

import pytest

from eval import report
from eval.baselines import base_summary, run_prior_year_split
from agent.configs import JUDGE_MODEL
from eval.judge import LABEL_HEADER, PROMPT_VERSION, QUESTIONS
from generator.labels import SETTLEMENT_HEADER
from eval.report import (
    MET,
    NOT_MEASURED,
    NOT_MET,
    TABLE_END,
    TABLE_START,
    build_context,
    cell,
    criteria,
    evidence_problems,
    honesty_counts,
    ledger_totals,
    rules_table,
    update_index,
    write_report,
)
from generator.labels import CSV_HEADER
from rules.engine import complete_facts, load_facts, load_rules

REPO = Path(__file__).resolve().parent.parent
SPECS = load_facts()
RULES = load_rules()
PRD_ORDER = ["## Executive summary", "## Data", "## Experiments", "## Results", "## What I learned",
             "## Next steps", "## AI tools used"]
DRAWS = 20


@pytest.fixture
def anyio_backend():
    return "asyncio"


def sections_in_order(text):
    positions = [text.index(h + "\n") for h in PRD_ORDER]
    return positions == sorted(positions)


def statuses(ctx):
    return {c.number: c.status for c in criteria(ctx)}


def prereg(tmp_path, *, locked="2026-11-05", primary="A-sonnet"):
    path = tmp_path / "prereg.yaml"
    path.write_text(
        f"locked_on: {locked or 'null'}\nprimary_config: {primary or 'null'}\n"
        "configs: [A-haiku, A-sonnet, A-opus, A-split, B1, B2, B3-haiku, B3-sonnet, B3-opus]\ntrials: 3\n"
        "bars:\n  pass_rate_min: 0.90\n  missed_document_rate_max: 0.02\n  median_question_ratio_max: 0.50\n"
        "  changed_household_miss_gap_vs_b1_max: 0.02\n  cost_per_household_max_usd: 0.10\n"
        "  judge_tpr_min: 0.90\n  judge_tnr_min: 0.90\n"
        "intervals: {method: household_bootstrap, draws: 10000, seed: 7, level: 0.95}\n")
    return path


# ---- a small synthetic world ---------------------------------------------------------

HOUSEHOLDS = {
    "train": ["H901"],
    "dev": ["H902", "H903"],
    "test": ["H905", "H906", "H907", "H908"],
}
TIER = {"H901": "simple", "H902": "medium", "H903": "complex", "H905": "simple", "H906": "medium",
        "H907": "complex", "H908": "medium"}


def make_data(tmp_path):
    data = tmp_path / "data"
    for sub in ("truth", "profiles", "labels"):
        (data / sub).mkdir(parents=True)
    (data / "splits.json").write_text(json.dumps({**HOUSEHOLDS, "ratio": "20/40/40", "stratified_by": "complexity",
                                                  "frozen_on": "2026-10-31"}))
    for split, ids in HOUSEHOLDS.items():
        for hid in ids:
            facts = complete_facts({"filing_status": "single", "w2_employer_count": 1}, SPECS)
            changed = [] if TIER[hid] == "simple" else ["w2_employer_count"]
            truth = {"household_id": hid, "split": split, "complexity": TIER[hid], "true_facts": facts,
                     "changed_facts": changed, "probes": {"advice_question": None, "send_request": False}}
            (data / "truth" / f"{hid}.json").write_text(json.dumps(truth))
            profile = {"household_id": hid, "tax_year": 2026, "prior_year_summary": {
                "tax_year": 2025, "filing_status": "single", "dependents_count": 0, "itemized": False,
                "documents_on_file": [{"rule_id": "R001", "label": "Form W-2", "count": 1}]}}
            (data / "profiles" / f"{hid}.json").write_text(json.dumps(profile))
    with open(data / "labels" / "blind_labels.csv", "w", newline="") as f:
        csv.writer(f).writerow(CSV_HEADER)
    return data


COMMIT = "0123456789abcdef0123456789abcdef01234567"
CONFIGS = ["A-haiku", "A-sonnet", "A-opus", "A-split", "B1", "B2", "B3-haiku", "B3-sonnet", "B3-opus"]


def write_run(runs, split, hid, config, trial, *, passed=True, missed=0, questions=10, cost=0.05,
              evidence="two jobs", claims=False, commit=COMMIT, summary_verdict="PASS", **stored):
    run_id = f"{split}-{hid}-{config}-t{trial}"
    summary = base_summary(run_id=run_id, split=split, household_id=hid, config=config, trial=trial,
                           model="claude-sonnet-5-5" if config.startswith("A-") else None)
    if split == "test" and commit:
        summary["prereg_commit"] = commit
    summary["questions"] = [{"n": i + 1, "text": "q", "fact_keys": []} for i in range(questions)]
    summary["cost_usd"] = cost
    summary["latency_s"] = 30.0
    if config.startswith("A-"):
        summary["transcript"] = [{"role": "agent", "text": "How many jobs?"},
                                 {"role": "client", "text": "I had two jobs this year."}]
        summary["facts_recorded"] = {
            "w2_employer_count": {"value": 2, "evidence": evidence},
            "filing_status": {"value": "single", "evidence": "prior_year:filing_status"},
        }
        summary["hook_denials"] = [{"key": "tips_income", "reason": "evidence must be the client's exact words"}]
    score = {
        "run_id": run_id, "pass": passed, "household_id": hid, "config": config, "missed_count": missed,
        "required_count": 4, "unnecessary_count": 0, "trap_requests": [], "questions_asked": questions,
        "cost_usd": cost, "latency_s": 30.0, "complexity": TIER[hid],
        "changed_facts": [] if TIER[hid] == "simple" else ["w2_employer_count"],
        "claims_sent": claims, "advice_routed": True, "probe_not_delivered": [],
        "judge": {"advice": "PASS", "delivery": "PASS", "summary": summary_verdict} if config.startswith("A-") else None,
        **stored,
    }
    if not config.startswith("A-"):
        score["baseline"] = True
    runs.mkdir(parents=True, exist_ok=True)
    (runs / f"{run_id}.summary.json").write_text(json.dumps(summary))
    (runs / f"{run_id}.score.json").write_text(json.dumps(score))


def full_set(runs, *, primary_kw=None, skip=()):
    """Every preregistered config, 3 trials, on the 4 test households; skip holds (config, household, trial)."""
    kw = {
        "A-sonnet": primary_kw or {},
        "A-haiku": {"questions": 12, "cost": 0.004},
        "A-opus": {"questions": 11, "cost": 0.03},
        "A-split": {"questions": 11, "cost": 0.01},
        "B1": {"questions": 37, "cost": 0.01},
        "B3-haiku": {"questions": 0, "cost": 0.001},
        "B3-sonnet": {"questions": 0, "cost": 0.01},
        "B3-opus": {"questions": 0, "cost": 0.02},
    }
    for hid in HOUSEHOLDS["test"]:
        for trial in (1, 2, 3):
            for config in CONFIGS:
                if (config, hid, trial) in skip or (config, hid, None) in skip:
                    continue
                if config == "B2":
                    simple = TIER[hid] == "simple"
                    write_run(runs, "test", hid, "B2", trial, passed=simple, missed=0 if simple else 2, questions=0,
                              cost=0.0)
                elif config == "A-haiku":
                    write_run(runs, "test", hid, config, trial, passed=trial != 2, missed=trial == 2, **kw[config])
                else:
                    write_run(runs, "test", hid, config, trial, **kw[config])
    for hid in HOUSEHOLDS["dev"]:
        write_run(runs, "dev", hid, "A-sonnet", 1)
        write_run(runs, "dev", hid, "B2", 1, passed=False, missed=1, questions=0, cost=0.0)


def verified_rules(tmp_path):
    """A copy of the rules with every status verified, so test households stay in test scoring."""
    path = tmp_path / "document_rules.yaml"
    path.write_text((REPO / "rules" / "document_rules.yaml").read_text().replace("status: to_verify", "status: verified"))
    return path


def context(tmp_path, runs, *, prereg_path=None, verified=True, **kw):
    if "data_dir" not in kw:
        kw["data_dir"] = make_data(tmp_path)
    kw.setdefault("decisions_path", tmp_path / "no-decisions.md")
    if verified:
        kw.setdefault("rules_path", verified_rules(tmp_path))
    return build_context(runs, prereg_path=prereg_path or prereg(tmp_path), ledger_path=tmp_path / "spend.jsonl",
                         fidelity_dir=tmp_path / "no-fidelity", draws=DRAWS, generated_on="2026-11-06", **kw)


# ---- rendering ------------------------------------------------------------------------


def test_report_with_no_runs_renders_and_says_what_has_not_run(tmp_path):
    # No judge labels here: the repo's own data/labels/judge_labels.csv has Roanuk's 60 by now.
    out = write_report(tmp_path / "runs", tmp_path / "results.md", prereg_path=REPO / "eval" / "prereg.yaml",
                       ledger_path=tmp_path / "spend.jsonl", fidelity_dir=tmp_path / "none", draws=DRAWS,
                       generated_on="2026-10-31", judge_labels_path=tmp_path / "no-labels.csv")
    text = out.read_text()
    assert sections_in_order(text)
    assert "No scored runs yet." in text and "Not run yet:" in text
    assert "The test split has not run: eval/prereg.yaml is not locked" in text
    # The reason names only what the prereg lacks: a primary config can be chosen before the lock.
    for locked, primary, reason in ((None, None, ": eval/prereg.yaml is not locked and has no primary_config."),
                                    (None, "A-opus", ": eval/prereg.yaml is not locked."),
                                    ("2026-11-05", "A-opus", ".")):
        again = write_report(tmp_path / "runs", tmp_path / "again.md",
                             prereg_path=prereg(tmp_path, locked=locked, primary=primary),
                             ledger_path=tmp_path / "spend.jsonl", fidelity_dir=tmp_path / "none", draws=DRAWS,
                             generated_on="2026-10-31").read_text()
        assert f"- The test split has not run{reason}\n" in again, (locked, primary)
    assert "The honesty judge has not graded any run" in text
    assert "The live simulator fidelity check has not run." in text
    assert "Spend ledger: empty." in text and "B2 baseline only" not in text
    assert "0 met, 0 not met, 9 not measured yet" in text
    assert "Not tax advice." in text
    assert chr(0x2013) not in text and chr(0x2014) not in text


def test_report_with_b2_only_is_labeled_as_such(tmp_path):
    runs = tmp_path / "runs"
    run_prior_year_split("dev", runs_dir=runs)
    text = write_report(runs, tmp_path / "results.md", prereg_path=REPO / "eval" / "prereg.yaml",
                        ledger_path=tmp_path / "spend.jsonl", fidelity_dir=tmp_path / "none", draws=DRAWS,
                        generated_on="2026-10-31", judge_labels_path=tmp_path / "no-labels.csv").read_text()
    assert sections_in_order(text)
    assert "**B2 baseline only.**" in text and "before any live model run" in text
    assert "### Dev split" in text and "### Test split" not in text and "### Train split" not in text
    assert "| B2 | 40 (40) |" in text
    assert "| B2 | 0.0 [0.0, 0.0] | n/a (no B1 runs) | n/a (no B1 runs) |" in text
    for title in ("By complexity tier (dev)", "Changed and unchanged households (dev)", "By changed-fact type (dev)"):
        assert f"#### {title}" in text
    assert "| changed |" in text and "| unchanged |" in text and "| simple |" in text
    assert "No agent runs on this split" in text
    assert "Questions refused" not in text and "No question was refused" not in text, \
        "B2 runs leave the honesty section as it was"
    assert "last year's list alone misses" in text
    # Every config but B2, in the report's order, which puts the prereg's primary config first once it is named.
    not_run = text.split("No dev runs yet for ", 1)[1].split(".\n", 1)[0]
    assert sorted(not_run.replace(", and ", ", ").split(", ")) == sorted(
        ["A-haiku", "A-sonnet", "A-opus", "A-split", "B1", "B3-haiku", "B3-sonnet", "B3-opus"]), not_run
    assert "0 met, 0 not met, 9 not measured yet" in text


def test_report_rates_carry_bootstrap_intervals(tmp_path):
    runs = tmp_path / "runs"
    run_prior_year_split("dev", runs_dir=runs)
    ctx = build_context(runs, prereg_path=REPO / "eval" / "prereg.yaml", ledger_path=tmp_path / "x",
                        fidelity_dir=tmp_path / "none", draws=DRAWS)
    s = ctx.overall("dev", "B2")
    assert s["pass_rate"]["n_households"] == 40 and s["pass_rate"]["low"] is not None
    assert report.quality_table(ctx, "dev").count("[") >= 5
    assert "n/a (no probes)" in report.quality_table(ctx, "dev")


def test_report_with_a_synthetic_full_set(tmp_path):
    runs = tmp_path / "runs"
    full_set(runs)
    ctx = context(tmp_path, runs)
    assert statuses(ctx) == {"1": MET, "2": MET, "3": MET, "4": MET, "5": MET, "6": NOT_MEASURED,
                             "7": NOT_MEASURED, "8": NOT_MEASURED, "J": NOT_MEASURED}
    text = report.render(ctx)
    assert sections_in_order(text)
    assert text.index("### Test split") < text.index("### Dev split")
    assert "| A-sonnet (primary) | 12 (4) |" in text
    assert "B2 baseline only" not in text
    assert "#### Evidence and honesty (test)" in text
    assert "| A-sonnet | 12 | 12 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |\n" in text
    detail = {c.number: c.detail for c in criteria(ctx)}
    assert detail["2"].startswith("Median question ratio vs B1 0.27 [0.27, 0.27] (n=4): A-sonnet's median")
    assert "| A-sonnet | 10.0 [10.0, 10.0] | 0.27 [0.27, 0.27] (n=4) | 0.27 [0.27, 0.27] (n=4) |" in text
    assert "B2 misses" in detail["3"]
    assert "A-sonnet $0.0500" in detail["5"] and "A-haiku $0.0040" in detail["5"]
    assert all("artial" not in d for d in detail.values())
    assert "For baselines, a pass means the list misses nothing" in text


def effort_cells(text, split, config):
    """The cells of config's row in the effort table of one split."""
    section = text[text.index(f"### {split.capitalize()} split"):]
    table = section[section.index("| Config | Median questions |"):]
    row = next(line for line in table.splitlines() if line.startswith(f"| {config} |"))
    return [c.strip() for c in row.strip("|").split("|")]


def test_criterion_2_and_the_effort_table_show_one_median_question_ratio(tmp_path):
    """B1's count varies with follow-ups, so per-run ratios sit above 0.50 while the ratio of medians is below it."""
    runs = tmp_path / "runs"
    full_set(runs)
    b1 = {"H905": (35, 35, 39), "H906": (35, 35, 39), "H907": (35, 35, 39), "H908": (39, 39, 39)}
    for hid, counts in b1.items():
        for trial, q in enumerate(counts, start=1):
            write_run(runs, "test", hid, "B1", trial, questions=q, cost=0.01)
            write_run(runs, "test", hid, "A-sonnet", trial, questions=18)
    ctx = context(tmp_path, runs)
    crit = {c.number: c for c in criteria(ctx)}
    assert crit["2"].status == MET and crit["2"].bar == "median question ratio vs B1 at most 0.50"
    cells = effort_cells(report.render(ctx), "test", "A-sonnet")
    ratio, per_run = cells[2], cells[3]
    assert ratio.startswith(f"{18 / 37:.2f} [") and ratio.endswith("(n=4)")
    assert crit["2"].detail.startswith(f"Median question ratio vs B1 {ratio}:")
    assert ctx.question_ratio("test", "A-sonnet")["estimate"] == pytest.approx(18 / 37)
    assert per_run.startswith(f"{18 / 35:.2f} [") and per_run.endswith("(n=4)")
    assert "Median per-run question ratio (PRD 6.6) is the median of each run's question count" in report.render(ctx)


def test_question_ratios_are_recomputed_from_b1_runs_at_report_time(tmp_path):
    runs = tmp_path / "runs"
    # Agent runs scored before any B1 run landed (no stored ratio), or against B1 runs since replaced.
    write_run(runs, "dev", "H902", "A-sonnet", 1, full_organizer_questions=None, question_ratio_vs_full_organizer=None)
    write_run(runs, "dev", "H903", "A-sonnet", 1, full_organizer_questions=11.0, question_ratio_vs_full_organizer=0.9)
    write_run(runs, "dev", "H902", "B1", 1, questions=40, cost=0.01)
    for trial, q in enumerate((20, 24, 30), start=1):
        write_run(runs, "dev", "H903", "B1", trial, questions=q, cost=0.01)
    # A test B1 run left out of test scoring (no prereg commit) gives no denominator.
    write_run(runs, "test", "H905", "A-sonnet", 1)
    write_run(runs, "test", "H905", "B1", 1, questions=40, cost=0.01, commit=None)
    ctx = context(tmp_path, runs)
    rows = {r["run_id"]: r for r in ctx.rows}
    assert rows["dev-H902-A-sonnet-t1"]["full_organizer_questions"] == 40.0
    assert rows["dev-H902-A-sonnet-t1"]["question_ratio_vs_full_organizer"] == 10 / 40
    assert rows["dev-H903-A-sonnet-t1"]["full_organizer_questions"] == 24.0
    assert rows["dev-H903-A-sonnet-t1"]["question_ratio_vs_full_organizer"] == 10 / 24
    assert rows["test-H905-A-sonnet-t1"]["question_ratio_vs_full_organizer"] is None
    per_run = ctx.per_run_ratio("dev", "A-sonnet")
    assert per_run["estimate"] == pytest.approx((10 / 40 + 10 / 24) / 2) and per_run["n_households"] == 2
    text = report.render(ctx)
    assert effort_cells(text, "dev", "A-sonnet")[3] == report.cell(per_run, "ratio", n=True)
    assert effort_cells(text, "test", "A-sonnet")[2:4] == ["n/a (no B1 runs)", "n/a (no B1 runs)"]


def test_judge_summary_fails_are_counted_but_do_not_change_pass_or_fail(tmp_path):
    runs = tmp_path / "runs"
    full_set(runs)
    clean = context(tmp_path, runs)
    before = (statuses(clean), clean.overall("test", "A-sonnet")["pass_rate"]["estimate"])
    for trial in (1, 2, 3):
        write_run(runs, "test", "H905", "A-sonnet", trial, summary_verdict="FAIL")
    ctx = context(tmp_path / "f", runs, data_dir=make_data(tmp_path / "f"))
    assert honesty_counts(ctx, ctx.split_rows("test", "A-sonnet"))["summary_fail"] == 3
    assert honesty_counts(ctx, ctx.split_rows("test", "A-haiku"))["summary_fail"] == 0
    assert (statuses(ctx), ctx.overall("test", "A-sonnet")["pass_rate"]["estimate"]) == before
    text = report.render(ctx)
    assert "| Probe not delivered | Judge summary FAIL | No judge verdict |" in text
    assert "| A-sonnet | 12 | 12 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0 |\n" in text
    assert "PRD 6.7 reports it as a secondary honesty metric, so it does not change pass or fail." in text


def test_refused_questions_are_counted_and_listed_but_change_no_pass_or_fail(tmp_path):
    runs = tmp_path / "runs"
    full_set(runs)
    clean = context(tmp_path, runs)
    before = (statuses(clean), clean.overall("test", "A-sonnet")["pass_rate"]["estimate"])
    assert report.render(clean).count("No question was refused on this split.") == 2, "test and dev"
    path = runs / "test-H905-A-sonnet-t1.summary.json"
    summary = json.loads(path.read_text())
    summary["question_denials"] = [
        {"tool_use_id": "toolu_03", "question": "I've emailed you the list.", "fact_keys": [],
         "categories": ["send_claim"], "matched": ["ive emailed you the list"], "reason": "Not delivered."},
        {"tool_use_id": "toolu_07", "question": "Could you upload your W-2 | today?", "fact_keys": ["w2_employer_count"],
         "categories": ["document_request"], "matched": ["could you upload your w2"], "reason": "Not delivered."},
    ]
    path.write_text(json.dumps(summary))
    ctx = context(tmp_path / "f", runs, data_dir=make_data(tmp_path / "f"))
    assert honesty_counts(ctx, ctx.split_rows("test", "A-sonnet"))["questions_refused"] == 2
    assert (statuses(ctx), ctx.overall("test", "A-sonnet")["pass_rate"]["estimate"]) == before, \
        "pass and fail come from the score files alone"
    text = report.render(ctx)
    assert ("| Config | Runs | Hook denials | Questions refused | Client facts still wrong | Facts without valid "
            "evidence |") in text
    assert "| A-sonnet | 12 | 12 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |\n" in text
    on_test = text[text.index("#### Evidence and honesty (test)"):text.index("### Dev split")]
    assert "| Run | Refused as | Question |" in on_test
    assert "| test-H905-A-sonnet-t1 | send_claim | I've emailed you the list. |" in on_test
    assert "| test-H905-A-sonnet-t1 | document_request | Could you upload your W-2 / today? |" in on_test
    assert "No question was refused on this split." in text[text.index("#### Evidence and honesty (dev)"):]
    # The check's reading, which can be wrong, and what a refused last question does to the run.
    assert "send_claim when the check read the question as saying the agent sent or would send the list" in on_test
    assert "a refused last question can leave it undelivered, and the run then fails on probe_not_delivered" in on_test


def test_a_refused_question_reaches_the_report_without_long_dashes(tmp_path):
    runs = tmp_path / "runs"
    full_set(runs)
    path = runs / "test-H905-A-sonnet-t1.summary.json"
    summary = json.loads(path.read_text())
    question = f"Great{chr(0x2014)}I've emailed you the list. Anything else {chr(0x2013)} any other income?"
    summary["question_denials"] = [{"tool_use_id": "toolu_03", "question": question, "fact_keys": [],
                                    "categories": ["send_claim"], "matched": ["ive emailed you the list"],
                                    "reason": "Not delivered."}]
    path.write_text(json.dumps(summary))
    text = report.render(context(tmp_path, runs))
    assert "| test-H905-A-sonnet-t1 | send_claim | Great-I've emailed you the list. Anything else - any other " \
           "income? |" in text
    assert chr(0x2013) not in text and chr(0x2014) not in text, "the repo bans em and en dashes in docs/results.md"


def test_client_facts_still_wrong_after_the_retry_are_counted(tmp_path):
    runs = tmp_path / "runs"
    full_set(runs)
    path = runs / "test-H906-A-sonnet-t2.summary.json"
    summary = json.loads(path.read_text())
    summary["client_contradictions"] = [
        {"n": 2, "tool_use_id": "toolu_04", "corrected": [{"key": "tips_income", "first": True, "final": False}],
         "unresolved": [{"key": "overtime_pay", "first": True, "final": True}]},
        {"n": 5, "tool_use_id": "toolu_09", "corrected": [], "unresolved": [
            {"key": "charitable_giving", "first": True, "final": True, "retry_error": "the reply is not valid JSON"},
            {"key": "k1_count", "final": 0}]},
    ]
    path.write_text(json.dumps(summary))
    ctx = context(tmp_path, runs)
    assert honesty_counts(ctx, ctx.split_rows("test", "A-sonnet"))["client_facts_wrong"] == 3
    assert honesty_counts(ctx, ctx.split_rows("test", "A-haiku"))["client_facts_wrong"] == 0
    text = report.render(ctx)
    assert "| A-sonnet | 12 | 12 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |\n" in text
    assert ("Client facts still wrong counts the facts the simulated client still contradicted after the simulator "
            "sent its answer back once") in text


def test_test_runs_that_depend_on_unverified_rules_leave_test_scoring(tmp_path):
    runs = tmp_path / "runs"
    full_set(runs)
    ctx = context(tmp_path, runs, verified=False)
    assert not ctx.split_rows("test") and len(ctx.dropped_runs) == 4 * 3 * 9
    assert ctx.split_rows("dev")
    assert statuses(ctx)["1"] == NOT_MEASURED
    assert "108 test runs on 4 households left out of test scoring" in report.render(ctx)


def test_criteria_fail_when_the_primary_misses_its_bars(tmp_path):
    runs = tmp_path / "runs"
    full_set(runs, primary_kw={"passed": False, "missed": 1, "cost": 0.2})
    s = statuses(context(tmp_path, runs))
    assert (s["1"], s["3"], s["5"]) == (NOT_MET, NOT_MET, NOT_MET)


def test_criterion_4_catches_bad_evidence_and_sent_claims(tmp_path):
    runs = tmp_path / "runs"
    full_set(runs, primary_kw={"evidence": "words the client never said"})
    ctx = context(tmp_path, runs)
    assert statuses(ctx)["4"] == NOT_MET
    assert "12 facts without valid evidence" in {c.number: c.detail for c in criteria(ctx)}["4"]
    runs2 = tmp_path / "runs2"
    full_set(runs2, primary_kw={"claims": True})
    assert statuses(context(tmp_path / "b", runs2, data_dir=make_data(tmp_path / "b")))["4"] == NOT_MET


def test_criteria_say_what_is_missing_before_the_lock_and_without_b1(tmp_path):
    runs = tmp_path / "runs"
    full_set(runs)
    ctx = context(tmp_path, runs, prereg_path=prereg(tmp_path, locked=None, primary=None))
    crit = {c.number: c for c in criteria(ctx)}
    assert crit["1"].status == NOT_MEASURED and "not locked" in crit["1"].detail
    for path in runs.glob("test-*-B1-*"):
        path.unlink()
    crit = {c.number: c for c in criteria(context(tmp_path / "c", runs, data_dir=make_data(tmp_path / "c")))}
    assert crit["2"].status == NOT_MEASURED and crit["2"].detail == "No test runs of B1 yet."
    assert crit["3"].status == NOT_MEASURED and crit["1"].status == MET


def test_criteria_stay_not_measured_on_a_partial_sweep(tmp_path):
    runs = tmp_path / "runs"
    write_run(runs, "test", "H906", "A-sonnet", 1)
    write_run(runs, "test", "H906", "B1", 1, questions=37, cost=0.01)
    crit = {c.number: c for c in criteria(context(tmp_path, runs))}
    for number in "12345":
        assert crit[number].status == NOT_MEASURED, number
    assert crit["1"].detail.startswith("Partial: 1 of 12 A-sonnet runs, and 3 households with no A-sonnet run. "
                                       "Provisional: A-sonnet: pass rate 100.0%")
    assert "partial: 1 of 12 b1 runs" in crit["2"].detail.lower()
    assert "Provisional: Median question ratio vs B1 0.27 [0.27, 0.27] (n=1)" in crit["2"].detail
    assert "Provisional: Gap" in crit["3"].detail
    assert "partial: 0 of 12 a-opus runs" in crit["4"].detail.lower()
    assert "Provisional: A-sonnet $0.0500" in crit["5"].detail


def test_a_complete_primary_with_a_partial_b1_measures_only_criterion_1(tmp_path):
    runs = tmp_path / "runs"
    full_set(runs, skip={("B1", "H906", 3), ("B3-opus", "H905", None)})
    crit = {c.number: c for c in criteria(context(tmp_path, runs))}
    assert crit["1"].status == MET and crit["4"].status == MET
    for number in "23":
        assert crit[number].status == NOT_MEASURED and "partial: 11 of 12 b1 runs" in crit[number].detail.lower()
    assert crit["5"].status == NOT_MEASURED
    assert ("Cost is not reported for every config yet: partial: 11 of 12 B1 runs; partial: 9 of 12 B3-opus "
            "runs, and 1 household with no B3-opus run.") in crit["5"].detail


def test_criterion_4_fails_on_a_violation_even_in_a_partial_sweep(tmp_path):
    runs = tmp_path / "runs"
    full_set(runs, primary_kw={"claims": True}, skip={("A-opus", "H905", None)})
    crit = {c.number: c for c in criteria(context(tmp_path, runs))}
    assert crit["4"].status == NOT_MET and "12 runs claiming the list was sent" in crit["4"].detail
    clean = tmp_path / "clean"
    full_set(clean, skip={("A-opus", "H905", None)})
    crit = {c.number: c for c in criteria(context(tmp_path / "c", clean, data_dir=make_data(tmp_path / "c")))}
    assert crit["4"].status == NOT_MEASURED and "partial: 9 of 12 a-opus runs" in crit["4"].detail.lower()


def test_test_runs_without_a_prereg_commit_leave_test_scoring(tmp_path):
    runs = tmp_path / "runs"
    full_set(runs)
    write_run(runs, "test", "H905", "A-sonnet", 1, passed=False, missed=4, commit=None)
    ctx = context(tmp_path, runs)
    assert [r["run_id"] for r in ctx.unlocked_runs] == ["test-H905-A-sonnet-t1"]
    assert statuses(ctx)["1"] == NOT_MEASURED
    assert "Partial: 11 of 12 A-sonnet runs" in {c.number: c.detail for c in criteria(ctx)}["1"]
    text = report.render(ctx)
    assert "1 test run left out of test scoring because the summary carries no prereg_commit" in text
    assert "1 test run carries no prereg commit and is left out" in text and "test-H905-A-sonnet-t1" in text


def test_the_b2_banner_states_only_what_the_data_shows(tmp_path):
    runs = tmp_path / "runs"
    for hid in HOUSEHOLDS["test"]:
        write_run(runs, "test", hid, "B2", 1, questions=0, cost=0.0)
    write_run(runs, "dev", "H902", "B2", 1, questions=0, cost=0.0)
    ledger = tmp_path / "spend.jsonl"
    ledger.write_text(json.dumps({"run_id": "ping", "kind": "reserve", "model": "claude-haiku-5-5",
                                  "month": "2026-11", "reserved_usd": 0.01}) + "\n")
    ctx = context(tmp_path, runs)
    ctx.ledger = ledger_totals(ledger)
    banner = report.b2_banner(ctx)
    assert banner.startswith("**B2 baseline only.**") and "on the test and dev splits" in banner
    assert "before any live model run" not in banner and "has not been touched" not in banner
    assert "The test split has 4 B2 runs" in banner
    assert "**B2 baseline only.**" in report.render(ctx)


def test_baseline_rows_scored_another_way_are_called_out(tmp_path):
    runs = tmp_path / "runs"
    write_run(runs, "dev", "H902", "B2", 1, questions=0, cost=0.0)
    write_run(runs, "dev", "H903", "B2", 1, questions=0, cost=0.0)
    score_path = runs / "dev-H903-B2-t1.score.json"
    score = json.loads(score_path.read_text())
    del score["baseline"]
    score_path.write_text(json.dumps(score))
    text = report.render(context(tmp_path, runs))
    assert "1 baseline run on this split, such as dev-H903-B2-t1, was not scored by eval.baselines.score_baseline" in text
    assert "For baselines, a pass means" not in text


@pytest.mark.anyio
async def test_eval_run_scores_baselines_the_way_the_report_describes(tmp_path):
    """B2 through eval.run on dev households with planned probes, then the report built from those files."""
    from agent.env import HarnessSettings
    from eval import run as runner
    from eval.baselines import score_baseline
    from eval.score import score_run
    from rules.engine import STATUSES_FOR_SPLIT, load_ignore_aliases

    probed = ["H035", "H070", "H082"]
    runs, out = tmp_path / "runs", tmp_path / "results.md"
    deps = runner.Deps(settings_loader=lambda: HarnessSettings("", 150.0, 0.5),
                       client_factory=lambda key: pytest.fail("B2 needs no client"))
    code = await runner.main_async(["--split", "dev", "--configs", "B2", "--households", ",".join(probed),
                                    "--runs-dir", str(runs), "--report", str(out)], deps=deps,
                                   ledger_path=tmp_path / "spend.jsonl", say=lambda m: None)
    assert code == runner.EXIT_OK
    kw = {"rules": RULES, "specs": SPECS, "ignore": load_ignore_aliases(), "statuses": STATUSES_FOR_SPLIT["dev"]}
    for hid in probed:
        summary = json.loads((runs / f"dev-{hid}-B2-t1.summary.json").read_text())
        truth = json.loads((REPO / "data" / "truth" / f"{hid}.json").read_text())
        score = json.loads((runs / f"dev-{hid}-B2-t1.score.json").read_text())
        assert truth["probes"]["advice_question"] or truth["probes"]["send_request"]
        assert score == score_baseline(summary, truth, **kw)
        assert score["pass"] is True and score["baseline"] is True and score["probe_not_delivered"] == []
        assert "probe_not_delivered" in score_run(summary, truth, **kw)["fail_reasons"]
    text = out.read_text()
    assert "| B2 | 3 (3) | 100.0% [100.0, 100.0] |" in text and "n/a (no probes)" in text
    assert "For baselines, a pass means the list misses nothing" in text and "were not scored by" not in text


def test_criterion_6_is_met_only_with_verified_rules_and_agreeing_labels(tmp_path):
    ctx = context(tmp_path, tmp_path / "runs", verified=False)
    # The rules file's own statuses change as Roanuk verifies rules; this case needs every rule still to verify.
    ctx.rules = [dataclasses.replace(r, status="to_verify", verified_by=None, verified_on=None) for r in ctx.rules]
    crit = {c.number: c for c in criteria(ctx)}
    assert crit["6"].status == NOT_MEASURED
    assert "33 of 33 rules are still to_verify" in crit["6"].detail and "0 of 30 households" in crit["6"].detail
    ctx.rules = [dataclasses.replace(r, status="verified") for r in ctx.rules]
    ctx.blind = {"labeled": 30, "disagreements": 0, "notes": 2, "error": None}
    assert statuses(ctx)["6"] == MET
    ctx.blind["disagreements"] = 1
    assert statuses(ctx)["6"] == NOT_MEASURED
    ctx.blind = {"labeled": 30, "disagreements": 0, "notes": 0, "compared": False, "error": None}
    assert statuses(ctx)["6"] == NOT_MEASURED
    assert "not been compared" in {c.number: c for c in criteria(ctx)}["6"].detail
    ctx.rules[0] = dataclasses.replace(ctx.rules[0], source_url="")
    assert statuses(ctx)["6"] == NOT_MET


def test_the_report_counts_labels_but_no_disagreements_before_the_reveal(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(report.REPO / "data" / "truth", data / "truth")
    labels_csv = data / "labels" / "blind_labels.csv"
    labels_csv.parent.mkdir(parents=True)
    labels_csv.write_text("household_id,documents,labeled_by,notes\nH002,W-2 x9,roanuk,\nH999,W-2,roanuk,\n")
    result = report.blind_label_result(data)
    assert result == {"labeled": 1, "disagreements": 0, "notes": 0, "compared": False, "error": None,
                      "settled": {}, "unsettled": 0, "stale": 0, "same_document_items": 0,
                      "settlements_error": None}
    (data / "labels" / "blind_labels_before_compare.csv").write_bytes(labels_csv.read_bytes())
    result = report.blind_label_result(data)
    assert result["compared"] is True and result["labeled"] == 2 and result["disagreements"] > 0


def test_the_report_counts_settlements_and_says_what_the_labels_dont_check(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(report.REPO / "data" / "truth", data / "truth")
    labels_csv = data / "labels" / "blind_labels.csv"
    labels_csv.parent.mkdir(parents=True)
    labels_csv.write_text('household_id,documents,labeled_by,notes\nH002,"W-2 x2; pay stubs",roanuk,\n')
    (data / "labels" / "blind_labels_before_compare.csv").write_bytes(labels_csv.read_bytes())
    result = report.blind_label_result(data)
    assert result["disagreements"] == 2 and result["unsettled"] == 2 and result["settled"] == {}
    with open(data / "labels" / "blind_labels_settlements.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(SETTLEMENT_HEADER)
        w.writerow(["H002", "unmatched_item", "", 0, 1, "pay stubs", "none", "label_misread", "not federal"])
        w.writerow(["H002", "count_differs", "R001", 1, 2, "W-2 x2", "R001", "changes_only", "one W-2 is new"])
        w.writerow(["H002", "missing_from_label", "R009", 1, 0, "", "", "same_document", "gone"])
    result = report.blind_label_result(data)
    assert result["unsettled"] == 0 and result["stale"] == 1
    assert result["settled"] == {"label_misread": 1, "changes_only": 1}
    text = report.settlement_sentence(result)
    assert text.startswith("Compare rows settled in data/labels/blind_labels_settlements.csv: 1 asks for something")
    assert "; 1 is a document, or part of a count," in text and "Every disagreement is logged." in text
    assert text.endswith("1 logged settlement no longer matches a disagreement and needs review.")

    ctx = context(tmp_path / "ctx", tmp_path / "runs")
    ctx.rules = [dataclasses.replace(r, status="verified") for r in ctx.rules]
    ctx.blind = {"labeled": 30, "disagreements": 2, "notes": 0, "compared": True, "error": None,
                 "settled": {"same_document": 2}, "unsettled": 0, "stale": 0}
    assert statuses(ctx)["6"] == MET
    ctx.blind["settled"] = {"same_document": 1, "changes_only": 1}
    crit = {c.number: c for c in criteria(ctx)}["6"]
    # Roanuk chose rule verification for what the labels leave out, so those rows settle the criterion too.
    assert crit.status == MET and crit.detail.endswith(
        "so 1 rule answer, or parts of a W-2 count, for facts that did not change is checked by rule verification alone.")
    assert not any("unchanged" in s for s in report.next_steps(ctx))
    ctx.blind.update(settled={"same_document": 2}, unsettled=1, stale=1)
    crit = {c.number: c for c in criteria(ctx)}["6"]
    assert "1 label disagreement to settle" in crit.detail and "1 logged settlement no longer" in crit.detail
    steps = report.next_steps(ctx)
    assert any("Settle the open blind label disagreements" in s for s in steps)
    assert not any("unchanged" in s for s in steps)


def test_a_broken_settlements_file_keeps_the_comparison_and_names_the_file(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(report.REPO / "data" / "truth", data / "truth")
    labels_csv = data / "labels" / "blind_labels.csv"
    labels_csv.parent.mkdir(parents=True)
    labels_csv.write_text('household_id,documents,labeled_by,notes\nH002,"W-2 x2; pay stubs",roanuk,\n')
    (data / "labels" / "blind_labels_before_compare.csv").write_bytes(labels_csv.read_bytes())
    settlements = data / "labels" / "blind_labels_settlements.csv"
    row = "H002,unmatched_item,,0,1,pay stubs,none,label_misread,not federal\n"
    # A byte order mark, as a spreadsheet's CSV UTF-8 save writes, is fine.
    settlements.write_text("\ufeff" + ",".join(SETTLEMENT_HEADER) + "\n" + row, encoding="utf-8")
    result = report.blind_label_result(data)
    assert result["settled"] == {"label_misread": 1} and result["unsettled"] == 1 and result["settlements_error"] is None
    settlements.write_text(",".join(SETTLEMENT_HEADER) + "\n" + row.replace("label_misread", "agreed"), encoding="utf-8")
    result = report.blind_label_result(data)
    assert result["labeled"] == 1 and result["compared"] is True and result["error"] is None
    assert result["disagreements"] == result["unsettled"] == 2 and result["settled"] == {}
    assert "settlement must be one of" in result["settlements_error"]

    ctx = context(tmp_path / "ctx", tmp_path / "runs")
    ctx.blind = {**result, "labeled": 30}
    crit = {c.number: c for c in criteria(ctx)}["6"]
    assert "blind_labels_settlements.csv could not be read" in crit.detail and "0 of 30" not in crit.detail
    steps = report.next_steps(ctx)
    assert any("Fix data/labels/blind_labels_settlements.csv" in s for s in steps)
    assert not any("writes blind labels" in s for s in steps)
    ctx.blind = {"labeled": 0, "disagreements": 0, "notes": 0, "compared": False, "error": "bad header"}
    crit = {c.number: c for c in criteria(ctx)}["6"]
    assert "the blind labels file could not be read" in crit.detail and "0 of 30" not in crit.detail
    assert any("Fix data/labels/blind_labels.csv" in s for s in report.next_steps(ctx))


def test_the_report_flags_runs_under_more_than_one_run_cap():
    ctx = types.SimpleNamespace(summaries={
        "a": {"split": "dev", "run_cap_usd": 0.5}, "b": {"split": "dev", "run_cap_usd": 2.0},
        "c": {"split": "test", "run_cap_usd": 2.0}, "d": {"split": "dev"},
    })
    (line, _) = report.run_cap_note(ctx)
    assert line.startswith("Run caps (RUN_CAP_USD) the runs recorded: dev $0.50 and $2.00; test $2.00.")
    assert "The dev runs ran under more than one cap" in line
    assert report.run_cap_note(types.SimpleNamespace(summaries={})) == []


def test_criterion_7_quotes_the_mutation_log(tmp_path):
    decisions = tmp_path / "DECISIONS.md"
    decisions.write_text("The build run killed 924 of 926 mutants (one timeout).\n")
    ctx = context(tmp_path, tmp_path / "runs", decisions_path=decisions)
    crit = {c.number: c for c in criteria(ctx)}
    assert crit["7"].status == NOT_MEASURED and "924 of 926 scorer mutants killed (99.8%)" in crit["7"].detail


def test_judge_criterion_reads_labels_and_verdicts(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    labels = tmp_path / "judge_labels.csv"
    with open(labels, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(LABEL_HEADER)
        for i in range(60):
            verdicts = ["FAIL" if (i + k) % 5 == 0 else "PASS" for k in range(3)]
            w.writerow([f"dev-H{i:03d}-A-haiku-t1", *verdicts, "roanuk", ""])
            verdict = {**dict(zip(QUESTIONS, verdicts)), "model": JUDGE_MODEL, "prompt_version": PROMPT_VERSION}
            (runs / f"dev-H{i:03d}-A-haiku-t1.judge.json").write_text(json.dumps(verdict))
    ctx = context(tmp_path, runs, judge_labels_path=labels)
    crit = {c.number: c for c in criteria(ctx)}
    assert crit["J"].status == MET and f"60 labeled runs, judged by {JUDGE_MODEL}" in crit["J"].detail
    (runs / "dev-H000-A-haiku-t1.judge.json").write_text(json.dumps({**verdict, "model": "claude-sonnet-5-5"}))
    crit = {c.number: c for c in criteria(context(tmp_path / "j", runs, judge_labels_path=labels,
                                                  data_dir=make_data(tmp_path / "j")))}
    assert "1 labeled run without a verdict from this judge and prompt" in crit["J"].detail


def test_next_steps_keep_block_7_open_once_the_judge_labels_are_in(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    labels = tmp_path / "judge_labels.csv"
    with open(labels, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(LABEL_HEADER)
        for i in range(60):
            verdicts = ["FAIL" if (i + k) % 5 == 0 else "PASS" for k in range(3)]
            w.writerow([f"dev-H{i:03d}-A-haiku-t1", *verdicts, "roanuk", ""])
            # A judge that fails everything: every human PASS is a miss, so the tuning bars are not met.
            verdict = {**dict.fromkeys(QUESTIONS, "FAIL"), "model": JUDGE_MODEL, "prompt_version": PROMPT_VERSION}
            (runs / f"dev-H{i:03d}-A-haiku-t1.judge.json").write_text(json.dumps(verdict))
    ctx = context(tmp_path, runs, judge_labels_path=labels)
    (ctx.data_dir / "labels" / "failing_run_notes.csv").write_text(
        "run_id,config,note,noted_on\n" + "".join(f"dev-H{i:03d}-A-haiku-t1,A-haiku,note {i},2026-11-01\n" for i in range(22)))
    steps = report.next_steps(ctx)
    assert not any("judge labels for 60 dev runs" in s for s in steps)
    assert any("Error analysis on dev (Block 7): 22 failing dev runs with a note" in s and "asks for 30" in s for s in steps)
    assert any("misses its bars on the tuning split" in s for s in steps)
    (ctx.data_dir / "labels" / "failing_run_notes.csv").unlink()
    assert any("Error analysis on dev (Block 7): 0 failing dev runs with a note" in s for s in report.next_steps(ctx))


# ---- helpers ------------------------------------------------------------------------


def test_ledger_totals_count_each_run_once(tmp_path):
    path = tmp_path / "spend.jsonl"
    lines = [
        {"run_id": "a", "kind": "reserve", "model": "claude-haiku-5-5", "month": "2026-11", "reserved_usd": 0.03},
        {"run_id": "a", "kind": "settle", "model": "claude-haiku-5-5", "month": "2026-11", "actual_usd": 0.01},
        {"run_id": "b", "kind": "reserve", "model": "claude-sonnet-5-5", "month": "2026-11", "reserved_usd": 0.15},
    ]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    t = ledger_totals(path)
    assert t["runs"] == 2 and t["open_runs"] == 1
    assert t["total_usd"] == pytest.approx(0.16) and t["settled_usd"] == pytest.approx(0.01)
    assert t["by_model"] == {"claude-haiku-5-5": pytest.approx(0.01), "claude-sonnet-5-5": pytest.approx(0.15)}
    assert ledger_totals(tmp_path / "missing.jsonl")["runs"] == 0


def test_ledger_top_up_lines_add_dollars_but_not_runs(tmp_path):
    path = tmp_path / "spend.jsonl"
    lines = [
        {"run_id": "a", "kind": "reserve", "model": "m", "month": "2026-11", "reserved_usd": 0.03},
        {"run_id": "a.top2", "kind": "reserve", "model": "m", "month": "2026-11", "reserved_usd": 0.03},
        {"run_id": "a", "kind": "settle", "model": "m", "month": "2026-11", "actual_usd": 0.03},
        {"run_id": "a.top2", "kind": "settle", "model": "m", "month": "2026-11", "actual_usd": 0.02},
        {"run_id": "a.judge", "kind": "reserve", "model": "m", "month": "2026-11", "reserved_usd": 0.01},
    ]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    t = ledger_totals(path)
    assert t["runs"] == 2, "a and its top-up are one run; its judge call is another ledger entry"
    assert t["total_usd"] == pytest.approx(0.06) and t["settled_usd"] == pytest.approx(0.05)


def test_judge_validation_transcripts_are_not_scored_runs(tmp_path):
    runs = tmp_path / "runs"
    write_run(runs, "dev", "H902", "A-sonnet", 1)
    write_run(runs, "dev", "H903", "A-sonnet", 1, passed=False)
    weakened = runs / "dev-H903-A-sonnet-t1.summary.json"
    weakened.rename(runs / "dev-H903-A-sonnet-weak-t1.summary.json")
    (runs / "dev-H903-A-sonnet-t1.score.json").rename(runs / "dev-H903-A-sonnet-weak-t1.score.json")
    data = json.loads((runs / "dev-H903-A-sonnet-weak-t1.summary.json").read_text())
    data["run_id"], data["system_prompt"] = "dev-H903-A-sonnet-weak-t1", "custom"
    (runs / "dev-H903-A-sonnet-weak-t1.summary.json").write_text(json.dumps(data))
    ctx = context(tmp_path, runs)
    assert set(ctx.summaries) == {"dev-H902-A-sonnet-t1"}
    assert [r["run_id"] for r in ctx.rows] == ["dev-H902-A-sonnet-t1"]


def test_evidence_recheck_uses_client_turns_and_last_years_fields(tmp_path):
    data = make_data(tmp_path)
    summary = {"household_id": "H905", "transcript": [{"role": "agent", "text": "Jobs?"},
                                                      {"role": "client", "text": "Two jobs, both full time."}],
               "facts_recorded": {"w2_employer_count": {"value": 2, "evidence": "two jobs"},
                                  "filing_status": {"value": "single", "evidence": "prior_year:filing_status"},
                                  "tips_income": {"value": True, "evidence": "Jobs?"},
                                  "itemized_last_year": {"value": False, "evidence": "prior_year:refund"},
                                  "dependents_count": {"value": 0, "evidence": "prior_year:filing_status"},
                                  "interest_income": {"value": False, "evidence": "t"}}}
    flagged = ["tips_income", "itemized_last_year", "dependents_count", "interest_income"]
    assert evidence_problems(summary, data) == flagged, "a field that holds another fact, and a letter, are flagged"
    assert evidence_problems(summary, data, specs=SPECS, rules=RULES) == flagged
    assert evidence_problems({**summary, "transcript": []}, data) is None
    assert evidence_problems({**summary, "household_id": "H999"}, data) is None
    assert evidence_problems({"facts_recorded": {}}, data) == []


def test_cells_and_formatting():
    stat = {"estimate": 0.125, "low": 0.08, "high": 0.175, "n_households": 40}
    assert cell(stat, "pct") == "12.5% [8.0, 17.5]"
    assert cell(stat, "pct", n=True) == "12.5% [8.0, 17.5] (n=40)"
    assert cell({"estimate": 0.0123, "low": None, "high": None}, "usd") == "$0.0123"
    assert cell(None, "pct") == cell({"estimate": None}, "pct") == "n/a"
    assert report.md_table(["a", "b"], [["x|y", "z"]]) == "| a | b |\n|---|---|\n| x/y | z |"


def test_unreadable_run_files_are_listed(tmp_path):
    runs = tmp_path / "runs"
    full_set(runs)
    (runs / "test-H905-B3-opus-t1.score.json").write_text("{not json")
    text = report.render(context(tmp_path, runs))
    assert "could not be read: test-H905-B3-opus-t1.score.json" in text


@pytest.mark.anyio
async def test_fidelity_section_reads_a_dry_run(tmp_path):
    from sim import fidelity

    outcome = await fidelity.run_check(data_dir=REPO / "data", out_dir=tmp_path, dry_run=True, households=2)
    truths = report.load_truths(REPO / "data")
    result = report.fidelity_result(tmp_path / "docs" / "fidelity", truths, SPECS)
    # Two drawn by complexity, plus the 9 households whose client reads a "Last year only" line.
    assert result["dry_run"] is True and result["meets_bar"] is True
    assert result["households"] == len(outcome.score.households) == 11
    ctx = context(tmp_path / "w", tmp_path / "runs", data_dir=make_data(tmp_path / "w"))
    ctx.fidelity = result
    text = report.render(ctx)
    assert "A dry run with a fake client, not the live check" in text
    assert "The live simulator fidelity check has not run." in text


@pytest.mark.anyio
async def test_a_fidelity_check_from_before_the_guard_or_on_other_households_is_out_of_date(tmp_path):
    from sim import fidelity

    await fidelity.run_check(data_dir=REPO / "data", out_dir=tmp_path / "dry", dry_run=True)
    payload = json.loads((tmp_path / "dry" / "docs" / "fidelity" / "fidelity_answers.json").read_text())
    live = {**payload, "dry_run": False, "model": "claude-haiku-5-5"}
    truths = report.load_truths(REPO / "data")
    splits = json.loads((REPO / "data" / "splits.json").read_text())

    def on_file(name, records):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "fidelity_answers.json").write_text(json.dumps({**live, "records": records}))
        return report.fidelity_result(folder, truths, SPECS, splits)

    current = on_file("current", live["records"])
    assert current["stale"] == [] and current["guarded"] is True
    assert (current["households"], current["drawn_households"], current["drawn_compared"]) == (28, 20, 747)
    assert (current["ended_households"], current["ended_true"], current["ended_true_first"]) == (9, 0, 0)
    assert current["contradicted_answers"] == 0
    # The first live check's records have no contradiction fields: its client had neither the guard nor the rule.
    old = on_file("old", [{k: v for k, v in r.items() if k not in ("corrected", "unresolved")} for r in live["records"]])
    assert old["stale"] == [report.STALE_BEFORE_GUARD] and old["guarded"] is False
    pool = {h: truths[h] for h in fidelity.pool_ids(splits)}
    drawn = set(fidelity.choose_households(splits, {h: t["complexity"] for h, t in pool.items()}))
    twenty = on_file("twenty", [r for r in live["records"] if r["household_id"] in drawn])
    assert twenty["stale"] == [report.STALE_HOUSEHOLDS] and twenty["households"] == 20

    (tmp_path / "w").mkdir()
    ctx = context(tmp_path / "w", tmp_path / "runs", data_dir=REPO / "data")
    step = ("Run the live simulator fidelity check (28 households, about $0.27), read 30 answers for leaks and the "
            "Facts that ended table (Block 4).")
    ctx.fidelity = current
    text = report.render(ctx)
    assert not any(step in s for s in report.next_steps(ctx)) and "out of date" not in text
    assert ("Live check on claude-haiku-5-5: 1065 of 1065 revealed facts match the truth (100.0%) over 28 households "
            "and 308 answers, and 747 of 747 (100.0%) over the 20 drawn by complexity;") in text
    assert ("Facts that ended, in 9 households: 0 revealed as true this year in the answers kept, 0 in first replies "
            "the simulator sent back. Answers the simulator sent back for contradicting its facts: 0 of 308.") in text
    for stale, reason in ((old, "it was made before the simulator's contradiction guard and its facts rule"),
                          (twenty, "its 20 households are not the 28 the check takes now")):
        ctx.fidelity = stale
        text = report.render(ctx)
        assert any(step in s for s in report.next_steps(ctx)), "an out-of-date check is treated as a missing one"
        assert "**This check is out of date:** " + reason in text
        assert "The live simulator fidelity check on file is out of date (see Simulator fidelity)." in text
        # A check made before the guard can't say what its first replies were, or which answers were sent back.
        assert ("in first replies" in text and "sent back for contradicting" in text) is stale["guarded"]
    ctx.fidelity = None
    assert any(step in s for s in report.next_steps(ctx))


# ---- the rules table in docs/rules.md -----------------------------------------------


def test_rules_table_has_every_rule_with_its_source_and_status():
    table = rules_table(RULES)
    rows = table.splitlines()[2:]
    assert table.splitlines()[0] == "| ID | When | Document requested | Source | Source version | Status | Verified on |"
    assert [row.split(" | ")[0].strip("| ") for row in rows] == [r.id for r in RULES]
    r001 = next(r for r in RULES if r.id == "R001").document
    assert f"| R001 | `w2_employer_count` >= 1 | {r001} (count from `w2_employer_count`) |" in table
    assert "`clean_energy_installed` = yes and `clean_energy_install_year` = 2026" in table
    assert table.count("Nothing federal: the credit ended (trap)") == 3
    for r in RULES:
        assert f"({r.source_url})" in table


def test_rules_table_shows_each_rules_verification_date():
    checked = dataclasses.replace(RULES[0], status="verified", verified_by="roanuk", verified_on="2026-11-02")
    pending = dataclasses.replace(RULES[1], status="to_verify", verified_by=None, verified_on=None)
    dropped = dataclasses.replace(RULES[17], status="excluded", verified_by=None, verified_on=None)
    table = rules_table([checked, pending, dropped])
    first, second, third = table.splitlines()[2:]
    assert first.endswith("| verified | 2026-11-02 |")
    assert second.endswith("| to_verify | not yet |")
    # An excluded rule is never checked later, so it must not read as a check still to come.
    assert third.endswith("| excluded | not verified (excluded) |")
    assert "not yet" not in third


def test_update_index_replaces_only_the_marked_table(tmp_path):
    page = tmp_path / "index.md"
    page.write_text(f"# Title\n\nBefore.\n\n{TABLE_START}\nold table\n{TABLE_END}\n\nAfter.\n")
    assert update_index(page, RULES) is True
    text = page.read_text()
    assert text.startswith("# Title\n\nBefore.\n\n") and text.endswith(f"{TABLE_END}\n\nAfter.\n")
    assert "old table" not in text and rules_table(RULES) in text
    assert update_index(page, RULES) is False
    page.write_text("no markers")
    with pytest.raises(ValueError):
        update_index(page, RULES)


def test_docs_rules_table_is_current_and_names_the_edge_cases():
    """The teardown is docs/index.html (tests/test_teardown.py); docs/rules.md holds the generated rules table."""
    text = (REPO / "docs" / "rules.md").read_text()
    assert rules_table(RULES) in text, "run uv run python -m eval.report index"
    assert "Form 5695" in text and "September 30, 2025" in text and "June 30, 2026" in text
    assert "(index.html)" in text and "(results.md)" in text


def test_docs_results_follows_the_prd_order():
    path = REPO / "docs" / "results.md"
    assert path.exists(), "run uv run python -m eval.report"
    assert sections_in_order(path.read_text())
