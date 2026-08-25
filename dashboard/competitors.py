"""Who is already selling this car in Cyprus for less than you would have to charge.

One panel per saved search, one section per **band** — because the band is the
thing that has a price. A band's max bid gives a landed cost; the landed cost
plus resale costs plus the profit that car has to earn gives a **cyprus sell
price**; and a **competitor** is a live Cyprus advert, inside that band's
declared competitor bounds, asking less than that.

Three numbers sit together on every section on purpose:

* the **cyprus sell price** — what you must get,
* the **Cyprus estimate** — what the market says you would get,
* the competitors underneath both.

If the first is above the second the band does not work at any profit, and the
length of the competitor list is a footnote. That comparison is the reason the
estimate is on the page at all.

**An empty list is never rendered as good news.** A search with no profit set,
no competitor bounds, or no completed bazaraki crawl covering it gets a section
saying which of those it is. The same instinct as the runs index, which dims a
run it cannot open rather than hiding it: hiding it is how it gets forgotten.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import searches
from searches.definition import PRIVATE, Band, CompetitorFilters, SearchDefinition

# How far back a delisted advert still counts as evidence. Long enough to have a
# few in a thin market, short enough that the median is about today's prices.
SOLD_WINDOW_DAYS = 30


def _fold(value: str | None) -> str:
    """The fold both databases already join on — case and punctuation removed."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _litres(engine_size: str | None) -> float | None:
    """``"2,0L"`` → ``2.0``. bazaraki writes a comma decimal and an ``L``.

    Parsed here rather than declared in the .toml: the comma is bazaraki's
    formatting, and a search file that had to spell ``"2,0L"`` would be carrying
    a fact about a website. Anything unparseable is ``None``, which fails an
    engine-size bound rather than passing it — a filter that silently keeps what
    it cannot read is not a filter.
    """
    if not engine_size:
        return None
    match = re.search(r"(\d+)[.,]?(\d*)", engine_size)
    if not match:
        return None
    whole, frac = match.groups()
    try:
        return float(f"{whole}.{frac or '0'}")
    except ValueError:
        return None


# --- what one advert has to be to count --------------------------------------


def _passes(listing, filters: CompetitorFilters) -> bool:
    """The ``[competitors]`` block, checked against one stored advert.

    In memory rather than at the crawl, for the reasons in
    :func:`bazaraki.config.filters_for`. A field the scraper never captured is a
    **fail**, not a pass: an advert that cannot be shown to be a petrol car is
    not evidence about the petrol market.
    """
    if filters.fuel_type and _fold(listing.fuel_type) not in {
            _fold(value) for value in filters.fuel_type}:
        return False
    if filters.gearbox and _fold(listing.gearbox) != _fold(filters.gearbox):
        return False
    if filters.seller_type and _fold(listing.seller_type) != _fold(filters.seller_type):
        return False
    if filters.exclude_colours and _fold(listing.colour) in {
            _fold(value) for value in filters.exclude_colours}:
        return False
    if filters.engine_size_start is not None or filters.engine_size_end is not None:
        litres = _litres(listing.engine_size)
        if litres is None:
            return False
        if filters.engine_size_start is not None and litres < filters.engine_size_start:
            return False
        if filters.engine_size_end is not None and litres > filters.engine_size_end:
            return False
    return True


def _in_band(listing, band: Band) -> bool:
    return (band.competitors.covers_year(listing.year)
            and band.competitors.covers_mileage(listing.mileage_km))


# --- the rendered pieces -----------------------------------------------------


@dataclass(frozen=True)
class CompetitorRow:
    """One Cyprus advert asking less than a band's cyprus sell price."""

    ad_id: int
    url: str
    title: str
    price: float
    under_by: float          # euro below the sell price; the size of the problem
    year: int | None
    mileage_km: int | None
    fuel_type: str | None
    gearbox: str | None
    seller_type: str | None
    days_on_market: int | None


@dataclass(frozen=True)
class Sold:
    """What has gone off the market lately, as evidence the car actually moves."""

    count: int
    median_price: float | None
    days: int = SOLD_WINDOW_DAYS

    def describe(self) -> str:
        if not self.count:
            return f"none delisted in the last {self.days} days"
        price = f", median €{self.median_price:,.0f}" if self.median_price else ""
        return f"{self.count} delisted in the last {self.days} days{price}"


