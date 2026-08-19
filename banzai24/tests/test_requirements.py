"""Judging one lot against one search.

Two rules carry this module, and both are about what a *missing* value means:

1. A ``[site]`` requirement with nothing on the sheet **passes** — banzai24
   already enforced it, and an unreadable box is not grounds to overturn a
   filter that was applied before we ever saw the lot.
2. A ``[sheet]`` requirement with nothing on the sheet is **unknown** — nobody
   has looked, and a report that counted that as a pass would put a car nobody
   inspected in the same pile as one that was cleared.

Get either backwards and the report still renders, still looks right, and is
wrong about which cars are worth bidding on.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from banzai24.config import AuctionFilters
from banzai24.models import AuctionLot, SheetExtraction
from banzai24.requirements import (
    FAILS,
    MEETS,
    UNCONFIRMED,
    SheetRequirements,
    banned_marks,
    judge,
)

FILTERS = AuctionFilters(
    make="MAZDA", model="CX-30", year_start=2023, year_end=2023,
    mileage_end=55_000, grade_origin=("4", "4.5", "5"),
)
REQUIREMENTS = SheetRequirements(drivetrain="4WD", no_damage_codes=("W", "X", "欠"))


def _lot(**overrides) -> AuctionLot:
    base = {
        "lot_number": "55-1850-33152", "lot_short": "33152",
        "banzai_id": "abc", "auction_id": 55, "auction_name": "CAA Chubu",
        "trade_date": date(2026, 8, 12), "trade_time": "12:00",
        "mark": "MAZDA", "model": "CX-30", "grade_origin": "5",
        "mileage_km": 15_000, "registration_year": 2023,
    }
    return AuctionLot(**{**base, **overrides})


def _extraction(marks=(), **overrides) -> SheetExtraction:
    base = {
        "lot_number": "55-1850-33152",
        "extracted_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
        "model_id": "claude-opus-5", "sheet_sha256": "abc", "raw_json": "{}",
        "sheet_grade": "5", "sheet_mileage_km": 15_415,
        "first_registration_year": 2023, "drivetrain": "4WD",
        "damage_marks": json.dumps(
            [{"panel": p, "code": c} for p, c in marks], ensure_ascii=False),
    }
    return SheetExtraction(**{**base, **overrides})


def _judge(lot=None, extraction=None, filters=FILTERS, requirements=REQUIREMENTS):
    return judge(filters, requirements, lot or _lot(), extraction)


# --- groups ------------------------------------------------------------------


def test_a_clean_read_sheet_meets_everything():
    assert _judge(extraction=_extraction(marks=[("roof", "A1")])).group == MEETS


def test_an_unread_sheet_is_unconfirmed_not_a_pass():
    """The majority of a freshly fetched run, and the reason the middle group
    exists at all: nothing has looked at these cars."""
    assessment = _judge(extraction=None)
    assert assessment.group == UNCONFIRMED
    assert {c.name for c in assessment.unknowns} == {"drivetrain", "damage"}


def test_a_read_sheet_with_a_blank_field_is_also_unconfirmed():
    """4 of 17 real extractions have a null drivetrain. "The model read the sheet
    and could not find it" is not a defect and is not a clearance."""
    assessment = _judge(extraction=_extraction(drivetrain=None))
    assert assessment.group == UNCONFIRMED
    assert assessment.get("drivetrain").detail == "drivetrain not on the sheet"


def test_a_failure_outranks_an_unknown():
    """A W2 on the door disqualifies the car whether or not the drivetrain box
    was legible — so the lot must not hide in *unconfirmed*."""
    assessment = _judge(extraction=_extraction(
        drivetrain=None, marks=[("left front door", "W2")]))
    assert assessment.group == FAILS


def test_a_search_with_no_requirements_meets_them_vacuously():
    assert _judge(filters=AuctionFilters(make="MAZDA"),
                  requirements=SheetRequirements()).group == MEETS


# --- the sheet re-judging what the site already filtered ---------------------


