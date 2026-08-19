"""The logged-out detection that keeps a dead session from looking like a bug."""
from __future__ import annotations

import asyncio
import json

import pytest

from banzai24 import session
from banzai24.session import SessionExpired


def test_valid_response_passes():
    session.assert_authorized(200, {"items": [], "pagination": {}})


@pytest.mark.parametrize("status", [401, 403])
def test_unauthorized_status_raises(status):
    with pytest.raises(SessionExpired):
        session.assert_authorized(status, {})


def test_missing_token_body_raises_even_on_http_200():
    """The API answers a logged-out call with 200 + an error body, not a 4xx."""
    with pytest.raises(SessionExpired):
        session.assert_authorized(200, {"error": "missing token"})


def test_missing_token_detection_is_case_insensitive():
    with pytest.raises(SessionExpired):
        session.assert_authorized(200, {"error": "Missing Token"})


def test_unrelated_error_body_is_not_treated_as_logged_out():
    session.assert_authorized(200, {"error": "no lots matched"})


def test_non_dict_payload_is_tolerated():
    session.assert_authorized(200, [])


def test_message_tells_the_user_how_to_recover():
    """SMS 2FA means only a human can fix this — the error must say so."""
    with pytest.raises(SessionExpired, match="login"):
        session.assert_authorized(401, {})


def test_block_page_is_recognised():
    """banzai24 serves this to browsers it thinks are automated."""
    assert session.looks_blocked(
        "The service is temporarily unavailable. We are aware of the issue."
    )
    assert session.looks_blocked("Сервис временно недоступен")


def test_normal_page_is_not_mistaken_for_a_block():
    assert not session.looks_blocked("Поиск автомобилей в Японии — 61 лот")


def test_blocked_and_expired_are_different_diagnoses():
    """Re-logging in cannot fix a 500, so the advice must differ."""
    assert "login" in str(session.SessionExpired())
    assert "Accept-Language" in str(session.ServiceUnavailable())
    assert not issubclass(session.ServiceUnavailable, session.SessionExpired)


def test_russian_locale_is_configured():
    """Load-bearing: banzai24 500s on a non-Russian Accept-Language."""
    assert session.LOCALE == "ru-RU"


def test_signed_out_page_is_recognised():
    """Logged out, the SPA never calls /lots — the only clue is the sign-in link."""
    assert session.looks_logged_out("Ru Войти Поиск автомобилей в Японии")
    assert session.looks_logged_out("Sign in to continue")


def test_signed_in_page_is_not_mistaken_for_signed_out():
    assert not session.looks_logged_out("Ru +79271805343 Поиск автомобилей в Японии")


def test_lots_endpoint_is_shared_with_fetch():
    """fetch.py imports it from here to avoid a circular import."""
    from banzai24 import fetch
    assert session.LOTS_ENDPOINT == "/api/catalog-service/lots"
    assert fetch._is_lots_response(type("R", (), {"url": "https://x" + session.LOTS_ENDPOINT})())


def test_login_no_longer_infers_completion_from_cookies():
    """Cookies fire when you type a phone number, before the SMS is verified.

    Guards against reintroducing the bug that closed the window mid-login.
    """
    assert not hasattr(session, "AUTH_COOKIE_HINTS")
    assert not hasattr(session, "_auth_cookie_names")


def test_saved_cookies_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSION_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(session, "PROFILE_DIR", tmp_path / "no-profile")
    assert session.saved_cookies() == []
    assert session.session_exists() is False


def test_saved_cookies_reads_storage_state(tmp_path, monkeypatch):
    """A session cookie is expires=-1 — it must survive the round trip."""
    path = tmp_path / ".session.json"
    path.write_text(json.dumps({
        "cookies": [{"name": "refresh", "value": "x", "domain": ".banzai24.com",
                     "path": "/", "expires": -1, "httpOnly": True, "secure": True,
                     "sameSite": "Lax"}],
        "origins": [],
    }))
    monkeypatch.setattr(session, "SESSION_PATH", path)
    cookies = session.saved_cookies()
    assert session.session_exists() is True
    assert cookies[0]["name"] == "refresh"
    assert cookies[0]["expires"] == -1


def test_saved_cookies_tolerates_corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / ".session.json"
    path.write_text("{ not json")
    monkeypatch.setattr(session, "SESSION_PATH", path)
    assert session.saved_cookies() == []


