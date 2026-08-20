"""Tests for the pricing-analysis module (pure functions, no DB/network)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from bazaraki import analysis
from bazaraki.analysis import CarRecord


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


# --- filter_fuel ------------------------------------------------------------

def test_filter_fuel_keeps_only_the_requested_fuel():
    records = [
        rec(ad_id=1, fuel_type="Petrol"),
        rec(ad_id=2, fuel_type="petrol"),
        rec(ad_id=3, fuel_type="Diesel"),
    ]
    out = analysis.filter_fuel(records, "PETROL")
    assert {r.ad_id for r in out} == {1, 2}


def test_filter_fuel_drops_unknown_fuel():
    records = [rec(ad_id=1, fuel_type="Petrol"), rec(ad_id=2, fuel_type=None)]
    assert {r.ad_id for r in analysis.filter_fuel(records, "Petrol")} == {1}


def test_filter_fuel_without_a_fuel_is_a_no_op():
    records = [rec(ad_id=1, fuel_type="Petrol"), rec(ad_id=2, fuel_type=None)]
    assert analysis.filter_fuel(records) == records


# --- exclude_availability ---------------------------------------------------

def test_exclude_availability_drops_the_named_state_case_insensitively():
    records = [
        rec(ad_id=1, availability="In stock"),
        rec(ad_id=2, availability="In transit"),
        rec(ad_id=3, availability="in-transit"),
    ]
    out = analysis.exclude_availability(records, analysis.IN_TRANSIT)
    assert {r.ad_id for r in out} == {1}


def test_exclude_availability_keeps_unknown_availability():
    records = [rec(ad_id=1, availability=None), rec(ad_id=2, availability="In transit")]
    out = analysis.exclude_availability(records, "In transit")
    assert {r.ad_id for r in out} == {1}


def test_exclude_availability_without_arguments_is_a_no_op():
    records = [rec(ad_id=1, availability="In transit"), rec(ad_id=2, availability=None)]
    assert analysis.exclude_availability(records) == records


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


def test_fit_adds_seller_type_dummy_and_prices_dealer_premium():
    # 40 cars, dealers vs private with a real price effect -> dummy included.
    rng = np.random.default_rng(2)
    records = []
    for i in range(40):
        age = int(rng.integers(0, 10))
        mileage = int(rng.integers(10_000, 150_000))
        seller = "dealer" if i % 2 else "private"
        bump = 0.15 if seller == "dealer" else 0.0  # dealers ask a premium
        records.append(
            rec(ad_id=i, year=REF_YEAR - age, mileage_km=mileage,
                price=_price(age, mileage, noise=bump), seller_type=seller)
        )
    curve = analysis.fit_price_curve(records, ref_year=REF_YEAR)
    assert "seller_type" in curve.spec.categoricals
    # Predicting the two seller types at the same age/mileage differs by ~the premium.
    dealer = analysis.predict(curve, year=REF_YEAR - 5, mileage=60_000, seller_type="dealer")
    private = analysis.predict(curve, year=REF_YEAR - 5, mileage=60_000, seller_type="private")
    assert dealer.estimate > private.estimate


def test_fit_skips_dummies_on_small_sample():
    curve = analysis.fit_price_curve(_synthetic(n=12, noise_sd=0.0), ref_year=REF_YEAR)
    # 12 rows < MIN_FOR_DUMMIES -> numeric model only (3 columns).
    assert curve.spec.categoricals == {}
    assert curve.beta.shape == (3,)


# --- Layer 2: price cuts ----------------------------------------------------

def test_price_cut_factor_median_of_first_to_last_drops():
    histories = [
        [20000, 19000, 18000],  # 10% cut
        [15000, 15000],         # 0% cut (flat)
        [10000, 9500],          # 5% cut
    ]
    signal = analysis.price_cut_factor(histories)
    assert signal.n == 3
    assert signal.median_cut == pytest.approx(0.05)


def test_price_cut_factor_skips_single_observation_trajectories():
    signal = analysis.price_cut_factor([[20000], [18000]])
    assert signal.median_cut is None
    assert signal.n == 0


# --- Layer 2: survivorship --------------------------------------------------

def _fit_noisefree_curve():
    return analysis.fit_price_curve(_synthetic(noise_sd=0.0), ref_year=REF_YEAR)


def _priced(ad_id, age, mileage, k, *, active, dom):
    """A record priced k x the noise-free curve value (residual = ln k)."""
    return rec(
        ad_id=ad_id, year=REF_YEAR - age, mileage_km=mileage,
        price=k * _price(age, mileage), is_active=active, days_on_market=dom,
    )


def test_survivorship_factor_below_one_when_fast_sellers_underpriced():
    curve = _fit_noisefree_curve()
    records = []
    # Fast-delisted cars priced 10% below the curve (residual ln 0.9).
    for i in range(5):
        records.append(_priced(100 + i, age=3 + i, mileage=40000 + 5000 * i, k=0.9,
                               active=False, dom=10))
    # Still-active cars priced 10% above the curve (residual ln 1.1).
    for i in range(5):
        records.append(_priced(200 + i, age=3 + i, mileage=40000 + 5000 * i, k=1.1,
                               active=True, dom=None))
    signal = analysis.survivorship_adjustment(records, curve, ref_year=REF_YEAR)
    assert signal.n_fast == 5 and signal.n_linger == 5
    assert signal.factor == pytest.approx(math.exp(math.log(0.9) - math.log(1.1)), rel=1e-6)
    assert signal.factor < 1.0


def test_survivorship_none_when_a_group_is_thin():
    curve = _fit_noisefree_curve()
    records = [_priced(1, 3, 40000, 0.9, active=False, dom=10)]  # one fast, no lingering
    signal = analysis.survivorship_adjustment(records, curve, ref_year=REF_YEAR)
    assert signal.factor is None


# --- Layer 2: combined factor -----------------------------------------------

def test_sale_adjustment_default_when_no_signals():
    assert analysis.sale_adjustment_factor() == analysis.DEFAULT_SALE_ADJUSTMENT


def test_sale_adjustment_default_when_cut_signal_too_thin():
    thin = analysis.PriceCutSignal(median_cut=0.05, n=2)  # < MIN_CUT_TRAJECTORIES
    assert analysis.sale_adjustment_factor(price_cut=thin) == analysis.DEFAULT_SALE_ADJUSTMENT


def test_sale_adjustment_combines_cut_and_survivorship():
    cut = analysis.PriceCutSignal(median_cut=0.05, n=8)
    surv = analysis.SurvivorshipSignal(factor=0.9, n_fast=5, n_linger=5)
    # (1 - 0.05) * 0.9 = 0.855
    assert analysis.sale_adjustment_factor(cut, surv) == pytest.approx(0.855)


def test_sale_adjustment_clamps_to_floor():
    cut = analysis.PriceCutSignal(median_cut=0.6, n=10)
    surv = analysis.SurvivorshipSignal(factor=0.5, n_fast=5, n_linger=5)
    assert analysis.sale_adjustment_factor(cut, surv) == 0.5


# --- top-level query: estimate_sale_price -----------------------------------

def _scoped_pool(n=40, noise_sd=0.0, seed=7):
    """A noise-tunable Mazda CX-5 active pool drawn from the known model."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        age = int(rng.integers(0, 12))
        mileage = int(rng.integers(10_000, 160_000))
        noise = float(rng.normal(0, noise_sd)) if noise_sd else 0.0
        out.append(rec(
            ad_id=i, year=REF_YEAR - age, mileage_km=mileage,
            price=_price(age, mileage, noise), make="Mazda", model="CX-5",
        ))
    return out


