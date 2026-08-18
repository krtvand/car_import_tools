#!/usr/bin/env bash

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

exec uv run python -m banzai24 fetch \
    --no-defaults \
    --make MAZDA \
    --model CX-5 \
    --transmission auto \
    --year-start 2023 \
    --year-end 2023 \
    --mileage-start 30000 \
    --mileage-end 70000 \
    --grade 4 --grade 4.5 --grade 5 \
    --source auctions \
    --body-model-code KFEP \
    --body-model-code KF5P \
    "$@"
