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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from playwright.async_api import Page, Response
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from . import config, session
from .config import AuctionFilters

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

# The first page waits far longer than the rest, because it may be waiting for a
# human. banzai24's session does not survive the browser closing (its auth is
# bound to the browser lifetime, and only Chrome's "continue where you left off"
# hides that in everyday use), so rather than fight to persist it we simply keep
# the window open: if you are already signed in this returns instantly, and if
# not you sign in right there and the run continues in the same browser.
FIRST_RESPONSE_TIMEOUT_MS = 300_000


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

    def summary(self) -> str:
        bits = [
            f"{len(self.lots)} lots from {self.pages_fetched}/{self.total_pages} page(s)",
            f"{self.sheets_downloaded} sheets downloaded",
        ]
        if self.sheets_skipped:
            bits.append(f"{self.sheets_skipped} already present")
        if self.sheets_missing:
            bits.append(f"{self.sheets_missing} lots have no sheet")
        if self.truncated:
            bits.append("TRUNCATED by --max-pages")
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
    filters: AuctionFilters | None = None,
    max_pages: int = 1,
    run_dir: Path | None = None,
    headless: bool = False,
) -> FetchResult:
    """Collect lots for ``filters``, writing raw payloads into a run directory."""
    filters = filters or config.DEFAULT_FILTERS
    url = config.build_search_url(filters)
    run_dir = run_dir or _new_run_dir(filters)

    payloads: list[dict] = []

    async with session.browser_context(headless=headless) as page:
        if not headless:
            print("Opening banzai24 — if it asks you to sign in, do it in that "
                  "window and the run continues by itself.")

        await page.goto(url, wait_until="domcontentloaded")
        first = await _await_first_page(page, headless=headless)
        await session.snapshot(page.context)  # only after a confirmed-good load
        payloads.append(first)

        total_pages = await _dom_total_pages(page)

        wanted = min(total_pages, max_pages)
        for number in range(2, wanted + 1):
            await asyncio.sleep(PAGE_DELAY_S)
            payloads.append(await _goto_page(page, number))

    lots = [item for payload in payloads for item in (payload.get("items") or [])]

    # Page 1 is server-rendered and carries no totals; any later page came from
    # the API and does. Prefer the API's numbers when we have them, so the
    # summary doesn't report page 1's item count as the whole result set.
    api_pagination = next((p["pagination"] for p in payloads if p.get("pagination")), {})
    total_lots = int(api_pagination.get("total") or 0) or len(lots)
    total_pages = int(api_pagination.get("totalPages") or 0) or total_pages

    result = FetchResult(
        run_dir=run_dir,
        lots=lots,
        pages_fetched=len(payloads),
        total_pages=total_pages,
        total_lots=total_lots,
        truncated=total_pages > len(payloads),
    )
    _write_lots_json(result, filters, url, payloads)
    return result


def _write_lots_json(
    result: FetchResult,
    filters: AuctionFilters,
    url: str,
    payloads: list[dict],
) -> Path:
    """Persist the API payloads verbatim, wrapped in run provenance."""
    path = result.run_dir / "lots.json"
    path.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "search_url": url,
                "filters": asdict(filters),
                "pages_fetched": result.pages_fetched,
                "total_pages": result.total_pages,
                "total_lots": result.total_lots,
                "truncated": result.truncated,
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


async def check_session(filters: AuctionFilters | None = None, headless: bool = True) -> int:
    """Confirm the saved session still authenticates; return the matching lot count.

    Raises :class:`session.SessionExpired` otherwise. Worth running before a long
    crawl — re-authenticating costs an SMS, so it is better to learn about a dead
    session deliberately than halfway through a run.
    """
    filters = filters or config.DEFAULT_FILTERS
    url = config.build_search_url(filters)
    async with session.browser_context(headless=headless) as page:
        payload = await _lots_from(page, lambda: page.goto(url, wait_until="commit"))
    pagination = payload.get("pagination") or {}
    return int(pagination.get("total") or len(payload.get("items") or []))


async def run_fetch(
    filters: AuctionFilters | None = None,
    max_pages: int = 1,
    sheets: bool = True,
    headless: bool = False,
) -> FetchResult:
    """``fetch`` end to end: lots, then their sheets."""
    result = await fetch_lots(filters, max_pages=max_pages, headless=headless)
    if sheets:
        await download_sheets(result)
    return result