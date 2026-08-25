"""Rendering the competitors page: what a reader is told when there is no answer.

The template is a layout and is not tested for its own sake. What is tested is
that the three "we cannot answer this" states actually reach the HTML — a
mis-edited search, a band with nothing declared, and a band the market will not
pay for — because each of them would otherwise render as a short list, and a
short list reads as good news.
"""
from __future__ import annotations

from searches.definition import Band, CompetitorBounds

from dashboard import cli, competitors

BAND = Band(year=2023, mileage_start=0, mileage_end=50_000,
            max_bid_jpy={"private": 1_805_000},
            competitors=CompetitorBounds(year_start=2019, mileage_end=120_000))


def _row(**overrides) -> competitors.CompetitorRow:
    base = dict(ad_id=1, url="https://bazaraki.com/adv/1", title="Mazda CX-30",
                price=16_000.0, under_by=1_859.0, year=2021, mileage_km=80_000,
                fuel_type="Petrol", gearbox="Automatic", seller_type="private",
                days_on_market=40)
    return competitors.CompetitorRow(**{**base, **overrides})


def _dashboard(*panels) -> competitors.Dashboard:
    return competitors.Dashboard(panels=panels, rates="¥185/€", costs="Aug 2026")


def test_a_priced_band_shows_all_three_numbers_and_the_advert():
    """The sell price, the estimate, and the link. The comparison between the
    first two is why the estimate is on the page at all."""
    html = cli.render(_dashboard(competitors.SearchPanel(
        name="mazda-cx30", car="Mazda CX-30",
        bands=(competitors.BandPanel(
            band=BAND, max_bid_jpy=1_805_000, landed_eur=15_859.0,
            profit_eur=2_000.0, sell_price_eur=17_859.0,
            cyprus_estimate_eur=20_705.0, competitors=(_row(),), considered=119,
            sold=competitors.Sold(count=3, median_price=21_000.0)),))))

    assert "€17,859" in html and "€20,705" in html and "€15,859" in html
    assert "https://bazaraki.com/adv/1" in html
    assert "−€1,859" in html
    assert "3 delisted in the last 30 days" in html


def test_an_empty_list_says_what_it_looked_at():
    """"Nothing under €17,859" is only good news if you know how many adverts
    were considered to reach it."""
    html = cli.render(_dashboard(competitors.SearchPanel(
        name="mazda-cx30", car="Mazda CX-30",
        bands=(competitors.BandPanel(
            band=BAND, max_bid_jpy=1_805_000, landed_eur=15_859.0,
            profit_eur=2_000.0, sell_price_eur=17_859.0,
            cyprus_estimate_eur=20_705.0, considered=119),))))

    assert "Nothing under €17,859" in html
    assert "119 adverts inside" in html


def test_a_band_with_nothing_declared_says_so_instead_of_showing_a_list():
    html = cli.render(_dashboard(competitors.SearchPanel(
        name="mazda-cx5", car="Mazda CX-5",
        bands=(competitors.BandPanel(
            band=BAND, max_bid_jpy=1_805_000,
            problem="no expected_profit_eur — add one under [dashboard]"),))))

    assert "no expected_profit_eur" in html
    assert "Nothing under" not in html


def test_a_search_that_will_not_load_still_gets_a_section():
    """Hiding it is how it gets forgotten — the runs index makes the same
    argument about a run it cannot open."""
    html = cli.render(_dashboard(competitors.SearchPanel(
        name="mazda-3", problem="mazda-3.toml: bands 2023 and 2023 overlap")))
    assert "will not load" in html and "overlap" in html


def test_an_underwater_band_is_called_out_above_its_list():
    html = cli.render(_dashboard(competitors.SearchPanel(
        name="mazda-cx30", car="Mazda CX-30",
        bands=(competitors.BandPanel(
            band=BAND, max_bid_jpy=1_805_000, landed_eur=20_000.0,
            profit_eur=2_000.0, sell_price_eur=22_000.0,
            cyprus_estimate_eur=20_705.0, competitors=(_row(),)),))))

    assert "not work at €2,000 profit" in html
    assert 'class="band underwater"' in html


def test_a_command_in_a_note_is_rendered_as_code_and_still_escaped():
    """The notes double as terminal output, so they are written with backticks —
    and advert titles beside them are free text off a public website."""
    html = cli.render(_dashboard(competitors.SearchPanel(
        name="mazda-3",
        coverage="no completed crawl — run `bazaraki scrape --search mazda-3`")))
    assert "<code>bazaraki scrape --search mazda-3</code>" in html

    escaped = cli.render(_dashboard(competitors.SearchPanel(
        name="x", car="Mazda CX-30",
        bands=(competitors.BandPanel(
            band=BAND, max_bid_jpy=1, landed_eur=1.0, profit_eur=1.0,
            sell_price_eur=3.0, cyprus_estimate_eur=9.0,
            competitors=(_row(title="<script>alert(1)</script>"),)),))))
    assert "<script>alert(1)</script>" not in escaped
    assert "&lt;script&gt;" in escaped


def test_the_index_summary_separates_adverts_found_from_searches_unpriced():
    """"0 competitors" and "3 searches not configured" mean opposite things."""
    summary = cli._summary(_dashboard(
        competitors.SearchPanel(name="a", bands=(competitors.BandPanel(
            band=BAND, max_bid_jpy=1, sell_price_eur=3.0,
            competitors=(_row(),)),)),
        competitors.SearchPanel(name="b", bands=(competitors.BandPanel(
            band=BAND, max_bid_jpy=1),)),
    ))
    assert "1 advert asking less" in summary
    assert "1 search not priced yet" in summary
