#!/usr/bin/env bash
#
# The morning run: both cars, nearest upcoming auction day, no money spent.
#
#   ./banzai24/searches/daily.sh                  # 20 lots per car + sheets
#   ./banzai24/searches/daily.sh --max-lots 40    # deeper, both cars
#   ./banzai24/searches/daily.sh --dry-run        # print both search URLs only
#
# What it does, and deliberately does not do:
#
#   check    the saved session, ONCE, before either fetch
#   fetch    each car — each narrows itself to its own nearest upcoming
#            auction day, which is often not the same day for both
#   report   a report.html per run, showing what turned up
#
# It does **not** read any auction sheets. That is the only step that costs
# money (~$0.015 a sheet), and it is worth deciding on after seeing what the
# morning actually turned up rather than before. The commands to do it are
# printed at the end.
#
# Session first, once, is the whole reason this is a script rather than two
# commands typed in a row: login is SMS 2FA, so an expired session costs a
# phone round-trip. Finding out before the first fetch costs one SMS; finding
# out between the two cars costs the same SMS plus a half-finished morning.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

banzai24() { uv run python -m banzai24 "$@"; }

# --dry-run prints URLs and fetches nothing, so a session check would be a
# pointless SMS risk on a command whose whole point is to touch nothing.
dry_run=false
for arg in "$@"; do
    [[ "$arg" == "--dry-run" ]] && dry_run=true
done

if [[ "$dry_run" == false ]]; then
    echo "== session =="
    banzai24 check
fi

for search in mazda-cx30 toyota-rav4; do
    echo
    echo "== ${search} =="
    # Each saved search owns that car's filters; this script owns only the
    # running order. Extra flags pass through to both.
    "./banzai24/searches/${search}.sh" "$@"
done

if [[ "$dry_run" == true ]]; then
    exit 0
fi

echo
echo "== reports =="
banzai24 report --today

cat <<'NEXT'

Sheets are downloaded but unread — the reports show grades, prices and Cyprus
comparables, and flag every lot as "sheet pending". To read the ones worth
reading (~$0.015 each) and refresh the reports in place:

  uv run python -m banzai24 extract --today --dry-run     # the queue and the bill
  uv run python -m banzai24 extract --today --limit 5     # read five
  uv run python -m banzai24 report --today --open         # re-render, free, and open

`--open` opens one browser tab per run — today's runs are listed above, so
`open <path>` from that list works too if you only want one car.

`--today` matters: without it the queue is every pending sheet ever
downloaded, so you would pay to read lots that traded last week. Add a run
directory to read one car only, e.g. `extract runs/<today>_TOYOTA-RAV4`.
NEXT
