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

# GeoIP — download GeoLite2-City.mmdb from MaxMind. Best-effort: retries
# transient failures (429 rate-limit / 5xx) with backoff and never blocks
# the deploy. When credentials are unset or MaxMind stays unreachable it
# continues with GeoIP disabled (geo fields go empty). See the script header.
./bin/fetch-geoip-data

uv run --no-sync python manage.py collectstatic --no-input
uv run --no-sync python manage.py migrate

# Sync the committed fixtures into the database. ``loaddata`` upserts by
# primary key (``region_id`` on MicroRegion, ``prefix`` on Major/SubRegion,
# numeric PK on Resort) and never deletes rows, so re-running on every
# deploy is idempotent. Without this step, fixture edits (e.g. corrected
# region names) only reach production if an operator remembers to run
# ``loaddata`` out of band — see ``docs/management-commands.md``.
uv run --no-sync python manage.py loaddata \
    regions/fixtures/eaws_CH.json \
    regions/fixtures/eaws_FR.json \
    regions/fixtures/eaws_AT.json \
    regions/fixtures/eaws_IT.json \
    regions/fixtures/resorts.json \
    regions/fixtures/region_aliases.json
