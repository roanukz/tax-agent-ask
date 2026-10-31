"""Alias matching: normalization, tokens, keys, document grouping, candidates, and precedence."""

import pytest

from eval.aliases import (
    IGNORE,
    RULE,
    SEPARATOR_FORMS,
    TRAP,
    UNMATCHED,
    Alias,
    AliasIndex,
    Match,
    alias_keys,
    clusters,
    forms,
    key,
    normalize,
    phrase,
    positions,
    tokenize,
)
from rules.engine import Rule, load_ignore_aliases, load_rules

RULES = load_rules()
IGNORE_LIST = load_ignore_aliases()
INDEX = AliasIndex(RULES, IGNORE_LIST)

EN_DASH = chr(0x2013)
RIGHT_QUOTE = chr(0x2019)


@pytest.fixture(autouse=True)
def mutmut_node_id(request):
    """mutmut 3.7.0 reads item._nodeid, which pytest 9 renamed; this restores it and does nothing else."""
    request.node._nodeid = request.node.nodeid


def rule(rule_id, *aliases, trap=False, document=None):
    return Rule(
        id=rule_id,
        when={"fact": "self_employment", "eq": True},
        document=document,
        count_from=None,
        aliases=tuple(aliases),
        client_reason=None,
        source_url="",
        source_title="",
        source_version="guidance",
        tax_years=(2026,),
        trap=trap,
        status="verified",
        verified_by=None,
        verified_on=None,
    )


def mapped(text, index=INDEX):
    return [(m.kind, m.rule_ids) for m in index.map_request(text)]


def alias(text, kind=RULE, rule_id="R001"):
    return Alias(text, alias_keys(text), kind, rule_id)


# normalize


def test_normalize_lowercases():
    assert normalize("Form W-2") == "form w-2"


@pytest.mark.parametrize("code", [0x2018, 0x2019, 0x201B, 0x02BC, 0x2032])
def test_normalize_drops_typographic_apostrophes(code):
    assert normalize("Driver" + chr(code) + "s") == "drivers"


def test_normalize_drops_plain_apostrophes_and_backticks():
    assert normalize("driver's `x`") == "drivers x"


@pytest.mark.parametrize("code", [0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFE63, 0xFF0D])
def test_normalize_turns_dashes_into_hyphens(code):
    assert normalize("W" + chr(code) + "2") == "w-2"


def test_normalize_keeps_double_quotes():
    assert normalize('"W-2"') == '"w-2"'


# tokenize and phrase


def test_tokenize_keeps_case_inner_hyphens_and_marks_separators():
    assert tokenize("Form 1098-E, and W-2;  plus\nK-1") == [
        "Form", "1098-E", None, "and", "W-2", None, "plus", None, "K-1"]


@pytest.mark.parametrize("separator", [",", ";", "&", "/", "+", "\n"])
def test_tokenize_separators(separator):
    assert tokenize("W-2" + separator + "1099") == ["W-2", None, "1099"]


def test_tokenize_drops_edge_hyphens_and_other_punctuation():
    assert tokenize("-w-2- (x) y.z: 'q'") == ["w-2", "x", "y", "z", "q"]


def test_tokenize_splits_on_underscores():
    assert tokenize("a_b") == ["a", "b"]


def test_tokenize_translates_dashes_and_apostrophes():
    assert tokenize("W" + EN_DASH + "2 Driver" + RIGHT_QUOTE + "s") == ["W-2", "Drivers"]


def test_tokenize_empty():
    assert tokenize("") == []


def test_phrase_casefolds_and_drops_separators():
    assert phrase(["Form", None, "W-2"]) == "form w-2"
    assert phrase([]) == ""


# key and forms


