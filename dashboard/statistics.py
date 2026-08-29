"""What the cheapest cars you would have accepted actually sold for.

One panel per enabled search, one section per **band**, and up to five rows: the
cheapest concluded lots whose auction sheet passes that search's ``[sheet]``
requirements. Five cars with links — not a median, not a distribution. See
``CONTEXT.md`` on *auction statistics*.

**Nothing here decides anything.** The page carries no bid, no landed cost and
no comparison against one: a benchmark's price is a hammer price and a
``max_bid_jpy`` is an all-in maximum, so the two side by side would compare
different quantities and flatter the bid by the size of an area price that
differs per auction house. The page lists what sold, and you go and edit the
file.

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
from banzai24 import db as banzai_db
from banzai24 import requirements, search as banzai_search, stats as stats_mod
from banzai24.models import AuctionLot
from searches.definition import Band

PROJECT_ROOT = Path(__file__).parent.parent

# How many rows a band shows. The same number the walk collects, and stated
# again here because the walk's cap and the page's appetite are different
# decisions that happen to agree: a band whose cheap end got deeper over several
# weeks should still show five.
KEEPERS = stats_mod.KEEPERS


@dataclass(frozen=True)
class BenchmarkRow:
    """One sale: a car, a price, and the sheet that says it was acceptable."""

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


@dataclass(frozen=True)
class BandPanel:
    """One band's cheap end, and how much of it had to be looked at."""

    band: Band
    rows: tuple[BenchmarkRow, ...] = ()
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
                extractions: dict) -> BandPanel:
    """One band's five cheapest passing sales, re-judged from today's file."""
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
            filters, definition.requirements, lot, extractions.get(lot.lot_number))
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
            ))

    return BandPanel(band=band, rows=tuple(rows), stored=len(inside),
                     failed=failed, unconfirmed=unconfirmed)


def build() -> Statistics:
    """Every enabled search, measured against what is stored today."""
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
            bands=tuple(_band_panel(definition, band, mine, extractions)
                        for band in definition.bands),
            measured_on=stats_mod.last_run(name),
            note=(None if definition.stats_declared else
                  "no [auction_statistics] section — every trim line of this car "
                  "is being measured together"),
            source=spec.source,
        ))

    return Statistics(panels=tuple(panels))
