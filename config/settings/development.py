"""
config/settings/development.py — Development-environment overrides.

Enables DEBUG, uses SQLite by default, and relaxes security settings that
would be inappropriate in local development.
"""

from decouple import config

from .base import *  # noqa: F401, F403

DEBUG = True

# Default dev hosts plus wildcard subdomains for ngrok and Render-style
# preview tunnels. ngrok's free tier rotates the public hostname on each
# restart, so we use leading-dot wildcards (``.ngrok-free.app``) — Django
# matches any subdomain when ALLOWED_HOSTS entries start with a dot.
# Override via the ``ALLOWED_HOSTS`` env var if a specific host list is
# needed (e.g. to test a non-tunnelled deployment).
ALLOWED_HOSTS = config(
    "ALLOWED_HOSTS",
    default="localhost,127.0.0.1,.ngrok-free.app,.ngrok.io,.ngrok.app,.ngrok.dev",
).split(",")

# CSRF Origin checks require the *scheme + host* of the inbound request to
# appear here for POSTs to succeed. Without this, an HTMX POST to any
# partial endpoint over an ngrok tunnel fails with HTTP 403.
# Wildcards are supported on the host portion (``https://*.ngrok-free.app``).
CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    default=(
        "https://*.ngrok-free.app,"
        "https://*.ngrok.io,"
        "https://*.ngrok.app,"
        "https://*.ngrok.dev"
    ),
).split(",")

INTERNAL_IPS = ["127.0.0.1"]

# ---------------------------------------------------------------------------
# Static files must not be cached in development
# ---------------------------------------------------------------------------
# ``runserver``'s ``StaticFilesHandler`` serves ``/static/`` before the
# middleware chain runs and sets no ``Cache-Control`` at all — only
# ``Last-Modified``. Chrome then falls back to HEURISTIC caching (roughly a
# tenth of the file's age at the moment it was cached), so a file that had sat
# unchanged for a day is held for hours and an edit to, say, ``map.js`` simply
# does not appear on reload. A hard refresh does not reliably clear it either,
# and the service worker compounds it by precaching whatever the HTTP cache
# handed it.
#
# WhiteNoise already serves static in production; pointing dev at it too — with
# a zero max-age — makes every static response revalidate, so a reload always
# runs the file on disk. ``runtimeArgs`` in ``.claude/launch.json`` passes
# ``--nostatic`` so ``runserver`` yields ``/static/`` to this middleware rather
# than intercepting it first.
#
# The e2e suite is unaffected: pytest-django's ``live_server`` installs its own
# ``StaticFilesHandler`` and never consults ``--nostatic``.
MIDDLEWARE = [  # noqa: F405
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *MIDDLEWARE,  # noqa: F405
]
WHITENOISE_AUTOREFRESH = True
WHITENOISE_MAX_AGE = 0

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
        # Let a transiently-locked connection wait-and-retry rather than
        # erroring immediately. This matters for the Playwright e2e suite,
        # where the live-server thread and the test thread share the database;
        # it is harmless for single-threaded unit tests and the dev server.
        "OPTIONS": {"timeout": 20},
    }
}

# Playwright e2e tests (``tox -e e2e``) run a live server in a background
# thread alongside the test thread. Django's default SQLite *test* database is
# an in-memory shared-cache DB (``file:memorydb_default?mode=memory&cache=shared``);
# its table-level shared-cache locks raise ``SQLITE_LOCKED`` ("database table
# is locked") at the teardown ``flush`` when the server thread still holds a
# connection — an error ``busy_timeout`` cannot retry. Point the e2e test DB at
# a real file so connections use ordinary file locking (``SQLITE_BUSY``, which
# the timeout above retries) instead of shared-cache table locks. Gated on
# ``E2E_TEST_DB`` (set only by the e2e tox env) so the unit ``test`` env keeps
# the faster in-memory database.
if config("E2E_TEST_DB", default=False, cast=bool):
    DATABASES["default"]["TEST"] = {"NAME": str(BASE_DIR / "e2e_test_db.sqlite3")}  # noqa: F405

# # Show all SQL queries in the console during development
# LOGGING["loggers"]["django.db.backends"] = {  # type: ignore[index]  # noqa: F405
#     "handlers": ["console"],
#     "level": "DEBUG",
#     "propagate": False,
# }

