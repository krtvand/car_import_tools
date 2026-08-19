#!/usr/bin/env bash
#
# The morning run: every car, nearest upcoming auction day, judged and priced.
#
#   ./banzai24/searches/daily.sh                  # 20 lots per car
#   ./banzai24/searches/daily.sh --max-lots 40    # deeper, every car
#   ./banzai24/searches/daily.sh --dry-run        # print the search URLs only
#
# What it does:
#
#   check    the saved session, ONCE, before any fetch
#   fetch    each search — each narrows itself to its own nearest upcoming
#            auction day, which is often not the same day for all of them
#   extract  read this morning's sheets with Claude
#   report   a report.html per run, each lot in one of three groups
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
# Session first, once, is the whole reason this is a script rather than commands
# typed in a row: login is SMS 2FA, so an expired session costs a phone
# round-trip. Finding out before the first fetch costs one SMS; finding out
# between two cars costs the same SMS plus a half-finished morning.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

banzai24() { uv run python -m banzai24 "$@"; }

# Which searches the morning covers. Every saved search is a file in this
# directory; naming them here rather than globbing is deliberate, because
# extraction costs money and a file dropped in this directory should not
# silently start spending it.
#SEARCHES=(mazda-cx30 )
SEARCHES=("mazda-3")

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

for name in "${SEARCHES[@]}"; do
    echo
    echo "== ${name} =="
    # The .toml owns that car's filters and requirements; this script owns only
    # the running order. Extra flags pass through to every search.
    banzai24 fetch --search "${name}" "$@"
done

if [[ "$dry_run" == true ]]; then
    exit 0
fi

echo
echo "== sheets =="
# --today matters: without it the queue is every pending sheet ever downloaded,
# so you would pay to read lots that traded last week.
banzai24 extract --today

echo
echo "== reports =="
banzai24 report --today

cat <<'NEXT'

Each report sorts into three groups: lots that meet every requirement, lots
nothing has confirmed yet, and lots the sheet disqualified. Every card carries
its bid price, whichever group it is in.

  uv run python -m banzai24 report --today --open   # re-render, free, and open

`--open` opens one tab: `runs/index.html`, this morning's runs on top of the
last ten. It opens in the parser's own Chrome profile, so clicking a lot
through to banzai24 uses the session the parser uses rather than your everyday
browser. If a lot comes up signed out, sign in in that window — it is captured
while it is open, so the next `fetch` gets it.

It **waits** until you close that window: the browser only lives as long as the
command. Give it its own terminal, and close it before the next `fetch`, which
needs the profile to itself.

Re-tuning a requirement does not need a re-fetch. Edit the search's .toml and
re-run `report --today` — the report loads the file by name, so the morning is
re-judged for free.
NEXT
