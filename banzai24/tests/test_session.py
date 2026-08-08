"""The logged-out detection that keeps a dead session from looking like a bug."""
from __future__ import annotations

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
