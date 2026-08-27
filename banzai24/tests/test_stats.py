"""The archive walk: what it searches, what it trusts, and what it refuses.

Two things here are worth more than the rest. The first is that a masked price
is *ranked* even though it is not shown — the whole walk is built on banzai24
ordering lots by a number it declines to display, so the guard that notices when
that stops being true is the guard that matters. The second is that a reveal
answering ``0`` is a failed read: the endpoint returns 200-with-zeros rather than
401 to a caller without the SPA's token, and a zero stored as a price would be a
free car on a page that still renders.

See ``docs/adr/0006-a-masked-price-is-still-a-ranked-price.md``.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlmodel import create_engine

from banzai24 import config, db, requirements, search, stats
from banzai24.models import AuctionLot
from banzai24.search import SearchDefinitionError

RAV4 = """
car = "toyota-rav4"

[site]
transmission = "auto"
grade = ["4", "4.5", "5"]

[api]
body_model_code = ["AXAH54"]

[sheet]
no_damage_codes = ["W", "X"]

[auction_statistics]
model_grade = ["HYBRID G"]
engine_capacity_start = 2.5

[[band]]
year = 2023
mileage_end = 50000
max_bid_jpy = { private = 2_505_000 }

[[band]]
year = 2023
mileage_start = 50001
mileage_end = 70000
max_bid_jpy = { private = 2_205_000 }
"""


def _write(tmp_path, text=RAV4, name="toyota-rav4"):
    (tmp_path / f"{name}.toml").write_text(text, encoding="utf-8")
    return tmp_path


# --- what the archive search asks for ----------------------------------------


def test_statistics_inherit_the_site_and_are_narrowed_by_their_own_section(tmp_path):
    """The measurement is of the car being bought, or it measures nothing useful."""
    definition = search.load("toyota-rav4", _write(tmp_path))
    filters = definition.stats_filters(definition.bands[0])

    assert filters.transmission == "auto"          # inherited from [site]
    assert filters.grade_origin == ("4", "4.5", "5")
    assert filters.model_grade == ("HYBRID G",)    # added by [auction_statistics]
    assert filters.engine_capacity_start == 2.5    # overrides [site]


def test_the_band_pins_the_year_and_mileage_exactly(tmp_path):
    """Not the search-wide span. A band is the thing with a price on it, so the
    sales it is judged against are the ones inside it — the second band must not
    be measured against the first band's cheaper, lower-mileage cars."""
    definition = search.load("toyota-rav4", _write(tmp_path))
    low, high = (definition.stats_filters(b) for b in definition.bands)

    assert (low.year_start, low.year_end) == (2023, 2023)
    assert (low.mileage_start, low.mileage_end) == (0, 50000)
    assert (high.mileage_start, high.mileage_end) == (50001, 70000)


def test_the_archive_and_sold_are_not_read_from_the_file(tmp_path):
    """They are what a statistics search *is*, not facts about a car."""
    definition = search.load("toyota-rav4", _write(tmp_path))
    filters = definition.stats_filters(definition.bands[0])
    assert (filters.source, filters.status) == ("archive", "SOLD")


def test_a_file_may_not_ask_for_statistics_over_unsold_lots(tmp_path):
    """`source = "auctions"` here would be statistics about cars nobody has
    bought yet, which have no hammer price to measure."""
    text = RAV4.replace('model_grade = ["HYBRID G"]',
                        'model_grade = ["HYBRID G"]\nsource = "auctions"')
    with pytest.raises(SearchDefinitionError, match="source"):
        search.load("toyota-rav4", _write(tmp_path, text))


def test_a_misspelled_key_is_an_error_here_too(tmp_path):
    """A silently-ignored `model_grades` would measure every trim line at once
    and still render — the failure this codebase is written against."""
    text = RAV4.replace("model_grade =", "model_grades =")
    with pytest.raises(SearchDefinitionError, match="model_grades"):
        search.load("toyota-rav4", _write(tmp_path, text))


def test_the_url_carries_the_trim_line_and_the_archive(tmp_path):
    definition = search.load("toyota-rav4", _write(tmp_path))
    url = config.build_search_url(definition.stats_filters(definition.bands[0]))
    assert "modelGrade=HYBRID+G" in url
    assert "source=archive" in url and "status=SOLD" in url
    assert "yearStart=2023&yearEnd=2023" in url


