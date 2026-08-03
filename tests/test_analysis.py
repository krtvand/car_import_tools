"""Tests for the pricing-analysis module (pure functions, no DB/network)."""
from __future__ import annotations

import math

import numpy as np
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


# --- hedonic regression -----------------------------------------------------

REF_YEAR = 2025
# True log-linear world: log(price) = 10.2 - 0.09*age - 0.06*mileage_10k
B0, B_AGE, B_MILE10K = 10.2, -0.09, -0.06


def _price(age, mileage_km, noise=0.0):
    log_p = B0 + B_AGE * age + B_MILE10K * (mileage_km / 10_000) + noise
    return math.exp(log_p)


def _synthetic(n=60, noise_sd=0.0, seed=0):
    """Cars drawn from the known log-linear model above."""
    rng = np.random.default_rng(seed)
    records = []
    for i in range(n):
        age = int(rng.integers(0, 12))
        mileage = int(rng.integers(5_000, 180_000))
        noise = float(rng.normal(0, noise_sd)) if noise_sd else 0.0
        records.append(
            rec(ad_id=i, year=REF_YEAR - age, mileage_km=mileage, price=_price(age, mileage, noise))
        )
    return records


def test_fit_recovers_known_coefficients_on_noisefree_data():
    curve = analysis.fit_price_curve(_synthetic(noise_sd=0.0), ref_year=REF_YEAR)
    # beta layout: [intercept, age, mileage_10k]
    assert curve is not None
    assert curve.beta[0] == pytest.approx(B0, abs=1e-6)
    assert curve.beta[1] == pytest.approx(B_AGE, abs=1e-6)
    assert curve.beta[2] == pytest.approx(B_MILE10K, abs=1e-6)


def test_predict_point_estimate_matches_truth():
    curve = analysis.fit_price_curve(_synthetic(noise_sd=0.0), ref_year=REF_YEAR)
    pred = analysis.predict(curve, year=REF_YEAR - 5, mileage=60_000)
    assert pred.estimate == pytest.approx(_price(5, 60_000), rel=1e-6)


def test_predict_interval_brackets_estimate_with_noise():
    curve = analysis.fit_price_curve(_synthetic(n=80, noise_sd=0.15, seed=3), ref_year=REF_YEAR)
    pred = analysis.predict(curve, year=REF_YEAR - 4, mileage=70_000)
    assert pred.lo is not None and pred.hi is not None
    assert pred.lo < pred.estimate < pred.hi
    # asymmetric in price space: upper gap exceeds lower gap for a log-linear fit
    assert (pred.hi - pred.estimate) > (pred.estimate - pred.lo)


def test_predict_wider_alpha_gives_narrower_interval():
    curve = analysis.fit_price_curve(_synthetic(n=80, noise_sd=0.15, seed=5), ref_year=REF_YEAR)
    wide = analysis.predict(curve, year=REF_YEAR - 4, mileage=70_000, alpha=0.05)
    narrow = analysis.predict(curve, year=REF_YEAR - 4, mileage=70_000, alpha=0.5)
    assert (narrow.hi - narrow.lo) < (wide.hi - wide.lo)


def test_fit_returns_none_when_too_few_rows():
    rows = [rec(ad_id=i, year=2020, mileage_km=50_000 + i, price=20_000 + i) for i in range(4)]
    assert analysis.fit_price_curve(rows, ref_year=REF_YEAR) is None


def test_fit_adds_categorical_dummies_only_when_n_allows():
    # 40 cars, two fuel types with a real price effect -> dummy included.
    rng = np.random.default_rng(1)
    records = []
    for i in range(40):
        age = int(rng.integers(0, 10))
        mileage = int(rng.integers(10_000, 150_000))
        fuel = "Petrol" if i % 2 else "Diesel"
        bump = 0.2 if fuel == "Diesel" else 0.0  # diesel commands a premium
        records.append(
            rec(ad_id=i, year=REF_YEAR - age, mileage_km=mileage,
                price=_price(age, mileage, noise=bump), fuel_type=fuel)
        )
    curve = analysis.fit_price_curve(records, ref_year=REF_YEAR)
    assert "fuel_type" in curve.spec.categoricals
    # Predicting the two fuels at the same age/mileage differs by ~the premium.
    diesel = analysis.predict(curve, year=REF_YEAR - 5, mileage=60_000, fuel_type="Diesel")
    petrol = analysis.predict(curve, year=REF_YEAR - 5, mileage=60_000, fuel_type="Petrol")
    assert diesel.estimate > petrol.estimate


def test_fit_skips_dummies_on_small_sample():
    curve = analysis.fit_price_curve(_synthetic(n=12, noise_sd=0.0), ref_year=REF_YEAR)
    # 12 rows < MIN_FOR_DUMMIES -> numeric model only (3 columns).
    assert curve.spec.categoricals == {}
    assert curve.beta.shape == (3,)


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
