"""Tests for the SQLite storage layer (upsert / read)."""
from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import create_engine

from bazaraki import db
from bazaraki.models import CarListing


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


# --- price history --------------------------------------------------------

def test_first_sighting_logs_a_price_observation(temp_db):
    db.upsert_listing({"ad_id": 1, "title": "A", "url": "u", "price": 100.0})
    hist = db.price_history(1)
    assert [o.price for o in hist] == [100.0]


def test_observation_logged_only_on_price_change(temp_db):
    db.upsert_listing({"ad_id": 1, "title": "A", "url": "u", "price": 100.0})
    db.upsert_listing({"ad_id": 1, "title": "A", "url": "u", "price": 100.0})  # unchanged
    db.upsert_listing({"ad_id": 1, "title": "A", "url": "u", "price": 90.0})   # cut
    db.upsert_listing({"ad_id": 1, "year": 2020})                              # no price key
    assert [o.price for o in db.price_history(1)] == [100.0, 90.0]


def test_detail_upsert_without_price_adds_no_observation(temp_db):
    db.upsert_listing({"ad_id": 1, "title": "A", "url": "u", "price": 100.0})
    db.upsert_listing({"ad_id": 1, "mileage_km": 5000})  # detail handler, no price
    assert len(db.price_history(1)) == 1


# --- lifecycle / delisting ------------------------------------------------

def _filters(**kw):
    """Minimal stand-in for config.CarFilters carrying only scope attrs."""
    scope = dict(
        make=None, model=None, year_min=None, year_max=None,
        price_min=None, price_max=None, mileage_min=None, mileage_max=None,
    )
    scope.update(kw)
    return types.SimpleNamespace(**scope)


def test_completed_run_delists_unseen_in_scope(temp_db):
    run = db.start_run(_filters(make="mazda", model="cx-30"))
    db.upsert_listing({"ad_id": 1, "title": "Mazda CX-30", "make": "Mazda",
                       "model": "CX-30", "url": "u", "price": 100.0})
    db.upsert_listing({"ad_id": 2, "title": "Mazda CX-30", "make": "Mazda",
                       "model": "CX-30", "url": "u", "price": 200.0})

    delisted = db.finalize_run(run, seen_ad_ids={1}, completed=True)

    assert delisted == 1
    rows = {r.ad_id: r for r in db.all_listings()}
    assert rows[1].is_active is True and rows[1].delisted_at is None
    assert rows[2].is_active is False and rows[2].delisted_at is not None


def test_truncated_run_delists_nothing(temp_db):
    run = db.start_run(_filters(make="mazda", model="cx-30"))
    db.upsert_listing({"ad_id": 2, "title": "Mazda CX-30", "make": "Mazda",
                       "model": "CX-30", "url": "u", "price": 200.0})
    assert db.finalize_run(run, seen_ad_ids=set(), completed=False) == 0
    assert db.all_listings()[0].is_active is True


def test_delisting_respects_model_scope(temp_db):
    run = db.start_run(_filters(make="mazda", model="cx-30"))
    db.upsert_listing({"ad_id": 5, "title": "Mazda CX-5", "make": "Mazda",
                       "model": "CX-5", "url": "u", "price": 300.0})  # different model
    assert db.finalize_run(run, seen_ad_ids=set(), completed=True) == 0
    assert db.all_listings()[0].is_active is True


def test_delisting_respects_numeric_scope(temp_db):
    run = db.start_run(_filters(make="mazda", model="cx-30", year_min=2018))
    db.upsert_listing({"ad_id": 6, "title": "Mazda CX-30", "make": "Mazda",
                       "model": "CX-30", "year": 2015, "url": "u", "price": 100.0})
    # 2015 < year_min, so it was never in this run's search space -> untouched.
    assert db.finalize_run(run, seen_ad_ids=set(), completed=True) == 0
    assert db.all_listings()[0].is_active is True


def test_sighting_reactivates_a_delisted_advert(temp_db):
    run = db.start_run(_filters(make="mazda", model="cx-30"))
    db.upsert_listing({"ad_id": 1, "title": "Mazda CX-30", "make": "Mazda",
                       "model": "CX-30", "url": "u", "price": 100.0})
    db.finalize_run(run, seen_ad_ids=set(), completed=True)
    assert db.all_listings()[0].is_active is False

    db.upsert_listing({"ad_id": 1, "title": "Mazda CX-30", "make": "Mazda",
                       "model": "CX-30", "url": "u", "price": 100.0})  # reappears
    row = db.all_listings()[0]
    assert row.is_active is True and row.delisted_at is None


# --- days_on_market property ---------------------------------------------

def test_days_on_market_uses_delisted_when_present():
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)
    listing = CarListing(
        ad_id=1, title="A", url="u",
        first_seen_at=first, delisted_at=first + timedelta(days=10),
    )
    assert listing.days_on_market == 10


def test_days_on_market_counts_to_now_while_active():
    first = datetime.now(timezone.utc) - timedelta(days=3, hours=1)
    listing = CarListing(ad_id=1, title="A", url="u", first_seen_at=first)
    assert listing.days_on_market == 3


def test_days_on_market_none_without_first_seen():
    assert CarListing(ad_id=1, title="A", url="u").days_on_market is None