"""SQLModel tables for Japanese auction lots.

Three tables, split by who writes them and how often:

* :class:`AuctionLot` — one row per lot, from the banzai24 list API. Rewritten
  every time a run sees the lot again.
* :class:`SheetExtraction` — the AI-derived half, keyed 1:1 to a lot. Separate
  so re-extracting a sheet rewrites only what the model produced and never
  touches the API-derived fields.
* :class:`BidRecord` — append-only, written after a bid is placed.

Mirrors :mod:`bazaraki.models` in shape; the field-level reasoning is in
``AUCTION_PLAN.md`` Phase 2.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlmodel import Field, SQLModel

# What ``AuctionLot.sheet_status`` may hold.
#   pending   — a sheet image is on disk, not yet read by the model
#   extracted — a SheetExtraction row exists for this image's hash
#   failed    — extraction was attempted and did not produce usable fields
#   no_sheet  — the lot has no auctImage, so there is nothing to read
SHEET_STATUSES = ("pending", "extracted", "failed", "no_sheet")


class AuctionLot(SQLModel, table=True):
    """One lot at one Japanese auction, as the list API describes it.

    The primary key is banzai24's own ``lot.number`` — ``"47-1312-35159"``,
    globally unique and encoding the auction house. That beats synthesising a
    ``house:date:lot_no`` composite: it comes free, it is what the site itself
    keys on, and it survives a car being re-listed on a later day.
    """

    lot_number: str = Field(primary_key=True)   # "47-1312-35159"
    lot_short: str                              # "35159", for display
    banzai_id: str                              # UUIDv7 -> /car/JP/{id}
    auction_id: int
    auction_name: str                           # "HAA Kobe"
    trade_date: date
    trade_time: str                             # "12:00", as printed

    mark: str | None = None
    model: str | None = None
    model_id: int | None = None
    modification: str | None = None      # "20S PROACTIVE TOURING"
    body_model_code: str | None = None   # "DMEJ3P" — prefix stripped
    body_number: str | None = None       # masked: "DMEJ3P-10**55"
    registration_year: int | None = None
    registration_month: int | None = None
    mileage_km: int | None = None        # API value — rounded to 1,000
    engine_capacity: str | None = None   # "2.0", as the API writes it
    fuel_type: str | None = None         # "petrol" | "hybrid" | … ; often absent
    transmission: str | None = None      # "auto" | "manual"
    colour: str | None = None            # "white", "other", …
    steering: str | None = None          # "right" | "left"

    grade_origin: str | None = None      # "4.5" — free on the list response
    status_code: str | None = None       # SOLD | LISTED | NOT_SOLD | …
    start_price_jpy: int | None = None
    end_price_jpy: int | None = None
    currency: str | None = None
    source: str | None = None            # auctions | archive

    sheet_url: str | None = None         # auctImage
    sheet_path: str | None = None
    sheet_sha256: str | None = None
    sheet_status: str = "pending"

    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


class SheetExtraction(SQLModel, table=True):
    """What the vision model read off one auction sheet.

    Keyed by ``sheet_sha256`` in practice even though the primary key is the lot:
    a re-extraction of the same image is a no-op, and a changed image (the lot
    was re-photographed, or re-listed) is a fresh extraction over the same row.
    ``raw_json`` keeps the model's full output verbatim so a later prompt change
    can be evaluated against what the old prompt actually returned.
    """

    lot_number: str = Field(primary_key=True, foreign_key="auctionlot.lot_number")
    extracted_at: datetime
    model_id: str                # "claude-opus-5" — provenance
    sheet_sha256: str
    raw_json: str                # full model output, verbatim

    interior_grade: str | None = None   # A–E
    exterior_grade: str | None = None   # A–E
    sheet_grade: str | None = None      # cross-check against grade_origin
    sheet_mileage_km: int | None = None # exact — 15415 vs API's 15000
    chassis_full: str | None = None     # unmasked
    damage_marks: str | None = None     # JSON: [{panel, code}]
    equipment: str | None = None        # JSON list
    warnings_ja: str | None = None      # 注意事項欄
    inspector_notes_ja: str | None = None
    inspector_notes_en: str | None = None
    drivetrain: str | None = None       # "2WD" | "4WD" | … as printed

    # 初度登録 and 車検 — on the sheet, absent from the list API. The raw form is
    # kept alongside the parsed one because it is what the sheet actually says
    # ("R5年1月"); the parsed year/month is what cross-checks and queries use.
    # A null shaken expiry is a fact, not a gap: no shaken means the buyer pays
    # to put the car back on the road.
    first_registration_raw: str | None = None
    first_registration_year: int | None = None
    first_registration_month: int | None = None
    shaken_expiry_raw: str | None = None

    # 車歴. Mutually exclusive and both nullable: an ex-rental sets the first and
    # leaves the second null, a private-use car the reverse, and a company car or
    # an unreadable field leaves both null. Two nullable notes rather than one
    # enum keeps the printed wording verbatim (both レンタカー and レンタ occur)
    # and keeps "the sheet didn't say" distinct from "the sheet said neither",
    # which a single `history` column would blur.
    rental_car_note: str | None = None
    private_car_note: str | None = None

    confidence: float | None = None


class BidRecord(SQLModel, table=True):
    """Append-only log of bids placed. For auditability and double-bid
    prevention — not an input to deciding what to bid."""

    id: int | None = Field(default=None, primary_key=True)
    lot_number: str = Field(index=True, foreign_key="auctionlot.lot_number")
    submitted_at: datetime
    amount_jpy: int
    outcome: str | None = None   # won | lost | error | unknown
    note: str | None = None
