"""What the cheapest cars you would have accepted actually sold for.

One panel per enabled search, one section per **band**, and up to five rows: the
cheapest concluded lots whose auction sheet passes that search's ``[sheet]``
requirements. Five cars with links — not a median, not a distribution. See
``CONTEXT.md`` on *auction statistics*.

**The band's max bid sits under its sales, landed.** Last row of the table and
never one of them: a ceiling you wrote, not something that happened. The
comparison it invites is honest in exactly one column — a ``max_bid_jpy`` is an
all-in maximum *at the auction*, while a sale's figure is a hammer price with
the house's area price still to come, so the yen column sets two different
quantities beside each other and only the euro column sets one against itself.
That is the whole reason the row can be here, and the reason its yen is the
greyer of its two numbers. Nothing here decides anything even so: you read the
euro column and go and edit the file.

**It does carry a landed cost per sale**, which is the one number that crosses
the currency without pretending to be a decision: what that car, at that
price, at that house, would have cost you on Cyprus plates. The comparison it
is worth making is against a **competitor** — a euro against a euro — and the
hammer price alone cannot be compared with anything on this side. It is landed
from the hammer price *plus that house's area price*, because every yen paid in
Japan is in the customs value; see :class:`banzai24.report.LandedPricer`, which
makes the same argument for a max bid. That is also why the auction house has
no column of its own: it is in the number, and on the cell that carries it.

**The keepers are re-derived, not stored.** ``banzai24 stats`` writes lots and
extractions; which five of them a band keeps is worked out here, every build,
from the search file as it reads today. So re-tuning a ``[sheet]`` requirement
re-judges every sale already paid for, for free — the same trade
``docs/adr/0004-bid-prices-are-read-live.md`` makes for prices, for the same
reason.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import searches
from banzai24 import bidding, db as banzai_db
from banzai24 import requirements, search as banzai_search, stats as stats_mod
from banzai24.models import AuctionLot
from searches.definition import PRIVATE, Band

PROJECT_ROOT = Path(__file__).parent.parent

# How many rows a band shows. The same number the walk collects, and stated
# again here because the walk's cap and the page's appetite are different
# decisions that happen to agree: a band whose cheap end got deeper over several
# weeks should still show five.
KEEPERS = stats_mod.KEEPERS


@dataclass(frozen=True)
class BenchmarkRow:
    """One sale: a car, a price, and the sheet that says it was acceptable.

    ``auction_name`` lost its column to ``landed_eur`` and kept its field: the
    house decides the area price inside that euro figure, and the cell names it.
    Exactly one of ``landed_eur`` and ``landed_reason`` is set — a sale nobody
    can land says why in the place the number would have been, the rule the rest
    of this project follows for one car that cannot be priced.
    """

    lot_number: str
    lot_short: str
    price_jpy: int
    mileage_km: int | None
    grade: str | None
    modification: str | None
    trade_date: date | None
    auction_name: str
    url: str | None
    sheet_uri: str | None
    landed_eur: float | None = None
    landed_reason: str | None = None
    area_price_jpy: int | None = None


@dataclass(frozen=True)
class MaxBidRow:
    """This band's own ``max_bid_jpy``, landed — the ceiling under the sales.

    Two numbers and no car, because there is no car: nothing was bought at this
    price, and every field a sale fills in is left empty rather than invented.
    The yen is the figure from the search file and the euro is that figure on
    Cyprus plates, landed through the same calculator and the same morning's
    rates as the rows above it — the one thing that makes the column readable
    top to bottom. ``landed_eur`` and ``landed_reason`` are exclusive, the rule
    :class:`BenchmarkRow` already follows.

    Nothing is added to the yen before landing it. A ``max_bid_jpy`` is
    *already* hammer plus the house's area price — that is what an all-in
    maximum means — so the step :meth:`LandedPricer.for_lot` has to take for a
    sale would be taken twice here. See ``banzai24.report.LandedPricer``, which
    prices the same number the same way on a card.
    """

    price_jpy: int
    landed_eur: float | None = None
    landed_reason: str | None = None


@dataclass(frozen=True)
class BandPanel:
    """One band's cheap end, and how much of it had to be looked at."""

    band: Band
    rows: tuple[BenchmarkRow, ...] = ()
    max_bid: MaxBidRow | None = None   # the band's ceiling, under its sales
    stored: int = 0          # lots this walk has stored inside this band
    failed: int = 0          # read, and the sheet disqualified them
    unconfirmed: int = 0     # stored, but nothing has read the sheet yet
    problem: str | None = None

    @property
    def low(self) -> int | None:
        return min((row.price_jpy for row in self.rows), default=None)

    @property
    def high(self) -> int | None:
        return max((row.price_jpy for row in self.rows), default=None)

    @property
    def note(self) -> str | None:
        """Why there are fewer than five rows — never silence.

        An empty list is not good news and a short one is not bad news; both are
        only readable next to how many lots were looked at to produce them. The
        same instinct as the competitors panel.
        """
        if self.problem:
            return self.problem
        if len(self.rows) >= KEEPERS:
            return None
        if not self.stored:
            return "not measured yet — run `banzai24 stats --search <name>`"
        bits = [f"{self.stored} sale{'' if self.stored == 1 else 's'} looked at"]
        if self.failed:
            bits.append(f"{self.failed} failed a requirement")
        if self.unconfirmed:
            bits.append(f"{self.unconfirmed} sheet{'' if self.unconfirmed == 1 else 's'} unread")
        return " · ".join(bits)


