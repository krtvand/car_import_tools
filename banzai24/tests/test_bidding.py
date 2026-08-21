"""Computing ``bid_reduced``, and refusing to compute it.

The bid price is the one number on the report that spends money, so the tests
that matter here are the ones about *not* producing it: a band edge off by a
kilometre, a year that is not in the table, a house whose name is spelled
differently in two files. Each of those must come back as a reason on the card —
never a guessed number, and never an exception that costs you the whole report.

The lookup is pure, so all of this runs against three-line fixture CSVs written
into ``tmp_path``; nothing here touches the real tables, the database, or a
rendered page.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from banzai24 import bidding
from banzai24.models import AuctionLot, SheetExtraction

BIDS_HEADER = "make,model,year,mileage_min,mileage_max,rental,max_bid_jpy"
AREA_HEADER = "AUCTION NAME,AREA PRICE $,AREA PRICE JPY"


# --- fixtures ----------------------------------------------------------------


def _write(path: Path, *lines: str) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _tables(tmp_path, bids=(), areas=(), aliases=(), title=True) -> dict:
    """The three CSVs on disk, as ``BidPricer`` keyword arguments.

    ``title=True`` writes the Numbers-style sheet-name line above every header,
    because that is what a fresh export actually looks like — the parser has to
    survive it in the normal case, not only in the test that checks for it.
    """
    bids = bids or ("MAZDA,CX-30,2023,0,50000,private,1855000",)
    areas = areas or ("USS TOKYO,110,12000",)
    aliases = aliases or ("U Tokyo,USS TOKYO",)
    lead = ("some_export_2026",) if title else ()

    return {
        "bid_prices_path": _write(tmp_path / "bids.csv", *lead, BIDS_HEADER, *bids),
        "area_prices_path": _write(tmp_path / "areas.csv", *lead, AREA_HEADER, *areas),
        "aliases_path": _write(tmp_path / "aliases.csv", *lead,
                               "db_name,area_price_name", *aliases),
    }


def _lot(**overrides) -> AuctionLot:
    base = {
        "lot_number": "39-1850-33152", "lot_short": "33152",
        "banzai_id": "019fded9-2a2a-714c-96e3-fcff2823fcb7",
        "auction_id": 39, "auction_name": "U Tokyo",
        "trade_date": date(2026, 8, 12), "trade_time": "12:00",
        "mark": "MAZDA", "model": "CX-30", "registration_year": 2023,
        "mileage_km": 15000,
    }
    return AuctionLot(**{**base, **overrides})


def _extraction(**overrides) -> SheetExtraction:
    base = {
        "lot_number": "39-1850-33152",
        "extracted_at": None, "model_id": "claude-opus-5",
        "sheet_sha256": "abc", "raw_json": "{}",
        "private_car_note": "自家用",
    }
    return SheetExtraction(**{**base, **overrides})


def _pricer(tmp_path, **kwargs) -> bidding.BidPricer:
    return bidding.BidPricer(**_tables(tmp_path, **kwargs))


# --- the number ---------------------------------------------------------------


def test_bid_reduced_is_the_max_bid_less_the_houses_area_cost(tmp_path):
    """The whole feature in one assertion: ¥1,855,000 − ¥12,000."""
    quote = _pricer(tmp_path).for_lot(_lot(), _extraction())

    assert quote.max_bid == 1_855_000
    assert quote.extra_costs == 12_000
    assert quote.bid_reduced == 1_843_000
    assert quote.reason is None


def test_the_money_block_names_all_three_numbers_and_the_house(tmp_path):
    """Formatted here rather than in the template, so it is asserted without
    rendering — and so the page stays a layout."""
    quote = _pricer(tmp_path).for_lot(_lot(), _extraction())
    assert quote.describe() == (
        "max bid ¥ 1855 000 · area (U Tokyo) −¥ 12 000 · bid reduced ¥ 1843 000"
    )


def test_a_null_bid_reduced_prints_its_reason_where_the_number_was(tmp_path):
    """The two numbers that *are* known still print — knowing the house costs
    ¥12,000 is useful on a card that cannot price the car."""
    quote = _pricer(tmp_path).for_lot(_lot(registration_year=2017), _extraction())

    assert quote.bid_reduced is None
    assert quote.extra_costs == 12_000
    assert quote.describe() == (
        "max bid — · area (U Tokyo) −¥ 12 000 · "
        "no table row for MAZDA CX-30 2017 · 15,000 km · private"
    )


# --- band edges ---------------------------------------------------------------


@pytest.mark.parametrize("mileage, priced", [
    (0, True),          # the bottom edge is in the band
    (50_000, True),     # …and so is the top one — inclusive at both ends
    (50_001, False),    # one kilometre past it, and there is no row
])
def test_mileage_bands_are_inclusive_at_both_ends(tmp_path, mileage, priced):
    quote = _pricer(tmp_path).for_lot(_lot(mileage_km=mileage), _extraction())
    assert (quote.bid_reduced is not None) is priced


def test_a_blank_mileage_max_is_open_ended(tmp_path):
    pricer = _pricer(tmp_path, bids=("MAZDA,CX-30,2023,50001,,private,1200000",))
    quote = pricer.for_lot(_lot(mileage_km=310_000), _extraction())
    assert quote.max_bid == 1_200_000


def test_the_year_must_match_exactly_rather_than_borrowing_a_neighbour(tmp_path):
    """2022 does not read 2023's price. An unpriced year is a null, because the
    alternative is a number that looks authored and is not."""
    quote = _pricer(tmp_path).for_lot(_lot(registration_year=2022), _extraction())
    assert quote.bid_reduced is None
    assert quote.reason == "no table row for MAZDA CX-30 2022 · 15,000 km · private"


def test_rental_and_private_are_priced_separately(tmp_path):
    pricer = _pricer(tmp_path, bids=(
        "MAZDA,CX-30,2023,0,50000,private,1855000",
        "MAZDA,CX-30,2023,0,50000,rental,1400000",
    ))
    private = pricer.for_lot(_lot(), _extraction())
    rental = pricer.for_lot(_lot(), _extraction(private_car_note=None,
                                                rental_car_note="レンタカー"))
    assert private.max_bid == 1_855_000
    assert rental.max_bid == 1_400_000


# --- matching the auction house ----------------------------------------------


def test_an_alias_reaches_the_cost_row_the_fold_cannot(tmp_path):
    """``U Tokyo`` matches nothing in a file offering USS/JU/NPS/CAA/LUM/NAA/ZIP
    TOKYO — seven wrong answers next to the right one, so it is stated in a file
    rather than inferred."""
    pricer = _pricer(tmp_path,
                     areas=("USS TOKYO,110,12000", "JU TOKYO,110,9000"),
                     aliases=("U Tokyo,USS TOKYO",))
    assert pricer.for_lot(_lot(), _extraction()).extra_costs == 12_000


def test_the_fold_alone_matches_a_house_spelled_with_a_space(tmp_path):
    """``BAY AUC`` and ``BAYAUC`` need no alias row — which is why the file
    carries six houses and not seventeen."""
    pricer = _pricer(tmp_path, areas=("BAYAUC,35,4000",))
    quote = pricer.for_lot(_lot(auction_name="BAY AUC"), _extraction())
    assert quote.extra_costs == 4_000


def test_an_unknown_house_is_a_reason_not_an_error(tmp_path):
    """banzai24 adds houses over time; a report must not start failing when
    they do."""
    quote = _pricer(tmp_path).for_lot(_lot(auction_name="NEW AA Sapporo"),
                                      _extraction())
    assert quote.reason == "unknown auction house: NEW AA Sapporo"
    assert quote.bid_reduced is None
    assert quote.extra_costs is None


def test_an_alias_pointing_at_a_house_the_cost_file_lacks_is_not_an_error(tmp_path):
    """Same landing place as a brand-new house: a reason on the card."""
    pricer = _pricer(tmp_path, aliases=("U Tokyo,USS ATLANTIS",))
    assert pricer.for_lot(_lot(), _extraction()).reason == "unknown auction house: U Tokyo"


# --- what the lot does not know ----------------------------------------------


def test_a_sheet_that_says_neither_rental_nor_private_is_priced_as_private(tmp_path):
    """The common case — and the one assumption this module makes.

    It used to be a null: a company car and an unread sheet both came back with
    no number at all. That left the commonest card on the page needing to be
    priced by hand from a table sitting right there. The assumption is stated on
    the quote rather than hidden, because private is the *dearer* row — it is not
    the cautious choice, it is the only one that always resolves, since some cars
    have no rental row at all.
    """
    quote = _pricer(tmp_path).for_lot(_lot(), _extraction(private_car_note=None))
    assert quote.max_bid == 1_855_000
    assert quote.assumed_private is True
    assert "assuming private, sheet did not say" in quote.describe()

    unread = _pricer(tmp_path).for_lot(_lot(), None)
    assert unread.max_bid == 1_855_000
    assert unread.assumed_private is True


def test_a_sheet_that_does_say_is_not_marked_as_an_assumption(tmp_path):
    """The flag has to distinguish, or it is decoration on every card."""
    quote = _pricer(tmp_path).for_lot(_lot(), _extraction())
    assert quote.assumed_private is False
    assert "assuming private" not in quote.describe()


def test_the_sheet_outranks_the_api_on_year_and_mileage(tmp_path):
    """The reversal recorded in ``docs/adr/0001-sheet-outranks-api.md``.

    The API rounds mileage to the nearest 1,000 and the bands match to the
    kilometre, so under the old precedence a car whose sheet read 50,415 km and
    whose API row read 50,000 km was priced from the under-50,000 band — while
    the report's own requirement check judged it on the exact figure. One card
    cannot read its bid off a rounded copy and its mileage off the original.
    """
    pricer = _pricer(tmp_path)
    quote = pricer.for_lot(
        _lot(registration_year=None, mileage_km=None),
        _extraction(first_registration_year=2023, sheet_mileage_km=15_415),
    )
    assert quote.bid_reduced == 1_843_000

    # Both present: the sheet's figure displaces the API's, and 99,999 km falls
    # outside every band in the fixture table.
    sheet_wins = pricer.for_lot(_lot(mileage_km=15_000),
                                _extraction(sheet_mileage_km=99_999))
    assert sheet_wins.max_bid is None
    assert "99,999 km" in sheet_wins.reason


def test_a_sheet_null_is_not_a_zero(tmp_path):
    """Where the sheet is silent the API's value stands, unchanged."""
    quote = _pricer(tmp_path).for_lot(
        _lot(mileage_km=15_000, registration_year=2023),
        _extraction(sheet_mileage_km=None, first_registration_year=None),
    )
    assert quote.max_bid == 1_855_000


