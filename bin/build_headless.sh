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

# Sync waffle.Flag rows to apps/core/fixtures/waffle_flags.json — create + delete
# only, never edit-in-place, so an operator's live admin-tuned targeting on
# an existing flag survives every deploy. Idempotent (a no-op once the DB
# matches the manifest) — see docs/management-commands.md. Mirrors build.sh so
# every deploy path keeps the flag set in sync with the shared DB.
uv run --no-sync python manage.py sync_waffle_flags --commit
