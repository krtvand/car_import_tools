"""Fetch banzai24 lots by driving the real page and reading its API responses.

The approach, and why it is not the obvious one: banzai24 has a clean JSON API,
but it is bearer-token authenticated and the token lives in the SPA's in-memory
store. Rather than extract and replay that token, we let the page make its own
authenticated calls and intercept the responses. That keeps us out of the
credential business entirely, and it survives banzai24 changing its auth scheme —
if the site still works in a browser, so does this.

Everything downstream reads the artifacts this module writes, so normalising,
extracting and reporting never touch the network or spend a session.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from playwright.async_api import Page, Response
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from . import config, lot_filters as lot_filters_mod, session
from .config import AuctionFilters
from .lot_filters import LotFilters
from .search import SearchDefinition

# `check` only needs *a* page that requires a session; it is not a search and
# nothing is fetched from it. Deliberately not one of the saved searches, so
# renaming or deleting one cannot break the session check.
CHECK_FILTERS = AuctionFilters(make="MAZDA", model="CX-30")

RUNS_DIR = Path(__file__).parent.parent / "runs"

# Pagination is client-side only — loading the UI URL with ?page=N is ignored,
# so pages are turned by clicking the numbered buttons the SPA renders.
PAGE_BUTTON = ".Page"
PAGE_BUTTON_ACTIVE = ".Page-active"

# Politeness: banzai24 is behind a login, so rate limiting is also account
# safety. A steady trickle, never a burst.
PAGE_DELAY_S = 1.5
IMAGE_DELAY_S = 0.2

RESPONSE_TIMEOUT_MS = 30_000

# One page of results, which is what a page-count limit used to buy.
DEFAULT_MAX_LOTS = 20

# `--max-lots` bounds the lots kept, so on its own it cannot bound the crawl: a
# filter that matches nothing would page to the end of the result set looking
# for lots it will never find. The day narrowing normally stops long before
# this; --all-days plus a narrow filter is the case that needs a floor under it.
# Hitting it is reported, never silent.
PAGE_SAFETY_LIMIT = 25

# The first page waits far longer than the rest, because it may be waiting for a
# human. banzai24's session does not survive the browser closing (its auth is
# bound to the browser lifetime, and only Chrome's "continue where you left off"
# hides that in everyday use), so rather than fight to persist it we simply keep
# the window open: if you are already signed in this returns instantly, and if
# not you sign in right there and the run continues in the same browser.
FIRST_RESPONSE_TIMEOUT_MS = 300_000

# A lot whose status is one of these has already been through the ring, so it is
# not "upcoming" however its trade date compares to today. This matters on the
# day itself: ``source=auctions`` keeps the morning's SOLD lots in the result set
# alongside the days still to come, so a plain min(tradeDate) would pick a day
# that is already over.
CONCLUDED_STATUSES = frozenset({"SOLD", "SOLD_BY_NEGO", "NOT_SOLD", "CANCELED", "CANCELLED", "REMOVED"})

# `tradeDate` is a Japanese calendar date, so "today" must be one too. Using the
# machine's local date instead would be wrong for six hours every evening: Japan
# rolls over at 18:00 Cyprus time, and in that window a JST day that has already
# finished still compares as today — leaving the status check alone to reject it.
# Deriving it from the data would be worse still, since the lots are the thing
# being filtered.
JAPAN_TZ = ZoneInfo("Asia/Tokyo")


def japan_today() -> date:
    """The current date in Japan — the calendar every ``tradeDate`` is written in."""
    return datetime.now(JAPAN_TZ).date()


def trade_date(lot: dict) -> str | None:
    """``"2026-08-11"`` — the auction day, as the API's ISO string.

    Normally straight off ``lot.tradeDate``. A lot from a *closed* auction —
    one the account may not see — comes back with its identifying fields
    blanked: no number, no auction, empty ``tradeDate``, ``status.code`` of
    ``"xxx"``. It keeps a populated ``tradeDateTime``, which is where the day
    comes from for those.

    Without the fallback such a lot has no day at all, so it can never be
    upcoming, and the day selection loses it in the worst possible way: it is
    not merely dropped, it is invisible to ``nearest_trade_date``, so a day
    whose lots are *all* closed is skipped over as if it were empty and the run
    silently reports on the day after. Every authenticated response so far has
    been fully populated; this is here so that changing what the account may see
    cannot quietly change which day a run is about.
    """
    day = ((lot.get("lot") or {}).get("tradeDate") or "").strip()
    if day:
        return day
    # "2026-08-18 14:28:00" — the same calendar date, space-separated.
    return (lot.get("tradeDateTime") or "").strip().partition(" ")[0] or None


def is_upcoming(lot: dict, today: date) -> bool:
    """Is this lot still to be sold? ``today`` is a *Japanese* date.

    Both halves are load-bearing and neither is redundant: the status rules out
    lots that have already been through the ring on a day still in progress, and
    the date rules out a stale non-terminal status left on an old lot.
    """
    day = trade_date(lot)
    if not day:
        return False
    status = ((lot.get("status") or {}).get("code") or "").upper()
    if status in CONCLUDED_STATUSES:
        return False
    return day >= today.isoformat()


def nearest_trade_date(lots: list[dict], today: date | None = None) -> str | None:
    """The closest auction day still ahead of us, or ``None`` if there is none."""
    today = today or japan_today()
    days = [d for lot in lots if (d := trade_date(lot)) and is_upcoming(lot, today)]
    return min(days) if days else None


def lots_on_nearest_day(
    lots: list[dict], today: date | None = None
) -> tuple[list[dict], str | None]:
    """Keep only the lots trading on the closest upcoming day.

    Returns the lots and the day chosen. When nothing is upcoming — an
    ``archive`` search, or a filter whose every lot has already sold — the
    filter is a no-op and every lot is returned, because a run that silently
    produced zero lots would look like a broken fetch.
    """
    today = today or japan_today()
    day = nearest_trade_date(lots, today)
    if day is None:
        return list(lots), None
    return [lot for lot in lots if trade_date(lot) == day and is_upcoming(lot, today)], day


def has_later_day(lots: list[dict], day: str, today: date | None = None) -> bool:
    """True once a lot beyond ``day`` has been seen — the signal to stop paging.

    ``source=auctions`` returns lots in trade-date order, so a later day showing
    up means the nearest day is complete and further pages hold only lots we are
    about to discard anyway.
    """
    today = today or japan_today()
    return any(
        (d := trade_date(lot)) and d > day and is_upcoming(lot, today) for lot in lots
    )


@dataclass
class Selection:
    """What a set of payloads yields, once the day and the filter are applied.

    Computed after every page so the paging loop and the finished run judge
    "how many lots do we have" by exactly the same rule.
    """

    fetched: list[dict]      # everything the site returned
    on_day: list[dict]       # …narrowed to the closest upcoming day
    kept: list[dict]         # …and matching the lot filter
    rejected: list[dict]     # on the day, but filtered out
    day: str | None


def select(
    payloads: list[dict],
    today: date | None = None,
    nearest_day_only: bool = True,
    lots_filter: LotFilters | None = None,
) -> Selection:
    """Apply the day narrowing and then the lot filter, in that order."""
    fetched = [item for payload in payloads for item in (payload.get("items") or [])]
    day = None
    on_day = fetched
    if nearest_day_only:
        on_day, day = lots_on_nearest_day(fetched, today or japan_today())
    kept, rejected = lot_filters_mod.split(on_day, lots_filter or LotFilters())
    return Selection(fetched=fetched, on_day=on_day, kept=kept, rejected=rejected, day=day)


@dataclass
class FetchResult:
    run_dir: Path
    lots: list[dict]
    pages_fetched: int
    total_pages: int
    total_lots: int
    truncated: bool
    sheets_downloaded: int = 0
    sheets_skipped: int = 0
    sheets_missing: int = 0
    trade_date: str | None = None      # the day we narrowed to, if we did
    lots_other_days: int = 0           # fetched, then set aside as later/past
    lots_filtered_out: int = 0         # on the day, then rejected by LotFilters
    truncated_by: str | None = None    # "--max-lots" | "page safety limit"

    def summary(self) -> str:
        scope = f" on {self.trade_date}" if self.trade_date else ""
        bits = [
            f"{len(self.lots)} lots{scope} from {self.pages_fetched}/{self.total_pages} page(s)",
            f"{self.sheets_downloaded} sheets downloaded",
        ]
        if self.lots_filtered_out:
            bits.append(f"{self.lots_filtered_out} lots filtered out")
        if self.lots_other_days:
            bits.append(f"{self.lots_other_days} lots on other days skipped")
        if self.sheets_skipped:
            bits.append(f"{self.sheets_skipped} already present")
        if self.sheets_missing:
            bits.append(f"{self.sheets_missing} lots have no sheet")
        if self.truncated:
            bits.append(f"TRUNCATED by {self.truncated_by or 'the page limit'}")
        return ", ".join(bits) + f" -> {self.run_dir}"


def _is_lots_response(response: Response) -> bool:
    return session.LOTS_ENDPOINT in response.url


async def _read_lots(response: Response) -> dict:
    """Parse a lots response, failing loudly if it says we're logged out."""
    try:
        payload = await response.json()
    except Exception as exc:  # non-JSON body means something is badly wrong
        raise session.SessionExpired(f"Unreadable lots response ({exc}).") from exc
    session.assert_authorized(response.status, payload)
    return payload


