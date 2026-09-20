"""The index: every saved search, and nothing else.

A table of contents. One row per search the dashboard is enabled for, sorted by
file name, saying how many lots are waiting on the days ahead — and that is the
whole page. No competitor counts, no benchmark counts, no list of runs.

Numbers that change weekly on a page read daily are numbers you stop seeing, and
a list that reorders itself each morning is one whose labels you stop reading.
Everything that used to be here in summary is now on the page it describes, a
click away. See ``docs/adr/0012-the-dashboard-is-per-search.md``.

The one piece of bad news it keeps is a search whose file will not parse: it gets
a row saying so, because a missing row is indistinguishable from a search
switched off on purpose — the same instinct that dimmed an unreported run rather
than hiding it, back when this page listed runs.

Labelled by **file name**, which is the string typed at ``--search``, sorts the
three Harriers together, and cannot be derived wrongly from a file that stops
following the convention.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

import searches

from . import days, search_page

TEMPLATE_DIR = Path(__file__).parent / "templates"

# Re-exported: several modules have wanted "where the runs are" and only one of
# them should own the answer.
RUNS_DIR = days.RUNS_DIR


@dataclass(frozen=True)
class Row:
    """One search on the index."""

    name: str
    lots: int | None = None        # None: never fetched
    upcoming: bool = False         # a day ahead of us was fetched
    problem: str | None = None     # the file will not parse

    @property
    def href(self) -> str:
        """Relative, so the set survives ``runs/`` being copied elsewhere."""
        return f"{search_page.PAGES_DIR}/{self.name}/{search_page.PAGE_FILENAME}"

    @property
    def summary(self) -> str:
        """Only ever about lots waiting — the one number that changes daily.

        Three absences, said apart: a file that will not load, a search nobody
        has ever fetched, and a search fetched with nothing ahead of it. The
        middle one is a thing you forgot; the last is a quiet week.
        """
        if self.problem:
            return f"file will not load: {self.problem}"
        if self.lots is None:
            return "never fetched"
        if not self.upcoming:
            return "no upcoming lots"
        return f"{self.lots} lot{'' if self.lots == 1 else 's'}"


def rows(root: Path | None = None, today=None) -> list[Row]:
    """Every enabled search, alphabetically by file name.

    A disabled search is simply absent — that is what switching it off is for —
    while a broken one is present and red, because nobody chose that.
    """
    out = []
    for name, definition, problem in sorted(searches.load_all(),
                                            key=lambda item: item[0]):
        if problem:
            out.append(Row(name=name, problem=problem))
            continue
        if not definition.dashboard.enabled:
            continue
        upcoming = days.upcoming(name, root, today=today)
        out.append(Row(
            name=name,
            lots=sum(day.count for day in upcoming) if days.ever_fetched(name, root) else None,
            upcoming=bool(upcoming),
        ))
    return out


def render(entries: list[Row], generated_at: datetime | None = None) -> str:
    """The whole page as one string. No file written, so this is testable."""
    # Same autoescape reasoning as report.py: the loader keys on ".j2", so
    # `select_autoescape` would see no ".html" and quietly leave escaping off —
    # and a parser error carries whatever somebody typed into a TOML file.
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("index.html.j2").render(
        entries=entries,
        generated_at=(generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M"),
    )


def write(root: Path | None = None, output: Path | None = None,
          today=None) -> Path:
    """(Re)write ``runs/index.html`` and return where it went.

    Always a full rewrite. The page costs a directory scan to build, so the only
    real failure mode is staleness, and rebuilding it every time removes it.
    """
    root = root or RUNS_DIR
    output = output or root / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(rows(root, today=today)), encoding="utf-8")
    return output
