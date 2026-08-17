"""One-off: drop the ``bidrecord`` table from ``auction.db``.

Run once, from the project root:

    uv run python -m banzai24.migrate_drop_bidrecord

``BidRecord`` existed for a bidding runbook that was never built. It held zero
rows for its whole life, so dropping it costs no data — and leaving it in place
costs a table that ``init_db`` would keep re-creating on every fresh database.

A standalone script rather than a ``DROP TABLE IF EXISTS`` inside
:func:`banzai24.db.init_db`: that line would run on every command forever, and a
permanent instruction to delete a table is how a table you *do* want later gets
deleted by surprise. Precedent is ``bazaraki/migrate_make_model.py``.

Idempotent — dropping a table that is already gone is a no-op, so re-running
after a fresh clone is safe.
"""
from __future__ import annotations

from sqlalchemy import inspect, text

from . import db


def drop_bidrecord() -> int:
    """Drop the table if it exists. Returns the number of rows it still held."""
    inspector = inspect(db._engine)
    if not inspector.has_table("bidrecord"):
        return 0

    with db._engine.begin() as conn:
        rows = conn.execute(text("SELECT COUNT(*) FROM bidrecord")).scalar_one()
        conn.execute(text("DROP TABLE bidrecord"))
    return rows


if __name__ == "__main__":
    dropped = drop_bidrecord()
    print(f"Dropped bidrecord ({dropped} row(s) discarded)."
          if dropped else "bidrecord dropped (it was empty, or already gone).")