def test_a_search_without_the_section_still_measures_something(tmp_path):
    """An absent section is not an error — [site] and [api] describe a perfectly
    good archive search. It is only reported, so an unnarrowed measurement is
    never mistaken for a narrowed one."""
    text = RAV4.replace('[auction_statistics]\nmodel_grade = ["HYBRID G"]\n'
                        'engine_capacity_start = 2.5\n', "")
    definition = search.load("toyota-rav4", _write(tmp_path, text))
    assert definition.stats_declared is False
    assert definition.stats_filters(definition.bands[0]).source == "archive"


# --- the ordering the whole walk rests on ------------------------------------


def test_masked_prices_do_not_break_the_ordering_check():
    """Most of the cheap end is masked. `None` is absence of a *display*, and
    says nothing about where the lot ranks."""
    stats._check_ordering([None, 2_960_000, None, None, 3_117_000, None])


def test_visible_prices_falling_out_of_order_stop_the_walk():
    """If the sort has moved, the cheap end is not the cheap end, and five
    plausible cars would render as the answer to a question they do not answer."""
    with pytest.raises(stats.OrderingBroken):
        stats._check_ordering([2_960_000, None, 2_100_000, 3_117_000])


def test_zero_is_not_a_price():
    """The reveal endpoint answers 200-with-zeros to a caller without the SPA's
    token. Nothing about a stored 0 would look wrong on the page."""
    assert stats._price_or_none({"data": {"priceStart": 0, "priceEnd": 0}}) is None
    assert stats._price_or_none({"data": {"priceEnd": 3_695_000}}) == 3_695_000
    assert stats._price_or_none({}) is None
    assert stats._price_or_none(None) is None


# --- the table these lots share with the morning workflow --------------------


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(db, "_engine", engine)
    db.init_db()
    return engine


def _lot(number: str, discovered_by: str | None, **kw) -> dict:
    return {
        "lot_number": number, "lot_short": number, "banzai_id": number,
        "auction_id": 1, "auction_name": "MIRIVE Saitama",
        "trade_date": date(2026, 8, 26), "trade_time": "11:00",
        "discovered_by": discovered_by,
        "sheet_path": f"stats/{number}.jpg", "sheet_sha256": "abc",
        "sheet_status": "pending", **kw,
    }


def test_statistics_lots_never_join_the_morning_extraction_queue(temp_db):
    """They are read by the walk that found them. Left in the queue, `extract`
    would pay a second time for sheets already read."""
    db.upsert_lots([_lot("1-1-1", "fetch"), _lot("2-2-2", db.STATS)])
    assert [lot.lot_number for lot in db.pending_sheets()] == ["1-1-1"]


def test_statistics_lots_are_not_on_the_days_report(temp_db):
    """A concluded sale can share today's date — the archive is read on the day
    it happens too — and it is still not something to bid on this morning."""
    db.upsert_lots([_lot("1-1-1", "fetch"), _lot("2-2-2", db.STATS)])
    assert [lot.lot_number for lot in db.lots_on(date(2026, 8, 26))] == ["1-1-1"]


def test_rows_written_before_statistics_existed_are_still_buy_side(temp_db):
    """`discovered_by` is NULL for every row that predates the column, and every
    one of them came from a morning fetch."""
    db.upsert_lots([_lot("1-1-1", None)])
    assert [lot.lot_number for lot in db.pending_sheets()] == ["1-1-1"]


# --- what the run says it did ------------------------------------------------


def _band(definition, index=0):
    return definition.bands[index]


def test_the_cap_is_reported_rather_than_left_to_look_like_a_thin_market(tmp_path):
    """Three keepers because the cheap end is damaged, and three keepers because
    only three cars sold, are opposite findings."""
    definition = search.load("toyota-rav4", _write(tmp_path))
    result = stats.BandStats(band=_band(definition), url="u", stopped_by="cap",
                             inspected=20, failed=17, cap=20)
    result.keepers = [stats.Benchmark("1-1-1", 2_885_000, {}, True)]
    assert "cap" in result.summary()
    assert "17 failed a requirement" in result.summary()


def test_the_cap_reported_is_the_cap_that_ran(tmp_path):
    """`--cap 1` reporting "stopped at the 20-inspection cap" is a wrong number
    on a page that still renders, which is the whole failure mode here."""
    definition = search.load("toyota-rav4", _write(tmp_path))
    result = stats.BandStats(band=_band(definition), url="u", stopped_by="cap",
                             inspected=1, cap=1)
    assert "stopped at the 1-inspection cap" in result.summary()


