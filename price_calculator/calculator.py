"""What a Japanese auction car costs on Cyprus plates, and what it is worth here.

A port of the ``Calculator`` and ``Exporter service fees`` sheets described in
``price_calculator_spec.md``.

**This module holds no prices.** Not one fee, rate or bill — those are a
:class:`CostBook`, read from ``inputs/costs.toml`` by
:mod:`price_calculator.sources` and passed in. What lives here is the
arithmetic and the argument for it, which is true for years; what lives in the
file is what somebody charges this month. Keeping them in one place meant that
raising an exporter's fee edited a module whose comments then lied about the
bands, and turned twelve arithmetic tests red for a reason that had nothing to
do with arithmetic. See ``docs/adr/0003-prices-are-data-not-code.md``.

**This module is also pure.** No database, no network, no clock — the same
inputs give the same money forever. That is not tidiness for its own sake: both
the rates and the prices a quote was produced with are *inputs recorded on the
answer* (:class:`Rates`, :class:`CostBook`), so re-rendering a run in September
gives September's page and August's decision. The reading of files and the
fetching of rates live in :mod:`price_calculator.sources`, exactly as
:mod:`bazaraki.analysis` keeps its DB wrappers at the bottom and its arithmetic
free of them.

Four names, used verbatim here and on the report:

* **``landed cost``** — everything paid between the hammer falling in Japan and
  the car sitting on Cyprus plates. Your margin is *not* in it.
* **``Cyprus estimate``** — what the same car realistically sells for here,
  from :func:`bazaraki.analysis.estimate_sale_price`.
* **``margin``** — ``Cyprus estimate − landed cost − resale costs``, in EUR and
  as a percentage of the landed cost.
* **``cost book``** — every price in force on a date that is not a property of
  one car.

Money is :class:`~decimal.Decimal` from end to end and is rounded only when it
is printed. The sheet rounds per cell, so a port disagrees with a screenshot by
a euro or two; the reference car in §6 of the spec comes out at €11,763 here
against the sheet's €11,760, and that gap is the rounding, not a defect.

**Nothing about one car raises.** A missing model spec, an auction price off the
end of the fee table, an unpriced Cyprus market — each resolves to a ``reason``
string sitting where the number would go, the same rule :mod:`banzai24.bidding`
follows and for the same reason: one car that cannot be priced must not cost you
the page. A bad *cost book* is the exception and does raise, because it is not
about one car: see :class:`CostBook`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

# --- rates -------------------------------------------------------------------


@dataclass(frozen=True)
class Rates:
    """The two exchange rates a quote was produced with, and where they came from.

    Carried *on* the answer rather than looked up inside it. A landed cost is a
    statement about a moment: the rate that decides whether you bid on a car on
    the 22nd is the rate on the 22nd, and re-opening the report in September must
    not retroactively rewrite it. :mod:`price_calculator.sources` fetches these
    once and stamps them into the run.

    These are the *market* rates, as the ECB publishes them. The haircut taken
    off EUR/JPY before converting is a policy of yours, not a fact of the
    market, so it lives in the :class:`CostBook` — and the effective rate is
    :meth:`CostBook.eur_jpy_effective`, where the two meet.
    """

    usd_jpy: Decimal
    eur_jpy_market: Decimal
    fetched_at: datetime
    source: str = "frankfurter.dev"

    def describe(self) -> str:
        return (f"¥{self.eur_jpy_market:.2f}/€ · ¥{self.usd_jpy:.2f}/$ · "
                f"{self.fetched_at:%Y-%m-%d %H:%M} {self.source}")


# --- the model spec ----------------------------------------------------------


@dataclass(frozen=True)
class ModelSpec:
    """The manufacturer's figures for one model, over a span of years.

    A **Model spec** is true of every car of that model; a lot is one car on one
    day. Dimensions are the only thing here the landed cost reads — they drive
    shipping volume, and freight was 17% of the CNF price on the sheet's own
    reference car, so this is not a rounding input.

    ``co2_gkm`` is **recorded and unread**: road tax is flat in the cost book
    today (spec §5). ``body_model_code`` is likewise descriptive — a
    comma-separated list of the codes this row covers, for a human checking that
    a lot belongs to this row.

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
    line: int = 0  # for the overlap error, which names both rows

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


# --- the cost book -----------------------------------------------------------


@dataclass(frozen=True)
class ServiceFeeTier:
    """One row of the exporter's fee table: pay ``fee_jpy`` at or below ``up_to_jpy``."""

    up_to_jpy: Decimal
    fee_jpy: Decimal


