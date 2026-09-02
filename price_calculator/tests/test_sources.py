"""The cost book, the stamps, the Cyprus market, and the margin that joins them.

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

from cars.specs import ModelSpecs
from price_calculator.calculator import Rates
from price_calculator.sources import (
    COSTS_PATH,
    CostBookError,
    CyprusMarket,
    load_cost_book,
    margin_for,
    read_costs,
    read_rates,
    write_costs,
    write_rates,
)

COSTS = load_cost_book()

# `margin_for` needs a spec table to price against; the table's own tests are in
# `cars/tests/test_specs.py`, where the table now lives.
HEADER = ("make,model,year_from,year_to,length_cm,width_cm,height_cm,"
          "co2_gkm,euro_standard,fuel,body_model_code\n")

RATES = Rates(usd_jpy=Decimal("158.9"), eur_jpy_market=Decimal("185.6"),
              fetched_at=datetime(2026, 8, 22, tzinfo=timezone.utc))


def write(tmp_path: Path, body: str, preamble: str = "") -> Path:
    path = tmp_path / "model_specs.csv"
    path.write_text(preamble + HEADER + body, encoding="utf-8")
    return path


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
road_tax_cap_eur = 1_500
[[taxes.road_tax_band]]
up_to_gkm = 120
eur_per_gram = "0.50"
[[taxes.road_tax_band]]
up_to_gkm = 150
eur_per_gram = "3.00"
[[taxes.road_tax_band]]
eur_per_gram = "10.00"
[taxes.first_registration_surcharge]
euro6_petrol_eur = 0
euro6_diesel_eur = 0
euro5b_petrol_eur = 0
euro5b_diesel_eur = 50
euro5a_petrol_eur = 100
euro5a_diesel_eur = 250
euro4_petrol_eur = 300
euro4_diesel_eur = 600
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


def test_road_tax_bands_out_of_order_are_refused(tmp_path):
    """A marginal scale read out of order compounds every band below it."""
    text = MINIMAL.replace("up_to_gkm = 120", "up_to_gkm = 400")
    with pytest.raises(CostBookError, match="ascending"):
        load_cost_book(book(tmp_path, text))


def test_a_road_tax_band_with_a_fractional_gram_is_refused(tmp_path):
    """The scale is written in whole grams; 120.5 is a typo, not a bracket."""
    text = MINIMAL.replace("up_to_gkm = 120", "up_to_gkm = 120.5")
    with pytest.raises(CostBookError, match="whole number of grams"):
        load_cost_book(book(tmp_path, text))


def test_a_missing_surcharge_row_names_the_row(tmp_path):
    """Eight rows or none — a book missing one would silently charge €0 for it."""
    text = MINIMAL.replace("euro4_diesel_eur = 600\n", "")
    with pytest.raises(CostBookError) as exc:
        load_cost_book(book(tmp_path, text))
    assert "taxes.first_registration_surcharge.euro4_diesel_eur" in str(exc.value)


def test_the_shipped_book_prices_the_law_at_its_published_checkpoints():
    """Against the *shipped* file, because the scale is law and not a supplier's price.

    Unlike the exporter's fees, these do not move when somebody sends a new
    price list — they move when Cyprus amends Schedule I, which is exactly when
    this test should go red. Checkpoints from Law 47(I)/2019.
    """
    shipped = load_cost_book(COSTS_PATH)
    for co2, annual in [(90, 45), (120, 60), (150, 150), (180, 300), (200, 500)]:
        assert shipped.annual_road_tax_eur(co2)[0] == Decimal(annual), f"{co2} g/km"
    assert shipped.annual_road_tax_eur(1_000) == (shipped.road_tax_cap_eur, True)


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
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,158,6,petrol,KFEP\n"))
    result = margin_for("HONDA", "Fit", 2023, 40_000, 1_500_000,
                        RATES, COSTS, specs, _market_with([]))
    assert isinstance(result, str)
    assert "no model spec for HONDA Fit 2023" in result


def test_a_spec_with_no_co2_prices_nothing_and_says_why(tmp_path):
    """The blank that costs a card its landed cost, and the sentence it costs it for.

    Road tax runs €45–€1,500 a year across the CO₂ scale, so there is no honest
    number to print for a row that has no figure — the report prints the reason
    where the money would go, exactly as it does for a missing spec. The row
    still *loads*; it is only unpriceable.
    """
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,,,,\n"))
    result = margin_for("MAZDA", "CX-5", 2023, 40_000, 2_055_000,
                        RATES, COSTS, specs, _market_with([]))
    assert isinstance(result, str)
    assert "no CO₂ figure for MAZDA CX-5" in result
    assert "model_specs.csv" in result


def test_margin_for_prices_the_car_even_with_no_cyprus_data(tmp_path):
    """The landed cost is the half that does not need bazaraki.db."""
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,158,6,petrol,KFEP\n"))
    result = margin_for("MAZDA", "CX-5", 2023, 40_000, 2_055_000,
                        RATES, COSTS, specs, _market_with([]))
    assert not isinstance(result, str)
    assert result.landed.total_eur > 0
    assert result.cyprus_eur is None
    assert result.gap_eur is None
    assert "no Cyprus listings" in result.reason


def test_margin_for_skips_the_market_when_asked_for_the_landed_half_only(tmp_path):
    """``market=None`` is "do not look", not "looked and found nothing".

    The report prints the landed cost and nothing else, so it passes None and
    bazaraki.db is never opened. A market object here would be a full listings
    query per report for four lines nobody renders.
    """
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,158,6,petrol,KFEP\n"))
    result = margin_for("MAZDA", "CX-5", 2023, 40_000, 2_055_000,
                        RATES, COSTS, specs, None)

    assert not isinstance(result, str)
    assert result.landed.total_eur > 0
    assert result.cyprus_eur is None
    assert result.reason == "Cyprus estimate not requested"
    assert result.warning is None
    assert result.adjustment_factor is None

    # The CX-5's 158 g/km is €190 a year; the rates were quoted on 22 August, so
    # the car registers that day and buys 132 of 365 days of it.
    assert result.landed.road_tax.annual_eur == Decimal(190)
    assert result.landed.road_tax.registered_on == RATES.fetched_at.date()
    assert result.landed.road_tax.days == 132
    assert round(result.landed.road_tax_eur, 2) == Decimal("68.71")


def test_margin_for_compares_against_a_hand_built_market(tmp_path):
    """A whole margin end to end, with no database anywhere near it."""
    from bazaraki.analysis import CarRecord

    records = [
        CarRecord(ad_id=i, price=24_000 + 40 * i, year=2023, mileage_km=40_000 + 200 * i,
                  make="Mazda", model="CX-5", fuel_type="Petrol", gearbox="Automatic",
                  seller_type="dealer", is_active=True)
        for i in range(30)
    ]
    specs = ModelSpecs(write(tmp_path, "MAZDA,CX-5,2017,2026,457.5,184.5,169.0,158,6,petrol,KFEP\n"))
    result = margin_for("MAZDA", "CX-5", 2023, 40_000, 2_055_000,
                        RATES, COSTS, specs, _market_with(records))

    assert result.cyprus_eur is not None
    assert result.gap_eur == result.cyprus_eur - result.landed.total_eur
    assert result.margin_pct is not None
    # No delisting history and no fast sales, so the default haircut must stand.
    assert result.adjustment_factor == Decimal("0.92")