@dataclass(frozen=True)
class BandPanel:
    """One band: what it costs, what it must fetch, and who is under that."""

    band: Band
    max_bid_jpy: int
    landed_eur: float | None = None
    sell_price_eur: float | None = None
    cyprus_estimate_eur: float | None = None
    cyprus_confidence: str | None = None
    profit_eur: float | None = None
    competitors: tuple[CompetitorRow, ...] = ()
    considered: int = 0            # adverts inside the bounds, priced or not
    sold: Sold | None = None
    problem: str | None = None     # why there is no sell price on this section
    warning: str | None = None     # there is one, but do not lean on it

    @property
    def underwater(self) -> bool:
        """The market will not pay what this band has to charge.

        A different failure from being undercut, and a worse one: no competitor
        has to do anything for this to be true.
        """
        return (self.sell_price_eur is not None
                and self.cyprus_estimate_eur is not None
                and self.sell_price_eur > self.cyprus_estimate_eur)


@dataclass(frozen=True)
class SearchPanel:
    """One car's whole panel."""

    name: str
    car: object | None = None
    bands: tuple[BandPanel, ...] = ()
    problem: str | None = None     # the file will not load; nothing else is true
    coverage: str | None = None    # bazaraki has not crawled what this asks about
    source: Path | None = None

    @property
    def competitor_count(self) -> int:
        return sum(len(band.competitors) for band in self.bands)


@dataclass(frozen=True)
class Dashboard:
    """Every enabled search, and the money the whole page was priced at."""

    panels: tuple[SearchPanel, ...] = ()
    rates: str | None = None
    costs: str | None = None
    money_problem: str | None = None
    generated_at: datetime = field(default_factory=datetime.now)

    @property
    def competitor_count(self) -> int:
        return sum(panel.competitor_count for panel in self.panels)


# --- building it -------------------------------------------------------------


def _coverage_note(search: SearchDefinition, run) -> str | None:
    """Why the adverts below may not be the whole story, in one sentence.

    The check that matters is not "is the database empty" but "has a *completed*
    crawl covered the range this panel asks about". `db._in_scope` bounds
    delisting to a run's own filters, so an advert outside every recent scope
    keeps its "still on sale" flag for ever — it would show here as live
    competition long after it sold.
    """
    if run is None:
        return (f"no completed bazaraki crawl for this car — run "
                f"`uv run python -m bazaraki scrape --search {search.name}`")

    scope = search.competitor_scope()
    if not scope.declared:
        # Nothing to compare the crawl against, and every band already says so.
        # Repeating it as a coverage warning would be one fact printed twice.
        return None
    gaps = []
    if scope.year_start is not None and (run.year_min or 0) > scope.year_start:
        gaps.append(f"years before {run.year_min}")
    if scope.year_end is not None and run.year_max is not None and run.year_max < scope.year_end:
        gaps.append(f"years after {run.year_max}")
    if (run.mileage_min or 0) > (scope.mileage_start or 0):
        gaps.append(f"under {run.mileage_min:,} km")
    if run.mileage_max is not None and (scope.mileage_end is None
                                        or run.mileage_max < scope.mileage_end):
        gaps.append(f"over {run.mileage_max:,} km")
    if not gaps:
        return None
    return (f"the last crawl ({run.started_at:%-d %b}) did not cover "
            f"{', '.join(gaps)} — those adverts are stale or missing. Re-run "
            f"`uv run python -m bazaraki scrape --search {search.name}`")


def _rows_for(band: Band, listings, filters: CompetitorFilters,
              sell_price: float) -> tuple[tuple[CompetitorRow, ...], int]:
    """The adverts under a band's sell price, cheapest first.

    Cheapest first because the cheapest undercut is the one that costs the sale;
    sorting by anything else buries it.
    """
    considered = 0
    rows = []
    for listing in listings:
        if not _in_band(listing, band) or not _passes(listing, filters):
            continue
        considered += 1
        if listing.price is None or listing.price >= sell_price:
            continue
        rows.append(CompetitorRow(
            ad_id=listing.ad_id,
            url=listing.url,
            title=listing.title,
            price=listing.price,
            under_by=sell_price - listing.price,
            year=listing.year,
            mileage_km=listing.mileage_km,
            fuel_type=listing.fuel_type,
            gearbox=listing.gearbox,
            seller_type=listing.seller_type,
            days_on_market=listing.days_on_market,
        ))
    rows.sort(key=lambda row: row.price)
    return tuple(rows), considered


