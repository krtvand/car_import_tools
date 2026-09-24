"""The statistics page: which stored sales become a band's five, and what the
page says when there are fewer than five.

The keepers are re-derived on every build rather than stored, so this is where
"tightening a requirement changes the page for free" is actually guarded — and
where the opposite failure is guarded too: a sale that no longer matches the
search still sitting on the page, looking measured.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from banzai24 import search
from banzai24.models import AuctionLot, SheetExtraction
from cars.specs import ModelSpecs
from dashboard import search_page, statistics
from price_calculator.calculator import Rates
from price_calculator.sources import load_cost_book, margin_for

# The shipped book and a fixed pair of rates. Nothing below asserts a euro
# figure — the book is edited whenever somebody's fee changes — only what went
# into it, which is the decision this module makes.
COSTS = load_cost_book()
RATES = Rates(usd_jpy=Decimal("158.9"), eur_jpy_market=Decimal("185.6"),
              fetched_at=datetime(2026, 8, 22, tzinfo=timezone.utc))

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
    band = definition.bands[0]
    filters = definition.stats_filters(band)
    lot_filters = definition.lot_filters_for(band)
    assert statistics._matches_stats_filters(
        _lot(modification="5D 4WD HYBRID G"), filters, lot_filters) is True
    assert statistics._matches_stats_filters(
        _lot(modification="ADVENTURE"), filters, lot_filters) is False


def test_a_sale_is_measured_only_against_the_variant_its_band_prices(definition):
    """banzai24 writes both `AXAH54` and `6AA-AXAH54` for the same car — and the
    2WD AXAH52 is a different car at a different price, so it is not a benchmark
    for the E-Four's band whatever else it has in common with it."""
    band = definition.bands[0]
    filters = definition.stats_filters(band)
    lot_filters = definition.lot_filters_for(band)
    assert statistics._matches_stats_filters(
        _lot(code="6AA-AXAH54"), filters, lot_filters) is True
    assert statistics._matches_stats_filters(
        _lot(code="AXAH52"), filters, lot_filters) is False


def test_an_exclusion_added_today_drops_a_sale_stored_before_it(tmp_path):
    """The failure that made this test exist: a RAV4 file split into a G half
    and an X half kept showing `HYBRID X 4WD` under the G search, because the
    walk had stored those sales back when nothing banned them. `[api]` is the
    file's own exclusion and the page must re-apply it, not trust the fetch."""
    (tmp_path / "toyota-rav4-g.toml").write_text(
        RAV4.replace("[sheet]", '[api]\nexclude_model_grades = ["X"]\n\n[sheet]')
            .replace('model_grade = ["HYBRID G"]', ""), encoding="utf-8")
    definition = search.load("toyota-rav4-g", tmp_path)
    band = definition.bands[0]
    filters = definition.stats_filters(band)
    lot_filters = definition.lot_filters_for(band)

    assert statistics._matches_stats_filters(
        _lot(modification="HYBRID X 4WD"), filters, lot_filters) is False
    assert statistics._matches_stats_filters(
        _lot(modification="HYBRID G 4WD"), filters, lot_filters) is True
    # A sale nobody wrote a trim line for is still a sale: an exclusion drops
    # only what it can positively recognise.
    assert statistics._matches_stats_filters(
        _lot(modification="4WD"), filters, lot_filters) is True


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


# --- landing a sale ----------------------------------------------------------


def _landed(price_jpy: int) -> float:
    """What `price_jpy` lands at, through the calculator directly."""
    margin = margin_for(make="TOYOTA", model="RAV4", year=2023, mileage_km=30_000,
                        auction_price_jpy=price_jpy, rates=RATES, costs=COSTS,
                        specs=ModelSpecs(), market=None)
    return float(margin.landed.total_eur)


def test_a_sale_is_landed_from_the_hammer_plus_the_houses_area_price():
    """Every yen paid in Japan is in the customs value the VAT is charged on, so
    the area price is inside the euro and not deducted from it. Landing the
    hammer alone would flatter every row on the page."""
    eur, reason, area = statistics.LandedPricer(RATES, COSTS).for_lot(
        _lot(price=3_000_000))                       # TAA Kinki, ¥5,500
    assert reason is None and area == 5_500
    assert eur == pytest.approx(_landed(3_005_500))
    assert eur > _landed(3_000_000)


def test_a_sale_from_a_house_with_no_area_price_is_not_landed_at_the_hammer():
    """It says which house instead. A euro short by an unknown area price is
    worse than a blank one, because nothing on the page would show it."""
    lot = AuctionLot(**{**_lot().__dict__, "auction_name": "SOME NEW AA"})
    eur, reason, area = statistics.LandedPricer(RATES, COSTS).for_lot(lot)
    assert eur is None and area is None
    assert "SOME NEW AA" in reason


def test_no_exchange_rates_blanks_the_euro_and_says_so():
    eur, reason, _ = statistics.LandedPricer(None, None).for_lot(_lot())
    assert eur is None and "no exchange rates" in reason


def test_the_keepers_do_not_need_money_to_be_worked_out(definition):
    """The five cheapest are re-derived from the search file, and a build with no
    rates still has them — it just has no euro beside them."""
    lots = [_lot(f"1-1-{i}", price=3_000_000 + i) for i in range(5)]
    panel = statistics._band_panel(definition, definition.bands[0], lots,
                                   {lot.lot_number: _clean(lot) for lot in lots})
    assert len(panel.rows) == statistics.KEEPERS
    assert all(row.landed_eur is None for row in panel.rows)


