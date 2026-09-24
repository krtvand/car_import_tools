"""``python -m dashboard`` — write the pages, and open them in the right browser.

``build`` writes ``runs/index.html`` and, for every enabled search, an
``index.html`` and a ``past.html`` under ``runs/searches/<name>/``. Seventeen
files today, always rewritten in full: the pages are derived from the runs, the
databases and the search files, so the only failure mode they have is staleness.

``open`` builds and then opens the index in **the parser's own Chrome**, not
your everyday browser. banzai24 caps how many authenticated clients you may have
at once and the cards link back to it, so opening a page anywhere else costs a
click that lands signed out and may unseat the session the parser needs. That
window lives only as long as this command; see :func:`banzai24.session.review`.

This module may import both parsers. Neither of them imports it — the pages
moved here precisely so that ``banzai24 report`` could go on promising to touch
no network and cost nothing, while these pages need two databases, a cost book
and today's exchange rate.

The two pages that panelled every search at once are gone: their content is on
the search pages now. Files an older build left in ``runs/`` are not deleted —
nothing here removes anything, and an orphan page is cheap to ignore. See
``docs/adr/0012-the-dashboard-is-per-search.md``.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import searches

from . import competitors, index, search_page, statistics


@dataclass(frozen=True)
class Build:
    """What one build wrote, and what it noticed while writing it."""

    listing: Path
    pages: tuple[tuple[str, Path, Path], ...] = ()   # name, search page, past page
    dashboard: competitors.Dashboard | None = None
    statistics: statistics.Statistics | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def build(runs_dir: Path | None = None) -> Build:
    """Write every page. Always a full rewrite; staleness is the only failure mode.

    The two panels are built once, for every search, and then handed out one at a
    time — they read the databases and today's money, and eight rebuilds of that
    to write eight pages would be the slowest thing here by an order of
    magnitude.
    """
    runs_dir = runs_dir or index.RUNS_DIR
    dashboard = competitors.build(runs_dir)
    # Today's money, fetched once by the panel above and handed on: a sale
    # landed at one rate beside a max bid priced at another would put a gap on
    # the page that looks like news about the car.
    stats = statistics.build(runs_dir, money=dashboard.money)

    runs_dir.mkdir(parents=True, exist_ok=True)
    pages, notes = [], []
    for name, definition, problem in sorted(searches.load_all(),
                                            key=lambda item: item[0]):
        if problem:
            # The index carries this one; it is repeated here because the person
            # who broke the file is standing at the terminal that broke it.
            notes.append(f"{name}: will not load — {problem}")
            continue
        if not definition.dashboard.enabled:
            continue
        page = search_page.collect(name, dashboard, stats, runs_dir)
        past = search_page.collect_past(name, dashboard, stats, runs_dir)
        pages.append((name, search_page.write(page, runs_dir),
                      search_page.write_past(past, runs_dir)))

    if dashboard.money_problem:
        notes.append(dashboard.money_problem)
    for panel in dashboard.panels:
        if panel.coverage:
            notes.append(f"{panel.name}: {panel.coverage}")

    return Build(listing=index.write(runs_dir), pages=tuple(pages),
                 dashboard=dashboard, statistics=stats, notes=tuple(notes))


# What a panel says when it has nothing to say — a gap in the setup rather than
# a fact about a market. These reach the terminal; the numbers do not.
_GAPS = ("not priced yet", "not measured yet")


def _line(name: str, rows: list[index.Row], result: Build) -> str:
    """One search's line: what is waiting, and only the gaps behind the folds.

    The lot count, because that is what changes daily and what you came for. Not
    the competitor or benchmark counts: the index deliberately stopped carrying
    weekly numbers onto a page read every morning, and a terminal that printed
    them anyway would just be the old index with a different font. What does
    come through is a panel with *nothing* in it — an unpriced search or an
    unmeasured one is a file to go and edit, not a market to read.
    """
    row = next((row for row in rows if row.name == name), None)
    bits = [row.summary if row else "?"]
    panel = next((p for p in (result.dashboard.panels if result.dashboard else ())
                  if p.name == name), None)
    stats_panel = next((p for p in (result.statistics.panels if result.statistics else ())
                        if p.name == name), None)
    for summary in (competitors.panel_summary(panel) if panel else None,
                    statistics.panel_summary(stats_panel) if stats_panel else None):
        if summary in _GAPS:
            bits.append(summary)
    return f"  {name:<18} {' · '.join(bits)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dashboard",
        description="Build the pages: the index, and one page per saved search.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="Write runs/index.html and runs/searches/*/")
    sub.add_parser("open", help="Build, then open the index in the parser's Chrome")

    args = parser.parse_args(argv)
    result = build()

    print(f"Wrote {result.listing}")
    rows = index.rows()
    for name, _page, _past in result.pages:
        print(_line(name, rows, result))
    for note in result.notes:
        # Said in the terminal as well as on the page: a stale cost book or a
        # crawl that does not cover a search is the difference between today's
        # numbers and last week's, and you want to hear about it where you typed
        # rather than three scrolls into the HTML.
        print(f"  {note}")

    if args.command == "open":
        from banzai24 import session

        print(f"\nOpening {result.listing} — close the window when you are done.")
        print("  If a lot opens signed out, sign in in that window — "
              "it will be saved.")
        try:
            asyncio.run(session.review(result.listing.resolve().as_uri()))
        except (session.SessionExpired, session.ProfileBusy) as exc:
            raise SystemExit(str(exc))
    return 0
