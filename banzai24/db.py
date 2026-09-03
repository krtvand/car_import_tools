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

from .models import AuctionLot, SheetExtraction  # noqa: F401 — registers tables

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


def extraction_is_current(lot_number: str, sheet_sha256: str | None) -> bool:
    """Has *this exact image* already been read by the model?

    The dedup key is the image hash rather than the lot, so a re-photographed or
    re-listed sheet is correctly treated as new work while a re-run over
    unchanged sheets costs nothing. This is the guard that makes `extract` safe
    to run repeatedly.
    """
    if not sheet_sha256:
        return False
    with Session(_engine) as session:
        existing = session.get(SheetExtraction, lot_number)
        return existing is not None and existing.sheet_sha256 == sheet_sha256


def upsert_extraction(row: dict) -> bool:
    """Write one extraction and flag its lot as read. ``True`` if it was new.

    Unlike :func:`upsert_lot` this replaces every field, including the null ones:
    a field the model no longer reports means the new read did not find it, and
    carrying the old value forward would silently blend two different readings of
    the sheet into one row.
    """
    with Session(_engine) as session:
        existing = session.get(SheetExtraction, row["lot_number"])
        if existing is None:
            session.add(SheetExtraction(**row))
            inserted = True
        else:
            for key, value in row.items():
                setattr(existing, key, value)
            session.add(existing)
            inserted = False

        if lot := session.get(AuctionLot, row["lot_number"]):
            lot.sheet_status = "extracted"
            session.add(lot)
        session.commit()
    return inserted


def set_end_price(lot_number: str, price_jpy: int) -> bool:
    """Record a revealed hammer price on a lot already stored. ``True`` if it landed.

    An update, never an insert. The price arrives separately from the lot — it
    is behind a click, and only worth clicking once the sheet has passed — so
    the obvious spelling is a partial upsert. But a partial upsert on a lot that
    is somehow *not* there inserts a row with nothing in it but a number, which
    would then fail its NOT NULL columns or, worse, not fail them. A price with
    no car under it is not a fact about anything.
    """
    with Session(_engine) as session:
        lot = session.get(AuctionLot, lot_number)
        if lot is None:
            return False
        lot.end_price_jpy = price_jpy
        session.add(lot)
        session.commit()
        return True


def mark_sheet_status(lot_number: str, status: str) -> None:
    """Record that a sheet could not be read, so a re-run can find it again."""
    with Session(_engine) as session:
        if lot := session.get(AuctionLot, lot_number):
            lot.sheet_status = status
            session.add(lot)
            session.commit()


def extraction_for(lot_number: str) -> SheetExtraction | None:
    with Session(_engine) as session:
        return session.get(SheetExtraction, lot_number)


# --- batched lookups, for the report ----------------------------------------
#
# A report over one run wants two tables for the same twenty lots. Fetching them
# per lot would be forty queries and forty sessions; these are two. The dict
# returns also make "this lot has no extraction" a plain ``.get()`` rather than a
# second round trip.


def lots_by_numbers(lot_numbers: list[str]) -> dict[str, AuctionLot]:
    if not lot_numbers:
        return {}
    with Session(_engine) as session:
        rows = session.exec(
            select(AuctionLot).where(AuctionLot.lot_number.in_(lot_numbers))
        )
        return {row.lot_number: row for row in rows}


def extractions_by_numbers(lot_numbers: list[str]) -> dict[str, SheetExtraction]:
    if not lot_numbers:
        return {}
    with Session(_engine) as session:
        rows = session.exec(
            select(SheetExtraction).where(SheetExtraction.lot_number.in_(lot_numbers))
        )
        return {row.lot_number: row for row in rows}


def all_extractions() -> list[SheetExtraction]:
    """Every sheet ever read, newest last.

    Only the glossary backfill wants these: the terms a sheet printed are worth
    translating whether or not the lot it belonged to has already traded, and a
    term learned from a car sold last month is one this morning's run does not
    pay for. See :mod:`banzai24.glossary`.
    """
    with Session(_engine) as session:
        return list(
            session.exec(select(SheetExtraction).order_by(SheetExtraction.lot_number))
        )


def stats_lots() -> list[AuctionLot]:
    """Every lot a statistics walk stored, cheapest first.

    The mirror image of :func:`_buy_side`, and the only reader that wants these.
    Ordered by price here rather than in the caller because "cheapest first" is
    what the whole table of them is for; a lot with no price yet sorts last,
    where SQLite puts NULLs.
    """
    with Session(_engine) as session:
        return list(
            session.exec(
                select(AuctionLot)
                .where(AuctionLot.discovered_by == STATS)
                .order_by(AuctionLot.end_price_jpy, AuctionLot.lot_number)
            )
        )


def all_lots() -> list[AuctionLot]:
    with Session(_engine) as session:
        return list(session.exec(select(AuctionLot).order_by(AuctionLot.lot_number)))


def count_lots() -> int:
    with Session(_engine) as session:
        return len(session.exec(select(AuctionLot.lot_number)).all())


# Rows a statistics walk wrote. Every buy-side reader excludes them: they are
# concluded sales kept as evidence for a price, not lots anyone can bid on, and
# they share this table only so that "never read the same sheet twice" keeps
# working across both. `is_(None)` is part of the test because every row written
# before the column existed came from a morning fetch.
STATS = "stats"


def _buy_side(statement):
    """Narrow a query to lots the morning workflow owns."""
    return statement.where(
        (AuctionLot.discovered_by != STATS) | (AuctionLot.discovered_by.is_(None))
    )


def lots_on(trade_date) -> list[AuctionLot]:
    """Every stored lot trading on one day, earliest slot first.

    Statistics lots are excluded even though a concluded sale can share today's
    date: the archive is read on the day it happens too, and a lot that has
    already been through the ring is not on the list of things to look at this
    morning.
    """
    with Session(_engine) as session:
        return list(
            session.exec(
                _buy_side(
                    select(AuctionLot).where(AuctionLot.trade_date == trade_date)
                ).order_by(AuctionLot.trade_time, AuctionLot.lot_number)
            )
        )


def pending_sheets(include_failed: bool = False) -> list[AuctionLot]:
    """Lots with a downloaded sheet that nothing has read yet — Phase 3's queue.

    ``failed`` lots are excluded by default so a re-run does not keep paying to
    retry a sheet that is genuinely unreadable, but they are not lost either:
    most failures are transient (a rate limit, a dropped connection), so
    ``include_failed`` puts them back in the queue on request.
    """
    wanted = ["pending", "failed"] if include_failed else ["pending"]
    with Session(_engine) as session:
        return list(
            session.exec(
                _buy_side(
                    select(AuctionLot)
                    .where(AuctionLot.sheet_status.in_(wanted))
                    .where(AuctionLot.sheet_path != None)  # noqa: E711 — SQL, not Python
                ).order_by(AuctionLot.lot_number)
            )
        )
