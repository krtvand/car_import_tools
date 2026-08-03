"""Pricing analysis: turn scraped *asking* prices into a realistic *sale* price.

This module is deliberately kept free of database and network dependencies: its
functions operate on plain ``CarRecord`` values (see :func:`to_records`) so they
can be unit-tested against hand-built data. Thin DB-backed wrappers that read
from :mod:`db` live at the bottom of the module. See ``PRICING_PLAN.md`` Part B.
"""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
from scipy import stats


@dataclass(frozen=True)
class CarRecord:
    """The subset of a listing the pricing model needs.

    Decoupled from :class:`models.CarListing` so analysis functions can be
    exercised with synthetic data and never touch the database.
    """

    ad_id: int
    price: float | None
    year: int | None
    mileage_km: int | None
    make: str | None = None
    model: str | None = None
    fuel_type: str | None = None
    gearbox: str | None = None
    seller_type: str | None = None
    is_active: bool = True
    days_on_market: int | None = None


def to_records(listings) -> list[CarRecord]:
    """Project ``CarListing`` rows (or any duck-typed equivalents) to records."""
    return [
        CarRecord(
            ad_id=l.ad_id,
            price=l.price,
            year=l.year,
            mileage_km=l.mileage_km,
            make=l.make,
            model=l.model,
            fuel_type=l.fuel_type,
            gearbox=l.gearbox,
            seller_type=l.seller_type,
            is_active=l.is_active,
            days_on_market=l.days_on_market,
        )
        for l in listings
    ]


def _normalise(value: str | None) -> str | None:
    """Case/punctuation-insensitive key for make/model matching.

    Mirrors :func:`db._normalise` so a filter slug ("cx-30") and a display name
    ("CX-30") compare equal without importing the DB layer into this pure module.
    """
    if value is None:
        return None
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _current_year() -> int:
    return datetime.now(timezone.utc).year


def car_age(year: int, ref_year: int | None = None) -> int:
    """Age in whole years, floored at 0 (a next-model-year car reads as new)."""
    if ref_year is None:
        ref_year = _current_year()
    return max(ref_year - year, 0)


def filter_model(records, make: str, model: str | None = None) -> list[CarRecord]:
    """Keep records matching ``make`` (and ``model`` when given), slug-insensitive."""
    make_key = _normalise(make)
    model_key = _normalise(model)
    out = []
    for r in records:
        if _normalise(r.make) != make_key:
            continue
        if model_key is not None and _normalise(r.model) != model_key:
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Outlier hygiene
# ---------------------------------------------------------------------------

# Physical plausibility bounds; anything outside is a typo/damaged/junk listing
# that would distort the fit. Deliberately wide — we drop impossibilities, not
# merely-unusual cars.
MIN_PRICE = 100.0            # below this is a parts/deposit listing, not a car
MAX_PRICE = 1_000_000.0
MAX_MILEAGE_KM = 1_000_000   # ~600k mi; beyond this is a data error
MIN_YEAR = 1950


def is_usable(record: CarRecord, ref_year: int | None = None) -> bool:
    """True if the record has the fields the model needs and they're plausible."""
    if ref_year is None:
        ref_year = _current_year()
    if record.price is None or record.year is None or record.mileage_km is None:
        return False
    if not (MIN_PRICE <= record.price <= MAX_PRICE):
        return False
    if not (0 <= record.mileage_km <= MAX_MILEAGE_KM):
        return False
    # A model year up to next calendar year is normal; anything further is bogus.
    if not (MIN_YEAR <= record.year <= ref_year + 1):
        return False
    return True


def clean(records, ref_year: int | None = None) -> list[CarRecord]:
    """Drop records missing required fields or failing plausibility checks."""
    return [r for r in records if is_usable(r, ref_year)]


# ---------------------------------------------------------------------------
# Layer 1 cross-check: comparables median
# ---------------------------------------------------------------------------