def _new_run_dir(filters: AuctionFilters, root: Path | None = None) -> Path:
    """``runs/2026-08-08_1630_MAZDA-CX-30/``.

    A run is one filter query, which may span several auction houses and trade
    days — so it is named after the query and the clock, not after an auction.
    """
    root = root or RUNS_DIR
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = root / f"{stamp}_{config.run_slug(filters)}"
    (run_dir / "sheets").mkdir(parents=True, exist_ok=True)
    return run_dir


async def _lots_from(page: Page, action, timeout_ms: int = RESPONSE_TIMEOUT_MS) -> dict:
    """Run ``action`` and return the lots payload it triggers.

    A timeout here usually is not a slow network: it is banzai24 answering with
    its 'service unavailable' page, which never fires the API call at all. We
    check the page body and say so, because "TimeoutError waiting for response"
    would send you debugging the wrong thing entirely.
    """
    try:
        async with page.expect_response(_is_lots_response, timeout=timeout_ms) as info:
            await action()
    except PlaywrightTimeoutError as exc:
        try:
            body = await page.inner_text("body")
        except Exception:
            body = ""
        if session.looks_blocked(body):
            raise session.ServiceUnavailable() from exc
        if session.looks_logged_out(body):
            raise session.SessionExpired(
                "Still on the signed-out page — no sign-in happened in time."
            ) from exc
        # Neither known case. Say what was actually on screen rather than
        # leaving a bare "waiting for event" for someone to guess at.
        try:
            where = page.url
            title = await page.title()
        except Exception:
            where, title = "?", "?"
        raise RuntimeError(
            f"No {session.LOTS_ENDPOINT} request was made within the timeout.\n"
            f"  url   : {where}\n"
            f"  title : {title}\n"
            f"  page  : {' '.join(body.split())[:300] or '(empty)'}"
        ) from exc
    return await _read_lots(await info.value)


