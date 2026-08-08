"""CLI for the bazaraki.com cars scraper.

Filters come from config.DEFAULT_FILTERS (edit config.py); the flags below
override the most common ones for quick one-off runs.

Examples:
    uv run python main.py scrape --max-pages 3 --export
    uv run python main.py scrape --make mazda --model cx-30 --year-min 2018 --price-max 25000
    uv run python main.py scrape --max-pages 10 --no-details
    uv run python main.py export --out cars.xlsx
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses

from . import config
from . import db
from .crawler import run_scrape
from .export import export_xlsx


def _filters_from_args(args: argparse.Namespace) -> config.CarFilters:
    """Start from DEFAULT_FILTERS and apply any provided CLI overrides."""
    overrides = {
        name: getattr(args, name)
        for name in ("make", "model", "price_min", "price_max", "year_min", "year_max")
        if getattr(args, name) is not None
    }
    return dataclasses.replace(config.DEFAULT_FILTERS, **overrides)


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
    p_scrape.add_argument("--max-pages", type=int, default=3, help="Listing pages to crawl")
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