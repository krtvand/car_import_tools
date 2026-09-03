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

NOW = datetime(2026, 8, 28, 12, 0)

BAND = Band(year=2023, mileage_start=0, mileage_end=50_000,
            max_bid_jpy={"private": 1_805_000},
            competitors=CompetitorBounds(year_start=2019, mileage_end=120_000))


def _advert(**overrides):
    base = dict(
        ad_id=1, url="https://bazaraki.com/adv/1", title="Mazda CX-30",
        price=16_000.0, year=2021, mileage_km=80_000,
        fuel_type="Petrol", gearbox="Automatic", seller_type="private",
        colour="White", engine_size="2,0L", availability=None, description=None,
        is_active=True, delisted_at=None, manual_exclusion_reason=None,
        posted_raw=None, first_seen_at=NOW - timedelta(days=3),
        last_seen_at=NOW,
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


def test_an_advert_naming_a_trim_you_are_not_selling_against_is_excluded():
    """The only filter that reads free text, because bazaraki has no grade field
    and a Cyprus advert names its trim — when it names it — in the seller's own
    words."""
    filters = CompetitorFilters(exclude_phrases=("hybrid x",))
    assert competitors._passes(
        _advert(description="Toyota RAV4 Hybrid X 2wd (Japanese Import)"),
        filters) is False


def test_the_phrase_is_matched_in_the_title_as_well_as_the_description():
    filters = CompetitorFilters(exclude_phrases=("hybrid x",))
    assert competitors._passes(
        _advert(title="Toyota RAV4 Hybrid X 2,5L 2023"), filters) is False


def test_the_seller_may_write_it_in_any_case_or_spacing():
    """`TOYOTA RAV 4  2.5L (G package)` is the spacing of a real advert."""
    filters = CompetitorFilters(exclude_phrases=("g package",))
    assert competitors._passes(
        _advert(description="TOYOTA RAV 4  2.5L (G   package) AWD hybrid"),
        filters) is False


def test_an_advert_that_names_another_trim_is_still_competition():
    filters = CompetitorFilters(exclude_phrases=("hybrid x",))
    assert competitors._passes(
        _advert(description="TOYOTA RAV 4 2.5L (G package) AWD hybrid"),
        filters) is True


def test_an_advert_that_names_no_trim_at_all_is_kept():
    """The one exclusion that keeps what it cannot read: an advert that has not
    said it is the cheaper grade still takes the sale, and about half the RAV4
    adverts up today say nothing about a grade."""
    filters = CompetitorFilters(exclude_phrases=("hybrid x",))
    assert competitors._passes(_advert(description=None), filters) is True
    assert competitors._passes(
        _advert(description="Japan import, full extra, 2 keys"), filters) is True


def test_two_bands_of_one_search_can_disagree_about_the_same_advert():
    """The RAV4 in one assertion: the band buying the G drops an advert that
    says it is an X, and the band buying the X — which is the same car as that
    advert — keeps it. Same listings, same search, one filter set apart."""
    import searches

    search = searches.parse({
        "car": "toyota-rav4",
        "competitors": {"fuel_type": ["hybrid petrol"]},
        "band": [
            {"year": 2023, "body_model_code": ["AXAH54"], "mileage_end": 50_000,
             "max_bid_jpy": {"private": 3_150_000},
             "competitors": {"year_start": 2022, "mileage_end": 70_000,
                             "exclude_phrases": ["hybrid x"]}},
            {"year": 2023, "body_model_code": ["AXAH52"], "mileage_end": 50_000,
             "max_bid_jpy": {"private": 2_705_000},
             "competitors": {"year_start": 2022, "mileage_end": 70_000}},
        ],
    }, name="toyota-rav4")
    advert = _advert(year=2023, mileage_km=39_000, fuel_type="Hybrid Petrol",
                     price=27_800.0, description="RAV4 Hybrid X 2wd, japan import")

    g_band, x_band = search.bands
    rows, _ = competitors._rows_for(
        g_band, [advert], search.competitors_for(g_band), 34_000.0, now=NOW)
    assert rows == ()
    rows, _ = competitors._rows_for(
        x_band, [advert], search.competitors_for(x_band), 34_000.0, now=NOW)
    assert [row.price for row in rows] == [27_800.0]


def test_no_filters_declared_keeps_everything():
    assert competitors._passes(_advert(), CompetitorFilters()) is True


# --- the list itself ---------------------------------------------------------


def test_an_advert_a_little_above_your_sell_price_is_a_competitor():
    """The ceiling. A seller asking 4% more is the same offer to a buyer who
    means to haggle, and the fact that such a car sold in a month is the
    clearest evidence this page can carry that your price works."""
    rows, considered = competitors._rows_for(
        BAND,
        [_advert(ad_id=1, price=16_000.0), _advert(ad_id=2, price=18_500.0)],
        CompetitorFilters(), sell_price=17_859.0)

    assert [row.ad_id for row in rows] == [1, 2]
    assert considered == 2


def test_an_advert_past_the_ceiling_is_not_a_competitor():
    """Where the list stops. Everything above is a different market — counted by
    ``_stuck_above``, never a row, or the three rows that matter are lost in two
    hundred that do not."""
    rows, considered = competitors._rows_for(
        BAND,
        [_advert(ad_id=1, price=16_000.0), _advert(ad_id=2, price=19_000.0)],
        CompetitorFilters(), sell_price=17_859.0)

    assert [row.ad_id for row in rows] == [1]
    # Both were inside the bounds. The count is on the page so "nothing under
    # €17,859" can say what it looked at.
    assert considered == 2


def test_the_ceiling_is_five_per_cent_unless_the_search_says_otherwise():
    """The default is the whole of what most searches will ever declare."""
    assert CompetitorFilters().price_ceiling_percent == 5.0
    assert CompetitorFilters().ceiling(20_000.0) == pytest.approx(21_000.0)
    assert CompetitorFilters(price_ceiling_percent=10).ceiling(20_000.0) == pytest.approx(22_000.0)

    wide = CompetitorFilters(price_ceiling_percent=20)
    rows, _ = competitors._rows_for(
        BAND, [_advert(price=19_000.0)], wide, sell_price=17_859.0)
    assert [row.price for row in rows] == [19_000.0]


def test_the_sign_of_the_euro_says_which_side_of_you_the_advert_is_on():
    """Positive undercuts you; negative is headroom. One signed number, because
    the table now reaches a little way above the sell price."""
    rows, _ = competitors._rows_for(
        BAND,
        [_advert(ad_id=1, price=16_000.0), _advert(ad_id=2, price=18_500.0)],
        CompetitorFilters(), sell_price=17_859.0)

    assert rows[0].versus_sell_price == pytest.approx(1_859.0)
    assert rows[0].undercuts is True
    assert rows[1].versus_sell_price == pytest.approx(-641.0)
    assert rows[1].undercuts is False


def test_the_cheapest_undercut_comes_first():
    """It is the one that costs you the sale; sorting by anything else buries it."""
    rows, _ = competitors._rows_for(
        BAND,
        [_advert(ad_id=1, price=16_900.0), _advert(ad_id=2, price=15_200.0),
         _advert(ad_id=3, price=16_100.0)],
        CompetitorFilters(), sell_price=17_859.0)

    assert [row.price for row in rows] == [15_200.0, 16_100.0, 16_900.0]
    assert rows[0].versus_sell_price == pytest.approx(2_659.0)


def test_an_advert_with_no_price_is_not_a_competitor():
    """It cannot be shown to be under the ceiling, and a filter that keeps what
    it cannot read is not a filter. It is still counted as looked at."""
    rows, considered = competitors._rows_for(
        BAND, [_advert(price=None)], CompetitorFilters(), sell_price=17_859.0)
    assert rows == () and considered == 1


# --- how old the advert is ---------------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    ("24.07.2026 17:13", datetime(2026, 7, 24)),
    ("Today", NOW),
    ("Today 15:07", NOW),
    ("Yesterday", NOW - timedelta(days=1)),
    ("3 days ago", NOW - timedelta(days=3)),
    ("1 week ago", NOW - timedelta(weeks=1)),
    ("2 months ago", NOW - timedelta(days=60)),
    ("56 minutes ago", NOW - timedelta(minutes=56)),
    ("", None),
    ("who knows", None),
    ("32.13.2026 10:00", None),      # unparseable date, not a crash
])
def test_every_shape_bazaraki_writes_a_publish_date_in(raw, expected):
    """Relative forms anchor to the last sighting: an upsert overwrites
    ``posted_raw`` every crawl, so the string is whatever the site said then."""
    assert competitors._published(_advert(posted_raw=raw, last_seen_at=NOW)) == expected