def test_estimate_sale_price_defaults_adjustment_without_history():
    records = _scoped_pool(noise_sd=0.12)
    est = analysis.estimate_sale_price(
        records, "Mazda", "CX-5",
        year_range=(2018, 2022), mileage_range=(40_000, 80_000), ref_year=REF_YEAR,
    )
    assert est.n == 40
    assert est.adjustment_factor == analysis.DEFAULT_SALE_ADJUSTMENT  # no signals
    assert est.sale_price == pytest.approx(est.asking_estimate * 0.92, rel=1e-9)
    assert est.range_low < est.sale_price < est.range_high
    assert est.confidence in {"low", "medium", "high"}


def test_estimate_sale_price_asking_matches_curve_at_midpoint():
    records = _scoped_pool(noise_sd=0.0)
    est = analysis.estimate_sale_price(
        records, "Mazda", "CX-5",
        year_range=(2018, 2022), mileage_range=(40_000, 80_000), ref_year=REF_YEAR,
    )
    # midpoint = age 5, 60k km; noise-free curve is exact there.
    assert est.asking_estimate == pytest.approx(_price(5, 60_000), rel=1e-6)


def test_estimate_sale_price_uses_history_and_delisting():
    records = _scoped_pool(n=40, noise_sd=0.0)
    # Five delisted CX-5s at the query point, priced under the curve.
    for i, dom in enumerate([10, 20, 30, 40, 50]):
        records.append(rec(
            ad_id=1000 + i, year=2020, mileage_km=60_000,
            price=0.9 * _price(5, 60_000), make="Mazda", model="CX-5",
            is_active=False, days_on_market=dom,
        ))
    histories = [[20_000, 19_000], [16_000, 15_000], [12_000, 11_500],
                 [30_000, 28_000], [10_000, 9_800]]  # 5 trajectories -> cut signal active
    est = analysis.estimate_sale_price(
        records, "Mazda", "CX-5",
        year_range=(2018, 2022), mileage_range=(40_000, 80_000),
        histories=histories, ref_year=REF_YEAR,
    )
    assert est.expected_days_on_market == 30           # median of [10,20,30,40,50]
    assert est.adjustment_factor < analysis.DEFAULT_SALE_ADJUSTMENT  # data pulled it down
    assert est.sale_price < est.asking_estimate