@pytest.mark.parametrize("missing, reason", [
    ({"registration_year": None}, "missing year"),
    ({"mileage_km": None}, "missing mileage"),
])
def test_a_lot_missing_a_key_field_says_which_one(tmp_path, missing, reason):
    quote = _pricer(tmp_path).for_lot(_lot(**missing), _extraction())
    assert quote.reason == reason


def test_an_area_cost_above_the_max_bid_is_not_a_bid_of_zero(tmp_path):
    """Nor a negative number. It is "do not buy this car at this house", and it
    must not look like a price."""
    pricer = _pricer(tmp_path,
                     bids=("MAZDA,CX-30,2023,0,50000,private,30000",),
                     areas=("USS TOKYO,365,47000",),
                     aliases=("U Tokyo,USS TOKYO",))
    quote = pricer.for_lot(_lot(), _extraction())

    assert quote.bid_reduced is None
    assert quote.reason == "area cost ¥ 47 000 exceeds max bid ¥ 30 000"
    assert quote.max_bid == 30_000 and quote.extra_costs == 47_000


# --- missing and malformed tables --------------------------------------------


def test_an_absent_table_costs_the_column_and_not_the_report(tmp_path):
    tables = _tables(tmp_path)
    tables["bid_prices_path"].unlink()

    pricer = bidding.BidPricer(**tables)

    assert pricer.available is False
    assert pricer.reason == "bid prices not loaded"
    assert pricer.for_lot(_lot(), _extraction()) is None


