"""What a Japanese auction car costs on Cyprus plates, and what it is worth here.

A port of the ``Calculator`` and ``Exporter service fees`` sheets described in
``price_calculator_spec.md``. Every constant below carries the cell it came
from, so the two can be kept in step.

**This module is pure.** No database, no network, no clock — the same inputs
give the same money forever. That is not tidiness for its own sake: the rates a
quote was produced with are an *input* recorded on the answer
(:class:`Rates`), so re-rendering a run in September gives September's page and
August's decision. The reading of files and the fetching of rates live in
:mod:`price_calculator.sources`, exactly as :mod:`bazaraki.analysis` keeps its
DB wrappers at the bottom and its arithmetic free of them.

Three names, used verbatim here and on the report:

* **``landed cost``** — everything paid between the hammer falling in Japan and
  the car sitting on Cyprus plates. Your margin is *not* in it.
* **``Cyprus estimate``** — what the same car realistically sells for here,
  from :func:`bazaraki.analysis.estimate_sale_price`.
* **``margin``** — ``Cyprus estimate − landed cost − resale costs``, in EUR and
  as a percentage of the landed cost.

Money is :class:`~decimal.Decimal` from end to end and is rounded only when it
is printed. The sheet rounds per cell, so a port disagrees with a screenshot by
a euro or two; the reference car in §6 of the spec comes out at €11,763 here
against the sheet's €11,760, and that gap is the rounding, not a defect.

**Nothing about one car raises.** A missing model spec, an auction price off the
end of the fee table, an unpriced Cyprus market — each resolves to a ``reason``
string sitting where the number would go, the same rule :mod:`banzai24.bidding`
follows and for the same reason: one car that cannot be priced must not cost you
the page.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

# --- rates -------------------------------------------------------------------

# ``Calculator!B32``. The sheet reads EUR/JPY live and then knocks two yen off
# it. That is not a fee anyone charges — it is a deliberate haircut on the rate
# the bank actually gives you, and it errs towards the car costing more. Left as
# a named constant rather than folded into the arithmetic so it can be argued
# with; USD/JPY deliberately has no equivalent.
EUR_JPY_SPREAD = Decimal(2)


@dataclass(frozen=True)
class Rates:
    """The two exchange rates a quote was produced with, and where they came from.

    Carried *on* the answer rather than looked up inside it. A landed cost is a
    statement about a moment: the rate that decides whether you bid on a car on
    the 22nd is the rate on the 22nd, and re-opening the report in September must
    not retroactively rewrite it. :mod:`price_calculator.sources` fetches these
    once and stamps them into the run.
    """

    usd_jpy: Decimal
    eur_jpy_market: Decimal
    fetched_at: datetime
    source: str = "frankfurter.dev"

    @property
    def eur_jpy_effective(self) -> Decimal:
        """The rate the conversion is actually done at — market less the haircut."""
        return self.eur_jpy_market - EUR_JPY_SPREAD

    def describe(self) -> str:
        return (f"¥{self.eur_jpy_market:.2f}/€ (−{EUR_JPY_SPREAD} → "
                f"{self.eur_jpy_effective:.2f}) · ¥{self.usd_jpy:.2f}/$ · "
                f"{self.fetched_at:%Y-%m-%d %H:%M} {self.source}")


# --- the model spec ----------------------------------------------------------


@dataclass(frozen=True)
class ModelSpec:
    """The manufacturer's figures for one model, over a span of years.

    A **Model spec** is true of every car of that model; a lot is one car on one
    day. Dimensions are the only thing here the landed cost reads — they drive
    shipping volume, and freight was 17% of the CNF price on the sheet's own
    reference car, so this is not a rounding input.

    ``co2_gkm`` is **recorded and unread**: road tax is a flat
    :data:`ROAD_TAX_EUR` today (spec §5). ``body_model_code`` is likewise
    descriptive — a comma-separated list of the codes this row covers, for a
    human checking that a lot belongs to this row.

    Both fields are the upgrade path rather than dead weight. ``body_model_code``
    is what separates a hybrid RAV4 (``AXAH54``) from a petrol one (``MXAA54``),
    and it separates them *more reliably than fuel does* — ``auction.db`` holds
    the same ``KFEP`` six times with a null ``fuel_type`` and four times as
    ``petrol``. On the day road tax becomes a function of CO₂, the key here
    should grow to ``(make, model, year, body_model_code)`` and this column
    becomes it. Not before: the dimensional difference between a 2WD and an AWD
    of the same generation is about a centimetre of height — 0.1 m³, some €25 of
    freight — and precision worth €25 is not worth an untested fallback path.
    """

    make: str
    model: str
    year_from: int
    year_to: int
    length_cm: Decimal
    width_cm: Decimal
    height_cm: Decimal
    co2_gkm: int | None = None
    body_model_code: str | None = None
    line: int = 0                      # for the overlap error, which names both rows

    @property
    def volume_m3(self) -> Decimal:
        """The shipping box, ``Calculator!B38:B40`` — length × width × height."""
        return (self.length_cm * self.width_cm * self.height_cm) / Decimal(1_000_000)

    def covers(self, year: int) -> bool:
        return self.year_from <= year <= self.year_to

    def describe(self) -> str:
        return (f"{self.make} {self.model} {self.year_from}–{self.year_to} · "
                f"{self.length_cm:.0f}×{self.width_cm:.0f}×{self.height_cm:.0f} cm · "
                f"{self.volume_m3:.2f} m³")


# --- exporter fees, JPY ------------------------------------------------------

# ``Exporter service fees!A3:C6``, a sorted VLOOKUP on the auction price. Bands
# are inclusive of their upper bound, which is how the sheet's own boundaries
# read: ¥1,000,000 pays ¥56,000 and ¥1,000,001 pays ¥71,000.
SERVICE_FEE_TIERS: tuple[tuple[int, int], ...] = (
    (1_000_000, 59_000),
    (1_500_000, 74_000),
    (2_000_000, 94_000),
    (2_500_000, 114_000),
    (9_000_000, 134_000),
)

EXPORTER_FIXED_FEE_JPY = Decimal(17_000)     # C8 = 10,000 + 7,000
CERTIFICATE_OF_ORIGIN_JPY = Decimal(1_200)   # C9
RORO_PRICE_PER_M3_USD = Decimal(166)         # C10
FREIGHT_INSURANCE_USD = Decimal(50)          # B19, flat


def service_fee_jpy(auction_price_jpy: Decimal) -> tuple[Decimal, bool]:
    """``(fee, above_table)`` for one hammer price.

    Above ¥9,000,000 a sorted ``VLOOKUP`` keeps returning the last tier, so this
    does too — but says so, because the sheet's silence there is an accident of
    how ``VLOOKUP`` works rather than a quoted price. Nothing in
    ``bid_prices.csv`` comes close today; the flag is for the day something does.
    """
    for upper, fee in SERVICE_FEE_TIERS:
        if auction_price_jpy <= upper:
            return Decimal(fee), False
    return Decimal(SERVICE_FEE_TIERS[-1][1]), True


# --- taxes and fixed expenses, EUR -------------------------------------------

VAT_RATE = Decimal("0.19")      # Calculator!B34, Cyprus
DUTY_RATE = Decimal("0")        # Calculator!B35 — see below

# ``Calculator!B35``: *"Starting January 2026, import duty on cars originating
# from Japan is reduced to zero, provided that all required documentation is
# submitted."* Zero is therefore a *condition being met*, not a property of the
# world, and it is a parameter of :func:`landed_cost` for exactly that reason.

# ``Calculator!B21:B29`` less road tax, which is the one line that varies per
# car. Every item is a bill that arrives before a buyer can take the keys, so
# there is no flag to exclude the ownership-flavoured ones (spec §7 offers one):
# service, insurance and road tax are €181 of an €11,760 landed cost, and a
# switch that can only ever make a car look cheaper is a switch that will
# eventually be left on.
SVA_TEST_EUR = Decimal(140)                  # B22
MOT_EUR = Decimal(35)                        # B23
REGISTRATION_EUR = Decimal(200)              # B24 = 150 department + 50 agent
CUSTOMS_CLEARANCE_EUR = Decimal(513)         # B25 = 339+10+10+10+15+10+119
NUMBER_PLATES_EUR = Decimal(30)              # B26
CAR_SERVICE_EUR = Decimal(120)               # B27, oil and filters
INSURANCE_EUR = Decimal(50)                  # B29

FIXED_EXPENSES_BASE_EUR = (SVA_TEST_EUR + MOT_EUR + REGISTRATION_EUR
                           + CUSTOMS_CLEARANCE_EUR + NUMBER_PLATES_EUR
                           + CAR_SERVICE_EUR + INSURANCE_EUR)   # 1,088

# ``Calculator!B28``. Road tax is a function of CO₂ (spec §5); €11 is the sheet's
# figure for its reference car and stands in until the Cyprus band table is
# ported. It is 0.09% of the total, which is why it can wait — and why
# ``co2_gkm`` sits unread in :class:`ModelSpec` rather than driving anything.
ROAD_TAX_EUR = Decimal(11)

REVOLUT_FX_RATE = Decimal("0.01")        # Calculator!B10, 1% on the exchange
INTERNATIONAL_TRANSFER_EUR = Decimal(60)  # two payments × €30


# --- what you get back -------------------------------------------------------


@dataclass(frozen=True)
class LandedCost:
    """One car, priced from hammer to plates. The breakdown *is* the answer.

    The sheet's value was never the total — it was showing where the money goes,
    and a module that returned one number would have ported the arithmetic and
    thrown away the point. Spec §4 lists these fields; they are all here.
    """

    auction_price_jpy: Decimal
    exporter_fees_jpy: Decimal
    freight_jpy: Decimal
    freight_insurance_jpy: Decimal
    cnf_price_jpy: Decimal

    cnf_price_eur: Decimal
    bank_transfer_fees_eur: Decimal
    duty_eur: Decimal
    vat_eur: Decimal
    fixed_expenses_eur: Decimal
    road_tax_eur: Decimal
    to_pay_in_cyprus_eur: Decimal
    total_eur: Decimal

    volume_m3: Decimal
    spec: ModelSpec
    rates: Rates
    above_fee_table: bool = False

    def lines(self) -> list[str]:
        """The breakdown, one string per line, in the order the money is spent."""
        return [
            f"auction ¥{self.auction_price_jpy:,.0f}",
            f"exporter ¥{self.exporter_fees_jpy:,.0f}",
            f"freight ¥{self.freight_jpy:,.0f} ({self.volume_m3:.2f} m³)",
            f"insurance ¥{self.freight_insurance_jpy:,.0f}",
            f"CNF €{self.cnf_price_eur:,.0f}",
            f"VAT €{self.vat_eur:,.0f}",
            f"bank €{self.bank_transfer_fees_eur:,.0f}",
            f"Cyprus fixed €{self.fixed_expenses_eur:,.0f}",
            f"landed €{self.total_eur:,.0f}",
        ]

    def describe(self) -> str:
        return " · ".join(self.lines())


def landed_cost(
    auction_price_jpy: Decimal | int,
    spec: ModelSpec,
    rates: Rates,
    road_tax_eur: Decimal = ROAD_TAX_EUR,
    duty_rate: Decimal = DUTY_RATE,
    with_freight_insurance: bool = True,
) -> LandedCost:
    """Spec §3, end to end. Raises only on an auction price that cannot be one.

    A hammer price of zero or less is not a car that is hard to price — it is a
    caller bug, and swallowing it into a ``reason`` string would hide the bug
    behind a blank cell on a report. Everything *else* that can go wrong is a
    missing input handled before this is called.
    """
    auction_price_jpy = Decimal(auction_price_jpy)
    if auction_price_jpy <= 0:
        raise ValueError(f"auction price must be positive, not {auction_price_jpy}")

    service_fee, above_table = service_fee_jpy(auction_price_jpy)
    exporter_fees = service_fee + EXPORTER_FIXED_FEE_JPY + CERTIFICATE_OF_ORIGIN_JPY

    volume = spec.volume_m3
    freight = RORO_PRICE_PER_M3_USD * rates.usd_jpy * volume
    insurance = FREIGHT_INSURANCE_USD * rates.usd_jpy if with_freight_insurance else Decimal(0)

    cnf_jpy = auction_price_jpy + exporter_fees + freight + insurance
    cnf_eur = cnf_jpy / rates.eur_jpy_effective

    bank = cnf_eur * REVOLUT_FX_RATE + INTERNATIONAL_TRANSFER_EUR
    duty = cnf_eur * duty_rate
    # VAT is charged on the customs value **plus duty** — not on the bank fees
    # and not on the local expenses, which are paid after the car clears.
    vat = (cnf_eur + duty) * VAT_RATE
    fixed = FIXED_EXPENSES_BASE_EUR + road_tax_eur

    to_pay = bank + duty + vat + fixed

    return LandedCost(
        auction_price_jpy=auction_price_jpy,
        exporter_fees_jpy=exporter_fees,
        freight_jpy=freight,
        freight_insurance_jpy=insurance,
        cnf_price_jpy=cnf_jpy,
        cnf_price_eur=cnf_eur,
        bank_transfer_fees_eur=bank,
        duty_eur=duty,
        vat_eur=vat,
        fixed_expenses_eur=fixed,
        road_tax_eur=road_tax_eur,
        to_pay_in_cyprus_eur=to_pay,
        total_eur=cnf_eur + to_pay,
        volume_m3=volume,
        spec=spec,
        rates=rates,
        above_fee_table=above_table,
    )


# --- landed cost against the Cyprus market -----------------------------------

# What it costs to turn an imported car into cash, beyond landing it: transfer
# of ownership at the Department of Road Transport, and advertising. Zero until
# you have receipts — a made-up number here would be indistinguishable from a
# measured one on the page.
#
# It does **not** hold VAT. You import as a private person, so the €1,679 of
# import VAT in :attr:`LandedCost.vat_eur` is a real sunk cost you never reclaim,
# and you sell VAT-free into a market whose asking prices are largely set by
# VAT-registered dealers. That asymmetry is the single biggest thing standing
# between the margins this module prints and the money you actually keep.
RESALE_COSTS_EUR = Decimal(0)


@dataclass(frozen=True)
class Margin:
    """Landed cost against what the car sells for here. Both numbers, always.

    ``gap_eur`` is what you keep; ``margin_pct`` is that as a percentage of what
    you spent. Neither is a decision — the Cyprus figure behind it is a curve
    fitted to asking prices with a resale haircut on top, and both halves of that
    have error bars far wider than the €25 of freight precision this module
    frets about elsewhere.
    """

    landed: LandedCost
    cyprus_eur: Decimal | None
    resale_costs_eur: Decimal = RESALE_COSTS_EUR
    cyprus_confidence: str | None = None
    adjustment_factor: Decimal | None = None
    reason: str | None = None       # why there is no Cyprus number
    warning: str | None = None      # the estimate exists but do not lean on it

    @property
    def gap_eur(self) -> Decimal | None:
        if self.cyprus_eur is None:
            return None
        return self.cyprus_eur - self.landed.total_eur - self.resale_costs_eur

    @property
    def margin_pct(self) -> Decimal | None:
        gap = self.gap_eur
        if gap is None or self.landed.total_eur == 0:
            return None
        return gap * Decimal(100) / self.landed.total_eur

    def lines(self) -> list[str]:
        out = [f"landed €{self.landed.total_eur:,.0f}"]
        if self.cyprus_eur is None:
            out.append(self.reason or "no Cyprus estimate")
            return out
        conf = f" ({self.cyprus_confidence})" if self.cyprus_confidence else ""
        out.append(f"Cyprus €{self.cyprus_eur:,.0f}{conf}")
        out.append(f"margin €{self.gap_eur:,.0f} · {self.margin_pct:.1f}%")
        if self.adjustment_factor is not None:
            out.append(f"resale ×{self.adjustment_factor:.3f}")
        if self.warning:
            out.append(self.warning)
        return out

    def describe(self) -> str:
        return " · ".join(self.lines())
