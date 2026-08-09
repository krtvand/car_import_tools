"""Storing normalised lots, and re-storing them without damage.

The whole point of keeping ``lots.json`` is that normalising can be re-run at
any time — after a parser fix, after a schema change. That is only true if
re-running it is harmless, which is what most of this file checks.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine

from banzai24 import db, export, normalize
from banzai24.models import AuctionLot

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(db, "_engine", engine)
    db.init_db()
    return engine


@pytest.fixture
def run_dir(tmp_path) -> Path:
    directory = tmp_path / "run"
    (directory / "sheets").mkdir(parents=True)
    (directory / "lots.json").write_text(
        (FIXTURES / "lots_run.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return directory


def _row(**overrides) -> dict:
    row = {
        "lot_number": "47-1312-35159",
        "lot_short": "35159",
        "banzai_id": "019f-abc",
        "auction_id": 47,
        "auction_name": "HAA Kobe",
        "trade_date": date(2026, 8, 12),
        "trade_time": "12:00",
        "mark": "MAZDA",
        "model": "CX-30",
        "mileage_km": 15000,
        "grade_origin": "4.5",
        "status_code": "LISTED",
        "start_price_jpy": 1180000,
        "sheet_url": "https://example.invalid/sheet.jpg",
        "sheet_status": "pending",
    }
    return {**row, **overrides}


# --- the plan's test: normalize -> upsert -> re-upsert is idempotent ----------

def test_a_saved_run_upserts_and_re_upserting_changes_nothing(temp_db, run_dir):
    rows, problems = normalize.load_run(run_dir, all_lots=True)
    assert not problems

    inserted, updated = db.upsert_lots(rows)
    assert (inserted, updated) == (len(rows), 0)
    assert db.count_lots() == len(rows)

    inserted, updated = db.upsert_lots(rows)
    assert (inserted, updated) == (0, len(rows))
    assert db.count_lots() == len(rows)          # no duplicates


def test_re_upserting_keeps_the_values_it_first_stored(temp_db, run_dir):
    rows, _ = normalize.load_run(run_dir, all_lots=True)
    db.upsert_lots(rows)
    before = {lot.lot_number: lot.start_price_jpy for lot in db.all_lots()}

    db.upsert_lots(rows)
    assert {lot.lot_number: lot.start_price_jpy for lot in db.all_lots()} == before


# --- upsert semantics --------------------------------------------------------

def test_first_seen_is_kept_while_last_seen_moves(temp_db):
    early = datetime(2026, 8, 1, tzinfo=timezone.utc)
    db.upsert_lots([_row()], now=early)
    db.upsert_lots([_row()], now=early + timedelta(days=3))

    lot = db.all_lots()[0]
    assert lot.first_seen_at.replace(tzinfo=timezone.utc) == early
    assert lot.last_seen_at.replace(tzinfo=timezone.utc) == early + timedelta(days=3)


def test_a_later_sighting_updates_the_fields_that_moved(temp_db):
    db.upsert_lots([_row(status_code="LISTED", end_price_jpy=None)])
    db.upsert_lots([_row(status_code="SOLD", end_price_jpy=1875000)])

    lot = db.all_lots()[0]
    assert db.count_lots() == 1
    assert lot.status_code == "SOLD"
    assert lot.end_price_jpy == 1875000


def test_a_missing_value_does_not_blank_out_one_already_stored(temp_db):
    db.upsert_lots([_row(mileage_km=15000)])
    db.upsert_lots([_row(mileage_km=None)])
    assert db.all_lots()[0].mileage_km == 15000


# --- not undoing Phase 3's work ----------------------------------------------
#
# Normalising always proposes sheet_status="pending" — it only knows an image is
# on disk. If that exact image has already been read, saying "pending" again
# would queue the whole database for re-extraction at $0.02 a sheet.

def test_re_normalizing_does_not_re_queue_an_already_extracted_sheet(temp_db):
    db.upsert_lots([_row(sheet_sha256="abc", sheet_status="pending")])
    with Session(temp_db) as session:                      # Phase 3 reads it
        lot = session.get(AuctionLot, "47-1312-35159")
        lot.sheet_status = "extracted"
        session.add(lot)
        session.commit()

    db.upsert_lots([_row(sheet_sha256="abc", sheet_status="pending")])
    assert db.all_lots()[0].sheet_status == "extracted"


def test_a_changed_sheet_image_does_go_back_in_the_queue(temp_db):
    """A different hash is a different photo — the old extraction describes
    something else, so it must be read again."""
    db.upsert_lots([_row(sheet_sha256="abc", sheet_status="pending")])
    with Session(temp_db) as session:
        lot = session.get(AuctionLot, "47-1312-35159")
        lot.sheet_status = "extracted"
        session.add(lot)
        session.commit()

    db.upsert_lots([_row(sheet_sha256="different", sheet_status="pending")])
    assert db.all_lots()[0].sheet_status == "pending"


def test_pending_sheets_lists_only_downloaded_unread_ones(temp_db):
    db.upsert_lots([
        _row(lot_number="1-1-1", sheet_path="runs/x/sheets/1-1-1.jpg", sheet_status="pending"),
        _row(lot_number="2-2-2", sheet_path=None, sheet_status="pending"),
        _row(lot_number="3-3-3", sheet_path="runs/x/sheets/3-3-3.jpg", sheet_status="extracted"),
    ])
    assert [lot.lot_number for lot in db.pending_sheets()] == ["1-1-1"]


# --- the CSV -----------------------------------------------------------------

def test_the_csv_holds_the_same_rows_as_the_database(temp_db, run_dir):
    result = normalize.run_normalize(run_dir, all_lots=True)

    written = (run_dir / "lots.csv").read_text(encoding="utf-8").splitlines()
    assert written[0].split(",") == export.COLUMNS
    assert len(written) - 1 == len(result.rows) == db.count_lots()


def test_the_csv_reads_in_the_order_the_lots_cross_the_block(run_dir):
    rows, _ = normalize.load_run(run_dir, all_lots=True)
    export.write_lots_csv(rows, run_dir / "lots.csv")

    import csv
    with (run_dir / "lots.csv").open(encoding="utf-8") as handle:
        stamps = [(r["trade_date"], r["trade_time"]) for r in csv.DictReader(handle)]
    assert stamps == sorted(stamps)


def test_normalizing_a_run_writes_both_outputs_and_says_so(temp_db, run_dir):
    result = normalize.run_normalize(run_dir)

    assert result.csv_path.exists()
    assert result.inserted == len(result.rows)
    assert "lots normalized" in result.summary()


# --- storing extractions -----------------------------------------------------

def _extraction_row(**overrides) -> dict:
    row = {
        "lot_number": "47-1312-35159",
        "extracted_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
        "model_id": "claude-opus-5",
        "sheet_sha256": "abc",
        "raw_json": "{}",
        "sheet_grade": "4.5",
        "sheet_mileage_km": 15415,
        "chassis_full": "DMEJ3P-103452",
        "confidence": 0.95,
    }
    return {**row, **overrides}


def test_writing_an_extraction_marks_its_lot_as_read(temp_db):
    db.upsert_lots([_row(sheet_sha256="abc")])
    assert db.upsert_extraction(_extraction_row()) is True

    assert db.all_lots()[0].sheet_status == "extracted"
    assert db.extraction_for("47-1312-35159").chassis_full == "DMEJ3P-103452"


def test_the_same_image_is_never_paid_for_twice(temp_db):
    db.upsert_lots([_row(sheet_sha256="abc")])
    db.upsert_extraction(_extraction_row(sheet_sha256="abc"))

    assert db.extraction_is_current("47-1312-35159", "abc") is True


def test_a_re_photographed_sheet_counts_as_new_work(temp_db):
    """Different hash, different image — the old extraction describes something
    else, so it must be read again."""
    db.upsert_lots([_row(sheet_sha256="abc")])
    db.upsert_extraction(_extraction_row(sheet_sha256="abc"))

    assert db.extraction_is_current("47-1312-35159", "different") is False


def test_a_lot_never_extracted_is_not_current(temp_db):
    assert db.extraction_is_current("47-1312-35159", "abc") is False
    assert db.extraction_is_current("47-1312-35159", None) is False


def test_re_extracting_replaces_nulls_instead_of_keeping_stale_values(temp_db):
    """Unlike lot upserts: a field the new read did not find means the sheet
    does not say it, and blending two readings would invent a third."""
    db.upsert_lots([_row(sheet_sha256="abc")])
    db.upsert_extraction(_extraction_row(warnings_ja="ﾋﾟSD欠品"))
    db.upsert_extraction(_extraction_row(warnings_ja=None))

    assert db.extraction_for("47-1312-35159").warnings_ja is None


def test_a_failed_sheet_is_recorded_and_left_out_of_the_queue(temp_db):
    db.upsert_lots([_row(sheet_path="runs/x/1.jpg", sheet_status="pending")])
    db.mark_sheet_status("47-1312-35159", "failed")

    assert db.pending_sheets() == []
    assert [l.lot_number for l in db.pending_sheets(include_failed=True)] \
        == ["47-1312-35159"]


# --- schema healing ----------------------------------------------------------

def test_a_column_added_after_the_table_was_created_is_backfilled(temp_db):
    """create_all never alters an existing table, so without this every read of
    an older database fails on the new column."""
    db.upsert_lots([_row(fuel_type="petrol")])
    with temp_db.begin() as conn:
        conn.execute(text("ALTER TABLE auctionlot DROP COLUMN fuel_type"))

    db.init_db()                                  # heals it
    assert db.all_lots()[0].fuel_type is None     # column back, value gone


def test_the_latest_run_is_the_newest_timestamped_directory(tmp_path):
    for name in ("2026-08-01_120000_A", "2026-08-09_090000_B", "2026-08-05_120000_C"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "lots.json").write_text("{}", encoding="utf-8")
    (tmp_path / "not-a-run").mkdir()

    assert normalize.latest_run(tmp_path).name == "2026-08-09_090000_B"
