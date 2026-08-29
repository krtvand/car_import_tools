"""The statistics page: which stored sales become a band's five, and what the
page says when there are fewer than five.

The keepers are re-derived on every build rather than stored, so this is where
"tightening a requirement changes the page for free" is actually guarded — and
where the opposite failure is guarded too: a sale that no longer matches the
search still sitting on the page, looking measured.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from banzai24 import search
from banzai24.models import AuctionLot, SheetExtraction
from dashboard import cli, statistics

RAV4 = """
car = "toyota-rav4"

[site]
grade = ["4", "4.5", "5"]

[sheet]
no_damage_codes = ["W", "X"]

[auction_statistics]
model_grade = ["HYBRID G"]

[[band]]
year = 2023
body_model_code = ["AXAH54"]
mileage_end = 50000
max_bid_jpy = { private = 2_505_000 }
"""


@pytest.fixture
def definition(tmp_path):
    (tmp_path / "toyota-rav4.toml").write_text(RAV4, encoding="utf-8")
    return search.load("toyota-rav4", tmp_path)


def _lot(number="1-1-1", price=3_000_000, mileage=30_000, year=2023,
         modification="5D 4WD HYBRID G", code="AXAH54") -> AuctionLot:
    return AuctionLot(
        lot_number=number, lot_short=number.rsplit("-", 1)[-1], banzai_id="uuid",
        auction_id=1, auction_name="TAA Kinki", trade_date=date(2026, 8, 1),
        trade_time="10:00", mark="TOYOTA", model="RAV4",
        modification=modification, body_model_code=code,
        registration_year=year, mileage_km=mileage, grade_origin="4.5",
        end_price_jpy=price, discovered_by="stats", sheet_status="extracted",
    )


def _clean(lot: AuctionLot, marks=()) -> SheetExtraction:
    return SheetExtraction(
        lot_number=lot.lot_number, extracted_at=datetime.now(), model_id="m",
        sheet_sha256="abc", raw_json="{}",
        sheet_grade="4.5", sheet_mileage_km=lot.mileage_km,
        first_registration_year=lot.registration_year,
        damage_marks=json.dumps([{"panel": "roof", "code": c} for c in marks]),
    )


# --- which stored sales are this band's --------------------------------------


def test_a_sale_is_measured_against_the_band_that_would_have_priced_it(definition):
    band = definition.bands[0]
    assert statistics._in_band(_lot(mileage=50_000), band) is True
    assert statistics._in_band(_lot(mileage=50_001), band) is False
    assert statistics._in_band(_lot(year=2022), band) is False


def test_a_sale_missing_a_figure_is_in_no_band_at_all(definition):
    """Not the first one. Bands never overlap, and a guessed band would file a
    sale under a max bid it says nothing about."""
    band = definition.bands[0]
    assert statistics._in_band(_lot(year=None), band) is False
    assert statistics._in_band(_lot(mileage=None), band) is False


def test_the_trim_line_is_matched_the_way_the_site_matches_it(definition):
    """banzai24 treats `modelGrade` as a substring — "HYBRID G" returns lots
    spelled "5D 4WD HYBRID G". The page has to agree, or it drops the very lots
    the walk was told to collect."""
    filters = definition.stats_filters(definition.bands[0])
    assert statistics._matches_stats_filters(
        _lot(modification="5D 4WD HYBRID G"), definition.bands[0], filters) is True
    assert statistics._matches_stats_filters(
        _lot(modification="ADVENTURE"), definition.bands[0], filters) is False


def test_a_sale_is_measured_only_against_the_variant_its_band_prices(definition):
    """banzai24 writes both `AXAH54` and `6AA-AXAH54` for the same car — and the
    2WD AXAH52 is a different car at a different price, so it is not a benchmark
    for the E-Four's band whatever else it has in common with it."""
    band = definition.bands[0]
    filters = definition.stats_filters(band)
    assert statistics._matches_stats_filters(
        _lot(code="6AA-AXAH54"), band, filters) is True
    assert statistics._matches_stats_filters(
        _lot(code="AXAH52"), band, filters) is False


# --- which of them become the five -------------------------------------------


def test_the_cheapest_passing_sales_win_in_order(definition):
    lots = [_lot(f"1-1-{i}", price=p) for i, p in
            enumerate((3_300_000, 2_900_000, 3_100_000))]
    extractions = {lot.lot_number: _clean(lot) for lot in lots}
    panel = statistics._band_panel(definition, definition.bands[0], lots, extractions)
    assert [row.price_jpy for row in panel.rows] == [2_900_000, 3_100_000, 3_300_000]