@pytest.mark.parametrize(
    "token,expected",
    [
        ("w-2", "w2"),
        ("W-2", "w2"),
        ("w2", "w2"),
        ("w-2s", "w2"),
        ("W-2S", "w2"),
        ("w2s", "w2"),
        ("1099-int", "1099int"),
        ("1099-ints", "1099int"),
        ("1099-s", "1099s"),
        ("1099-S", "1099s"),
        ("1099s", "1099"),
        ("statements", "statement"),
        ("address", "address"),
        ("us", "us"),
        ("is", "is"),
        ("s", "s"),
        ("gas", "ga"),
        ("form", "form"),
        ("k-1", "k1"),
        ("1098-e", "1098e"),
    ],
)
def test_key(token, expected):
    assert key(token) == expected


@pytest.mark.parametrize(
    "token,expected",
    [
        ("1099S", {"1099", "1099s"}),
        ("1099s", {"1099"}),
        ("W-2S", {"w2", "w2s"}),
        ("K-1S", {"k1", "k1s"}),
        ("SSA-1099S", {"ssa1099", "ssa1099s"}),
        ("1099-S", {"1099s"}),
        ("FORMS", {"form"}),
        ("2S", {"2s"}),
        ("S", {"s"}),
        ("W-2", {"w2"}),
    ],
)
def test_forms(token, expected):
    assert forms(token) == expected


def test_separator_forms_hold_only_none():
    assert SEPARATOR_FORMS == {None}


# positions


def test_positions_finds_every_occurrence():
    assert positions(("a", "b"), [{"a"}, {"b"}, {"x"}, {"a"}, {"b"}]) == [0, 3]


def test_positions_finds_a_match_at_the_end():
    assert positions(("b",), [{"a"}, {"b"}]) == [1]


def test_positions_matches_any_form():
    assert positions(("b",), [{"a", "b"}]) == [0]


def test_positions_never_crosses_a_separator():
    assert positions(("a", "b"), [{"a"}, SEPARATOR_FORMS, {"b"}]) == []


def test_positions_matches_a_separator_in_the_needle():
    assert positions(("a", None, "b"), [{"a"}, SEPARATOR_FORMS, {"b"}]) == [0]
    assert positions(("a", None, "b"), [{"a"}, {"b"}]) == []


def test_positions_accepts_a_list_needle():
    assert positions(["a"], [{"a"}]) == [0]


def test_positions_needs_the_whole_needle():
    assert positions(("a", "b"), [{"a"}]) == []
    assert positions(("a",), []) == []


# alias_keys


def test_alias_keys_keep_inner_separators_and_strip_the_ends():
    assert alias_keys(", Form W-2s; Box 1,") == ("form", "w2", None, "box", "1")


def test_alias_keys_of_only_separators():
    assert alias_keys(", ;") == ()


# clusters


def hit(start, end, rule_id="R001", kind=RULE):
    return (start, end, Alias("x", ("x",), kind, rule_id))


def spans(groups):
    return [(first, last, [a.rule_id for a in aliases]) for first, last, aliases in groups]


def test_clusters_keep_apart_hits_that_do_not_overlap():
    assert spans(clusters([hit(0, 1), hit(1, 2)])) == [(0, 1, ["R001"]), (1, 2, ["R001"])]


def test_clusters_merge_a_hit_inside_the_group():
    assert spans(clusters([hit(0, 3, "R001"), hit(1, 2, "R002")])) == [(0, 3, ["R001", "R002"])]


def test_clusters_merge_a_hit_ending_with_the_group():
    assert spans(clusters([hit(0, 3, "R001"), hit(2, 3, "R002")])) == [(0, 3, ["R001", "R002"])]


def test_clusters_merge_a_hit_that_starts_with_the_group_and_holds_it():
    assert spans(clusters([hit(0, 1, "R001"), hit(0, 3, "R002")])) == [(0, 3, ["R001", "R002"])]


def test_clusters_merge_a_partial_overlap_that_shares_a_rule():
    assert spans(clusters([hit(0, 2, "R001"), hit(1, 4, "R001")])) == [(0, 4, ["R001", "R001"])]


def test_clusters_keep_apart_a_partial_overlap_without_a_shared_rule():
    assert spans(clusters([hit(0, 2, "R001"), hit(1, 4, "R002")])) == [(0, 2, ["R001"]), (1, 4, ["R002"])]