def test_an_exhausted_archive_says_so(tmp_path):
    definition = search.load("toyota-rav4", _write(tmp_path))
    result = stats.BandStats(band=_band(definition), url="u",
                             stopped_by="exhausted", inspected=4)
    assert "archive exhausted" in result.summary()


def test_a_full_house_reports_the_range_it_found(tmp_path):
    definition = search.load("toyota-rav4", _write(tmp_path))
    result = stats.BandStats(band=_band(definition), url="u", stopped_by="keepers")
    result.keepers = [stats.Benchmark(f"{i}", price, {}, False)
                      for i, price in enumerate((2_885_000, 3_040_000, 3_117_000))]
    assert "2,885,000–3,117,000 ¥" in result.summary()
    assert "exhausted" not in result.summary()


# --- the weekly guard --------------------------------------------------------


@pytest.fixture
def stats_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "STATS_DIR", tmp_path)
    return tmp_path


def test_a_search_never_measured_is_never_fresh(stats_dir):
    assert stats.is_fresh("toyota-rav4", 7) is False


def test_freshness_is_counted_from_the_stamped_day(stats_dir):
    stats.record_run("toyota-rav4", date(2026, 8, 20))
    assert stats.is_fresh("toyota-rav4", 7, today=date(2026, 8, 26)) is True
    assert stats.is_fresh("toyota-rav4", 7, today=date(2026, 8, 27)) is False


def test_an_unreadable_stamp_means_measure_it_again(stats_dir):
    """The cautious reading of a corrupt file is the one that costs a walk, not
    the one that leaves a price un-evidenced for weeks with nothing saying so."""
    stats.stamp_path("toyota-rav4").parent.mkdir(parents=True, exist_ok=True)
    stats.stamp_path("toyota-rav4").write_text("last tuesday", encoding="utf-8")
    assert stats.is_fresh("toyota-rav4", 7) is False


# --- the cap is about money, so it is tested by counting the spending ---------


class _FakePage:
    """Just enough page for the walk: it never sorts, clicks or navigates."""

    context = object()

    async def goto(self, *a, **kw):
        return None


def _items(prices):
    return [
        {
            "id": f"id-{i}", "auctImage": "http://example/sheet",
            "bodyModelCode": "AXAH54",
            "endPrice": str(price),
            "lot": {"number": f"1-1-{i}", "shortNumber": str(i),
                    "tradeDate": "2026-08-01", "auction": {"id": 1, "name": "TAA"}},
            "car": {"mark": "TOYOTA", "model": "RAV4"},
            "characteristics": {"mileage": 30000, "modification": "HYBRID G"},
        }
        for i, price in enumerate(prices)
    ]


@pytest.fixture
def offline_walk(monkeypatch):
    """Every step of the walk except the counting one, stubbed out."""
    calls = {"paid": 0, "pages": [], "turned": 0}

    async def _first(page, headless):
        return {"items": ["something"]}

    async def _sorted(page):
        pages = calls["pages"] or [_items(range(3_000_000, 3_020_000, 1000))]
        calls["pages"] = pages
        return {"items": pages[0],
                "pagination": {"totalPages": len(pages)}}

    async def _goto(page, number):
        calls["turned"] += 1
        return {"items": calls["pages"][number - 1]}

    async def _download(client, item, directory):
        return None

    def _store(item, directory):
        """Stores for real, into the temp database, as the walk's own does."""
        db.upsert_lots([{
            "lot_number": item["lot"]["number"],
            "lot_short": item["lot"]["shortNumber"],
            "banzai_id": item["id"], "auction_id": 1, "auction_name": "TAA",
            "trade_date": date(2026, 8, 1), "trade_time": "10:00",
        }])
        return item["lot"]["number"]

    def _judge(definition, filters, lot_number, client):
        """Always a keeper, always paid — the worst case for a cap."""
        calls["paid"] += 1

        class _Passing:
            group = requirements.MEETS

        return _Passing(), True

    async def _snapshot(context):
        return True

    monkeypatch.setattr("banzai24.fetch._await_first_page", _first)
    monkeypatch.setattr(stats, "_sort_ascending", _sorted)
    monkeypatch.setattr(stats, "_download_sheet", _download)
    monkeypatch.setattr(stats, "_store", _store)
    monkeypatch.setattr(stats, "_judge", _judge)
    monkeypatch.setattr(stats, "_goto_page", _goto)
    # Politeness to banzai24 is not politeness to the test suite.
    monkeypatch.setattr("banzai24.fetch.PAGE_DELAY_S", 0)
    monkeypatch.setattr("banzai24.session.snapshot", _snapshot)
    return calls


