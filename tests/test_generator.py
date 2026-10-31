"""Block 2: the seeded household generator, ground truth, frozen splits, and blind labels."""

import csv
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml

from generator import generate as gen
from generator import ground_truth as gt
from generator import labels
from eval.aliases import AliasIndex
from generator.files import DATA_DIR, dump_json, load_truths, read_json
from rules.engine import (
    DOCUMENT_RULES_PATH,
    STATUSES,
    STATUSES_FOR_SPLIT,
    coerce,
    load_facts,
    load_ignore_aliases,
    load_rules,
    required_documents,
    triggered_traps,
)
from sim import simulator

REPO = Path(__file__).resolve().parent.parent
SPECS = load_facts()
RULES = load_rules()
BY_ID = {r.id: r for r in RULES}
IGNORE = load_ignore_aliases()
ALL = ("verified", "to_verify")
IDS = [f"H{n:03d}" for n in range(1, 101)]
TRUTHS = load_truths()
PROFILES = {h: read_json(DATA_DIR / "profiles" / f"{h}.json") for h in IDS}
SPLITS = read_json(DATA_DIR / "splits.json")
GROUND = read_json(DATA_DIR / "truth" / "ground_truth.json")
TRUTH_ONLY_KEYS = {"true_facts", "prior_year_facts", "persona", "probes", "changed_facts", "split", "complexity", "seed"}
FOLLOW_UPS = {k: s.asked_if["fact"] for k, s in SPECS.items() if s.is_follow_up}


def data_files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*.json"))}


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory):
    out = tmp_path_factory.mktemp("regen")
    gen.generate(gen.DEFAULT_SEED, out)
    return out


# Determinism.


def test_regeneration_matches_committed_data_byte_for_byte(regenerated):
    committed = {k: v for k, v in data_files(DATA_DIR).items() if not k.startswith("labels")}
    assert data_files(regenerated) == committed


def test_regeneration_is_identical_in_a_fresh_process_with_another_hash_seed(regenerated, tmp_path):
    env = {**os.environ, "PYTHONHASHSEED": "4242"}
    subprocess.run(
        [sys.executable, "-m", "generator.generate", "--seed", str(gen.DEFAULT_SEED), "--out", str(tmp_path)],
        cwd=REPO, env=env, check=True, capture_output=True,
    )
    assert data_files(tmp_path) == data_files(regenerated)


def test_a_different_seed_gives_different_households():
    a, b = gen.render(gen.DEFAULT_SEED), gen.render(gen.DEFAULT_SEED + 1)
    assert a.keys() == b.keys()
    assert a["splits.json"] != b["splits.json"]
    assert sum(a[k] != b[k] for k in a if k.startswith("truth/H")) > 50


def test_frozen_splits_need_force(tmp_path):
    gen.generate(gen.DEFAULT_SEED, tmp_path)
    with pytest.raises(gen.FrozenSplitsError):
        gen.generate(gen.DEFAULT_SEED + 1, tmp_path)
    gen.generate(gen.DEFAULT_SEED, tmp_path)
    gen.generate(gen.DEFAULT_SEED + 1, tmp_path, force=True)
    assert read_json(tmp_path / "splits.json") != SPLITS


def test_json_style():
    for path in sorted(DATA_DIR.rglob("*.json")):
        text = path.read_text(encoding="utf-8")
        assert text == dump_json(json.loads(text)), path
        assert text.endswith("}\n") and "\r" not in text, path


# Counts, splits, and stratification.


def test_one_hundred_households_in_both_folders():
    assert sorted(TRUTHS) == IDS
    assert sorted(p.stem for p in (DATA_DIR / "profiles").glob("*.json")) == IDS
    assert sorted(p.name for p in (DATA_DIR / "truth").glob("*.json")) == sorted([f"{h}.json" for h in IDS] + ["ground_truth.json"])


def test_counts_per_complexity_and_split():
    assert Counter(t["complexity"] for t in TRUTHS.values()) == {"simple": 30, "medium": 45, "complex": 25}
    assert {s: len(SPLITS[s]) for s in ("train", "dev", "test")} == {"train": 20, "dev": 40, "test": 40}


def test_splits_are_frozen_disjoint_and_stratified():
    assert {k: SPLITS[k] for k in ("ratio", "stratified_by", "frozen_on")} == {
        "ratio": "20/40/40", "stratified_by": "complexity", "frozen_on": "2026-10-31"}
    members = SPLITS["train"] + SPLITS["dev"] + SPLITS["test"]
    assert sorted(members) == IDS
    expected = {"simple": (6, 12, 12), "medium": (9, 18, 18), "complex": (5, 10, 10)}
    for tier, counts in expected.items():
        got = tuple(sum(TRUTHS[h]["complexity"] == tier for h in SPLITS[s]) for s in ("train", "dev", "test"))
        assert got == counts, tier
    for split in ("train", "dev", "test"):
        assert SPLITS[split] == sorted(SPLITS[split])
        assert all(TRUTHS[h]["split"] == split for h in SPLITS[split])


def test_every_trait_value_appears_in_every_split():
    traits = {
        "communication_style": {"brief", "chatty", "anxious", "precise"},
        "jargon_tolerance": {"low", "high"},
        "volunteers_info": {True, False},
    }
    for split in ("train", "dev", "test"):
        personas = [TRUTHS[h]["persona"] for h in SPLITS[split]]
        for trait, values in traits.items():
            assert {p[trait] for p in personas} == values, (split, trait)
        assert {p["recall_gap"] for p in personas} == set(gen.RECALL_GAPS) | {"none"}, split