def test_a_sale_the_sheet_disqualifies_is_counted_not_shown(definition):
    """The whole point of paying to read the sheet. An `XX` mark is a replaced
    panel, and `no_damage_codes = ["X"]` catches it as letters."""
    good, bad = _lot("1-1-1", price=3_100_000), _lot("1-1-2", price=2_900_000)
    extractions = {good.lot_number: _clean(good),
                   bad.lot_number: _clean(bad, marks=["XX"])}
    panel = statistics._band_panel(
        definition, definition.bands[0], [good, bad], extractions)
    assert [row.price_jpy for row in panel.rows] == [3_100_000]
    assert panel.failed == 1
    assert "1 failed a requirement" in panel.note


def test_an_unread_sheet_is_not_a_pass(definition):
    """Nothing has judged it, so it is not evidence of anything — and the page
    says how many are in that state rather than quietly showing four rows."""
    lot = _lot()
    panel = statistics._band_panel(definition, definition.bands[0], [lot], {})
    assert panel.rows == ()
    assert panel.unconfirmed == 1
    assert "1 sheet unread" in panel.note


def test_a_passing_sale_whose_price_never_revealed_is_not_a_benchmark(definition):
    """The page exists to show a number."""
    lot = _lot(price=None)
    panel = statistics._band_panel(
        definition, definition.bands[0], [lot], {lot.lot_number: _clean(lot)})
    assert panel.rows == ()
    assert panel.unconfirmed == 1


def test_a_band_nobody_has_measured_says_so_rather_than_nothing(definition):
    """An empty list is never rendered as good news."""
    panel = statistics._band_panel(definition, definition.bands[0], [], {})
    assert "not measured yet" in panel.note


def test_a_full_five_needs_no_footnote(definition):
    lots = [_lot(f"1-1-{i}", price=3_000_000 + i) for i in range(5)]
    extractions = {lot.lot_number: _clean(lot) for lot in lots}
    panel = statistics._band_panel(definition, definition.bands[0], lots, extractions)
    assert len(panel.rows) == statistics.KEEPERS
    assert panel.note is None


# --- the page ----------------------------------------------------------------


def _page(definition, **kw):
    band = statistics.BandPanel(band=definition.bands[0], **kw)
    return cli.render_statistics(statistics.Statistics(panels=(
        statistics.SearchPanel(name="toyota-rav4", bands=(band,),
                               measured_on=date(2026, 8, 27)),
    )))


def _row(price=2_960_000, uri=None):
    return statistics.BenchmarkRow(
        lot_number="1-1-1", lot_short="2388", price_jpy=price, mileage_km=31_000,
        grade="4.5", modification="HYBRID G 4WD", trade_date=date(2026, 8, 21),
        auction_name="TAA Kinki", url="https://banzai24.com/car/JP/uuid",
        sheet_uri=uri,
    )


def test_the_page_shows_the_sale_and_links_the_lot(definition):
    html = _page(definition, rows=(_row(),), stored=6)
    assert "2,960,000 ¥" in html
    assert "https://banzai24.com/car/JP/uuid" in html
    assert "TAA Kinki" in html


def test_the_page_carries_no_bid_to_compare_against(definition):
    """A sale price is a hammer price and a max bid is an all-in maximum. Side
    by side they compare different quantities and flatter the bid by an area
    price that differs per auction house."""
    html = _page(definition, rows=(_row(),), stored=6)
    assert "max bid" not in html.lower()
    assert "2,505,000" not in html      # the band's max_bid_jpy


def test_a_short_list_carries_its_reason_onto_the_page(definition):
    html = _page(definition, rows=(_row(),), stored=9, failed=8)
    assert "8 failed a requirement" in html


def test_the_sheet_is_inlined_so_the_page_stays_one_file(definition):
    html = _page(definition, rows=(_row(uri="data:image/jpeg;base64,AAAA"),), stored=6)
    assert "data:image/jpeg;base64,AAAA" in html
    assert "../stats/" not in html


def test_the_sheet_opens_full_size_on_click(definition):
    """A bare <img> is not clickable, and a sheet you cannot enlarge is a smudge:
    you open it to read damage codes off it."""
    html = _page(definition, rows=(_row(uri="data:image/jpeg;base64,AAAA"),), stored=6)
    assert 'id="sheet-1-1-1"' in html
    assert 'href="#sheet-1-1-1"' in html          # the thumbnail opens it
    assert 'class="shut" href="#"' in html        # and clicking away closes it


def test_the_enlarged_sheet_is_not_a_second_copy_of_the_bytes(definition):
    """`:target` restyles the one img. A page carrying each sheet twice would
    pay ~130 KB a row for a feature it already had the bytes for."""
    html = _page(definition, rows=(_row(uri="data:image/jpeg;base64,AAAA"),), stored=6)
    assert html.count("data:image/jpeg;base64,AAAA") == 1
