"""CLI for the bazaraki.com cars scraper.

A scrape normally names a **saved search** — the same ``searches/*.toml`` the
auction side runs on — and crawls the car it names over the union of its
competitor bounds. That is the whole configuration; there is nothing to inherit
and no second place to keep in step:

    uv run python -m bazaraki scrape --search mazda-cx30
    uv run python -m bazaraki scrape --search mazda-cx30 --dry-run

The individual flags are still here for a one-off probe — checking whether a
model slug is right, seeing how much stock a bound would pull in — and they are
not a saved search: nothing records them, and the dashboard will not know a
scrape happened over that scope.

    uv run python -m bazaraki scrape --make mazda --model cx-30 --year-min 2018
    uv run python -m bazaraki export --out cars.xlsx
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses

import searches

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

# Every filter unset. There is no DEFAULT_FILTERS to fall back to any more, so a
# flag that is not given is a filter that is not applied — full stop.
NEUTRAL_FILTERS = config.CarFilters()


def _filters_from_args(args: argparse.Namespace) -> config.CarFilters:
    """The filters one scrape will use: a saved search, or the flags given.

    The two do not mix. A ``--search`` that also carried ad-hoc overrides would
    be a crawl whose scope no panel could reproduce, and ``db._in_scope`` bounds
    delisting to that scope — so the overrides would silently decide which
    adverts are allowed to go missing.
    """
    if getattr(args, "search", None):
        if any(getattr(args, name, None) is not None for name in _OVERRIDABLE):
            raise SystemExit(
                "--search is the whole configuration; it cannot be combined with "
                "the individual filter flags. Edit the search file instead.")
        try:
            return config.filters_for(searches.load(args.search))
        except (searches.SearchDefinitionError, config.NoCompetitorBounds) as exc:
            raise SystemExit(str(exc)) from None
        except Exception as exc:      # bazaraki.cars.UnknownCar and friends
            raise SystemExit(str(exc.args[0] if exc.args else exc)) from None

    overrides = {
        name: getattr(args, name)
        for name in _OVERRIDABLE
        if getattr(args, name, None) is not None
    }
    if not overrides:
        known = ", ".join(searches.available()) or "none found"
        raise SystemExit(
            f"Nothing to scrape. Name a saved search with --search "
            f"(available: {known}), or give filter flags for a one-off probe.")
    return dataclasses.replace(NEUTRAL_FILTERS, **overrides)


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
    p_scrape.add_argument(
        "--search", metavar="NAME",
        help="Saved search to crawl for — the file name without its suffix, from "
             "searches/. Crawls that car over the union of its bands' competitor "
             "bounds, which is the scope the dashboard's panels ask about. The "
             "file is the complete declaration; it cannot be combined with the "
             "filter flags below.")
    p_scrape.add_argument("--make", help="Make slug, e.g. mazda (one-off probe)")
    p_scrape.add_argument("--model", help="Model slug, e.g. cx-30 (requires --make)")
    p_scrape.add_argument("--price-min", type=int, dest="price_min")
    p_scrape.add_argument("--price-max", type=int, dest="price_max")
    p_scrape.add_argument("--year-min", type=int, dest="year_min")
    p_scrape.add_argument("--year-max", type=int, dest="year_max")
    p_scrape.add_argument("--mileage-min", type=int, dest="mileage_min", metavar="KM")
    p_scrape.add_argument("--mileage-max", type=int, dest="mileage_max", metavar="KM")
    p_scrape.add_argument(
        "--max-pages", type=int, default=10,
        help="Listing pages to crawl, 60 adverts each. Set well above what a "
             "search needs and keep it there: a run stopped by --max-pages is "
             "treated as truncated and skips delisting entirely, so adverts that "
             "have sold would never be marked gone.")
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