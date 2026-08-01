"""SQLite storage layer for car listings."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

from models import CarListing

DB_PATH = Path(__file__).parent / "bazaraki.db"
_engine = create_engine(f"sqlite:///{DB_PATH}")


def init_db() -> None:
    SQLModel.metadata.create_all(_engine)


def upsert_listing(data: dict) -> None:
    """Insert a listing or update only the provided (non-None) fields.

    Card and detail handlers each contribute a subset of fields; this merges
    them onto the same row keyed by ``ad_id`` without clobbering existing
    values with ``None``.
    """
    now = datetime.now(timezone.utc)
    ad_id = data["ad_id"]
    with Session(_engine) as session:
        existing = session.get(CarListing, ad_id)
        if existing is None:
            listing = CarListing(**data, first_seen_at=now, last_seen_at=now)
            session.add(listing)
        else:
            for key, value in data.items():
                if value is not None:
                    setattr(existing, key, value)
            existing.last_seen_at = now
            session.add(existing)
        session.commit()


def all_listings() -> list[CarListing]:
    with Session(_engine) as session:
        return list(session.exec(select(CarListing).order_by(CarListing.ad_id)))


def count_listings() -> int:
    with Session(_engine) as session:
        return len(session.exec(select(CarListing.ad_id)).all())