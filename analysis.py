"""Pricing analysis: turn scraped *asking* prices into a realistic *sale* price.

This module is deliberately kept free of database and network dependencies: its
functions operate on plain ``CarRecord`` values (see :func:`to_records`) so they
can be unit-tested against hand-built data. Thin DB-backed wrappers that read
from :mod:`db` live at the bottom of the module. See ``PRICING_PLAN.md`` Part B.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone


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
