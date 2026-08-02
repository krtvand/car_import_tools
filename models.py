"""SQLModel data model for scraped car listings."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _to_naive_utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; normalise both sides before subtracting."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class CarListing(SQLModel, table=True):
    """A single car advertisement from bazaraki.com.

    ``ad_id`` is bazaraki's own advert id (stable across re-scrapes), used as the
    primary key so re-running the scraper upserts rather than duplicates.
    """

    ad_id: int = Field(primary_key=True)
    title: str
    make: str | None = None    # split out of ``title`` (e.g. "Mercedes-Benz")
    model: str | None = None   # split out of ``title`` (e.g. "C-Class")
    url: str
    price: float | None = None
    currency: str | None = None
    image_url: str | None = None
    photo_count: int | None = None

    # Enrichment from the advert detail page.
    location: str | None = None
    posted_raw: str | None = None  # e.g. "19.06.2026 09:56"
    year: int | None = None
    mileage_km: int | None = None
    fuel_type: str | None = None
    gearbox: str | None = None
    body_type: str | None = None
    engine_size: str | None = None
    power_hp: int | None = None
    colour: str | None = None
    doors: str | None = None
    seats: int | None = None
    drive: str | None = None
    mot_till: str | None = None
    availability: str | None = None
    extras: str | None = None
    seller_type: str | None = None  # "private" / "dealer"; reserved, not yet parsed

    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None

    # Lifecycle: a sighting sets ``is_active`` True and clears ``delisted_at``;
    # a completed scrape that no longer finds the advert flips it to delisted.
    is_active: bool = True
    delisted_at: datetime | None = None

    @property
    def days_on_market(self) -> int | None:
        """Whole days from first sighting to delisting (or now, if still live)."""
        if self.first_seen_at is None:
            return None
        end = self.delisted_at or datetime.now(timezone.utc)
        delta = _to_naive_utc(end) - _to_naive_utc(self.first_seen_at)
        return max(delta.days, 0)


class PriceObservation(SQLModel, table=True):
    """Append-only log of an advert's price over time.

    A row is written on first sighting and thereafter only when the price
    changes, so an advert's rows describe its full price trajectory (needed to
    measure price cuts on the way to a realistic sale price).
    """

    id: int | None = Field(default=None, primary_key=True)
    ad_id: int = Field(index=True, foreign_key="carlisting.ad_id")
    observed_at: datetime
    price: float


class ScrapeRun(SQLModel, table=True):
    """One invocation of the scraper.

    Records the filter scope so delisting can be applied to exactly the search
    space the run covered, and ``completed`` (did the crawl exhaust all result
    pages, vs. stop at ``max_pages``) so a truncated run never delists adverts
    that were merely on an un-fetched later page.
    """

    run_id: int | None = Field(default=None, primary_key=True)
    started_at: datetime
    finished_at: datetime | None = None
    n_seen: int | None = None
    completed: bool = False

    # Filter scope this run covered (used to bound delisting).
    make: str | None = None
    model: str | None = None
    year_min: int | None = None
    year_max: int | None = None
    price_min: int | None = None
    price_max: int | None = None
    mileage_min: int | None = None
    mileage_max: int | None = None