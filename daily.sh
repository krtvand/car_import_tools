#!/usr/bin/env bash
#
# The morning run: every car, nearest upcoming auction day, judged, priced, and
# put next to what Cyprus is already asking for the same thing.
#
#   ./daily.sh                  # 20 lots per car
#   ./daily.sh --max-lots 40    # deeper, every car
#   ./daily.sh --dry-run        # print the search URLs only
#   ./daily.sh --no-cyprus      # skip the bazaraki crawl (the panel goes stale)
#   ./daily.sh --no-stats       # skip the weekly archive walk
#
# What it does:
#
#   check      the saved session, ONCE, before any fetch
#   fetch      each search — each narrows itself to its own nearest upcoming
#              auction day, which is often not the same day for all of them
#   extract    read this morning's sheets with Claude
#   report     a report.html per run, each lot in one of three groups
#   scrape     the same cars on bazaraki, over each search's competitor bounds
#   stats      once a week per car, the cheapest concluded sales that pass
#              [sheet] — the evidence behind a max bid
#   dashboard  runs/index.html and runs/competitors.html, rebuilt
#
# **It reads the sheets, and that costs money.** It did not always: the reports
# used to be built unread, on the reasoning that it was worth seeing what turned
# up before paying to look closer. That reasoning is now backwards. Whether a lot
# is worth looking at is exactly the judgement the sheet makes — the damage codes
# and the drivetrain are only on the sheet — so a report built before extraction
# puts every single lot in "unconfirmed" and answers nothing.
#
# The bill is small because the day narrowing already did the work: a run keeps
# only the nearest auction day, which has been 2-4 lots per car. Two cars is
# roughly five sheets, 8-15 cents. --limit is there if a wide morning surprises
# you.
#
# The bazaraki crawl at the end costs nothing but time. It is here rather than on
# its own cadence because a stale competitor panel is a failure you cannot see by
# looking at it — the page renders, the list is short, and short reads as good
# news. `--no-cyprus` skips it when you know it is fresh.
#
# Session first, once, is the whole reason this is a script rather than commands
# typed in a row: login is SMS 2FA, so an expired session costs a phone
# round-trip. Finding out before the first fetch costs one SMS; finding out
# between two cars costs the same SMS plus a half-finished morning.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

banzai24() { uv run python -m banzai24 "$@"; }
bazaraki() { uv run python -m bazaraki "$@"; }
dashboard() { uv run python -m dashboard "$@"; }

# Which searches the morning covers. Every saved search is a file in `searches/`;
# naming them here rather than globbing is deliberate, because extraction costs
# money and a file dropped in that directory should not silently start spending
# it.
#SEARCHES=("mazda-cx30")
#SEARCHES=("mazda-3")
#SEARCHES=("mazda-cx5")
#SEARCHES=("toyota-rav4-g" "toyota-rav4-x")
#SEARCHES=("toyota-rav4-x")
#SEARCHES=("toyota-harrier-g" "toyota-harrier-z" "toyota-harrier-z-leather" "toyota-harrier-s")
SEARCHES=("toyota-harrier-g" "toyota-harrier-s")

# --dry-run prints URLs and fetches nothing, so a session check would be a
# pointless SMS risk on a command whose whole point is to touch nothing.
dry_run=false
no_stats=false
no_cyprus=false
passthrough=()
for arg in "$@"; do
    case "$arg" in
        --dry-run) dry_run=true; passthrough+=("$arg") ;;
        --no-stats) no_stats=true ;;
        # Ours, not banzai24's — swallowed rather than passed on, because `fetch`
        # would reject a flag it has never heard of and kill the morning.
        --no-cyprus) no_cyprus=true ;;
        *) passthrough+=("$arg") ;;
    esac
done

if [[ "$dry_run" == false ]]; then
    echo "== searches =="
    # Cheap, and it fails before anything has been spent: a mis-edited band or a
    # competitor bound narrower than its own band stops the file loading, and
    # finding that out after the fetch is finding it out too late.
    uv run python -m searches check

    echo
    echo "== session =="
    banzai24 check
fi

for name in "${SEARCHES[@]}"; do
    echo
    echo "== ${name} =="
    # The .toml owns that car's bands and requirements; this script owns only the
    # running order. Extra flags pass through to every search.
    banzai24 fetch --search "${name}" ${passthrough[@]+"${passthrough[@]}"}
done

if [[ "$dry_run" == true ]]; then
    exit 0
fi

echo
echo "== sheets =="
# --today matters: without it the queue is every pending sheet ever downloaded,
# so you would pay to read lots that traded last week.
#
# Never fatal. An empty queue is an exit 1 saying "No sheets waiting", and it is
# the ordinary state of a morning whose lots all traded before: the sheets were
# read on the day they arrived and match by image hash, so a re-fetch queues
# nothing. Under `set -e` that ends the morning before a single report is built.
banzai24 extract --today || true

echo
echo "== reports =="
banzai24 report --today

if [[ "$no_cyprus" == false ]]; then
    for name in "${SEARCHES[@]}"; do
        echo
        echo "== cyprus: ${name} =="
        # Crawls the union of that search's competitor bounds, which is the scope
        # the panel asks about — and, because delisting is bounded to a run's own
        # scope, the only range whose "still on sale" flags can be trusted.
        # A search with no [band.competitors] declared says so and is skipped.
        bazaraki scrape --search "${name}" || true
    done
fi

if [[ "$no_stats" == false ]]; then
    for name in "${SEARCHES[@]}"; do
        # Weekly, not daily: what a car sold for last month does not change
        # overnight, and each walk pays to read sheets. The guard is inside the
        # command rather than out here, so "have seven days passed" is one
        # tested rule and not bash date arithmetic that differs on macOS.
        # Never fatal — a search with no sales to measure must not take the
        # dashboard down with it.
        banzai24 stats --search "${name}" --max-age-days 7 || true
    done
fi

echo
echo "== dashboard =="
dashboard build

cat <<'NEXT'

Each report sorts into three groups: lots that meet every requirement, lots
nothing has confirmed yet, and lots the sheet disqualified. Every card carries
its bid price, whichever group it is in.

  uv run python -m dashboard open     # rebuild both pages and open them

`open` opens one tab: `runs/index.html`, this morning's runs on top of the last
ten, with a link to the competitors panel. It opens in the parser's own Chrome
profile, so clicking a lot through to banzai24 uses the session the parser uses
rather than your everyday browser. If a lot comes up signed out, sign in in that
window — it is captured while it is open, so the next `fetch` gets it.

It **waits** until you close that window: the browser only lives as long as the
command. Give it its own terminal, and close it before the next `fetch`, which
needs the profile to itself.

Re-tuning a requirement does not need a re-fetch. Edit the search's .toml and
re-run `report --today` — the report loads the file by name, so the morning is
re-judged for free. The same is now true of a max bid, which is a trade made
deliberately: see docs/adr/0004-bid-prices-are-read-live.md.
NEXT