# A median needs a few points to be meaningful; below this we widen the search
# band (and lower confidence). Above HIGH_CONFIDENCE_N an exact-band match is
# trustworthy on its own.
MIN_COMPARABLES = 5
HIGH_CONFIDENCE_N = 12


@dataclass(frozen=True)
class Comparables:
    """Result of a comparables lookup.

    ``estimate`` is the median asking price of the matched cars, or ``None`` when
    nothing matched even after widening. ``confidence`` is one of
    ``"high" | "medium" | "low" | "none"``; ``widened`` counts how many times the
    band had to be enlarged to reach :data:`MIN_COMPARABLES`.
    """

    estimate: float | None
    n: int
    confidence: str
    year_tol: int
    mileage_band: int
    widened: int


def comparables(
    records,
    year: int,
    mileage: int,
    year_tol: int = 1,
    mileage_band: int = 15_000,
    max_widen: int = 2,
) -> Comparables:
    """Median asking price of cars near ``(year, mileage)``.

    Matches records within ``±year_tol`` years and ``±mileage_band`` km. When
    fewer than :data:`MIN_COMPARABLES` match, the band is widened (year_tol +1,
    mileage_band doubled) up to ``max_widen`` times and confidence is lowered —
    robust to thin data without silently reporting a one-listing "median".

    ``records`` are expected to be already scoped to the model of interest and
    cleaned (see :func:`filter_model`, :func:`clean`); records lacking price,
    year or mileage are ignored defensively.
    """
    yt, mb, widened = year_tol, mileage_band, 0
    while True:
        matched = [
            r for r in records
            if r.price is not None and r.year is not None and r.mileage_km is not None
            and abs(r.year - year) <= yt
            and abs(r.mileage_km - mileage) <= mb
        ]
        if len(matched) >= MIN_COMPARABLES or widened >= max_widen:
            break
        widened += 1
        yt += 1
        mb *= 2

    n = len(matched)
    if n == 0:
        return Comparables(None, 0, "none", yt, mb, widened)

    estimate = float(statistics.median(r.price for r in matched))
    if widened > 0 or n < MIN_COMPARABLES:
        confidence = "low"
    elif n >= HIGH_CONFIDENCE_N:
        confidence = "high"
    else:
        confidence = "medium"
    return Comparables(estimate, n, confidence, yt, mb, widened)


# ---------------------------------------------------------------------------
# Layer 1 primary: hedonic log-linear regression
# ---------------------------------------------------------------------------

# log(price) depreciates roughly linearly in age and mileage, so a log-linear
# fit gives a smooth estimate *and* a prediction interval for any (year, mileage)
# — even where no exact comparable exists.
MIN_FIT = 8              # too few rows below this to fit anything meaningful
MIN_FOR_DUMMIES = 20     # only spend degrees of freedom on categoricals when N allows
MIN_LEVEL_COUNT = 5      # ignore categorical levels thinner than this
_MILEAGE_UNIT = 10_000   # model mileage in 10k-km units for interpretable coeffs


@dataclass(frozen=True)
class _Spec:
    """Design-matrix layout shared by fit and predict.

    ``categoricals`` maps an attribute to ``(reference_level, other_levels)``;
    each *other* level contributes one dummy column (the reference and any
    unseen/None level map to all-zeros = baseline). Column order is fixed:
    intercept, age, mileage, then the categorical dummies in insertion order.
    """

    ref_year: int
    categoricals: dict[str, tuple[str, list[str]]] = field(default_factory=dict)


@dataclass(eq=False)
class PriceCurve:
    """A fitted asking-price curve for one make/model."""

    beta: np.ndarray
    spec: _Spec
    n: int
    df: int          # residual degrees of freedom (n - params)
    s2: float        # residual variance on the log scale
    xtx_inv: np.ndarray


@dataclass(frozen=True)
class PricePrediction:
    """A point estimate with an (asymmetric, price-scale) prediction interval."""

    estimate: float
    lo: float | None
    hi: float | None
    alpha: float
    n: int
    df: int


