"""Alias matching: normalization, tokens, pieces, whole words, documents, candidates, precedence, parts, and merging."""

import pytest

from eval.aliases import (
    BREAKS,
    CONTEXT,
    FILLER,
    IGNORE,
    RULE,
    TRAP,
    UNMATCHED,
    Alias,
    AliasIndex,
    Match,
    alias_keys,
    breaks,
    clusters,
    forms,
    key,
    merge,
    normalize,
    phrase,
    piece_forms,
    pieces,
    plural,
    positions,
    short_suffix,
    spans,
    split,
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


def credited(text, index=INDEX):
    """Each document a request names once merged, as (kind, best candidates), without context or ignored words."""
    return [(m.kind, m.best) for m in merge(index.map_request(text)) if m.kind not in (CONTEXT, IGNORE)]


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
    assert tokenize("-w-2- (x) ab.cd: 'q'") == ["w-2", "x", "ab", "cd", "q"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1098 e.g. x", ["1098", None, "x"]),
        ("1098 (e.g., x)", ["1098", None, None, "x"]),
        ("1098 e.g x", ["1098", None, "x"]),
        ("i.e. a.k.a. U.S. x", [None, None, None, "x"]),
        ("1098 T. Rowe", ["1098", None, "Rowe"]),
        ("1098 T.\tRowe", ["1098", None, "Rowe"]),
        ("A. Form W-2", [None, "Form", "W-2"]),
    ],
)
def test_tokenize_reads_abbreviations_and_initials_as_separators(text, expected):
    assert tokenize(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Schedule C.", ["Schedule", "C"]),
        ("Schedule C.\nx", ["Schedule", "C", None, "x"]),
        ("e.gov", ["e", "gov"]),
        ("e.g2", ["e", "g2"]),
        ("1099-B. Next", ["1099-B", "Next"]),
        ("2. Form", ["2", "Form"]),
        ("ab. cd", ["ab", "cd"]),
    ],
)
def test_tokenize_keeps_letters_that_are_not_abbreviations(text, expected):
    assert tokenize(text) == expected


def test_tokenize_splits_on_underscores():
    assert tokenize("a_b") == ["a", "b"]


def test_tokenize_translates_dashes_and_apostrophes():
    assert tokenize("W" + EN_DASH + "2 Driver" + RIGHT_QUOTE + "s") == ["W-2", "Drivers"]


def test_tokenize_empty():
    assert tokenize("") == []


def test_phrase_casefolds_and_drops_separators():
    assert phrase(["Form", None, "W-2"]) == "form w-2"
    assert phrase(iter(["A", "B"])) == "a b"
    assert phrase([]) == ""


# breaks and split


def test_breaks_are_semicolons_and_line_breaks():
    assert BREAKS == {";", "\n"}
    assert breaks("W-2; 1099-INT\nK-1, x & y / z + w") == {1, 3}


def test_breaks_index_the_tokens_after_translating_dashes():
    assert breaks("W" + EN_DASH + "2; x") == {1}
    assert tokenize("W" + EN_DASH + "2; x") == ["W-2", None, "x"]


def test_breaks_of_nothing():
    assert breaks("") == set()
    assert breaks("W-2, 1099-INT") == set()


def test_an_abbreviation_is_no_break():
    assert breaks("x e.g. y; z") == {3}
    assert tokenize("x e.g. y; z") == ["x", None, "y", None, "z"]


def test_split_drops_the_cuts_and_empty_runs():
    assert split(range(5), {1, 3}) == [[0], [2], [4]]
    assert split(range(4), {0, 1, 3}) == [[2]]
    assert split([4, 5, 6], {5}) == [[4], [6]]
    assert split(range(3), set()) == [[0, 1, 2]]
    assert split([], {0}) == []


# pieces and piece_forms


@pytest.mark.parametrize(
    "token,expected",
    [
        ("w-2", ("w", "2")),
        ("W-2", ("w", "2")),
        ("w2", ("w", "2")),
        ("w-2s", ("w", "2")),
        ("W-2S", ("w", "2")),
        ("w2s", ("w", "2")),
        ("1099-int", ("1099", "int")),
        ("1099INT", ("1099", "int")),
        ("1099-ints", ("1099", "int")),
        ("1099-s", ("1099", "s")),
        ("1099-S", ("1099", "s")),
        ("1099s", ("1099",)),
        ("w-s", ("w", "s")),
        ("statements", ("statement",)),
        ("address", ("address",)),
        ("us", ("us",)),
        ("is", ("is",)),
        ("s", ("s",)),
        ("ES", ("es",)),
        ("gas", ("ga",)),
        ("form", ("form",)),
        ("k-1", ("k", "1")),
        ("1098-e", ("1098", "e")),
        ("prior-year", ("prior", "year")),
        ("prior-years", ("prior", "year")),
        ("CP01A", ("cp", "01", "a")),
        ("a-b-c", ("a", "b", "c")),
        ("1040-ES", ("1040", "es")),
        ("1040es", ("1040", "es")),
        ("1099-Rs", ("1099", "rs")),
        ("W-2Gs", ("w", "2", "gs")),
        ("1099-INTs", ("1099", "int")),
        ("1040-SS", ("1040", "ss")),
    ],
)
def test_pieces(token, expected):
    assert pieces(token) == expected


