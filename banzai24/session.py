"""Persisted, hand-authenticated browser session for banzai24.

Two constraints shape this module.

**banzai24 needs a Russian locale.** Its backend answers ``HTTP 500 Service
Unavailable`` when ``Accept-Language`` is not Russian — an automated browser
looks "blocked" but is really just being told the wrong language. Setting
``locale="ru-RU"`` is the entire fix; it was verified by A/B probe against the
same URL, same machine, same minute (``en`` → 500, ``ru-RU`` → 200). Everything
else here — the real Chrome binary, the persistent profile, the
``AutomationControlled`` switch — is defence in depth for a site that is clearly
particular about its clients, not the thing that made it work.

**Login is SMS 2FA, and the session does not outlive the browser.** banzai24
binds auth to the browser lifetime; everyday Chrome only appears to survive a
restart because "continue where you left off" restores session cookies. Several
attempts to persist it — profile directory, cookie snapshot, ``__Host-`` cookie
replay — all failed, so the design stopped fighting it: :func:`login` is now
optional convenience, and a fetch simply keeps its window open, signing in
inline if needed. No password or token is ever read, stored or reconstructed
here; Chrome holds the credentials throughout.

Because signing in costs a phone round-trip, a run resolves auth on its very
first request and fails loudly otherwise, rather than quietly filling a run
directory with logged-out responses that later look like a parser bug.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from playwright.async_api import BrowserContext, Page, async_playwright

from . import config

# A dedicated Chrome profile, kept out of the repo. Separate from your everyday
# Chrome profile so a crawl never disturbs (or is disturbed by) normal browsing.
PROFILE_DIR = Path(__file__).parent / ".chrome-profile"

# Cookie snapshot, taken while the login window is open and replayed into later
# launches. The profile directory alone is not enough: banzai24's refresh cookie
# is a *session* cookie, which Chrome drops on exit unless it was told to
# restore the previous session. Everyday Chrome hides this ("continue where you
# left off"); a Playwright-launched profile does not, so signing in appeared to
# work and then evaporated the moment the window closed.
SESSION_PATH = Path(__file__).parent / ".session.json"

# The API answers unauthenticated calls with this instead of an HTTP error.
MISSING_TOKEN_MARKER = "missing token"

# The endpoint that only answers for a signed-in user. Shared with
# :mod:`banzai24.fetch`, which lives here to avoid a circular import.
LOTS_ENDPOINT = "/api/catalog-service/lots"

LOGIN_TIMEOUT_S = 600
LOGIN_POLL_S = 2.0
VERIFY_TIMEOUT_MS = 20_000

# Chrome's own automation switch. Disabling it clears navigator.webdriver.
CHROME_ARGS = ["--disable-blink-features=AutomationControlled"]

# Load-bearing: banzai24's backend 500s on a non-Russian Accept-Language.
LOCALE = "ru-RU"
TIMEZONE = "Europe/Moscow"


class SessionExpired(RuntimeError):
    """Raised when the saved profile no longer authenticates.

    Only a human can fix this (SMS), so it is deliberately fatal rather than
    something the fetcher retries.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            (detail + " " if detail else "")
            + "banzai24 session is missing or expired — "
            + "run `uv run python -m banzai24 login` and sign in again."
        )


