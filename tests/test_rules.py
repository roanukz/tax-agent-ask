import copy

import pytest
import yaml

from rules import engine
from rules.engine import (
    FactError,
    as_multiset,
    coerce,
    complete_facts,
    condition_holds,
    load_facts,
    load_rules,
    required_documents,
    rule_facts,
    triggered_traps,
)
from rules.validate import EXPECTED_RULE_IDS, validate

SPECS = load_facts()
RULES = load_rules()
ALL = ("verified", "to_verify")

H017 = {
    "filing_status": "married_joint",
    "dependents_count": 1,
    "w2_employer_count": 3,
    "self_employment": True,
    "business_app_payments": True,
    "interest_income": True,
    "has_mortgage": True,
    "marketplace_coverage": False,
    "clean_energy_installed": True,
    "clean_energy_install_year": 2026,
}


def test_fact_keys():
    assert len(SPECS) == 39
    assert sum(1 for s in SPECS.values() if not s.is_follow_up) == 36
    assert {k for k, s in SPECS.items() if s.determinable_from_prior_year} == {
        "filing_status", "dependents_count", "itemized_last_year"}
    assert SPECS["itemized_last_year"].prior_year_field == "itemized"


def test_follow_ups_are_the_year_and_date_keys():
    follow_ups = sorted(k for k, s in SPECS.items() if s.is_follow_up)
    assert follow_ups == ["clean_energy_install_year", "efficiency_improvements_year", "ev_acquired_date"]
    assert SPECS["ev_acquired_date"].asked_if == {"fact": "ev_purchased", "eq": True}


def test_rule_ids_in_order():
    assert [r.id for r in RULES] == EXPECTED_RULE_IDS
    assert [r.id for r in RULES if r.trap] == ["T001", "T002", "T003"]


@pytest.mark.parametrize(
    "key,value,stored",
    [
        ("self_employment", True, True),
        ("self_employment", "false", False),
        ("w2_employer_count", 3, 3),
        ("w2_employer_count", "2", 2),
        ("filing_status", "single", "single"),
        ("ev_acquired_date", "2026-03-04", "2026-03-04"),
        ("clean_energy_install_year", 2026, 2026),
    ],
)
def test_coerce_accepts(key, value, stored):
    assert coerce(SPECS[key], value) == stored


@pytest.mark.parametrize(
    "key,value",
    [
        ("self_employment", "yes"),
        ("self_employment", 1),
        ("w2_employer_count", True),
        ("w2_employer_count", -1),
        ("w2_employer_count", 11),
        ("w2_employer_count", 2.5),
        ("filing_status", "married"),
        ("ev_acquired_date", "March 2026"),
        ("ev_acquired_date", 2026),
        ("clean_energy_install_year", 2027),
        ("w2_employer_count", "\u00b2"),
        ("w2_employer_count", "--3"),
        ("w2_employer_count", "3.0"),
        ("ev_acquired_date", "20260304"),
        ("ev_acquired_date", "2026-W10-3"),
        ("ev_acquired_date", "2026-02-30"),
    ],
)
def test_coerce_rejects(key, value):
    with pytest.raises(FactError):
        coerce(SPECS[key], value)


def test_unrecorded_facts_count_as_no_or_zero():
    full = complete_facts({}, SPECS)
    assert full["self_employment"] is False
    assert full["w2_employer_count"] == 0
    assert full["filing_status"] is None
    assert full["ev_acquired_date"] is None
    assert full["clean_energy_install_year"] is None
    with pytest.raises(FactError):
        complete_facts({"not_a_key": True}, SPECS)


