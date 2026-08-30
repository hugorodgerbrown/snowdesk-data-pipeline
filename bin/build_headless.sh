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
# must see the same region data as the web service). resorts.json is
# excluded here for the same reason as in build.sh: Resort rows are editable
# data, applied by ``import_resorts`` rather than reloaded on every deploy.
uv run --no-sync python manage.py loaddata \
    apps/regions/fixtures/eaws_CH.json \
    apps/regions/fixtures/eaws_FR.json \
    apps/regions/fixtures/eaws_AT.json \
    apps/regions/fixtures/eaws_IT.json

# Re-link every micro-region to its centroid Location (SNOW-771). Mirrors
# build.sh, and is load-bearing here for the same reason it is there: the
# loaddata above resets `centroid_location` to NULL on all 461 regions,
# because no fixture carries that column.
#
# It matters especially on this path. Production's three services share ONE
# database, so a scheduler or task-worker deploy running this script would
# otherwise undo the links the web service's build.sh had just restored —
# the estate would come back or not depending purely on which dyno finished
# last. Offline and idempotent, so running it on every path is free.
uv run --no-sync python manage.py link_region_centroid_locations --commit

# Sync waffle.Flag rows to apps/core/fixtures/waffle_flags.json — create + delete
# only, never edit-in-place, so an operator's live admin-tuned targeting on
# an existing flag survives every deploy. Idempotent (a no-op once the DB
# matches the manifest) — see docs/management-commands.md. Mirrors build.sh so
# every deploy path keeps the flag set in sync with the shared DB.
uv run --no-sync python manage.py sync_waffle_flags --commit
