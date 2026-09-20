"""The index — a table of contents, and the three absences it must keep apart.

What this page must never do is go quiet about a search. A missing row reads as
"switched off on purpose" whatever the real reason was, so a file that will not
parse stays on the page, and a search nobody has fetched says that rather than
showing a zero.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from dashboard import index


def _definition(enabled: bool = True):
    return SimpleNamespace(dashboard=SimpleNamespace(enabled=enabled))


@pytest.fixture
def searches_on_disk(monkeypatch):
    """Control what `searches.load_all()` finds, without writing TOML files."""
    def use(*entries):
        monkeypatch.setattr(index.searches, "load_all", lambda *a, **kw: list(entries))
    return use


@pytest.fixture
def fetched(monkeypatch):
    """Control what `days` knows, without a runs directory."""
    def use(upcoming: dict[str, list[int]], ever: set[str] | None = None):
        ever = set(upcoming) if ever is None else ever
        monkeypatch.setattr(index.days, "upcoming", lambda name, *a, **kw: [
            SimpleNamespace(count=n) for n in upcoming.get(name, [])])
        monkeypatch.setattr(index.days, "ever_fetched", lambda name, *a, **kw: name in ever)
    return use


def test_rows_are_alphabetical_by_file_name(searches_on_disk, fetched):
    """The file name is what you type at `--search`, and it sorts the three
    Harriers together."""
    searches_on_disk(("toyota-harrier-z", _definition(), None),
                     ("mazda-3", _definition(), None),
                     ("toyota-harrier-g", _definition(), None))
    fetched({})
    assert [row.name for row in index.rows()] == [
        "mazda-3", "toyota-harrier-g", "toyota-harrier-z"]


def test_a_disabled_search_is_simply_absent(searches_on_disk, fetched):
    searches_on_disk(("mazda-cx5", _definition(enabled=False), None),
                     ("mazda-3", _definition(), None))
    fetched({})
    assert [row.name for row in index.rows()] == ["mazda-3"]


def test_a_broken_file_keeps_its_row_and_carries_the_parser_s_message(
        searches_on_disk, fetched):
    """The whole reason this page still has bad news on it."""
    searches_on_disk(("toyota-rav4-g", None, "[band] mileage_end must be an integer"))
    fetched({})
    row, = index.rows()
    assert row.problem == "[band] mileage_end must be an integer"
    assert row.summary == "file will not load: [band] mileage_end must be an integer"


def test_the_count_is_every_kept_lot_on_the_days_ahead(searches_on_disk, fetched):
    searches_on_disk(("toyota-harrier-z", _definition(), None))
    fetched({"toyota-harrier-z": [3, 2]})       # two upcoming days
    assert index.rows()[0].summary == "5 lots"


def test_one_lot_is_not_pluralised(searches_on_disk, fetched):
    searches_on_disk(("toyota-harrier-g", _definition(), None))
    fetched({"toyota-harrier-g": [1]})
    assert index.rows()[0].summary == "1 lot"


def test_a_quiet_week_and_a_morning_you_forgot_read_differently(
        searches_on_disk, fetched):
    """Fetched with nothing ahead is not the same news as never fetched."""
    searches_on_disk(("toyota-rav4-x", _definition(), None),
                     ("mazda-cx30", _definition(), None))
    fetched({}, ever={"toyota-rav4-x"})
    summaries = {row.name: row.summary for row in index.rows()}
    assert summaries == {"toyota-rav4-x": "no upcoming lots",
                         "mazda-cx30": "never fetched"}


def test_a_row_links_into_the_search_folder():
    assert index.Row(name="toyota-harrier-z").href == (
        "searches/toyota-harrier-z/index.html")


# --- rendering ----------------------------------------------------------------


def test_render_lists_every_row_with_its_link_and_summary():
    html = index.render([index.Row(name="toyota-harrier-z", lots=3, upcoming=True),
                         index.Row(name="mazda-3", lots=0, upcoming=False)])
    assert 'href="searches/toyota-harrier-z/index.html"' in html
    assert "3 lots" in html
    assert "no upcoming lots" in html


def test_render_marks_the_rows_with_nothing_waiting(): 
    """Readable at a glance down the whole list, not a word you have to find."""
    html = index.render([index.Row(name="mazda-3", lots=0, upcoming=False)])
    assert "search quiet" in html


def test_a_broken_row_is_loud_and_not_a_link():
    html = index.render([index.Row(name="toyota-rav4-g", problem="line 4: bad table")])
    assert "search broken" in html
    assert 'href="searches/toyota-rav4-g/' not in html


def test_render_escapes_a_parser_message():
    """The message carries whatever somebody typed into a TOML file."""
    html = index.render([index.Row(name="x", problem='<script>alert("hi")</script>')])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_is_self_contained():
    html = index.render([index.Row(name="mazda-3", lots=1, upcoming=True)])
    assert "http://" not in html and "https://" not in html
    assert "<script" not in html


def test_render_with_no_searches_says_where_they_live():
    assert "searches/" in index.render([])


def test_nothing_about_runs_or_the_weekly_panels_is_on_the_page():
    """Everything that used to be summarised here is now on the page it
    describes — including the two panel links this page used to carry."""
    html = index.render([index.Row(name="toyota-harrier-z", lots=3, upcoming=True)])
    for gone in ("competitors.html", "auction_statistics.html", "report.html",
                 "competing advert", "cheapest acceptable"):
        assert gone not in html


# --- writing ------------------------------------------------------------------


def test_write_lands_beside_the_runs(tmp_path, searches_on_disk, fetched):
    searches_on_disk(("mazda-3", _definition(), None))
    fetched({"mazda-3": [2]})
    path = index.write(tmp_path)
    assert path == tmp_path / "index.html"
    assert "2 lots" in path.read_text(encoding="utf-8")


def test_write_replaces_rather_than_appends(tmp_path, searches_on_disk, fetched):
    searches_on_disk(("mazda-3", _definition(), None))
    fetched({"mazda-3": [2]})
    index.write(tmp_path)
    first = (tmp_path / "index.html").read_text(encoding="utf-8")
    index.write(tmp_path)
    assert (tmp_path / "index.html").read_text(encoding="utf-8").count("<!doctype") == 1
    assert len(first) == len((tmp_path / "index.html").read_text(encoding="utf-8"))
