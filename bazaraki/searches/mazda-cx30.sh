#!/usr/bin/env bash
#
# Saved search: Mazda CX-30 (Cyprus prices, bazaraki.com).
#
# Edit the filters below; they are the whole configuration for this car.
# Extra flags are passed straight through, so:
#
#   ./bazaraki/searches/mazda-cx30.sh                    # crawl + detail pages
#   ./bazaraki/searches/mazda-cx30.sh --export           # also write the xlsx
#   ./bazaraki/searches/mazda-cx30.sh --no-details       # list view only, faster
#   ./bazaraki/searches/mazda-cx30.sh --dry-run          # just print the plan
#
# --no-defaults matters: without it, any filter this script does not set would
# silently inherit config.DEFAULT_FILTERS' value for it.
#
# --max-pages is set well above what this search needs (60 adverts per page,
# ~2 pages today). Keep it that way: a run stopped by --max-pages is treated as
# truncated and skips delisting, so adverts that have gone would never be
# marked sold.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

exec uv run python -m bazaraki scrape \
    --no-defaults \
    --make mazda \
    --model cx-30 \
    --year-min 2022 \
    --mileage-min 0 \
    --mileage-max 60000 \
    --max-pages 10 \
    "$@"