def test_an_absent_area_table_says_which_file_is_gone(tmp_path):
    tables = _tables(tmp_path)
    tables["area_prices_path"].unlink()
    assert bidding.BidPricer(**tables).reason == "area prices not loaded"


def test_absent_aliases_are_silent_because_the_fold_still_works(tmp_path):
    tables = _tables(tmp_path, areas=("BAYAUC,35,4000",))
    tables["aliases_path"].unlink()

    pricer = bidding.BidPricer(**tables)

    assert pricer.available is True and pricer.reason is None
    assert pricer.for_lot(_lot(auction_name="BAY AUC"), _extraction()).extra_costs == 4_000


def test_a_mis_edited_alias_file_is_said_out_loud_but_suppresses_nothing(tmp_path):
    """The aliases are an accelerator, not an input. Breaking the file costs the
    six houses it covers, not the whole bid column — so it is reported without
    switching the pricer off."""
    tables = _tables(tmp_path,
                     areas=("BAYAUC,35,4000",),
                     aliases=("U Tokyo,USS TOKYO", "U TOKYO,JU TOKYO"))

    pricer = bidding.BidPricer(**tables)

    assert pricer.available is True
    assert "aliased twice" in pricer.reason
    assert pricer.for_lot(_lot(auction_name="BAY AUC"), _extraction()).extra_costs == 4_000


def test_two_rows_that_could_both_match_one_car_are_caught_at_load(tmp_path):
    """Checked as band overlap rather than "this lot matched twice": at load
    there is no lot, and a shadowed row is a wrong price you never see."""
    path = _write(tmp_path / "bids.csv", BIDS_HEADER,
                  "MAZDA,CX-30,2023,0,50000,private,1855000",
                  "MAZDA,CX-30,2023,40000,60000,private,1705000")

    with pytest.raises(bidding.BidTableError) as caught:
        bidding.load_bid_prices(path)

    assert "lines 2 and 3" in str(caught.value)


