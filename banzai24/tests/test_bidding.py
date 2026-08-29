"""Computing ``bid_reduced``, and refusing to compute it.

The bid price is the one number on the report that spends money, so the tests
that matter here are the ones about *not* producing it: a band edge off by a
kilometre, a year that is not in the table, a house whose name is spelled
differently in two files. Each of those must come back as a reason on the card —
never a guessed number, and never an exception that costs you the whole report.

The lookup is pure, so all of this runs against hand-built bands and two-line
fixture CSVs written into ``tmp_path``; nothing here touches the real tables, the
database, or a rendered page. The bands arrive already parsed and already checked
for overlap — :mod:`searches` refuses to load a file whose bands collide — so the
tests for *that* live in ``searches/tests/test_definition.py``.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from searches.cars import Car
from searches.definition import Band

from banzai24 import bidding
from banzai24.models import AuctionLot, SheetExtraction

AREA_HEADER = "AUCTION NAME,AREA PRICE $,AREA PRICE JPY"

CX30 = Car(key="mazda-cx30", make="Mazda", model="CX-30")
DEFAULT_BANDS = (
    Band(year=2023, mileage_start=0, mileage_end=50_000,
         max_bid_jpy={"private": 1_855_000}),
)


# --- fixtures ----------------------------------------------------------------


def _write(path: Path, *lines: str) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _tables(tmp_path, bands=None, areas=(), aliases=(), title=True) -> dict:
    """The two CSVs on disk plus the bands, as ``BidPricer`` keyword arguments.

    ``title=True`` writes the Numbers-style sheet-name line above every header,
    because that is what a fresh export actually looks like — the parser has to
    survive it in the normal case, not only in the test that checks for it.
    """
    areas = areas or ("USS TOKYO,110,12000",)
    aliases = aliases or ("U Tokyo,USS TOKYO",)
    lead = ("some_export_2026",) if title else ()

    return {
        "bands": DEFAULT_BANDS if bands is None else bands,
        "car": CX30,
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
        "no band for MAZDA CX-30 2017 · 15,000 km"
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


def test_an_omitted_mileage_end_is_open_ended(tmp_path):
    pricer = _pricer(tmp_path, bands=(
        Band(year=2023, mileage_start=50_001, max_bid_jpy={"private": 1_200_000}),))
    quote = pricer.for_lot(_lot(mileage_km=310_000), _extraction())
    assert quote.max_bid == 1_200_000


def test_the_year_must_match_exactly_rather_than_borrowing_a_neighbour(tmp_path):
    """2022 does not read 2023's price. An unpriced year is a null, because the
    alternative is a number that looks authored and is not."""
    quote = _pricer(tmp_path).for_lot(_lot(registration_year=2022), _extraction())
    assert quote.bid_reduced is None
    assert quote.reason == "no band for MAZDA CX-30 2022 · 15,000 km"


def test_the_chassis_code_picks_the_band_when_two_variants_share_a_year(tmp_path):
    """The RAV4: E-Four and 2WD, one year, one mileage range, ¥445,000 apart.
    The code is the only thing that tells them apart, so it is what picks the
    price."""
    bands = (
        Band(year=2023, body_model_code=("AXAH54",), mileage_end=50_000,
             max_bid_jpy={"private": 3_150_000}),
        Band(year=2023, body_model_code=("AXAH52",), mileage_end=50_000,
             max_bid_jpy={"private": 2_705_000}),
    )
    pricer = _pricer(tmp_path, bands=bands)
    four = pricer.for_lot(_lot(body_model_code="6AA-AXAH54"), _extraction())
    two = pricer.for_lot(_lot(body_model_code="AXAH52"), _extraction())
    assert (four.max_bid, two.max_bid) == (3_150_000, 2_705_000)


def test_a_car_whose_code_no_band_prices_says_which_code_it_was(tmp_path):
    """Not the dearer band and not the first one. On a search that prices by
    code the code is usually the whole answer, so the card carries it."""
    pricer = _pricer(tmp_path, bands=(
        Band(year=2023, body_model_code=("AXAH54",), mileage_end=50_000,
             max_bid_jpy={"private": 3_150_000}),))
    quote = pricer.for_lot(_lot(body_model_code="AXAH52"), _extraction())
    assert quote.max_bid is None
    assert quote.reason.endswith("2023 · 15,000 km · AXAH52")
    unstated = pricer.for_lot(_lot(), _extraction())
    assert unstated.reason.endswith("· no model code")


def test_a_search_that_does_not_price_by_code_says_nothing_about_one(tmp_path):
    """On every other search it would be a column of noise."""
    quote = _pricer(tmp_path).for_lot(_lot(registration_year=2017), _extraction())
    assert quote.reason == "no band for MAZDA CX-30 2017 · 15,000 km"


def test_rental_and_private_are_priced_separately(tmp_path):
    pricer = _pricer(tmp_path, bands=(
        Band(year=2023, mileage_start=0, mileage_end=50_000,
             max_bid_jpy={"private": 1_855_000, "rental": 1_400_000}),))
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
                     bands=(Band(year=2023, mileage_end=50_000,
                                 max_bid_jpy={"private": 30_000}),),
                     areas=("USS TOKYO,365,47000",),
                     aliases=("U Tokyo,USS TOKYO",))
    quote = pricer.for_lot(_lot(), _extraction())

    assert quote.bid_reduced is None
    assert quote.reason == "area cost ¥ 47 000 exceeds max bid ¥ 30 000"
    assert quote.max_bid == 30_000 and quote.extra_costs == 47_000


# --- missing and malformed tables --------------------------------------------


def test_a_run_that_named_no_search_has_no_bands_and_says_so(tmp_path):
    """The only way the bid column goes missing now. A run fetched before saved
    searches were files named none, so nothing declares what to pay for its lots
    — and pricing them off whichever car's table happened to load would be worse
    than a blank column."""
    pricer = bidding.BidPricer(**{**_tables(tmp_path), "bands": ()})

    assert pricer.available is False
    assert pricer.reason == "no bid bands: this run named no saved search"
    assert pricer.for_lot(_lot(), _extraction()) is None


def test_a_lot_that_is_not_the_search_s_car_is_not_priced_off_its_bands(tmp_path):
    """A run holds one search's lots, so this only fires when a run's provenance
    and its lots disagree — worth saying rather than pricing a RAV4 off a
    CX-30's band."""
    quote = _pricer(tmp_path).for_lot(_lot(mark="TOYOTA", model="RAV4"), _extraction())

    assert quote.max_bid is None
    assert quote.reason == "TOYOTA RAV4 is not the Mazda CX-30 this search prices"


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
    path = _write(tmp_path / "areas.csv", "house,price", "USS TOKYO,1")
    with pytest.raises(bidding.BidTableError, match="no header row"):
        bidding.load_area_prices(path)


def test_the_shipped_tables_load_and_price_a_real_house():
    """The defaults are committed, so a fresh clone gets a priced report — and
    the six aliases are exercised against the real 123-row cost file."""
    import searches

    definition = searches.load("mazda-cx30")
    pricer = bidding.BidPricer(bands=definition.bands, car=definition.car)

    assert pricer.available is True and pricer.reason is None
    assert pricer.bands and len(pricer.aliases) == 6
    for db_name in ("U Tokyo", "U Nagoya", "U Kyushu", "U Osaka",
                    "U Yokohama", "Honda AA Tokyo", "BAY AUC", "HAA Kobe"):
        cost, reason = pricer._area_cost(_lot(auction_name=db_name))
        assert reason is None and cost > 0, db_name
