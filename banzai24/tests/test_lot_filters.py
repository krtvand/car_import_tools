"""Post-fetch lot filters, and the inconsistent field they have to cope with."""
from __future__ import annotations

from banzai24 import lot_filters
from banzai24.lot_filters import LotFilters


def _lot(code=None, short=None, body_number=None, colour=None,
         modification=None) -> dict:
    return {
        "bodyModelCode": code,
        "car": {"shortCodeModel": short},
        "characteristics": {"bodyNumber": body_number, "color": colour,
                            "modification": modification},
    }


# --- normalisation -----------------------------------------------------------
#
# banzai24 writes the same model both ways. Both forms appear in the saved runs
# for one and the same CX-30, which is the whole reason this module exists.

def test_prefixed_and_bare_codes_normalise_to_the_same_thing():
    assert lot_filters.normalize_model_code("5AA-DMEJ3P") == "DMEJ3P"
    assert lot_filters.normalize_model_code("DMEJ3P") == "DMEJ3P"


def test_normalisation_is_case_and_whitespace_insensitive():
    assert lot_filters.normalize_model_code("  6la-axap54 ") == "AXAP54"


def test_normalising_nothing_yields_nothing():
    assert lot_filters.normalize_model_code(None) == ""
    assert lot_filters.normalize_model_code("") == ""
    assert lot_filters.normalize_model_code("   ") == ""


# --- reading the code off a lot ----------------------------------------------

def test_body_model_code_is_the_first_choice():
    assert lot_filters.model_code_of(_lot(code="5AA-DMEJ3P", short="OTHER")) == "DMEJ3P"


def test_falls_back_to_short_code_model():
    assert lot_filters.model_code_of(_lot(code=None, short="6BA-MXAA54")) == "MXAA54"


def test_falls_back_to_body_number_which_splits_the_other_way():
    """`bodyNumber` is `DMEJ3P-10**32` — code first, then the masked serial."""
    assert lot_filters.model_code_of(_lot(body_number="DMEJ3P-10**32")) == "DMEJ3P"


def test_a_lot_with_no_code_anywhere_reads_as_empty():
    assert lot_filters.model_code_of(_lot()) == ""
    assert lot_filters.model_code_of({}) == ""


# --- matching ----------------------------------------------------------------

def test_one_pattern_matches_both_spellings_of_the_same_model():
    wanted = LotFilters(body_model_code=("DMEJ3P",))
    assert wanted.matches(_lot(code="5AA-DMEJ3P"))
    assert wanted.matches(_lot(code="DMEJ3P"))


def test_a_prefixed_pattern_also_matches_a_bare_value():
    """Normalisation runs on both sides, so either spelling can be asked for."""
    assert LotFilters(body_model_code=("5AA-DMEJ3P",)).matches(_lot(code="DMEJ3P"))


def test_a_near_miss_is_not_a_match():
    assert not LotFilters(body_model_code=("DMEJ3P",)).matches(_lot(code="5AA-DMEJ3R"))


def test_a_shorter_pattern_deliberately_spans_variants():
    wanted = LotFilters(body_model_code=("DMEJ3",))
    assert wanted.matches(_lot(code="DMEJ3P"))
    assert wanted.matches(_lot(code="5AA-DMEJ3R"))


def test_patterns_are_or_ed():
    wanted = LotFilters(body_model_code=("DMEJ3P", "DMFP"))
    assert wanted.matches(_lot(code="DMFP"))
    assert not wanted.matches(_lot(code="AXAH54"))


def test_matching_is_case_insensitive():
    assert LotFilters(body_model_code=("dmej3p",)).matches(_lot(code="5AA-DMEJ3P"))


def test_a_prefix_only_pattern_matches_nothing():
    """The prefix is exactly the undependable part, so filtering on it is a no-op."""
    assert not LotFilters(body_model_code=("5AA",)).matches(_lot(code="5AA-DMEJ3P"))


def test_a_codeless_lot_is_rejected_rather_than_waved_through():
    assert not LotFilters(body_model_code=("DMEJ3P",)).matches(_lot())


def test_an_empty_filter_keeps_everything():
    empty = LotFilters()
    assert not empty.active
    assert empty.matches(_lot())
    assert empty.matches(_lot(code="ANYTHING"))


