#!/usr/bin/env bash
# build_headless.sh — Headless build for cron/worker processes.
#
# Installs Python dependencies and applies migrations only.
# No Tailwind, no npm, no collectstatic — no frontend assets needed.

set -o errexit

# See build.sh for the rationale behind `uv sync --active --no-dev --frozen`.
pip install uv
uv sync --active --no-dev --frozen

python manage.py migrate

# Sync committed fixtures (see build.sh for the rationale — cron workers
# must see the same region/resort data as the web service).
python manage.py loaddata \
    regions/fixtures/eaws_CH.json \
    regions/fixtures/eaws_FR.json \
    regions/fixtures/eaws_AT.json \
    regions/fixtures/eaws_IT.json \
    regions/fixtures/resorts.json