def test_rows_differing_only_in_rental_never_conflict(tmp_path):
    path = _write(tmp_path / "bids.csv", BIDS_HEADER,
                  "MAZDA,CX-30,2023,0,50000,private,1855000",
                  "MAZDA,CX-30,2023,0,50000,rental,1400000")
    assert len(bidding.load_bid_prices(path)) == 2


@pytest.mark.parametrize("row, complaint", [
    ("MAZDA,CX-30,2023,0,50000,,1855000", "rental must be one of"),
    ("MAZDA,CX-30,2023,0,50000,ex-fleet,1855000", "rental must be one of"),
    ("MAZDA,CX-30,20xx,0,50000,private,1855000", "year is not a whole number"),
    ("MAZDA,CX-30,2023,0,50000,private,", "max_bid_jpy is empty"),
    ("MAZDA,CX-30,2023,60000,50000,private,1855000", "is below mileage_min"),
])
def test_an_unusable_row_names_the_line_and_the_column(tmp_path, row, complaint):
    """A blank ``rental`` is a typo, not a wildcard: a lot whose sheet says
    neither never consults the table, so the row could never match."""
    path = _write(tmp_path / "bids.csv", BIDS_HEADER, row)

    with pytest.raises(bidding.BidTableError) as caught:
        bidding.load_bid_prices(path)

    assert "line 2" in str(caught.value)
    assert complaint in str(caught.value)


def test_a_mis_edited_table_is_reported_on_the_page_not_raised_at_the_reader(tmp_path):
    """Loud, but not fatal: the parser's own complaint reaches the header, so
    the broken edit is named rather than silently dropped — and the other
    sixty-one cards still render."""
    tables = _tables(tmp_path, bids=("MAZDA,CX-30,2023,0,50000,ex-fleet,1855000",))

    pricer = bidding.BidPricer(**tables)

    assert pricer.available is False
    assert pricer.reason.startswith("bid prices not loaded: ")
    assert "rental must be one of" in pricer.reason
    assert pricer.for_lot(_lot(), _extraction()) is None


# --- reading the files as they actually arrive -------------------------------


def test_a_numbers_export_drops_in_with_its_title_line(tmp_path):
    """Line 1 of a fresh export is the sheet's own name. Hand-deleting it before
    every re-export is a step that gets forgotten once and then produces a
    report with no prices on it."""
    path = _write(tmp_path / "areas.csv", "auction_area_prices_2026",
                  AREA_HEADER, "ARAI BAYSIDE,35,4000")
    assert bidding.load_area_prices(path) == {"araibayside": 4000}


def test_a_recased_header_reads_the_rows_rather_than_silently_reading_none(tmp_path):
    """The header is matched folded, so the rows must be keyed folded too.

    Otherwise a re-export that wrote ``Auction Name`` would pass the header check
    and then miss every lookup below it — 123 rows read as zero, no error, and a
    report where every card says "unknown auction house". That is the exact
    wrong-report-that-still-renders this module is built to prevent.
    """
    path = _write(tmp_path / "areas.csv", "Auction Name,Area Price $,Area Price JPY",
                  "USS TOKYO,365,47000")
    assert bidding.load_area_prices(path) == {"usstokyo": 47000}


def test_the_dollar_column_is_ignored(tmp_path):
    """The two columns are not one conversion of the other — ¥114/$ on one row
    and ¥129/$ on another. You bid in yen."""
    path = _write(tmp_path / "areas.csv", AREA_HEADER, "USS TOKYO,365,47000")
    assert bidding.load_area_prices(path) == {"usstokyo": 47000}


def test_a_file_without_the_expected_header_is_named_as_such(tmp_path):
    path = _write(tmp_path / "bids.csv", "make,model,price", "MAZDA,CX-30,1")
    with pytest.raises(bidding.BidTableError, match="no header row"):
        bidding.load_bid_prices(path)


def test_the_shipped_tables_load_and_price_a_real_house():
    """The defaults are committed, so a fresh clone gets a priced report — and
    the six aliases are exercised against the real 123-row cost file."""
    pricer = bidding.BidPricer()

    assert pricer.available is True and pricer.reason is None
    assert pricer.rows and len(pricer.aliases) == 6
    for db_name in ("U Tokyo", "U Nagoya", "U Kyushu", "U Osaka",
                    "U Yokohama", "Honda AA Tokyo", "BAY AUC", "HAA Kobe"):
        cost, reason = pricer._area_cost(_lot(auction_name=db_name))
        assert reason is None and cost > 0, db_name