def test_active_reports_whether_any_criterion_is_set():
    assert LotFilters(body_model_code=("DMEJ3P",)).active


def test_split_returns_both_halves_so_a_run_can_report_what_it_dropped():
    lots = [_lot(code="5AA-DMEJ3P"), _lot(code="DMEJ3R"), _lot(code="DMEJ3P")]
    kept, rejected = lot_filters.split(lots, LotFilters(body_model_code=("DMEJ3P",)))
    assert len(kept) == 2 and len(rejected) == 1
    assert rejected[0]["bodyModelCode"] == "DMEJ3R"


def test_describe_names_the_active_criteria():
    assert "DMEJ3P" in LotFilters(body_model_code=("DMEJ3P",)).describe()
    assert "black" in LotFilters(exclude_colours=("black",)).describe()
    assert LotFilters().describe() == "none"


# --- excluded colours --------------------------------------------------------
#
# The one criterion that names what to drop rather than what to keep, so the
# lot the API said nothing about goes the other way from a codeless one.

def test_colour_is_read_off_the_lot_lower_cased():
    """The API writes it upper-case; everything downstream shows lower-case."""
    assert lot_filters.colour_of(_lot(colour="BLACK")) == "black"
    assert lot_filters.colour_of(_lot()) == ""
    assert lot_filters.colour_of({}) == ""


def test_an_excluded_colour_is_dropped():
    unwanted = LotFilters(exclude_colours=("black", "blue"))
    assert not unwanted.matches(_lot(colour="BLACK"))
    assert not unwanted.matches(_lot(colour="BLUE"))


def test_a_colour_not_named_is_kept():
    unwanted = LotFilters(exclude_colours=("black", "blue"))
    assert unwanted.matches(_lot(colour="WHITE"))
    assert unwanted.matches(_lot(colour="GRAY"))


def test_the_exclusion_is_written_in_either_case():
    assert not LotFilters(exclude_colours=("BLACK",)).matches(_lot(colour="black"))


def test_a_lot_with_no_colour_is_kept_not_dropped():
    """The opposite of a codeless lot: an exclusion drops only what it recognises."""
    unwanted = LotFilters(exclude_colours=("black",))
    assert unwanted.matches(_lot())
    assert unwanted.matches(_lot(colour=""))


def test_colours_are_matched_whole_not_as_substrings():
    """`grey` is not `gray`, and a half-written colour excludes nothing."""
    assert LotFilters(exclude_colours=("blac",)).matches(_lot(colour="BLACK"))
    assert LotFilters(exclude_colours=("grey",)).matches(_lot(colour="GRAY"))


def test_excluding_a_colour_is_an_active_filter():
    assert LotFilters(exclude_colours=("black",)).active


def test_both_criteria_apply_to_the_same_lot():
    wanted = LotFilters(body_model_code=("DMEJ3P",), exclude_colours=("black",))
    assert wanted.matches(_lot(code="5AA-DMEJ3P", colour="WHITE"))
    assert not wanted.matches(_lot(code="5AA-DMEJ3P", colour="BLACK"))
    assert not wanted.matches(_lot(code="DMEJ3R", colour="WHITE"))


# --- excluded model grades ---------------------------------------------------
#
# The trim line is written in whatever order the auction house felt like, which
# is why this is an exclusion over words rather than a list of wanted spellings.

def test_the_trim_line_is_read_off_the_lot_as_upper_case_words():
    assert lot_filters.model_grade_of(
        _lot(modification="5d 4wd hybrid g")) == ["5D", "4WD", "HYBRID", "G"]
    assert lot_filters.model_grade_of(_lot()) == []
    assert lot_filters.model_grade_of({}) == []


def test_an_excluded_grade_is_dropped_however_the_line_is_ordered():
    """All four spellings of an X in one run of RAV4 data."""
    unwanted = LotFilters(exclude_model_grades=("X",))
    assert not unwanted.matches(_lot(modification="5D 4WD HYBRID X"))
    assert not unwanted.matches(_lot(modification="HYBRID X 4WD"))
    assert not unwanted.matches(_lot(modification="X 4WD"))
    assert not unwanted.matches(_lot(modification="X"))


def test_another_grade_is_kept():
    unwanted = LotFilters(exclude_model_grades=("X",))
    assert unwanted.matches(_lot(modification="5D 4WD HYBRID G"))
    assert unwanted.matches(_lot(modification="G 4WD"))
    assert unwanted.matches(_lot(modification="5D 4WD ADVENTURE OFFROAD PACKAGE"))


