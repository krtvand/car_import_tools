#!/usr/bin/env bash
#
# Saved search: Mazda CX-30 (Japan auctions).
#
# Edit the filters below; they are the whole configuration for this car.
# Extra flags are passed straight through, so:
#
#   ./banzai24/searches/mazda-cx30.sh                     # up to 20 lots + sheets
#   ./banzai24/searches/mazda-cx30.sh --max-lots 60       # more lots
#   ./banzai24/searches/mazda-cx30.sh --no-sheets         # lots only, faster
#   ./banzai24/searches/mazda-cx30.sh --source archive    # completed sales
#   ./banzai24/searches/mazda-cx30.sh --dry-run           # just print the URL
#
# --no-defaults matters: without it, any filter this script does not set would
# silently inherit config.DEFAULT_FILTERS' value for it.
#
# The --body-model-code lines are the exception to that: they are post-fetch
# filters, so they are not part of DEFAULT_FILTERS and only apply here.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

exec uv run python -m banzai24 fetch \
    --no-defaults \
    --make MAZDA \
    --model CX-30 \
    --transmission auto \
    --year-start 2023 \
    --year-end 2023 \
    --mileage-end 55000 \
    --engine-capacity-start 1.9 \
    --grade 4 --grade 4.5 --grade 5 \
    --source auctions \
    --body-model-code DMEJ3R \
    --body-model-code DMFP \
    --body-model-code DMEJ3P \
    "$@"
