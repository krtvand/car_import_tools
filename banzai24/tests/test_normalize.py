"""Normalising banzai24's API JSON into flat rows.

Every literal in the parser tests below was taken from a saved run — the two
price formats, the empty-string-for-null fields, the missing registration
month. They are the inconsistencies this module exists to absorb, so they are
asserted on directly rather than through a fixture.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from banzai24 import normalize

FIXTURES = Path(__file__).parent / "fixtures"
SHEET = FIXTURES / "sheet_CAA-Chubu_2026-08-12_33152.jpg"


@pytest.fixture
def run_dir(tmp_path) -> Path:
    """A saved run, copied somewhere writable so tests can add sheets to it."""
    (tmp_path / "sheets").mkdir()
    (tmp_path / "lots.json").write_text(
        (FIXTURES / "lots_run.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_path


def _items(run_dir: Path) -> list[dict]:
    payload = json.loads((run_dir / "lots.json").read_text(encoding="utf-8"))
    return [item for page in payload["pages"] for item in page["items"]]


# --- prices ------------------------------------------------------------------
#
# The same field arrives in two formats. Both appear in saved runs.

def test_both_price_formats_parse_to_the_same_kind_of_number():
    assert normalize.parse_price("1 180 000 ¥") == 1180000
    assert normalize.parse_price("980000") == 980000


def test_zero_yen_means_no_price_yet_not_a_price_of_zero():
    """banzai24 writes "0 ¥" for a lot that has not sold. Storing 0 would drag
    every average and minimum over the column down to nothing."""
    assert normalize.parse_price("0 ¥") is None
    assert normalize.parse_price(0) is None


def test_absent_and_unparseable_prices_read_as_none():
    assert normalize.parse_price(None) is None
    assert normalize.parse_price("") is None
    assert normalize.parse_price("—") is None


# --- Japanese era dates ------------------------------------------------------
#
# Sheets print era years, the API reports Gregorian ones; comparing them at all
# requires this.

def test_reiwa_converts_to_gregorian():
    assert normalize.parse_era_date("R5年1月") == (2023, 1)
    assert normalize.parse_era_date("令和5年1月") == (2023, 1)


def test_heisei_and_showa_convert_too():
    assert normalize.parse_era_date("H31年12月") == (2019, 12)
    assert normalize.parse_era_date("S60年3月") == (1985, 3)


def test_separator_style_does_not_matter():
    assert normalize.parse_era_date("R5/1") == (2023, 1)
    assert normalize.parse_era_date("R5.1") == (2023, 1)


def test_gannen_is_year_one():
    """元年 is how year 1 of an era is written — never 1年."""
    assert normalize.parse_era_date("R元年3月") == (2019, 3)


def test_a_year_without_a_month_still_parses():
    assert normalize.parse_era_date("R5年") == (2023, 0)


def test_unparseable_era_dates_yield_nothing_rather_than_a_guess():
    assert normalize.parse_era_date("2023-01") is None
    assert normalize.parse_era_date("X5年1月") is None
    assert normalize.parse_era_date("") is None
    assert normalize.parse_era_date(None) is None


# --- the Russian half --------------------------------------------------------

def test_transmission_is_translated():
    assert normalize.normalize_transmission("Автомат") == "auto"
    assert normalize.normalize_transmission("Механика") == "manual"


def test_an_unknown_transmission_is_kept_verbatim_not_dropped():
    """A gearbox type we have not seen should look odd in the data, not vanish."""
    assert normalize.normalize_transmission("Полуавтомат") == "Полуавтомат"


def test_fuel_type_is_translated():
    assert normalize.normalize_fuel_type("Бензин") == "petrol"
    assert normalize.normalize_fuel_type("Гибрид") == "hybrid"
    assert normalize.normalize_fuel_type("Дизель") == "diesel"


def test_an_unknown_fuel_is_kept_verbatim():
    """"Газ" is deliberately not in the table — it could be LPG or CNG, and a
    wrong guess is worse than the Russian word sitting there being obvious."""
    assert normalize.normalize_fuel_type("Газ") == "Газ"


def test_a_blank_fuel_is_not_inferred_from_the_engine():
    """Blank on 72 of 112 saved lots. `characteristics.engine` looks like a
    second source but names the fuel only where fuelType already does, so there
    is nothing to fall back to — and engine size is not evidence of fuel."""
    assert normalize.normalize_fuel_type("") is None
    assert normalize.normalize_lot({
        "lot": {"number": "1-2-3"},
        "characteristics": {"fuelType": "", "engine": "2.0 л", "engineCapacity": "2.0"},
    })["fuel_type"] is None


def test_empty_strings_are_the_api_saying_null():
    assert normalize.normalize_steering("") is None
    assert normalize.blank_to_none("   ") is None
    assert normalize.blank_to_none("RIGHT") == "RIGHT"


# --- registration date -------------------------------------------------------

def test_registration_month_falls_back_to_the_car_year_field():
    """registrationMonth is null on most lots; car.year carries "2023.08"."""
    item = {"registrationYear": 2023, "registrationMonth": None,
            "car": {"year": "2023.08"}}
    assert normalize.parse_registration(item) == (2023, 8)


def test_an_explicit_month_wins_over_the_fallback():
    item = {"registrationYear": 2023, "registrationMonth": 5,
            "car": {"year": "2023.08"}}
    assert normalize.parse_registration(item) == (2023, 5)


def test_a_year_with_no_month_anywhere_leaves_the_month_empty():
    item = {"registrationYear": 2023, "registrationMonth": None, "car": {"year": "2023"}}
    assert normalize.parse_registration(item) == (2023, None)


def test_an_impossible_month_is_discarded():
    item = {"registrationYear": 2023, "registrationMonth": 13, "car": {}}
    assert normalize.parse_registration(item) == (2023, None)


# --- a whole lot -------------------------------------------------------------

def test_normalizing_a_real_lot_flattens_every_nesting_level(run_dir):
    row = normalize.normalize_lot(_items(run_dir)[0])

    assert row["lot_number"].count("-") == 2          # lot.number
    assert row["mark"] == "MAZDA"                     # car.mark
    assert isinstance(row["mileage_km"], int)         # characteristics.mileage
    assert isinstance(row["trade_date"], date)        # lot.tradeDate, parsed
    assert isinstance(row["auction_id"], int)         # lot.auction.id


def test_the_stored_chassis_code_is_the_one_the_filter_matches_on(run_dir):
    """Both must use lot_filters' normalisation, or a lot could be stored under
    a code that the --body-model-code filter which selected it would not match."""
    from banzai24.lot_filters import model_code_of

    for item in _items(run_dir):
        assert normalize.normalize_lot(item)["body_model_code"] == model_code_of(item)


def test_a_lot_without_a_lot_number_is_refused(run_dir):
    item = dict(_items(run_dir)[0], lot={})
    with pytest.raises(ValueError):
        normalize.normalize_lot(item)


def test_one_unreadable_lot_does_not_cost_us_the_others(run_dir):
    items = _items(run_dir)
    rows, problems = normalize.normalize_lots([dict(items[0], lot={}), *items])
    assert len(rows) == len(items)
    assert len(problems) == 1


# --- the sheet on disk -------------------------------------------------------

def test_a_downloaded_sheet_is_hashed_and_pointed_at(run_dir):
    row = normalize.normalize_lot(_items(run_dir)[0])
    (run_dir / "sheets" / f"{row['lot_number']}.jpg").write_bytes(SHEET.read_bytes())

    attached = normalize.attach_sheet(row, run_dir)
    assert attached["sheet_status"] == "pending"
    assert len(attached["sheet_sha256"]) == 64
    assert attached["sheet_path"].endswith(f"{row['lot_number']}.jpg")


def test_a_lot_with_no_sheet_published_is_distinct_from_one_not_downloaded(run_dir):
    """"no_sheet" is permanent; "pending" means re-running fetch would help."""
    row = normalize.normalize_lot(_items(run_dir)[0])

    assert normalize.attach_sheet(dict(row, sheet_url=None), run_dir)["sheet_status"] == "no_sheet"
    assert normalize.attach_sheet(row, run_dir)["sheet_status"] == "pending"


# --- a whole run -------------------------------------------------------------

def test_a_run_normalizes_only_the_lots_it_kept(run_dir):
    """The kept lots are the run's subject — the ones on the nearest day that
    survived the filter, and the only ones with sheets."""
    payload = json.loads((run_dir / "lots.json").read_text(encoding="utf-8"))
    rows, problems = normalize.load_run(run_dir)

    assert not problems
    assert [r["lot_number"] for r in rows] == payload["lots_selected"]
    assert len(rows) < len(_items(run_dir))     # the other days were set aside


def test_all_lots_widens_it_to_everything_the_fetch_saw(run_dir):
    rows, _ = normalize.load_run(run_dir, all_lots=True)
    assert len(rows) == len(_items(run_dir))


def test_a_run_saved_before_lots_selected_existed_normalizes_in_full(run_dir):
    """Older runs recorded no selection. Reading zero lots out of one would be
    a worse answer than reading all of them."""
    payload = json.loads((run_dir / "lots.json").read_text(encoding="utf-8"))
    del payload["lots_selected"]
    (run_dir / "lots.json").write_text(json.dumps(payload), encoding="utf-8")

    rows, _ = normalize.load_run(run_dir)
    assert len(rows) == len(_items(run_dir))
