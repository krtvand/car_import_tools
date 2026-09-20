"""One saved search, whole, on one page — the page the morning actually starts on.

The lots you can still bid on at the top at full size, the weekly reading folded
away under them, and the file that decides all of it at the bottom. Assembled
from four sources that already exist and are not rebuilt here: :mod:`days` for
the trade dates, :mod:`competitors` and :mod:`statistics` for their panels, and
the search's own TOML read off disk.

Only the *upcoming* days are rendered here. The last seven days live on
``past.html`` beside it — same day blocks, same cards, and nothing older than
that anywhere in the UI. See ``docs/adr/0012-the-dashboard-is-per-search.md``.

Every page writes to ``runs/searches/<name>/index.html``, two directories below
``runs/``, so the links back into run directories carry the ``../../`` that the
relative-link promise costs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape

from . import competitors, days, statistics

TEMPLATE_DIR = Path(__file__).parent / "templates"

# Where a search's pages go, under the runs directory. A folder per search
# rather than `toyota-harrier-z.html` beside the runs: it is the only layout
# where the eventual published URL is a name a person would type, and it keeps
# `index.html` and `past.html` together.
PAGES_DIR = "searches"
PAGE_FILENAME = "index.html"
PAST_FILENAME = "past.html"


def _backticks(text: str) -> Markup:
    """`like this` → <code>like this</code>, escaping everything else first.

    The panels' notes are written as prose with commands in them, and backticks
    are the readable form in a terminal, which is the other place they are
    printed. Marking the result safe is only sound because :func:`escape` has
    already run over the whole string — the substitution introduces the only
    tags in it.
    """
    out, tag = [], False
    for part in escape(text).split("`"):
        out.append(f"<code>{part}</code>" if tag else part)
        tag = not tag
    return Markup("".join(out))


@dataclass(frozen=True)
class SearchPage:
    """Everything one search page prints, gathered before anything is rendered."""

    name: str
    car: object | None = None
    # (day, report) — the report is None for a day with nothing to render but
    # its own heading, which is a fetch that found no upcoming day at all.
    upcoming: tuple[tuple[days.Day, object | None], ...] = ()
    competitors: competitors.SearchPanel | None = None
    statistics: statistics.SearchPanel | None = None
    competitors_summary: str = ""
    statistics_summary: str = ""
    toml_text: str = ""
    past_window: int = days.PAST_WINDOW_DAYS

    @property
    def lot_count(self) -> int:
        """Every kept lot on the days ahead — the number the index row repeats.

        Deliberately every lot and not the biddable ones: an *unconfirmed* lot is
        one you resolve by opening the sheet and a failing one is one you
        re-judge when you loosen a requirement, so which of them is a bid is the
        operator's call, not this number's.
        """
        return sum(day.count for day, _ in self.upcoming)


@dataclass(frozen=True)
class PastPage:
    """The same search's last seven days of lots, grouped by day, newest first.

    A page of its own rather than a block on the search page, because the two
    answer different questions and must never be mistaken for one another: one
    is what you can still bid on, the other what was on offer and what became of
    it. Nothing older than the window is on it, and nothing on it links to
    anything older.
    """

    name: str
    car: object | None = None
    past: tuple[tuple[days.Day, object | None], ...] = ()
    window: int = days.PAST_WINDOW_DAYS


def _car_for(name: str,
             dashboard: competitors.Dashboard | None,
             stats: statistics.Statistics | None) -> object | None:
    """The car as the panels name it — "Toyota Harrier" rather than the file stem.

    Both panels carry it and either may be missing, so this asks each in turn
    and falls back to nothing: the pages print the file name when there is no
    car, which is the string you type at ``--search`` anyway.
    """
    for source in (dashboard, stats):
        for panel in (source.panels if source else ()):
            if panel.name == name and getattr(panel, "car", None):
                return panel.car
    return None


def _toml_text(panel: competitors.SearchPanel | None,
               statistics_panel: statistics.SearchPanel | None,
               name: str) -> str:
    """The search file itself, verbatim.

    Read from the path the panels recorded rather than rebuilt from the parsed
    definition: the comments in that file are half of what it says, and a
    re-serialised copy would drop every one of them.
    """
    for candidate in (panel, statistics_panel):
        source = getattr(candidate, "source", None)
        if source:
            try:
                return Path(source).read_text(encoding="utf-8")
            except OSError:
                break
    return f"{name}.toml could not be read."


def collect(name: str,
            dashboard: competitors.Dashboard | None = None,
            stats: statistics.Statistics | None = None,
            root: Path | None = None,
            today=None) -> SearchPage:
    """Gather one page. Cheap except for the day reports, which are the cards."""
    competitors_panel = next(
        (panel for panel in (dashboard.panels if dashboard else ()) if panel.name == name),
        None)
    statistics_panel = next(
        (panel for panel in (stats.panels if stats else ()) if panel.name == name),
        None)
    upcoming = tuple((day, day.report())
                     for day in days.upcoming(name, root, today=today))
    car = _car_for(name, dashboard, stats)
    return SearchPage(
        name=name,
        car=car,
        upcoming=upcoming,
        competitors=competitors_panel,
        statistics=statistics_panel,
        competitors_summary=(competitors.panel_summary(competitors_panel)
                             if competitors_panel else "not on the dashboard"),
        statistics_summary=(statistics.panel_summary(statistics_panel)
                            if statistics_panel else "never measured"),
        toml_text=_toml_text(competitors_panel, statistics_panel, name),
    )


def _environment() -> Environment:
    """The report's environment, pointed at both template directories.

    The cards come from ``banzai24/templates`` and the panels from this one, and
    the card macros need the report's own filters — ``yen``, ``km``, ``check``,
    ``verdict`` — so the environment is borrowed rather than rebuilt. A second
    environment would be a second place to forget a filter.
    """
    from banzai24 import report as report_mod

    env = report_mod._environment()
    env.loader = FileSystemLoader([str(TEMPLATE_DIR), str(report_mod.TEMPLATE_DIR)])
    env.filters["backticks"] = _backticks
    return env


def render(page: SearchPage, generated_at: datetime | None = None) -> str:
    """The whole page as one string. No file written, so this is testable."""
    from banzai24 import sheets

    return _environment().get_template("search.html.j2").render(
        page=page,
        damage_codes=sheets.DAMAGE_CODES,
        generated_at=(generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M"),
    )


def write(page: SearchPage, runs_dir: Path | None = None) -> Path:
    """Write ``runs/searches/<name>/index.html`` and return where it went."""
    runs_dir = runs_dir or days.RUNS_DIR
    output = runs_dir / PAGES_DIR / page.name / PAGE_FILENAME
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(page), encoding="utf-8")
    return output


def collect_past(name: str,
                 dashboard: competitors.Dashboard | None = None,
                 stats: statistics.Statistics | None = None,
                 root: Path | None = None,
                 today=None,
                 window: int = days.PAST_WINDOW_DAYS) -> PastPage:
    """Gather the past page. The reports are the expensive part — up to seven
    runs' worth of sheets and photographs, all inlined."""
    car = _car_for(name, dashboard, stats)
    past = tuple((day, day.report())
                 for day in days.past(name, root, today=today, window=window))
    return PastPage(name=name, car=car, past=past, window=window)


def render_past(page: PastPage, generated_at: datetime | None = None) -> str:
    """The whole page as one string. No file written, so this is testable."""
    from banzai24 import sheets

    return _environment().get_template("past.html.j2").render(
        page=page,
        damage_codes=sheets.DAMAGE_CODES,
        generated_at=(generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M"),
    )


def write_past(page: PastPage, runs_dir: Path | None = None) -> Path:
    """Write ``runs/searches/<name>/past.html`` and return where it went."""
    runs_dir = runs_dir or days.RUNS_DIR
    output = runs_dir / PAGES_DIR / page.name / PAST_FILENAME
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_past(page), encoding="utf-8")
    return output