@pytest.mark.parametrize(
    "token,expected",
    [
        ("1099S", {("1099",), ("1099", "s")}),
        ("1099s", {("1099",)}),
        ("W-2S", {("w", "2"), ("w", "2", "s")}),
        ("K-1S", {("k", "1"), ("k", "1", "s")}),
        ("SSA-1099S", {("ssa", "1099"), ("ssa", "1099", "s")}),
        ("1099-S", {("1099", "s")}),
        ("FORMS", {("form",)}),
        ("2S", {("2", "s")}),
        ("S", {("s",)}),
        ("W-2", {("w", "2")}),
        ("1040-ES", {("1040", "es"), ("1040", "e")}),
        ("1040ES", {("1040", "es"), ("1040", "e")}),
        ("1099-Rs", {("1099", "rs"), ("1099", "r")}),
        ("W-2GS", {("w", "2", "gs"), ("w", "2", "g")}),
        ("1040-SS", {("1040", "ss")}),
        ("ES", {("es",)}),
        ("GAS", {("ga",)}),
    ],
)
def test_piece_forms(token, expected):
    assert piece_forms(token) == expected


@pytest.mark.parametrize("token,expected", [("gas", True), ("w-2s", True), ("1099s", True), ("es", False),
                                            ("ss", False), ("boss", False), ("1099-s", False), ("s", False)])
def test_plural(token, expected):
    assert plural(token) is expected


@pytest.mark.parametrize("token,expected", [("1040-es", True), ("1040es", True), ("w-2gs", True), ("1099-sa", True),
                                            ("1099-s", False), ("1099-int", False), ("es", False), ("w-2", False),
                                            ("prior-years", False), ("es-1040", False)])
def test_short_suffix(token, expected):
    assert short_suffix(token) is expected


# spans


A, B, C, X = {("a",)}, {("b",)}, {("c",)}, {("x",)}


def test_spans_finds_every_occurrence():
    assert spans(("a", "b"), [A, B, X, A, B]) == [(0, 2), (3, 5)]


def test_spans_finds_a_match_at_the_end():
    assert spans(("b",), [A, B]) == [(1, 2)]


def test_spans_matches_any_form():
    assert spans(("b",), [{("a",), ("b",)}]) == [(0, 1)]


def test_spans_spreads_pieces_over_words_and_joins_them_in_one():
    assert spans(("a", "b"), [{("a", "b")}]) == [(0, 1)]
    assert spans(("a", "b"), [A, B]) == [(0, 2)]
    assert spans(("a", "b", "c"), [{("a", "b")}, C]) == [(0, 2)]
    assert spans(("a", "b", "c"), [A, {("b", "c")}]) == [(0, 2)]


def test_spans_never_matches_part_of_a_word():
    assert spans(("a",), [{("a", "b")}]) == []
    assert spans(("b",), [{("a", "b")}]) == []
    assert spans(("a", "b"), [{("a", "b", "c")}]) == []
    assert spans(("a", "b"), [A, {("b", "c")}]) == []


def test_spans_never_crosses_a_separator():
    assert spans(("a", "b"), [A, None, B]) == []


def test_spans_matches_a_separator_in_the_needle_once_or_not_at_all():
    assert spans(("a", None, "b"), [A, None, B]) == [(0, 3)]
    assert spans(("a", None, "b"), [A, B]) == [(0, 2)]
    assert spans(("a", None, "b"), [A, None, None, B]) == []
    assert spans(("a", None, None, "b"), [A, None, None, B]) == [(0, 4)]
    assert spans(("a", None, "b"), [{("a", "b")}]) == []


def test_spans_with_a_separator_left_in_the_needle_at_the_end_of_the_tokens():
    assert spans(("a", None, "b"), [A]) == []
    assert spans(("a", None), [A, None]) == [(0, 1), (0, 2)]


def test_spans_never_starts_at_a_separator():
    assert spans(("a",), [None, A]) == [(1, 2)]


def test_spans_lists_every_end_of_one_start_in_order():
    assert spans(("a", "b"), [{("a", "b"), ("a",)}, B]) == [(0, 1), (0, 2)]


def test_spans_accepts_a_list_needle():
    assert spans(["a"], [A]) == [(0, 1)]
    assert spans(["a", "b"], [{("a", "b")}]) == [(0, 1)]


def test_spans_needs_the_whole_needle():
    assert spans(("a", "b"), [A]) == []
    assert spans(("a",), []) == []


# key, forms, and positions: the same comparison, one whole word to one key


@pytest.mark.parametrize(
    "token,expected",
    [
        ("w-2", "w2"),
        ("W-2", "w2"),
        ("w2", "w2"),
        ("w-2s", "w2"),
        ("W-2S", "w2"),
        ("1099-int", "1099int"),
        ("1099-ints", "1099int"),
        ("1099-S", "1099s"),
        ("1099s", "1099"),
        ("statements", "statement"),
        ("us", "us"),
        ("k-1", "k1"),
        ("1098-e", "1098e"),
        ("prior-year", "prioryear"),
        ("1040-ES", "1040es"),
    ],
)
def test_key_joins_the_pieces(token, expected):
    assert key(token) == expected


