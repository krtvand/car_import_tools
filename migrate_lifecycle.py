"""One-off backfill for the price-history / lifecycle schema (Part A).

Run once after pulling the lifecycle change:

    python migrate_lifecycle.py

It (a) ensures the new columns/tables exist via ``db.init_db`` and (b) seeds a
single baseline ``PriceObservation`` for every existing listing that has a price
but no history yet, dated at ``first_seen_at`` so trajectories start from the
first time we saw the advert. Idempotent: adverts that already have an
observation are skipped, so re-running is safe.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

import db
from models import CarListing, PriceObservation


def backfill() -> int:
    """Seed baseline price observations. Returns the number inserted."""
    db.init_db()
    inserted = 0
    with Session(db._engine) as session:
        with_history = set(session.exec(select(PriceObservation.ad_id)).all())
        rows = session.exec(select(CarListing)).all()
        for row in rows:
            if row.price is None or row.ad_id in with_history:
                continue
            observed_at = row.first_seen_at or row.last_seen_at or datetime.now(timezone.utc)
            session.add(
                PriceObservation(ad_id=row.ad_id, observed_at=observed_at, price=row.price)
            )
            inserted += 1
        session.commit()
    return inserted


if __name__ == "__main__":
    n = backfill()
    print(f"Seeded baseline price observations for {n} listing(s).")