"""CLI for the bazaraki.com cars scraper.

Examples:
    uv run python main.py scrape --max-pages 3
    uv run python main.py scrape --max-pages 10 --no-details
    uv run python main.py export
    uv run python main.py export --out cars.xlsx
"""
from __future__ import annotations

import argparse
import asyncio

import db
from crawler import CARS_URL, run_scrape
from export import export_xlsx


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape car listings from bazaraki.com")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scrape = sub.add_parser("scrape", help="Crawl listings into the SQLite DB")
    p_scrape.add_argument("--url", default=CARS_URL, help="Category URL to scrape")
    p_scrape.add_argument("--max-pages", type=int, default=3, help="Listing pages to crawl")
    p_scrape.add_argument(
        "--no-details",
        dest="details",
        action="store_false",
        help="Skip detail pages (faster; only card fields: title/price/url/image)",
    )
    p_scrape.add_argument("--concurrency", type=int, default=5, help="Concurrent requests")
    p_scrape.add_argument("--export", action="store_true", help="Also write xlsx when done")

    p_export = sub.add_parser("export", help="Export the DB to an .xlsx file")
    p_export.add_argument("--out", default="bazaraki_cars.xlsx", help="Output .xlsx path")

    args = parser.parse_args()

    if args.command == "scrape":
        asyncio.run(
            run_scrape(
                category_url=args.url,
                max_pages=args.max_pages,
                details=args.details,
                concurrency=args.concurrency,
            )
        )
        print(f"Done. {db.count_listings()} listings in {db.DB_PATH}")
        if args.export:
            out = export_xlsx()
            print(f"Exported -> {out}")
    elif args.command == "export":
        out = export_xlsx(args.out)
        print(f"Exported {db.count_listings()} listings -> {out}")


if __name__ == "__main__":
    main()