def test_conditions():
    facts = complete_facts({"w2_employer_count": 2, "ev_purchased": True, "ev_acquired_date": "2026-02-01"}, SPECS)
    assert condition_holds({"fact": "w2_employer_count", "gte": 1}, facts, SPECS)
    assert not condition_holds({"fact": "w2_employer_count", "gte": 3}, facts, SPECS)
    assert condition_holds({"fact": "w2_employer_count", "lte": 2}, facts, SPECS)
    assert condition_holds({"fact": "w2_employer_count", "in": [1, 2]}, facts, SPECS)
    assert condition_holds({"fact": "ev_acquired_date", "gte": "2026-01-01"}, facts, SPECS)
    assert not condition_holds({"fact": "ev_acquired_date", "lte": "2026-01-31"}, facts, SPECS)
    assert condition_holds({"any": [{"fact": "self_employment", "eq": True}, {"fact": "ev_purchased", "eq": True}]}, facts, SPECS)
    assert not condition_holds({"all": [{"fact": "self_employment", "eq": True}, {"fact": "ev_purchased", "eq": True}]}, facts, SPECS)


def test_unknown_value_never_satisfies():
    facts = complete_facts({"ev_purchased": True}, SPECS)
    for op in ("eq", "gte", "lte"):
        assert not condition_holds({"fact": "ev_acquired_date", op: "2026-01-01"}, facts, SPECS)
    assert not condition_holds({"fact": "filing_status", "in": ["single"]}, facts, SPECS)


def test_condition_needs_one_comparison():
    facts = complete_facts({}, SPECS)
    with pytest.raises(ValueError):
        condition_holds({"fact": "w2_employer_count", "gte": 1, "lte": 2}, facts, SPECS)
    with pytest.raises(ValueError):
        condition_holds({"fact": "w2_employer_count"}, facts, SPECS)


def test_h017_documents_and_traps():
    docs = required_documents(H017, RULES, SPECS, statuses=ALL)
    assert docs == [
        {"rule_id": "R001", "count": 3},
        {"rule_id": "R002", "count": 1},
        {"rule_id": "R003", "count": 1},
        {"rule_id": "R004", "count": 1},
        {"rule_id": "R014", "count": 1},
    ]
    assert triggered_traps(H017, RULES, SPECS, statuses=ALL) == ["T001"]


def test_only_listed_statuses_count():
    assert required_documents(H017, RULES, SPECS) == []
    rules = [r if r.id != "R004" else engine.Rule(**{**r.__dict__, "status": "verified"}) for r in RULES]
    assert required_documents(H017, rules, SPECS) == [{"rule_id": "R004", "count": 1}]
    excluded = [engine.Rule(**{**r.__dict__, "status": "excluded"}) for r in RULES]
    assert required_documents(H017, excluded, SPECS, statuses=ALL) == []


def test_state_refund_rule_needs_both_facts():
    one = required_documents({"state_refund_received": True}, RULES, SPECS, statuses=ALL)
    both = required_documents({"state_refund_received": True, "itemized_last_year": True}, RULES, SPECS, statuses=ALL)
    assert one == []
    assert both == [{"rule_id": "R011", "count": 1}]


def test_trap_years_and_dates():
    def traps(facts):
        return triggered_traps(facts, RULES, SPECS, statuses=ALL)

    assert traps({"clean_energy_installed": True, "clean_energy_install_year": 2025}) == []
    assert traps({"efficiency_improvements": True, "efficiency_improvements_year": 2026}) == ["T002"]
    assert traps({"ev_purchased": True, "ev_acquired_date": "2025-09-30"}) == []
    assert traps({"ev_purchased": True, "ev_acquired_date": "2026-12-31"}) == ["T003"]
    assert traps({"ev_purchased": True}) == []
    assert required_documents({"ev_purchased": True, "ev_acquired_date": "2026-05-01"}, RULES, SPECS, statuses=ALL) == []


def test_zero_count_rule_does_not_apply():
    assert required_documents({"k1_count": 0}, RULES, SPECS, statuses=ALL) == []
    assert required_documents({"k1_count": 2}, RULES, SPECS, statuses=ALL) == [{"rule_id": "R020", "count": 2}]