def test_the_walk_never_spends_past_its_cap(tmp_path, offline_walk, temp_db):
    """The cap is a budget, and a budget that reports 2 while spending 6 is
    worse than no budget: you would not go looking for the difference."""
    import asyncio

    definition = search.load("toyota-rav4", _write(tmp_path))
    result = asyncio.run(stats.walk_band(
        _FakePage(), definition, definition.bands[0],
        client=None, wanted=99, cap=3, headless=True,
    ))
    assert offline_walk["paid"] == 3
    assert result.inspected == 3
    assert result.stopped_by == "cap"


def test_the_walk_stops_at_the_keepers_it_wanted(tmp_path, offline_walk, temp_db):
    import asyncio

    definition = search.load("toyota-rav4", _write(tmp_path))
    result = asyncio.run(stats.walk_band(
        _FakePage(), definition, definition.bands[0],
        client=None, wanted=2, cap=99, headless=True,
    ))
    assert offline_walk["paid"] == 2
    assert len(result.keepers) == 2
    assert result.stopped_by == "keepers"


# --- paging, because one page says nothing about an archive ------------------


def _walk(definition, offline_walk, **kw):
    import asyncio

    return asyncio.run(stats.walk_band(
        _FakePage(), definition, definition.bands[0],
        client=None, headless=True, **kw))


def _wrong_car(prices):
    """Lots the `[api]` chassis filter rejects — the cheap petrol RAV4s."""
    items = _items(prices)
    for item in items:
        item["bodyModelCode"] = "MXAA52"
    return items


def test_a_page_of_the_wrong_car_is_not_an_empty_archive(tmp_path, offline_walk, temp_db):
    """The bug this was written for: `[api]` rejected all twenty cheapest lots,
    the walk stopped, and it reported "archive exhausted" over 29 unread pages.
    Rejecting a page costs nothing, so it says nothing about what is behind it."""
    definition = search.load("toyota-rav4", _write(tmp_path))
    offline_walk["pages"] = [
        _wrong_car(range(2_000_000, 2_020_000, 1000)),
        _items(range(3_000_000, 3_020_000, 1000)),
    ]
    result = _walk(definition, offline_walk, wanted=2, cap=99)

    assert offline_walk["turned"] == 1
    assert result.api_rejected == 20
    assert len(result.keepers) == 2
    assert result.pages == 2


def test_the_reason_nothing_was_inspected_is_on_the_line(tmp_path, offline_walk, temp_db):
    """"0 keepers · 0 inspected" with no reason reads as "there is nothing here"
    when it means "none of the cheapest lots is even the right car"."""
    definition = search.load("toyota-rav4", _write(tmp_path))
    offline_walk["pages"] = [_wrong_car(range(2_000_000, 2_020_000, 1000))]
    result = _walk(definition, offline_walk, wanted=5, cap=99)

    assert "20 dropped by [api]" in result.summary()
    assert "1 of 1 page read" in result.summary()
    assert result.stopped_by == "exhausted"


def test_paging_stops_at_the_page_limit_and_says_so(tmp_path, offline_walk, temp_db,
                                                    monkeypatch):
    """`[api]` can reject page after page without spending a penny, so the
    inspection cap cannot bound this. Something has to."""
    monkeypatch.setattr(stats, "PAGE_LIMIT", 3)
    definition = search.load("toyota-rav4", _write(tmp_path))
    offline_walk["pages"] = [_wrong_car(range(2_000_000 + n, 2_020_000 + n, 1000))
                             for n in range(0, 50_000, 20_000)][:5]
    offline_walk["pages"] += [_wrong_car(range(3_000_000, 3_020_000, 1000))]
    result = _walk(definition, offline_walk, wanted=5, cap=99)

    assert result.stopped_by == "pages"
    assert result.pages == 3
    assert "stopped at the 3-page limit" in result.summary()


def test_a_page_turn_that_loses_the_sort_is_caught(tmp_path, offline_walk, temp_db):
    """Pagination is the SPA's, so the sort travels with it — but that is an
    assumption about someone else's client state. Page two on its own would look
    perfectly ordered while being a different list entirely."""
    definition = search.load("toyota-rav4", _write(tmp_path))
    offline_walk["pages"] = [
        _wrong_car(range(3_000_000, 3_020_000, 1000)),
        _items(range(2_000_000, 2_020_000, 1000)),    # cheaper again: sort reset
    ]
    with pytest.raises(stats.OrderingBroken):
        _walk(definition, offline_walk, wanted=2, cap=99)
