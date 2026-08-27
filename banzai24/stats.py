"""Auction statistics: the cheapest sales you would actually have accepted.

For one band, the five cheapest concluded lots that pass that search's
``[sheet]`` requirements — five cars with links, not a distribution. This is the
evidence you read before re-tuning a ``max_bid_jpy``, and it is deliberately
individual: see ``CONTEXT.md`` on *auction statistics*, which is the Japan-side
counterpart to a *competitor*.

**The site's sort is the ranking.** More than half of banzai24's sold lots hide
their hammer price and show it one click at a time, but the server orders on the
true price and masks only the display — so page one of ``sortPriceEnd=asc`` is
the cheapest lots in the window, in real order, before a single price is
revealed. The walk therefore reads from the top and reveals a price only for a
lot it is going to keep. See
``docs/adr/0006-a-masked-price-is-still-a-ranked-price.md``, which is also where
the guard below is argued for.

**A revealed zero is a failed read.** The reveal endpoint answers HTTP 200 with
``priceStart: 0, priceEnd: 0`` — not 401 — to any caller without the SPA's own
Authorization header. So the reveal is a real button click, and a zero is
dropped rather than stored as a free car.

Nothing here writes a page. The walk stores lots and extractions in the same
tables the morning workflow uses, tagged ``discovered_by="stats"`` so the
morning queue and the day's report do not pick them up, and ``dashboard`` reads
them back.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import anthropic
import httpx

from searches.definition import Band

from . import (config, db, fetch, lot_filters as lot_filters_mod, normalize,
               requirements, session, sheets)
from .search import SearchDefinition

# How many keepers a band wants, and how many paid extractions it may spend
# finding them. Cheap lots are cheap because something is wrong with them, and
# `[sheet]` rejects exactly what makes them cheap — so without a cap the walk
# runs longest on the bands whose answer is least useful. A cache hit is free
# and does not count.
KEEPERS = 5
INSPECTION_CAP = 20

# Sheets and their extractions live outside `runs/`: a statistics walk is not a
# morning run, and a directory under `runs/` would show up on the runs index as
# one — dimmed and unopenable, because it has no lots.csv and never will.
STATS_DIR = Path(__file__).parent.parent / "stats"

# The table's price column, its sort popover, and the per-row reveal. Read off
# the live DOM on 2026-08-26; each is checked for rather than assumed, because a
# renamed class here means a walk that silently reads the wrong twenty lots.
PRICE_HEADER = "th.table__header-item"
PRICE_HEADER_TEXT = "цена"          # "Стар. цена / Кон. цена"
SORT_BUTTON = "button.table__header-item-sort-btn"
SORT_OPTION = ".table__sort-type"   # [0] По возрастанию, [1] По убыванию
REVEAL_BUTTON = ".end-price__show-btn"
GET_PRICE = "get-price"
ROW = "tbody tr"

# Politeness, and the same reasoning as `fetch`: banzai24 is behind a login, so
# rate limiting is account safety before it is manners.
REVEAL_DELAY_S = 1.2
SORT_TIMEOUT_MS = 25_000
REVEAL_TIMEOUT_MS = 15_000


class OrderingBroken(RuntimeError):
    """The site stopped ranking masked lots by their true price.

    Raised rather than returned because every keeper found before it is now
    suspect: the walk was reading a list it believed was sorted, and if it was
    not, "the five cheapest" is five arbitrary cars that still render.
    """


@dataclass
class Benchmark:
    """One kept lot: the row as stored, and the price it actually sold for."""

    lot_number: str
    price_jpy: int
    row: dict
    revealed: bool          # was the price behind a click?


@dataclass
class BandStats:
    """What one band's walk found, and what stopped it."""

    band: Band
    url: str
    keepers: list[Benchmark] = field(default_factory=list)
    considered: int = 0     # lots read off the sorted list, in order
    api_rejected: int = 0   # dropped by `[api]` before any sheet was touched
    inspected: int = 0      # paid extractions spent
    failed: int = 0         # read, and something on the sheet disqualified it
    unconfirmed: int = 0    # read, and a requirement had nothing to judge
    unreadable: int = 0     # no sheet, or the model could not read it
    cap: int = INSPECTION_CAP       # the cap this walk actually ran under
    stopped_by: str = "exhausted"   # keepers | cap | exhausted
    problem: str | None = None

    def summary(self) -> str:
        if self.problem:
            return f"{self.band.label}: {self.problem}"
        found = len(self.keepers)
        bits = [f"{found} keeper{'' if found == 1 else 's'}"]
        if found:
            low = min(k.price_jpy for k in self.keepers)
            high = max(k.price_jpy for k in self.keepers)
            bits.append(f"{low:,}–{high:,} ¥")
        bits.append(f"{self.inspected} inspected")
        if self.failed:
            bits.append(f"{self.failed} failed a requirement")
        if self.unconfirmed:
            bits.append(f"{self.unconfirmed} unconfirmed")
        # Said out loud, because "3 keepers" and "5 keepers" mean different
        # things depending on which of them stopped the walk.
        if self.stopped_by == "cap":
            bits.append(f"stopped at the {self.cap}-inspection cap")
        elif self.stopped_by == "exhausted" and found < KEEPERS:
            bits.append("archive exhausted")
        return f"{self.band.label}: " + " · ".join(bits)


