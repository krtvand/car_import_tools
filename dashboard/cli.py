"""``python -m dashboard`` — write the pages, and open them in the right browser.

``build`` writes ``runs/index.html``, ``runs/competitors.html`` and
``runs/auction_statistics.html`` side by side. Side by side because the links
between them are relative, so the set survives ``runs/`` being copied somewhere
else — the same property the index has always had for its links into run
directories.

``open`` builds and then opens the index in **the parser's own Chrome**, not
your everyday browser. banzai24 caps how many authenticated clients you may have
at once and the reports link back to it, so opening a report anywhere else costs
a click that lands signed out and may unseat the session the parser needs. That
window lives only as long as this command; see :func:`banzai24.session.review`.

This module may import both parsers. Neither of them imports it — the runs index
moved here precisely so that ``banzai24 report`` could go on promising to touch
no network and cost nothing, while this page needs two databases, a cost book
and today's exchange rate.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape

from . import competitors, index, statistics

TEMPLATE_DIR = Path(__file__).parent / "templates"
COMPETITORS_FILENAME = "competitors.html"
STATISTICS_FILENAME = "auction_statistics.html"


def _backticks(text: str) -> Markup:
    """`like this` → <code>like this</code>, escaping everything else first.

    The notes are written as prose with commands in them and are also printed to
    a terminal by ``build``, where backticks are the readable form. Marking the
    result safe is only sound because :func:`escape` has already run over the
    whole string — the substitution introduces the only tags in it.
    """
    out, tag = [], False
    for part in escape(text).split("`"):
        out.append(f"<code>{part}</code>" if tag else part)
        tag = not tag
    return Markup("".join(out))


def render(dashboard: competitors.Dashboard,
           generated_at: datetime | None = None) -> str:
    """The whole page as one string. No file written, so this is testable."""
    # Same autoescape reasoning as report.py and index.py: the loader keys on
    # ".j2", so `select_autoescape` would see no ".html" and quietly leave
    # escaping off — and advert titles are free text off a public website.
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["backticks"] = _backticks
    return env.get_template("competitors.html.j2").render(
        dashboard=dashboard,
        generated_at=(generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M"),
    )


def render_statistics(stats: statistics.Statistics,
                      generated_at: datetime | None = None) -> str:
    """The statistics page as one string. No file written, so this is testable."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["backticks"] = _backticks
    return env.get_template("auction_statistics.html.j2").render(
        statistics=stats,
        generated_at=(generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M"),
    )


def _statistics_summary(stats: statistics.Statistics) -> str:
    """The line the index carries under its link to the statistics page.

    Counts the searches nobody has measured separately from the sales found,
    because "0 benchmarks" and "2 searches never measured" mean opposite things
    and one number would blur them — the same reasoning as :func:`_summary`.
    """
    count = stats.benchmark_count
    bits = [f"{count} cheapest acceptable sale{'' if count == 1 else 's'}"]
    if stats.unmeasured:
        bits.append(f"{stats.unmeasured} search"
                    f"{'' if stats.unmeasured == 1 else 'es'} not measured yet")
    return " · ".join(bits)


def _summary(dashboard: competitors.Dashboard) -> str:
    """The line the index carries under its link to this page.

    Counts the searches that could not be priced separately from the adverts
    found, because "0 competitors" and "3 searches unpriced" mean opposite
    things and a single number would blur them.
    """
    unpriced = sum(
        1 for panel in dashboard.panels
        if panel.problem or all(band.sell_price_eur is None for band in panel.bands)
    )
    count = dashboard.competitor_count
    bits = [f"{count} advert{'' if count == 1 else 's'} asking less than a "
            f"cyprus sell price"]
    if unpriced:
        bits.append(f"{unpriced} search{'' if unpriced == 1 else 'es'} not priced yet")
    return " · ".join(bits)


def build(runs_dir: Path | None = None) -> tuple[
        Path, Path, competitors.Dashboard, statistics.Statistics]:
    """Write all three pages. Always a full rewrite; staleness is the only failure mode."""
    runs_dir = runs_dir or index.RUNS_DIR
    dashboard = competitors.build(runs_dir)
    stats = statistics.build()

    runs_dir.mkdir(parents=True, exist_ok=True)
    panel_path = runs_dir / COMPETITORS_FILENAME
    panel_path.write_text(render(dashboard), encoding="utf-8")

    stats_path = runs_dir / STATISTICS_FILENAME
    stats_path.write_text(render_statistics(stats), encoding="utf-8")

    listing = index.write(
        runs_dir,
        competitors_summary=_summary(dashboard),
        statistics_summary=_statistics_summary(stats),
    )
    return listing, panel_path, dashboard, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dashboard",
        description="Build the workflow's pages: the runs index, the "
                    "competitors panel and the auction statistics.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="Write runs/index.html, runs/competitors.html "
                                 "and runs/auction_statistics.html")
    sub.add_parser("open", help="Build, then open the index in the parser's Chrome")

    args = parser.parse_args(argv)
    listing, panel_path, dashboard, stats = build()

    print(f"Wrote {listing}")
    print(f"Wrote {panel_path} — {_summary(dashboard)}")
    print(f"Wrote {panel_path.parent / STATISTICS_FILENAME} — "
          f"{_statistics_summary(stats)}")
    if dashboard.money_problem:
        # Said in the terminal as well as on the page: it is the difference
        # between today's numbers and a stale run's, and you want to hear about
        # it where you typed rather than three scrolls into the HTML.
        print(f"  {dashboard.money_problem}")
    for panel in dashboard.panels:
        if panel.problem:
            print(f"  {panel.name}: will not load — {panel.problem}")
        elif panel.coverage:
            print(f"  {panel.name}: {panel.coverage}")

    if args.command == "open":
        from banzai24 import session

        print(f"\nOpening {listing} — close the window when you are done.")
        print("  If a lot opens signed out, sign in in that window — "
              "it will be saved.")
        try:
            asyncio.run(session.review(listing.resolve().as_uri()))
        except (session.SessionExpired, session.ProfileBusy) as exc:
            raise SystemExit(str(exc))
    return 0
