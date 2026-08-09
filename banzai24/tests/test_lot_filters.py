"""Post-fetch lot filters, and the inconsistent field they have to cope with."""
from __future__ import annotations

from banzai24 import lot_filters
from banzai24.lot_filters import LotFilters


def _lot(code=None, short=None, body_number=None) -> dict:
    return {
        "bodyModelCode": code,
        "car": {"shortCodeModel": short},
        "characteristics": {"bodyNumber": body_number},
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
    assert LotFilters().describe() == "none"