def test_the_sheets_exact_mileage_can_break_a_bound_the_apis_rounded_one_passed():
    """The case the whole re-judging exists for. banzai24 filtered on 55,000 and
    let this lot through; the sheet says 55,415."""
    assessment = _judge(
        lot=_lot(mileage_km=55_000),
        extraction=_extraction(sheet_mileage_km=55_415),
    )
    assert assessment.group == FAILS
    assert assessment.get("mileage").detail == "55,415 km, over 55,000 km"


def test_an_unreadable_mileage_box_does_not_overturn_the_sites_own_filter():
    """The asymmetry that makes the whole scheme safe. The site enforced this
    bound before we saw the lot; a blank box is not evidence against it."""
    assessment = _judge(extraction=_extraction(sheet_mileage_km=None))
    assert assessment.get("mileage").verdict == "pass"
    assert assessment.group == MEETS


def test_a_grade_the_sheet_disagrees_with_fails_rather_than_merely_flagging():
    assessment = _judge(extraction=_extraction(sheet_grade="3.5"))
    assert assessment.group == FAILS
    assert "wanted 4/4.5/5" in assessment.get("grade").detail


def test_grades_compare_numerically_where_they_are_numbers():
    """``4.5`` and ``4.50`` are the same grade; ``R`` is not a number at all."""
    assert _judge(extraction=_extraction(sheet_grade="4.50")).get("grade").verdict == "pass"
    assert _judge(extraction=_extraction(sheet_grade="R")).get("grade").verdict == "fail"


def test_a_bound_the_search_does_not_set_is_never_checked():
    """A search without a mileage ceiling has no mileage verdict — not a pass.
    The template prints no marker at all for it, which is the honest rendering."""
    filters = AuctionFilters(make="MAZDA", year_start=2023, year_end=2023)
    assert _judge(filters=filters, extraction=_extraction()).get("mileage") is None


# --- damage codes ------------------------------------------------------------


@pytest.mark.parametrize("code", ["W1", "W2", "W3", "XX", "X", "欠"])
def test_every_banned_letter_is_caught_at_any_severity(code):
    """W3 is a *worse* repair mark than W2, so a ban list naming the digit would
    wave through exactly the marks you most want to see."""
    assert _judge(extraction=_extraction(marks=[("door", code)])).group == FAILS


@pytest.mark.parametrize("code", ["A1", "U2", "B1", "G", "トビA", "A3U2"])
def test_innocent_codes_are_not_caught(code):
    """Including the compound and the Japanese ones that are really in the DB."""
    assert _judge(extraction=_extraction(marks=[("door", code)])).group == MEETS


def test_a_banned_letter_inside_a_compound_code_is_caught():
    """``auction.db`` holds ``A3U2`` and ``AU1``: codes combine, so equality
    against ``"W2"`` would miss a W sitting inside one."""
    assert _judge(extraction=_extraction(marks=[("door", "A3W2")])).group == FAILS


def test_a_clean_diagram_is_a_pass_not_an_unknown():
    """An empty damage list means the inspector drew nothing, which is a real
    finding — unlike a null field, which means nobody answered."""
    assessment = _judge(extraction=_extraction(marks=[]))
    assert assessment.get("damage").verdict == "pass"


def test_the_failure_names_the_mark_and_where_it_is():
    """The card has to say which mark disqualified the car — the whole point of
    the group is that you can act on it without re-reading the diagram."""
    detail = _judge(extraction=_extraction(
        marks=[("roof", "A1"), ("left front door", "W2")])).get("damage").detail
    assert "W2 on the left front door" in detail
    assert "A1" not in detail


def test_malformed_damage_json_reads_as_clean_rather_than_raising():
    """A report that will not render because one row holds bad JSON is worse
    than one that treats it as no marks — but the consequence is worth knowing,
    which is why it is asserted rather than left implicit."""
    assert banned_marks("{not json", ("W",)) == []
    assert banned_marks(None, ("W",)) == []