@pytest.mark.parametrize(
    "token,expected",
    [
        ("1099S", {"1099", "1099s"}),
        ("1099s", {"1099"}),
        ("W-2S", {"w2", "w2s"}),
        ("1099-S", {"1099s"}),
        ("FORMS", {"form"}),
        ("W-2", {"w2"}),
        ("1040-ES", {"1040es", "1040e"}),
    ],
)
def test_forms_join_the_piece_forms(token, expected):
    assert forms(token) == expected


def test_positions_match_whole_words_one_for_one():
    assert positions(("a", "b"), [{"a"}, {"b"}, {"x"}, {"a"}, {"b"}]) == [0, 3]
    assert positions(("b",), [{"a"}, {"b"}]) == [1]
    assert positions(("b",), [{"a", "b"}]) == [0]
    assert positions(["a"], [{"a"}]) == [0]


def test_positions_never_spread_a_key_over_words():
    assert positions((key("1099-INT"),), [forms("1099"), forms("INT")]) == []
    assert positions((key("1099-INT"),), [forms("1099INT")]) == [0]
    assert positions((key("W-2"), key("from")), [forms("W2"), forms("from")]) == [0]


def test_positions_compare_separators_like_keys():
    assert positions(("a", None, "b"), [{"a"}, {None}, {"b"}]) == [0]
    assert positions(("a", None, "b"), [{"a"}, {"b"}]) == []
    assert positions(("a", "b"), [{"a"}, {None}, {"b"}]) == []


def test_positions_need_the_whole_needle():
    assert positions(("a", "b"), [{"a"}]) == []
    assert positions(("a",), []) == []
    assert positions((), [{"a"}]) == [0, 1]


# alias_keys


def test_alias_keys_keep_inner_separators_and_strip_the_ends():
    assert alias_keys(", Form W-2s; Box 1,") == ("form", "w", "2", None, "box", "1")


def test_alias_keys_are_pieces():
    assert alias_keys("1099-INT") == alias_keys("1099 INT") == alias_keys("1099INT") == ("1099", "int")
    assert alias_keys("prior-year tax return") == ("prior", "year", "tax", "return")


def test_alias_keys_of_only_separators():
    assert alias_keys(", ;") == ()


# clusters


def hit(start, end, rule_id="R001", kind=RULE):
    return (start, end, Alias("x", ("x",), kind, rule_id))


def group_spans(groups):
    return [(first, last, [a.rule_id for a in aliases]) for first, last, aliases in groups]


def test_clusters_keep_apart_hits_that_do_not_overlap():
    assert group_spans(clusters([hit(0, 1), hit(1, 2)])) == [(0, 1, ["R001"]), (1, 2, ["R001"])]


def test_clusters_merge_a_hit_inside_the_group():
    assert group_spans(clusters([hit(0, 3, "R001"), hit(1, 2, "R002")])) == [(0, 3, ["R001", "R002"])]


def test_clusters_merge_a_hit_ending_with_the_group():
    assert group_spans(clusters([hit(0, 3, "R001"), hit(2, 3, "R002")])) == [(0, 3, ["R001", "R002"])]


def test_clusters_merge_a_hit_that_starts_with_the_group_and_holds_it():
    assert group_spans(clusters([hit(0, 1, "R001"), hit(0, 3, "R002")])) == [(0, 3, ["R001", "R002"])]


def test_clusters_merge_a_partial_overlap_that_shares_a_rule():
    assert group_spans(clusters([hit(0, 2, "R001"), hit(1, 4, "R001")])) == [(0, 4, ["R001", "R001"])]


def test_clusters_keep_apart_a_partial_overlap_without_a_shared_rule():
    assert group_spans(clusters([hit(0, 2, "R001"), hit(1, 4, "R002")])) == [(0, 2, ["R001"]), (1, 4, ["R002"])]


def test_clusters_sort_hits_by_start_then_end():
    assert group_spans(clusters([hit(3, 4, "R003"), hit(0, 2, "R002"), hit(0, 1, "R001")])) == [
        (0, 2, ["R001", "R002"]), (3, 4, ["R003"])]


def test_clusters_join_only_the_first_group_that_fits():
    # The last hit shares R001 with the first group and starts with the second.
    groups = clusters([hit(0, 2, "R001"), hit(1, 4, "R002"), hit(1, 5, "R001")])
    assert group_spans(groups) == [(0, 5, ["R001", "R001"]), (1, 4, ["R002"])]


def test_clusters_check_later_groups_too():
    groups = clusters([hit(0, 2, "R001"), hit(1, 4, "R002"), hit(3, 5, "R002")])
    assert group_spans(groups) == [(0, 2, ["R001"]), (1, 5, ["R002", "R002"])]


def test_clusters_of_nothing():
    assert clusters([]) == []


# AliasIndex construction