def test_estimate_sale_price_prices_the_requested_fuel():
    # Same pool, but diesels carry a premium the dummy should pick up.
    records = _scoped_pool(n=40, noise_sd=0.0)
    for i, r in enumerate(records):
        fuel = "Diesel" if i % 2 else "Petrol"
        bump = math.exp(0.2) if fuel == "Diesel" else 1.0
        records[i] = rec(
            ad_id=r.ad_id, year=r.year, mileage_km=r.mileage_km, price=r.price * bump,
            make="Mazda", model="CX-5", fuel_type=fuel,
        )

    def est(fuel):
        return analysis.estimate_sale_price(
            records, "Mazda", "CX-5", year_range=(2018, 2022),
            mileage_range=(40_000, 80_000), fuel_type=fuel, ref_year=REF_YEAR,
        )

    petrol, diesel = est("petrol"), est("Diesel")
    # Fit uses the whole pool either way; only the fuel dummy moves the estimate.
    assert petrol.n == diesel.n == 40
    assert diesel.asking_estimate == pytest.approx(petrol.asking_estimate * math.exp(0.2), rel=1e-6)
    # The comparables cross-check is restricted to the queried fuel.
    assert diesel.comparables_median > petrol.comparables_median


def test_estimate_sale_price_unknown_fuel_falls_back_to_baseline():
    records = _scoped_pool(n=40, noise_sd=0.0)
    baseline = analysis.estimate_sale_price(
        records, "Mazda", "CX-5", year_range=(2018, 2022),
        mileage_range=(40_000, 80_000), ref_year=REF_YEAR,
    )
    hydrogen = analysis.estimate_sale_price(
        records, "Mazda", "CX-5", year_range=(2018, 2022),
        mileage_range=(40_000, 80_000), fuel_type="Hydrogen", ref_year=REF_YEAR,
    )
    assert hydrogen.asking_estimate == pytest.approx(baseline.asking_estimate, rel=1e-9)
    # ...but nothing comparable exists, so the query is graded down.
    assert hydrogen.comparables_median is None
    assert hydrogen.confidence == "none"