def test_a_republished_advert_never_comes_out_younger():
    """The bug this clamp exists to prevent. ``posted_raw`` is a *bump* date —
    90 of 216 RAV4 adverts claimed to be published after the day we first saw
    them. Trusting it would paint the sellers who cannot sell as the freshest."""
    bumped = _advert(posted_raw="Today", last_seen_at=NOW,
                     first_seen_at=NOW - timedelta(days=60))
    assert competitors._first_listed(bumped) == NOW - timedelta(days=60)
    assert competitors._age_days(bumped, NOW) == 60


def test_a_publish_date_older_than_the_crawl_deepens_the_history():
    """The other half of the clamp, and the reason to read the field at all: it
    reaches back before bazaraki was first crawled for this car."""
    old = _advert(posted_raw="27.05.2026 09:00", last_seen_at=NOW,
                  first_seen_at=datetime(2026, 8, 9))
    assert competitors._first_listed(old) == datetime(2026, 5, 27)
    assert competitors._age_days(old, NOW) == 93


def test_age_stops_at_delisting_not_at_today():
    gone = _advert(first_seen_at=datetime(2026, 7, 24),
                   delisted_at=datetime(2026, 8, 9), is_active=False)
    assert competitors._age_days(gone, NOW) == 16


# --- what the age means ------------------------------------------------------