# Page 1 never triggers an API call: Nuxt server-renders it into the HTML
# payload. Only changing page or tab calls /lots. So page 1 is read out of the
# hydration state, and pages 2+ are intercepted as normal.
_SSR_LOTS_JS = """() => {
  const looksRight = a =>
    Array.isArray(a) && a.length && a[0] && typeof a[0] === 'object'
    && 'auctImage' in a[0] && a[0].lot && a[0].lot.number;
  const hunt = (o, d = 0) => {
    if (!o || d > 8 || typeof o !== 'object') return null;
    if (Array.isArray(o)) {
      if (looksRight(o)) return o;
      for (const v of o) { const r = hunt(v, d + 1); if (r) return r; }
      return null;
    }
    for (const k of Object.keys(o)) { const r = hunt(o[k], d + 1); if (r) return r; }
    return null;
  };
  const items = hunt(window.__NUXT__ || {});
  return items ? { items } : null;
}"""


async def _ssr_lots(page: Page) -> dict | None:
    """Page 1's items, read from the server-rendered hydration state."""
    try:
        return await page.evaluate(_SSR_LOTS_JS)
    except Exception:
        return None


async def _dom_total_pages(page: Page) -> int:
    """Read the page count off the pagination buttons.

    The SSR payload carries items but not the ``pagination`` block the API
    returns, and the rendered buttons are the same information in a form we can
    actually see.
    """
    try:
        numbers = await page.eval_on_selector_all(
            PAGE_BUTTON,
            "els => els.map(e => parseInt((e.textContent || '').trim(), 10))"
            ".filter(n => Number.isFinite(n))",
        )
    except Exception:
        return 1
    return max(numbers) if numbers else 1


