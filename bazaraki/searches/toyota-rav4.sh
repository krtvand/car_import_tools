#!/usr/bin/env bash
#
# Saved search: Toyota RAV4 (Cyprus prices, bazaraki.com).
#
# NOTE the model slug is toyota-rav4, not rav4 or rav-4 — bazaraki prefixes the
# make on some models. Verified live: /toyota/toyota-rav4/ returns listings,
# /toyota/rav4/ and /toyota/rav-4/ both 404.
#
# Edit the filters below; they are the whole configuration for this car.
# Extra flags are passed straight through, so:
#
#   ./bazaraki/searches/toyota-rav4.sh                   # crawl + detail pages
#   ./bazaraki/searches/toyota-rav4.sh --export          # also write the xlsx
#   ./bazaraki/searches/toyota-rav4.sh --no-details      # list view only, faster
#   ./bazaraki/searches/toyota-rav4.sh --dry-run         # just print the plan
#
# The year/mileage bounds mirror the CX-30 search as a starting point — adjust
# them to what you actually want for this car. RAV4 stock on bazaraki is larger,
# so check the "truncated" note in the run summary and raise --max-pages if it
# appears (a truncated run skips delisting).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

exec uv run python -m bazaraki scrape \
    --no-defaults \
    --make toyota \
    --model toyota-rav4 \
    --year-min 2022 \
    --mileage-min 0 \
    --mileage-max 60000 \
    --max-pages 10 \
    "$@"
