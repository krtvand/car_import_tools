"""The arithmetic, checked against the sheet it was ported from.

The anchor test is the spec's own reference car (§6). Everything else here
guards a boundary the sheet expresses as a ``VLOOKUP`` and this module has to
express as code — the places where a port silently drifts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from price_calculator.calculator import (
    EUR_JPY_SPREAD,
    FIXED_EXPENSES_BASE_EUR,
    Margin,
    ModelSpec,
    Rates,
    landed_cost,
    service_fee_jpy,
)

# Spec §6: Nissan Note e-Power (e13) 2023, 404 × 173 × 152 cm, ¥1,245,000,
# at USD/JPY 158.9 and EUR/JPY *effective* 183.6.
REFERENCE_RATES = Rates(
    usd_jpy=Decimal("158.9"),
    eur_jpy_market=Decimal("183.6") + EUR_JPY_SPREAD,
    fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    source="spec §6",
)
REFERENCE_CAR = ModelSpec(
    make="NISSAN", model="Note e-Power", year_from=2023, year_to=2023,
    length_cm=Decimal(404), width_cm=Decimal(173), height_cm=Decimal(152),
)


@pytest.fixture
def reference():
    return landed_cost(1_245_000, REFERENCE_CAR, REFERENCE_RATES)


def test_the_reference_car_matches_the_sheet(reference):
    """Every line of the spec's §6 table, to the euro the sheet prints.

    The sheet rounds per cell and this does not, so a couple of lines are
    asserted to the nearest euro rather than exactly — that gap is the rounding.
    The total comes out at €11,763 against the sheet's €11,760; a port that
    drifts further than that has changed something.
    """
    assert round(reference.volume_m3, 2) == Decimal("10.62")
    assert reference.exporter_fees_jpy == Decimal(89_200)      # 71,000 + 17,000 + 1,200
    assert round(reference.freight_jpy) == Decimal(280_223)    # sheet prints 280,288
    assert round(reference.freight_insurance_jpy) == Decimal(7_945)
    assert round(reference.cnf_price_jpy) == Decimal(1_622_368)
    assert round(reference.cnf_price_eur) == Decimal(8_836)
    assert round(reference.duty_eur) == Decimal(0)
    assert round(reference.vat_eur) == Decimal(1_679)
    assert round(reference.fixed_expenses_eur) == Decimal(1_099)
    assert round(reference.bank_transfer_fees_eur, 1) == Decimal("148.4")
    assert round(reference.total_eur) == Decimal(11_763)


def test_vat_is_charged_on_cnf_plus_duty_only(reference):
    """Not on the bank fees, and not on the local expenses paid after clearance."""
    assert reference.vat_eur == (reference.cnf_price_eur + reference.duty_eur) * Decimal("0.19")
    assert reference.vat_eur < (reference.total_eur - reference.vat_eur) * Decimal("0.19")


def test_duty_is_a_parameter_because_zero_is_a_condition_being_met():
    """Spec §2: duty is 0 *provided the documentation is submitted*."""
    dutied = landed_cost(1_245_000, REFERENCE_CAR, REFERENCE_RATES,
                         duty_rate=Decimal("0.10"))
    free = landed_cost(1_245_000, REFERENCE_CAR, REFERENCE_RATES)
    assert dutied.duty_eur > 0
    # Duty raises the VAT base too, so the gap is more than the duty alone.
    assert dutied.total_eur - free.total_eur > dutied.duty_eur


def test_the_eur_haircut_makes_the_car_dearer():
    """The −2 on EUR/JPY is a conservative margin, so it must never flatter."""
    assert REFERENCE_RATES.eur_jpy_effective == REFERENCE_RATES.eur_jpy_market - 2
    generous = Rates(usd_jpy=REFERENCE_RATES.usd_jpy,
                     eur_jpy_market=REFERENCE_RATES.eur_jpy_market + 10,
                     fetched_at=REFERENCE_RATES.fetched_at)
    assert landed_cost(1_245_000, REFERENCE_CAR, generous).total_eur < \
        landed_cost(1_245_000, REFERENCE_CAR, REFERENCE_RATES).total_eur


@pytest.mark.parametrize("price,fee", [
    (1, 56_000),
    (1_000_000, 56_000),        # inclusive upper bound
    (1_000_001, 71_000),        # first yen of the next tier
    (1_500_000, 71_000),
    (1_500_001, 91_000),
    (2_000_000, 91_000),
    (2_000_001, 111_000),
    (9_000_000, 111_000),
])
def test_service_fee_bands_are_inclusive_at_the_top(price, fee):
    assert service_fee_jpy(Decimal(price)) == (Decimal(fee), False)


def test_above_the_fee_table_holds_the_last_tier_but_says_so():
    """A sorted VLOOKUP keeps returning the last row; that is an accident, not a quote."""
    fee, above = service_fee_jpy(Decimal(12_000_000))
    assert fee == Decimal(111_000)
    assert above is True
    assert landed_cost(12_000_000, REFERENCE_CAR, REFERENCE_RATES).above_fee_table


def test_a_non_positive_auction_price_raises_rather_than_explains():
    """A caller bug, not a car that is hard to price — it must not become a blank cell."""
    for bad in (0, -1):
        with pytest.raises(ValueError, match="must be positive"):
            landed_cost(bad, REFERENCE_CAR, REFERENCE_RATES)


def test_freight_insurance_can_be_dropped_but_is_on_by_default(reference):
    without = landed_cost(1_245_000, REFERENCE_CAR, REFERENCE_RATES,
                          with_freight_insurance=False)
    assert reference.freight_insurance_jpy > 0
    assert without.freight_insurance_jpy == 0
    assert without.total_eur < reference.total_eur


def test_fixed_expenses_are_the_base_plus_road_tax(reference):
    """Spec §2: everything but road tax is a constant, and nothing is excluded."""
    assert FIXED_EXPENSES_BASE_EUR == Decimal(1_088)
    assert reference.fixed_expenses_eur == FIXED_EXPENSES_BASE_EUR + reference.road_tax_eur
    dirtier = landed_cost(1_245_000, REFERENCE_CAR, REFERENCE_RATES,
                          road_tax_eur=Decimal(300))
    assert dirtier.fixed_expenses_eur == FIXED_EXPENSES_BASE_EUR + 300


def test_freight_scales_with_the_box_not_the_price():
    """Dimensions are the only car parameter beyond the hammer price (spec §1)."""
    bigger = ModelSpec(make="X", model="Y", year_from=2023, year_to=2023,
                       length_cm=Decimal(808), width_cm=Decimal(173),
                       height_cm=Decimal(152))
    twice = landed_cost(1_245_000, bigger, REFERENCE_RATES)
    once = landed_cost(1_245_000, REFERENCE_CAR, REFERENCE_RATES)
    assert twice.freight_jpy == once.freight_jpy * 2


# --- the margin --------------------------------------------------------------


def test_margin_is_cyprus_less_landed_less_resale_costs(reference):
    margin = Margin(landed=reference, cyprus_eur=Decimal(15_000),
                    resale_costs_eur=Decimal(200))
    assert round(margin.gap_eur) == Decimal(15_000) - round(reference.total_eur) - 200
    assert margin.margin_pct == margin.gap_eur * 100 / reference.total_eur


def test_a_missing_cyprus_estimate_still_carries_the_landed_cost(reference):
    """The two halves fail independently — a landed cost is useful on its own."""
    margin = Margin(landed=reference, cyprus_eur=None,
                    reason="no Cyprus listings for NISSAN Note e-Power")
    assert margin.gap_eur is None
    assert margin.margin_pct is None
    assert "€11,763" in margin.describe()
    assert "no Cyprus listings" in margin.describe()


def test_margin_can_be_negative(reference):
    """A car that loses money must print a loss, not a blank or a zero."""
    margin = Margin(landed=reference, cyprus_eur=Decimal(5_000))
    assert margin.gap_eur < 0
    assert margin.margin_pct < 0
    assert "-" in margin.describe()
