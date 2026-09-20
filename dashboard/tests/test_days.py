"""Days, not runs: what the search pages are built from.

Every case here is about the seam between the two — a day fetched twice, a run
carrying three days, a fetch that kept nothing, a fetch that found no day at all.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from dashboard import days


@pytest.fixture(autouse=True)
def _fresh():
    """The scan is cached for the life of a process; tests are not."""
    days.forget()
    yield
    days.forget()


def make_run(root: Path, name: str, search: str, *, trade_date: str | None,
             lots: dict[str, str] | None = None, dropped: int = 0,
             others: dict[str, str] | None = None) -> Path:
    """A run directory with a ``lots.json`` shaped like the real thing.

    ``lots`` is ``{lot number: trade date}`` for the lots the fetch kept;
    ``others`` is the same for lots that came back on the pages and were not
    kept, which is how a real run carries the days it set aside.
    """
    run = root / name
    run.mkdir(parents=True)
    items = [{"lot": {"number": number, "tradeDate": day}}
             for number, day in {**(lots or {}), **(others or {})}.items()]
    (run / "lots.json").write_text(json.dumps({
        "fetched_at": "2026-09-11T21:44:44+00:00",
        "search": {"name": search, "car": "toyota-harrier"},
        "trade_date": trade_date,
        "lots_selected": list(lots or {}),
        "lots_filtered_out": dropped,
        "lots_other_days": len(others or {}),
        "pages": [{"items": items}],
    }), encoding="utf-8")
    return run


def test_a_lot_is_filed_under_its_own_trade_date(tmp_path):
    """An `--all-days` run has no day of its own; its lots still know theirs."""
    make_run(tmp_path, "2026-09-11_234435_TOYOTA-HARRIER", "harrier",
             trade_date=None,
             lots={"a": "2026-09-12", "b": "2026-09-12", "c": "2026-09-14"})
    found = days.upcoming("harrier", tmp_path, today=date(2026, 9, 12))
    assert [(day.date, day.count) for day in found] == [
        (date(2026, 9, 12), 2),
        (date(2026, 9, 14), 1),
    ]


def test_the_newest_fetch_of_a_day_wins(tmp_path):
    """Re-fetching Saturday replaces Saturday — it never appears twice."""
    make_run(tmp_path, "2026-09-11_090000_TOYOTA-HARRIER", "harrier",
             trade_date="2026-09-12", lots={"old": "2026-09-12"})
    make_run(tmp_path, "2026-09-11_234435_TOYOTA-HARRIER", "harrier",
             trade_date="2026-09-12",
             lots={"new": "2026-09-12", "also-new": "2026-09-12"})
    found = days.upcoming("harrier", tmp_path, today=date(2026, 9, 12))
    assert len(found) == 1
    assert found[0].lot_numbers == ("new", "also-new")


def test_ordering_ignores_mtime(tmp_path):
    """Re-rendering an old run must not make it the winner for its day."""
    old = make_run(tmp_path, "2026-09-11_090000_TOYOTA-HARRIER", "harrier",
                   trade_date="2026-09-12", lots={"old": "2026-09-12"})
    make_run(tmp_path, "2026-09-11_234435_TOYOTA-HARRIER", "harrier",
             trade_date="2026-09-12", lots={"new": "2026-09-12"})
    (old / "report.html").write_text("touched", encoding="utf-8")  # newest mtime
    found = days.upcoming("harrier", tmp_path, today=date(2026, 9, 12))
    assert found[0].lot_numbers == ("new",)


def test_upcoming_is_nearest_first_and_past_is_newest_first(tmp_path):
    for stamp, day in (("2026-09-10_100000", "2026-09-11"),
                       ("2026-09-11_100000", "2026-09-12"),
                       ("2026-09-13_100000", "2026-09-14"),
                       ("2026-09-14_100000", "2026-09-15")):
        make_run(tmp_path, f"{stamp}_TOYOTA-HARRIER", "harrier",
                 trade_date=day, lots={f"lot-{day}": day})
    today = date(2026, 9, 13)
    assert [str(d.date) for d in days.upcoming("harrier", tmp_path, today=today)] == [
        "2026-09-14", "2026-09-15"]
    assert [str(d.date) for d in days.past("harrier", tmp_path, today=today)] == [
        "2026-09-12", "2026-09-11"]


def test_the_window_is_the_seven_days_before_today(tmp_path):
    """Seven days back is in; eight is gone from the UI entirely."""
    for day in ("2026-09-11", "2026-09-12", "2026-09-13", "2026-09-19"):
        make_run(tmp_path, f"2026-09-10_1000{day[-2:]}_TOYOTA-HARRIER", "harrier",
                 trade_date=day, lots={f"lot-{day}": day})
    found = days.past("harrier", tmp_path, today=date(2026, 9, 20))
    assert [str(d.date) for d in found] == ["2026-09-19", "2026-09-13"]


def test_a_day_that_kept_nothing_still_appears_with_its_drop_count(tmp_path):
    """"31 cars, none of them yours" is the most informative block on the page."""
    make_run(tmp_path, "2026-09-11_234435_TOYOTA-HARRIER", "harrier",
             trade_date="2026-09-12", lots={}, dropped=31)
    day, = days.upcoming("harrier", tmp_path, today=date(2026, 9, 12))
    assert day.count == 0
    assert day.heading == "Sat 12 Sep · no lots met the requirements — 31 dropped"


def test_headings_name_the_day_first(tmp_path):
    make_run(tmp_path, "2026-09-11_234435_TOYOTA-HARRIER", "harrier",
             trade_date="2026-09-12", lots={"a": "2026-09-12"}, dropped=20)
    day, = days.upcoming("harrier", tmp_path, today=date(2026, 9, 12))
    assert day.heading == "Sat 12 Sep · 1 lot"


def test_a_fetch_that_found_no_upcoming_day_says_so(tmp_path):
    """Not the same news as never having fetched, so not the same empty list."""
    make_run(tmp_path, "2026-09-11_234435_TOYOTA-HARRIER", "harrier",
             trade_date=None, lots={"sold": "2026-09-01"})
    found = days.upcoming("harrier", tmp_path, today=date(2026, 9, 20))
    assert len(found) == 1
    assert found[0].date is None
    assert found[0].heading == "no upcoming day found"
    assert found[0].report() is None


def test_a_search_never_fetched_has_no_days_and_no_runs(tmp_path):
    make_run(tmp_path, "2026-09-11_234435_TOYOTA-HARRIER", "harrier",
             trade_date="2026-09-12", lots={"a": "2026-09-12"})
    assert days.ever_fetched("harrier", tmp_path) is True
    assert days.ever_fetched("mazda-cx5", tmp_path) is False
    assert days.upcoming("mazda-cx5", tmp_path, today=date(2026, 9, 12)) == []
    assert days.past("mazda-cx5", tmp_path, today=date(2026, 9, 12)) == []


def test_runs_of_other_searches_are_not_this_search_s_days(tmp_path):
    """Three Harrier files fetch the same car; a day belongs to one of them."""
    make_run(tmp_path, "2026-09-11_234415_TOYOTA-HARRIER", "toyota-harrier-g",
             trade_date="2026-09-12", lots={"g": "2026-09-12"})
    make_run(tmp_path, "2026-09-11_234435_TOYOTA-HARRIER", "toyota-harrier-z",
             trade_date="2026-09-12", lots={"z1": "2026-09-12", "z2": "2026-09-12"})
    found = days.upcoming("toyota-harrier-z", tmp_path, today=date(2026, 9, 12))
    assert found[0].lot_numbers == ("z1", "z2")


def test_lots_set_aside_for_other_days_are_not_days(tmp_path):
    """A run's pages carry later days it did not keep. They are not blocks."""
    make_run(tmp_path, "2026-09-11_234435_TOYOTA-HARRIER", "harrier",
             trade_date="2026-09-12", lots={"kept": "2026-09-12"},
             others={"later": "2026-09-19", "earlier": "2026-09-01"})
    found = days.upcoming("harrier", tmp_path, today=date(2026, 9, 12))
    assert [str(d.date) for d in found] == ["2026-09-12"]


def test_a_directory_that_is_not_a_run_is_skipped(tmp_path):
    (tmp_path / "searches").mkdir()
    (tmp_path / "searches" / "index.html").write_text("page", encoding="utf-8")
    broken = tmp_path / "2026-09-11_000000_BROKEN"
    broken.mkdir()
    (broken / "lots.json").write_text("{not json", encoding="utf-8")
    make_run(tmp_path, "2026-09-11_234435_TOYOTA-HARRIER", "harrier",
             trade_date="2026-09-12", lots={"a": "2026-09-12"})
    assert len(days.upcoming("harrier", tmp_path, today=date(2026, 9, 12))) == 1