@dataclass(frozen=True)
class SearchPanel:
    """One car's whole panel."""

    name: str
    car: object | None = None
    bands: tuple[BandPanel, ...] = ()
    measured_on: date | None = None
    problem: str | None = None      # the file will not load; nothing else is true
    note: str | None = None         # it loads, but it is measuring loosely
    source: Path | None = None

    @property
    def benchmark_count(self) -> int:
        return sum(len(band.rows) for band in self.bands)


@dataclass(frozen=True)
class Statistics:
    """Every enabled search's cheap end."""

    panels: tuple[SearchPanel, ...] = ()
    generated_at: datetime = field(default_factory=datetime.now)

    @property
    def benchmark_count(self) -> int:
        return sum(panel.benchmark_count for panel in self.panels)

    @property
    def unmeasured(self) -> int:
        return sum(1 for panel in self.panels
                   if panel.problem or panel.measured_on is None)


def panel_summary(panel: SearchPanel) -> str:
    """The line printed on this panel's closed ``<details>`` on a search page.

    A search nobody has measured is said separately from a search measured and
    found empty: "0 sales" and "never measured" are opposite news, and one
    number would blur them — see ``docs/adr/0002-unmeasured-survivorship-is-
    ignorance.md``.
    """
    if panel.problem:
        return f"will not load: {panel.problem}"
    if panel.measured_on is None:
        return "not measured yet"
    count = panel.benchmark_count
    return (f"{count} cheapest acceptable sale{'' if count == 1 else 's'}"
            f" · measured {panel.measured_on.isoformat()}")


# --- turning stored lots back into a band's five -----------------------------


# banzai24's image service serves by token, so a saved sheet's suffix is
# whatever `fetch` decided by sniffing the bytes. Naming the wrong media type
# renders a blank box rather than a wrong-looking image, so it is read off the
# suffix rather than assumed — the same table `banzai24.report` keeps.
_MEDIA_TYPES = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}


def _data_uri(path: Path | None) -> str | None:
    """A sheet as ``data:image/jpeg;base64,…``, at its original resolution.

    Inlined rather than linked so the page stays one file: these pages get
    copied about, and a thumbnail resolving through ``../stats/`` would survive
    exactly as far as the directory it points into.

    **Full size, though it renders as a thumbnail.** The page shows it at a
    thumb's width and expands it to full size on click, and both come from this
    one copy of the bytes — so scaling it down here would cost the reading of
    the sheet, which is the only thing anybody opens it for, and save nothing
    that a second inlined copy would not immediately spend again.
    """
    if path is None or not path.exists():
        return None
    media = _MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")
    return (f"data:{media};base64,"
            + base64.standard_b64encode(path.read_bytes()).decode("ascii"))


