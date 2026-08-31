"""Who is already selling this car in Cyprus for less than you would have to charge.

One panel per saved search, one section per **band** — because the band is the
thing that has a price. A band's max bid gives a landed cost; the landed cost
plus resale costs plus the profit that car has to earn — a flat
``expected_profit_eur``, or an ``expected_profit_percent`` of the landed cost —
gives a **cyprus sell price**; and a **competitor** is a Cyprus advert, inside that band's declared
competitor bounds, asking less than that — live, or gone within the last
``COMPETITOR_HISTORY_DAYS``.

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

Every row is marked by how long the advert took to leave the market, and the
reading is the queue: buyers work a market cheapest-acceptable-first, so a
competitor is one car ahead of you in that order and its age is a fact about
your own wait. Gone quickly means the queue is moving; still listed after
``OVERPRICED_AFTER_DAYS`` means it is not. That is why a *disappeared* advert is
the friendly mark here while ``bazaraki.analysis`` treats the same event as a
mere sold-proxy — see ``docs/adr/0007-competitors-are-a-queue.md``.

One advert can be dismissed by hand — **manually excluded**, with the reason
written into ``carlisting.manual_exclusion_reason``. That is the inverse of a
``[competitors]`` filter: a filter is a *rule* and deletes the advert from the
page, while this is a *judgement* about one advert already read, so the row
stays, dimmed and counted nowhere. It stays because bazaraki still shows it
under the same filters — a row that vanishes is a row you re-investigate next
week. The mark never leaves this panel: it is deliberately broad ("only by
order", "wrong trim", "duplicate"), and one flag mixing those is not evidence
about a market, so ``CyprusMarket`` and ``bazaraki.analysis`` never see it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import searches
from searches.definition import PRIVATE, Band, CompetitorFilters, SearchDefinition

# Past this age a price has been refused by the market for long enough to call
# it too high — whether the advert eventually went or is still sitting there.
# Deliberately *not* ``bazaraki.analysis.FAST_DAYS``, which is also 30: how long
# before a price is too high and what counts as a fast sale for the asking->sale
# haircut are different questions that happen to agree today, and tuning one
# must not silently move the other.
OVERPRICED_AFTER_DAYS = 30

# How far back a departed competitor is still shown. Not a mark — the marks read
# age, not recency — only how much of the queue's history stays on the page.
COMPETITOR_HISTORY_DAYS = 90

# What the marks mean, as CSS classes. No labels ride with them: the legend under
# the table carries the reading, and the age they are computed from is printed in
# the very cell they colour, so the distinction survives without colour.
FAIR = "fair"                # gone inside OVERPRICED_AFTER_DAYS — the price worked
OVERPRICED = "overpriced"    # older than that, gone or still up — it did not
UNPROVEN = "unproven"        # still up, but not long enough to have said anything


def _fold(value: str | None) -> str:
    """The fold both databases already join on — case and punctuation removed."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _says(listing, phrase: str) -> bool:
    """Does the advert's own text contain ``phrase``?

    Title and description together, lower-cased with runs of whitespace
    collapsed — sellers write "TOYOTA RAV 4  2.5L (G package)" with the spacing
    of a shop window. Punctuation is *kept*, so a phrase is matched as the
    seller would have typed it, which is why a phrase has to be long enough to
    mean the trim: "hybrid x" and not "x".
    """
    text = " ".join(f"{listing.title or ''} {listing.description or ''}".lower().split())
    return phrase in text


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
    # The one exclusion that reads free text, and so the one place a *missing*
    # value keeps the advert rather than dropping it: an advert that never names
    # its trim has not been shown to be the trim you are not selling against,
    # and it still undercuts you.
    if any(_says(listing, phrase) for phrase in filters.exclude_phrases):
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


# --- how long the advert has been on sale ------------------------------------

_RELATIVE = {"minute": "minutes", "hour": "hours", "day": "days", "week": "weeks"}


