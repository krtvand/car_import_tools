"""Deciding who counts as a competitor, and refusing to render silence as good news.

The arithmetic is small — landed cost plus resale costs plus the required profit
— and it is exercised end to end elsewhere. What is guarded here is the *edge*:
which adverts get in, which get out, and what the page says when it cannot answer
at all. An advert wrongly excluded is a threat you never see, and every one of
these failures looks identical on the page: a shorter list.

Built against plain stand-ins rather than the database, the same way
``bazaraki.analysis`` keeps its pure functions testable without one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from searches.definition import Band, CompetitorBounds, CompetitorFilters

from dashboard import competitors

BAND = Band(year=2023, mileage_start=0, mileage_end=50_000,
            max_bid_jpy={"private": 1_805_000},
            competitors=CompetitorBounds(year_start=2019, mileage_end=120_000))


def _advert(**overrides):
    base = dict(
        ad_id=1, url="https://bazaraki.com/adv/1", title="Mazda CX-30",
        price=16_000.0, year=2021, mileage_km=80_000,
        fuel_type="Petrol", gearbox="Automatic", seller_type="private",
        colour="White", engine_size="2,0L", availability=None,
        is_active=True, delisted_at=None, days_on_market=40,
    )
    return SimpleNamespace(**{**base, **overrides})


# --- who is inside the bounds ------------------------------------------------


def test_an_older_car_with_more_kilometres_is_still_competition():
    """The whole reason the bounds are declared separately from the band: a 2021
    car with 80,000 km takes the sale from your 2023 import with 50,000."""
    assert competitors._in_band(_advert(), BAND) is True


@pytest.mark.parametrize("advert", [
    _advert(year=2018),            # older than the bounds allow
    _advert(mileage_km=130_000),   # past the ceiling
    _advert(year=None),            # unknown is not "inside"
    _advert(mileage_km=None),
])
def test_outside_the_bounds_or_unknown_is_not_competition(advert):
    assert competitors._in_band(advert, BAND) is False


# --- the [competitors] filters -----------------------------------------------


def test_fuel_type_is_a_list_and_matched_case_insensitively():
    """bazaraki's own filter is single-choice, which is why this is applied in
    memory rather than at the crawl."""
    filters = CompetitorFilters(fuel_type=("petrol", "hybrid petrol"))
    assert competitors._passes(_advert(fuel_type="Hybrid Petrol"), filters) is True
    assert competitors._passes(_advert(fuel_type="Diesel"), filters) is False


def test_engine_size_is_read_out_of_bazarakis_own_formatting():
    """``2,0L`` is a fact about a website, so the .toml says ``2.0`` and this
    does the translating."""
    assert competitors._litres("2,0L") == 2.0
    assert competitors._litres(None) is None

    filters = CompetitorFilters(engine_size_start=1.8, engine_size_end=2.5)
    assert competitors._passes(_advert(engine_size="2,0L"), filters) is True
    assert competitors._passes(_advert(engine_size="1,5L"), filters) is False


def test_a_field_the_scraper_never_captured_fails_rather_than_passes():
    """An advert that cannot be shown to be a petrol car is not evidence about
    the petrol market. A filter that keeps what it cannot read is not a filter."""
    assert competitors._passes(
        _advert(fuel_type=None), CompetitorFilters(fuel_type=("petrol",))) is False
    assert competitors._passes(
        _advert(engine_size=None), CompetitorFilters(engine_size_start=1.8)) is False


def test_no_filters_declared_keeps_everything():
    assert competitors._passes(_advert(), CompetitorFilters()) is True


# --- the list itself ---------------------------------------------------------


def test_only_adverts_under_the_sell_price_are_competitors():
    rows, considered = competitors._rows_for(
        BAND,
        [_advert(ad_id=1, price=16_000.0), _advert(ad_id=2, price=19_000.0)],
        CompetitorFilters(), sell_price=17_859.0)

    assert [row.ad_id for row in rows] == [1]
    # Both were inside the bounds; only one is under the price. The count is on
    # the page so "nothing under €17,859" can say what it looked at.
    assert considered == 2


def test_the_cheapest_undercut_comes_first():
    """It is the one that costs you the sale; sorting by anything else buries it."""
    rows, _ = competitors._rows_for(
        BAND,
        [_advert(ad_id=1, price=16_900.0), _advert(ad_id=2, price=15_200.0),
         _advert(ad_id=3, price=16_100.0)],
        CompetitorFilters(), sell_price=17_859.0)

    assert [row.price for row in rows] == [15_200.0, 16_100.0, 16_900.0]
    assert rows[0].under_by == pytest.approx(2_659.0)


def test_an_advert_with_no_price_is_not_a_competitor():
    rows, considered = competitors._rows_for(
        BAND, [_advert(price=None)], CompetitorFilters(), sell_price=17_859.0)
    assert rows == () and considered == 1


# --- what has already gone ---------------------------------------------------


def test_recently_delisted_adverts_are_counted_as_evidence_it_moves():
    """Delisting is the only sold-proxy this system has."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    sold = competitors._sold(BAND, [
        _advert(ad_id=1, price=20_000.0, delisted_at=now - timedelta(days=5)),
        _advert(ad_id=2, price=22_000.0, delisted_at=now - timedelta(days=20)),
        _advert(ad_id=3, price=99_000.0, delisted_at=now - timedelta(days=400)),
        _advert(ad_id=4, price=21_000.0, delisted_at=None),
    ], CompetitorFilters())

    assert sold.count == 2
    assert sold.median_price == 21_000.0
    assert "2 delisted" in sold.describe()