def test_as_multiset_and_rule_facts():
    bag = as_multiset([{"rule_id": "R001", "count": 2}, {"rule_id": "R001"}, {"rule_id": "R004", "count": 1}])
    assert bag == {"R001": 3, "R004": 1}
    assert rule_facts({"all": [{"fact": "a", "eq": 1}, {"any": [{"fact": "b", "eq": 1}, {"fact": "c", "eq": 1}]}]}) == {"a", "b", "c"}


def test_every_fact_a_rule_reads_exists():
    for r in RULES:
        assert rule_facts(r.when) <= set(SPECS), r.id


# Validator: start from a valid copy of the repo files and break one thing at a time.

VALID_RULE = {
    "id": "R001", "when": {"all": [{"fact": "w2_employer_count", "gte": 1}]}, "document": "Form W-2",
    "count_from": "w2_employer_count", "aliases": ["W-2"], "client_reason": "You had wages.",
    "source_url": "https://www.irs.gov/forms-pubs/about-form-w-2", "source_title": "About Form W-2",
    "source_version": "guidance", "tax_years": [2026], "trap": False, "status": "to_verify",
    "verified_by": None, "verified_on": None,
}


def write_rules(tmp_path, mutate=None):
    rules = yaml.safe_load(engine.DOCUMENT_RULES_PATH.read_text())
    for r in rules:
        if not r["source_url"]:
            r["source_url"] = "https://www.irs.gov/example"
            r["source_title"] = "Example"
    if mutate:
        mutate(rules)
    p = tmp_path / "document_rules.yaml"
    p.write_text(yaml.safe_dump(rules, sort_keys=False))
    return p


def errors_for(tmp_path, mutate=None):
    errors, _ = validate(rules_path=write_rules(tmp_path, mutate))
    return errors


def by_id(rules, rid):
    return next(r for r in rules if r["id"] == rid)


def test_validator_accepts_filled_repo_rules(tmp_path):
    assert errors_for(tmp_path) == []


@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda rs: by_id(rs, "R004")["when"]["all"][0].update(fact="nope"), "unknown fact"),
        (lambda rs: by_id(rs, "R004")["when"]["all"][0].update(gt=1), "needs exactly one"),
        (lambda rs: by_id(rs, "R004")["when"]["all"][0].update(eq="yes"), "doesn't fit bool"),
        (lambda rs: by_id(rs, "T001").update(document="Form 5695"), "a trap has document"),
        (lambda rs: by_id(rs, "R004").update(status="verified"), "verified_by and verified_on"),
        (lambda rs: by_id(rs, "R004").update(verified_by="roanuk"), "can't have verified_by"),
        (lambda rs: by_id(rs, "R004").update(source_url="https://example.com/x"), "official .gov"),
        (lambda rs: by_id(rs, "R004").update(source_url="http://www.irs.gov/x"), "official .gov"),
        (lambda rs: by_id(rs, "R004").update(source_version="final"), "source_version"),
        (lambda rs: by_id(rs, "R004").update(tax_years=[2025]), "tax_years"),
        (lambda rs: by_id(rs, "R004").update(aliases=[]), "aliases"),
        (lambda rs: by_id(rs, "R004").update(count_from="self_employment"), "count_from"),
        (lambda rs: by_id(rs, "R004").update(client_reason=None), "needs document and client_reason"),
        (lambda rs: by_id(rs, "R004").update(document="Form 1099" + chr(0x2014) + "INT"), "dash"),
        (lambda rs: by_id(rs, "R004").update(extra_field=1), "unknown fields"),
        (lambda rs: rs.append(copy.deepcopy(by_id(rs, "R004"))), "duplicate id"),
        (lambda rs: rs.pop(), "rule IDs"),
        (lambda rs: rs.reverse(), "ID order"),
    ],
)
def test_validator_catches(tmp_path, mutate, needle):
    errors = errors_for(tmp_path, mutate)
    assert any(needle in e for e in errors), errors


def test_validator_flags_verified_rule_with_proper_fields_as_fine(tmp_path):
    def verify(rs):
        by_id(rs, "R004").update(status="verified", verified_by="roanuk", verified_on="2026-10-31")

    assert errors_for(tmp_path, verify) == []
