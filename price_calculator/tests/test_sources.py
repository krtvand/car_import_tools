"""The model spec table, the cost book, the stamps, and the shipped files themselves.

Nothing here touches the network or ``bazaraki.db``; the Cyprus half is exercised
with hand-built records, which is the whole reason ``analysis`` takes plain
``CarRecord`` values.

The cost book tests come in two kinds and the split is the point (ADR 0003).
Against the **shipped file** only invariants are asserted — it parses, the tiers
ascend, nothing is negative — so raising a real price stays green. Against
**hand-written books** the loader's refusals are asserted, so a mis-edit is
caught the morning it is made rather than three reports later.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from price_calculator.calculator import Rates
from price_calculator.sources import (
    COSTS_PATH,
    MODEL_SPECS_PATH,
    CostBookError,
    CyprusMarket,
    ModelSpecError,
    ModelSpecs,
    load_cost_book,
    load_model_specs,
    margin_for,
    read_costs,
    read_rates,
    write_costs,
    write_rates,
)

COSTS = load_cost_book()

HEADER = ("make,model,year_from,year_to,length_cm,width_cm,height_cm,"
          "co2_gkm,body_model_code\n")

RATES = Rates(usd_jpy=Decimal("158.9"), eur_jpy_market=Decimal("185.6"),
              fetched_at=datetime(2026, 8, 22, tzinfo=timezone.utc))


def write(tmp_path: Path, body: str, preamble: str = "") -> Path:
    path = tmp_path / "model_specs.csv"
    path.write_text(preamble + HEADER + body, encoding="utf-8")
    return path


# --- the shipped file --------------------------------------------------------


def test_the_shipped_specs_load_and_cover_every_bid_price_row():
    """Every car you bid on must be priceable, or the table has a hole in it."""
    from banzai24.bidding import load_bid_prices

    specs = ModelSpecs()
    assert specs.available, specs.reason

    for row in load_bid_prices():
        assert specs.for_car(row.make, row.model, row.year) is not None, \
            f"no model spec covers {row.make} {row.model} {row.year}"


def test_the_shipped_specs_have_plausible_volumes():
    """A transposed digit is the failure mode here, and it is worth ~EUR 150."""
    for spec in load_model_specs(MODEL_SPECS_PATH):
        assert Decimal(8) < spec.volume_m3 < Decimal(20), spec.describe()


def test_the_preamble_above_the_header_is_ignored():
    """The shipped file opens with four lines of prose saying it is unverified."""
    assert load_model_specs(MODEL_SPECS_PATH)
    assert "UNVERIFIED" in MODEL_SPECS_PATH.read_text(encoding="utf-8")


# --- loading -----------------------------------------------------------------


def test_a_row_loads_with_its_optional_columns_blank(tmp_path):
    path = write(tmp_path, "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,,\n")
    spec, = load_model_specs(path)
    assert spec.co2_gkm is None
    assert spec.body_model_code is None
    assert round(spec.volume_m3, 2) == Decimal("14.27")


def test_the_header_is_matched_folded(tmp_path):
    """A re-export that recases a column must not read every row as blank."""
    path = tmp_path / "model_specs.csv"
    path.write_text(
        "Make,Model,Year From,Year To,Length CM,Width CM,Height CM,CO2 g/km,Body Model Code\n"
        "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,158,KFEP\n", encoding="utf-8")
    spec, = load_model_specs(path)
    assert spec.make == "MAZDA"


def test_overlapping_year_spans_are_rejected_at_load(tmp_path):
    """Two rows that could both describe one car, caught before a car falls in."""
    path = write(tmp_path,
                 "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,158,KFEP\n"
                 "MAZDA,CX-5,2024,2028,460.0,187.0,168.0,150,KF5P\n")
    with pytest.raises(ModelSpecError, match="year spans overlap"):
        load_model_specs(path)


def test_adjacent_year_spans_are_fine(tmp_path):
    path = write(tmp_path,
                 "MAZDA,CX-5,2017,2023,457.5,184.5,169.0,158,KFEP\n"
                 "MAZDA,CX-5,2024,2028,460.0,187.0,168.0,150,KF5P\n")
    assert len(load_model_specs(path)) == 2


def test_a_backwards_year_span_is_rejected(tmp_path):
    path = write(tmp_path, "MAZDA,CX-5,2026,2017,457.5,184.5,169.0,,\n")
    with pytest.raises(ModelSpecError, match="before"):
        load_model_specs(path)


@pytest.mark.parametrize("body,match", [
    (",CX-5,2017,2026,457.5,184.5,169.0,,\n", "make and model are required"),
    ("MAZDA,CX-5,2017,2026,,184.5,169.0,,\n", "length_cm is empty"),
    ("MAZDA,CX-5,2017,2026,wide,184.5,169.0,,\n", "not a number"),
    ("MAZDA,CX-5,2017,2026,457.5,184.5,169.0,lots,\n", "not a whole number"),
])
def test_a_bad_cell_names_the_column(tmp_path, body, match):
    with pytest.raises(ModelSpecError, match=match):
        load_model_specs(write(tmp_path, body))


def test_a_missing_file_costs_the_column_not_the_page(tmp_path):
    """Mirrors BidPricer: a missing input is reported, never raised."""
    specs = ModelSpecs(tmp_path / "absent.csv")
    assert not specs.available
    assert specs.reason == "model specs not loaded"
    assert specs.for_car("MAZDA", "CX-5", 2023) is None


def test_a_malformed_file_attaches_the_parsers_complaint(tmp_path):
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-5,2026,2017,457.5,184.5,169.0,,\n"))
    assert not specs.available
    assert "year_to 2017 is before" in specs.reason


# --- lookup ------------------------------------------------------------------


def test_lookup_folds_case_and_punctuation(tmp_path):
    """banzai24 writes MAZDA / CX-30, bazaraki writes Mazda / cx30."""
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-30,2019,2026,439.5,179.5,154.0,,\n"))
    assert specs.for_car("mazda", "cx30", 2023) is not None
    assert specs.for_car("Mazda", "CX 30", 2020) is not None


def test_a_year_outside_every_span_is_none_not_the_nearest_row(tmp_path):
    """Freight is 17% of CNF — a borrowed row is wrong by more than any fee here."""
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,,\n"))
    assert specs.for_car("MAZDA", "CX-5", 2010) is None
    assert specs.for_car("MAZDA", "CX-9", 2023) is None


def test_missing_identifiers_do_not_match_anything(tmp_path):
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,,\n"))
    assert specs.for_car(None, "CX-5", 2023) is None
    assert specs.for_car("MAZDA", "CX-5", None) is None


# --- rates round-trip --------------------------------------------------------


def test_rates_survive_the_stamp_exactly(tmp_path):
    """Decimals go through as text: a float round-trip would move the money."""
    write_rates(tmp_path, RATES)
    restored = read_rates(tmp_path)
    assert restored.usd_jpy == RATES.usd_jpy
    assert restored.eur_jpy_market == RATES.eur_jpy_market
    assert restored.fetched_at == RATES.fetched_at


def test_a_run_without_a_stamp_has_no_rates_rather_than_todays(tmp_path):
    """Runs predating the stamp get no landed cost — never one invented at today's rate."""
    assert read_rates(tmp_path) is None