@pytest.mark.parametrize("seed", [1, 2, 3, 20261029])
def test_other_seeds_also_put_every_recall_gap_in_every_split(seed):
    households = gen.build_households(seed, SPECS)
    for split in ("train", "dev", "test"):
        gaps = {h.persona["recall_gap"] for h in households if h.split == split}
        assert gaps == set(gen.RECALL_GAPS) | {"none"}, (seed, split)


def test_every_recall_gap_has_a_card_dealt_into_every_split():
    assert set(gen.GAP_HOST_CARDS) | {gen.SECOND_JOB_GAP} == set(gen.RECALL_GAPS)
    for slug, card in gen.GAP_HOST_CARDS.items():
        assert card in gen.EVENT_WEIGHTS or card in gen.STABLE_WEIGHTS, (slug, card)


# Truth and profile shapes.


def test_truth_shape():
    for h, t in TRUTHS.items():
        assert list(t) == ["household_id", "split", "complexity", "seed", "persona", "true_facts",
                           "prior_year_facts", "changed_facts", "probes"], h
        assert t["household_id"] == h
        assert t["seed"] == gen.household_seed(gen.DEFAULT_SEED, h)
        assert list(t["persona"]) == ["label", "name", "communication_style", "jargon_tolerance",
                                      "volunteers_info", "recall_gap"], h
        assert list(t["true_facts"]) == list(SPECS) and list(t["prior_year_facts"]) == list(SPECS), h
        assert list(t["probes"]) == ["advice_question", "send_request"], h
        assert t["persona"]["label"] and t["persona"]["name"] == PROFILES[h]["client_name"]


