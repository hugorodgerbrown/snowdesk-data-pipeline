#!/usr/bin/env bash
# build.sh — Render.com build script.
#
# Runs during each deploy to install dependencies, collect static files,
# and apply database migrations.

set -o errexit

pip install poetry
poetry install --no-interaction --no-root

python manage.py collectstatic --no-input
python manage.py migrate
