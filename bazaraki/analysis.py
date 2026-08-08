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
        for attr in ("fuel_type", "gearbox", "seller_type"):
            counts, kept = _kept_levels(records, attr)
            if len(kept) >= 2:
                reference = max(kept, key=lambda lvl: counts[lvl])
                others = sorted(lvl for lvl in kept if lvl != reference)
                cats[attr] = (reference, others)
    return _Spec(ref_year=ref_year, categoricals=cats)


def _row(spec: _Spec, age: int, mileage_km: int, fuel_type, gearbox, seller_type) -> list[float]:
    row = [1.0, float(age), mileage_km / _MILEAGE_UNIT]
    values = {"fuel_type": fuel_type, "gearbox": gearbox, "seller_type": seller_type}
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
        _row(spec, car_age(r.year, ref_year), r.mileage_km, r.fuel_type, r.gearbox, r.seller_type)
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
    seller_type: str | None = None,
    alpha: float = 0.05,
) -> PricePrediction:
    """Predict asking price at ``(year, mileage)`` with a ``1 - alpha`` interval.

    The interval is computed on the log scale (``s * sqrt(1 + leverage)`` times a
    Student-t critical value) then exponentiated, so it is asymmetric in price
    space — wider above than below, as depreciation curves are. When the fit has
    no residual degrees of freedom the interval is ``None``.
    """
    x0 = np.array(_row(curve.spec, car_age(year, curve.spec.ref_year), mileage, fuel_type, gearbox, seller_type))
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


# ---------------------------------------------------------------------------
# Layer 2: discount from asking to realistic sale price
# ---------------------------------------------------------------------------
#
# The hedonic curve models *asking* prices, but a bid decision needs the *sale*
# price. Two signals close that gap, both refined as history accrues:
#   * price cuts — how far adverts drop from first to last asking before they go;
#   * survivorship — still-listed cars are biased high (overpriced cars linger,
#     good deals vanish), so what actually sells looks cheaper than the pool.
# Until enough history exists, sale_adjustment_factor falls back to a default.

DEFAULT_SALE_ADJUSTMENT = 0.92   # ~8% asking->sale haircut before data replaces it
FAST_DAYS = 30                   # "sold quickly" threshold for the survivorship split
MIN_GROUP = 3                    # per-group minimum for a usable survivorship signal
MIN_CUT_TRAJECTORIES = 5         # price-cut median needs at least this many adverts


@dataclass(frozen=True)
class PriceCutSignal:
    median_cut: float | None   # typical first->last drop as a fraction, e.g. 0.05
    n: int                     # adverts with >= 2 price observations


@dataclass(frozen=True)
class SurvivorshipSignal:
    factor: float | None       # multiply an asking-curve estimate by this (<1 = pool biased high)
    n_fast: int
    n_linger: int


def price_cut_factor(histories) -> PriceCutSignal:
    """Median fractional drop between an advert's first and last observed price.

    ``histories`` is an iterable of price trajectories, each a chronological
    sequence of prices (oldest first). Trajectories with fewer than two
    observations are skipped; a flat trajectory contributes a 0% cut. A positive
    result means prices typically fall on the way to delisting.
    """
    cuts = []
    for prices in histories:
        prices = list(prices)
        if len(prices) < 2 or prices[0] <= 0:
            continue
        cuts.append((prices[0] - prices[-1]) / prices[0])
    if not cuts:
        return PriceCutSignal(None, 0)
    return PriceCutSignal(float(statistics.median(cuts)), len(cuts))


def _log_residual(curve: PriceCurve, r: CarRecord) -> float:
    """How over/under-priced ``r`` is vs the pooled curve, on the log scale."""
    pred = predict(curve, r.year, r.mileage_km, r.fuel_type, r.gearbox, r.seller_type)
    return math.log(r.price) - math.log(pred.estimate)