def test_index_holds_aliases_in_order():
    index = AliasIndex(
        [rule("R001", "W-2", "Form W-2"), rule("T001", "Form 5695", trap=True)],
        ["photo ID"],
    )
    assert index.aliases == [
        Alias("W-2", ("w", "2"), RULE, "R001"),
        Alias("Form W-2", ("form", "w", "2"), RULE, "R001"),
        Alias("Form 5695", ("form", "5695"), TRAP, "T001"),
        Alias("photo ID", ("photo", "id"), IGNORE, None),
    ]
    assert index.order == {"R001": 0, "T001": 1}


def test_index_adds_the_document_text_after_the_aliases():
    index = AliasIndex([rule("R001", "W-2", document="Wage statement, each job")], [])
    assert index.aliases == [
        Alias("W-2", ("w", "2"), RULE, "R001"),
        Alias("Wage statement, each job", ("wage", "statement", None, "each", "job"), RULE, "R001"),
    ]


def test_index_never_adds_a_trap_document():
    index = AliasIndex([rule("T001", "x", trap=True, document="solar invoice")], [])
    assert index.aliases == [Alias("x", ("x",), TRAP, "T001")]


def test_index_skips_aliases_without_words():
    index = AliasIndex([rule("R001", "!!!", "x")], [])
    assert index.aliases == [Alias("x", ("x",), RULE, "R001")]


def test_index_skips_duplicate_aliases_of_one_rule_and_kind():
    index = AliasIndex([rule("R001", "W-2", "w2", "W 2", document="W-2"), rule("R002", "W-2")], ["W2"])
    assert index.aliases == [
        Alias("W-2", ("w", "2"), RULE, "R001"),
        Alias("W-2", ("w", "2"), RULE, "R002"),
        Alias("W2", ("w", "2"), IGNORE, None),
    ]


def test_index_keeps_separators_inside_an_alias():
    index = AliasIndex([rule("R001", "a, b")], [])
    assert index.aliases == [Alias("a, b", ("a", None, "b"), RULE, "R001")]


def test_match_defaults_to_no_rule_ids_in_the_first_part():
    match = Match("x", UNMATCHED)
    assert (match.rule_ids, match.tied, match.best, match.part) == ((), 1, (), 0)


def test_match_best_is_the_tied_candidates():
    assert Match("x", RULE, ("R010", "R011"), 2).best == ("R010", "R011")
    assert Match("x", RULE, ("R011", "R010")).best == ("R011",)


def test_alias_length_counts_pieces_not_separators():
    assert alias("Form W-2").length == 3
    assert Alias("a, b", ("a", None, "b"), RULE, "R001").length == 2


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
        ("1099 INT", ("R004",)),
        ("SSA 1099", ("R009",)),
        ("W 2 from the old job", ("R001",)),
        ("Form W2", ("R001",)),
        ("1099 S", ("R030",)),
        ("W-2G", ("R023",)),
        ("Form W-2G", ("R023",)),
        ("1099-SA", ("R013",)),
    ],
)
def test_single_rule_matches(text, expected):
    assert mapped(text) == [(RULE, expected)]


def test_pieces_spread_over_words_rank_like_one_word():
    assert INDEX.map_request("1098 E") == [Match("1098 e", RULE, ("R016", "R014"))]
    assert INDEX.map_request("W 2 G") == [Match("w 2 g", RULE, ("R023", "R001"))]


def test_plural_1099s_matches_nothing():
    assert mapped("1099s") == [(UNMATCHED, ())]
    assert INDEX.map_request("all my 1099s") == [Match("all my 1099s", UNMATCHED)]


def test_bare_1099_g_keeps_both_candidates_in_rule_order_and_tied():
    assert INDEX.map_request("1099-G") == [Match("1099-g", RULE, ("R010", "R011"), 2)]


def test_closing_statement_keeps_both_candidates_tied():
    assert INDEX.map_request("closing statement") == [Match("closing statement", RULE, ("R015", "R030"), 2)]


def test_longest_alias_comes_first():
    assert mapped("1099-G for state refund") == [(RULE, ("R011", "R010"))]
    assert mapped("unemployment 1099-G") == [(RULE, ("R010", "R011"))]
    assert mapped("sale closing statement") == [(RULE, ("R030", "R015"))]


def test_overlapping_aliases_of_one_rule_are_one_document():
    assert INDEX.map_request("Form 1099-G for state refund") == [
        Match("form 1099-g for state refund", RULE, ("R011", "R010"))]


def test_one_item_naming_two_documents_of_close_rules():
    text = "1099-G for state refund, unemployment 1099-G"
    assert mapped(text) == [(RULE, ("R011", "R010")), (RULE, ("R010", "R011"))]
    assert credited(text) == [(RULE, ("R011",)), (RULE, ("R010",))]


def test_a_bare_1099_g_beside_a_specific_one_is_read_as_the_same_document():
    # The bare "1099-G" could be either rule, so it joins the specific one: a known trade-off of merging.
    text = "1099-G for state refund, 1099-G for unemployment"
    assert mapped(text) == [(RULE, ("R011", "R010")), (RULE, ("R010", "R011"))]
    assert credited(text) == [(RULE, ("R011",))]


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
    assert INDEX.map_request("Form 5695") == [Match("form 5695", TRAP, ("T001", "T002"), 2)]
    assert mapped("solar invoice") == [(TRAP, ("T001",))]
    # Dropped at Roanuk's rule call: an itemizer can still use the purchase paperwork.
    assert mapped("EV purchase agreement") == [("unmatched", ())]
    assert INDEX.map_request("Form 5695 for the solar panels") == [Match("form 5695", TRAP, ("T001", "T002"), 2)]
    assert mapped("Solar panel installation invoice") == [(TRAP, ("T001",))]