def test_clusters_sort_hits_by_start_then_end():
    assert spans(clusters([hit(3, 4, "R003"), hit(0, 2, "R002"), hit(0, 1, "R001")])) == [
        (0, 2, ["R001", "R002"]), (3, 4, ["R003"])]


def test_clusters_join_only_the_first_group_that_fits():
    # The last hit shares R001 with the first group and starts with the second.
    groups = clusters([hit(0, 2, "R001"), hit(1, 4, "R002"), hit(1, 5, "R001")])
    assert spans(groups) == [(0, 5, ["R001", "R001"]), (1, 4, ["R002"])]


def test_clusters_check_later_groups_too():
    groups = clusters([hit(0, 2, "R001"), hit(1, 4, "R002"), hit(3, 5, "R002")])
    assert spans(groups) == [(0, 2, ["R001"]), (1, 5, ["R002", "R002"])]


def test_clusters_of_nothing():
    assert clusters([]) == []


# AliasIndex construction


def test_index_holds_aliases_in_order():
    index = AliasIndex(
        [rule("R001", "W-2", "Form W-2"), rule("T001", "Form 5695", trap=True)],
        ["photo ID"],
    )
    assert index.aliases == [
        Alias("W-2", ("w2",), RULE, "R001"),
        Alias("Form W-2", ("form", "w2"), RULE, "R001"),
        Alias("Form 5695", ("form", "5695"), TRAP, "T001"),
        Alias("photo ID", ("photo", "id"), IGNORE, None),
    ]
    assert index.order == {"R001": 0, "T001": 1}


def test_index_adds_the_document_text_after_the_aliases():
    index = AliasIndex([rule("R001", "W-2", document="Wage statement, each job")], [])
    assert index.aliases == [
        Alias("W-2", ("w2",), RULE, "R001"),
        Alias("Wage statement, each job", ("wage", "statement", None, "each", "job"), RULE, "R001"),
    ]


def test_index_never_adds_a_trap_document():
    index = AliasIndex([rule("T001", "x", trap=True, document="solar invoice")], [])
    assert index.aliases == [Alias("x", ("x",), TRAP, "T001")]


def test_index_skips_aliases_without_words():
    index = AliasIndex([rule("R001", "!!!", "x")], [])
    assert index.aliases == [Alias("x", ("x",), RULE, "R001")]


def test_index_skips_duplicate_aliases_of_one_rule_and_kind():
    index = AliasIndex([rule("R001", "W-2", "w2", document="W-2"), rule("R002", "W-2")], ["W2"])
    assert index.aliases == [
        Alias("W-2", ("w2",), RULE, "R001"),
        Alias("W-2", ("w2",), RULE, "R002"),
        Alias("W2", ("w2",), IGNORE, None),
    ]


def test_index_keeps_separators_inside_an_alias():
    index = AliasIndex([rule("R001", "a, b")], [])
    assert index.aliases == [Alias("a, b", ("a", None, "b"), RULE, "R001")]


def test_match_defaults_to_no_rule_ids():
    assert Match("x", UNMATCHED).rule_ids == ()


# map_request with the real rules


def test_two_documents_split_on_and():
    assert INDEX.map_request("W-2 and 1099-INT") == [
        Match("w-2", RULE, ("R001",)),
        Match("1099-int", RULE, ("R004",)),
    ]


@pytest.mark.parametrize(
    "text",
    ["W-2, 1099-INT", "W-2; 1099-INT", "W-2 & 1099-INT", "W-2\n1099-INT", "W-2 plus 1099-INT",
     "W-2 and 1099-INT", "W-2/1099-INT", "W-2 + 1099-INT", "W-2 1099-INT"],
)
def test_every_separator_splits(text):
    assert mapped(text) == [(RULE, ("R001",)), (RULE, ("R004",))]