def test_a_grade_is_a_whole_word_not_a_letter_inside_one():
    """Banning `X` must not ban the X sitting inside a longer word."""
    assert LotFilters(exclude_model_grades=("X",)).matches(
        _lot(modification="4WD XLE PACKAGE"))


def test_a_multi_word_grade_matches_only_when_its_words_are_consecutive():
    unwanted = LotFilters(exclude_model_grades=("HYBRID X",))
    assert not unwanted.matches(_lot(modification="5D 4WD HYBRID X"))
    assert unwanted.matches(_lot(modification="X 4WD"))          # not spelled out
    assert unwanted.matches(_lot(modification="HYBRID G 4WD"))


def test_the_exclusion_is_written_in_either_case_here_too():
    assert not LotFilters(exclude_model_grades=("x",)).matches(
        _lot(modification="HYBRID X 4WD"))


def test_a_lot_with_no_trim_line_is_kept_not_dropped():
    """About one hybrid RAV4 in six is listed as no more than `4WD`."""
    unwanted = LotFilters(exclude_model_grades=("X",))
    assert unwanted.matches(_lot(modification="4WD"))
    assert unwanted.matches(_lot(modification=""))
    assert unwanted.matches(_lot())


def test_excluding_a_grade_is_an_active_filter():
    assert LotFilters(exclude_model_grades=("X",)).active
    assert "X" in LotFilters(exclude_model_grades=("X",)).describe()


def test_the_grade_exclusion_applies_alongside_the_others():
    wanted = LotFilters(body_model_code=("AXAH54",), exclude_model_grades=("X",))
    assert wanted.matches(_lot(code="6AA-AXAH54", modification="5D 4WD HYBRID G"))
    assert not wanted.matches(_lot(code="6AA-AXAH54", modification="5D 4WD HYBRID X"))
    assert not wanted.matches(_lot(code="6AA-AXAH52", modification="5D 4WD HYBRID G"))


def test_a_wanted_grade_keeps_only_the_lots_that_name_it():
    """All three spellings of a Harrier's leather package in one archive walk."""
    wanted = LotFilters(model_grades=("LEATHER",))
    assert wanted.matches(_lot(modification="HYBRID Z LEATHER PACKAGE"))
    assert wanted.matches(_lot(modification="HYBRID Z LEATHER PACKAGE 4WD"))
    assert wanted.matches(_lot(modification="4WD HYBRID Z LEATHER PACKAGE"))
    assert not wanted.matches(_lot(modification="HYBRID Z"))
    assert not wanted.matches(_lot(modification="HYBRID Z 4WD"))


def test_a_wanted_grade_is_whole_consecutive_words_like_the_exclusion():
    wanted = LotFilters(model_grades=("HYBRID G",))
    assert wanted.matches(_lot(modification="4WD HYBRID G"))
    assert not wanted.matches(_lot(modification="HYBRID Z"))
    assert not wanted.matches(_lot(modification="G 4WD"))       # not spelled out
    assert LotFilters(model_grades=("g",)).matches(_lot(modification="4WD HYBRID G"))


def test_a_lot_with_no_trim_line_fails_a_wanted_grade():
    """The opposite of the exclusion, and for the model code's reason."""
    wanted = LotFilters(model_grades=("HYBRID Z",))
    assert not wanted.matches(_lot(modification="4WD"))
    assert not wanted.matches(_lot(modification=""))
    assert not wanted.matches(_lot())


def test_wanting_a_grade_is_an_active_filter():
    assert LotFilters(model_grades=("LEATHER",)).active
    assert "LEATHER" in LotFilters(model_grades=("LEATHER",)).describe()


def test_a_wanted_grade_and_an_excluded_one_both_apply():
    """The plain Harrier Z: the site says Z, and the leather ones are dropped."""
    wanted = LotFilters(model_grades=("HYBRID Z",), exclude_model_grades=("LEATHER",))
    assert wanted.matches(_lot(modification="HYBRID Z 4WD"))
    assert not wanted.matches(_lot(modification="HYBRID Z LEATHER PACKAGE"))
    assert not wanted.matches(_lot(modification="HYBRID G"))
