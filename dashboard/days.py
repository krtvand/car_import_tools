"""Which cars were on offer on which day, for one saved search.

The unit these pages are built from is a **day** — a trade date — and never a
run. A run is a step in the morning: you fetch, and what you get back is
"Saturday's Harriers". Fetch twice for the same Saturday and there is still one
Saturday, so the newer fetch simply wins; fetch with ``--all-days`` and one run
carries three days, which is three blocks. Nothing downstream of this module
names a run. See ``docs/adr/0012-the-dashboard-is-per-search.md``.

**Lots are assigned to a day by the lot's own trade date, not the run's.** The
run-level ``trade_date`` says which day the fetch narrowed to and is ``null``
when it narrowed to none — but the lots themselves always know their own day,
which is what makes an ``--all-days`` run split correctly.

A day with no lots is still a day. A fetch that ran, found cars, and dropped
every one of them against the search's ``[api]`` requirements is not the same
news as a quiet market, so the day survives with its drop count and says so.
What is *not* invented is a day nobody fetched: no auction calendar is stored,
so an unfetched day and a day with no auction are the same absence here.

Reading ``lots.json`` is cheap; building a :class:`~banzai24.report.Report` is
not — it base64s every sheet and photograph in the run. So the scan is eager and
cached, and the report behind a day is built only when a page asks for it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

RUNS_DIR = Path(__file__).parent.parent / "runs"

# How far back the past page looks. Seven days is the operator's own window:
# long enough that a Monday morning still shows last Tuesday's cars, short
# enough that the page stays a page. Older runs stay on disk and in the
# database, reachable from nothing.
PAST_WINDOW_DAYS = 7

# The directory names fetch writes: `YYYY-MM-DD_HHMMSS_MAKE-MODEL`.
_STAMP_FORMAT = "%Y-%m-%d_%H%M%S"


@dataclass(frozen=True)
class Day:
    """One trade date's lots, from the newest run that covered it.

    ``date`` is ``None`` in exactly one case: a fetch that looked and found no
    upcoming day at all. That is a fact worth printing — the site had nothing
    ahead for this search — and it is not the same as never having fetched.
    """

    date: date | None
    run_dir: Path
    lot_numbers: tuple[str, ...] = ()
    dropped: int = 0               # on this day, rejected by the [api] rules

    @property
    def count(self) -> int:
        return len(self.lot_numbers)

    @property
    def heading(self) -> str:
        """What the block says about itself — the day, and what came of it.

        The operator's one requirement for this line: it must be clear which day
        these lots are for, *or were not found for*. So the date leads, and the
        three outcomes differ after the separator rather than in how the day is
        written.
        """
        if self.date is None:
            return "no upcoming day found"
        when = self.date.strftime("%a %-d %b")
        if self.count:
            return f"{when} · {self.count} lot{'' if self.count == 1 else 's'}"
        if self.dropped:
            return (f"{when} · no lots met the requirements — "
                    f"{self.dropped} dropped")
        return f"{when} · no lots"

    def report(self):
        """This day's lots as a :class:`~banzai24.report.Report`, or ``None``.

        ``None`` for a dateless day: there is nothing to render but the heading,
        and building a report to say so would read the database and base64 a
        run's worth of sheets for it.

        Built here rather than in the scan because it is the expensive half — it
        reads the database and inlines every sheet and photograph as a data URI —
        and the index wants counts and no cards at all, so it never pays for it.
        """
        from dataclasses import replace

        if self.date is None:
            return None
        full = _collect(self.run_dir)
        wanted = set(self.lot_numbers)
        return replace(full, views=[view for view in full.views
                                    if view.lot.lot_number in wanted])


@dataclass(frozen=True)
class _Run:
    """One ``lots.json``, read for the four facts the pages need from it."""

    run_dir: Path
    started_at: datetime | None
    search: str
    run_day: date | None           # the day the fetch narrowed to, if it did
    dropped: int
    days: dict[date, tuple[str, ...]]   # trade date -> the lots kept on it

    @property
    def sort_key(self) -> tuple:
        """Ordered by the stamp in the *name*, never by mtime.

        ``extract`` and ``report`` both write into an existing run directory, so
        an mtime sort would float last week's runs above this morning's the
        moment you re-rendered one.
        """
        return (self.started_at or datetime.min, self.run_dir.name)


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _parse_stamp(name: str) -> datetime | None:
    date_part, _, rest = name.partition("_")
    time_part, _, _car = rest.partition("_")
    try:
        return datetime.strptime(f"{date_part}_{time_part}", _STAMP_FORMAT)
    except ValueError:
        return None


def _lot_days(payload: dict, kept: set[str]) -> dict[date, tuple[str, ...]]:
    """The kept lots grouped by their own trade date.

    Walks the untouched API pages the run recorded, so this is the site's own
    ``tradeDate`` for each lot rather than the run's summary of it. A lot the
    fetch kept but whose day will not parse is dropped from the grouping: it
    would otherwise have to be filed under a day we would be making up.
    """
    from banzai24 import fetch

    grouped: dict[date, list[str]] = {}
    for page in payload.get("pages") or []:
        for item in page.get("items") or []:
            number = (item.get("lot") or {}).get("number")
            if number not in kept:
                continue
            day = _parse_date(fetch.trade_date(item))
            if day is None:
                continue
            numbers = grouped.setdefault(day, [])
            if number not in numbers:      # a lot can appear on two pages
                numbers.append(number)
    return {day: tuple(numbers) for day, numbers in grouped.items()}


def _read(run_dir: Path) -> _Run | None:
    """One run directory, or ``None`` if it is not a run or will not parse."""
    path = run_dir / "lots.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None            # the pre-search format; nothing here can read it

    name = ((payload.get("search") or {}).get("name") or "").strip()
    if not name:
        return None
    kept = {number for number in (payload.get("lots_selected") or []) if number}
    run_day = _parse_date(payload.get("trade_date"))
    days = _lot_days(payload, kept)
    # A fetch that kept nothing still covered the day it narrowed to, and that
    # day has no lots to be derived from. Without this the most informative
    # block on the page — "31 cars, none of them yours" — would be the one that
    # silently disappeared.
    if run_day is not None and run_day not in days:
        days[run_day] = ()
    return _Run(
        run_dir=run_dir,
        started_at=_parse_stamp(run_dir.name),
        search=name,
        run_day=run_day,
        dropped=int(payload.get("lots_filtered_out") or 0),
        days=days,
    )


@lru_cache(maxsize=8)
def _scan(root: Path) -> tuple[_Run, ...]:
    """Every run under ``root``, oldest first. Cached for the life of the process.

    One ``dashboard build`` writes seventeen pages from the same eighty-odd
    ``lots.json`` files; re-reading them once per page would be the slowest thing
    the build does. Nothing writes a run while the build runs, so a cache that
    lives as long as the process is safe — and a long-lived process that wants
    fresh runs calls :func:`forget`.
    """
    if not root.exists():
        return ()
    runs = (_read(d) for d in sorted(root.glob("*")) if d.is_dir())
    return tuple(sorted((run for run in runs if run), key=lambda run: run.sort_key))


def forget() -> None:
    """Drop the scan cache. For tests, and for anything long-lived."""
    _scan.cache_clear()
    _collect.cache_clear()


# Small on purpose: a cached Report holds every sheet and photograph in the run
# as base64, so this is megabytes apiece. It exists for the one case that
# repeats — an `--all-days` run whose days are rendered one after another — not
# as a store.
@lru_cache(maxsize=4)
def _collect(run_dir: Path):
    """Imported inside the function: :mod:`banzai24.report` pulls in the pricers,
    the cost book and the model specs, and a module that reads directory names
    should not drag those in to be imported."""
    from banzai24 import report as report_mod

    return report_mod.collect(run_dir)


def _runs_for(name: str, root: Path | None) -> list[_Run]:
    return [run for run in _scan(root or RUNS_DIR) if run.search == name]


def _days(runs: list[_Run]) -> list[Day]:
    """One :class:`Day` per trade date, from the newest run that covered it.

    Runs arrive oldest first, so a later run simply overwrites an earlier one's
    entry for the same day: re-fetching Saturday because you widened a band
    replaces Saturday, and the superseded lots are never shown beside the ones
    that replaced them.
    """
    by_day: dict[date, Day] = {}
    for run in runs:
        for day, numbers in run.days.items():
            by_day[day] = Day(date=day, run_dir=run.run_dir, lot_numbers=numbers,
                              dropped=run.dropped if day == run.run_day else 0)
    return list(by_day.values())


def ever_fetched(name: str, root: Path | None = None) -> bool:
    """Has this search ever produced a run? Distinct from having no lots today."""
    return bool(_runs_for(name, root))


def upcoming(name: str, root: Path | None = None,
             today: date | None = None) -> list[Day]:
    """Days still ahead of us, nearest first.

    Normally exactly one: ``fetch`` narrows to the nearest upcoming trade date
    unless it is given ``--all-days``.

    Empty means *nothing to bid on*, and the page says so with the command that
    would fix it. The one exception is a fetch that ran and found no upcoming
    day at all — the site had nothing ahead for this search — which comes back
    as a single dateless :class:`Day` so that the page can print that rather
    than the empty state, which would read as "you forgot to fetch".
    """
    today = today or date.today()
    runs = _runs_for(name, root)
    days = sorted((day for day in _days(runs) if day.date >= today),
                  key=lambda day: day.date)
    if days:
        return days
    if runs and runs[-1].run_day is None:
        return [Day(date=None, run_dir=runs[-1].run_dir)]
    return []


def past(name: str, root: Path | None = None, today: date | None = None,
         window: int = PAST_WINDOW_DAYS) -> list[Day]:
    """Days already traded, inside the window, newest first.

    Nothing older than the window is returned — not as a link, not as a count.
    The runs are still on disk and the lots are still in ``auction.db``; they
    have simply stopped competing for space on a page read every morning.
    """
    today = today or date.today()
    first = today.toordinal() - window
    return sorted(
        (day for day in _days(_runs_for(name, root))
         if day.date < today and day.date.toordinal() >= first),
        key=lambda day: day.date, reverse=True)
