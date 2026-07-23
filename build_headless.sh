#!/usr/bin/env bash
# build_headless.sh — Headless build for cron/worker processes.
#
# Installs Python dependencies and applies migrations only.
# No Tailwind, no npm, no collectstatic — no frontend assets needed.

set -o errexit

# See build.sh for the rationale behind the in-project virtualenv.
pip install uv
uv sync --no-dev --frozen

uv run --no-sync python manage.py migrate

# Sync committed fixtures (see build.sh for the rationale — cron workers
# must see the same region/resort data as the web service).
uv run --no-sync python manage.py loaddata \
    regions/fixtures/eaws_CH.json \
    regions/fixtures/eaws_FR.json \
    regions/fixtures/eaws_AT.json \
    regions/fixtures/eaws_IT.json \
    regions/fixtures/resorts.json

# Sync waffle.Flag rows to core/fixtures/waffle_flags.json — create + delete
# only, never edit-in-place, so an operator's live admin-tuned targeting on
# an existing flag survives every deploy. Idempotent (a no-op once the DB
# matches the manifest) — see docs/management-commands.md. Mirrors build.sh so
# every deploy path keeps the flag set in sync with the shared DB.
uv run --no-sync python manage.py sync_waffle_flags --commit
