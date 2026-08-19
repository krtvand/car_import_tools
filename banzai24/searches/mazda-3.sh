#!/usr/bin/env bash

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

exec uv run python -m banzai24 fetch \
    --no-defaults \
    --make MAZDA \
    --model 3 \
    --transmission auto \
    --year-start 2023 \
    --year-end 2023 \
    --mileage-start 0 \
    --mileage-end 50000 \
    --grade 4 --grade 4.5 --grade 5 \
    --source auctions \
    --body-model-code BP5P \
    --body-model-code KF5P \
    "$@"
