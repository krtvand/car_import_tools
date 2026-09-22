"""The search page: what sits at the top, what is folded away, and what it says
when there is nothing to bid on.

The cards themselves are `banzai24`'s and are tested there; what is tested here
is the page around them — the order, the fold, the empty state, and the fact
that a day with no lots still names its day.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from banzai24 import report as report_mod
from banzai24.models import AuctionLot
from dashboard import competitors, days, search_page, statistics


def _lot(number: str = "47-1316-35179", **overrides) -> AuctionLot:
    base = dict(lot_number=number, lot_short=number.rsplit("-", 1)[-1],
                banzai_id="0190-uuid", auction_id=47, auction_name="TAA Kinki",
                trade_date=date(2026, 9, 22), trade_time="10:30",
                mark="TOYOTA", model="HARRIER", mileage_km=21000,
                registration_year=2023, grade_origin="4.5")
    return AuctionLot(**{**base, **overrides})


def _report(views: list | None = None) -> report_mod.Report:
    """A report with no search definition: one ungrouped list, as collect() gives
    for a run that named no search. Enough to prove the cards reach the page."""
    return report_mod.Report(run_dir=Path("runs/x"), views=views or [])


def _view(lot=None) -> report_mod.LotView:
    view = report_mod.LotView(lot=lot or _lot())
    view.flags = report_mod._flags(view)
    return view


def _day(day: date | None = date(2026, 9, 22), lots=("a",), dropped=0) -> days.Day:
    return days.Day(date=day, run_dir=Path("runs/x"),
                    lots=tuple(days.Lot(number) for number in lots),
                    dropped=dropped)


def _page(**overrides) -> search_page.SearchPage:
    base = dict(name="toyota-harrier-z", car="Toyota Harrier",
                competitors_summary="11 competing adverts in Cyprus · 2 asking less",
                statistics_summary="10 cheapest acceptable sales",
                toml_text="# the whole search\ncar = \"toyota-harrier\"\n")
    return search_page.SearchPage(**{**base, **overrides})


def test_the_day_heads_the_everyday_block(): 
    html = search_page.render(_page(upcoming=((_day(), _report([_view()])),)))
    assert '<h2 class="day">Tue 22 Sep · 1 lot</h2>' in html
    assert 'class="lot' in html                      # the card itself
    assert "TAA Kinki" in html


def test_with_nothing_ahead_the_page_says_so_and_names_the_command():
    """Never a fallback to the last finished day: that is how you bid on a car
    that sold on Tuesday."""
    html = search_page.render(_page())
    assert "No upcoming lots" in html
    assert "uv run python -m banzai24 fetch --search toyota-harrier-z" in html


def test_a_day_that_kept_nothing_still_names_its_day():
    day = _day(lots=(), dropped=31)
    html = search_page.render(_page(upcoming=((day, _report([])),)))
    assert "Tue 22 Sep · no lots met the requirements — 31 dropped" in html


def test_a_fetch_that_found_no_day_says_that_instead_of_the_empty_state():
    html = search_page.render(_page(upcoming=((_day(day=None, lots=()), None),)))
    assert "no upcoming day found" in html
    assert "No upcoming lots" not in html


def test_the_past_link_sits_under_the_lots_not_in_the_header():
    html = search_page.render(_page())
    assert 'href="past.html"' in html
    assert html.index('href="past.html"') > html.index("No upcoming lots")


def test_the_back_link_climbs_out_of_the_search_folder():
    assert 'href="../../index.html"' in search_page.render(_page())


def test_the_weekly_blocks_are_folded_with_their_numbers_showing():
    """Closed by default, and the reading survives the fold."""
    html = search_page.render(_page())
    assert html.count('<details class="panel">') == 3
    assert "open>" not in html
    assert "11 competing adverts in Cyprus · 2 asking less" in html
    assert "10 cheapest acceptable sales" in html


def test_the_toml_is_printed_verbatim_comments_and_all():
    html = search_page.render(_page(toml_text='# a comment\nyear = 2023  # trailing\n'))
    assert "# a comment" in html
    assert "# trailing" in html


def test_the_toml_is_escaped_not_interpreted():
    html = search_page.render(_page(toml_text='phrase = "<script>alert(1)</script>"'))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_lot_count_is_every_kept_lot_on_the_days_ahead():
    page = _page(upcoming=((_day(lots=("a", "b")), _report()),
                           (_day(date(2026, 9, 23), lots=("c",)), _report())))
    assert page.lot_count == 3


# --- the summary lines on the closed folds ------------------------------------


def _band(**overrides) -> competitors.BandPanel:
    from searches.definition import Band

    band = Band(year=2023, mileage_start=0, mileage_end=50000,
                max_bid_jpy={"private": 2_700_000})
    return competitors.BandPanel(band=band, max_bid_jpy=2_700_000, **overrides)


def test_a_panel_nobody_priced_says_so_rather_than_zero():
    """"0 asking less" and "not priced yet" are opposite news."""
    panel = competitors.SearchPanel(name="mazda-3", bands=(_band(),))
    assert competitors.panel_summary(panel) == "not priced yet"


def test_a_priced_panel_carries_both_counts():
    panel = competitors.SearchPanel(name="x", bands=(_band(landed_eur=22_383.0, profit_eur=5_000.0, sell_price_eur=27_383.0),))
    assert competitors.panel_summary(panel) == (
        "0 competing adverts in Cyprus · 0 asking less than a cyprus sell price")


def test_a_broken_file_says_why_on_the_fold():
    panel = competitors.SearchPanel(name="x", problem="[band] needs an integer")
    assert competitors.panel_summary(panel) == "will not load: [band] needs an integer"


def test_an_unmeasured_search_is_not_a_search_with_no_sales():
    unmeasured = statistics.SearchPanel(name="toyota-rav4-g")
    assert statistics.panel_summary(unmeasured) == "not measured yet"
    measured = statistics.SearchPanel(name="x", measured_on=date(2026, 9, 19))
    assert statistics.panel_summary(measured) == "0 cheapest acceptable sales · measured 2026-09-19"


def test_a_panel_is_rendered_inside_its_fold_not_linked_to():
    """The weekly reading is on this page, folded — never a link to another one."""
    panel = competitors.SearchPanel(name="toyota-harrier-z", car="Toyota Harrier",
                                    bands=(_band(landed_eur=22_383.0, profit_eur=5_000.0, sell_price_eur=27_383.0),))
    html = search_page.render(_page(competitors=panel))
    assert "2023 · 0–50,000 km" in html      # the band's own heading
    assert "€27,383" in html
    assert "not on the competitors dashboard" not in html


def test_a_search_missing_from_a_dashboard_says_which_one():
    html = search_page.render(_page())
    assert "This search is not on the competitors dashboard." in html
    assert "This search has never been measured." in html


# --- the past page ------------------------------------------------------------


def _past(**overrides) -> search_page.PastPage:
    base = dict(name="toyota-harrier-z", car="Toyota Harrier")
    return search_page.PastPage(**{**base, **overrides})


def test_past_days_are_headed_and_carry_their_cards():
    page = _past(past=((_day(date(2026, 9, 19), lots=("a",)), _report([_view()])),))
    html = search_page.render_past(page)
    assert '<h2 class="day">Sat 19 Sep · 1 lot</h2>' in html
    assert 'class="lot' in html


def test_past_is_newest_first_as_days_hands_it_over():
    """Ordering is `days.past`'s; the page must not re-sort and disagree with it."""
    pairs = ((_day(date(2026, 9, 19)), _report()), (_day(date(2026, 9, 15)), _report()))
    html = search_page.render_past(_past(past=pairs))
    assert html.index("Sat 19 Sep") < html.index("Tue 15 Sep")


def test_an_empty_window_says_days_nobody_fetched_are_not_shown():
    """The honest version: a quiet market and a skipped morning look the same."""
    html = search_page.render_past(_past())
    assert "Nothing in the last 7 days" in html
    assert "auction calendar is stored" in html   # line-wrapped in the template


def test_the_past_page_climbs_back_to_the_search_and_to_the_index():
    html = search_page.render_past(_past())
    assert 'href="index.html"' in html
    assert 'href="../../index.html"' in html


def test_nothing_on_the_past_page_reaches_older_runs():
    """Seven days is the whole UI; there is no archive and no link to one."""
    html = search_page.render_past(_past(past=((_day(date(2026, 9, 19)), _report([_view()])),)))
    assert "report.html" not in html
    assert "older" not in html.lower()


def test_the_window_is_printed_where_it_is_promised():
    html = search_page.render_past(_past(window=7))
    assert "last 7 days" in html
    assert 'href="past.html"' not in html      # it *is* the past page
