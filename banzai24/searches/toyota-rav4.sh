#!/usr/bin/env bash
#
# Saved search: Toyota RAV4 (Japan auctions).
#
# NOTE the slug is RAV4, not RAV-4 — verified against banzai24, where
# /TOYOTA/RAV4/ resolves and /TOYOTA/RAV-4/ does not.
#
# Edit the filters below; they are the whole configuration for this car.
# Extra flags are passed straight through, so:
#
#   ./banzai24/searches/toyota-rav4.sh                    # 1 page + sheets
#   ./banzai24/searches/toyota-rav4.sh --max-pages 5      # more pages
#   ./banzai24/searches/toyota-rav4.sh --no-sheets        # lots only, faster
#   ./banzai24/searches/toyota-rav4.sh --source archive   # completed sales
#   ./banzai24/searches/toyota-rav4.sh --dry-run          # just print the URL
#
# The year/mileage/grade bounds mirror the CX-30 search as a starting point —
# adjust them to what you actually want for this car. No engine-capacity filter
# is set, deliberately: RAV4 trims span 2.0/2.5 petrol and hybrid, and a floor
# copied from the CX-30 would quietly exclude some of them.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

exec uv run python -m banzai24 fetch \
    --no-defaults \
    --make TOYOTA \
    --model RAV4 \
    --transmission auto \
    --year-start 2023 \
    --year-end 2023 \
    --mileage-end 55000 \
    --grade 4 --grade 4.5 --grade 5 \
    --source auctions \
    "$@"