def test_snapshot_writes_cookies_without_opening_a_page(tmp_path, monkeypatch):
    """Playwright's storage_state opens a throwaway page per origin to read
    localStorage. On review()'s once-a-second loop that is a tab blinking in
    front of you, and nothing ever restores what it collects."""
    monkeypatch.setattr(session, "SESSION_PATH", tmp_path / "session.json")

    class FakeContext:
        async def cookies(self):
            return [{"name": "__Host-csrf", "value": "x", "domain": "banzai24.com",
                     "path": "/", "expires": -1, "secure": True}]

        async def storage_state(self, **kwargs):
            raise AssertionError("storage_state opens pages; snapshot must not")

    assert asyncio.run(session.snapshot(FakeContext()))
    saved = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    assert [c["name"] for c in saved["cookies"]] == ["__Host-csrf"]
    assert saved["origins"] == []  # the shape saved_cookies expects, kept empty


def test_snapshot_round_trips_through_saved_cookies(tmp_path, monkeypatch):
    """What snapshot writes must be what the next launch can replay."""
    monkeypatch.setattr(session, "SESSION_PATH", tmp_path / "session.json")

    class FakeContext:
        async def cookies(self):
            return [{"name": "__Host-csrf", "value": "x", "domain": "banzai24.com",
                     "path": "/", "expires": -1, "secure": True}]

    asyncio.run(session.snapshot(FakeContext()))
    replayed = session.saved_cookies()
    # __Host- cookies must come back keyed by url, not domain — see _restorable.
    assert replayed[0]["url"] == "https://banzai24.com/"
    assert "domain" not in replayed[0]


def test_snapshot_survives_a_context_that_is_already_gone(tmp_path, monkeypatch):
    """The window can close mid-poll; the previous snapshot stands."""
    monkeypatch.setattr(session, "SESSION_PATH", tmp_path / "session.json")

    class ClosedContext:
        async def cookies(self):
            raise RuntimeError("Target page, context or browser has been closed")

    assert asyncio.run(session.snapshot(ClosedContext())) is False


def test_session_exists_accepts_either_snapshot_or_profile(tmp_path, monkeypatch):
    """A live profile with no snapshot yet is still something to try."""
    snap, profile = tmp_path / ".session.json", tmp_path / ".chrome-profile"
    monkeypatch.setattr(session, "SESSION_PATH", snap)
    monkeypatch.setattr(session, "PROFILE_DIR", profile)
    assert session.session_exists() is False

    profile.mkdir()
    (profile / "Default").mkdir()
    assert session.session_exists() is True

    snap.write_text('{"cookies": [], "origins": []}')
    assert session.session_exists() is True


# --- browsing the profile by hand -----------------------------------------


class _FakePage:
    def __init__(self):
        self.visited: list[str] = []

    async def goto(self, url, **kwargs):
        self.visited.append(url)


class _FakeContext:
    """Enough of a BrowserContext for `review`, which needs a real Chrome.

    `review` waits on `pages` emptying — that is how a human closing the window
    reaches it — so tests close the window by emptying this list.
    """

    def __init__(self):
        self.page = _FakePage()
        self.pages = [self.page]

    async def new_page(self):
        return self.page


def _fake_context(context):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def factory(headless: bool):
        yield context

    return factory


def _closing_snapshot(context):
    """A snapshot that closes the window on the first poll."""

    async def snapshot(ctx):
        context.pages.clear()
        return True

    return snapshot


def _async_returning(value):
    async def coroutine(*args, **kwargs):
        return value
    return coroutine


def test_profile_busy_reads_a_symlink_lock(tmp_path, monkeypatch):
    """Chrome's SingletonLock points at `<host>-<pid>`, which does not exist.

    `Path.exists()` follows the link and answers False for a lock that is
    plainly there, so a dangling symlink has to be the thing we look for.
    """
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setattr(session, "PROFILE_DIR", profile)
    assert not session.profile_busy()

    (profile / session.SINGLETON_LOCK).symlink_to("some-host-12345")
    assert not (profile / session.SINGLETON_LOCK).exists()  # the trap
    assert session.profile_busy()


def test_profile_busy_is_false_without_a_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "PROFILE_DIR", tmp_path / "never-launched")
    assert not session.profile_busy()