async def _goto_page(page: Page, number: int) -> dict:
    """Click pagination button ``number`` and return the resulting payload."""
    button = page.locator(PAGE_BUTTON).filter(has_text=re.compile(rf"^{number}$")).first
    return await _lots_from(page, button.click)


async def _await_first_page(page: Page, headless: bool) -> dict:
    """Wait until page 1's lots are readable, signing in inline if needed.

    Polls the hydration state rather than waiting on a network request, because
    page 1 makes none. The wait is generous when headed: it may be waiting for a
    human to complete an SMS sign-in in the visible window.
    """
    deadline = asyncio.get_running_loop().time() + (
        FIRST_RESPONSE_TIMEOUT_MS if not headless else RESPONSE_TIMEOUT_MS
    ) / 1000

    announced = False
    while asyncio.get_running_loop().time() < deadline:
        payload = await _ssr_lots(page)
        if payload:
            return payload

        body = ""
        try:
            body = await page.inner_text("body")
        except Exception:
            pass

        if session.looks_blocked(body):
            raise session.ServiceUnavailable()

        # A logout bounces to /?redirectTo=<original path>.
        signed_out = "redirectTo=" in page.url or session.looks_logged_out(body)
        if signed_out:
            if headless:
                raise session.SessionExpired("Redirected to the sign-in page.")
            if not announced:
                print("Not signed in — please sign in in the browser window; "
                      "the run resumes automatically.")
                announced = True
            # After signing in the site returns to the search on its own; if it
            # lands elsewhere, steer it back.
            if "redirectTo=" not in page.url and not session.looks_logged_out(body):
                await page.goto(page.url, wait_until="domcontentloaded")

        await asyncio.sleep(2.0)

    raise session.SessionExpired(
        "Timed out waiting for page 1 lots (never signed in?)."
        if not headless else "Timed out waiting for page 1 lots."
    )


