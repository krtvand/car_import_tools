"""The arithmetic, checked against the sheet it was ported from.

The anchor test is the spec's own reference car (§6). Everything else here
guards a boundary the sheet expresses as a ``VLOOKUP`` and this module has to
express as code — the places where a port silently drifts.

**Every price here is frozen at the port**, in ``REFERENCE_COSTS``, including
the ¥56,000 service fee tiers the spreadsheet carried in January. That is
deliberate: §6's worked example is a statement about the sheet on the day it was
ported, not about what an exporter charges this month, and wiring these tests to
the live cost book is what made a fee rise turn twelve of them red — a false
alarm on a change that touched no arithmetic (ADR 0003). Prices are checked in
``test_sources.py``, against the file, for the things that are true of any book.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from price_calculator.calculator import (
    CostBook,
    Margin,
    ModelSpec,
    Rates,
    RegistrationSurcharge,
    RoadTaxBand,
    ServiceFeeTier,
    landed_cost,
)

# The cost book as the spreadsheet had it at the port. Never updated: raising a
# real price must not touch this file, and a change here means the *sheet* is
# being re-read, not that an exporter sent a new list.
REFERENCE_COSTS = CostBook(
    service_fee_tiers=(
        ServiceFeeTier(Decimal(1_000_000), Decimal(56_000)),
        ServiceFeeTier(Decimal(1_500_000), Decimal(71_000)),
        ServiceFeeTier(Decimal(2_000_000), Decimal(91_000)),
        ServiceFeeTier(Decimal(9_000_000), Decimal(111_000)),
    ),
    exporter_fixed_fee_jpy=Decimal(17_000),
    certificate_of_origin_jpy=Decimal(1_200),
    roro_per_m3_usd=Decimal(166),
    freight_insurance_usd=Decimal(50),
    vat_rate=Decimal("0.19"),
    duty_rate=Decimal(0),
    # Law 47(I)/2019, Schedule I. Unlike the fees above this is not the sheet's
    # January state — the sheet had no scale, only a flat €11 — so it is the law
    # as ported, and it moves when the law moves rather than when a supplier
    # sends a price list.
    road_tax_bands=(
        RoadTaxBand(120, Decimal("0.50")),
        RoadTaxBand(150, Decimal("3.00")),
        RoadTaxBand(180, Decimal("5.00")),
        RoadTaxBand(None, Decimal("10.00")),
    ),
    road_tax_cap_eur=Decimal(1_500),
    registration_surcharge=RegistrationSurcharge(
        euro6_petrol_eur=Decimal(0), euro6_diesel_eur=Decimal(0),
        euro5b_petrol_eur=Decimal(0), euro5b_diesel_eur=Decimal(50),
        euro5a_petrol_eur=Decimal(100), euro5a_diesel_eur=Decimal(250),
        euro4_petrol_eur=Decimal(300), euro4_diesel_eur=Decimal(600),
    ),
    bank_fx_rate=Decimal("0.01"),
    international_transfer_eur=Decimal(60),
    eur_jpy_spread=Decimal(2),
    sva_test_eur=Decimal(140),
    mot_eur=Decimal(35),
    registration_eur=Decimal(200),
    customs_clearance_eur=Decimal(513),
    number_plates_eur=Decimal(30),
    car_service_eur=Decimal(120),
    insurance_eur=Decimal(50),
    resale_costs_eur=Decimal(0),
    source="spec §2, as ported",
)

# Spec §6: Nissan Note e-Power (e13) 2023, 404 × 173 × 152 cm, ¥1,245,000,
# at USD/JPY 158.9 and EUR/JPY *effective* 183.6.
REFERENCE_RATES = Rates(
    usd_jpy=Decimal("158.9"),
    eur_jpy_market=Decimal("183.6") + REFERENCE_COSTS.eur_jpy_spread,
    fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    source="spec §6",
)
# The CO₂, Euro standard and fuel are *not* from the sheet — it had none of the
# three, only a flat €11 of road tax. 90 g/km is the Note e-Power's NEDC
# combined figure, and it is here so the §6 car keeps being one car all the way
# through rather than acquiring a second, undated identity for the road tax line.
REFERENCE_CAR = ModelSpec(
    make="NISSAN", model="Note e-Power", year_from=2023, year_to=2023,
    length_cm=Decimal(404), width_cm=Decimal(173), height_cm=Decimal(152),
    co2_gkm=90, euro_standard="6", fuel="petrol",
)

# 1 January is the whole-year case, and it is chosen for that: it takes the
# day-count arithmetic out of every test below that is about something else, so
# a test that says "duty raises the VAT base" fails for that reason alone. The
# part year has tests of its own.
REFERENCE_REGISTRATION = date(2026, 1, 1)


@pytest.fixture
def reference():
    return landed_cost(1_245_000, REFERENCE_CAR, REFERENCE_RATES, REFERENCE_COSTS,
                       REFERENCE_REGISTRATION)


def test_the_reference_car_matches_the_sheet(reference):
    """Every line of the spec's §6 table, to the euro the sheet prints.

    The sheet rounds per cell and this does not, so a couple of lines are
    asserted to the nearest euro rather than exactly — that gap is the rounding.

    **One line no longer reconciles, on purpose.** ``Calculator!B28`` held a flat
    €11 of road tax; this charges the Note e-Power's own €45 off the CO₂ scale,
    so fixed expenses come out at €1,133 against the sheet's €1,099 and the
    total at €11,797 against €11,760. That €34 is the port being *more* right
    than the sheet, not drift — every other line is still the sheet's, and the
    €3 between €11,763 and €11,760 is the rounding it always was.
    """
    assert round(reference.volume_m3, 2) == Decimal("10.62")
    assert reference.exporter_fees_jpy == Decimal(89_200)      # 71,000 + 17,000 + 1,200
    assert round(reference.freight_jpy) == Decimal(280_223)    # sheet prints 280,288
    assert round(reference.freight_insurance_jpy) == Decimal(7_945)
    assert round(reference.cnf_price_jpy) == Decimal(1_622_368)
    assert round(reference.cnf_price_eur) == Decimal(8_836)
    assert round(reference.duty_eur) == Decimal(0)
    assert round(reference.vat_eur) == Decimal(1_679)
    assert round(reference.bank_transfer_fees_eur, 1) == Decimal("148.4")

    # The sheet's own numbers for these two, kept so the departure is legible
    # rather than merely absent: €1,099 and €11,760 are B21 and B5 in January.
    assert round(reference.fixed_expenses_eur) == Decimal(1_099) - 11 + 45
    assert round(reference.road_tax_eur) == Decimal(45)
    assert round(reference.total_eur) == Decimal(11_763) - 11 + 45


def test_vat_is_charged_on_cnf_plus_duty_only(reference):
    """Not on the bank fees, and not on the local expenses paid after clearance."""
    assert reference.vat_eur == (reference.cnf_price_eur + reference.duty_eur) * Decimal("0.19")
    assert reference.vat_eur < (reference.total_eur - reference.vat_eur) * Decimal("0.19")


def test_duty_can_be_changed_because_zero_is_a_condition_being_met():
    """Spec §2: duty is 0 *provided the documentation is submitted*.

    Overriding one price for one car is ``replace`` on the book, which is the
    only override mechanism there is — the argument list grew no flags back.
    """
    dutied = landed_cost(1_245_000, REFERENCE_CAR, REFERENCE_RATES,
                         replace(REFERENCE_COSTS, duty_rate=Decimal("0.10")))
    free = landed_cost(1_245_000, REFERENCE_CAR, REFERENCE_RATES, REFERENCE_COSTS)
    assert dutied.duty_eur > 0
    # Duty raises the VAT base too, so the gap is more than the duty alone.
    assert dutied.total_eur - free.total_eur > dutied.duty_eur


def test_the_eur_haircut_makes_the_car_dearer():
    """The −2 on EUR/JPY is a conservative margin, so it must never flatter."""
    effective = REFERENCE_COSTS.eur_jpy_effective(REFERENCE_RATES)
    assert effective == REFERENCE_RATES.eur_jpy_market - 2
    generous = Rates(usd_jpy=REFERENCE_RATES.usd_jpy,
                     eur_jpy_market=REFERENCE_RATES.eur_jpy_market + 10,
                     fetched_at=REFERENCE_RATES.fetched_at)
    assert landed_cost(1_245_000, REFERENCE_CAR, generous, REFERENCE_COSTS).total_eur < \
        landed_cost(1_245_000, REFERENCE_CAR, REFERENCE_RATES, REFERENCE_COSTS).total_eur


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
    assert REFERENCE_COSTS.service_fee_jpy(Decimal(price)) == (Decimal(fee), False)


def test_above_the_fee_table_holds_the_last_tier_but_says_so():
    """A sorted VLOOKUP keeps returning the last row; that is an accident, not a quote."""
    fee, above = REFERENCE_COSTS.service_fee_jpy(Decimal(12_000_000))
    assert fee == Decimal(111_000)
    assert above is True
    assert landed_cost(12_000_000, REFERENCE_CAR, REFERENCE_RATES,
                       REFERENCE_COSTS).above_fee_table


def test_a_non_positive_auction_price_raises_rather_than_explains():
    """A caller bug, not a car that is hard to price — it must not become a blank cell."""
    for bad in (0, -1):
        with pytest.raises(ValueError, match="must be positive"):
            landed_cost(bad, REFERENCE_CAR, REFERENCE_RATES, REFERENCE_COSTS)


def test_freight_insurance_is_dropped_by_pricing_it_at_zero(reference):
    """There is no flag: the book already holds the price, and 0 is a price."""
    without = landed_cost(1_245_000, REFERENCE_CAR, REFERENCE_RATES,
                          replace(REFERENCE_COSTS, freight_insurance_usd=Decimal(0)))
    assert reference.freight_insurance_jpy > 0
    assert without.freight_insurance_jpy == 0
    assert without.total_eur < reference.total_eur


def test_fixed_expenses_are_the_base_plus_road_tax(reference):
    """Spec §2: seven bills that never vary, plus the one that depends on the car."""
    base = REFERENCE_COSTS.fixed_expenses_base_eur
    assert base == Decimal(1_088)
    assert reference.fixed_expenses_eur == base + reference.road_tax_eur

    # And the dependence is on the *car*, not on an override: the dirtier car is
    # a different ModelSpec, because road tax is no longer a line you can
    # `replace` on the book. 180 g/km is €300 a year, a full year from 1 January.
    dirtier = landed_cost(1_245_000, replace(REFERENCE_CAR, co2_gkm=180),
                          REFERENCE_RATES, REFERENCE_COSTS, REFERENCE_REGISTRATION)
    assert dirtier.road_tax_eur == Decimal(300)
    assert dirtier.fixed_expenses_eur == base + 300


def test_freight_scales_with_the_box_not_the_price(reference):
    """Dimensions are the only car parameter the *freight* reads (spec §1)."""
    bigger = replace(REFERENCE_CAR, length_cm=Decimal(808))
    twice = landed_cost(1_245_000, bigger, REFERENCE_RATES, REFERENCE_COSTS,
                        REFERENCE_REGISTRATION)
    assert twice.freight_jpy == reference.freight_jpy * 2
    # …and with nothing else: doubling the box must not move the road tax, which
    # is a function of CO₂ and of the calendar, and of neither dimension.
    assert twice.road_tax_eur == reference.road_tax_eur


# --- road tax ----------------------------------------------------------------


@pytest.mark.parametrize("co2,annual", [
    (90, 45),      # wholly inside the first band
    (120, 60),     # the first band's own top
    (121, 63),     # one gram past the top: that gram costs 6x the ones below it
    (133, 99),     # the CX-30, and the worked example in costs.toml
    (150, 150),
    (180, 300),
    (200, 500),
    (300, 1_500),  # the cap, reached exactly
])
def test_the_co2_scale_is_marginal_not_banded(co2, annual):
    """Law 47(I)/2019: the rate applies to the *portion*, like income-tax brackets.

    Every figure here is a checkpoint published against the law, not one this
    module produced. Read as flat bands instead, 133 g/km would pay €399 rather
    than €99 — four times over, on exactly the ordinary car this project imports.
    """
    assert REFERENCE_COSTS.annual_road_tax_eur(co2) == (Decimal(str(annual)), False)


def test_the_annual_fee_is_capped_and_says_so():
    """Above 300 g/km the answer stops depending on the input, which is worth saying."""
    assert REFERENCE_COSTS.annual_road_tax_eur(400) == (Decimal(1_500), True)
    assert REFERENCE_COSTS.annual_road_tax_eur(299)[1] is False


def test_registering_in_august_pays_for_august_to_december():
    """Cyprus circulation tax runs to 31 December whatever month you register in.

    129 days of 365 at the CX-30's €99 a year. Charging the full year would put
    €64 on the landed cost that nobody collects until January.
    """
    tax = REFERENCE_COSTS.road_tax_at_registration(133, date(2026, 8, 25))
    assert tax.annual_eur == Decimal(99)
    assert (tax.days, tax.days_in_year) == (129, 365)
    assert round(tax.part_year_eur, 2) == Decimal("34.99")
    assert tax.total_eur == tax.part_year_eur  # no surcharge without a Euro standard


def test_registering_on_new_years_day_pays_the_whole_year():
    """The boundary the fixture leans on, asserted rather than assumed."""
    tax = REFERENCE_COSTS.road_tax_at_registration(133, date(2026, 1, 1))
    assert (tax.days, tax.days_in_year) == (365, 365)
    assert tax.part_year_eur == tax.annual_eur


def test_registering_on_new_years_eve_still_pays_a_day():
    """Inclusive of the day of registration — the last day is a day, not zero."""
    tax = REFERENCE_COSTS.road_tax_at_registration(133, date(2026, 12, 31))
    assert tax.days == 1
    assert tax.part_year_eur == Decimal(99) / 365


def test_a_leap_year_is_counted_as_one():
    """366 days in and 366 days out, so the fraction is right on both halves."""
    tax = REFERENCE_COSTS.road_tax_at_registration(133, date(2028, 1, 1))
    assert (tax.days, tax.days_in_year) == (366, 366)
    assert tax.part_year_eur == tax.annual_eur


def test_road_tax_defaults_to_the_rate_date_not_to_today():
    """The clockless contract: a re-render in December must not reprice August.

    ``REFERENCE_RATES`` were quoted on 1 January, so the car registers on 1
    January and pays a full year — whatever day this test is run on.
    """
    priced = landed_cost(1_245_000, REFERENCE_CAR, REFERENCE_RATES, REFERENCE_COSTS)
    assert priced.road_tax.registered_on == REFERENCE_RATES.fetched_at.date()
    assert priced.road_tax.part_year_eur == priced.road_tax.annual_eur


def test_a_spec_with_no_co2_refuses_to_price_rather_than_charging_nothing():
    """€45 to €1,500 a year is too wide a hole to fill with a zero.

    ``margin_for`` turns this into the sentence the card prints; the raise is
    what makes sure no path quietly lands a car with no road tax on it.
    """
    with pytest.raises(ValueError, match="no CO₂ figure"):
        landed_cost(1_245_000, replace(REFERENCE_CAR, co2_gkm=None),
                    REFERENCE_RATES, REFERENCE_COSTS)


def test_the_registration_surcharge_is_paid_once_and_not_pro_rated():
    """A Euro 4 diesel owes €600 on the day, whatever month it registers in."""
    august = REFERENCE_COSTS.road_tax_at_registration(
        133, date(2026, 8, 25), euro_standard="4", fuel="diesel")
    january = REFERENCE_COSTS.road_tax_at_registration(
        133, date(2026, 1, 1), euro_standard="4", fuel="diesel")
    assert august.surcharge_eur == january.surcharge_eur == Decimal(600)
    assert august.part_year_eur < january.part_year_eur


@pytest.mark.parametrize("euro_standard,fuel,surcharge", [
    ("6", "petrol", 0),
    ("6d", "diesel", 0),        # written half a dozen ways in the wild
    ("Euro 6", "petrol", 0),
    ("7", "diesel", 0),         # a later standard is a cleaner car, not an error
    ("5b", "diesel", 50),
    ("5a", "petrol", 100),
    ("5", "petrol", 100),       # a bare 5 reads as 5a, the dearer of the two
    ("4", "diesel", 600),
    ("2", "petrol", 300),       # "Euro 4 and older" is one row
    ("6", "hybrid", 0),         # a hybrid burns petrol
    ("4", None, 600),           # unrecorded fuel takes the dearer column
])
def test_the_surcharge_table_is_read_the_way_the_law_writes_it(
        euro_standard, fuel, surcharge):
    assert REFERENCE_COSTS.registration_surcharge.for_car(euro_standard, fuel) == \
        (Decimal(surcharge), False)


def test_an_unrecorded_euro_standard_charges_nothing_and_flags_it():
    """Not the €600 bottom row: the real answer is almost certainly €0.

    Charging the dearest row to an unknown would put a fiction on the card.
    The flag is how the gap stays visible without inventing either number — the
    same bargain ``above_fee_table`` makes.
    """
    for missing in (None, "", "unknown"):
        assert REFERENCE_COSTS.registration_surcharge.for_car(missing, "diesel") == \
            (Decimal(0), True)
    tax = REFERENCE_COSTS.road_tax_at_registration(133, date(2026, 8, 25), None, "diesel")
    assert tax.euro_standard_assumed is True
    assert "Euro standard not recorded" in tax.describe()


def test_a_road_tax_scale_out_of_order_is_a_bad_book():
    """A marginal scale read out of order compounds every band below it."""
    scrambled = replace(REFERENCE_COSTS, road_tax_bands=(
        RoadTaxBand(150, Decimal("3.00")),
        RoadTaxBand(120, Decimal("0.50")),
        RoadTaxBand(None, Decimal("10.00")),
    ))
    assert any("ascending" in problem for problem in scrambled.problems())


def test_only_the_last_band_may_run_open_ended():
    """An open band in the middle would swallow every band after it."""
    open_middle = replace(REFERENCE_COSTS, road_tax_bands=(
        RoadTaxBand(None, Decimal("0.50")),
        RoadTaxBand(150, Decimal("3.00")),
    ))
    assert any("open-ended" in problem for problem in open_middle.problems())
    assert replace(REFERENCE_COSTS, road_tax_bands=()).problems() == ["no road tax bands"]


# --- the margin --------------------------------------------------------------


def test_margin_is_cyprus_less_landed_less_resale_costs(reference):
    margin = Margin(landed=reference, cyprus_eur=Decimal(15_000),
                    resale_costs_eur=Decimal(200))
    assert round(margin.gap_eur) == Decimal(15_000) - round(reference.total_eur) - 200
    assert margin.margin_pct == margin.gap_eur * 100 / reference.total_eur


def test_a_missing_cyprus_estimate_still_carries_the_landed_cost(reference):
    """The two halves fail independently — a landed cost is useful on its own."""
    margin = Margin(landed=reference, cyprus_eur=None, resale_costs_eur=Decimal(0),
                    reason="no Cyprus listings for NISSAN Note e-Power")
    assert margin.gap_eur is None
    assert margin.margin_pct is None
    assert "€11,797" in margin.describe()
    assert "no Cyprus listings" in margin.describe()


def test_margin_can_be_negative(reference):
    """A car that loses money must print a loss, not a blank or a zero."""
    margin = Margin(landed=reference, cyprus_eur=Decimal(5_000),
                    resale_costs_eur=Decimal(0))
    assert margin.gap_eur < 0
    assert margin.margin_pct < 0
    assert "-" in margin.describe()