def _sold(band: Band, listings, filters: CompetitorFilters) -> Sold:
    """Delisting is the only sold-proxy this system has; see ``PRICING_PLAN.md``."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=SOLD_WINDOW_DAYS)
    prices = [
        listing.price for listing in listings
        if listing.delisted_at is not None
        and listing.delisted_at.replace(tzinfo=None) >= cutoff
        and _in_band(listing, band) and _passes(listing, filters)
        and listing.price is not None
    ]
    return Sold(count=len(prices),
                median_price=statistics.median(prices) if prices else None)


def _band_panel(search: SearchDefinition, band: Band, listings, live, rates,
                costs, specs, market) -> BandPanel:
    max_bid = band.bid(PRIVATE)
    base = BandPanel(band=band, max_bid_jpy=max_bid)

    if not band.competitors.declared:
        return _with(base, problem=(
            "no [band.competitors] bounds — nothing declared as competition for "
            "this band"))

    profit = search.profit_for(band)
    if profit is None:
        return _with(base, problem=(
            "no expected_profit_eur — add one under [dashboard], or on this band"))

    if rates is None:
        return _with(base, problem="no exchange rates, so no landed cost")

    from price_calculator.sources import margin_for

    margin = margin_for(
        make=search.car.make, model=search.car.model, year=band.year,
        # Priced at the top of the band, where the cars you actually import sit.
        # The midpoint would flatter every number on the page.
        mileage_km=band.mileage_end if band.mileage_end is not None else band.mileage_start,
        auction_price_jpy=max_bid, rates=rates, costs=costs,
        specs=specs, market=market,
    )
    if isinstance(margin, str):
        return _with(base, problem=margin)

    landed = float(margin.landed.total_eur)
    sell_price = landed + float(margin.resale_costs_eur) + profit
    rows, considered = _rows_for(band, live, search.competitors, sell_price)

    return BandPanel(
        band=band,
        max_bid_jpy=max_bid,
        landed_eur=landed,
        sell_price_eur=sell_price,
        cyprus_estimate_eur=(float(margin.cyprus_eur)
                             if margin.cyprus_eur is not None else None),
        cyprus_confidence=margin.cyprus_confidence,
        profit_eur=profit,
        competitors=rows,
        considered=considered,
        sold=_sold(band, listings, search.competitors),
        problem=None if margin.cyprus_eur is not None else margin.reason,
        warning=margin.warning,
    )


def _with(panel: BandPanel, **changes) -> BandPanel:
    return replace(panel, **changes)


def _listings_for(search: SearchDefinition):
    """``(all, live)`` bazaraki adverts for this car.

    ``all`` keeps the delisted ones, which are the sold evidence; ``live`` is
    what can still take a sale from you. Adverts marked *In transit* are dropped
    from both — they are import quotes for cars not yet on the island, not the
    local market, and :class:`price_calculator.sources.CyprusMarket` already
    excludes them from the estimate this page puts them next to.
    """
    from bazaraki import analysis, db

    make_key, model_key = _fold(search.car.make), _fold(search.car.model)
    scoped = [
        listing for listing in db.all_listings()
        if _fold(listing.make) == make_key and _fold(listing.model) == model_key
        and _fold(listing.availability) != _fold(analysis.IN_TRANSIT)
    ]
    return scoped, [listing for listing in scoped if listing.is_active]


def build(runs_dir: Path | None = None) -> Dashboard:
    """Every enabled saved search, priced at today's money.

    Today's, not the newest run's: this page is not a record of a decision, it is
    the live question "should I be buying this car this morning". See
    :func:`price_calculator.sources.money_for_today`, and
    ``docs/adr/0004-bid-prices-are-read-live.md`` for why that does not
    contradict a run's stamped prices.
    """
    from price_calculator.sources import CyprusMarket, ModelSpecs, money_for_today

    rates, costs, money_problem = money_for_today(runs_dir)
    specs = ModelSpecs()
    market = CyprusMarket()

    panels = []
    for name, search, problem in searches.load_all():
        if problem:
            panels.append(SearchPanel(name=name, problem=problem))
            continue
        if not search.dashboard.enabled:
            continue

        from bazaraki import cars as bazaraki_cars, db

        listings, live = _listings_for(search)
        try:
            make_slug, model_slug = bazaraki_cars.slugs(search.car)
            run = db.latest_run_for(make_slug, model_slug)
            coverage = _coverage_note(search, run)
        except bazaraki_cars.UnknownCar as exc:
            coverage = exc.args[0]

        panels.append(SearchPanel(
            name=name,
            car=search.car,
            bands=tuple(_band_panel(search, band, listings, live, rates, costs,
                                    specs, market)
                        for band in search.bands),
            coverage=coverage,
            source=search.source,
        ))

    return Dashboard(
        panels=tuple(panels),
        rates=rates.describe() if rates is not None else None,
        costs=costs.describe() if costs is not None else None,
        money_problem=money_problem,
    )
