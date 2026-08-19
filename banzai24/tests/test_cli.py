"""CLI wiring — mostly that a saved search is the only way to name a car.

The filter-resolution tests this file used to hold are gone with the machinery
they guarded: ``--no-defaults``, ``NEUTRAL_FILTERS`` and ``DEFAULT_FILTERS``
existed so a saved search could not inherit another car's filters, and a search
is now a complete file with nothing to inherit from. What is worth guarding
instead is that a run cannot happen without naming one.
"""
from __future__ import annotations

import argparse

import pytest

from banzai24 import cli, search


def test_fetch_requires_a_named_search():
    """A bare ``fetch`` used to run whatever DEFAULT_FILTERS happened to hold —
    a CX-30 search, for anyone who typed ``fetch`` meaning "the thing I set up".
    """
    with pytest.raises(SystemExit):
        cli._build_parser().parse_args(["fetch"])


def test_fetch_takes_the_search_by_name():
    args = cli._build_parser().parse_args(["fetch", "--search", "mazda-cx30"])
    assert args.search == "mazda-cx30"


def test_there_are_no_per_filter_flags_left():
    """Each of these would now be a second place a car is declared.

    Left as a test rather than a comment because re-adding one is an easy,
    reasonable-looking change that quietly reintroduces the split-brain the
    .toml files exist to close.
    """
    parser = cli._build_parser()
    for flag in ("--make", "--model", "--year-start", "--grade",
                 "--body-model-code", "--no-defaults"):
        with pytest.raises(SystemExit):
            parser.parse_args(["fetch", "--search", "mazda-cx30", flag, "X"])


def test_source_survives_as_the_one_override():
    """Completed sales is a question asked *of* a saved search, not another one."""
    args = cli._build_parser().parse_args(
        ["fetch", "--search", "mazda-cx30", "--source", "archive"])
    assert args.source == "archive"


def test_an_unknown_search_name_lists_the_ones_that_exist():
    with pytest.raises(SystemExit) as exc:
        cli._load_search("mazda-cx31")
    assert "mazda-cx30" in str(exc.value)


def test_every_shipped_search_loads():
    """A definition is only read when it is run, so a typo in one would otherwise
    surface on the morning you needed it."""
    names = search.available()
    assert names, "no saved searches found"
    for name in names:
        assert search.load(name).filters.make


def test_the_flags_the_daily_script_recommends_actually_exist():
    """``searches/daily.sh`` ends by printing the commands for the rest of the
    morning. A renamed or removed flag would make that advice wrong in the one
    place nothing else looks — a shell script's here-doc.

    argparse exits non-zero on an unknown flag, so parsing is the assertion.
    """
    parser = cli._build_parser()

    parser.parse_args(["check"])
    parser.parse_args(["fetch", "--search", "mazda-cx30"])
    parser.parse_args(["extract", "--today"])
    parser.parse_args(["extract", "--today", "--dry-run"])
    parser.parse_args(["extract", "--today", "--limit", "5"])
    parser.parse_args(["extract", "runs/2026-08-09_222421_TOYOTA-RAV4"])
    parser.parse_args(["report", "--today", "--open"])


def test_extract_and_report_both_understand_today():
    """The two commands scope by day the same way, so the daily flow reads the
    sheets it fetched this morning and re-renders the reports for those runs."""
    parser = cli._build_parser()
    assert parser.parse_args(["extract", "--today"]).today is True
    assert parser.parse_args(["report", "--today"]).today is True
    assert parser.parse_args(["extract"]).today is False
    assert parser.parse_args(["report"]).today is False


# --- `report --open` -------------------------------------------------------

def _report_run(monkeypatch, tmp_path, argv, reviewer=None):
    """Run `report ...` against a throwaway runs/ and record what it opened."""
    from banzai24 import db, index, session

    run_dir = tmp_path / "2026-08-17_222903_TOYOTA-RAV4"
    (run_dir / "sheets").mkdir(parents=True)
    (run_dir / "lots.json").write_text("[]", encoding="utf-8")

    opened: list[str] = []

    async def fake_review(url, **kwargs):
        opened.append(url)
        if reviewer is not None:
            reviewer()

    monkeypatch.setattr(index, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(db, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(session, "review", fake_review)

    # The report itself is exercised in test_report.py; here only the opening is.
    class Built:
        output = run_dir / "report.html"
        missing: list = []
        cyprus_reason = None
        bid_reason = None
        quoted = 0

        def summary(self) -> str:
            return "1 lot"

    Built.output.write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr(
        "banzai24.report.run_report", lambda *a, **k: Built(), raising=False
    )
    monkeypatch.setattr("sys.argv", ["banzai24", *argv, str(run_dir)])
    cli.main()
    return opened


def test_open_opens_the_index_not_the_report(monkeypatch, tmp_path):
    """The report is one run; the index is the last ten. `--open` means index."""
    opened = _report_run(monkeypatch, tmp_path, ["report", "--open"])
    assert len(opened) == 1
    assert opened[0].endswith("/index.html")


def test_open_goes_through_the_signed_in_profile(monkeypatch, tmp_path):
    """banzai24 limits authenticated clients and the report links back to it, so
    the default browser is the wrong one to click through from — and only
    session.review replays the cookies that sign a launch in."""
    import webbrowser

    calls = []
    monkeypatch.setattr(webbrowser, "open", lambda url: calls.append(url))
    _report_run(monkeypatch, tmp_path, ["report", "--open"])
    assert calls == []


def test_no_saved_profile_is_reported_rather_than_crashing(monkeypatch, tmp_path):
    """The only fatal auth case left. A session that merely looks dead is not
    fatal — you sign in in the window review() opened."""
    from banzai24 import session

    def missing():
        raise session.SessionExpired("No saved profile.")

    with pytest.raises(SystemExit) as exc:
        _report_run(monkeypatch, tmp_path, ["report", "--open"], reviewer=missing)
    assert "banzai24 login" in str(exc.value)


def test_a_busy_profile_is_reported_without_sending_you_to_login(monkeypatch, tmp_path):
    """A second `report --open` while one is already up must not read as an
    expired session — that costs an SMS and fixes nothing."""
    from banzai24 import session

    def busy():
        raise session.ProfileBusy()

    with pytest.raises(SystemExit) as exc:
        _report_run(monkeypatch, tmp_path, ["report", "--open"], reviewer=busy)
    assert "already open" in str(exc.value)
    assert "banzai24 login" not in str(exc.value)


def test_the_index_is_rebuilt_even_without_open(monkeypatch, tmp_path):
    """Derived from directory names and free to build, so it never goes stale."""
    opened = _report_run(monkeypatch, tmp_path, ["report"])
    assert (tmp_path / "index.html").exists()
    assert opened == []
