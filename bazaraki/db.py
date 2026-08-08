"""SQLite storage layer for car listings.

Beyond upserting listings, this layer records the two signals that turn *asking*
prices into a realistic *sale* price: an advert's price trajectory
(``PriceObservation``) and its lifecycle — when a completed scrape stops finding
an advert it is marked delisted (a sold-proxy). See ``PRICING_PLAN.md``.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

from .models import CarListing, PriceObservation, ScrapeRun

DB_PATH = Path(__file__).parent.parent / "bazaraki.db"
_engine = create_engine(f"sqlite:///{DB_PATH}")

# Columns added after the first schema; SQLite create_all won't add these to an
# existing carlisting table, so init_db backfills them in place (idempotent).
_ADDED_COLUMNS = {
    "seller_type": "ALTER TABLE carlisting ADD COLUMN seller_type VARCHAR",
    "is_active": "ALTER TABLE carlisting ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1",
    "delisted_at": "ALTER TABLE carlisting ADD COLUMN delisted_at DATETIME",
}


def _ensure_columns() -> None:
    """Add lifecycle columns to a pre-existing carlisting table if missing."""
    existing = {c["name"] for c in inspect(_engine).get_columns("carlisting")}
    with _engine.begin() as conn:
        for column, stmt in _ADDED_COLUMNS.items():
            if column not in existing:
                conn.execute(text(stmt))


def init_db() -> None:
    SQLModel.metadata.create_all(_engine)  # new tables + fresh carlisting
    _ensure_columns()                       # heal an old carlisting in place


def _normalise(value: str | None) -> str | None:
    """Fold a make/model to a comparable key (filter slugs vs. display names).

    Filters carry URL slugs ("mazda", "cx-30") while listings store display
    names ("Mazda", "CX-30"); stripping case and non-alphanumerics makes the two
    comparable ("land-cruiser" == "Land Cruiser").
    """
    if value is None:
        return None
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _record_price(session: Session, ad_id: int, price: float, now: datetime) -> None:
    session.add(PriceObservation(ad_id=ad_id, observed_at=now, price=price))


def upsert_listing(data: dict) -> None:
    """Insert a listing or update only the provided (non-None) fields.

    Card and detail handlers each contribute a subset of fields; this merges
    them onto the same row keyed by ``ad_id`` without clobbering existing
    values with ``None``.

    Two side effects support the pricing analysis:
      * when ``data`` carries a price that differs from the last known one, a
        ``PriceObservation`` is appended (first sighting always logs one);
      * any sighting marks the advert active again and clears ``delisted_at``.
    """
    now = datetime.now(timezone.utc)
    ad_id = data["ad_id"]
    new_price = data.get("price")
    with Session(_engine) as session:
        existing = session.get(CarListing, ad_id)
        if existing is None:
            listing = CarListing(**data, first_seen_at=now, last_seen_at=now)
            session.add(listing)
            if new_price is not None:
                _record_price(session, ad_id, new_price, now)
        else:
            if new_price is not None and new_price != existing.price:
                _record_price(session, ad_id, new_price, now)
            for key, value in data.items():
                if value is not None:
                    setattr(existing, key, value)
            existing.last_seen_at = now
            existing.is_active = True
            existing.delisted_at = None
            session.add(existing)
        session.commit()


def start_run(filters=None) -> int:
    """Open a ``ScrapeRun`` recording the filter scope; return its ``run_id``."""
    now = datetime.now(timezone.utc)
    run = ScrapeRun(started_at=now)
    if filters is not None:
        for attr in (
            "make", "model", "year_min", "year_max",
            "price_min", "price_max", "mileage_min", "mileage_max",
        ):
            setattr(run, attr, getattr(filters, attr, None))
    with Session(_engine) as session:
        session.add(run)
        session.commit()
        return run.run_id


def _in_scope(listing: CarListing, run: ScrapeRun) -> bool:
    """True if ``listing`` falls within the run's filter scope.

    Rows the run could not have covered (different model, or a numeric field
    outside a set bound) are excluded so they are never wrongly delisted. A row
    whose field is NULL while that bound is set is treated as out of scope —
    conservative: we never delist on unconfirmed membership.
    """
    if _normalise(listing.make) != _normalise(run.make):
        return False
    if run.model is not None and _normalise(listing.model) != _normalise(run.model):
        return False
    checks = [
        (listing.year, run.year_min, run.year_max),
        (listing.price, run.price_min, run.price_max),
        (listing.mileage_km, run.mileage_min, run.mileage_max),
    ]
    for value, lo, hi in checks:
        if lo is not None and (value is None or value < lo):
            return False
        if hi is not None and (value is None or value > hi):
            return False
    return True


def finalize_run(run_id: int, seen_ad_ids: set[int], completed: bool) -> int:
    """Close a run and delist adverts it should have seen but didn't.

    Delisting only runs for a ``completed`` crawl (one that exhausted all result
    pages) with a ``make`` in scope; a truncated run leaves lifecycle untouched
    so adverts on un-fetched later pages are not mistaken for sold. Returns the
    number of adverts newly marked delisted.
    """
    now = datetime.now(timezone.utc)
    delisted = 0
    with Session(_engine) as session:
        run = session.get(ScrapeRun, run_id)
        run.finished_at = now
        run.n_seen = len(seen_ad_ids)
        run.completed = completed

        if completed and run.make is not None:
            active = session.exec(
                select(CarListing).where(CarListing.is_active == True)  # noqa: E712
            ).all()
            for listing in active:
                if listing.ad_id in seen_ad_ids:
                    continue
                if _in_scope(listing, run):
                    listing.is_active = False
                    listing.delisted_at = now
                    session.add(listing)
                    delisted += 1
        session.add(run)
        session.commit()
    return delisted


def all_listings() -> list[CarListing]:
    with Session(_engine) as session:
        return list(session.exec(select(CarListing).order_by(CarListing.ad_id)))


def count_listings() -> int:
    with Session(_engine) as session:
        return len(session.exec(select(CarListing.ad_id)).all())


def price_history(ad_id: int) -> list[PriceObservation]:
    """All price observations for an advert, oldest first."""
    with Session(_engine) as session:
        return list(
            session.exec(
                select(PriceObservation)
                .where(PriceObservation.ad_id == ad_id)
                .order_by(PriceObservation.observed_at)
            )
        )