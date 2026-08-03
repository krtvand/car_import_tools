"""Tests for the pricing-analysis module (pure functions, no DB/network)."""
from __future__ import annotations

import pytest

import analysis
from analysis import CarRecord


def rec(ad_id=1, price=20000.0, year=2020, mileage_km=50000, **kw) -> CarRecord:
    """Build a plausible record; override any field via kwargs."""
    return CarRecord(ad_id=ad_id, price=price, year=year, mileage_km=mileage_km, **kw)


# --- car_age ----------------------------------------------------------------

def test_car_age_basic():
    assert analysis.car_age(2015, ref_year=2025) == 10


def test_car_age_floors_at_zero_for_future_model_year():
    assert analysis.car_age(2026, ref_year=2025) == 0


def test_car_age_defaults_to_current_year():
    age = analysis.car_age(analysis._current_year() - 3)
    assert age == 3


# --- filter_model -----------------------------------------------------------

def test_filter_model_matches_make_slug_insensitive():
    records = [
        rec(ad_id=1, make="Mazda", model="CX-30"),
        rec(ad_id=2, make="mazda", model="cx-5"),
        rec(ad_id=3, make="Toyota", model="Yaris"),
    ]
    out = analysis.filter_model(records, make="mazda")
    assert {r.ad_id for r in out} == {1, 2}


def test_filter_model_matches_model_when_given():
    records = [
        rec(ad_id=1, make="Mazda", model="CX-30"),
        rec(ad_id=2, make="Mazda", model="cx-30"),
        rec(ad_id=3, make="Mazda", model="CX-5"),
    ]
    out = analysis.filter_model(records, make="Mazda", model="cx-30")
    assert {r.ad_id for r in out} == {1, 2}


# --- outlier hygiene --------------------------------------------------------

def test_is_usable_accepts_plausible_record():
    assert analysis.is_usable(rec(), ref_year=2025)


@pytest.mark.parametrize(
    "field, value",
    [
        ("price", None),
        ("year", None),
        ("mileage_km", None),
        ("price", 0.0),            # below MIN_PRICE (parts/deposit listing)
        ("price", 5_000_000.0),    # above MAX_PRICE
        ("mileage_km", -1),        # negative
        ("mileage_km", 2_000_000), # beyond MAX_MILEAGE_KM
        ("year", 1900),            # before MIN_YEAR
        ("year", 2027),            # more than one year ahead of ref_year 2025
    ],
)
def test_is_usable_rejects_missing_or_impossible(field, value):
    assert not analysis.is_usable(rec(**{field: value}), ref_year=2025)


def test_is_usable_allows_next_model_year():
    assert analysis.is_usable(rec(year=2026), ref_year=2025)


def test_clean_drops_only_the_bad_rows():
    records = [
        rec(ad_id=1),                         # good
        rec(ad_id=2, price=None),             # missing price
        rec(ad_id=3, mileage_km=3_000_000),   # impossible mileage
        rec(ad_id=4, year=2021),              # good
    ]
    kept = analysis.clean(records, ref_year=2025)
    assert {r.ad_id for r in kept} == {1, 4}


# --- to_records -------------------------------------------------------------

# --- comparables ------------------------------------------------------------

def test_comparables_median_of_in_band_cars():
    # Six 2020 cars near 50k km; two 2015 cars are out of the year band.
    records = [
        rec(ad_id=i, year=2020, mileage_km=50000, price=p)
        for i, p in enumerate([18000, 19000, 20000, 21000, 22000, 23000])
    ] + [rec(ad_id=99, year=2015, mileage_km=50000, price=5000)]
    result = analysis.comparables(records, year=2020, mileage=50000)
    assert result.n == 6
    assert result.estimate == 20500.0  # median of the six in-band prices
    assert result.widened == 0
    assert result.confidence == "medium"


def test_comparables_high_confidence_with_many_matches():
    records = [
        rec(ad_id=i, year=2020, mileage_km=50000, price=20000 + i)
        for i in range(15)
    ]
    result = analysis.comparables(records, year=2020, mileage=50000)
    assert result.n == 15
    assert result.confidence == "high"
    assert result.widened == 0


def test_comparables_widens_band_when_thin_and_lowers_confidence():
    # Only 2 exact-band cars, but more appear once the band widens.
    records = [
        rec(ad_id=1, year=2020, mileage_km=50000, price=20000),
        rec(ad_id=2, year=2020, mileage_km=52000, price=21000),
        rec(ad_id=3, year=2022, mileage_km=80000, price=23000),
        rec(ad_id=4, year=2018, mileage_km=20000, price=19000),
        rec(ad_id=5, year=2022, mileage_km=78000, price=22000),
    ]
    result = analysis.comparables(records, year=2020, mileage=50000)
    assert result.widened >= 1
    assert result.confidence == "low"
    assert result.n >= analysis.MIN_COMPARABLES or result.widened == 2


def test_comparables_none_when_nothing_matches():
    records = [rec(ad_id=1, year=2005, mileage_km=300000, price=3000)]
    result = analysis.comparables(records, year=2023, mileage=10000)
    assert result.estimate is None
    assert result.n == 0
    assert result.confidence == "none"


def test_comparables_ignores_rows_missing_fields():
    records = [
        rec(ad_id=1, year=2020, mileage_km=50000, price=20000),
        rec(ad_id=2, year=2020, mileage_km=None, price=21000),
        rec(ad_id=3, year=2020, mileage_km=50000, price=None),
    ]
    result = analysis.comparables(records, year=2020, mileage=50000, max_widen=0)
    assert result.n == 1
    assert result.estimate == 20000.0


def test_to_records_projects_listing_attributes():
    class FakeListing:
        ad_id = 7
        price = 15000.0
        year = 2019
        mileage_km = 80000
        make = "Mazda"
        model = "CX-5"
        fuel_type = "Petrol"
        gearbox = "Automatic"
        seller_type = "dealer"
        is_active = True
        days_on_market = 42

    (r,) = analysis.to_records([FakeListing()])
    assert r == CarRecord(
        ad_id=7, price=15000.0, year=2019, mileage_km=80000,
        make="Mazda", model="CX-5", fuel_type="Petrol", gearbox="Automatic",
        seller_type="dealer", is_active=True, days_on_market=42,
    )