@dataclass(frozen=True)
class CostBook:
    """Every price in force on a date that is not a property of one car.

    Read from ``inputs/costs.toml``; edited when a supplier or the state changes
    a price, never when the arithmetic changes. It is stamped into a run beside
    the rates, so a report re-opened in September still shows the prices that
    priced it — raising a number in the file changes what you bid tomorrow, not
    what you decided last week.

    The fields are flat and named for the file's own sections. There is no
    default anywhere: a cost book that cannot be read is not a cost book with
    holes in it, and a fallback copy of the prices living here would be the copy
    that goes quietly stale, which is the whole thing this class exists to stop.
    """

    # the exporter, JPY
    service_fee_tiers: tuple[ServiceFeeTier, ...]
    exporter_fixed_fee_jpy: Decimal
    certificate_of_origin_jpy: Decimal

    # shipping, USD
    roro_per_m3_usd: Decimal
    freight_insurance_usd: Decimal

    # the state
    vat_rate: Decimal
    duty_rate: Decimal

    # money moving
    bank_fx_rate: Decimal
    international_transfer_eur: Decimal
    eur_jpy_spread: Decimal

    # the bills at this end, EUR
    sva_test_eur: Decimal
    mot_eur: Decimal
    registration_eur: Decimal
    customs_clearance_eur: Decimal
    number_plates_eur: Decimal
    car_service_eur: Decimal
    insurance_eur: Decimal
    road_tax_eur: Decimal

    # selling it again
    resale_costs_eur: Decimal

    updated: date | None = None
    source: str = ""

    @property
    def fixed_expenses_base_eur(self) -> Decimal:
        """``Calculator!B21:B29`` less road tax — the seven bills that never vary.

        Road tax is separate because it is the one line that is a function of the
        car (spec §5), even though it is a flat figure today. Nothing here can be
        switched off: every item is a bill that arrives before a buyer can take
        the keys, and a flag to exclude the ownership-flavoured ones — service,
        insurance, road tax, some €181 of an €11,760 total — would be a switch
        that can only ever make a car look cheaper, which is a switch that gets
        left on.
        """
        return (self.sva_test_eur + self.mot_eur + self.registration_eur
                + self.customs_clearance_eur + self.number_plates_eur
                + self.car_service_eur + self.insurance_eur)

    def eur_jpy_effective(self, rates: Rates) -> Decimal:
        """The rate the conversion is actually done at — market less the haircut."""
        return rates.eur_jpy_market - self.eur_jpy_spread

    def service_fee_jpy(self, auction_price_jpy: Decimal) -> tuple[Decimal, bool]:
        """``(fee, above_table)`` for one hammer price.

        Above the last band a sorted ``VLOOKUP`` keeps returning the last row, so
        this does too — but says so, because the sheet's silence there is an
        accident of how ``VLOOKUP`` works rather than a quoted price. Nothing in
        ``bid_prices.csv`` comes close today; the flag is for the day something
        does.
        """
        for tier in self.service_fee_tiers:
            if auction_price_jpy <= tier.up_to_jpy:
                return tier.fee_jpy, False
        return self.service_fee_tiers[-1].fee_jpy, True

    def problems(self) -> list[str]:
        """Everything wrong with this book, in words, or an empty list.

        Pure, so the invariants live next to the arithmetic that relies on them;
        :func:`price_calculator.sources.load_cost_book` calls it and turns the
        result into a ``CostBookError`` naming the file. Checked here rather than
        trusted: these numbers are hand-edited monthly, and every one of them
        multiplies through to a figure you bid against.
        """
        found: list[str] = []
        if not self.service_fee_tiers:
            found.append("no exporter service fee tiers")
        previous: Decimal | None = None
        for tier in self.service_fee_tiers:
            if tier.up_to_jpy <= 0 or tier.fee_jpy < 0:
                found.append(f"service fee tier up to ¥{tier.up_to_jpy:,} is not a price")
            if previous is not None and tier.up_to_jpy <= previous:
                found.append(
                    f"service fee tiers are not ascending: ¥{tier.up_to_jpy:,} "
                    f"follows ¥{previous:,}")
            previous = tier.up_to_jpy

        for name in ("exporter_fixed_fee_jpy", "certificate_of_origin_jpy",
                     "roro_per_m3_usd", "freight_insurance_usd", "vat_rate",
                     "duty_rate", "bank_fx_rate", "international_transfer_eur",
                     "eur_jpy_spread", "sva_test_eur", "mot_eur",
                     "registration_eur", "customs_clearance_eur",
                     "number_plates_eur", "car_service_eur", "insurance_eur",
                     "road_tax_eur", "resale_costs_eur"):
            if getattr(self, name) < 0:
                found.append(f"{name} is negative")

        if self.roro_per_m3_usd <= 0:
            found.append("roro_per_m3_usd is not a price")
        for rate in ("vat_rate", "duty_rate", "bank_fx_rate"):
            if getattr(self, rate) > 1:
                found.append(f"{rate} is above 1 — rates are fractions, not percents")
        return found

    def describe(self) -> str:
        when = f"{self.updated:%Y-%m-%d}" if self.updated else "undated"
        return (f"{when} · {self.source or 'no source given'} · "
                f"RoRo ${self.roro_per_m3_usd}/m³ · VAT {self.vat_rate * 100:.0f}% · "
                f"FX haircut ¥{self.eur_jpy_spread}/€")


