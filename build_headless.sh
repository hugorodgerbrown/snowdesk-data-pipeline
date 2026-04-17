#!/usr/bin/env bash
# build_headless.sh — Headless build for cron/worker processes.
#
# Installs Python dependencies and applies migrations only.
# No Tailwind, no npm, no collectstatic — no frontend assets needed.

set -o errexit

pip install poetry
poetry install --no-interaction --no-root --only main

python manage.py migrate