def test_unmatched_is_the_whole_request_once():
    assert INDEX.map_request("Receipts for the boat") == [Match("receipts for the boat", UNMATCHED)]
    assert INDEX.map_request("Mileage log, and receipts for the rental repairs") == [
        Match("mileage log and receipts for the rental repairs", UNMATCHED)]


def test_context_around_a_document_is_not_unmatched():
    assert mapped("W-2 from Acme and Beta") == [(RULE, ("R001",))]
    assert mapped("W-2 from my employer, 1099-INT from the bank") == [(RULE, ("R001",)), (RULE, ("R004",))]


def test_each_part_without_a_hit_is_one_unmatched_document():
    assert INDEX.map_request("W-2; divorce decree; car title") == [
        Match("w-2", RULE, ("R001",)), Match("divorce decree", UNMATCHED, part=1),
        Match("car title", UNMATCHED, part=2)]
    assert INDEX.map_request("W-2\ncar title") == [Match("w-2", RULE, ("R001",)), Match("car title", UNMATCHED, part=1)]
    assert INDEX.map_request("divorce decree; car title") == [
        Match("divorce decree", UNMATCHED), Match("car title", UNMATCHED, part=1)]


def test_a_part_without_a_hit_counts_once_whatever_its_commas():
    assert INDEX.map_request("car title; Mileage log, and receipts for the rental repairs; W-2") == [
        Match("car title", UNMATCHED), Match("mileage log and receipts for the rental repairs", UNMATCHED, part=1),
        Match("w-2", RULE, ("R001",), part=2)]


def test_every_match_records_its_part():
    assert INDEX.map_request("photo ID; W-2, from Acme\nForm 5695; x") == [
        Match("photo id", IGNORE), Match("w-2", RULE, ("R001",), part=1), Match("from acme", CONTEXT, part=1),
        Match("form 5695", TRAP, ("T001", "T002"), 2, part=2), Match("x", UNMATCHED, part=3)]
    assert [m.part for m in INDEX.map_request(";;W-2;\n1099-INT")] == [0, 1]


@pytest.mark.parametrize("text", ["W-2; if applicable", "W-2\nIf Applicable", "W-2; etc.", "W-2; one per bank",
                                  "W-2\nPlease upload a clear copy", "W-2; from each employer, as needed"])
def test_a_part_of_filler_words_is_context(text):
    matches = INDEX.map_request(text)
    assert [(m.kind, m.part) for m in matches] == [(RULE, 0), (CONTEXT, 1)]


def test_every_filler_word_alone_names_nothing():
    assert all(word == word.casefold() for word in FILLER)
    text = "W-2; " + " ".join(sorted(FILLER)).upper()
    assert INDEX.map_request(text) == [Match("w-2", RULE, ("R001",)), Match(" ".join(sorted(FILLER)), CONTEXT, part=1)]


def test_a_part_with_one_word_beyond_filler_is_unmatched():
    assert INDEX.map_request("W-2; if applicable, payment confirmations") == [
        Match("w-2", RULE, ("R001",)), Match("if applicable payment confirmations", UNMATCHED, part=1)]
    assert INDEX.map_request("if applicable") == [Match("if applicable", CONTEXT)]
    assert INDEX.map_request("if applicable; boat") == [
        Match("if applicable", CONTEXT), Match("boat", UNMATCHED, part=1)]


@pytest.mark.parametrize("separator", [",", "&", "/", "+"])
def test_a_segment_without_a_hit_beside_a_hit_is_context(separator):
    assert INDEX.map_request(f"misc {separator} W-2 {separator} from each employer") == [
        Match("misc", CONTEXT), Match("w-2", RULE, ("R001",)), Match("from each employer", CONTEXT)]


def test_context_unmatched_and_ignore_in_one_request_keep_their_order():
    assert INDEX.map_request("car title; Photo ID, medical expense receipts\nW-2, gym bills") == [
        Match("car title", UNMATCHED), Match("photo id", IGNORE, part=1),
        Match("medical expense receipts", CONTEXT, part=1), Match("w-2", RULE, ("R001",), part=2),
        Match("gym bills", CONTEXT, part=2)]


def test_a_hit_across_a_semicolon_covers_both_parts():
    assert INDEX.map_request("Provider name; address; taxpayer ID; and amount paid") == [
        Match("provider name address taxpayer id and amount paid", RULE, ("R018",))]


def test_parts_of_only_separators_name_nothing():
    assert mapped(";\n,") == []
    assert mapped("W-2;,;") == [(RULE, ("R001",))]


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
            assert r.id in matches[0].best, text


def test_every_document_text_maps_to_its_rule_first():
    for r in RULES:
        if r.document:
            matches = [m for m in merge(INDEX.map_request(r.document)) if m.kind != CONTEXT]
            assert len(matches) == 1, r.document
            assert matches[0].kind == RULE, r.document
            assert matches[0].rule_ids[0] == r.id, r.document