class LandedPricer:
    """What one sold lot would have cost you on Cyprus plates, or why it cannot.

    A thin arrangement of parts that already exist — the area price list from
    :class:`banzai24.bidding.BidPricer`, the model specs, and
    :func:`price_calculator.sources.margin_for` — and it owns none of them. What
    it decides is the one thing neither of them can: that the price to land is
    the **hammer plus the house's area price**, because that is what the car
    costs at the auction and all of it is in the customs value. Landing the
    hammer alone would flatter every row by ¥4,000–¥47,000, which is the mirror
    of the mistake :class:`banzai24.report.LandedPricer` names.

    **Today's money, like the rest of the dashboard**, and never the stamped
    money of a run: the panel answers "what is the market doing" this morning,
    not "what was it doing when this was fetched". See
    :func:`price_calculator.sources.money_for_today` and
    ``docs/adr/0004-bid-prices-are-read-live.md``.

    Built once per build and asked once per row. Nothing here raises: every way
    a sale fails to land comes back as a sentence, and the row prints it where
    the number was — the page is worth more with four landed rows and a reason
    on the fifth than with an exception.
    """

    def __init__(self, rates=None, costs=None, money_problem: str | None = None):
        from cars.specs import ModelSpecs

        self.rates, self.costs = rates, costs
        self.areas = bidding.BidPricer()
        if rates is None or costs is None:
            self.specs = None
            self.reason = money_problem or "no exchange rates, so no landed cost"
        else:
            self.specs = ModelSpecs()
            self.reason = self.specs.reason

    def for_lot(self, lot: AuctionLot) -> tuple[float | None, str | None, int | None]:
        """``(landed EUR, reason, area price JPY)`` — the euro or the sentence."""
        from price_calculator.sources import margin_for

        if self.rates is None or self.costs is None or self.specs is None:
            return None, self.reason, None
        if not lot.end_price_jpy:
            return None, "no hammer price", None

        area, house_problem = self.areas.area_cost(lot)
        if area is None:
            return None, house_problem, None

        margin = margin_for(
            make=lot.mark, model=lot.model, year=lot.registration_year,
            mileage_km=lot.mileage_km,
            auction_price_jpy=lot.end_price_jpy + area,
            rates=self.rates, costs=self.costs, specs=self.specs,
            # No market: this is a Japanese sale, and what it sells for in
            # Cyprus is the competitors panel's question. The estimate costs a
            # full `bazaraki.db` query per row. See `margin_for`.
            market=None,
        )
        if isinstance(margin, str):
            return None, margin, area
        return float(margin.landed.total_eur), None, area

    def for_band(self, definition, band: Band) -> tuple[float | None, str | None]:
        """``(landed EUR, reason)`` for this band's max bid — no area price.

        The one place this differs from :meth:`for_lot`, and it is not an
        omission: a ``max_bid_jpy`` is an all-in maximum at the auction, so the
        house's area price is inside it already and adding one would charge it
        twice. Which house is not even a question here — the ceiling is one
        number for the band, whichever room the car turns up in.

        The car is the *search's* make and model rather than a lot's, because no
        lot is being priced. Same spelling either way: ``_matches_car`` picks a
        band's sales out of the table with these two fields.
        """
        from price_calculator.sources import margin_for

        if self.rates is None or self.costs is None or self.specs is None:
            return None, self.reason

        margin = margin_for(
            make=definition.filters.make, model=definition.filters.model,
            year=band.year,
            # No mileage and no market, for the same reason `for_lot` passes
            # none: mileage only moves the Cyprus estimate, and the estimate is
            # the competitors panel's question.
            mileage_km=None, market=None,
            auction_price_jpy=band.bid(),
            rates=self.rates, costs=self.costs, specs=self.specs,
        )
        if isinstance(margin, str):
            return None, margin
        return float(margin.landed.total_eur), None