def _naive(moment: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; normalise both sides before subtracting."""
    if moment is None:
        return None
    return (moment.astimezone(timezone.utc).replace(tzinfo=None)
            if moment.tzinfo is not None else moment)


def _published(listing) -> datetime | None:
    """The publish date bazaraki *claims*, read off ``posted_raw``.

    Relative forms are anchored to ``last_seen_at``, because the stored string is
    whatever the site said at the most recent sighting — an upsert overwrites it
    every crawl. Unreadable is ``None``, which simply leaves the age to
    :func:`_first_listed`'s other half.
    """
    raw = (listing.posted_raw or "").strip().lower()
    anchor = _naive(listing.last_seen_at)
    if not raw or anchor is None:
        return None
    absolute = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", raw)
    if absolute:
        day, month, year = (int(part) for part in absolute.groups())
        try:
            return datetime(year, month, day)
        except ValueError:
            return None
    if raw.startswith("today"):
        return anchor
    if raw.startswith("yesterday"):
        return anchor - timedelta(days=1)
    relative = re.match(r"(\d+)\s+(minute|hour|day|week|month)", raw)
    if not relative:
        return None
    count, unit = int(relative.group(1)), relative.group(2)
    if unit == "month":
        return anchor - timedelta(days=30 * count)
    return anchor - timedelta(**{_RELATIVE[unit]: count})


def _first_listed(listing) -> datetime | None:
    """The earliest date this advert can be shown to have existed.

    ``min`` of what bazaraki claims and when we first saw it, and the ``min`` is
    the whole point: ``posted_raw`` is a **bump** date. Sellers re-publish a
    stale advert to lift it up the results page and the site then reports it as
    newly posted — 90 of 216 RAV4 adverts claimed a publish date later than the
    day we first recorded them. Trusting it alone would paint the adverts of the
    most desperate sellers freshest, which is this page's signal exactly
    backwards. Clamping keeps what the field is good for: genuine publish dates
    reaching back before the crawl began.
    """
    seen, claimed = _naive(listing.first_seen_at), _published(listing)
    if seen is None:
        return claimed
    return min(seen, claimed) if claimed is not None else seen


def _age_days(listing, now: datetime | None = None) -> int | None:
    """Whole days from first listing to leaving the market, or to now if still up.

    Not :attr:`bazaraki.models.CarListing.days_on_market`, which starts at
    ``first_seen_at`` and so cannot see a car that was already on sale before the
    crawl reached it. Kept here rather than corrected there on purpose: that
    property feeds ``bazaraki.analysis.survivorship_adjustment``, so changing it
    would re-price every Cyprus estimate on this page as a side effect of a
    colour change. See ``docs/adr/0007-competitors-are-a-queue.md``.
    """
    birth = _first_listed(listing)
    if birth is None:
        return None
    end = _naive(listing.delisted_at) or _naive(now) or datetime.now(timezone.utc).replace(tzinfo=None)
    return max((end - birth).days, 0)


def _mark(listing, age: int | None) -> str:
    """Which of the three readings this advert has earned.

    An age we cannot compute is :data:`UNPROVEN` rather than a verdict — the same
    instinct as :func:`_passes`, where a field the scraper never captured fails
    rather than passes.
    """
    if age is not None and age > OVERPRICED_AFTER_DAYS:
        return OVERPRICED
    return UNPROVEN if listing.is_active else FAIR


# --- the rendered pieces -----------------------------------------------------


@dataclass(frozen=True)
class CompetitorRow:
    """One Cyprus advert asking less than a band's cyprus sell price.

    Live, or gone within :data:`COMPETITOR_HISTORY_DAYS`. ``mark`` is what its
    ``age`` earned it; ``gone`` says which side of the market it is on, because
    the same age reads differently for a car that left and one still sitting.

    A row carrying an ``exclusion_reason`` is *not* one of these: the operator
    has read it and judged it out. It is still rendered, at its price, because
    bazaraki shows it under the same filters and it will look like a competitor
    again tomorrow — but nothing counts it and it has no ``mark`` worth reading.
    """

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
    age: int | None
    mark: str
    gone: bool
    # Non-``None`` means manually excluded, and is the operator's own words.
    exclusion_reason: str | None = None

    @property
    def excluded(self) -> bool:
        return self.exclusion_reason is not None


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
    # Set only when the profit was asked for as a share of the landed cost. The
    # euro above is what it came to; this is what was actually declared, so the
    # panel can say 20% and not leave the reader deriving it.
    profit_percent: float | None = None
    # Every advert rendered under the sell price, cheapest first — including the
    # manually excluded ones, which are shown and counted nowhere. ``competitors``
    # is the half of this that the word actually covers.
    rows: tuple[CompetitorRow, ...] = ()
    considered: int = 0            # adverts inside the bounds, priced or not
    # Live adverts *above* the sell price that have not moved in
    # OVERPRICED_AFTER_DAYS. Never rows — they hold the better offer, so they are
    # not competitors — but without the count the table reads as an easy sale
    # while the market just above you is frozen. See
    # .scratch/market-state-panel/spec.md, which replaces this line with a panel.
    stuck_above: int = 0
    problem: str | None = None     # why there is no sell price on this section
    warning: str | None = None     # there is one, but do not lean on it

    @property
    def competitors(self) -> tuple[CompetitorRow, ...]:
        """The rows that are genuinely ahead of you in the queue.

        Everything that *counts* asks for this rather than :attr:`rows`, so a
        manually excluded advert can never hold a competitor count above zero.
        """
        return tuple(row for row in self.rows if not row.excluded)

    @property
    def excluded(self) -> tuple[CompetitorRow, ...]:
        return tuple(row for row in self.rows if row.excluded)

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


def _still_relevant(listing, now: datetime) -> bool:
    """Live, or gone recently enough to still describe today's queue.

    A competitor that vanished in June says nothing about who is ahead of you
    now, so it is dropped rather than shown grey — the page has one job and a
    row that cannot be read against today's price is not doing it.
    """
    if listing.is_active:
        return True
    gone = _naive(listing.delisted_at)
    return gone is not None and (now - gone).days <= COMPETITOR_HISTORY_DAYS


def _rows_for(band: Band, listings, filters: CompetitorFilters,
              sell_price: float, now: datetime | None = None,
              ) -> tuple[tuple[CompetitorRow, ...], int]:
    """The adverts under a band's sell price, cheapest first.

    Cheapest first because that is the order buyers work through — it is the
    order of the queue, so the cheapest undercut is both the one that costs the
    sale and the first one to clear.

    A car that sold for *more* than the sell price is not here, however fast it
    went: you hold the better offer, so it was never ahead of you.

    **Manually excluded** adverts are in this list but are not competitors. They
    keep their place in the price order — that is what makes them recognisable
    against bazaraki's own results page — while ``considered`` and every count
    downstream skip them. Once such an advert is delisted it is dropped
    altogether: the mark exists so you recognise a row you have already dealt
    with, and an advert that has left the site is one you will never meet again.
    """
    now = _naive(now) or datetime.now(timezone.utc).replace(tzinfo=None)
    considered = 0
    rows = []
    for listing in listings:
        if not _in_band(listing, band) or not _passes(listing, filters):
            continue
        if not _still_relevant(listing, now):
            continue
        excluded = listing.manual_exclusion_reason
        if excluded is not None and not listing.is_active:
            continue
        if excluded is None:
            considered += 1
        if listing.price is None or listing.price >= sell_price:
            continue
        age = _age_days(listing, now)
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
            age=age,
            # No mark on an excluded row: the three marks are verdicts about a
            # price, and an advert nobody can buy has withdrawn from that
            # judgement rather than earned a new verdict.
            mark="" if excluded is not None else _mark(listing, age),
            gone=not listing.is_active,
            exclusion_reason=excluded,
        ))
    rows.sort(key=lambda row: row.price)
    return tuple(rows), considered


def _stuck_above(band: Band, live, filters: CompetitorFilters,
                 sell_price: float, now: datetime | None = None) -> int:
    """Live adverts above the sell price that nobody has bought in a month.

    The queue only helps you if it is moving on *both* sides. Cars ahead of you
    clearing is good news; cars behind you frozen is the market saying it will
    not pay that much, and none of them can ever appear as a competitor. One
    number, because the operator asked for the table to stay a competitor list.

    A **manually excluded** advert is not counted here either: this is the same
    panel making the same judgement, and a car available only by order is not
    the market refusing your price.
    """
    now = _naive(now) or datetime.now(timezone.utc).replace(tzinfo=None)
    return sum(
        1 for listing in live
        if _in_band(listing, band) and _passes(listing, filters)
        and listing.manual_exclusion_reason is None
        and listing.price is not None and listing.price >= sell_price
        and (_age_days(listing, now) or 0) > OVERPRICED_AFTER_DAYS
    )


def _band_panel(search: SearchDefinition, band: Band, listings, live, rates,
                costs, specs, market) -> BandPanel:
    max_bid = band.bid(PRIVATE)
    base = BandPanel(band=band, max_bid_jpy=max_bid)

    if not band.competitors.declared:
        return _with(base, problem=(
            "no [band.competitors] bounds — nothing declared as competition for "
            "this band"))

    target = search.profit_for(band)
    if target is None:
        return _with(base, problem=(
            "no expected_profit_eur or expected_profit_percent — add one under "
            "[dashboard], or on this band"))

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
    # Resolved here rather than in the file, because a percent target is a
    # question about *this* band's landed cost — which is the yen of the day
    # through today's rates, and is not known until the margin above is run.
    profit = target.eur_for(landed)
    sell_price = landed + float(margin.resale_costs_eur) + profit
    # ``listings`` rather than ``live``: a competitor that has already gone is
    # the most useful row on the page, because how long it took to go is the
    # only direct evidence this system has about whether the price works.
    # The band's own filters, not the search's: on the RAV4 the AXAH54 band drops
    # adverts that say they are an X, and the AXAH52 band — which *is* the X —
    # keeps the very same ones, because they are the market it sells into.
    filters = search.competitors_for(band)
    rows, considered = _rows_for(band, listings, filters, sell_price)

    return BandPanel(
        band=band,
        max_bid_jpy=max_bid,
        landed_eur=landed,
        sell_price_eur=sell_price,
        cyprus_estimate_eur=(float(margin.cyprus_eur)
                             if margin.cyprus_eur is not None else None),
        cyprus_confidence=margin.cyprus_confidence,
        profit_eur=profit,
        profit_percent=target.percent,
        rows=rows,
        considered=considered,
        stuck_above=_stuck_above(band, live, filters, sell_price),
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