def test_document_text_matches_with_or_without_its_separators():
    for text in ["Provider name, address, taxpayer ID, and amount paid",
                 "Provider name; address; taxpayer ID; and amount paid",
                 "Provider name, address, taxpayer ID and amount paid",
                 "Provider name address taxpayer ID and amount paid"]:
        assert mapped(text) == [(RULE, ("R018",))], text
    assert mapped("Provider name, address, taxpayer, ID, and amount paid") == [(UNMATCHED, ())]


def test_every_ignore_alias_is_ignored():
    for text in IGNORE_LIST:
        assert mapped(text) == [(IGNORE, ())], text


@pytest.mark.parametrize(
    "text",
    ["Prior year tax return", "prior-year tax return", "Prior year's tax return", "Last year's tax return",
     "Copy of 2025 tax return", "Previous year's tax return", "Prior-year return"],
)
def test_prior_year_returns_are_ignored(text):
    assert mapped(text) == [(IGNORE, ())]


# Every rule, traps included, read in phrasings a model or preparer would write.
# Each phrasing must name its rule alone once merged; context segments are allowed.
PARAPHRASES = {
    "R001": ["W-2s from both jobs", "Wage and tax statement from your employer", "Form W2 for each job",
             "Corrected Form W-2 (W-2c) from employer"],
    "R002": ["Business income and expense records", "Records of business income and expenses",
             "1099-NECs from clients", "Self-employment income records"],
    "R003": ["Form 1099-K from the payment app", "1099 K", "Payment app statements"],
    "R004": ["1099 INT", "Form 1099-INT from each bank", "Bank interest statements"],
    "R005": ["1099 DIV", "Dividend statements from the fund", "Form 1099-DIV for dividends and distributions"],
    "R006": ["Consolidated Form 1099 from your brokerage", "Brokerage 1099", "Year-end brokerage statement",
             "Form 1099-B for stock sales"],
    "R007": ["Cryptocurrency transaction history", "Crypto exchange records", "Form 1099-DA from the platform",
             "Digital asset transaction records"],
    "R008": ["Form 1099-R from the pension plan", "Retirement distribution statement", "1099 R for the rollover"],
    "R009": ["SSA 1099", "Social Security benefit statement", "Form SSA-1099 for Social Security benefits"],
    "R010": ["Unemployment compensation statement", "Unemployment 1099-G", "Unemployment compensation records",
             "Form 1099-G (unemployment compensation statement)"],
    "R011": ["1099-G for state refund", "State tax refund statement", "State refund 1099-G",
             "1099-G for state tax refund"],
    "R012": ["Form 1095-A from the Marketplace", "Health Insurance Marketplace statement", "1095 A"],
    "R013": ["Form 1099-SA from the HSA", "HSA distribution statement", "1099 SA"],
    "R014": ["Form 1098 from the mortgage lender", "Mortgage interest statement", "1098 for the mortgage"],
    "R015": ["Closing Disclosure for the home purchase", "Purchase closing statement", "HUD-1 from buying the house"],
    "R016": ["Form 1098-E from the loan servicer", "Student loan interest statement", "1098E"],
    "R017": ["Form 1098-T from the college", "Tuition statement", "1098 T"],
    "R018": ["Provider name, address, taxpayer ID and amount paid", "Child care provider name, address, and EIN",
             "Daycare receipts", "Dependent care provider's name and taxpayer ID"],
    "R019": ["Social Security number and date of birth for new dependent", "Social Security card for the new baby",
             "Dependent's Social Security number", "Birth certificate and Social Security card for new child",
             "Newborn's SSN"],
    "R020": ["Schedule K-1 from each partnership", "K-1s", "Schedule K-1 and Schedule K-3"],
    "R021": ["Rental property income and expenses", "Records of rental income", "Rental income records for the duplex"],
    "R022": ["Records of estimated taxes paid", "Estimated tax payment confirmations", "Form 1040-ES vouchers",
             "Form 1040 ES", "Estimated tax records"],
    "R023": ["Form W-2G for gambling winnings", "W2-G", "Gambling winnings statement"],
    "R024": ["IP PIN letter", "Identity Protection PIN", "CP01A notice"],
    "R025": ["Receipts and acknowledgments for charitable contributions", "Charitable donation receipts",
             "Written acknowledgments from the charities"],
    "R026": ["Records of tips received", "Tip records", "Form 4070 tip reports", "Pay stubs showing tips"],
    "R027": ["Overtime pay documentation", "Overtime records", "Overtime pay statement from the employer",
             "Pay stubs showing overtime hours"],
    "R028": ["Auto loan interest statement", "Form 1098-VLI", "Car loan interest statement from the lender"],
    "R029": ["Form 5498-SA", "HSA contribution records", "5498 SA from the HSA custodian"],
    "R030": ["Sale closing statement", "Form 1099-S", "1099S from the title company"],
    "T001": ["Solar panel installation invoice", "Receipts for solar installation",
             "Residential clean energy credit paperwork", "Form 5695 (Residential Clean Energy Credit)"],
    "T002": ["Receipts for energy-efficient windows", "Manufacturer certification statement for the heat pump",
             "Home energy improvement receipts"],
    "T003": ["Form 8936", "Clean vehicle seller report from the dealer",
             "Form 8936, Clean Vehicle Credit"],
}


