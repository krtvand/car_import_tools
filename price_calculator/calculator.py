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

One line is deliberately **not** the sheet's any more. ``Calculator!B28`` held a
flat road tax; here it is computed per car from the Cyprus CO₂ scale and
pro-rated over the days left in the registration year (:class:`RoadTax`), which
is what the counter actually charges. Every other line still reconciles.

**Nothing about one car reaches the page as an exception.** A missing model
spec, a spec with no CO₂ figure, an auction price off the end of the fee table,
an unpriced Cyprus market — each resolves to a ``reason`` string sitting where
the number would go, the same rule :mod:`banzai24.bidding` follows and for the
same reason: one car that cannot be priced must not cost you the page. The two
that :func:`landed_cost` raises on are caught one layer up in
:func:`price_calculator.sources.margin_for` and turned into that sentence. A bad
*cost book* is the real exception and does raise all the way, because it is not
about one car: see :class:`CostBook`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


def _fold(value: str | None) -> str:
    """The same fold the rest of the project uses, for the two enum-ish columns.

    ``Euro 6d`` and ``euro6d`` are one Euro standard, and ``Diesel`` is
    ``diesel``. Nothing else in this module compares strings.
    """
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())

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

    ``co2_gkm`` **is read**, and is now the second thing here that spends money:
    road tax comes off the Cyprus CO₂ band table (spec §5), so a row with an
    empty ``co2_gkm`` cannot be priced at all and every lot matching it prints
    the reason instead of a landed cost. It is not defaulted, guessed or
    interpolated from a neighbouring model — a car's road tax runs from €45 to
    €1,500 a year across the scale, and a guess in the middle of that is worth
    less than a blank.

    ``euro_standard`` and ``fuel`` feed only the one-off registration surcharge,
    which is €0 for every Euro 6 car and so €0 for everything in
    ``model_specs.csv`` today. They are deliberately weaker inputs than
    ``co2_gkm``: an unrecorded ``euro_standard`` is priced at no surcharge and
    said out loud on the answer, and an unrecorded ``fuel`` is priced from the
    diesel column, the dearer of the two on every row.

    ``body_model_code`` is descriptive — a space-separated list of the codes this
    row covers, for a human checking that a lot belongs to this row. It is the
    upgrade path rather than dead weight: it is what separates a hybrid RAV4
    (``AXAH54``) from a petrol one (``MXAA54``), and it separates them *more
    reliably than fuel does* — ``auction.db`` holds the same ``KFEP`` six times
    with a null ``fuel_type`` and four times as ``petrol``.

    Now that road tax *is* a function of CO₂, the key here wants to grow to
    ``(make, model, year, body_model_code)``, because a hybrid and a petrol of
    one generation differ by far more CO₂ than they do centimetres. That is a
    real gap and not a hypothetical one; it is left open because closing it means
    a CO₂ figure per body code for every row, and one wrong band is €50–€100
    against the €25 of freight precision this module frets about elsewhere.
    Until then a row's ``co2_gkm`` is the generation's figure and the margin
    inherits that error.
    """

    make: str
    model: str
    year_from: int
    year_to: int
    length_cm: Decimal
    width_cm: Decimal
    height_cm: Decimal
    co2_gkm: int | None = None
    euro_standard: str | None = None
    fuel: str | None = None
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
class RoadTaxBand:
    """One bracket of the Cyprus CO₂ scale: ``eur_per_gram`` on the portion up to
    ``up_to_gkm``.

    **Marginal, not banded flat.** The law says "για μέρος μάζας …" — for the
    *portion* — so 133 g/km pays 120 × €0.50 plus 13 × €3.00, not 133 × €3.00.
    Reading it as a flat band would charge €399 where €99 is due, and it would
    be wrong in the expensive direction on exactly the ordinary cars this
    project imports.

    ``up_to_gkm`` is ``None`` on the last band only, which runs open-ended to
    :attr:`CostBook.road_tax_cap_eur`.
    """

    up_to_gkm: int | None
    eur_per_gram: Decimal


@dataclass(frozen=True)
class RegistrationSurcharge:
    """Schedule I §8(c): a one-off paid at first registration, by Euro standard × fuel.

    Not an annual fee and not pro-rated — it is paid once, on the day the car
    first goes onto Cyprus plates, which for an import is the day this
    calculation is about. Euro 6 pays nothing on either fuel, and every model in
    ``model_specs.csv`` is Euro 6, so this is €0 today for every car the project
    prices. It is modelled rather than assumed away because the first pre-2015
    car through it owes €600.
    """

    euro6_petrol_eur: Decimal
    euro6_diesel_eur: Decimal
    euro5b_petrol_eur: Decimal
    euro5b_diesel_eur: Decimal
    euro5a_petrol_eur: Decimal
    euro5a_diesel_eur: Decimal
    euro4_petrol_eur: Decimal
    euro4_diesel_eur: Decimal

    @staticmethod
    def _tier(euro_standard: str | None) -> str | None:
        """``"euro6"`` … ``"euro4"`` for anything nameable, else ``None``.

        Accepts ``6``, ``Euro 6``, ``6d``, ``EURO6D-TEMP`` — the column is
        hand-typed and the standard is written half a dozen ways in the wild.
        Two deliberate readings:

        * **Euro 7 and later read as Euro 6.** A later standard is a cleaner car
          and the table's top row is €0; refusing to price it would blank a
          landed cost over a car that plainly owes nothing.
        * **A bare ``5`` reads as Euro 5a**, the dearer of the two 5 rows. The
          law splits 5a from 5b and the column may not; guessing the cheaper one
          would flatter the total, and this is a place where a guess has a
          cautious side.
        """
        text = _fold(euro_standard).removeprefix("euro")
        if not text or not text[0].isdigit():
            return None
        generation = int(text[0])
        if generation >= 6:
            return "euro6"
        if generation == 5:
            return "euro5b" if text.startswith("5b") else "euro5a"
        return "euro4"

    def for_car(self, euro_standard: str | None,
                fuel: str | None) -> tuple[Decimal, bool]:
        """``(surcharge, euro_standard_assumed)`` for one car.

        An unreadable Euro standard is priced at **nothing** and flagged, rather
        than at the €300–€600 bottom row. The flag is the point: charging the
        dearest row to an unknown would put a €600 fiction on a card whose real
        answer is almost certainly €0, and the flag makes the gap visible without
        inventing either number. It is the mirror of ``above_fee_table`` — the
        table was not used, and the answer says so.

        An unreadable **fuel** takes no flag and the diesel column, which is the
        dearer on every row. Unlike the Euro standard, fuel has only two answers
        and one of them is safe.
        """
        tier = self._tier(euro_standard)
        if tier is None:
            return Decimal(0), True
        text = _fold(fuel)
        # Petrol, hybrid and electric all burn petrol or nothing; only diesel is
        # diesel, and an empty cell is priced as the dearer of the two.
        column = "petrol" if text and "diesel" not in text else "diesel"
        return getattr(self, f"{tier}_{column}_eur"), False


@dataclass(frozen=True)
class RoadTax:
    """``Calculator!B28``, worked out for one car on one day. The breakdown is the answer.

    Two separate bills that arrive together at the counter:

    * :attr:`part_year_eur` — the annual CO₂ fee, **pro-rated over the days left
      in the calendar year**. Cyprus circulation tax runs to 31 December
      whenever you register, so a car registered in August pays for August to
      December and pays again in January. Charging the full €99 to a car
      registered on the 25th of August would overstate the landed cost by €64.
    * :attr:`surcharge_eur` — the §8(c) one-off, paid once and never again.

    :attr:`annual_eur` is carried alongside because it is the number that recurs:
    it is what the buyer pays every January after, and it is the figure a
    Cyprus road-tax calculator will print if you check this against one.
    """

    annual_eur: Decimal
    part_year_eur: Decimal
    surcharge_eur: Decimal
    co2_gkm: int
    days: int
    days_in_year: int
    registered_on: date
    capped: bool = False
    euro_standard_assumed: bool = False

    @property
    def total_eur(self) -> Decimal:
        """What is actually paid at registration — the only figure the landed cost reads."""
        return self.part_year_eur + self.surcharge_eur

    def describe(self) -> str:
        parts = [f"€{self.total_eur:,.2f} at registration",
                 f"{self.co2_gkm} g/km",
                 f"€{self.annual_eur:,.2f}/yr × {self.days}/{self.days_in_year} d"]
        if self.surcharge_eur:
            parts.append(f"+ €{self.surcharge_eur:,.0f} one-off")
        if self.capped:
            parts.append("at the annual cap")
        if self.euro_standard_assumed:
            parts.append("Euro standard not recorded, no surcharge charged")
        return " · ".join(parts)


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
    road_tax_bands: tuple[RoadTaxBand, ...]
    road_tax_cap_eur: Decimal
    registration_surcharge: RegistrationSurcharge

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

    # selling it again
    resale_costs_eur: Decimal

    updated: date | None = None
    source: str = ""

    @property
    def fixed_expenses_base_eur(self) -> Decimal:
        """``Calculator!B21:B29`` less road tax — the seven bills that never vary.

        Road tax is separate because it is the one line that is a function of the
        car (spec §5) — it is not in this book as a number at all, only as the
        band table it is computed from. Nothing here can be switched off: every
        item is a bill that arrives before a buyer can take the keys, and a flag
        to exclude the ownership-flavoured ones — service, insurance, road tax,
        some €181 of an €11,760 total — would be a switch that can only ever make
        a car look cheaper, which is a switch that gets left on.
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
        the max bids come close today; the flag is for the day something
        does.
        """
        for tier in self.service_fee_tiers:
            if auction_price_jpy <= tier.up_to_jpy:
                return tier.fee_jpy, False
        return self.service_fee_tiers[-1].fee_jpy, True

    def annual_road_tax_eur(self, co2_gkm: int) -> tuple[Decimal, bool]:
        """``(fee, capped)`` — a full year of circulation tax at that CO₂ figure.

        The scale is marginal: each band charges its rate on the *portion* of the
        figure that falls inside it, and they are summed. See
        :class:`RoadTaxBand` for why that distinction is worth €300 on an
        ordinary car.

        ``capped`` says the CO₂-derived amount hit :attr:`road_tax_cap_eur`
        (€1,500, reached at 300 g/km) and was held there. It is reported for the
        same reason ``above_fee_table`` is: the answer is right, but it stopped
        depending on the input, and a number that has stopped moving with its
        input should say so rather than look computed.
        """
        co2 = Decimal(co2_gkm)
        due = Decimal(0)
        lower = Decimal(0)
        for band in self.road_tax_bands:
            upper = co2 if band.up_to_gkm is None else min(co2, Decimal(band.up_to_gkm))
            if upper <= lower:
                break
            due += (upper - lower) * band.eur_per_gram
            lower = upper
        if due > self.road_tax_cap_eur:
            return self.road_tax_cap_eur, True
        return due, False

    def road_tax_at_registration(
            self,
            co2_gkm: int,
            registered_on: date,
            euro_standard: str | None = None,
            fuel: str | None = None,
    ) -> RoadTax:
        """What the counter takes for road tax on the day the car is registered.

        **A part year, not a year.** Cyprus circulation tax is charged to 31
        December whatever month you register in, so an import registered in
        August buys August–December and buys January–December again in the new
        year. The landed cost is what it takes to put the car on plates *once*,
        so it carries the part year; the recurring figure is on the answer as
        :attr:`RoadTax.annual_eur` and is not added to anything.

        Pro-rated **by day**, inclusive of the day of registration — 25 August
        2026 is 129 days of 365. By whole month would be the coarser reading and
        is arguably closer to what a clerk bills, but it is €6 dearer on the same
        car and there is no reason to prefer the dearer of two guesses when the
        exact one is a subtraction.
        """
        annual, capped = self.annual_road_tax_eur(co2_gkm)
        year_end = date(registered_on.year, 12, 31)
        days = (year_end - registered_on).days + 1
        days_in_year = (year_end - date(registered_on.year, 1, 1)).days + 1
        surcharge, assumed = self.registration_surcharge.for_car(euro_standard, fuel)
        return RoadTax(
            annual_eur=annual,
            part_year_eur=annual * days / days_in_year,
            surcharge_eur=surcharge,
            co2_gkm=co2_gkm,
            days=days,
            days_in_year=days_in_year,
            registered_on=registered_on,
            capped=capped,
            euro_standard_assumed=assumed,
        )

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

        # The road tax scale, checked exactly as hard as the fee table above and
        # for a stronger reason: a fee tier read out of order costs one band's
        # difference, while a marginal scale read out of order compounds every
        # band below it.
        if not self.road_tax_bands:
            found.append("no road tax bands")
        edge: int | None = None
        for index, band in enumerate(self.road_tax_bands):
            last = index == len(self.road_tax_bands) - 1
            if band.eur_per_gram < 0:
                found.append(f"road tax band #{index + 1} charges a negative rate")
            if band.up_to_gkm is None:
                if not last:
                    found.append(
                        f"road tax band #{index + 1} has no up_to_gkm — only the "
                        f"last band may run open-ended")
                continue
            if band.up_to_gkm <= 0:
                found.append(f"road tax band up to {band.up_to_gkm} g/km is not a band")
            if edge is not None and band.up_to_gkm <= edge:
                found.append(
                    f"road tax bands are not ascending: {band.up_to_gkm} g/km "
                    f"follows {edge} g/km")
            edge = band.up_to_gkm
        if self.road_tax_cap_eur <= 0:
            found.append("road_tax_cap_eur is not a cap")

        surcharges = (
            "euro6_petrol_eur", "euro6_diesel_eur", "euro5b_petrol_eur",
            "euro5b_diesel_eur", "euro5a_petrol_eur", "euro5a_diesel_eur",
            "euro4_petrol_eur", "euro4_diesel_eur")
        for name in surcharges:
            if getattr(self.registration_surcharge, name) < 0:
                found.append(f"registration surcharge {name} is negative")

        for name in ("exporter_fixed_fee_jpy", "certificate_of_origin_jpy",
                     "roro_per_m3_usd", "freight_insurance_usd", "vat_rate",
                     "duty_rate", "bank_fx_rate", "international_transfer_eur",
                     "eur_jpy_spread", "sva_test_eur", "mot_eur",
                     "registration_eur", "customs_clearance_eur",
                     "number_plates_eur", "car_service_eur", "insurance_eur",
                     "resale_costs_eur"):
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
    road_tax: RoadTax
    to_pay_in_cyprus_eur: Decimal
    total_eur: Decimal

    volume_m3: Decimal
    spec: ModelSpec
    rates: Rates
    costs: CostBook
    above_fee_table: bool = False

    @property
    def road_tax_eur(self) -> Decimal:
        """The road tax line of :attr:`fixed_expenses_eur` — what is paid at registration.

        The recurring annual figure and the day count behind this one are on
        :attr:`road_tax`; this is only the part of it that belongs in a landed
        cost.
        """
        return self.road_tax.total_eur

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
            f"road tax €{self.road_tax_eur:,.0f} "
            f"({self.road_tax.co2_gkm} g/km, "
            f"{self.road_tax.days}/{self.road_tax.days_in_year} d)",
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
        registered_on: date | None = None,
) -> LandedCost:
    """Spec §3, end to end. Raises on the two inputs that cannot price a car.

    A hammer price of zero or less is a caller bug, and a model spec with no
    ``co2_gkm`` is a hole in ``model_specs.csv``. Neither is swallowed into a
    ``reason`` string here — but :func:`price_calculator.sources.margin_for`
    catches both and hands the card the sentence, which is where the report's
    "nothing about one car raises" rule is actually kept. This function is the
    layer that would rather say what is missing than price around it: road tax
    now runs from €45 to €1,500 a year across the CO₂ scale, and a landed cost
    computed with a road tax of zero would be wrong by more than the freight it
    is careful about, invisibly.

    ``registered_on`` is the day the car goes onto Cyprus plates, and decides how
    much of the year's road tax is paid at the counter. It defaults to the date
    the run's **rates** were quoted at — not to today — because that is the
    module's whole clockless contract: the same inputs give the same money
    forever, and a report re-rendered in December must not quietly reprice an
    August car with four months less road tax on it.

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
    if spec.co2_gkm is None:
        # Phrased to sit on a card beside "no band for MAZDA CX-30 2023 · 15,000
        # km": the fact, the row it is missing from, and the file to fix it in.
        raise ValueError(
            f"no CO₂ figure for {spec.make} {spec.model} "
            f"{spec.year_from}–{spec.year_to} in model_specs.csv — "
            f"road tax is charged on it")

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

    road_tax = costs.road_tax_at_registration(
        spec.co2_gkm,
        registered_on or rates.fetched_at.date(),
        euro_standard=spec.euro_standard,
        fuel=spec.fuel,
    )
    fixed = costs.fixed_expenses_base_eur + road_tax.total_eur

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
        road_tax=road_tax,
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
