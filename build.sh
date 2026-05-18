#!/usr/bin/env bash
# build.sh — Render.com build script.
#
# Runs during each deploy to install dependencies, build the Tailwind CSS
# output, collect static files, and apply database migrations.

set -o errexit

# Python dependencies
pip install poetry
poetry install --no-interaction --no-root --only main

# Tailwind CSS build (output.css is gitignored — must be built on deploy)
npm install
npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --minify

python manage.py collectstatic --no-input
python manage.py migrate

# Sync the committed fixtures into the database. ``loaddata`` upserts by
# primary key (``region_id`` on MicroRegion, ``prefix`` on Major/SubRegion,
# numeric PK on Resort) and never deletes rows, so re-running on every
# deploy is idempotent. Without this step, fixture edits (e.g. corrected
# region names) only reach production if an operator remembers to run
# ``loaddata`` out of band — see ``docs/management-commands.md``.
python manage.py loaddata \
    regions/fixtures/eaws_CH.json \
    regions/fixtures/eaws_FR.json \
    regions/fixtures/eaws_AT.json \
    regions/fixtures/eaws_IT.json \
    regions/fixtures/resorts.json