class ServiceUnavailable(RuntimeError):
    """Raised when banzai24 serves its 'service unavailable' page.

    Distinct from :class:`SessionExpired`: the session may be perfectly valid and
    the site simply declined to serve this request. Re-logging in will not help,
    so the two must not be conflated in the advice given.

    The known cause is a non-Russian ``Accept-Language`` (see :data:`LOCALE`);
    if that is already set, the site is probably genuinely down.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            (detail + " " if detail else "")
            + "banzai24 returned its 'service temporarily unavailable' page. This is "
            + f"what it does when Accept-Language is not Russian — check that "
            + f"session.LOCALE is still {LOCALE!r}. If it is, the site is likely "
            + "genuinely down; try again in a few minutes."
        )


def session_exists() -> bool:
    """True if there is anything to authenticate with — snapshot or profile."""
    return SESSION_PATH.exists() or (PROFILE_DIR.exists() and any(PROFILE_DIR.iterdir()))


async def snapshot(context: BrowserContext) -> bool:
    """Save the live cookie jar, including session cookies.

    Called while a browser is *alive*, because that is the only time the auth
    cookie exists: banzai24 rotates it per browser lifetime and Chrome drops
    session cookies on exit, so a profile that just worked is dead by the next
    launch unless we captured it first.
    """
    try:
        await context.storage_state(path=str(SESSION_PATH))
        return True
    except Exception:
        return False


def _restorable(cookie: dict) -> dict:
    """Rewrite a stored cookie into a form ``add_cookies`` will actually accept.

    banzai24 authenticates with ``__Host-csrf``. The ``__Host-`` prefix requires
    a host-only cookie with **no Domain attribute**, but ``storage_state``
    exports every cookie with a ``domain`` field — feed that straight back and
    Chrome rejects it *silently*, so the session looks restored and is not. Any
    host-only cookie is therefore replayed by ``url`` instead of domain+path.
    """
    domain = (cookie.get("domain") or "").strip()
    host_only = not domain.startswith(".")
    if not (host_only and domain):
        return cookie

    restored = {k: v for k, v in cookie.items() if k not in ("domain", "path")}
    scheme = "https" if cookie.get("secure", True) else "http"
    restored["url"] = f"{scheme}://{domain}{cookie.get('path') or '/'}"
    return restored


def saved_cookies() -> list[dict]:
    """Cookies captured during login, including session cookies."""
    if not SESSION_PATH.exists():
        return []
    try:
        raw = json.loads(SESSION_PATH.read_text(encoding="utf-8")).get("cookies") or []
    except (ValueError, OSError):
        return []
    return [_restorable(c) for c in raw]


async def authenticated_on(page: Page) -> bool:
    """True if ``page``'s profile can actually load lots.

    The only honest login test. Cookies are useless as a signal here: banzai24
    sets ``phone`` / ``safariLoginNumber`` the moment you *type* a phone number,
    well before the SMS is verified — an earlier version watched for those and
    declared success mid-login, closing the window on a half-authenticated
    profile. So we ask the question that actually matters: does the
    authenticated endpoint answer?

    Navigates the page it is given rather than opening a tab of its own; a
    second tab appearing mid-login was confusing and served no purpose.
    """
    try:
        async with page.expect_response(
            lambda r: LOTS_ENDPOINT in r.url, timeout=VERIFY_TIMEOUT_MS
        ) as info:
            await page.goto(config.build_search_url(), wait_until="commit")
        response = await info.value
        payload = await response.json()
    except Exception:
        return False

    try:
        assert_authorized(response.status, payload)
    except SessionExpired:
        return False
    return payload.get("items") is not None


async def profile_is_authenticated(headless: bool = True) -> bool:
    """Open the saved profile and check it can load lots."""
    if not session_exists():
        return False
    async with _persistent_context(headless=headless) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        return await authenticated_on(page)


@asynccontextmanager
async def _persistent_context(headless: bool) -> AsyncIterator[BrowserContext]:
    """Open the dedicated Chrome profile.

    ``channel="chrome"`` selects the real Google Chrome binary rather than
    Playwright's bundled Chromium — the difference banzai24 notices.
    """
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=headless,
            args=CHROME_ARGS,
            locale=LOCALE,          # load-bearing — see module docstring
            timezone_id=TIMEZONE,
            viewport={"width": 1440, "height": 900},
        )
        cookies = saved_cookies()
        if cookies:
            await context.add_cookies(cookies)
        try:
            yield context
        finally:
            # login() waits for you to close the window, so by here the context
            # may already be gone — closing a closed context is not an error.
            try:
                await context.close()
            except Exception:
                pass


async def login(timeout_s: float = LOGIN_TIMEOUT_S) -> Path:
    """Open the profile in a real Chrome window and wait until you've signed in.

    **You** signal completion, by closing the window. Every attempt to infer it
    automatically was worse: cookies fire before the SMS is verified, and
    watching the page for the sign-in link to disappear never fired at all.
    Closing a window is unambiguous, needs no stdin, and cannot go off early.

    Chrome writes the profile to disk as it goes, so by the time the window is
    gone the credentials are already saved; the check afterwards just confirms
    it rather than being the thing that captures anything.
    """
    async with _persistent_context(headless=False) as context:
        page = context.pages[0] if context.pages else await context.new_page()

        if await authenticated_on(page):
            # Snapshot before returning. Skipping this is what made the previous
            # attempt fail: it reported success, closed the browser, and took the
            # only live copy of the session down with it.
            await snapshot(context)
            print("Already signed in — session captured.")
            return PROFILE_DIR

        await page.goto(config.BASE_URL, wait_until="domcontentloaded")
        print(
            "\nA Chrome window is open on banzai24.com.\n"
            "  1. Sign in: phone number, then the SMS code.\n"
            "  2. When you are signed in, CLOSE THE WINDOW.\n"
            "     Nothing is checked until you do, so take as long as you need.\n"
        )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while context.pages:
            if loop.time() > deadline:
                raise SessionExpired(
                    f"Gave up waiting after {int(timeout_s / 60)} minutes."
                )
            # Snapshot continuously rather than once at the end: session cookies
            # only exist while the browser is alive, so the last snapshot before
            # you close the window is the one that carries the login.
            if not await snapshot(context):
                break  # window closed mid-snapshot; the previous one stands
            await asyncio.sleep(1.0)

    print("Window closed — verifying…")
    if not await profile_is_authenticated():
        raise SessionExpired("Sign-in did not complete.")

    print(f"Signed in. Profile saved -> {PROFILE_DIR}")
    return PROFILE_DIR


@asynccontextmanager
async def browser_context(headless: bool = True) -> AsyncIterator[Page]:
    """Yield a page in the signed-in Chrome profile.

    Headless is fine — verified serving 200s once the locale is right. Headed is
    only needed for :func:`login`, where a human has to type an SMS code.
    """
    if not session_exists():
        raise SessionExpired("No saved profile.")

    async with _persistent_context(headless=headless) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        # Deliberately no snapshot on exit: a failed run would overwrite a good
        # snapshot with its signed-out state. fetch() captures explicitly, only
        # once it has a confirmed-good response in hand.
        yield page


def assert_authorized(status: int, payload: object) -> None:
    """Raise :class:`SessionExpired` if a lots response shows we're logged out.

    Checks both shapes the API uses: a 401/403 status, and a 200 whose body is
    ``{"error": "missing token"}``.
    """
    if status in (401, 403):
        raise SessionExpired(f"API returned HTTP {status}.")
    if isinstance(payload, dict):
        error = str(payload.get("error", "")).lower()
        if MISSING_TOKEN_MARKER in error:
            raise SessionExpired("API returned 'missing token'.")


# Text banzai24 puts on its anti-automation interstitial (Russian original and
# the English rendering seen in the wild).
_BLOCK_MARKERS = (
    "service is temporarily unavailable",
    "temporarily unavailable",
    "временно недоступен",
)


def looks_blocked(page_text: str) -> bool:
    """True if a page body is banzai24's unavailable page rather than content."""
    lowered = page_text.lower()
    return any(marker in lowered for marker in _BLOCK_MARKERS)


# A logged-out banzai24 renders a sign-in affordance and never calls the lots
# endpoint at all — so the symptom is a missing request, not a failed one.
_LOGGED_OUT_MARKERS = ("войти", "sign in", "log in")


def looks_logged_out(page_text: str) -> bool:
    """True if a page is banzai24's signed-out view.

    Needed because the signed-out SPA simply never fires the lots request, so
    the only symptom is a timeout — indistinguishable from a network problem
    unless we look at what is actually on screen.
    """
    lowered = page_text.lower()
    return any(marker in lowered for marker in _LOGGED_OUT_MARKERS)