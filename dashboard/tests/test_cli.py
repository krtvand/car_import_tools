"""``dashboard build`` — what it writes, what it refuses to delete, and what it
says in the terminal.

The pages themselves are tested next door. What is tested here is the wiring:
one page per enabled search, nothing for a disabled one, a note rather than a
crash for a broken one, and no file ever removed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import searches
from dashboard import cli, competitors, statistics


def _definition(enabled: bool = True):
    return SimpleNamespace(dashboard=SimpleNamespace(enabled=enabled))


@pytest.fixture(autouse=True)
def _no_databases(monkeypatch):
    """The panels read two databases and today's exchange rate; the wiring does
    not care what they say."""
    monkeypatch.setattr(cli.competitors, "build",
                        lambda *a, **kw: competitors.Dashboard())
    monkeypatch.setattr(cli.statistics, "build",
                        lambda *a, **kw: statistics.Statistics())


@pytest.fixture
def searches_on_disk(monkeypatch):
    def use(*entries):
        monkeypatch.setattr(searches, "load_all", lambda *a, **kw: list(entries))
    return use


def test_build_writes_an_index_and_two_pages_per_search(tmp_path, searches_on_disk):
    searches_on_disk(("toyota-harrier-z", _definition(), None),
                     ("mazda-3", _definition(), None))
    result = cli.build(tmp_path)

    assert result.listing == tmp_path / "index.html"
    assert [name for name, _, _ in result.pages] == ["mazda-3", "toyota-harrier-z"]
    for name in ("mazda-3", "toyota-harrier-z"):
        assert (tmp_path / "searches" / name / "index.html").exists()
        assert (tmp_path / "searches" / name / "past.html").exists()


def test_the_pages_that_panelled_every_search_are_not_written(tmp_path, searches_on_disk):
    """Their content is a fold on each search page now."""
    searches_on_disk(("mazda-3", _definition(), None))
    cli.build(tmp_path)
    assert not (tmp_path / "competitors.html").exists()
    assert not (tmp_path / "auction_statistics.html").exists()


def test_build_deletes_nothing(tmp_path, searches_on_disk):
    """A page for a search since switched off is unreachable, not removed.

    Everything here is a full rewrite, and a build that removed files would be a
    sharper tool than this needs.
    """
    orphan = tmp_path / "searches" / "toyota-rav4-x" / "index.html"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("last week's page", encoding="utf-8")
    stale = tmp_path / "competitors.html"
    stale.write_text("the old eight-panel page", encoding="utf-8")

    searches_on_disk(("mazda-3", _definition(), None))
    cli.build(tmp_path)

    assert orphan.read_text(encoding="utf-8") == "last week's page"
    assert stale.read_text(encoding="utf-8") == "the old eight-panel page"


def test_a_disabled_search_gets_no_page(tmp_path, searches_on_disk):
    searches_on_disk(("mazda-cx5", _definition(enabled=False), None),
                     ("mazda-3", _definition(), None))
    result = cli.build(tmp_path)
    assert [name for name, _, _ in result.pages] == ["mazda-3"]
    assert not (tmp_path / "searches" / "mazda-cx5").exists()


def test_a_broken_search_is_a_note_not_a_crash(tmp_path, searches_on_disk):
    searches_on_disk(("toyota-rav4-g", None, "[band] mileage_end must be an integer"),
                     ("mazda-3", _definition(), None))
    result = cli.build(tmp_path)
    assert [name for name, _, _ in result.pages] == ["mazda-3"]
    assert any("will not load" in note for note in result.notes)
    # …and it is still on the index, where it is red.
    assert "toyota-rav4-g" in (tmp_path / "index.html").read_text(encoding="utf-8")


def test_a_stale_cost_book_is_said_in_the_terminal_too(tmp_path, searches_on_disk,
                                                       monkeypatch):
    """The difference between today's numbers and last week's, where you typed."""
    monkeypatch.setattr(cli.competitors, "build", lambda *a, **kw: competitors.Dashboard(
        money_problem="no rates for today — using 2026-09-01"))
    searches_on_disk(("mazda-3", _definition(), None))
    assert "no rates for today — using 2026-09-01" in cli.build(tmp_path).notes


def test_a_search_the_crawl_does_not_cover_is_said_too(tmp_path, searches_on_disk,
                                                       monkeypatch):
    monkeypatch.setattr(cli.competitors, "build", lambda *a, **kw: competitors.Dashboard(
        panels=(competitors.SearchPanel(name="mazda-3",
                                        coverage="no completed crawl for MAZDA 3"),)))
    searches_on_disk(("mazda-3", _definition(), None))
    notes = cli.build(tmp_path).notes
    assert any("no completed crawl" in note for note in notes)


# --- the terminal line --------------------------------------------------------


def _build(**kw) -> cli.Build:
    from pathlib import Path

    return cli.Build(listing=Path("runs/index.html"), **kw)


def test_the_line_carries_what_is_waiting():
    from dashboard import index

    rows = [index.Row(name="toyota-harrier-z", lots=3, upcoming=True)]
    assert cli._line("toyota-harrier-z", rows, _build()).split() == [
        "toyota-harrier-z", "3", "lots"]


def test_the_line_carries_a_gap_but_not_a_count():
    """A search nobody priced is a file to go and edit. "63 competing adverts"
    is a market to read, and it belongs on the page, not in the terminal."""
    from dashboard import index

    rows = [index.Row(name="mazda-3", lots=0, upcoming=False)]
    unpriced = competitors.SearchPanel(name="mazda-3", bands=())
    priced = competitors.SearchPanel(name="mazda-3", bands=(competitors.BandPanel(
        band=SimpleNamespace(label="2023"), max_bid_jpy=1, sell_price_eur=3.0),))

    with_gap = cli._line("mazda-3", rows,
                         _build(dashboard=competitors.Dashboard(panels=(unpriced,))))
    without = cli._line("mazda-3", rows,
                        _build(dashboard=competitors.Dashboard(panels=(priced,))))
    assert with_gap.endswith("no upcoming lots · not priced yet")
    assert without.endswith("no upcoming lots")
