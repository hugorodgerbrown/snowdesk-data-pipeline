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