async def fetch_lots(
    definition: SearchDefinition,
    max_lots: int = DEFAULT_MAX_LOTS,
    run_dir: Path | None = None,
    headless: bool = False,
    nearest_day_only: bool = True,
) -> FetchResult:
    """Collect lots for one saved search, writing raw payloads into a run directory.

    By default the run is narrowed to the **closest upcoming auction day**: the
    later days a search also returns are fetched but set aside, and paging stops
    as soon as a later day appears. That is the day you can actually still bid
    on, and it keeps the paid extraction step off lots that are weeks out.

    ``lots_filter`` is applied **within** that day, not before it. The day is
    chosen from everything on offer, and if nothing on it matches, the run is
    empty — the question being answered is "what should I look at for the next
    auction", so a match three weeks out is not a better answer than none.

    ``max_lots`` bounds **the lots the run keeps**, not the lots it reads. Those
    are the ones that go on to cost a sheet download and a paid extraction, and
    they are what a page count was always a clumsy proxy for: with a filter on,
    one page might yield twenty of them or none. So the run keeps turning pages
    until it has ``max_lots`` of them, the day is exhausted, or the safety limit
    below stops it.
    """
    filters = definition.filters
    lots_filter = definition.lot_filters
    url = config.build_search_url(filters)
    run_dir = run_dir or _new_run_dir(filters)

    payloads: list[dict] = []
    # Pinned once, so a run that crosses midnight in Tokyo cannot change its
    # mind about which day is nearest halfway through paging.
    today = japan_today()

    async with session.browser_context(headless=headless) as page:
        if not headless:
            print("Opening banzai24 — if it asks you to sign in, do it in that "
                  "window and the run continues by itself.")

        await page.goto(url, wait_until="domcontentloaded")
        first = await _await_first_page(page, headless=headless)
        await session.snapshot(page.context)  # only after a confirmed-good load
        payloads.append(first)

        total_pages = await _dom_total_pages(page)

        hard_stop = min(total_pages, PAGE_SAFETY_LIMIT)
        for number in range(2, hard_stop + 1):
            if _enough(payloads, max_lots, today, nearest_day_only, lots_filter):
                break
            await asyncio.sleep(PAGE_DELAY_S)
            payloads.append(await _goto_page(page, number))

    chosen = select(payloads, today, nearest_day_only, lots_filter)
    lots = chosen.kept[:max_lots]

    # Page 1 is server-rendered and carries no totals; any later page came from
    # the API and does. Prefer the API's numbers when we have them, so the
    # summary doesn't report page 1's item count as the whole result set.
    api_pagination = next((p["pagination"] for p in payloads if p.get("pagination")), {})
    total_lots = int(api_pagination.get("total") or 0) or len(chosen.fetched)
    total_pages = int(api_pagination.get("totalPages") or 0) or total_pages

    truncated_by = _truncation_reason(
        kept=len(chosen.kept),
        max_lots=max_lots,
        unread_pages=total_pages > len(payloads),
        day_done=bool(chosen.day) and _nearest_day_complete(payloads, today),
    )

    result = FetchResult(
        run_dir=run_dir,
        lots=lots,
        pages_fetched=len(payloads),
        total_pages=total_pages,
        total_lots=total_lots,
        truncated=truncated_by is not None,
        truncated_by=truncated_by,
        trade_date=chosen.day,
        lots_other_days=len(chosen.fetched) - len(chosen.on_day),
        lots_filtered_out=len(chosen.rejected),
    )
    _write_lots_json(result, definition, url, payloads)
    return result


def _enough(
    payloads: list[dict],
    max_lots: int,
    today: date | None = None,
    nearest_day_only: bool = True,
    lots_filter: LotFilters | None = None,
) -> bool:
    """Is there any reason left to turn another page?

    Two reasons to stop, and they are independent: we have the lots we asked
    for, or the day we care about is over. The second is why a filter matching
    nothing still terminates quickly instead of chasing ``max_lots`` to the end
    of the result set.
    """
    if len(select(payloads, today, nearest_day_only, lots_filter).kept) >= max_lots:
        return True
    return nearest_day_only and _nearest_day_complete(payloads, today)


def _truncation_reason(
    kept: int, max_lots: int, unread_pages: bool, day_done: bool
) -> str | None:
    """Why the run stopped short, or ``None`` if it did not.

    Stopping because the day is complete is not truncation — everything the run
    wanted is in hand, and saying otherwise would train you to ignore the
    warning. Only a count cutting the results short, or the safety limit firing,
    leaves something behind.
    """
    if kept > max_lots:
        return "--max-lots"
    if not unread_pages or day_done:
        return None
    if kept >= max_lots:
        return "--max-lots"
    return f"the {PAGE_SAFETY_LIMIT}-page safety limit"


def _nearest_day_complete(payloads: list[dict], today: date | None = None) -> bool:
    """Have we already seen past the closest upcoming day?

    Judged over every lot, not the filtered ones, because the day is chosen that
    way too: once a later day appears the nearest day is complete, whether or not
    anything on it survived the filter. Paging on in the hope of a match would be
    searching the days the run has deliberately excluded.
    """
    today = today or japan_today()
    lots = [item for payload in payloads for item in (payload.get("items") or [])]
    day = nearest_trade_date(lots, today)
    return bool(day) and has_later_day(lots, day, today)