def survivorship_adjustment(
    records,
    curve: PriceCurve,
    fast_days: int = FAST_DAYS,
    ref_year: int | None = None,
) -> SurvivorshipSignal:
    """Compare how (under)priced fast-selling adverts are vs still-active ones.

    Using residuals from the pooled asking-curve controls for age/mileage, so the
    comparison isn't confounded by the two groups holding different cars. The
    returned ``factor = exp(median_resid_fast - median_resid_linger)`` is below 1
    when cars that sold quickly were priced under what's still on the market —
    i.e. the live asking pool sits above realistic sale prices. ``None`` when
    either group is thinner than :data:`MIN_GROUP`.
    """
    if ref_year is None:
        ref_year = _current_year()
    fast, linger = [], []
    for r in clean(records, ref_year):
        e = _log_residual(curve, r)
        if not r.is_active and r.days_on_market is not None and r.days_on_market <= fast_days:
            fast.append(e)
        elif r.is_active:
            linger.append(e)
    if len(fast) < MIN_GROUP or len(linger) < MIN_GROUP:
        return SurvivorshipSignal(None, len(fast), len(linger))
    factor = math.exp(statistics.median(fast) - statistics.median(linger))
    return SurvivorshipSignal(float(factor), len(fast), len(linger))


def sale_adjustment_factor(
    price_cut: PriceCutSignal | None = None,
    survivorship: SurvivorshipSignal | None = None,
    default: float = DEFAULT_SALE_ADJUSTMENT,
) -> float:
    """Combine the Layer-2 signals into one asking->sale multiplier.

    Applies the price-cut haircut and the survivorship factor multiplicatively
    when each is backed by enough data, and clamps the result to ``[0.5, 1.0]``.
    With neither signal available it returns ``default``. The combination is
    intentionally simple and provisional — Part D calibrates it once weeks of
    delisting data have accrued.
    """
    factor = 1.0
    used = False
    if (
        price_cut is not None
        and price_cut.median_cut is not None
        and price_cut.n >= MIN_CUT_TRAJECTORIES
    ):
        factor *= 1.0 - price_cut.median_cut
        used = True
    if survivorship is not None and survivorship.factor is not None:
        factor *= survivorship.factor
        used = True
    if not used:
        return default
    return float(min(max(factor, 0.5), 1.0))


# ---------------------------------------------------------------------------
# Top-level query: (make, model, year range, mileage range) -> sale estimate
# ---------------------------------------------------------------------------

_CONFIDENCE_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}
_RANK_CONFIDENCE = {v: k for k, v in _CONFIDENCE_RANK.items()}


@dataclass(frozen=True)
class SalePriceEstimate:
    """The actionable answer to a bid query.

    ``sale_price`` is the asking-curve estimate discounted by the Layer-2
    adjustment; ``range_low``/``range_high`` are the P25-P75 predictive band on
    the same discounted scale. ``expected_days_on_market`` is the median time
    comparable adverts took to delist. ``n`` is the fit sample size.
    """

    sale_price: float | None
    asking_estimate: float | None
    range_low: float | None
    range_high: float | None
    expected_days_on_market: int | None
    n: int
    confidence: str
    adjustment_factor: float
    comparables_median: float | None


def _midpoint(rng: tuple[float, float]) -> float:
    lo, hi = rng
    return (lo + hi) / 2.0


def _resolve_range(rng, values) -> tuple[float, float] | None:
    if rng is not None:
        return rng
    vals = [v for v in values if v is not None]
    return (min(vals), max(vals)) if vals else None


def _fit_confidence(n: int) -> str:
    if n >= 40:
        return "high"
    if n >= 20:
        return "medium"
    if n >= MIN_FIT:
        return "low"
    return "none"


def _weaker(a: str, b: str) -> str:
    return _RANK_CONFIDENCE[min(_CONFIDENCE_RANK[a], _CONFIDENCE_RANK[b])]