# --- the page ----------------------------------------------------------------


def _page(definition, **kw):
    """One panel, through the macro the search page folds away.

    There is no statistics *page* any more — the eight-panel version is what the
    10 MB file was — so what is rendered here is one search's section of it.
    """
    band = statistics.BandPanel(band=definition.bands[0], **kw)
    macro = search_page._environment().get_template("_statistics.html.j2").module.panel
    return str(macro(statistics.SearchPanel(
        name="toyota-rav4", bands=(band,), measured_on=date(2026, 8, 27))))


def _row(price=2_960_000, uri=None, **kw):
    return statistics.BenchmarkRow(
        lot_number="1-1-1", lot_short="2388", price_jpy=price, mileage_km=31_000,
        grade="4.5", modification="HYBRID G 4WD", trade_date=date(2026, 8, 21),
        auction_name="TAA Kinki", url="https://banzai24.com/car/JP/uuid",
        sheet_uri=uri, **{"landed_eur": 21_480.0, "area_price_jpy": 5_500, **kw},
    )


def test_the_page_shows_the_sale_and_links_the_lot(definition):
    html = _page(definition, rows=(_row(),), stored=6)
    assert "2,960,000 ¥" in html
    assert "https://banzai24.com/car/JP/uuid" in html


def test_the_page_shows_what_the_sale_would_have_landed_at(definition):
    """The yen is what the market paid; the euro is what it would have cost you,
    and it is the only figure on the page comparable with a competitor."""
    html = _page(definition, rows=(_row(),), stored=6)
    assert "€21,480" in html


def test_the_auction_house_keeps_no_column_and_is_named_on_the_euro(definition):
    """Its area price is inside the landed figure, so the house belongs to that
    cell rather than to one of its own."""
    html = _page(definition, rows=(_row(),), stored=6)
    assert "<th>landed</th>" in html and "<th>auction</th>" not in html
    assert 'title="TAA Kinki · area price 5,500 ¥"' in html


def test_a_sale_that_cannot_be_landed_says_why_where_the_euro_was(definition):
    """Never a blank cell and never a guessed euro — the same rule the bid
    column follows on a card."""
    html = _page(definition, rows=(_row(landed_eur=None, area_price_jpy=None,
                                        landed_reason="unknown auction house: TAA Kinki"),),
                 stored=6)
    assert "unknown auction house: TAA Kinki" in html
    assert "€" not in html


# --- the band's own ceiling --------------------------------------------------


def test_the_max_bid_is_landed_all_in_with_no_area_price_added(definition):
    """A `max_bid_jpy` is *already* hammer plus the house's area price, so the
    step a sale needs would be charged twice here. This is the one place the two
    pricings differ, and getting it wrong would push the ceiling above itself by
    every area price on the list."""
    eur, reason = statistics.LandedPricer(RATES, COSTS).for_band(
        definition, definition.bands[0])          # max_bid_jpy = 2,505,000
    assert reason is None
    assert eur == pytest.approx(_landed(2_505_000))
    assert eur < _landed(2_505_000 + 5_500)


def test_a_band_carries_its_ceiling_whether_or_not_anything_sold(definition):
    """The number is the search file's, not the walk's. A band nobody has
    measured is exactly where knowing what you are willing to pay reads for
    most, so it is never withheld for want of sales to sit under."""
    panel = statistics._band_panel(definition, definition.bands[0], [], {},
                                   statistics.LandedPricer(RATES, COSTS))
    assert panel.rows == ()
    assert panel.max_bid.price_jpy == 2_505_000
    assert panel.max_bid.landed_eur == pytest.approx(_landed(2_505_000))


def test_no_exchange_rates_blanks_the_ceilings_euro_too(definition):
    """The same rule as a sale: a sentence where the number would have been,
    never a euro nobody could compute."""
    eur, reason = statistics.LandedPricer(None, None).for_band(
        definition, definition.bands[0])
    assert eur is None and "no exchange rates" in reason


def test_the_page_prints_the_max_bid_below_the_sales(definition):
    """Both figures, and the euro is the one that compares: a hammer price and
    an all-in maximum are different quantities in yen, and the same quantity
    once each is on Cyprus plates."""
    html = _page(definition, rows=(_row(),), stored=6,
                 max_bid=statistics.MaxBidRow(price_jpy=2_505_000,
                                              landed_eur=18_900.0))
    foot = html.split("<tfoot>")[1]
    assert "max bid" in foot
    assert "2,505,000 ¥" in foot and "€18,900" in foot


def test_the_max_bid_row_fills_in_none_of_a_sales_fields(definition):
    """Empty, not em-dashed. An em-dash on a sale means nobody typed the figure;
    there is no mileage, grade or lot to type here, because nothing was bought
    at this price."""
    html = _page(definition, rows=(_row(),), stored=6,
                 max_bid=statistics.MaxBidRow(price_jpy=2_505_000,
                                              landed_eur=18_900.0))
    rest = html.split("€18,900")[1]          # everything after the landed cell
    assert "—" not in rest and "km" not in rest and "<a " not in rest
    assert "<td></td><td></td><td></td><td></td><td></td>" in rest


def test_a_ceiling_that_cannot_be_landed_says_why_where_the_euro_was(definition):
    html = _page(definition, rows=(), stored=0,
                 max_bid=statistics.MaxBidRow(
                     price_jpy=2_505_000, landed_reason="no model spec for TOYOTA RAV4 2023"))
    assert "no model spec for TOYOTA RAV4 2023" in html
    assert "€" not in html


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