@dataclass
class StatsResult:
    """One search's walk, one entry per band."""

    name: str
    bands: list[BandStats] = field(default_factory=list)

    @property
    def keepers(self) -> int:
        return sum(len(b.keepers) for b in self.bands)

    @property
    def inspected(self) -> int:
        return sum(b.inspected for b in self.bands)

    def summary(self) -> str:
        return (f"{self.name}: {self.keepers} benchmark(s) across "
                f"{len(self.bands)} band(s), {self.inspected} sheet(s) read")


def sheets_dir(name: str) -> Path:
    """Where one search's statistics sheets live: ``stats/<search>/sheets``.

    A directory per search rather than per walk, because the same lot turns up
    week after week and re-downloading a sheet already on disk is the one cost
    here that buys nothing at all.
    """
    return STATS_DIR / name / "sheets"


def stamp_path(name: str) -> Path:
    """Where a search records that it was last measured: ``stats/<search>/last_run``.

    A file rather than a query, because "when did this search last measure
    itself" is not a fact about any one lot. The database could only answer it
    by asking when a lot was last *seen*, which is a different question — a walk
    that found nothing would leave no trace at all and be re-run every morning.
    """
    return STATS_DIR / name / "last_run"


def last_run(name: str) -> date | None:
    """The date this search was last walked, or ``None`` if it never was."""
    path = stamp_path(name)
    if not path.exists():
        return None
    try:
        return date.fromisoformat(path.read_text(encoding="utf-8").strip())
    except ValueError:
        # An unreadable stamp means "measure it again", never "skip it": the
        # cautious reading of a corrupt file is the one that costs a walk, not
        # the one that silently leaves a price un-evidenced for weeks.
        return None


def record_run(name: str, today: date | None = None) -> None:
    path = stamp_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((today or date.today()).isoformat(), encoding="utf-8")


def is_fresh(name: str, max_age_days: int, today: date | None = None) -> bool:
    """Has this search been walked within ``max_age_days``?

    The guard behind ``daily.sh``: statistics move at the speed of the auction
    calendar, not the morning, so re-walking every day would spend on sheets to
    re-learn last week's answer.
    """
    stamped = last_run(name)
    if stamped is None:
        return False
    return ((today or date.today()) - stamped).days < max_age_days


def _check_ordering(prices: list[int | None]) -> None:
    """Every price we can see must ascend. Anything else and the sort has moved.

    Free to check: the visible prices come with the payload, so this runs over
    the whole page before a single sheet is downloaded. It is the cheap half of
    the guard; the expensive half is that a revealed price must not fall below
    the one before it either.
    """
    known = [p for p in prices if p]
    if known != sorted(known):
        raise OrderingBroken(
            "banzai24 returned prices out of order under sortPriceEnd=asc "
            f"({known[:6]}…). The walk reads the cheap end off that ordering, "
            "so it cannot trust this page. See docs/adr/0006."
        )