def test_busy_message_names_the_real_cause_and_does_not_send_you_to_login():
    """A lock collision and an expired session look identical from the outside
    and have opposite remedies; re-logging in costs an SMS and fixes nothing."""
    message = str(session.ProfileBusy())
    assert "already open" in message
    assert "session is fine" in message.lower()
    assert "banzai24 login" not in message
    assert session.SINGLETON_LOCK in message  # how to clear a stale one


def test_persistent_context_refuses_a_busy_profile(tmp_path, monkeypatch):
    """Playwright would not fail here — it would hand off and quietly produce a
    signed-out browser, which reads as an expired session."""
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / session.SINGLETON_LOCK).symlink_to("some-host-12345")
    monkeypatch.setattr(session, "PROFILE_DIR", profile)

    async def run():
        async with session._persistent_context(headless=True):
            pass

    with pytest.raises(session.ProfileBusy):
        asyncio.run(run())


def test_review_refuses_without_a_saved_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSION_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(session, "PROFILE_DIR", tmp_path / "no-profile")
    with pytest.raises(SessionExpired):
        asyncio.run(session.review("file:///runs/index.html"))


def test_review_opens_the_report_not_banzai24(monkeypatch, tmp_path):
    """It must not verify auth on the way in.

    `authenticated_on` asks by navigating to the auction site, so a check here
    put banzai24 on screen instead of the report that was asked for.
    """
    monkeypatch.setattr(session, "SESSION_PATH", tmp_path / "session.json")
    (tmp_path / "session.json").write_text("{}", encoding="utf-8")
    context = _FakeContext()
    monkeypatch.setattr(session, "_persistent_context", _fake_context(context))
    monkeypatch.setattr(session, "snapshot", _closing_snapshot(context))

    asyncio.run(session.review("file:///runs/index.html", poll_s=0))
    assert context.page.visited == ["file:///runs/index.html"]


def test_review_still_opens_when_the_session_looks_dead(monkeypatch, tmp_path):
    """Signing in inline is the normal path on this site, not an error.

    Failing here would close the browser on the one condition you can fix from
    inside it — and `fetch` already treats a signed-out window the same way.
    """
    monkeypatch.setattr(session, "SESSION_PATH", tmp_path / "session.json")
    (tmp_path / "session.json").write_text("{}", encoding="utf-8")
    context = _FakeContext()
    monkeypatch.setattr(session, "_persistent_context", _fake_context(context))
    monkeypatch.setattr(session, "authenticated_on", _async_returning(False))
    monkeypatch.setattr(session, "snapshot", _closing_snapshot(context))

    asyncio.run(session.review("file:///runs/index.html", poll_s=0))  # no raise
    assert context.page.visited == ["file:///runs/index.html"]


def test_review_opens_the_url_and_waits_for_the_window(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "SESSION_PATH", tmp_path / "session.json")
    (tmp_path / "session.json").write_text("{}", encoding="utf-8")
    context = _FakeContext()
    monkeypatch.setattr(session, "_persistent_context", _fake_context(context))

    polls = 0

    async def fake_snapshot(ctx):
        """Stands in for the human, who closes the window after three polls."""
        nonlocal polls
        polls += 1
        if polls == 3:
            ctx.pages.clear()
        return True

    monkeypatch.setattr(session, "snapshot", fake_snapshot)
    asyncio.run(session.review("file:///runs/index.html", poll_s=0))

    assert context.page.visited == ["file:///runs/index.html"]
    assert polls == 3


def test_review_snapshots_while_the_window_is_open(monkeypatch, tmp_path):
    """banzai24 rotates its token per browser lifetime, so an hour of reviewing
    should leave the saved session fresher than it found it, as `login` does."""
    monkeypatch.setattr(session, "SESSION_PATH", tmp_path / "session.json")
    (tmp_path / "session.json").write_text("{}", encoding="utf-8")
    context = _FakeContext()
    monkeypatch.setattr(session, "_persistent_context", _fake_context(context))

    snapshotted = []

    async def fake_snapshot(ctx):
        snapshotted.append(ctx)
        ctx.pages.clear()
        return True

    monkeypatch.setattr(session, "snapshot", fake_snapshot)
    asyncio.run(session.review("file:///runs/index.html", poll_s=0))
    assert snapshotted == [context]