def _expected_days_on_market(records, year, mileage, year_tol, mileage_band) -> int | None:
    """Median days-on-market of comparable *delisted* adverts near the query.

    Delisted rows are used because an active advert's days-on-market is
    right-censored (the clock is still running). Approximates DOM at the query
    year/mileage, not strictly conditioned on the estimated price.
    """
    doms = [
        r.days_on_market
        for r in records
        if not r.is_active and r.days_on_market is not None
        and r.year is not None and r.mileage_km is not None
        and abs(r.year - year) <= year_tol
        and abs(r.mileage_km - mileage) <= mileage_band
    ]
    if len(doms) < MIN_GROUP:
        return None
    return int(statistics.median(doms))


def estimate_sale_price(
    records,
    make: str,
    model: str | None = None,
    year_range: tuple[int, int] | None = None,
    mileage_range: tuple[int, int] | None = None,
    histories=None,
    ref_year: int | None = None,
) -> SalePriceEstimate:
    """Estimate a realistic sale price for a make/model at a year/mileage query.

    Combines the hedonic curve (Layer 1) with the asking->sale adjustment
    (Layer 2), cross-checks against comparables, and reports an expected
    days-on-market and a confidence grade. ``year_range``/``mileage_range``
    default to the span of the matched data when omitted; the estimate is taken
    at their midpoint. ``histories`` (optional trajectories for the scoped
    adverts) powers the price-cut signal.
    """
    if ref_year is None:
        ref_year = _current_year()
    scoped = clean(filter_model(records, make, model), ref_year)

    price_cut = price_cut_factor(histories) if histories is not None else PriceCutSignal(None, 0)

    curve = fit_price_curve(scoped, ref_year)
    if curve is None:
        adjustment = sale_adjustment_factor(price_cut, None)
        return SalePriceEstimate(
            sale_price=None, asking_estimate=None, range_low=None, range_high=None,
            expected_days_on_market=None, n=len(scoped), confidence="none",
            adjustment_factor=adjustment, comparables_median=None,
        )

    yr = _resolve_range(year_range, [r.year for r in scoped])
    mr = _resolve_range(mileage_range, [r.mileage_km for r in scoped])
    q_year, q_mileage = _midpoint(yr), _midpoint(mr)

    # P25-P75 predictive band (alpha=0.5) plus the point estimate.
    band = predict(curve, q_year, q_mileage, alpha=0.5)
    comp = comparables(scoped, int(round(q_year)), int(round(q_mileage)))
    survivorship = survivorship_adjustment(scoped, curve, ref_year=ref_year)
    adjustment = sale_adjustment_factor(price_cut, survivorship)

    asking = band.estimate
    sale_price = asking * adjustment
    range_low = band.lo * adjustment if band.lo is not None else None
    range_high = band.hi * adjustment if band.hi is not None else None

    year_tol = comp.year_tol
    mileage_band = comp.mileage_band
    dom = _expected_days_on_market(scoped, q_year, q_mileage, year_tol, mileage_band)

    confidence = _weaker(_fit_confidence(curve.n), comp.confidence)
    return SalePriceEstimate(
        sale_price=float(sale_price),
        asking_estimate=float(asking),
        range_low=range_low,
        range_high=range_high,
        expected_days_on_market=dom,
        n=curve.n,
        confidence=confidence,
        adjustment_factor=adjustment,
        comparables_median=comp.estimate,
    )


def estimate_from_db(
    make: str,
    model: str | None = None,
    year_range: tuple[int, int] | None = None,
    mileage_range: tuple[int, int] | None = None,
) -> SalePriceEstimate:
    """DB-backed :func:`estimate_sale_price`: reads listings and price histories."""
    from . import db

    listings = db.all_listings()
    records = to_records(listings)
    scoped = filter_model(records, make, model)
    histories = [[o.price for o in db.price_history(r.ad_id)] for r in scoped]
    return estimate_sale_price(
        records, make, model, year_range, mileage_range, histories=histories
    )
