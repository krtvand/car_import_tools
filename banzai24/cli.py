"""CLI for the banzai24 Japanese-auction scraper.

Filters come from ``config.DEFAULT_FILTERS`` (edit ``banzai24/config.py``); the
flags below override the common ones for one-off runs.

Examples:
    uv run python -m banzai24 login
    uv run python -m banzai24 fetch --max-pages 1
    uv run python -m banzai24 fetch --make MAZDA --model CX-30 --year-start 2023
    uv run python -m banzai24 fetch --max-pages 4 --no-sheets
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses

from . import config, fetch, session


_OVERRIDABLE = (
    "make", "model", "transmission",
    "year_start", "year_end",
    "mileage_start", "mileage_end",
    "engine_capacity_start", "engine_capacity_end",
    "source",
)

# Base for --no-defaults: everything unset except the fields a search must have.
# Without this, a saved search that omits a filter would silently inherit
# DEFAULT_FILTERS' value for it — e.g. a RAV4 config picking up the CX-30's
# engine-capacity floor.
NEUTRAL_FILTERS = config.AuctionFilters(model=None, transmission=None)


def _filters_from_args(args: argparse.Namespace) -> config.AuctionFilters:
    """Apply CLI overrides on top of DEFAULT_FILTERS, or of nothing."""
    base = NEUTRAL_FILTERS if getattr(args, "no_defaults", False) else config.DEFAULT_FILTERS
    overrides = {
        name: getattr(args, name)
        for name in _OVERRIDABLE
        if getattr(args, name, None) is not None
    }
    if getattr(args, "grade", None):
        overrides["grade_origin"] = tuple(args.grade)
    return dataclasses.replace(base, **overrides)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="banzai24",
        description="Scrape Japanese auction lots and their auction sheets from banzai24.com",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Sign in by hand once and save the browser session")
    sub.add_parser("check", help="Verify the saved session still authenticates")

    for name, help_text in (("fetch", "Fetch lots + auction sheets into a run directory"),):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--make")
        p.add_argument("--model")
        p.add_argument("--transmission", choices=["auto", "manual"])
        p.add_argument("--year-start", type=int, dest="year_start")
        p.add_argument("--year-end", type=int, dest="year_end")
        p.add_argument("--mileage-start", type=int, dest="mileage_start")
        p.add_argument("--mileage-end", type=int, dest="mileage_end")
        p.add_argument("--engine-capacity-start", type=float, dest="engine_capacity_start")
        p.add_argument("--engine-capacity-end", type=float, dest="engine_capacity_end")
        p.add_argument("--grade", action="append", help="Repeatable, e.g. --grade 4 --grade 4.5")
        p.add_argument("--source", choices=["auctions", "archive"])
        p.add_argument("--no-defaults", action="store_true", dest="no_defaults",
                       help="Ignore config.DEFAULT_FILTERS; use only the flags given. "
                            "Saved searches use this so they cannot inherit stray filters.")
        p.add_argument("--dry-run", action="store_true", dest="dry_run",
                       help="Print the search URL and exit without opening a browser")
        p.add_argument("--max-pages", type=int, default=1,
                       help="Result pages to fetch (20 lots per page). Default 1.")
        p.add_argument("--no-sheets", action="store_true", help="Skip downloading auction sheets")
        p.add_argument("--headless", action="store_true",
                       help="Hide the browser. Only works when already signed in — "
                            "the default shows a window so you can sign in inline.")

    return parser


def main() -> None:
    args = _build_parser().parse_args()

    if args.command == "login":
        try:
            asyncio.run(session.login())
        except session.SessionExpired as exc:
            raise SystemExit(str(exc))
        return

    if args.command == "check":
        try:
            total = asyncio.run(fetch.check_session())
        except (session.SessionExpired, session.ServiceUnavailable) as exc:
            raise SystemExit(str(exc))
        print(f"Session OK — {total} lots match DEFAULT_FILTERS.")
        return

    filters = _filters_from_args(args)
    print(f"Filters: {config.describe(filters)}")
    print(f"Search:  {config.build_search_url(filters)}")

    if args.dry_run:
        print("Dry run — nothing fetched.")
        return

    try:
        result = asyncio.run(
            fetch.run_fetch(
                filters,
                max_pages=args.max_pages,
                sheets=not args.no_sheets,
                headless=args.headless,
            )
        )
    except (session.SessionExpired, session.ServiceUnavailable) as exc:
        raise SystemExit(str(exc))
    except RuntimeError as exc:
        raise SystemExit(str(exc))

    print(result.summary())
    if result.truncated:
        print(
            f"Note: {result.total_pages} pages available "
            f"({result.total_lots} lots) — raise --max-pages to get the rest."
        )


if __name__ == "__main__":
    main()