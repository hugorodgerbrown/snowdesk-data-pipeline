#!/usr/bin/env bash
# build.sh — Render.com build script.
#
# Runs during each deploy to install dependencies, build the Tailwind CSS
# output, collect static files, and apply database migrations.

set -o errexit

# Python dependencies. Keep the environment in the project so this works in
# both Render and cloud development environments; the latter does not provide
# an active virtualenv. `--no-dev` mirrors the old `--only main`; `--frozen`
# installs strictly from uv.lock and fails if it is out of date.
pip install uv
uv sync --no-dev --frozen

# Tailwind CSS build (output.css is gitignored — must be built on deploy)
npm install
npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --minify

# SNOW-622: minify the first-party JS in place, before collectstatic hashes
# it. ~23,000 lines reached the browser as written, comments and all, on a
# map homepage that is already the heaviest page on the site. The checkout
# is ephemeral, so rewriting the tracked sources here affects nothing
# beyond this deploy. sw.js and sw-kill.js are deliberately excluded — see
# the script header.
node bin/minify-js

# Minify the hand-written CSS in place, for the same reason and with the same
# ephemeral-checkout caveat. map.css is render-blocking on the map homepage
# and shipped as ~3,000 commented lines; Tailwind's output.css is already
# built with --minify above and is skipped, as is the vendored maplibre-gl.css.
node bin/minify-css

# GeoIP — download GeoLite2-City.mmdb from MaxMind. Best-effort: retries
# transient failures (429 rate-limit / 5xx) with backoff and never blocks
# the deploy. When credentials are unset or MaxMind stays unreachable it
# continues with GeoIP disabled (geo fields go empty). See the script header.
./bin/fetch-geoip-data

uv run --no-sync python manage.py collectstatic --no-input
uv run --no-sync python manage.py migrate

# Sync the committed fixtures into the database. ``loaddata`` upserts by
# primary key (``region_id`` on MicroRegion, ``prefix`` on Major/SubRegion)
# and never deletes rows. Without this step, fixture edits (e.g. corrected
# region names) only reach production if an operator remembers to run
# ``loaddata`` out of band — see ``docs/management-commands.md``.
#
# **It is NOT idempotent for columns the fixtures do not carry**, and this
# comment claimed it was until SNOW-771. ``loaddata`` builds each instance
# from the fixture's fields alone and saves the whole row, so any column
# added to the model since the fixture was written is reset to its default
# on every deploy. ``MicroRegion.centroid_location`` is the first one where
# that mattered: it silently NULLed 461 links on staging, orphaning the
# Location rows behind them, and nothing noticed until the weather map came
# back empty. The re-link step below repairs it; a future nullable column
# that something depends on will need the same treatment, or a fixture that
# carries it.
#
# resorts.json is deliberately NOT in this list. Resort rows are editable
# data owned by this database (admin + map editor), not reference data;
# re-loading the fixture on every deploy would silently revert every edit.
# The fixture seeds fresh local/CI databases only, and the curated sheet is
# applied by hand with ``manage.py import_resorts --commit``. See
# docs/decisions/resorts-are-editable-data.md.
uv run --no-sync python manage.py loaddata \
    apps/regions/fixtures/eaws_CH.json \
    apps/regions/fixtures/eaws_FR.json \
    apps/regions/fixtures/eaws_AT.json \
    apps/regions/fixtures/eaws_IT.json \
    apps/regions/fixtures/region_aliases.json

# Precompute per-region offline-basemap tile coverage (SNOW-521) on every
# tier — pure function of each region's (static) geometry, so recomputing
# in full on every deploy is safe and idempotent. The eaws_CH fixture
# above already ships basemap_download for CH; this also backfills the
# FR/AT/IT fixtures, which don't (see docs/offline-map.md).
uv run --no-sync python manage.py compute_basemap_download --commit

# Re-link every micro-region to its centroid Location (SNOW-771). This is
# NOT optional and NOT a backfill: the loaddata above writes back every
# field its fixtures carry and resets the ones they do not to the model
# default, and no fixture carries `centroid_location` — so that column is
# NULL for all 461 regions by the time this line runs, every single deploy.
#
# It went unnoticed because nothing reads the column at deploy time; the
# symptom is a weather map that is empty for every region, hours later.
# Staging lost 461 links this way on 2026-08-30.
#
# Affordable to run every time because it is wholly offline: the coordinate
# is derived from each region's own boundary and the elevation is read from
# `centroid_elevation_m`, which `refresh_centroid_elevations` resolved once
# and committed to the fixtures. No Open-Meteo call, no per-environment
# backfill.
uv run --no-sync python manage.py link_region_centroid_locations --commit

# Sync waffle.Flag rows to apps/core/fixtures/waffle_flags.json — create + delete
# only, never edit-in-place, so an operator's live admin-tuned targeting on
# an existing flag survives every deploy. Idempotent (a no-op once the DB
# matches the manifest) — see docs/management-commands.md.
uv run --no-sync python manage.py sync_waffle_flags --commit