def test_the_paraphrase_battery_covers_every_rule_three_times():
    assert set(PARAPHRASES) == {r.id for r in RULES}
    assert all(len(set(texts)) >= 3 for texts in PARAPHRASES.values())


@pytest.mark.parametrize(
    "rule_id,text", [(rule_id, text) for rule_id, texts in PARAPHRASES.items() for text in texts])
def test_paraphrases_name_their_rule_alone(rule_id, text):
    kind = TRAP if rule_id.startswith("T") else RULE
    assert credited(text) == [(kind, (rule_id,))]


# Aliases that name more than one rule on purpose, with the rules they name.
AMBIGUOUS = {
    "1099-g": ("R010", "R011"),
    "form 1099-g": ("R010", "R011"),
    "closing statement": ("R015", "R030"),
    "settlement statement": ("R015", "R030"),
    "form 5695": ("T001", "T002"),
    "5695": ("T001", "T002"),
}


def test_every_ambiguous_alias_is_a_real_alias_of_each_rule_it_names():
    by_id = {r.id: r for r in RULES}
    for text, rule_ids in AMBIGUOUS.items():
        for rule_id in rule_ids:
            r = by_id[rule_id]
            assert text in {a.casefold() for a in (*r.aliases, r.document or "")}, (text, rule_id)


def test_every_alias_and_document_text_names_its_rule_alone():
    for r in RULES:
        for text in (*r.aliases, *([r.document] if r.document else [])):
            expected = AMBIGUOUS.get(text.casefold(), (r.id,))
            assert credited(text) == [(TRAP if r.trap else RULE, expected)], text


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Form 1099-S", [(RULE, ("R030",))]),
        ("Social Security benefit statement", [(RULE, ("R009",))]),
        ("Form SSA-1099 (Social Security benefits)", [(RULE, ("R009",))]),
        ("Child care receipts for the dependent", [(RULE, ("R018",))]),
        ("Form 1098 for the new home", [(RULE, ("R014",))]),
        ("Closing statement for the rental property", [(RULE, ("R015", "R030"))]),
        ("Brokerage 1099-DIV", [(RULE, ("R005",))]),
        ("Schedule K-1 showing rental income", [(RULE, ("R020",))]),
        ("W-2 showing tips", [(RULE, ("R001",))]),
        ("Form 1098-E for student loans", [(RULE, ("R016",))]),
        ("2025 W-2", [(RULE, ("R001",))]),
        ("Prior year W-2", [(RULE, ("R001",))]),
        ("Form 1099-SA", [(RULE, ("R013",))]),
        ("Form 1099-INT from the brokerage", [(RULE, ("R004",))]),
        ("Receipts for the new windows and heat pump, for Form 5695", [(TRAP, ("T001", "T002"))]),
        ("Dependent care FSA statement", [(UNMATCHED, ())]),
        ("Seller report", [(UNMATCHED, ())]),
        ("Pay stubs", [(UNMATCHED, ())]),
        # Facts named in requests for other documents credit nothing.
        ("Voided check for direct deposit of your federal and state refunds", []),
        ("Direct deposit information for your state refund", []),
        ("State tax refund amount", [(UNMATCHED, ())]),
        ("Estimated tax penalty notice (if any)", [(UNMATCHED, ())]),
        ("Spouse's date of birth", []),
        ("Your Social Security card", []),
        ("Social Security cards for you and your spouse", []),
        ("SSA-1099 (shows your Social Security number)", [(RULE, ("R009",))]),
        ("Form 1099-K for your self-employment income", [(RULE, ("R003",))]),
        ("Form W-2 showing your reported tip income", [(RULE, ("R001",))]),
        ("Form W-2 showing overtime compensation", [(RULE, ("R001",))]),
        ("Rental business income and expenses", [(UNMATCHED, ())]),
        # An abbreviation's letters never complete a form number.
        ("Form 1098 (e.g. from your mortgage lender)", [(RULE, ("R014",))]),
        ("Form 1040 (e.g. PDF)", [(UNMATCHED, ())]),
        ("Copy of Form 1040 (e.g. last year's)", [(UNMATCHED, ())]),
        ("Form 1098 (T. Rowe Price)", [(RULE, ("R014",))]),
    ],
)
def test_new_aliases_make_no_false_matches(text, expected):
    assert credited(text) == expected


def test_a_new_dependent_named_beside_another_document_also_names_r019():
    # The dependent nouns are R019 aliases, so a passing mention credits R019 too: a known trade-off.
    assert credited("Child care receipts for the new baby") == [(RULE, ("R018",)), (RULE, ("R019",))]


def test_identity_documents_alone_are_ignored_and_with_the_new_dependent_name_r019():
    assert INDEX.map_request("Social Security card") == [Match("social security card", IGNORE)]
    assert INDEX.map_request("Social Security card for the new baby") == [
        Match("social security card", IGNORE), Match("new baby", RULE, ("R019",))]
    assert mapped("Dependent Social Security card") == [(RULE, ("R019",))]


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


