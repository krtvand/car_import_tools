"""SQLite storage for auction lots.

Separate database from bazaraki's: different market, different lifecycle,
nothing joins across them. Same shape of module, though — a module-level engine
that tests point at a temp file.

The one non-obvious rule is in :func:`upsert_lot`: a re-normalise must be able
to run at any time, over any saved run, without undoing what the paid extraction
step already achieved. See the ``sheet_status`` handling there.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

from .models import AuctionLot, BidRecord, SheetExtraction  # noqa: F401 — registers tables

DB_PATH = Path(__file__).parent.parent / "auction.db"
_engine = create_engine(f"sqlite:///{DB_PATH}")


def _ensure_columns() -> None:
    """Add columns the models gained since the tables were created.

    ``create_all`` creates missing *tables* but never alters an existing one, so
    a field added to a model is invisible to a database created before it — and
    every read of that table then fails on the missing column. Rather than
    listing each addition by hand (as :mod:`bazaraki.db` does), this diffs the
    models against the tables, so adding a column is just adding a column.

    Only nullable columns can be healed this way: SQLite cannot add a NOT NULL
    column to a populated table without a default, so one of those still needs a
    deliberate migration. That is loud rather than silent — the column stays
    missing and the next query says so.
    """
    inspector = inspect(_engine)
    with _engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing or not column.nullable:
                    continue
                kind = column.type.compile(_engine.dialect)
                conn.execute(
                    text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {kind}")
                )


def init_db() -> None:
    SQLModel.metadata.create_all(_engine)  # new tables
    _ensure_columns()                      # new columns on old tables


def _keep_extraction(existing: AuctionLot, incoming: dict) -> bool:
    """Would overwriting ``sheet_status`` throw away a completed extraction?

    Normalising always proposes ``"pending"``, because all it knows is that an
    image is on disk. If Phase 3 has already read that exact image — same hash —
    the row is ``"extracted"`` and must stay that way, or every re-normalise
    would silently queue the whole database for re-extraction at $0.02 a sheet.

    A *changed* hash is the opposite case: the sheet was re-photographed or the
    lot re-listed, the old extraction describes a different image, and going
    back to ``"pending"`` is exactly right.
    """
    if existing.sheet_status not in ("extracted", "failed"):
        return False
    return bool(existing.sheet_sha256) and existing.sheet_sha256 == incoming.get("sheet_sha256")


def upsert_lot(row: dict, session: Session, now: datetime | None = None) -> bool:
    """Insert or update one lot. Returns ``True`` if it was new.

    Updates only the fields the row actually carries a value for, so a partial
    row cannot blank out columns another pass filled in. Caller owns the
    transaction — :func:`upsert_lots` commits once for the batch.
    """
    now = now or datetime.now(timezone.utc)
    existing = session.get(AuctionLot, row["lot_number"])

    if existing is None:
        session.add(AuctionLot(**row, first_seen_at=now, last_seen_at=now))
        return True

    keep_status = _keep_extraction(existing, row)
    for key, value in row.items():
        if key == "sheet_status" and keep_status:
            continue
        if value is not None:
            setattr(existing, key, value)
    existing.last_seen_at = now
    session.add(existing)
    return False


def upsert_lots(rows: list[dict], now: datetime | None = None) -> tuple[int, int]:
    """``(inserted, updated)`` for a batch, in one transaction."""
    now = now or datetime.now(timezone.utc)
    inserted = 0
    with Session(_engine) as session:
        for row in rows:
            inserted += upsert_lot(row, session, now)
        session.commit()
    return inserted, len(rows) - inserted


def all_lots() -> list[AuctionLot]:
    with Session(_engine) as session:
        return list(session.exec(select(AuctionLot).order_by(AuctionLot.lot_number)))


def count_lots() -> int:
    with Session(_engine) as session:
        return len(session.exec(select(AuctionLot.lot_number)).all())


def lots_on(trade_date) -> list[AuctionLot]:
    """Every stored lot trading on one day, earliest slot first."""
    with Session(_engine) as session:
        return list(
            session.exec(
                select(AuctionLot)
                .where(AuctionLot.trade_date == trade_date)
                .order_by(AuctionLot.trade_time, AuctionLot.lot_number)
            )
        )


def pending_sheets() -> list[AuctionLot]:
    """Lots with a downloaded sheet that nothing has read yet — Phase 3's queue."""
    with Session(_engine) as session:
        return list(
            session.exec(
                select(AuctionLot)
                .where(AuctionLot.sheet_status == "pending")
                .where(AuctionLot.sheet_path != None)  # noqa: E711 — SQL, not Python
                .order_by(AuctionLot.lot_number)
            )
        )