def test_no_recent_delistings_says_so_rather_than_showing_nothing():
    assert "none delisted" in competitors._sold(BAND, [], CompetitorFilters()).describe()


# --- never an empty list without a reason ------------------------------------


def test_no_completed_crawl_names_the_command_that_would_fix_it():
    """The failure this panel exists to avoid: an empty list that means "nobody
    has looked" rendered as "nobody undercuts you"."""
    search = SimpleNamespace(name="mazda-3",
                             competitor_scope=lambda: BAND.competitors)
    note = competitors._coverage_note(search, None)
    assert "no completed bazaraki crawl" in note
    assert "scrape --search mazda-3" in note


def test_a_crawl_narrower_than_the_bounds_names_the_gap():
    """`db._in_scope` bounds delisting to a run's own filters, so an advert
    outside every recent scope keeps its "still on sale" flag for ever."""
    search = SimpleNamespace(name="mazda-cx30",
                            competitor_scope=lambda: BAND.competitors)
    run = SimpleNamespace(started_at=datetime(2026, 8, 24),
                          year_min=2022, year_max=None,
                          mileage_min=10_000, mileage_max=70_000)

    note = competitors._coverage_note(search, run)
    assert "years before 2022" in note
    assert "under 10,000 km" in note and "over 70,000 km" in note


def test_a_crawl_that_covers_the_bounds_says_nothing():
    search = SimpleNamespace(name="mazda-cx30",
                            competitor_scope=lambda: BAND.competitors)
    run = SimpleNamespace(started_at=datetime(2026, 8, 24),
                          year_min=2019, year_max=None,
                          mileage_min=0, mileage_max=120_000)
    assert competitors._coverage_note(search, run) is None


def test_undeclared_bounds_produce_no_coverage_note():
    """Every band already says it has none; a second warning would be one fact
    printed twice."""
    search = SimpleNamespace(name="mazda-cx5",
                            competitor_scope=lambda: CompetitorBounds())
    run = SimpleNamespace(started_at=datetime(2026, 8, 24), year_min=2022,
                          year_max=None, mileage_min=20_000, mileage_max=70_000)
    assert competitors._coverage_note(search, run) is None


# --- the verdict that outranks the list --------------------------------------


def test_a_band_the_market_will_not_pay_for_is_flagged_regardless_of_competitors():
    """No competitor has to do anything for this to be a losing trade, so it is
    said before the list rather than inside it."""
    losing = competitors.BandPanel(band=BAND, max_bid_jpy=1_805_000,
                                   sell_price_eur=22_000.0,
                                   cyprus_estimate_eur=20_705.0)
    working = competitors.BandPanel(band=BAND, max_bid_jpy=1_805_000,
                                    sell_price_eur=17_859.0,
                                    cyprus_estimate_eur=20_705.0)
    unpriced = competitors.BandPanel(band=BAND, max_bid_jpy=1_805_000)

    assert losing.underwater is True
    assert working.underwater is False
    assert unpriced.underwater is False    # no claim either way