# --- what you get back -------------------------------------------------------


@dataclass(frozen=True)
class LandedCost:
    """One car, priced from hammer to plates. The breakdown *is* the answer.

    The sheet's value was never the total — it was showing where the money goes,
    and a module that returned one number would have ported the arithmetic and
    thrown away the point. Spec §4 lists these fields; they are all here, along
    with the two things that decided them: the rates and the cost book.
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
    costs: CostBook
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
        costs: CostBook,
) -> LandedCost:
    """Spec §3, end to end. Raises only on an auction price that cannot be one.

    A hammer price of zero or less is not a car that is hard to price — it is a
    caller bug, and swallowing it into a ``reason`` string would hide the bug
    behind a blank cell on a report. Everything *else* that can go wrong is a
    missing input handled before this is called.

    ``costs`` has no default, deliberately. A default would either be prices
    living in this module — the thing ADR 0003 removed — or a read of the disk
    from inside a function that promises to touch nothing. Priced with a
    different book? ``dataclasses.replace(costs, duty_rate=Decimal("0.10"))``,
    which is also how you price one car as an exception without inventing a
    second override mechanism.
    """
    auction_price_jpy = Decimal(auction_price_jpy)
    if auction_price_jpy <= 0:
        raise ValueError(f"auction price must be positive, not {auction_price_jpy}")

    service_fee, above_table = costs.service_fee_jpy(auction_price_jpy)
    exporter_fees = (service_fee + costs.exporter_fixed_fee_jpy
                     + costs.certificate_of_origin_jpy)

    volume = spec.volume_m3
    freight = costs.roro_per_m3_usd * rates.usd_jpy * volume
    insurance = costs.freight_insurance_usd * rates.usd_jpy

    cnf_jpy = auction_price_jpy + exporter_fees + freight + insurance
    cnf_eur = cnf_jpy / costs.eur_jpy_effective(rates)

    bank = cnf_eur * costs.bank_fx_rate + costs.international_transfer_eur
    duty = cnf_eur * costs.duty_rate
    # VAT is charged on the customs value **plus duty** — not on the bank fees
    # and not on the local expenses, which are paid after the car clears.
    vat = (cnf_eur + duty) * costs.vat_rate
    fixed = costs.fixed_expenses_base_eur + costs.road_tax_eur

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
        road_tax_eur=costs.road_tax_eur,
        to_pay_in_cyprus_eur=to_pay,
        total_eur=cnf_eur + to_pay,
        volume_m3=volume,
        spec=spec,
        rates=rates,
        costs=costs,
        above_fee_table=above_table,
    )


# --- landed cost against the Cyprus market -----------------------------------


@dataclass(frozen=True)
class Margin:
    """Landed cost against what the car sells for here. Both numbers, always.

    ``gap_eur`` is what you keep; ``margin_pct`` is that as a percentage of what
    you spent. Neither is a decision — the Cyprus figure behind it is a curve
    fitted to asking prices with a resale haircut on top, and both halves of that
    have error bars far wider than the €25 of freight precision this module
    frets about elsewhere.

    ``resale_costs_eur`` — transfer of ownership, advertising — comes off the
    book, and is zero there until you have receipts. It does **not** hold VAT.
    You import as a private person, so the €1,679 of import VAT in
    :attr:`LandedCost.vat_eur` is a real sunk cost you never reclaim, and you
    sell VAT-free into a market whose asking prices are largely set by
    VAT-registered dealers. That asymmetry is the single biggest thing standing
    between the margins this module prints and the money you actually keep, and
    no line in the cost book captures it.
    """

    landed: LandedCost
    cyprus_eur: Decimal | None
    resale_costs_eur: Decimal
    cyprus_confidence: str | None = None
    adjustment_factor: Decimal | None = None
    reason: str | None = None  # why there is no Cyprus number
    warning: str | None = None  # the estimate exists but do not lean on it

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