def test_estimate_sale_price_none_when_no_matching_data():
    est = analysis.estimate_sale_price(
        [rec(ad_id=1, make="Toyota", model="Yaris")], "Mazda", "CX-5", ref_year=REF_YEAR,
    )
    assert est.sale_price is None
    assert est.n == 0
    assert est.confidence == "none"


# --- DB-backed wrapper ------------------------------------------------------

def test_estimate_from_db_reads_listings_and_histories(tmp_path, monkeypatch):
    from sqlmodel import create_engine
    from bazaraki import db

    engine = create_engine(f"sqlite:///{tmp_path / 'analysis.db'}")
    monkeypatch.setattr(db, "_engine", engine)
    db.init_db()

    # Enough CX-5 rows (>= MIN_FIT) spanning age/mileage for a fit.
    for i in range(10):
        age, mileage = i, 20_000 + 12_000 * i
        db.upsert_listing({
            "ad_id": i, "title": f"Mazda CX-5 {i}", "url": f"/adv/{i}/",
            "make": "Mazda", "model": "CX-5",
            "year": REF_YEAR - age, "mileage_km": mileage,
            "price": _price(age, mileage),
        })

    est = analysis.estimate_from_db(
        "Mazda", "CX-5", year_range=(2018, 2022), mileage_range=(40_000, 80_000)
    )
    assert est.n == 10
    assert est.asking_estimate is not None
    assert est.sale_price == pytest.approx(est.asking_estimate * est.adjustment_factor, rel=1e-9)


def test_estimate_from_db_drops_in_transit_adverts(tmp_path, monkeypatch):
    from sqlmodel import create_engine
    from bazaraki import db

    engine = create_engine(f"sqlite:///{tmp_path / 'transit.db'}")
    monkeypatch.setattr(db, "_engine", engine)
    db.init_db()

    for i in range(10):
        age, mileage = i, 20_000 + 12_000 * i
        db.upsert_listing({
            "ad_id": i, "title": f"Mazda CX-5 {i}", "url": f"/adv/{i}/",
            "make": "Mazda", "model": "CX-5", "availability": "In stock",
            "year": REF_YEAR - age, "mileage_km": mileage,
            "price": _price(age, mileage),
        })
    # An in-transit import quote at triple the market price: excluded, so the
    # fit is unchanged from the ten in-stock rows.
    db.upsert_listing({
        "ad_id": 99, "title": "Mazda CX-5 import", "url": "/adv/99/",
        "make": "Mazda", "model": "CX-5", "availability": "In transit",
        "year": REF_YEAR - 2, "mileage_km": 44_000, "price": 3 * _price(2, 44_000),
    })

    est = analysis.estimate_from_db(
        "Mazda", "CX-5", year_range=(2018, 2022), mileage_range=(40_000, 80_000)
    )
    assert est.n == 10


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


def test_comparables_restricted_to_one_fuel():
    records = [
        rec(ad_id=i, year=2020, mileage_km=50000, price=p, fuel_type="Petrol")
        for i, p in enumerate([18000, 20000, 22000])
    ] + [
        rec(ad_id=10 + i, year=2020, mileage_km=50000, price=p, fuel_type="Diesel")
        for i, p in enumerate([30000, 32000, 34000])
    ]
    petrol = analysis.comparables(records, year=2020, mileage=50000, fuel_type="petrol")
    assert petrol.n == 3 and petrol.estimate == 20000.0  # case-insensitive match
    diesel = analysis.comparables(records, year=2020, mileage=50000, fuel_type="Diesel")
    assert diesel.n == 3 and diesel.estimate == 32000.0
    assert analysis.comparables(records, year=2020, mileage=50000).n == 6


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
        availability = "In stock"
        is_active = True
        days_on_market = 42

    (r,) = analysis.to_records([FakeListing()])
    assert r == CarRecord(
        ad_id=7, price=15000.0, year=2019, mileage_km=80000,
        make="Mazda", model="CX-5", fuel_type="Petrol", gearbox="Automatic",
        seller_type="dealer", availability="In stock", is_active=True,
        days_on_market=42,
    )
