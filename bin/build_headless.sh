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

# NO BULK DATA WRITES ON DEPLOY — mirrors build.sh.
#
# This path used to run `loaddata` over the four EAWS fixtures and then
# `link_region_centroid_locations --commit` to repair what that reload
# wiped. Both are gone. A deploy that times out mid-write leaves the data
# half applied, and this script runs on the scheduler AND the task worker,
# which deploy alongside the web service against one shared database.
#
# Reference data is seeded and refreshed by an operator running a command —
# see docs/runbooks/reset-live-db.md.

# Sync waffle.Flag rows to apps/core/fixtures/waffle_flags.json — create + delete
# only, never edit-in-place, so an operator's live admin-tuned targeting on
# an existing flag survives every deploy. Idempotent (a no-op once the DB
# matches the manifest) — see docs/management-commands.md. Mirrors build.sh so
# every deploy path keeps the flag set in sync with the shared DB.
uv run --no-sync python manage.py sync_waffle_flags --commit
