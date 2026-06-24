"""Tests for the SQLite storage layer (upsert / read)."""
from __future__ import annotations

import pytest
from sqlmodel import create_engine

import db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point db at a fresh temp-file SQLite database for each test."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(db, "_engine", engine)
    db.init_db()
    return engine


def test_insert_then_read(temp_db):
    db.upsert_listing({"ad_id": 1, "title": "Car A", "url": "u", "price": 100.0})
    rows = db.all_listings()
    assert db.count_listings() == 1
    assert rows[0].ad_id == 1
    assert rows[0].title == "Car A"
    assert rows[0].first_seen_at is not None
    assert rows[0].last_seen_at is not None


def test_upsert_merges_without_clobbering_with_none(temp_db):
    # Card handler writes base fields.
    db.upsert_listing({"ad_id": 1, "title": "Car A", "url": "u", "price": 100.0})
    # Detail handler writes enrichment; missing keys must not wipe existing data.
    db.upsert_listing({"ad_id": 1, "year": 2020, "mileage_km": 5000})

    row = db.all_listings()[0]
    assert db.count_listings() == 1          # still one row, not two
    assert row.title == "Car A"              # preserved
    assert row.price == 100.0                # preserved
    assert row.year == 2020                  # added
    assert row.mileage_km == 5000            # added


def test_upsert_overwrites_with_new_non_none_value(temp_db):
    db.upsert_listing({"ad_id": 1, "title": "Old", "url": "u"})
    db.upsert_listing({"ad_id": 1, "title": "New", "url": "u"})
    rows = db.all_listings()
    assert len(rows) == 1
    assert rows[0].title == "New"


def test_last_seen_updates_on_reupsert(temp_db):
    db.upsert_listing({"ad_id": 1, "title": "A", "url": "u"})
    first = db.all_listings()[0]
    seen1, last1 = first.first_seen_at, first.last_seen_at

    db.upsert_listing({"ad_id": 1, "title": "A", "url": "u"})
    second = db.all_listings()[0]
    assert second.first_seen_at == seen1        # first_seen is stable
    assert second.last_seen_at >= last1         # last_seen refreshed


def test_all_listings_ordered_by_ad_id(temp_db):
    for ad_id in (3, 1, 2):
        db.upsert_listing({"ad_id": ad_id, "title": f"c{ad_id}", "url": "u"})
    assert [r.ad_id for r in db.all_listings()] == [1, 2, 3]