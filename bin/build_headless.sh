#!/usr/bin/env bash
# build_headless.sh — Headless build for cron/worker processes.
#
# Installs Python dependencies. Nothing else: no Tailwind, no npm, no
# collectstatic (no frontend assets are needed), and — deliberately — no
# schema or flag writes. See "ONE MIGRATOR" below.

set -o errexit

# See build.sh for the rationale behind the in-project virtualenv.
pip install uv
uv sync --no-dev --frozen

# ONE MIGRATOR: the web service, and only the web service.
#
# Production deploys three services from the same `release` commit —
# snowdesk-website, snowdesk-scheduler and snowdesk-background-tasks — and
# Render builds them CONCURRENTLY against one shared Postgres. This script
# used to run `migrate` on the two workers, so all three raced:
# `django_migrations` is read, the same DDL is attempted more than once, and
# the losers fail or deadlock. Django takes no advisory lock across
# processes, so nothing serialises them.
#
# `migrate` and `sync_waffle_flags --commit` therefore live in build.sh
# alone. Both are writes against the shared database, and both are now run
# exactly once per deploy.
#
# The trade-off, stated plainly: Render does not order the three builds, so
# a worker can boot on new code while the web service is still migrating.
# That window is safe for these two workers specifically, and only because
# their work is asynchronous and retried — the scheduler's next tick fires
# on APScheduler's interval, and a django-tasks job that raises is left in
# the queue for the next pass. Neither serves a request, so nobody sees an
# error. A worker that had to be correct at the instant it booted would need
# a real ordering mechanism (a Render pre-deploy command on the web service,
# or a start-up gate that waits on `showmigrations`), not this comment.

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

# Waffle flags are synced by build.sh on the web service, for the same
# reason migrations are — see "ONE MIGRATOR" above. `sync_waffle_flags`
# creates and deletes rows, so two concurrent runs can both miss the same
# absent flag and both try to create it.