def _kept_levels(records, attr: str) -> tuple[Counter, list[str]]:
    counts = Counter(getattr(r, attr) for r in records if getattr(r, attr) is not None)
    kept = [lvl for lvl, c in counts.items() if c >= MIN_LEVEL_COUNT]
    return counts, kept


def _build_spec(records, ref_year: int) -> _Spec:
    cats: dict[str, tuple[str, list[str]]] = {}
    if len(records) >= MIN_FOR_DUMMIES:
        for attr in ("fuel_type", "gearbox"):
            counts, kept = _kept_levels(records, attr)
            if len(kept) >= 2:
                reference = max(kept, key=lambda lvl: counts[lvl])
                others = sorted(lvl for lvl in kept if lvl != reference)
                cats[attr] = (reference, others)
    return _Spec(ref_year=ref_year, categoricals=cats)


def _row(spec: _Spec, age: int, mileage_km: int, fuel_type, gearbox) -> list[float]:
    row = [1.0, float(age), mileage_km / _MILEAGE_UNIT]
    values = {"fuel_type": fuel_type, "gearbox": gearbox}
    for attr, (_reference, others) in spec.categoricals.items():
        val = values[attr]
        row.extend(1.0 if val == lvl else 0.0 for lvl in others)
    return row


def fit_price_curve(records, ref_year: int | None = None) -> PriceCurve | None:
    """Fit ``log(price) ~ age + mileage (+ fuel/gearbox dummies)`` via OLS.

    Records are cleaned first (see :func:`clean`). Returns ``None`` when fewer
    than :data:`MIN_FIT` usable rows remain. Categorical dummies are only added
    when the sample is large enough to afford the degrees of freedom.
    """
    if ref_year is None:
        ref_year = _current_year()
    rows = clean(records, ref_year)
    if len(rows) < MIN_FIT:
        return None

    spec = _build_spec(rows, ref_year)
    X = np.array([
        _row(spec, car_age(r.year, ref_year), r.mileage_km, r.fuel_type, r.gearbox)
        for r in rows
    ])
    y = np.log(np.array([r.price for r in rows], dtype=float))

    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    n, p = X.shape
    df = n - p
    s2 = float(resid @ resid / df) if df > 0 else float("nan")
    xtx_inv = np.linalg.pinv(X.T @ X)  # pinv tolerates collinear columns
    return PriceCurve(beta=beta, spec=spec, n=n, df=df, s2=s2, xtx_inv=xtx_inv)


def predict(
    curve: PriceCurve,
    year: int,
    mileage: int,
    fuel_type: str | None = None,
    gearbox: str | None = None,
    alpha: float = 0.05,
) -> PricePrediction:
    """Predict asking price at ``(year, mileage)`` with a ``1 - alpha`` interval.

    The interval is computed on the log scale (``s * sqrt(1 + leverage)`` times a
    Student-t critical value) then exponentiated, so it is asymmetric in price
    space — wider above than below, as depreciation curves are. When the fit has
    no residual degrees of freedom the interval is ``None``.
    """
    x0 = np.array(_row(curve.spec, car_age(year, curve.spec.ref_year), mileage, fuel_type, gearbox))
    mean_log = float(x0 @ curve.beta)
    estimate = float(np.exp(mean_log))

    lo = hi = None
    if curve.df > 0 and math.isfinite(curve.s2):
        leverage = float(x0 @ curve.xtx_inv @ x0)
        se_pred = math.sqrt(curve.s2 * (1.0 + leverage))
        tcrit = float(stats.t.ppf(1.0 - alpha / 2.0, curve.df))
        lo = float(np.exp(mean_log - tcrit * se_pred))
        hi = float(np.exp(mean_log + tcrit * se_pred))
    return PricePrediction(estimate=estimate, lo=lo, hi=hi, alpha=alpha, n=curve.n, df=curve.df)