def test_an_unreadable_stamp_is_none_not_an_exception(tmp_path):
    (tmp_path / "rates.json").write_text("{not json", encoding="utf-8")
    assert read_rates(tmp_path) is None


# --- the cost book -----------------------------------------------------------

MINIMAL = """
updated = 2026-08-22
source = "test"
[exporter]
fixed_fee_jpy = 17_000
certificate_of_origin_jpy = 1_200
[[exporter.service_fee]]
up_to_jpy = 1_000_000
fee_jpy = 59_000
[[exporter.service_fee]]
up_to_jpy = 9_000_000
fee_jpy = 134_000
[freight]
roro_per_m3_usd = 166
insurance_usd = 50
[taxes]
vat_rate = "0.19"
duty_rate = "0"
[bank]
fx_rate = "0.01"
international_transfer_eur = 60
eur_jpy_spread = 2
[cyprus]
sva_test_eur = 140
mot_eur = 35
registration_eur = 200
customs_clearance_eur = 513
number_plates_eur = 30
car_service_eur = 120
insurance_eur = 50
road_tax_eur = 11
[resale]
costs_eur = 0
"""


def book(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "costs.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_shipped_cost_book_is_a_cost_book():
    """Invariants only — never the prices themselves.

    Asserting ¥134,000 here would mean the exporter's next price list turns this
    suite red, which is the coupling ADR 0003 removed. What must hold of *any*
    book is that it loads, that the tiers ascend, and that nothing is negative;
    :meth:`CostBook.problems` is what says so and this proves it is run.
    """
    shipped = load_cost_book(COSTS_PATH)
    assert shipped.problems() == []
    assert shipped.updated is not None, "an undated price list cannot be reconciled"
    assert shipped.source, "say which price list these came from"
    assert shipped.fixed_expenses_base_eur > 0


def test_a_missing_field_names_the_field_and_the_file(tmp_path):
    text = MINIMAL.replace("roro_per_m3_usd = 166\n", "")
    with pytest.raises(CostBookError) as exc:
        load_cost_book(book(tmp_path, text))
    assert "freight.roro_per_m3_usd" in str(exc.value)
    assert "costs.toml" in str(exc.value)


def test_a_rate_written_as_a_float_is_refused_not_rounded(tmp_path):
    """0.19 in TOML is a binary float that is not 0.19; the file must quote it."""
    text = MINIMAL.replace('vat_rate = "0.19"', "vat_rate = 0.19")
    with pytest.raises(CostBookError) as exc:
        load_cost_book(book(tmp_path, text))
    assert "quote it as a string" in str(exc.value)


def test_tiers_out_of_order_are_refused(tmp_path):
    """A sorted VLOOKUP on an unsorted table silently charges the wrong fee."""
    text = MINIMAL.replace("up_to_jpy = 9_000_000", "up_to_jpy = 500_000")
    with pytest.raises(CostBookError, match="ascending"):
        load_cost_book(book(tmp_path, text))


def test_a_percent_written_as_a_percent_is_refused(tmp_path):
    """``vat_rate = 19`` would charge nineteen times the car's value in VAT."""
    text = MINIMAL.replace('vat_rate = "0.19"', 'vat_rate = "19"')
    with pytest.raises(CostBookError, match="fractions, not percents"):
        load_cost_book(book(tmp_path, text))


def test_a_missing_cost_book_raises_rather_than_pricing_at_nothing(tmp_path):
    """No fallback copy in code: without a book there is nothing to price with."""
    with pytest.raises(CostBookError, match="no cost book"):
        load_cost_book(tmp_path / "costs.toml")


def test_broken_toml_names_the_file(tmp_path):
    with pytest.raises(CostBookError, match="costs.toml"):
        load_cost_book(book(tmp_path, "[exporter\nfixed_fee_jpy = 1"))


def test_the_cost_book_survives_the_stamp_exactly(tmp_path):
    """Every price back as the same Decimal — the run's prices are the run's."""
    write_costs(tmp_path, COSTS)
    restored = read_costs(tmp_path)
    assert restored == COSTS


def test_a_run_without_a_stamped_book_has_none_rather_than_todays(tmp_path):
    """The fee rise of August must not reach back into a July run."""
    assert read_costs(tmp_path) is None
    (tmp_path / "costs.json").write_text("{not json", encoding="utf-8")
    assert read_costs(tmp_path) is None


# --- margin_for --------------------------------------------------------------


def _market_with(records):
    return CyprusMarket(records=records)


def test_margin_for_returns_a_reason_when_the_spec_is_missing(tmp_path):
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,,\n"))
    result = margin_for("HONDA", "Fit", 2023, 40_000, 1_500_000,
                        RATES, COSTS, specs, _market_with([]))
    assert isinstance(result, str)
    assert "no model spec for HONDA Fit 2023" in result


def test_margin_for_prices_the_car_even_with_no_cyprus_data(tmp_path):
    """The landed cost is the half that does not need bazaraki.db."""
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,,\n"))
    result = margin_for("MAZDA", "CX-5", 2023, 40_000, 2_055_000,
                        RATES, COSTS, specs, _market_with([]))
    assert not isinstance(result, str)
    assert result.landed.total_eur > 0
    assert result.cyprus_eur is None
    assert result.gap_eur is None
    assert "no Cyprus listings" in result.reason


def test_margin_for_compares_against_a_hand_built_market(tmp_path):
    """A whole margin end to end, with no database anywhere near it."""
    from bazaraki.analysis import CarRecord

    records = [
        CarRecord(ad_id=i, price=24_000 + 40 * i, year=2023, mileage_km=40_000 + 200 * i,
                  make="Mazda", model="CX-5", fuel_type="Petrol", gearbox="Automatic",
                  seller_type="dealer", is_active=True)
        for i in range(30)
    ]
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,,\n"))
    result = margin_for("MAZDA", "CX-5", 2023, 40_000, 2_055_000,
                        RATES, COSTS, specs, _market_with(records))

    assert result.cyprus_eur is not None
    assert result.gap_eur == result.cyprus_eur - result.landed.total_eur
    assert result.margin_pct is not None
    # No delisting history and no fast sales, so the default haircut must stand.
    assert result.adjustment_factor == Decimal("0.92")