def test_match_text_is_the_matched_words():
    assert INDEX.map_request("  Form   W" + EN_DASH + "2 from my old job ") == [Match("form w-2", RULE, ("R001",))]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1098", ("R014",)),
        ("Form 1098", ("R014",)),
        ("Form 1098-E", ("R016",)),
        ("1098E", ("R016",)),
        ("W2", ("R001",)),
        ("W-2s from both jobs", ("R001",)),
        ("W-2S FROM BOTH JOBS", ("R001",)),
        ("1099INT", ("R004",)),
        ("Form 1099-S", ("R030",)),
        ("1099S", ("R030",)),
        ("brokerage statements", ("R006",)),
        ("K-1s from the partnerships", ("R020",)),
        ("Social Security benefits statement", ("R009",)),
        ("Rental income and expense records", ("R021",)),
    ],
)
def test_single_rule_matches(text, expected):
    assert mapped(text) == [(RULE, expected)]


def test_plural_1099s_matches_nothing():
    assert mapped("1099s") == [(UNMATCHED, ())]
    assert INDEX.map_request("all my 1099s") == [Match("all my 1099s", UNMATCHED)]


def test_bare_1099_g_keeps_both_candidates_in_rule_order():
    assert mapped("1099-G") == [(RULE, ("R010", "R011"))]


def test_closing_statement_keeps_both_candidates():
    assert mapped("closing statement") == [(RULE, ("R015", "R030"))]


def test_longest_alias_comes_first():
    assert mapped("1099-G for state refund") == [(RULE, ("R011", "R010"))]
    assert mapped("unemployment 1099-G") == [(RULE, ("R010", "R011"))]
    assert mapped("sale closing statement") == [(RULE, ("R030", "R015"))]


def test_overlapping_aliases_of_one_rule_are_one_document():
    assert INDEX.map_request("Form 1099-G for state refund") == [
        Match("form 1099-g for state refund", RULE, ("R011", "R010"))]


def test_one_item_naming_two_documents_of_close_rules():
    assert mapped("1099-G for state refund, 1099-G for unemployment") == [
        (RULE, ("R011", "R010")), (RULE, ("R010", "R011"))]


def test_documents_that_share_a_word_stay_apart():
    assert mapped("child care receipts and written acknowledgment") == [(RULE, ("R018",)), (RULE, ("R025",))]


def test_the_same_document_twice_is_two_matches():
    assert mapped("1099-INT 1099-INT") == [(RULE, ("R004",)), (RULE, ("R004",))]


def test_aliases_with_and_inside():
    assert mapped("Form 1099-DIV, dividends and distributions") == [(RULE, ("R005",)), (RULE, ("R005",))]
    assert mapped("payment card and third party network transactions") == [(RULE, ("R003",))]
    assert mapped("estimated payment dates and amounts") == [(RULE, ("R022",))]


def test_ignore_alias_with_and_inside():
    assert mapped("bank account and routing number") == [(IGNORE, ())]


def test_ignore_aliases():
    assert mapped("Driver" + RIGHT_QUOTE + "s license") == [(IGNORE, ())]
    assert mapped("copy of last year's tax return") == [(IGNORE, ())]
    assert INDEX.map_request("photo ID") == [Match("photo id", IGNORE)]


def test_trap_aliases():
    assert mapped("Form 5695") == [(TRAP, ("T001", "T002"))]
    assert mapped("solar invoice") == [(TRAP, ("T001",))]
    assert mapped("EV purchase agreement") == [(TRAP, ("T003",))]
    assert INDEX.map_request("Form 5695 for the solar panels") == [Match("form 5695", TRAP, ("T001", "T002"))]


def test_unmatched_is_the_whole_request_once():
    assert INDEX.map_request("Receipts for the boat") == [Match("receipts for the boat", UNMATCHED)]
    assert INDEX.map_request("Mileage log, and receipts for the rental repairs") == [
        Match("mileage log and receipts for the rental repairs", UNMATCHED)]


def test_context_around_a_document_is_not_unmatched():
    assert mapped("W-2 from Acme and Beta") == [(RULE, ("R001",))]


def test_empty_requests():
    assert mapped("") == []
    assert mapped(", ;&") == []
    assert mapped("W-2,,") == [(RULE, ("R001",))]


