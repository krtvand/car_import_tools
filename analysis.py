"""Pricing analysis: turn scraped *asking* prices into a realistic *sale* price.

This module is deliberately kept free of database and network dependencies: its
functions operate on plain ``CarRecord`` values (see :func:`to_records`) so they
can be unit-tested against hand-built data. Thin DB-backed wrappers that read
from :mod:`db` live at the bottom of the module. See ``PRICING_PLAN.md`` Part B.
"""
from __future__ import annotations

import re
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