async def _sort_ascending(page) -> dict:
    """Click the price column into ascending order; return the sorted payload.

    Two clicks, because the header button opens a popover rather than sorting:
    *Конечная цена: По возрастанию / По убыванию*. The URL cannot do this — the
    server ignores sort parameters in the query string and the SPA holds the
    sort as client state — which is why this is DOM work rather than a filter.
    """
    headers = await page.query_selector_all(PRICE_HEADER)
    target = None
    for header in headers:
        if PRICE_HEADER_TEXT in (await header.inner_text()).lower():
            target = header
            break
    if target is None:
        raise OrderingBroken(
            "no price column on the archive table — banzai24's table has "
            "changed shape, and the walk cannot sort by price."
        )

    button = await target.query_selector(SORT_BUTTON)
    if button is None:
        raise OrderingBroken("the price column no longer offers a sort.")
    await button.click(force=True)
    await asyncio.sleep(1.0)

    options = await page.query_selector_all(SORT_OPTION)
    if not options:
        raise OrderingBroken("the sort popover offered no options.")

    async with page.expect_response(
        lambda r: session.LOTS_ENDPOINT in r.url, timeout=SORT_TIMEOUT_MS
    ) as info:
        await options[0].click(force=True)      # По возрастанию
    response = await info.value
    session.assert_authorized(response.status, None)
    return await response.json()


def _price_or_none(payload: object) -> int | None:
    """The hammer price out of a reveal response, or ``None`` for a failed read.

    Pure, and separate from the click, because the interesting half is not the
    clicking: the endpoint answers **HTTP 200 with zeros** — never 401 — to any
    caller lacking the SPA's Authorization header, so ``0`` is the shape a
    refusal arrives in. A zero stored as a price is a free car on a page that
    still renders, which is the one failure mode nothing downstream could catch.
    """
    if not isinstance(payload, dict):
        return None
    price = (payload.get("data") or {}).get("priceEnd")
    try:
        return int(price) or None
    except (TypeError, ValueError):
        return None


async def _reveal(page, index: int) -> int | None:
    """The hammer price of the masked lot in row ``index``, or ``None``.

    The row order is the payload order — this is the same list, rendered — so
    the index that identifies a lot in the response identifies its row.

    Returns ``None`` for a failed read, which includes the endpoint's own way of
    failing: **200 with zeros**, for any caller lacking the Authorization header
    the SPA adds from memory. A zero is not a price.
    """
    rows = await page.query_selector_all(ROW)
    if index >= len(rows):
        return None
    button = await rows[index].query_selector(REVEAL_BUTTON)
    if button is None:
        return None
    try:
        async with page.expect_response(
            lambda r: GET_PRICE in r.url, timeout=REVEAL_TIMEOUT_MS
        ) as info:
            await button.click(force=True)
        payload = await (await info.value).json()
    except Exception:
        return None
    return _price_or_none(payload)


async def _download_sheet(client: httpx.AsyncClient, item: dict, directory: Path) -> None:
    """Fetch one lot's sheet if it is not already on disk. Public URL, no session."""
    url = item.get("auctImage")
    if not url:
        return
    destination = directory / fetch.sheet_filename(item)
    if destination.exists():
        return
    directory.mkdir(parents=True, exist_ok=True)
    response = await client.get(url)
    response.raise_for_status()
    destination.write_bytes(response.content)
    await asyncio.sleep(fetch.IMAGE_DELAY_S)


def _store(item: dict, directory: Path) -> str | None:
    """Normalise one archive lot into the database. Returns its lot number.

    Written with ``discovered_by="stats"`` so ``db.pending_sheets()`` and
    ``db.lots_on()`` leave it alone: it is evidence for a price, not a car
    anyone can still bid on.
    """
    try:
        row = normalize.normalize_lot(item)
    except ValueError:
        return None
    row = normalize.attach_sheet(row, directory.parent)
    row["discovered_by"] = db.STATS
    db.upsert_lots([row])
    return row["lot_number"]


def _judge(definition: SearchDefinition, filters, lot_number: str, client):
    """``(assessment, spent)`` for one lot — reading its sheet if nobody has.

    ``spent`` is whether this cost a model call, which is what the cap counts. A
    lot already read in an earlier week is judged again for free, because the
    requirements it is judged against may have been re-tuned since.
    """
    lot = db.lots_by_numbers([lot_number]).get(lot_number)
    if lot is None or not lot.sheet_path:
        return None, False

    spent = False
    try:
        extraction = sheets.extract_lot(lot, client=client)
    except anthropic.AuthenticationError:
        # Not this sheet's fault, and not the next nineteen sheets' fault
        # either. Swallowed, it would burn the whole cap marking readable
        # sheets "failed" and then report a band with no cheap acceptable
        # sales — which is a real finding, and would be a lie.
        raise
    except (sheets.SheetRefused, anthropic.APIError, RuntimeError) as exc:
        db.mark_sheet_status(lot_number, "failed")
        print(f"    {lot.lot_short}: sheet unreadable — {exc}")
        return None, True
    if extraction is not None:
        db.upsert_extraction(sheets.to_row(extraction))
        spent = True

    stored = db.extraction_for(lot_number)
    if stored is None:
        return None, spent
    return requirements.judge(filters, definition.requirements, lot, stored), spent