@pytest.mark.parametrize("age, active, expected", [
    (16, False, competitors.FAIR),         # gone in a fortnight: the price worked
    (30, False, competitors.FAIR),         # the threshold itself is still fair
    (31, False, competitors.OVERPRICED),   # took too long to go
    (3, True, competitors.UNPROVEN),       # still up, too soon to say
    (31, True, competitors.OVERPRICED),    # still up and nobody has bought it
    (None, True, competitors.UNPROVEN),    # no age is not a verdict
    (None, False, competitors.FAIR),
])
def test_the_three_readings(age, active, expected):
    assert competitors._mark(_advert(is_active=active), age) == expected


def test_a_departed_competitor_is_a_row_not_a_footnote():
    """The whole point: an advert that sold is the most useful thing on the page,
    because how long it took is the only direct evidence the price works."""
    rows, _ = competitors._rows_for(
        BAND,
        [_advert(ad_id=1, price=15_000.0, is_active=False,
                 first_seen_at=NOW - timedelta(days=9),
                 delisted_at=NOW - timedelta(days=1))],
        CompetitorFilters(), sell_price=17_859.0, now=NOW)

    assert [(row.ad_id, row.mark, row.gone) for row in rows] == [(1, competitors.FAIR, True)]


def test_a_competitor_that_left_months_ago_is_dropped_entirely():
    """It says nothing about who is ahead of you now; showing it grey would only
    invite it to be read against today's price."""
    rows, considered = competitors._rows_for(
        BAND,
        [_advert(ad_id=1, price=15_000.0, is_active=False,
                 delisted_at=NOW - timedelta(days=91))],
        CompetitorFilters(), sell_price=17_859.0, now=NOW)
    assert rows == () and considered == 0


def test_a_car_that_sold_inside_the_headroom_is_the_best_news_on_the_page():
    """It is the one fact that separates "I am next in the queue" from "the
    market ends below me", and a table drawn at the sell price could not show
    it. Which is why the ceiling is above the sell price and not on it."""
    rows, _ = competitors._rows_for(
        BAND,
        [_advert(ad_id=1, price=18_500.0, is_active=False,
                 first_seen_at=NOW - timedelta(days=9),
                 delisted_at=NOW - timedelta(days=1))],
        CompetitorFilters(), sell_price=17_859.0, now=NOW)

    assert [(row.ad_id, row.mark, row.gone) for row in rows] == [
        (1, competitors.FAIR, True)]
    assert rows[0].undercuts is False


def test_frozen_stock_past_the_ceiling_is_counted_even_though_it_cannot_be_a_row():
    """Without this the table reads as an easy sale while the market above it has
    not moved in a month."""
    live = [
        _advert(ad_id=1, price=19_000.0, first_seen_at=NOW - timedelta(days=45)),
        _advert(ad_id=2, price=20_000.0, first_seen_at=NOW - timedelta(days=60)),
        _advert(ad_id=3, price=19_500.0, first_seen_at=NOW - timedelta(days=5)),
        _advert(ad_id=4, price=15_000.0, first_seen_at=NOW - timedelta(days=90)),
    ]
    assert competitors._stuck_above(
        BAND, live, CompetitorFilters(), sell_price=17_859.0, now=NOW) == 2