def _sheet_path(lot: AuctionLot) -> Path | None:
    if not lot.sheet_path:
        return None
    path = Path(lot.sheet_path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _matches_car(lot: AuctionLot, definition) -> bool:
    """Is this stored lot the car this search is about?

    Lots are stored under one table for every search, so a panel has to pick its
    own back out. Make and model are banzai24's own spelling of the car, which
    is what the search asked the site for.
    """
    return ((lot.mark or "").upper() == (definition.filters.make or "").upper()
            and (lot.model or "").upper() == (definition.filters.model or "").upper())


def _matches_stats_filters(lot: AuctionLot, filters, lot_filters) -> bool:
    """The narrowings the sheet cannot re-judge, re-applied to a stored row.

    ``requirements.judge`` re-checks year, mileage, grade and the sheet — but not
    the trim line, the colour or the chassis code, because none of them is on the
    sheet. They were applied when the lot was fetched; they are applied again
    here so that tightening ``model_grade``, or splitting a car's file in two and
    banning a trim line in one half, drops yesterday's wider sales from the page
    instead of leaving them to sit there looking measured.

    Two layers, because the file writes the trim line two ways.
    ``[auction_statistics] model_grade`` is the positive one, matched as a
    substring the way banzai24 matches it. ``[api]`` is the exclusion, and it
    arrives already narrowed to this **band's** chassis codes — the one narrowing
    that differs panel to panel: a 2WD sale is not a benchmark for the E-Four's
    max bid, and the search-wide union would let it be one.
    """
    if filters.model_grade:
        modification = (lot.modification or "").casefold()
        if not any(wanted.casefold() in modification for wanted in filters.model_grade):
            return False
    return lot_filters.keeps(code=lot.body_model_code, colour=lot.colour,
                             modification=lot.modification)


def _in_band(lot: AuctionLot, band: Band) -> bool:
    """Does this sale fall in this band? Year exactly, mileage inclusively.

    The same test the band applies to a car being bought, so a band is measured
    against sales it would itself have priced. A lot missing either figure is in
    no band at all rather than in the first one — bands never overlap, and a
    guessed one would put a sale under the wrong max bid.
    """
    if lot.registration_year is None or lot.mileage_km is None:
        return False
    return lot.registration_year == band.year and band.covers(lot.mileage_km)


def _band_panel(definition, band: Band, lots: list[AuctionLot],
                extractions: dict, landed: LandedPricer | None = None) -> BandPanel:
    """One band's five cheapest passing sales, re-judged from today's file.

    ``landed=None`` keeps the rows and drops their euro — which is what a caller
    with no money wants, and what a test that is asking about the *keepers* does
    not have to arrange.
    """
    filters = definition.stats_filters(band)
    lot_filters = definition.lot_filters_for(band)

    inside = [
        lot for lot in lots
        if _in_band(lot, band) and _matches_stats_filters(lot, filters, lot_filters)
    ]

    rows: list[BenchmarkRow] = []
    failed = unconfirmed = 0
    for lot in sorted(inside, key=lambda l: (l.end_price_jpy is None, l.end_price_jpy or 0)):
        assessment = requirements.judge(
            filters, lot_filters, definition.requirements, lot,
            extractions.get(lot.lot_number))
        if assessment.group == requirements.FAILS:
            failed += 1
            continue
        if assessment.group == requirements.UNCONFIRMED:
            unconfirmed += 1
            continue
        # A lot that passes but whose price never revealed is not a benchmark:
        # the page exists to show a number.
        if not lot.end_price_jpy:
            unconfirmed += 1
            continue
        if len(rows) < KEEPERS:
            landed_eur, landed_reason, area = (
                landed.for_lot(lot) if landed else (None, None, None))
            rows.append(BenchmarkRow(
                lot_number=lot.lot_number,
                lot_short=lot.lot_short,
                price_jpy=lot.end_price_jpy,
                mileage_km=lot.mileage_km,
                grade=lot.grade_origin,
                modification=lot.modification,
                trade_date=lot.trade_date,
                auction_name=lot.auction_name,
                url=(f"https://banzai24.com/car/JP/{lot.banzai_id}"
                     if lot.banzai_id else None),
                sheet_uri=_data_uri(_sheet_path(lot)),
                landed_eur=landed_eur,
                landed_reason=landed_reason,
                area_price_jpy=area,
            ))

    # The ceiling is the file's, not the walk's: it is there whether or not a
    # single sale was ever stored, and a band with nothing under it is exactly
    # where knowing what you are willing to pay is worth reading.
    max_bid = None
    if band.max_bid_jpy.get(PRIVATE):
        landed_eur, landed_reason = (
            landed.for_band(definition, band) if landed else (None, None))
        max_bid = MaxBidRow(price_jpy=band.bid(), landed_eur=landed_eur,
                            landed_reason=landed_reason)

    return BandPanel(band=band, rows=tuple(rows), max_bid=max_bid,
                     stored=len(inside), failed=failed, unconfirmed=unconfirmed)


def build(runs_dir: Path | None = None, money=None) -> Statistics:
    """Every enabled search, measured against what is stored today.

    ``money`` is the ``(rates, costs, problem)`` triple
    :func:`price_calculator.sources.money_for_today` returns, handed over by
    :func:`dashboard.cli.build` because the competitors panel has already
    fetched it. Two fetches a build would be one HTTP round trip wasted and,
    worse, two rates: the same search page would land a *sale* at one euro and a
    *max bid* at another, and the gap between them would be read as a fact about
    the cars. Left out, this fetches its own — which is what a caller building
    the panel alone wants.
    """
    from price_calculator.sources import money_for_today

    rates, costs, money_problem = money or money_for_today(runs_dir)
    landed = LandedPricer(rates, costs, money_problem)

    banzai_db.init_db()
    lots = banzai_db.stats_lots()
    extractions = banzai_db.extractions_by_numbers([lot.lot_number for lot in lots])

    panels = []
    for name, spec, problem in searches.load_all():
        if problem:
            panels.append(SearchPanel(name=name, problem=problem))
            continue
        if not spec.dashboard.enabled:
            continue
        try:
            definition = banzai_search.load(name)
        except banzai_search.SearchDefinitionError as exc:
            panels.append(SearchPanel(name=name, problem=str(exc)))
            continue

        mine = [lot for lot in lots if _matches_car(lot, definition)]
        panels.append(SearchPanel(
            name=name,
            car=spec.car,
            bands=tuple(_band_panel(definition, band, mine, extractions, landed)
                        for band in definition.bands),
            measured_on=stats_mod.last_run(name),
            note=(None if definition.stats_declared else
                  "no [auction_statistics] section — every trim line of this car "
                  "is being measured together"),
            source=spec.source,
        ))

    return Statistics(panels=tuple(panels))
