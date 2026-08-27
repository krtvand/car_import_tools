"""CLI for the banzai24 Japanese-auction scraper.

Every search is a file: ``searches/<name>.toml`` holds one car's whole
declaration — what banzai24 filters on, what we filter on, and what the auction
sheet has to say before a lot counts as wanted. Nothing is inherited from
anywhere, so a filter that is not in the file is not applied.

Examples:
    uv run python -m banzai24 login
    uv run python -m banzai24 fetch --search mazda-cx30
    uv run python -m banzai24 fetch --search mazda-cx30 --max-lots 80 --no-sheets
    uv run python -m banzai24 fetch --search toyota-rav4 --all-days
    uv run python -m banzai24 searches            # what is declared, and where

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
report.html, with each lot judged against the search that fetched it. It costs
nothing to regenerate — no network, no model call — so re-tuning a requirement
and re-judging this morning is a re-run of this, never a re-fetch:

    uv run python -m banzai24 report                     # the most recent run
    uv run python -m banzai24 report --open              # and open the runs index
    uv run python -m banzai24 report --bid-prices my.csv # try a re-tuned price table

Each card's bid block is `max bid − area cost = bid reduced`, read out of the two
CSVs under `banzai24/inputs/`. Editing one of those is the normal way to change
what the report says to bid; `report` is free to re-run afterwards.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
from datetime import date
from pathlib import Path

from . import bidding, config, db, fetch, normalize, search, session
from . import stats as stats_mod

_ROOT = Path(__file__).parent.parent   # only to print the default paths readably


# The index page. Written by `dashboard build` and only opened here, so this is
# the one place `report` needs to know where it lives.
INDEX_PATH = _ROOT / "runs" / "index.html"


def _load_search(name: str) -> search.SearchDefinition:
    """One saved search, or a clean exit naming the ones that exist.

    A malformed definition stops the command rather than degrading, unlike a
    malformed price table: a bad price table costs you a column, while a bad
    search would mean fetching the wrong car — or judging the right one against
    half a list.
    """
    try:
        return search.load(name)
    except search.SearchDefinitionError as exc:
        raise SystemExit(str(exc))


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
                     help="Open runs/index.html in the parser's signed-in Chrome, "
                          "so a click through to a lot lands authenticated. One "
                          "tab, whatever this command built. The page itself is "
                          "written by `dashboard build`, not here — this opens "
                          "what is already there. Waits until you close the "
                          "window: the browser is signed in only for as long as "
                          "this command runs.")
    rep.add_argument("--jpy-per-eur", type=float, metavar="RATE", dest="jpy_per_eur",
                     help="Also show start prices in euro at this rate, so they compare "
                          "with the Cyprus figures. No default: a hard-coded rate would "
                          "go stale silently, and a wrong one is worse than none.")
    rep.add_argument("--area-prices", metavar="PATH", dest="area_prices",
                     help=f"The auction houses' area costs, subtracted from the max bid. "
                          f"Default {bidding.AREA_PRICES_PATH.relative_to(_ROOT)} — the "
                          f"year in that name is part of the path, not read off the "
                          f"clock, so nothing silently changes file on 1 January.")

    sub.add_parser("searches",
                   help="List the saved searches and what each asks for "
                        "(the same list as `python -m searches list`)")

    for name, help_text in (("fetch", "Fetch lots + auction sheets into a run directory"),):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--search", required=True, metavar="NAME",
                       help="Which saved search to run — the file name without "
                            f"its suffix, from {search.SEARCH_DIR.relative_to(_ROOT)}/. "
                            "The file is the complete declaration; there are no "
                            "per-filter flags and nothing to inherit. Edit the "
                            "file to change what is searched for.")
        p.add_argument("--source", choices=["auctions", "archive"],
                       help="Override the search's own source for one run. The one "
                            "exception to 'edit the file': flipping to completed "
                            "sales is a question you ask *of* a saved search, not a "
                            "different search.")
        p.add_argument("--dry-run", action="store_true", dest="dry_run",
                       help="Print the search URL and exit without opening a browser")
        p.add_argument("--max-lots", type=int, default=fetch.DEFAULT_MAX_LOTS,
                       dest="max_lots", metavar="N",
                       help=f"Stop once N lots have been kept — after the day narrowing "
                            f"and any filters, so N is what you will review and pay to "
                            f"extract. Pages are turned as needed. "
                            f"Default {fetch.DEFAULT_MAX_LOTS}.")
        p.add_argument("--no-sheets", action="store_true", help="Skip downloading auction sheets")
        p.add_argument("--no-photos", action="store_true", dest="no_photos",
                       help=f"Skip downloading the lot photographs. Up to "
                            f"{fetch.PHOTO_LIMIT} per lot otherwise — they cost a few "
                            f"KB each and show under the sheet in the report.")
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

    st = sub.add_parser(
        "stats",
        help="The cheapest concluded sales that pass this search's [sheet] "
             "requirements — evidence for a max bid")
    st.add_argument("--search", required=True, metavar="NAME",
                    help="Which saved search to measure. Its [auction_statistics] "
                         "section narrows the archive; everything else is inherited "
                         "from [site] and [api], so the sales measured are the car "
                         "being bought.")
    st.add_argument("--keepers", type=int, default=stats_mod.KEEPERS, metavar="N",
                    help=f"How many passing lots to find per band. "
                         f"Default {stats_mod.KEEPERS}.")
    st.add_argument("--cap", type=int, default=stats_mod.INSPECTION_CAP, metavar="N",
                    help=f"Most paid sheet readings per band. A lot read in an "
                         f"earlier week is free and does not count. "
                         f"Default {stats_mod.INSPECTION_CAP}.")
    st.add_argument("--max-age-days", type=int, metavar="N", dest="max_age_days",
                    help="Do nothing if this search was measured within the last "
                         "N days. What makes `daily.sh` able to call this every "
                         "morning: statistics move at the speed of the auction "
                         "calendar, not the morning.")
    st.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="Print each band's archive URL and exit — no browser, "
                         "no model call, nothing spent.")
    st.add_argument("--headless", action="store_true",
                    help="Hide the browser. Only works when already signed in.")

    return parser


def main() -> None:
    args = _build_parser().parse_args()

    if args.command == "login":
        try:
            asyncio.run(session.login())
        except (session.SessionExpired, session.ProfileBusy) as exc:
            raise SystemExit(str(exc))
        return

    if args.command == "check":
        try:
            checked = asyncio.run(fetch.check_session())
        except (session.SessionExpired, session.ServiceUnavailable,
                session.ProfileBusy) as exc:
            raise SystemExit(str(exc))
        print(f"Session OK — {checked.describe()}")
        return

    if args.command == "searches":
        # An alias, kept because it is in muscle memory and in every script. The
        # searches are no longer banzai24's — both parsers and the dashboard read
        # them — so the listing itself lives with the files.
        from searches.cli import main as searches_main

        return searches_main(["list"]) and None

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

    if args.command == "stats":
        definition = _load_search(args.search)
        if args.max_age_days and stats_mod.is_fresh(args.search, args.max_age_days):
            stamped = stats_mod.last_run(args.search)
            print(f"{args.search}: measured {stamped} — inside "
                  f"{args.max_age_days} days, nothing to do.")
            return
        if not definition.stats_declared:
            # Not fatal: [site] and [api] alone describe a perfectly good archive
            # search. Said out loud because an unnarrowed measurement of a RAV4
            # mixes trim lines, and the operator asked for HYBRID G specifically.
            print(f"{args.search} has no [auction_statistics] section — "
                  f"measuring against [site] and [api] alone.")

        if args.dry_run:
            for band in definition.bands:
                print(f"{band.label}\n  "
                      f"{config.build_search_url(definition.stats_filters(band))}")
            print("Dry run — nothing opened, nothing spent.")
            return

        # Say the price before spending it, the same way `extract` does. The cap
        # is per band, so the worst case is what a first run on a cold cache
        # costs; a later week pays only for lots that newly entered the cheap end.
        worst = args.cap * len(definition.bands)
        print(f"Up to {worst} sheet(s) to read with {stats_mod.sheets.MODEL} "
              f"across {len(definition.bands)} band(s) — at most "
              f"roughly ${0.03 * worst:.2f}")
        try:
            result = asyncio.run(stats_mod.run_stats(
                definition,
                wanted=args.keepers,
                cap=args.cap,
                headless=args.headless,
            ))
        except stats_mod.OrderingBroken as exc:
            raise SystemExit(f"Stopped: {exc}")
        except (session.SessionExpired, session.ServiceUnavailable,
                session.ProfileBusy) as exc:
            raise SystemExit(str(exc))
        print(result.summary())
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
                area_prices=Path(args.area_prices) if args.area_prices else None,
            )
            print(built.summary())
            if built.missing:
                print(f"  {len(built.missing)} lot(s) rendered from the run file only — "
                      f"run `normalize {run_dir}` to fill them in.")
            if built.bid_reason:
                # Said here as well as on the page: a mis-edited price table is
                # your own edit, and you want to hear about it in the terminal
                # you just typed in rather than three scrolls into the HTML.
                #
                # The consequence clause is conditional for the same reason it is
                # in the template — a broken *alias* file suppresses nothing, and
                # telling you the bid column is gone when it is not is how you
                # learn to ignore this line.
                gone = "" if built.quoted else " — no bid price on any card"
                print(f"  {built.bid_reason}{gone}")

        # Not written here. The index is the dashboard's page now — it carries a
        # link to the competitors panel, which needs both databases and the cost
        # book, and `report` is the command that promises to touch no network and
        # cost nothing. So this opens what `dashboard build` last wrote.
        listing = INDEX_PATH

        if args.open_report:
            if not listing.exists():
                raise SystemExit(
                    f"{listing} does not exist yet — run `uv run python -m "
                    f"dashboard build` to write it.")
            # One tab, always the index — never one per report. A two-car
            # morning used to open two windows and still left every earlier run
            # findable only in Finder.
            #
            # In the parser's own Chrome, because the report exists to be clicked
            # through to banzai24 and banzai24 limits how many authenticated
            # clients you may have — your everyday browser is not the signed-in
            # one. That browser only stays signed in for as long as this command
            # runs; see session.review.
            print(f"\nOpening {listing} — close the window when you are done.")
            print("  It is as fresh as your last `dashboard build`.")
            # Not a warning about a broken state: banzai24 does not reliably
            # hand a session between browsers, so signing in here is the normal
            # path. The snapshot loop in review() captures it for the next fetch.
            print("  If a lot opens signed out, sign in in that window — "
                  "it will be saved.")
            try:
                asyncio.run(session.review(listing.resolve().as_uri()))
            except (session.SessionExpired, session.ProfileBusy) as exc:
                raise SystemExit(str(exc))
        return

    definition = _load_search(args.search)
    if args.source:
        definition = dataclasses.replace(
            definition,
            filters=dataclasses.replace(definition.filters, source=args.source),
        )
    lots_filter = definition.lot_filters

    print(f"Search:  {definition.name} — {definition.describe()}")
    print(f"URL:     {config.build_search_url(definition.filters)}")

    if args.dry_run:
        print("Dry run — nothing fetched.")
        return

    try:
        result = asyncio.run(
            fetch.run_fetch(
                definition,
                max_lots=args.max_lots,
                sheets=not args.no_sheets,
                photos=not args.no_photos,
                headless=args.headless,
                nearest_day_only=not args.all_days,
            )
        )
    except (session.SessionExpired, session.ServiceUnavailable,
            session.ProfileBusy) as exc:
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