def test_tied_counts_the_candidates_sharing_the_longest_alias():
    index = AliasIndex([rule("R001", "x"), rule("R002", "x"), rule("R003", "x y"), rule("R004", "y")], [])
    assert index.map_request("x") == [Match("x", RULE, ("R001", "R002"), 2)]
    assert index.map_request("x y") == [Match("x y", RULE, ("R003", "R001", "R002", "R004"), 1)]


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


def test_a_separator_in_an_alias_matches_any_one_separator_or_none():
    index = AliasIndex([rule("R001", "a, b")], [])
    for text in ["a, b", "a b", "a; b", "a & b", "a\nb"]:
        assert mapped(text, index) == [(RULE, ("R001",))], text
    assert mapped("a, , b", index) == [(UNMATCHED, ())]


# merge


def test_merge_joins_matches_of_one_rule():
    assert merge([Match("a", RULE, ("R001",)), Match("b", RULE, ("R001",)), Match("c", RULE, ("R001",))]) == [
        Match("a, b, c", RULE, ("R001",))]


def test_merge_keeps_matches_of_different_rules_apart_and_in_order():
    matches = [Match("a", RULE, ("R001",)), Match("b", RULE, ("R004",)), Match("c", RULE, ("R001",))]
    assert merge(matches) == [Match("a, c", RULE, ("R001",)), Match("b", RULE, ("R004",))]


def test_merge_keeps_the_shared_best_candidates_in_the_earlier_order_all_tied():
    both = merge([Match("x", RULE, ("R010", "R011"), 2), Match("y", RULE, ("R011", "R010"), 2)])
    assert both == [Match("x, y", RULE, ("R010", "R011"), 2)]
    narrowed = merge([Match("x", RULE, ("R010", "R011"), 2), Match("y", RULE, ("R011", "R010"))])
    assert narrowed == [Match("x, y", RULE, ("R011",))]


def test_merge_reads_only_best_candidates():
    matches = [Match("x", RULE, ("R011", "R010")), Match("y", RULE, ("R010",))]
    assert merge(matches) == matches


def test_merge_joins_the_first_earlier_match_that_shares_a_rule():
    matches = [Match("a", RULE, ("R001",)), Match("b", RULE, ("R004",)), Match("c", RULE, ("R004", "R001"), 2)]
    assert merge(matches) == [Match("a, c", RULE, ("R001",)), Match("b", RULE, ("R004",))]


def test_merge_compares_with_the_narrowed_match():
    matches = [Match("x", RULE, ("R010", "R011"), 2), Match("y", RULE, ("R010",)), Match("z", RULE, ("R011",))]
    assert merge(matches) == [Match("x, y", RULE, ("R010",)), Match("z", RULE, ("R011",))]


def test_merge_joins_traps_but_never_a_trap_with_a_rule():
    assert merge([Match("a", TRAP, ("T001", "T002"), 2), Match("b", TRAP, ("T001",))]) == [
        Match("a, b", TRAP, ("T001",))]
    matches = [Match("a", TRAP, ("T001",)), Match("b", RULE, ("T001",))]
    assert merge(matches) == matches


def test_merge_joins_only_within_one_part():
    apart = [Match("a", RULE, ("R001",)), Match("b", RULE, ("R001",), part=1), Match("c", TRAP, ("T001",), part=1),
             Match("d", TRAP, ("T001",), part=2)]
    assert merge(apart) == apart
    assert merge([Match("a", RULE, ("R010", "R011"), 2, 3), Match("b", RULE, ("R011",), 1, 3)]) == [
        Match("a, b", RULE, ("R011",), 1, 3)]


def test_merge_on_parts_of_real_requests():
    assert merge(INDEX.map_request("Form W-2 from Acme; Form W-2 from Beta")) == [
        Match("form w-2", RULE, ("R001",)), Match("form w-2", RULE, ("R001",), part=1)]
    assert credited("Form 1099-G (unemployment); Form 1099-G (state refund)") == [
        (RULE, ("R010", "R011")), (RULE, ("R010", "R011"))]
    assert credited("Closing statement for the home you sold; closing statement for the home you bought") == [
        (RULE, ("R015", "R030")), (RULE, ("R015", "R030"))]


def test_merge_passes_other_kinds_through():
    matches = [Match("a", IGNORE), Match("a", IGNORE), Match("b", UNMATCHED), Match("b", UNMATCHED),
               Match("c", CONTEXT), Match("c", CONTEXT)]
    assert merge(matches) == matches
    assert merge([]) == []


def test_merge_on_real_requests():
    assert merge(INDEX.map_request("Form W-2 (Wage and Tax Statement)")) == [
        Match("form w-2, wage and tax statement", RULE, ("R001",))]
    assert merge(INDEX.map_request("Form 8936, Clean Vehicle Credit")) == [
        Match("form 8936, clean vehicle credit", TRAP, ("T003",))]
    assert merge(INDEX.map_request("Form 5695 (Residential Clean Energy Credit)")) == [
        Match("form 5695, residential clean energy credit", TRAP, ("T001",))]
