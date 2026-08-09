"""Write normalised lots to ``runs/<run>/lots.csv``.

CSV rather than xlsx (which is what bazaraki exports) because this file sits
*inside* a run directory next to ``lots.json`` and the sheets, and a run
directory should stay something you can read with anything — ``less``, Numbers,
pandas, a diff between two runs. Nothing here needs formatting or formulas.

The database holds every lot ever seen; this holds the lots of one run. Deleting
it loses nothing that ``normalize`` cannot rebuild from ``lots.json``.
"""
from __future__ import annotations

import csv
from pathlib import Path

# Column order for reading, not for storage: identity, then the car, then what
# it costs, then the sheet's bookkeeping. Deliberately not the model's field
# order — `lot_short` and `trade_time` matter more to a human scanning this than
# `banzai_id` does.
COLUMNS = [
    "lot_number", "lot_short", "auction_name", "trade_date", "trade_time",
    "mark", "model", "modification", "body_model_code",
    "registration_year", "registration_month", "mileage_km",
    "engine_capacity", "fuel_type", "transmission", "colour", "steering",
    "grade_origin", "status_code",
    "start_price_jpy", "end_price_jpy", "currency", "source",
    "sheet_status", "sheet_path", "body_number", "banzai_id", "auction_id",
    "model_id", "sheet_sha256", "sheet_url",
]


def write_lots_csv(rows: list[dict], path: Path) -> Path:
    """One row per lot, in :data:`COLUMNS` order. Overwrites.

    Sorted by trade time then lot number so the file reads in the order the
    lots will actually cross the block, which is the order you want when
    deciding what to look at first.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        rows,
        key=lambda r: (
            str(r.get("trade_date") or ""),
            r.get("trade_time") or "",
            r.get("lot_number") or "",
        ),
    )

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in ordered:
            writer.writerow({column: row.get(column) for column in COLUMNS})
    return path