def _write_lots_json(
    result: FetchResult,
    definition: SearchDefinition,
    url: str,
    payloads: list[dict],
) -> Path:
    """Persist the API payloads verbatim, wrapped in run provenance.

    ``search`` records the saved search by **name** as well as by value.
    ``report`` re-loads the named file rather than reading these back, so
    re-tuning a requirement re-judges this run for free; the copy here is what
    the run was judged by on the day, and is used only if the file has since
    been deleted or renamed.
    """
    path = result.run_dir / "lots.json"
    path.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "search_url": url,
                "search": definition.to_payload(),
                "pages_fetched": result.pages_fetched,
                "total_pages": result.total_pages,
                "total_lots": result.total_lots,
                "truncated": result.truncated,
                # The day this run is about; `pages` below still holds every lot
                # the site returned, including the later days we set aside.
                "trade_date": result.trade_date,
                "lots_selected": [
                    (lot.get("lot") or {}).get("number") for lot in result.lots
                ],
                "lots_other_days": result.lots_other_days,
                "lots_filtered_out": result.lots_filtered_out,
                # untouched, exactly as the API returned them
                "pages": payloads,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return path


def sheet_filename(lot: dict) -> str:
    """``47-1312-35159.jpg`` — banzai24's globally unique lot number."""
    number = (lot.get("lot") or {}).get("number") or lot.get("id") or "unknown"
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in str(number))
    return f"{safe}.jpg"


async def download_sheets(result: FetchResult) -> FetchResult:
    """Download each lot's auction sheet into ``<run>/sheets/``.

    Sheet URLs are public — verified during Phase 0 that they need no cookie or
    token — so this is a plain HTTP client, no browser involved.
    """
    sheets_dir = result.run_dir / "sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for lot in result.lots:
            url = lot.get("auctImage")
            if not url:
                result.sheets_missing += 1
                continue

            destination = sheets_dir / sheet_filename(lot)
            if destination.exists():
                result.sheets_skipped += 1
                continue

            response = await client.get(url)
            response.raise_for_status()
            destination.write_bytes(response.content)
            result.sheets_downloaded += 1
            await asyncio.sleep(IMAGE_DELAY_S)

    return result


def sha256_of(path: Path) -> str:
    """Content hash of a saved sheet — the dedup key for paid extraction."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class SessionCheck:
    """What a session check saw. ``pages`` is 1 when the search fits one page.

    Deliberately not a single "total": the SSR payload that page 1 arrives in
    carries no ``pagination`` block, so multiplying page 1's count by the page
    count would be a guess dressed up as a total. Reporting both numbers says
    exactly what is known.
    """

    lots_on_first_page: int
    pages: int

    def describe(self) -> str:
        if self.pages <= 1:
            return f"{self.lots_on_first_page} lots match"
        return f"{self.lots_on_first_page} lots on page 1 of {self.pages}"


async def check_session(
    filters: AuctionFilters | None = None, headless: bool = True
) -> SessionCheck:
    """Confirm the saved session still authenticates; report what it matched.

    Raises :class:`session.SessionExpired` otherwise. Worth running before a long
    crawl — re-authenticating costs an SMS, so it is better to learn about a dead
    session deliberately than halfway through a run.

    Reads page 1 the same way :func:`fetch_lots` does — out of the hydration
    state, via :func:`_await_first_page` — and for the same reason: **page 1
    makes no API call**, so waiting for a ``/lots`` response here times out on a
    perfectly healthy session and reports it as expired. That is the Phase 1
    trap, and this function kept falling into it after the fetch path stopped:
    a check whose failure mode is "your session is fine, and I say it is dead"
    is worse than no check, because it sends you to re-authenticate by SMS for
    nothing.
    """
    url = config.build_search_url(filters or CHECK_FILTERS)
    async with session.browser_context(headless=headless) as page:
        await page.goto(url, wait_until="commit")
        payload = await _await_first_page(page, headless)
        # The SSR payload carries items but no `pagination` block, so the total
        # comes off the rendered pagination buttons — one page's worth of lots
        # is the floor when there is only one page.
        pages = await _dom_total_pages(page)
    return SessionCheck(lots_on_first_page=len(payload.get("items") or []), pages=pages)


async def run_fetch(
    definition: SearchDefinition,
    max_lots: int = DEFAULT_MAX_LOTS,
    sheets: bool = True,
    headless: bool = False,
    nearest_day_only: bool = True,
) -> FetchResult:
    """``fetch`` end to end: lots, then their sheets."""
    result = await fetch_lots(
        definition,
        max_lots=max_lots,
        headless=headless,
        nearest_day_only=nearest_day_only,
    )
    if sheets:
        await download_sheets(result)
    return result