def test_the_count_and_the_table_never_describe_the_same_advert():
    """The one thing that must be true of the two: they meet at the ceiling."""
    stale = _advert(ad_id=1, price=18_500.0, first_seen_at=NOW - timedelta(days=45))

    rows, _ = competitors._rows_for(
        BAND, [stale], CompetitorFilters(), sell_price=17_859.0, now=NOW)
    assert [row.ad_id for row in rows] == [1]      # inside the headroom: a row
    assert competitors._stuck_above(
        BAND, [stale], CompetitorFilters(), sell_price=17_859.0, now=NOW) == 0


# --- adverts dismissed by hand -----------------------------------------------


def test_a_manually_excluded_advert_is_still_a_row():
    """The point of the mark: bazaraki shows the advert under the same filters,
    so hiding it is how you end up investigating the same car twice."""
    rows, _ = competitors._rows_for(
        BAND,
        [_advert(ad_id=1, price=16_000.0, manual_exclusion_reason="order only")],
        CompetitorFilters(), sell_price=17_859.0, now=NOW)

    assert [row.ad_id for row in rows] == [1]
    assert rows[0].excluded and rows[0].exclusion_reason == "order only"


def test_a_manually_excluded_advert_keeps_its_place_in_the_price_order():
    """Recognition is the whole job: you meet it on bazaraki at its price."""
    rows, _ = competitors._rows_for(
        BAND,
        [_advert(ad_id=1, price=16_900.0),
         _advert(ad_id=2, price=15_200.0, manual_exclusion_reason="broker"),
         _advert(ad_id=3, price=16_100.0)],
        CompetitorFilters(), sell_price=17_859.0, now=NOW)

    assert [row.ad_id for row in rows] == [2, 3, 1]


def test_a_manually_excluded_advert_has_no_mark():
    """The three marks are verdicts about a price. A car nobody can buy has
    withdrawn from that judgement rather than earned a fourth reading."""
    rows, _ = competitors._rows_for(
        BAND,
        [_advert(price=16_000.0, first_seen_at=NOW - timedelta(days=90),
                 manual_exclusion_reason="duplicate advert")],
        CompetitorFilters(), sell_price=17_859.0, now=NOW)

    assert rows[0].mark == ""


def test_nothing_counts_a_manually_excluded_advert():
    """``considered`` is what "nothing under €17,859 — of N adverts" counts, and
    a dismissed advert must not hold that figure up either — nor the count of
    competitors, on whichever side of the sell price it sits."""
    rows, considered = competitors._rows_for(
        BAND,
        [_advert(ad_id=1, price=16_000.0, manual_exclusion_reason="order only"),
         _advert(ad_id=2, price=18_500.0, manual_exclusion_reason="broker"),
         _advert(ad_id=3, price=19_000.0)],
        CompetitorFilters(), sell_price=17_859.0, now=NOW)

    panel = competitors.BandPanel(band=BAND, max_bid_jpy=1_805_000, rows=rows,
                                  sell_price_eur=17_859.0)
    assert panel.competitors == () and panel.excluded == rows
    assert panel.undercutting == ()
    assert considered == 1


def test_a_manually_excluded_advert_is_not_frozen_stock_above_you():
    """Same panel, same judgement: a car available only by order is not the
    market refusing your price."""
    live = [
        _advert(ad_id=1, price=19_000.0, first_seen_at=NOW - timedelta(days=45)),
        _advert(ad_id=2, price=20_000.0, first_seen_at=NOW - timedelta(days=60),
                manual_exclusion_reason="order only"),
    ]
    assert competitors._stuck_above(
        BAND, live, CompetitorFilters(), sell_price=17_859.0, now=NOW) == 1


def test_a_manually_excluded_advert_drops_off_once_it_is_delisted():
    """A departed competitor stays for 90 days because how fast it went is
    evidence. A dismissed advert offers none, and once it is off bazaraki there
    is nothing left to recognise."""
    gone = _advert(price=16_000.0, is_active=False,
                   delisted_at=NOW - timedelta(days=2),
                   manual_exclusion_reason="order only")
    rows, considered = competitors._rows_for(
        BAND, [gone], CompetitorFilters(), sell_price=17_859.0, now=NOW)

    assert rows == () and considered == 0


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