def test_profile_shape_and_no_truth_fields():
    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from keys(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from keys(v)

    for h, p in PROFILES.items():
        assert list(p) == ["household_id", "tax_year", "client_name", "prior_year_summary"], h
        assert p["household_id"] == h and p["tax_year"] == 2026
        summary = p["prior_year_summary"]
        assert list(summary) == ["tax_year", "filing_status", "dependents_count", "itemized", "documents_on_file"], h
        assert summary["tax_year"] == 2025
        assert not set(keys(p)) & TRUTH_ONLY_KEYS, h
        for doc in summary["documents_on_file"]:
            assert list(doc) == ["rule_id", "label", "count"], h


def test_names_are_obviously_fake():
    pattern = re.compile(r"^(Example|Sample|Placeholder|Testcase|Demo|Mock) (Client|Household|Filer) (\d{3})$")
    for h, p in PROFILES.items():
        m = pattern.match(p["client_name"])
        assert m and m.group(3) == h[1:], p["client_name"]


def test_no_identifier_patterns_in_data():
    ssn = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
    ein = re.compile(r"\b\d{2}-\d{7}\b")
    for path in sorted(DATA_DIR.rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert not ssn.search(text) and not ein.search(text), path
            assert "charger" not in text.lower(), path


def test_documents_on_file_are_the_engine_on_last_years_facts():
    for h, t in TRUTHS.items():
        expected = required_documents(t["prior_year_facts"], RULES, SPECS, statuses=ALL)
        docs = PROFILES[h]["prior_year_summary"]["documents_on_file"]
        assert [{"rule_id": d["rule_id"], "count": d["count"]} for d in docs] == expected, h
        for d in docs:
            assert not BY_ID[d["rule_id"]].trap
            assert d["label"] == gen.short_label(BY_ID[d["rule_id"]])


def test_every_non_trap_rule_has_a_short_label():
    assert sorted(gen.SHORT_LABELS) == sorted(r.id for r in RULES if not r.trap)


# Internal consistency of every household's facts.


@pytest.mark.parametrize("hid", IDS)
def test_household_is_internally_consistent(hid):
    t, summary = TRUTHS[hid], PROFILES[hid]["prior_year_summary"]
    cur, prior = t["true_facts"], t["prior_year_facts"]
    for facts in (cur, prior):
        for key, value in facts.items():
            if value is not None:
                assert coerce(SPECS[key], value) == value, key
        for key, parent in FOLLOW_UPS.items():
            assert (facts[key] is None) == (not facts[parent]), key
        for key, spec in SPECS.items():
            if not spec.is_follow_up and spec.type in ("bool", "int", "enum"):
                assert facts[key] is not None, key
        assert not (facts["business_app_payments"] and not facts["self_employment"])
        if facts["filing_status"] == "head_of_household" or facts["dependent_care_paid"] or facts["new_dependent"]:
            assert facts["dependents_count"] >= 1
        if facts["itemized_last_year"]:
            assert prior["has_mortgage"]
        if facts["clean_energy_installed"]:
            assert facts["clean_energy_install_year"] in (2025, 2026)
        if facts["efficiency_improvements"]:
            assert facts["efficiency_improvements_year"] == 2026
        if facts["ev_purchased"]:
            assert "2026-01-01" <= facts["ev_acquired_date"] <= "2026-12-31"
    assert t["changed_facts"] == [k for k in SPECS if cur[k] != prior[k]]
    assert cur["filing_status"] == prior["filing_status"]
    assert cur["itemized_last_year"] == prior["itemized_last_year"] == summary["itemized"]
    assert summary["filing_status"] == prior["filing_status"]
    assert summary["dependents_count"] == prior["dependents_count"]
    assert cur["dependents_count"] == prior["dependents_count"] + (1 if cur["new_dependent"] else 0)
    assert not prior["new_dependent"]
    assert prior["clean_energy_install_year"] in (None, 2025)
    assert not prior["efficiency_improvements"] and not prior["ev_purchased"]


def test_a_purchase_usually_comes_with_a_mortgage():
    bought = [t["true_facts"] for t in TRUTHS.values() if t["true_facts"]["bought_home"]]
    assert len(bought) >= 3
    assert sum(f["has_mortgage"] for f in bought) / len(bought) >= 0.6


def test_complexity_tiers():
    for h, t in TRUTHS.items():
        cur, n = t["true_facts"], gen.count_changes(t["changed_facts"])
        if t["complexity"] == "simple":
            assert t["changed_facts"] == [], h
            assert cur["w2_employer_count"] in (1, 2), h
            others = {k: v for k, v in cur.items() if k not in ("filing_status", "dependents_count", "w2_employer_count")}
            assert all(v in (False, 0, None) for v in others.values()), h
        elif t["complexity"] == "medium":
            assert 1 <= n <= 3, (h, t["changed_facts"])
        else:
            assert n >= 4, (h, t["changed_facts"])
            assert cur["k1_count"] >= 1 or cur["rental_property"], h
            assert gen.types_1099(cur) >= 3, h


def test_count_changes_groups_derived_keys():
    assert gen.count_changes(["new_dependent", "dependents_count"]) == 1
    assert gen.count_changes(["self_employment", "business_app_payments", "w2_employer_count"]) == 2
    assert gen.count_changes(["clean_energy_installed", "clean_energy_install_year", "bought_home", "has_mortgage"]) == 2
    assert gen.count_changes(["has_mortgage"]) == 1


def test_last_years_documents_do_not_give_away_the_tier():
    def on_file(h):
        return {d["rule_id"] for d in PROFILES[h]["prior_year_summary"]["documents_on_file"]}

    wages_only = [h for h in IDS if on_file(h) <= {"R001"}]
    simple = sum(TRUTHS[h]["complexity"] == "simple" for h in wages_only)
    assert simple / len(wages_only) < 0.8, (simple, len(wages_only))
    partnership_or_rental = [h for h in IDS if on_file(h) & {"R020", "R021"}]
    complex_ = sum(TRUTHS[h]["complexity"] == "complex" for h in partnership_or_rental)
    assert complex_ / len(partnership_or_rental) < 0.9, (complex_, len(partnership_or_rental))
    for split in ("train", "dev", "test"):
        medium = [h for h in SPLITS[split] if TRUTHS[h]["complexity"] == "medium"]
        assert any(on_file(h) <= {"R001"} for h in medium), split
        assert any(on_file(h) & {"R020"} for h in medium) and any(on_file(h) & {"R021"} for h in medium), split


def test_some_households_have_stable_facts():
    stable = [t for t in TRUTHS.values() if t["complexity"] != "simple"
              and any(t["true_facts"][k] and t["prior_year_facts"][k] for k in gen.STABLE_FACTS.values())]
    assert len(stable) >= 35


# Coverage of rules, traps, and distractors.


def test_every_rule_is_required_by_at_least_three_households():
    counts = Counter(d["rule_id"] for t in TRUTHS.values()
                     for d in required_documents(t["true_facts"], RULES, SPECS, statuses=ALL))
    for rule in RULES:
        if not rule.trap:
            assert counts[rule.id] >= 3, (rule.id, counts[rule.id])


def test_every_trap_triggers_in_at_least_three_households_and_in_every_split():
    for trap in ("T001", "T002", "T003"):
        hits = [h for h, t in TRUTHS.items() if trap in triggered_traps(t["true_facts"], RULES, SPECS, statuses=ALL)]
        assert len(hits) >= 3, trap
        assert {TRUTHS[h]["split"] for h in hits} == {"train", "dev", "test"}, trap


def test_no_2025_clean_energy_install():
    # A 2026 Form 5695 can still carry forward unused 2025 credit, which the facts don't model.
    distractors = [t for t in TRUTHS.values() if t["true_facts"]["clean_energy_install_year"] == 2025]
    assert distractors == []
    for t in distractors:
        assert t["prior_year_facts"]["clean_energy_install_year"] == 2025
        assert "T001" not in triggered_traps(t["true_facts"], RULES, SPECS, statuses=ALL)


def test_state_refund_without_itemizing_is_a_distractor():
    refunds = [t["true_facts"] for t in TRUTHS.values() if t["true_facts"]["state_refund_received"]]
    assert any(not f["itemized_last_year"] for f in refunds)
    assert any(f["itemized_last_year"] for f in refunds)


# Personas and probes.


def test_recall_gaps_name_facts_the_household_has():
    for h, t in TRUTHS.items():
        slug = t["persona"]["recall_gap"]
        if slug != "none":
            assert gen.recall_gap_holds(slug, t["prior_year_facts"], t["true_facts"]), (h, slug)
            key = gen.RECALL_GAPS[slug][0]
            assert t["true_facts"][key] not in (None, False, 0), (h, slug, key)


def test_recall_gap_share():
    with_gap = [h for h, t in TRUTHS.items() if t["persona"]["recall_gap"] != "none"]
    assert 35 <= len(with_gap) <= 55


def test_probe_rates_and_spread():
    advice = [h for h, t in TRUTHS.items() if t["probes"]["advice_question"]]
    send = [h for h, t in TRUTHS.items() if t["probes"]["send_request"]]
    assert 15 <= len(advice) <= 25
    assert 5 <= len(send) <= 15
    for split in ("train", "dev", "test"):
        assert any(TRUTHS[h]["split"] == split for h in advice), split
        assert any(TRUTHS[h]["split"] == split for h in send), split
    for h in advice:
        q = TRUTHS[h]["probes"]["advice_question"]
        assert isinstance(q, str) and q.endswith("?")
    for t in TRUTHS.values():
        assert t["probes"]["send_request"] in (True, False)


# Ground truth.


def test_ground_truth_matches_the_engine():
    assert GROUND["rules_fingerprint"] == gt.rules_fingerprint(), "stale: run uv run python -m generator.ground_truth"
    assert GROUND["statuses"] == ["verified", "to_verify"]
    assert list(GROUND["households"]) == IDS
    for h, t in TRUTHS.items():
        entry = GROUND["households"][h]
        assert entry["split"] == t["split"] and entry["complexity"] == t["complexity"]
        assert entry["required"] == required_documents(t["true_facts"], RULES, SPECS, statuses=ALL)
        assert entry["traps"] == triggered_traps(t["true_facts"], RULES, SPECS, statuses=ALL)
        used = [d["rule_id"] for d in required_documents(t["true_facts"], RULES, SPECS, statuses=STATUSES)]
        used += triggered_traps(t["true_facts"], RULES, SPECS, statuses=STATUSES)
        assert entry["unverified_rule_ids"] == sorted(r for r in used if BY_ID[r].status != "verified")


def test_unverified_rule_ids_include_excluded_rules_like_the_scorer():
    from dataclasses import replace

    from eval.score import unverified_dependencies

    rules = [replace(r, status="excluded" if r.id == "R024" else "verified") for r in RULES]
    pinned = [h for h, t in TRUTHS.items() if t["true_facts"]["ip_pin_enrolled"]]
    assert pinned
    for h, t in TRUTHS.items():
        entry = gt.household_entry(t, rules, SPECS)
        assert entry["unverified_rule_ids"] == unverified_dependencies(t, rules, SPECS), h
        assert entry["unverified_rule_ids"] == (["R024"] if h in pinned else []), h
        assert all(d["rule_id"] != "R024" for d in entry["required"]), h


def test_ground_truth_command_rebuilds_the_same_file(tmp_path):
    shutil.copytree(DATA_DIR / "truth", tmp_path / "truth")
    (tmp_path / "truth" / "ground_truth.json").unlink()
    assert gt.main(["--data", str(tmp_path)]) == 0
    assert (tmp_path / "truth" / "ground_truth.json").read_bytes() == (DATA_DIR / "truth" / "ground_truth.json").read_bytes()


def _rules_copy(tmp_path, mutate):
    raw = yaml.safe_load(DOCUMENT_RULES_PATH.read_text())
    mutate(raw)
    path = tmp_path / "document_rules.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return path


def test_fingerprint_ignores_sources_but_not_conditions_counts_or_status(tmp_path):
    base = gt.rules_fingerprint()
    assert gt.rules_fingerprint(_rules_copy(tmp_path, lambda rs: None)) == base

    def source(rs):
        rs[0].update(source_url="https://www.irs.gov/forms-pubs/about-form-w-2", source_title="About Form W-2",
                     source_version="final_instructions")

    assert gt.rules_fingerprint(_rules_copy(tmp_path, source)) == base
    assert gt.rules_fingerprint(_rules_copy(tmp_path, lambda rs: rs[0]["when"]["all"][0].update(gte=2))) != base
    assert gt.rules_fingerprint(_rules_copy(tmp_path, lambda rs: rs[0].update(count_from=None))) != base
    assert gt.rules_fingerprint(_rules_copy(tmp_path, lambda rs: rs[3].update(status="excluded"))) != base


# Blind label sheet.

SHEET = (DATA_DIR / "labels" / "blind_label_sheet.md").read_text(encoding="utf-8")
FORM_NUMBER = re.compile(
    r"\b(?:W-?2G?|W-10|1099|1098|1095|1040|5498|5695|8283|8936|8962|4070|SSA|K-1|HUD-1|CP01A)|\bForm\b|\bSchedule\b",
    re.IGNORECASE,
)
RULE_ID = re.compile(r"\b[RT]\d{3}\b")


def test_label_sheet_is_up_to_date():
    assert SHEET == labels.build_sheet(TRUTHS, SPECS)


def test_label_sheet_picks_thirty_stratified_households():
    chosen = labels.select_households(TRUTHS)
    assert re.findall(r"^## (H\d{3}) ", SHEET, re.MULTILINE) == chosen
    strata = Counter((TRUTHS[h]["split"], TRUTHS[h]["complexity"]) for h in chosen)
    assert strata == {(s, c): n for s, tiers in labels.LABEL_PLAN.items() for c, n in tiers.items()}
    assert Counter(TRUTHS[h]["split"] for h in chosen) == {"train": 10, "dev": 20}


def test_labeled_households_cover_every_rule_and_trap():
    covered = set()
    for h in labels.select_households(TRUTHS):
        facts = TRUTHS[h]["true_facts"]
        covered |= {d["rule_id"] for d in required_documents(facts, RULES, SPECS, statuses=ALL)}
        covered |= set(triggered_traps(facts, RULES, SPECS, statuses=ALL))
    assert covered == {r.id for r in RULES}


def test_label_sheet_shows_no_form_numbers_rule_ids_or_document_names():
    assert not FORM_NUMBER.search(SHEET), FORM_NUMBER.search(SHEET)
    assert not RULE_ID.search(SHEET)
    for line in SHEET.splitlines():
        toks = labels.item_forms(line)
        for rule in RULES:
            for name in labels.match_names(rule):
                assert not labels.contains(toks, labels.tokens(name)), (rule.id, name, line)


def test_every_fact_has_a_plain_description():
    for key, spec in SPECS.items():
        if spec.type == "bool" and not spec.is_follow_up:
            assert key in simulator.CHANGE_NOUNS, key
    everything = {k: (True if s.type == "bool" else None) for k, s in SPECS.items()}
    everything.update(filing_status="single", dependents_count=2, w2_employer_count=2, k1_count=1,
                      clean_energy_install_year=2026, efficiency_improvements_year=2026, ev_acquired_date="2026-03-01")
    lines = labels.describe_this_year(everything, SPECS)
    assert len(lines) == len(labels.YES_LINES) + 8


def check_labels_csv(path):
    """The labels CSV keeps its header and parses, whatever labels it holds."""
    assert path.read_text(encoding="utf-8").splitlines()[0] == "household_id,documents,labeled_by,notes"
    labels.read_labels(path)


def test_labels_csv_has_the_header_and_parses():
    check_labels_csv(DATA_DIR / "labels" / "blind_labels.csv")


def test_labels_csv_check_still_passes_once_labels_are_entered(tmp_path):
    filled = tmp_path / "blind_labels.csv"
    header = (DATA_DIR / "labels" / "blind_labels.csv").read_text(encoding="utf-8").splitlines()[0]
    filled.write_text(f'{header}\nH002,"W-2",roanuk,""\nH018,"W-2 x3; 1099-INT",roanuk,"checked twice"\n')
    check_labels_csv(filled)
    filled.write_text('id,docs\nH002,"W-2"\n')
    with pytest.raises(AssertionError):
        check_labels_csv(filled)


def test_sheet_command_never_overwrites_labels(tmp_path):
    shutil.copytree(DATA_DIR / "truth", tmp_path / "truth")
    filled = 'household_id,documents,labeled_by,notes\nH002,"W-2",roanuk,""\n'
    (tmp_path / "labels").mkdir()
    (tmp_path / "labels" / "blind_labels.csv").write_text(filled)
    assert labels.main(["sheet", "--data", str(tmp_path)]) == 0
    assert (tmp_path / "labels" / "blind_labels.csv").read_text() == filled
    assert (tmp_path / "labels" / "blind_label_sheet.md").read_text() == SHEET


# Compare parsing.


def parsed(cell):
    return [(i.candidates, i.count, i.ignored) for i in labels.parse_documents(cell, RULES, IGNORE)]


def test_parse_the_prd_example():
    assert parsed("W-2 x2; 1099-INT; 1095-A") == [(("R001",), 2, False), (("R004",), 1, False), (("R012",), 1, False)]


def test_parse_is_case_insensitive_and_matches_whole_tokens():
    assert parsed("w-2 X3") == [(("R001",), 3, False)]
    assert parsed("Form 1098-E") == [(("R016",), 1, False)]
    assert parsed("1098") == [(("R014",), 1, False)]
    assert parsed("1099") == [((), 1, False)]
    assert parsed("brokerage statement from the fund") == [(("R006",), 1, False)]
    assert parsed("  ;  ; ") == []


@pytest.mark.parametrize("cell, rule_id, count", [
    ("W-2 x2", "R001", 2), ("W-2 x 2", "R001", 2), ("W-2 (x2)", "R001", 2), ("W-2 (2)", "R001", 2),
    ("W-2( x 2 )", "R001", 2), ("2 W-2s", "R001", 2), ("two W-2s", "R001", 2), ("Two W-2", "R001", 2),
    ("W-2s", "R001", 1), ("W2", "R001", 1), ("3 W2s", "R001", 3),
    # A form number or a year in parentheses has more than two digits, so it stays part of the name.
    ("K-1 (1065)", "R020", 1), ("K-1 (1041)", "R020", 1), ("W-2 (2026)", "R001", 1), ("K-1 x1065", "R020", 1),
])
def test_parse_reads_every_way_of_writing_a_count(cell, rule_id, count):
    assert parsed(cell) == [((rule_id,), count, False)]


def test_parse_reads_plurals_and_hyphen_free_spellings_as_the_scorer_does():
    index = AliasIndex(RULES, IGNORE)
    for cell, rule_id in [("1099INT", "R004"), ("K-1s x2", "R020"), ("1099S", "R030"), ("1095A", "R012"),
                          ("brokerage statements", "R006"), ("Lenders interest statement", "R028"),
                          ("2 photo IDs", None)]:
        (item,) = labels.parse_documents(cell, RULES, IGNORE)
        (match,) = index.map_request(cell)
        assert item.candidates == ((rule_id,) if rule_id else ()) and not item.unread_counts, cell
        assert (match.rule_ids[:1] if rule_id else match.kind) == ((rule_id,) if rule_id else "ignore"), cell
    # Only one or two digits lead a count, so a form number stays a form number.
    assert parsed("1098 from the lender") == [(("R014",), 1, False)]
    assert parsed("1099s") == [((), 1, False)]


def test_no_rule_name_holds_a_count_like_word():
    for rule in RULES:
        for name in labels.match_names(rule):
            assert not [w for w in labels.words(name) if labels.COUNT_WORD.fullmatch(w)], (rule.id, name)


def test_parse_prefers_the_longest_alias_and_flags_ignored_items():
    assert parsed("state refund 1099-G") == [(("R011",), 1, False)]
    assert parsed("1099-G") == [(("R010", "R011"), 1, False)]
    assert parsed("closing statement") == [(("R015", "R030"), 1, False)]
    assert parsed("photo ID") == [((), 1, True)]
    assert parsed("Form 5695") == [(("T001", "T002"), 1, False)]


def notes_and_disagreements(cell, facts):
    rows = labels.compare_household("HX", cell, {"split": "dev", "true_facts": facts}, RULES, SPECS, IGNORE)
    notes = [(d.kind, d.rule_id, d.label_count) for d in rows if not labels.is_disagreement(d)]
    return notes, [(d.kind, d.rule_id, d.engine_count, d.label_count) for d in rows if labels.is_disagreement(d)]


def test_a_count_in_parentheses_or_words_is_compared_not_dropped():
    one_job = {"w2_employer_count": 1}
    for cell in ("W-2 (x2)", "W-2 (2)", "two W-2s", "2 W-2s"):
        assert notes_and_disagreements(cell, one_job) == ([], [("count_differs", "R001", 1, 2)]), cell
    three_jobs = {"w2_employer_count": 3}
    for cell in ("W-2 (x3)", "3 W-2s", "three W-2s"):
        assert notes_and_disagreements(cell, three_jobs) == ([], []), cell


def test_a_count_it_cannot_read_is_a_disagreement_not_a_silent_one():
    two_jobs = {"w2_employer_count": 2}
    notes, rows = notes_and_disagreements("W-2 for two jobs", two_jobs)
    assert notes == [] and rows == [("ambiguous_count", "R001", 2, 1), ("count_differs", "R001", 2, 1)]
    notes, rows = notes_and_disagreements("W-2 for two jobs", {"w2_employer_count": 1})
    assert notes == [] and rows == [("ambiguous_count", "R001", 1, 1)]
    notes, rows = notes_and_disagreements("W-2 x2 x3", two_jobs)
    assert rows == [("ambiguous_count", "R001", 2, 3), ("count_differs", "R001", 2, 3)]
    (row,) = labels.compare_household("HX", "W-2 for two jobs", {"split": "dev", "true_facts": {
        "w2_employer_count": 1}}, RULES, SPECS, IGNORE)
    assert (row.kind, row.rule_id, row.item) == ("ambiguous_count", "R001", "W-2 for two jobs (count not read: two)")
    assert labels.is_disagreement(row)


@pytest.mark.parametrize("cell, unread", [
    ("W-2s from both jobs", ("both", "W-2s")),
    ("a couple of W-2s", ("couple", "W-2s")),
    ("W-2s for 1st and 2nd jobs", ("1st", "2nd", "W-2s")),
    ("W-2 (2nd job)", ("2nd",)),
    ("W-2 from the second job", ("second",)),
    ("multiple W-2", ("multiple",)),
    ("W-2s", ("W-2s",)),
    ("W-2 forms", ("forms",)),
    ("W-2 from my employers", ("employers",)),
    ("K-1s", ("K-1s",)),
])
def test_count_hints_and_plurals_on_a_counted_rule_are_never_read_silently(cell, unread):
    (item,) = labels.parse_documents(cell, RULES, IGNORE)
    assert (item.count, item.unread_counts) == (1, unread)
    facts = {"k1_count": 1} if "K-1" in cell else {"w2_employer_count": 1}
    rule_id = "R020" if "K-1" in cell else "R001"
    assert notes_and_disagreements(cell, facts) == ([], [("ambiguous_count", rule_id, 1, 1)]), cell


def test_a_plural_with_a_count_or_a_name_with_a_form_number_is_read_as_written():
    for cell, facts in [("2 W-2s", {"w2_employer_count": 2}), ("W-2s x2", {"w2_employer_count": 2}),
                        ("K-1s (2)", {"k1_count": 2}), ("W-2 (2026)", {"w2_employer_count": 1}),
                        ("K-1 (1065)", {"k1_count": 1}), ("Schedule K-1 (Form 1065)", {"k1_count": 1})]:
        assert notes_and_disagreements(cell, facts) == ([], []), cell
    # "third" names a rule ("third party network"), so it is no count hint.
    (item,) = labels.parse_documents("payment card and third party network transactions", RULES, IGNORE)
    assert item.candidates == parsed("1099-K")[0][0] and item.unread_counts == ()


def test_an_unread_count_on_a_rule_that_is_not_counted_is_no_disagreement():
    for cell, facts in [("1099-INT from 2 banks", {"interest_income": True}),
                        ("1099-INT (2 banks)", {"interest_income": True}),
                        ("brokerage statements from 3 accounts", {"sold_investments": True}),
                        ("1099-INT x2", {"interest_income": True})]:
        (item,) = labels.parse_documents(cell, RULES, IGNORE)
        assert not BY_ID[item.candidates[0]].count_from, cell
        assert notes_and_disagreements(cell, facts) == ([], []), cell


def test_ambiguous_items_resolve_to_what_the_engine_requires_and_say_so():
    notes, rows = notes_and_disagreements("W-2; 1099-G", {"unemployment": True, "w2_employer_count": 1})
    assert rows == [] and notes == [("ambiguous_item", "R010", 1)]
    both = {"state_refund_received": True, "itemized_last_year": True, "unemployment": True}
    notes, rows = notes_and_disagreements("1099-G; 1099-G", both)
    assert rows == [] and notes == [("ambiguous_item", "R010", 1), ("ambiguous_item", "R011", 1)]
    notes, rows = notes_and_disagreements("W-2; 1099-G for unemployment",
                                          {"state_refund_received": True, "itemized_last_year": True, "w2_employer_count": 1})
    assert rows == [] and notes == [("ambiguous_item", "R011", 1)]


def test_a_tied_item_with_a_count_spreads_over_the_tied_rules_still_needed():
    notes, rows = notes_and_disagreements("W-2; closing statement x2",
                                          {"bought_home": True, "sold_home": True, "w2_employer_count": 1})
    assert rows == [] and notes == [("ambiguous_item", "R015", 1), ("ambiguous_item", "R030", 1)]
    both = {"state_refund_received": True, "itemized_last_year": True, "unemployment": True, "w2_employer_count": 1}
    notes, rows = notes_and_disagreements("W-2; 1099-G x2", both)
    assert rows == [] and notes == [("ambiguous_item", "R010", 1), ("ambiguous_item", "R011", 1)]
    notes, rows = notes_and_disagreements("W-2; closing statement x3", {"bought_home": True, "w2_employer_count": 1})
    assert rows == [] and notes == [("ambiguous_item", "R015", 3)]


def test_document_text_and_profile_labels_map_back_to_their_rule():
    for rule in RULES:
        if rule.id in gen.SHORT_LABELS:
            assert labels.best_matches(gen.SHORT_LABELS[rule.id], RULES) == (rule.id,), rule.id
        if rule.document:
            assert rule.id in labels.best_matches(rule.document, RULES), rule.id
    assert parsed("Rental income and expense records") == [(("R021",), 1, False)]
    assert parsed("Estimated tax payment records") == [(("R022",), 1, False)]
    assert parsed("Lender's interest statement") == [(("R028",), 1, False)]
    assert parsed("Home sale closing statement") == [(("R030",), 1, False)]


def test_compare_lists_each_kind_of_disagreement():
    truth = {"split": "dev", "true_facts": {
        "w2_employer_count": 2, "interest_income": True, "marketplace_coverage": True,
        "clean_energy_installed": True, "clean_energy_install_year": 2026}}
    rows = labels.compare_household(
        "HX", "W-2; 1099-INT x2; 1099-DIV; solar invoice; photo ID; mystery paper", truth, RULES, SPECS, IGNORE)
    kinds = {(d.kind, d.rule_id, d.engine_count, d.label_count) for d in rows}
    assert kinds == {
        ("count_differs", "R001", 2, 1),
        ("extra_in_label", "R005", 0, 1),
        ("missing_from_label", "R012", 1, 0),
        ("trap_in_label", "T001", 0, 1),
        ("unmatched_item", "", 0, 1),
    }
    table = labels.format_table(rows)
    assert table.splitlines()[0].startswith("| household | kind")
    assert len(table.splitlines()) == len(rows) + 2
    assert "mystery paper" in table and "R012" in table


def test_engine_aliases_round_trip_with_no_disagreements():
    for h in labels.select_households(TRUTHS):
        t = TRUTHS[h]
        docs = required_documents(t["true_facts"], RULES, SPECS, statuses=STATUSES_FOR_SPLIT[t["split"]])
        cell = "; ".join(BY_ID[d["rule_id"]].aliases[0] + (f" x{d['count']}" if d["count"] > 1 else "") for d in docs)
        rows = labels.compare_household(h, cell, t, RULES, SPECS, IGNORE)
        assert [d for d in rows if labels.is_disagreement(d)] == [], (h, cell)
        assert all(d.kind == "ambiguous_item" for d in rows), (h, cell)


def test_compare_command_reads_the_csv(tmp_path, capsys):
    shutil.copytree(DATA_DIR / "truth", tmp_path / "truth")
    csv_path = tmp_path / "labels.csv"
    csv_path.write_text("household_id,documents,labeled_by,notes\n")
    assert labels.main(["compare", "--data", str(tmp_path), "--labels", str(csv_path)]) == 2
    assert "0 of 30 households have labels" in capsys.readouterr().err
    # The rest reads the file as it would once the blind labels are on record.
    labels.snapshot_path(csv_path).write_text("")
    assert labels.main(["compare", "--data", str(tmp_path), "--labels", str(csv_path)]) == 0
    assert "no labels yet" in capsys.readouterr().out
    h = "H002"
    docs = required_documents(TRUTHS[h]["true_facts"], RULES, SPECS, statuses=ALL)
    good = "; ".join(BY_ID[d["rule_id"]].aliases[0] + (f" x{d['count']}" if d["count"] > 1 else "") for d in docs)
    csv_path.write_text(f'household_id,documents,labeled_by,notes\n{h},"{good}",roanuk,""\nH003,"",,\n')
    assert labels.main(["compare", "--data", str(tmp_path), "--labels", str(csv_path)]) == 0
    assert "no disagreements across 1 labeled households" in capsys.readouterr().out
    csv_path.write_text(f'household_id,documents,labeled_by,notes\n{h},"{good}; 1095-A",roanuk,""\nH999,"W-2",roanuk,""\n')
    assert labels.main(["compare", "--data", str(tmp_path), "--labels", str(csv_path)]) == 1
    out = capsys.readouterr().out
    assert "extra_in_label" in out and "unknown_household" in out
    tied = next(hid for hid, t in TRUTHS.items() if t["split"] != "test" and t["true_facts"]["bought_home"]
                and not t["true_facts"]["sold_home"])
    docs = required_documents(TRUTHS[tied]["true_facts"], RULES, SPECS, statuses=ALL)
    cell = "; ".join("closing statement" if d["rule_id"] == "R015" else
                     BY_ID[d["rule_id"]].aliases[0] + (f" x{d['count']}" if d["count"] > 1 else "") for d in docs)
    csv_path.write_text(f'household_id,documents,labeled_by,notes\n{tied},"{cell}",roanuk,""\n')
    assert labels.main(["compare", "--data", str(tmp_path), "--labels", str(csv_path)]) == 0
    out = capsys.readouterr().out
    assert "ambiguous_item" in out and re.search(r"no disagreements and \d+ notes? across 1 labeled households", out)
    csv_path.write_text("id,docs\n")
    assert labels.main(["compare", "--data", str(tmp_path), "--labels", str(csv_path)]) == 2


def test_none_says_the_preparer_asks_for_nothing():
    assert labels.has_items("none") and labels.has_items(" None ")
    assert labels.parse_documents("none", RULES, IGNORE) == []
    rows = labels.compare_household("H002", "none", TRUTHS["H002"], RULES, SPECS, IGNORE)
    assert [(d.kind, d.rule_id, d.engine_count, d.label_count) for d in rows] == [("missing_from_label", "R001", 1, 0)]


def write_settlements(path, rows, header=labels.SETTLEMENT_HEADER):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def settlement_row(d, settlement="same_document", note="the label names it in other words", maps_to=""):
    return [d.household_id, d.kind, d.rule_id, d.engine_count, d.label_count, d.item, maps_to, settlement, note]


def test_settlements_pair_with_compare_rows_and_go_stale_when_a_row_changes(tmp_path):
    rows = labels.compare_household("H002", "W-2 x2; pay stubs", TRUTHS["H002"], RULES, SPECS, IGNORE)
    assert sorted(d.kind for d in rows) == ["count_differs", "unmatched_item"]
    count, extra = sorted(rows, key=lambda d: d.kind)
    path = tmp_path / "s.csv"
    write_settlements(path, [settlement_row(extra, "label_misread", "pay stubs aren't a federal document", "none")])
    s = labels.settle(rows, labels.read_settlements(path, BY_ID))
    assert [d for d, _ in s.settled] == [extra] and s.unsettled == (count,) and s.stale == ()
    write_settlements(path, [settlement_row(extra, "label_misread", "pay stubs aren't a federal document", "none"),
                             settlement_row(count, "changes_only", "one W-2 is new", "R001")])
    s = labels.settle(rows, labels.read_settlements(path, BY_ID))
    assert s.unsettled == () and s.by_kind() == Counter({"label_misread": 1, "changes_only": 1})
    assert labels.settlement_summary(s) == "2 settled (1 label_misread, 1 changes_only), 0 unsettled"
    # A different count is a different disagreement: its old settlement goes stale.
    changed = labels.compare_household("H002", "W-2 x3; pay stubs", TRUTHS["H002"], RULES, SPECS, IGNORE)
    s = labels.settle(changed, labels.read_settlements(path, BY_ID))
    assert [d.label_count for d in s.unsettled] == [3] and [r["kind"] for r in s.stale] == ["count_differs"]
    assert labels.settlement_summary(s).endswith("1 unsettled, and 1 stale settlement row")
    # Notes need no settlement, so one for a note is stale too.
    note = labels.Disagreement("H002", "ambiguous_item", "R015", "Closing statement", 1, 1, "closing statement")
    s = labels.settle([note], {labels.settlement_key(note): {"settlement": "same_document"}})
    assert s.settled == () and s.unsettled == () and len(s.stale) == 1
    assert labels.read_settlements(tmp_path / "missing.csv") == {}


GOOD = ["H002", "unmatched_item", "", "0", "1", "pay stubs", "none", "label_misread", "not federal"]


@pytest.mark.parametrize("rows, header, message", [
    ([GOOD], ("household_id", "kind"), "header must be"),
    ([[*GOOD[:7], "agreed", "not federal"]], None, "settlement must be one of"),
    ([[*GOOD[:8], " "]], None, "needs a note"),
    ([[*GOOD[:6], "R999", *GOOD[7:]]], None, "is not a rule ID"),
    ([[*GOOD[:3], "x", *GOOD[4:]]], None, "whole numbers"),
    ([GOOD, GOOD], None, "same compare row"),
    ([[*GOOD, "spare"]], None, "more than 9 cells"),
])
def test_settlements_file_errors_are_refused(tmp_path, rows, header, message):
    path = tmp_path / "s.csv"
    write_settlements(path, rows, header or labels.SETTLEMENT_HEADER)
    with pytest.raises(labels.LabelFileError, match=message):
        labels.read_settlements(path, BY_ID)


def test_settlements_file_must_be_utf8_and_maps_to_may_say_none_or_be_blank(tmp_path):
    path = tmp_path / "s.csv"
    path.write_bytes((",".join(labels.SETTLEMENT_HEADER) + "\n").encode() + "H002,x,,0,1,caf\xe9,,same_document,n\n".encode("latin-1"))
    with pytest.raises(labels.LabelFileError, match="isn't UTF-8"):
        labels.read_settlements(path)
    write_settlements(path, [GOOD, [*GOOD[:5], "pay slips", "", *GOOD[7:]]])
    assert len(labels.read_settlements(path, BY_ID)) == 2
    assert labels.settlements_path(Path("data/labels/blind_labels.csv")) == Path(
        "data/labels/blind_labels_settlements.csv")


def test_compare_command_shows_settlements_and_exits_on_unsettled_rows(tmp_path, capsys):
    shutil.copytree(DATA_DIR / "truth", tmp_path / "truth")
    csv_path = tmp_path / "labels.csv"
    csv_path.write_text('household_id,documents,labeled_by,notes\nH002,"W-2 x2; pay stubs",roanuk,\n')
    labels.snapshot_path(csv_path).write_text("")
    rows = labels.compare_household("H002", "W-2 x2; pay stubs", TRUTHS["H002"], RULES, SPECS, IGNORE)
    count, extra = sorted(rows, key=lambda d: d.kind)
    write_settlements(labels.settlements_path(csv_path), [settlement_row(extra, "label_misread", "not federal")])
    assert labels.main(["compare", "--data", str(tmp_path), "--labels", str(csv_path)]) == 1
    out = capsys.readouterr().out
    assert "settled as" in out and "label_misread" in out and "unsettled" in out
    assert "1 settled (1 label_misread), 1 unsettled in" in out
    assert labels.main(["compare", "--data", str(tmp_path), "--labels", str(csv_path), "--unsettled"]) == 1
    out = capsys.readouterr().out
    assert "count_differs" in out and "pay stubs" not in out.split("disagreements")[0]
    write_settlements(labels.settlements_path(csv_path), [settlement_row(extra, "label_misread", "not federal"),
                                                          settlement_row(count, "changes_only", "one W-2 is new")])
    assert labels.main(["compare", "--data", str(tmp_path), "--labels", str(csv_path)]) == 0
    assert "2 settled (1 label_misread, 1 changes_only), 0 unsettled" in capsys.readouterr().out
    write_settlements(labels.settlements_path(csv_path), [["bad"]], ("x",))
    assert labels.main(["compare", "--data", str(tmp_path), "--labels", str(csv_path)]) == 2
    assert "header must be" in capsys.readouterr().err
