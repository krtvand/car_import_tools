"""CLI for the banzai24 Japanese-auction scraper.

Filters come from ``config.DEFAULT_FILTERS`` (edit ``banzai24/config.py``); the
flags below override the common ones for one-off runs.

Examples:
    uv run python -m banzai24 login
    uv run python -m banzai24 fetch --max-lots 20
    uv run python -m banzai24 fetch --make MAZDA --model CX-30 --year-start 2023
    uv run python -m banzai24 fetch --max-lots 80 --no-sheets
    uv run python -m banzai24 fetch --all-days   # don't narrow to the nearest day
    uv run python -m banzai24 fetch --body-model-code DMEJ3P --body-model-code DMEJ3R

`fetch` normalizes what it collected on the way out. `normalize` re-does that
over an already-saved run and touches no network, so a parser fix is applied by
re-running it rather than by fetching anything again:

    uv run python -m banzai24 normalize                  # the most recent run
    uv run python -m banzai24 normalize runs/2026-08-08_234439_MAZDA-CX-30
    uv run python -m banzai24 normalize --all            # incl. the later days

`extract` reads the downloaded sheets with Claude. It is the only step that
costs money (~$0.03/sheet), so it prints the bill first and takes --limit:

    uv run python -m banzai24 extract --dry-run          # what would be read
    uv run python -m banzai24 extract --limit 1          # try one
    uv run python -m banzai24 extract                    # the whole queue

`report` joins the run directory and the database into one self-contained
report.html. It costs nothing to regenerate — no network, no model call — so a
template tweak is a re-run of this, never a re-fetch:

    uv run python -m banzai24 report                     # the most recent run
    uv run python -m banzai24 report --open              # and open it
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import webbrowser
from datetime import date
from pathlib import Path

from . import config, db, fetch, normalize, session
from .lot_filters import LotFilters


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


def _lot_filters_from_args(args: argparse.Namespace) -> LotFilters:
    """Post-fetch filters. Not affected by --no-defaults: they have no defaults.

    Kept apart from :func:`_filters_from_args` because these never reach the
    site — see :mod:`banzai24.lot_filters`.
    """
    return LotFilters(body_model_code=tuple(getattr(args, "body_model_code", None) or ()))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="banzai24",
        description="Scrape Japanese auction lots and their auction sheets from banzai24.com",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Sign in by hand once and save the browser session")
    sub.add_parser("check", help="Verify the saved session still authenticates")

    norm = sub.add_parser(
        "normalize",
        help="Turn a saved run's lots.json into DB rows and lots.csv (no network)",
    )
    norm.add_argument("run_dir", nargs="?", metavar="RUN_DIR",
                      help="Run directory to normalize. Defaults to the most recent one.")
    norm.add_argument("--all", action="store_true", dest="all_lots",
                      help="Normalize every lot the fetch saw, not just the ones it kept. "
                           "The later auction days a run set aside are still real market "
                           "data, and re-reading them costs nothing.")
    norm.add_argument("--no-db", action="store_true", help="Write lots.csv only")
    norm.add_argument("--no-csv", action="store_true", help="Write to the database only")

    ext = sub.add_parser(
        "extract",
        help="Read pending auction sheets with Claude (costs money — see --limit)",
    )
    ext.add_argument("run_dir", nargs="?", metavar="RUN_DIR",
                     help="Read only the sheets this run downloaded. Without it "
                          "the queue is every pending sheet in the database — "
                          "which after a two-car morning is both cars. Results "
                          "always go to the run directory that owns the sheet.")
    ext.add_argument("--today", action="store_true",
                     help="Read only sheets downloaded by today's runs. The queue is "
                          "otherwise every pending sheet ever downloaded, which after "
                          "a few days means paying to read lots that have already "
                          "traded.")
    ext.add_argument("--limit", type=int, metavar="N",
                     help="Read at most N sheets. Use this the first time.")
    ext.add_argument("--force", action="store_true",
                     help="Re-read sheets already extracted at the same image hash. "
                          "For after a prompt change, when the image is unchanged "
                          "but what we can get out of it is not.")
    ext.add_argument("--retry-failed", action="store_true", dest="retry_failed",
                     help="Put previously failed sheets back in the queue. Most "
                          "failures are transient.")
    ext.add_argument("--dry-run", action="store_true", dest="dry_run",
                     help="List what would be read, and the estimated cost, "
                          "without calling the API.")

    rep = sub.add_parser(
        "report",
        help="Build a self-contained report.html for a run (no network, free to re-run)",
    )
    rep.add_argument("run_dir", nargs="?", metavar="RUN_DIR",
                     help="Run directory to report on. Defaults to the most recent one.")
    rep.add_argument("--today", action="store_true",
                     help="Report every run started today, one report.html each. "
                          "A two-car morning is two runs, and this re-renders both "
                          "after `extract` without naming either.")
    rep.add_argument("--all", action="store_true", dest="all_lots",
                     help="Include every lot the fetch saw, not just the ones it kept.")
    rep.add_argument("-o", "--output", metavar="PATH",
                     help="Write somewhere other than <run>/report.html.")
    rep.add_argument("--open", action="store_true", dest="open_report",
                     help="Open the report in your browser when it is written.")
    rep.add_argument("--jpy-per-eur", type=float, metavar="RATE", dest="jpy_per_eur",
                     help="Also show start prices in euro at this rate, so they compare "
                          "with the Cyprus figures. No default: a hard-coded rate would "
                          "go stale silently, and a wrong one is worse than none.")

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
        p.add_argument("--body-model-code", "--model-code", action="append",
                       dest="body_model_code", metavar="CODE",
                       help="Keep only lots whose chassis code contains CODE. Repeatable; "
                            "a lot matching any one is kept. The type prefix banzai24 "
                            "sometimes attaches is ignored, so DMEJ3P matches both "
                            "'5AA-DMEJ3P' and 'DMEJ3P'.")
        p.add_argument("--no-defaults", action="store_true", dest="no_defaults",
                       help="Ignore config.DEFAULT_FILTERS; use only the flags given. "
                            "Saved searches use this so they cannot inherit stray filters.")
        p.add_argument("--dry-run", action="store_true", dest="dry_run",
                       help="Print the search URL and exit without opening a browser")
        p.add_argument("--max-lots", type=int, default=fetch.DEFAULT_MAX_LOTS,
                       dest="max_lots", metavar="N",
                       help=f"Stop once N lots have been kept — after the day narrowing "
                            f"and any filters, so N is what you will review and pay to "
                            f"extract. Pages are turned as needed. "
                            f"Default {fetch.DEFAULT_MAX_LOTS}.")
        p.add_argument("--no-sheets", action="store_true", help="Skip downloading auction sheets")
        p.add_argument("--no-normalize", action="store_true", dest="no_normalize",
                       help="Stop after writing lots.json. The run can be normalized "
                            "later with `normalize` — it needs no network.")
        p.add_argument("--all-days", action="store_true", dest="all_days",
                       help="Keep every auction day the search returns. By default a run "
                            "is narrowed to the closest upcoming day — the one still "
                            "biddable — and later days are skipped.")
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
            checked = asyncio.run(fetch.check_session())
        except (session.SessionExpired, session.ServiceUnavailable) as exc:
            raise SystemExit(str(exc))
        print(f"Session OK — {checked.describe()} DEFAULT_FILTERS.")
        return

    if args.command == "normalize":
        run_dir = Path(args.run_dir) if args.run_dir else normalize.latest_run()
        if run_dir is None:
            raise SystemExit("No run directories found — run `fetch` first.")
        if not (run_dir / "lots.json").exists():
            raise SystemExit(f"{run_dir} has no lots.json.")
        result = normalize.run_normalize(
            run_dir,
            all_lots=args.all_lots,
            to_db=not args.no_db,
            to_csv=not args.no_csv,
        )
        print(result.summary())
        for problem in result.problems:
            print(f"  skipped: {problem}")
        return

    if args.command == "extract":
        from . import sheets

        db.init_db()
        lots = db.pending_sheets(include_failed=args.retry_failed)
        if args.force:
            lots = [lot for lot in db.all_lots() if lot.sheet_path]

        # Narrowing happens before --limit, so `extract <rav4-run> --limit 5`
        # reads five RAV4 sheets rather than five lots off the front of a queue
        # that also holds this morning's CX-30 run.
        run_dir = Path(args.run_dir) if args.run_dir else None
        if run_dir:
            lots = [lot for lot in lots if sheets.owned_by(lot, run_dir)]
        if args.today:
            today = {d.resolve() for d in normalize.runs_from(date.today())}
            lots = [lot for lot in lots
                    if (owner := sheets.run_dir_of(lot)) and owner.resolve() in today]
        if args.limit:
            lots = lots[: args.limit]

        if not lots:
            where = f" in {run_dir}" if run_dir else " from today's runs" if args.today else ""
            raise SystemExit(
                f"No sheets waiting{where}. Run `fetch` to download some, or "
                "`extract --force` to re-read ones already done."
            )

        # Say the price before spending it. ~$0.03/sheet is the arithmetic in
        # sheets.py, not a quote — the real figure lands in the summary.
        print(f"{len(lots)} sheet(s) to read with {sheets.MODEL} "
              f"(effort={sheets.EFFORT}) — roughly ${0.03 * len(lots):.2f}")
        if args.dry_run:
            for lot in lots:
                print(f"  {lot.lot_short}  {lot.mark} {lot.model}  {lot.sheet_path}")
            print("Dry run — nothing sent, nothing spent.")
            return

        result = sheets.run_extract(lots, run_dir=run_dir, force=args.force)
        print(result.summary())
        for mismatch in result.mismatches:
            print(f"  cross-check: {mismatch}")
        return

    if args.command == "report":
        from . import report as report_module

        if args.today:
            if args.run_dir:
                raise SystemExit("Give a RUN_DIR or --today, not both.")
            if args.output:
                raise SystemExit("--output writes one file; it cannot combine with --today.")
            run_dirs = normalize.runs_from(date.today())
            if not run_dirs:
                raise SystemExit("No runs started today — run `fetch` first.")
        else:
            run_dir = Path(args.run_dir) if args.run_dir else normalize.latest_run()
            if run_dir is None:
                raise SystemExit("No run directories found — run `fetch` first.")
            if not (run_dir / "lots.json").exists():
                raise SystemExit(f"{run_dir} has no lots.json.")
            run_dirs = [run_dir]

        db.init_db()
        for run_dir in run_dirs:
            built = report_module.run_report(
                run_dir,
                output=Path(args.output) if args.output else None,
                all_lots=args.all_lots,
                jpy_per_eur=args.jpy_per_eur,
            )
            print(built.summary())
            if built.missing:
                print(f"  {len(built.missing)} lot(s) rendered from the run file only — "
                      f"run `normalize {run_dir}` to fill them in.")
            if built.cyprus_reason:
                print(f"  no Cyprus comparables: {built.cyprus_reason}")
            if args.open_report:
                webbrowser.open(built.output.resolve().as_uri())
        return

    filters = _filters_from_args(args)
    lots_filter = _lot_filters_from_args(args)
    print(f"Filters: {config.describe(filters)}")
    print(f"Search:  {config.build_search_url(filters)}")
    if lots_filter.active:
        print(f"Keeping: {lots_filter.describe()}")

    if args.dry_run:
        print("Dry run — nothing fetched.")
        return

    try:
        result = asyncio.run(
            fetch.run_fetch(
                filters,
                max_lots=args.max_lots,
                sheets=not args.no_sheets,
                headless=args.headless,
                nearest_day_only=not args.all_days,
                lots_filter=lots_filter,
            )
        )
    except (session.SessionExpired, session.ServiceUnavailable) as exc:
        raise SystemExit(str(exc))
    except RuntimeError as exc:
        raise SystemExit(str(exc))

    print(result.summary())
    if lots_filter.active and not result.lots:
        where = f"on {result.trade_date}" if result.trade_date else "in the pages fetched"
        print(f"Note: nothing {where} matched {lots_filter.describe()}. Later auction "
              "days are not searched — re-run with --all-days to look past this one.")
    if not args.all_days and result.trade_date is None:
        print("Note: no upcoming auction day in these results — kept every lot. "
              "(Expected for --source archive.)")
    if result.truncated:
        knob = "raise --max-lots" if result.truncated_by == "--max-lots" else "narrow the search"
        print(
            f"Note: stopped by {result.truncated_by} with "
            f"{result.total_pages} pages ({result.total_lots} lots) available — "
            f"{knob} to get the rest."
        )

    if not args.no_normalize and result.lots:
        normalized = normalize.run_normalize(result.run_dir)
        print(normalized.summary())
        for problem in normalized.problems:
            print(f"  skipped: {problem}")


if __name__ == "__main__":
    main()