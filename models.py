"""SQLModel data model for scraped car listings."""
from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


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

    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None