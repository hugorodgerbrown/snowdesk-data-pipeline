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

# NO BULK DATA WRITES ON DEPLOY.
#
# This script used to run `loaddata` over the four EAWS fixtures (461 rows),
# `compute_basemap_download --commit` (461 rows) and
# `link_region_centroid_locations --commit` (461 rows) on every single
# deploy. All three are gone.
#
# They were wrong for the reason data migrations are wrong: a deploy that
# times out mid-write leaves the data half applied, and production deploys
# THREE services concurrently against one shared database. Reference data
# is seeded and refreshed by an operator running a command, the same way
# `import_resorts` and `import_locations` already work.
#
# The reload also caused real harm. `loaddata` writes back every field its
# fixtures carry and resets every column they do not, so each deploy NULLed
# all 461 `MicroRegion.centroid_location` values and orphaned the Location
# rows behind them — silently, because nothing reads that column at deploy
# time (SNOW-771). Removing the reload removes the wipe, which is why the
# re-link step is gone too rather than merely moved.
#
# Seeding a fresh database, or refreshing after a fixture change, is in
# docs/runbooks/reset-live-db.md.

# Sync waffle.Flag rows to apps/core/fixtures/waffle_flags.json — create + delete
# only, never edit-in-place, so an operator's live admin-tuned targeting on
# an existing flag survives every deploy. Idempotent (a no-op once the DB
# matches the manifest) — see docs/management-commands.md.
uv run --no-sync python manage.py sync_waffle_flags --commit