# Disable rate limiting in development and tests so that rapid local requests
# (including the full test suite) are never throttled.
RATELIMIT_ENABLE = False

# ---------------------------------------------------------------------------
# Dev-only shell-cache bypass (SNOW-585)
# ---------------------------------------------------------------------------
# base.py defaults this to False; development flips the default to True so a
# fresh worktree gets the fix for stale-shell-after-git-pull with no .env
# change. The env var still wins either way — the e2e tox env sets
# SW_DEV_SHELL_BYPASS=false so tests/e2e/test_pwa_lifecycle_update.py keeps
# exercising production semantics (banner + pwa.sw.update_available).
SW_DEV_SHELL_BYPASS = config("SW_DEV_SHELL_BYPASS", default=True, cast=bool)

# Use ImmediateBackend in development: tasks run inline so email lands in
# Mailpit immediately without needing a separate db_worker process.
TASKS = {
    "default": {
        "BACKEND": "django_tasks.backends.immediate.ImmediateBackend",
    }
}

# Allow per-request flag overrides via ``?dwf_<flag_name>=1`` (or ``=0``)
# while developing locally. Lets you flip a flag on the fly without
# touching the DB or the admin. Production deliberately omits this — an
# externally toggleable flag override would defeat the point of the gate.
WAFFLE_OVERRIDE = True

# Expose X-DB-Query-Count so local pages show the per-request SQL query
# count in DevTools; also needed for `monitor_query_counts` locally.
QUERY_COUNT_HEADER_ENABLED = True

# ---------------------------------------------------------------------------
# Content Security Policy — on in report-only mode locally
# ---------------------------------------------------------------------------
# django-csp-plus wires CSP_ENABLED at middleware __init__ (raising
# MiddlewareNotUsed when disabled), so we can't toggle it per-test via
# override_settings. Turning it on in dev (and therefore tests) mirrors
# production behaviour and lets local browsers surface real violations.
# Report-only means nothing is actually blocked — DEBUG error pages, etc.
# still render untouched.
CSP_ENABLED = True
CSP_REPORT_ONLY = True

# ---------------------------------------------------------------------------
# Local SLF mirror (dev only)
# ---------------------------------------------------------------------------
# URL of the development-only view at ``apps.bulletins.dev_views.slf_mirror``,
# which replays ``apps/bulletins/local_mirrors/slf_archive.ndjson`` with the same
# limit/offset paging contract as the upstream SLF API. Only defined in
# development.py so that ``fetch_bulletins --source local-mirror`` errors
# loudly if anyone tries to run it against a production-like environment.
SLF_API_LOCAL_MIRROR_URL = config(
    "SLF_API_LOCAL_MIRROR_URL",
    default="http://localhost:8000/dev/slf-mirror/api/bulletin-list/caaml",
)

# ---------------------------------------------------------------------------
# Local ALBINA mirror (dev only)
# ---------------------------------------------------------------------------
# URL of the development-only view at ``apps.bulletins.dev_views.albina_mirror``,
# which replays ``apps/bulletins/local_mirrors/albina_archive.ndjson`` with the
# same date/region path contract as the upstream avalanche.report CDN. Only
# defined in development.py so that ``fetch_bulletins --source albina
# --local-mirror`` errors loudly if anyone tries to run it against a
# production-like environment.
ALBINA_API_LOCAL_MIRROR_URL = config(
    "ALBINA_API_LOCAL_MIRROR_URL",
    default="http://localhost:8000/dev/albina-mirror",
)

# ---------------------------------------------------------------------------
# Local MeteoFrance mirror (dev only)
# ---------------------------------------------------------------------------
# When set to a ``file://`` directory URI, ``fetch_bulletins --source
# meteofrance`` reads ``massif-{NN:03d}.xml`` files from that directory
# instead of calling the live MeteoFrance APIM. The value must be an
# absolute path in the three-slash ``file:///`` form (relative paths are
# ambiguous and silently parse with the first segment as the netloc).
# Override via env var; default empty so production doesn't inherit a path.
METEOFRANCE_API_LOCAL_MIRROR_URL = config(
    "METEOFRANCE_API_LOCAL_MIRROR_URL",
    default="",
)