def test_trap_and_rule_at_different_places_are_both_kept():
    assert mapped("Form 5695 with the Form 1098") == [(TRAP, ("T001", "T002")), (RULE, ("R014",))]


def test_ignore_and_rule_at_different_places_are_both_kept():
    assert mapped("photo ID with the W-2") == [(IGNORE, ()), (RULE, ("R001",))]


def test_every_alias_maps_back_to_its_rule():
    for r in RULES:
        for text in r.aliases:
            matches = INDEX.map_request(text)
            assert len(matches) == 1, text
            assert matches[0].kind == (TRAP if r.trap else RULE), text
            assert r.id in matches[0].rule_ids, text


def test_every_document_text_maps_to_its_rule_first():
    for r in RULES:
        if r.document:
            matches = INDEX.map_request(r.document)
            assert len(matches) == 1, r.document
            assert matches[0].kind == RULE, r.document
            assert matches[0].rule_ids[0] == r.id, r.document


def test_document_text_needs_its_own_separators():
    assert mapped("Provider name, address, taxpayer ID, and amount paid") == [(RULE, ("R018",))]
    assert mapped("Provider name; address; taxpayer ID; and amount paid") == [(RULE, ("R018",))]
    assert mapped("Provider name, address, taxpayer ID and amount paid") == [(UNMATCHED, ())]


def test_every_ignore_alias_is_ignored():
    for text in IGNORE_LIST:
        assert mapped(text) == [(IGNORE, ())], text


# map_request with synthetic rules


def test_candidates_rank_by_longest_alias():
    index = AliasIndex([rule("R001", "x"), rule("R002", "x y")], [])
    assert mapped("x y", index) == [(RULE, ("R002", "R001"))]


def test_candidates_rank_by_longest_alias_of_each_rule():
    index = AliasIndex([rule("R001", "x y", "x"), rule("R002", "y")], [])
    assert mapped("x y", index) == [(RULE, ("R001", "R002"))]


def test_candidates_rank_by_longest_alias_whatever_the_alias_order():
    index = AliasIndex([rule("R001", "x", "x y z"), rule("R002", "x y")], [])
    assert mapped("x y z", index) == [(RULE, ("R001", "R002"))]


def test_ties_break_by_rule_order_not_id():
    index = AliasIndex([rule("R009", "x"), rule("R002", "x")], [])
    assert mapped("x", index) == [(RULE, ("R009", "R002"))]


def test_traps_rank_like_rules():
    index = AliasIndex([rule("T009", "a", trap=True), rule("T002", "a b", trap=True), rule("T005", "b", trap=True)], [])
    assert mapped("a b", index) == [(TRAP, ("T002", "T009", "T005"))]


def test_trap_wins_over_a_rule_in_one_document():
    index = AliasIndex([rule("R001", "a b"), rule("T001", "b", trap=True)], [])
    assert index.map_request("a b") == [Match("a b", TRAP, ("T001",))]


def test_rule_wins_over_ignore_in_one_document():
    index = AliasIndex([rule("R001", "a b")], ["b"])
    assert index.map_request("a b") == [Match("a b", RULE, ("R001",))]


def test_ignore_when_only_ignore_aliases_match():
    index = AliasIndex([rule("R001", "a")], ["b", "b c"])
    assert index.map_request("b c") == [Match("b c", IGNORE)]
    assert mapped("c", index) == [(UNMATCHED, ())]


def test_separate_hits_are_separate_documents():
    index = AliasIndex([rule("R001", "a"), rule("R002", "b")], [])
    assert mapped("a or b", index) == [(RULE, ("R001",)), (RULE, ("R002",))]
    assert mapped("b, then a", index) == [(RULE, ("R002",)), (RULE, ("R001",))]


def test_an_alias_never_matches_across_a_separator():
    index = AliasIndex([rule("R001", "a b")], [])
    assert mapped("a, b", index) == [(UNMATCHED, ())]
    assert mapped("a b", index) == [(RULE, ("R001",))]