async def walk_band(
    page,
    definition: SearchDefinition,
    band: Band,
    client=None,
    wanted: int = KEEPERS,
    cap: int = INSPECTION_CAP,
    headless: bool = False,
) -> BandStats:
    """Read the cheap end of one band's archive until it has ``wanted`` keepers."""
    filters = definition.stats_filters(band)
    url = config.build_search_url(filters)
    result = BandStats(band=band, url=url, cap=cap)

    await page.goto(url, wait_until="domcontentloaded")
    first = await fetch._await_first_page(page, headless=headless)
    await session.snapshot(page.context)

    if not (first or {}).get("items"):
        result.problem = "no sold lots in the archive for this band"
        return result

    payload = await _sort_ascending(page)
    items = payload.get("items") or []
    if not items:
        result.problem = "the sorted archive came back empty"
        return result

    _check_ordering([normalize.parse_price(i.get("endPrice")) for i in items])

    directory = sheets_dir(definition.name)
    last_price: int | None = None

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
        for index, item in enumerate(items):
            if len(result.keepers) >= wanted:
                result.stopped_by = "keepers"
                break
            if result.inspected >= cap:
                result.stopped_by = "cap"
                break

            result.considered += 1
            if not definition.lot_filters.matches(item):
                result.api_rejected += 1
                continue

            await _download_sheet(http, item, directory)
            lot_number = _store(item, directory)
            if lot_number is None:
                result.unreadable += 1
                continue

            assessment, spent = _judge(definition, filters, lot_number, client)
            result.inspected += spent
            if assessment is None:
                result.unreadable += 1
                continue
            if assessment.group == requirements.FAILS:
                result.failed += 1
                continue
            if assessment.group == requirements.UNCONFIRMED:
                result.unconfirmed += 1
                continue

            # Only now is a price worth a click.
            price = normalize.parse_price(item.get("endPrice"))
            revealed = False
            if not price:
                price = await _reveal(page, index)
                revealed = True
                await asyncio.sleep(REVEAL_DELAY_S)
            if not price:
                print(f"    {lot_number}: price would not reveal — skipped")
                result.unreadable += 1
                continue

            if last_price is not None and price < last_price:
                raise OrderingBroken(
                    f"{lot_number} revealed {price:,} ¥ after {last_price:,} ¥ — "
                    "the archive is no longer ordered by the price it hides. "
                    "See docs/adr/0006."
                )
            last_price = price

            if not db.set_end_price(lot_number, price):
                print(f"    {lot_number}: vanished before its price could be stored")
                result.unreadable += 1
                continue
            row = db.lots_by_numbers([lot_number]).get(lot_number)
            result.keepers.append(Benchmark(
                lot_number=lot_number,
                price_jpy=price,
                row=dict(row) if isinstance(row, dict) else row.model_dump(),
                revealed=revealed,
            ))
            print(f"    {lot_number}  {price:,} ¥"
                  f"{'  (revealed)' if revealed else ''}")

    return result


async def run_stats(
    definition: SearchDefinition,
    wanted: int = KEEPERS,
    cap: int = INSPECTION_CAP,
    headless: bool = False,
    client=None,
) -> StatsResult:
    """Walk every band of one search, in one browser session."""
    db.init_db()
    client = client or anthropic.Anthropic()
    result = StatsResult(name=definition.name)

    async with session.browser_context(headless=headless) as page:
        if not headless:
            print("Opening banzai24 — if it asks you to sign in, do it in that "
                  "window and the walk continues by itself.")
        for band in definition.bands:
            print(f"  {band.label}")
            stats = await walk_band(
                page, definition, band,
                client=client, wanted=wanted, cap=cap, headless=headless,
            )
            result.bands.append(stats)
            print(f"    {stats.summary()}")

    # Stamped only after every band is walked. A run that died halfway is not a
    # measurement, and marking it as one would leave the rest un-measured for a
    # week with nothing on the page to say so.
    record_run(definition.name)
    return result
