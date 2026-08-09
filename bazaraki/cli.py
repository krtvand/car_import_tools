"""CLI for the bazaraki.com cars scraper.

Filters come from config.DEFAULT_FILTERS (edit config.py); the flags below
override the most common ones for quick one-off runs.

Examples:
    uv run python main.py scrape --max-pages 3 --export
    uv run python main.py scrape --make mazda --model cx-30 --year-min 2018 --price-max 25000
    uv run python main.py scrape --max-pages 10 --no-details
    uv run python main.py export --out cars.xlsx

Saved searches live in ``bazaraki/searches/*.sh`` — one script per car, each
pinning its own filters with --no-defaults.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses

from . import config
from . import db
from .crawler import run_scrape
from .export import export_xlsx


_OVERRIDABLE = (
    "make", "model",
    "price_min", "price_max",
    "year_min", "year_max",
    "mileage_min", "mileage_max",
)

# Base for --no-defaults: every filter unset. Without it, a saved search that
# omits a filter would silently inherit DEFAULT_FILTERS' value for it — e.g. a
# RAV4 search picking up the CX-30's mileage ceiling.
NEUTRAL_FILTERS = config.CarFilters()


def _filters_from_args(args: argparse.Namespace) -> config.CarFilters:
    """Apply CLI overrides on top of DEFAULT_FILTERS, or of nothing."""
    base = NEUTRAL_FILTERS if getattr(args, "no_defaults", False) else config.DEFAULT_FILTERS
    overrides = {
        name: getattr(args, name)
        for name in _OVERRIDABLE
        if getattr(args, name, None) is not None
    }
    return dataclasses.replace(base, **overrides)


def _describe(filters: config.CarFilters) -> str:
    """One-line summary of the filters actually set, for the run log."""
    parts = [
        f"{f.name}={value}"
        for f in dataclasses.fields(filters)
        if (value := getattr(filters, f.name)) not in (None, [], "")
    ]
    return ", ".join(parts) or "none (whole cars category)"


def _plan_url(filters: config.CarFilters) -> str:
    """The URL --dry-run shows.

    Year and engine-size codes are site-specific and only readable from the live
    category page, so a search using them can't be spelled out ahead of the
    crawl; say so rather than print a URL missing those filters.
    """
    if config.needs_option_resolution(filters):
        return (
            config.BASE_URL + config.base_path(filters)
            + "  (+ year/engine-size codes resolved from the live page)"
        )
    return config.build_search_url(filters)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape car listings from bazaraki.com")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scrape = sub.add_parser("scrape", help="Crawl listings into the SQLite DB")
    p_scrape.add_argument("--make", help="Make slug, e.g. mazda (overrides config)")
    p_scrape.add_argument("--model", help="Model slug, e.g. cx-30 (requires --make)")
    p_scrape.add_argument("--price-min", type=int, dest="price_min")
    p_scrape.add_argument("--price-max", type=int, dest="price_max")
    p_scrape.add_argument("--year-min", type=int, dest="year_min")
    p_scrape.add_argument("--year-max", type=int, dest="year_max")
    p_scrape.add_argument("--mileage-min", type=int, dest="mileage_min", metavar="KM")
    p_scrape.add_argument("--mileage-max", type=int, dest="mileage_max", metavar="KM")
    p_scrape.add_argument(
        "--no-defaults",
        action="store_true",
        dest="no_defaults",
        help="Ignore config.DEFAULT_FILTERS; use only the flags given. Saved "
             "searches use this so they cannot inherit stray filters.",
    )
    p_scrape.add_argument("--max-pages", type=int, default=3, help="Listing pages to crawl")
    p_scrape.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Print the filters and the search URL, then exit without crawling",
    )
    p_scrape.add_argument(
        "--no-details",
        dest="details",
        action="store_false",
        help="Skip detail pages (faster; only list-view fields)",
    )
    p_scrape.add_argument("--concurrency", type=int, default=1, help="Concurrent requests")
    p_scrape.add_argument("--export", action="store_true", help="Also write xlsx when done")

    p_export = sub.add_parser("export", help="Export the DB to an .xlsx file")
    p_export.add_argument("--out", default="bazaraki_cars.xlsx", help="Output .xlsx path")

    args = parser.parse_args()

    if args.command == "scrape":
        filters = _filters_from_args(args)
        print(f"Filters: {_describe(filters)}")
        if args.dry_run:
            print(f"Search:  {_plan_url(filters)}")
            print("Dry run — nothing scraped.")
            return
        summary = asyncio.run(
            run_scrape(
                filters=filters,
                max_pages=args.max_pages,
                details=args.details,
                concurrency=args.concurrency,
            )
        )
        note = "" if summary["completed"] else " (truncated by --max-pages; no delisting)"
        print(
            f"Done. Saw {summary['seen']} adverts, delisted {summary['delisted']}"
            f"{note}. {db.count_listings()} listings total in {db.DB_PATH}"
        )
        if args.export:
            out = export_xlsx()
            print(f"Exported -> {out}")
    elif args.command == "export":
        out = export_xlsx(args.out)
        print(f"Exported {db.count_listings()} listings -> {out}")


if __name__ == "__main__":
    main()