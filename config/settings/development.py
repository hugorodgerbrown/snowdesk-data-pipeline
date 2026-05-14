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
# appear here for POSTs to succeed. Without this, an HTMX POST to e.g.
# ``fetch_weather_snippet`` over an ngrok tunnel fails with HTTP 403.
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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}

# # Show all SQL queries in the console during development
# LOGGING["loggers"]["django.db.backends"] = {  # type: ignore[index]  # noqa: F405
#     "handlers": ["console"],
#     "level": "DEBUG",
#     "propagate": False,
# }

# Disable rate limiting in development and tests so that rapid local requests
# (including the full test suite) are never throttled.
RATELIMIT_ENABLE = False

# Send email synchronously in development so Mailhog receives every message
# immediately. The base-settings default (True) runs email on a daemon thread,
# which can be killed by the dev server's auto-reloader before delivery.
SUBSCRIPTIONS_EMAIL_ASYNC = False

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
# URL of the development-only view at ``bulletins.dev_views.slf_mirror``,
# which replays ``bulletins/local_mirrors/slf_archive.ndjson`` with the same
# limit/offset paging contract as the upstream SLF API. Only defined in
# development.py so that ``fetch_bulletins --source local-mirror`` errors
# loudly if anyone tries to run it against a production-like environment.
SLF_API_LOCAL_MIRROR_URL = config(
    "SLF_API_LOCAL_MIRROR_URL",
    default="http://localhost:8000/dev/slf-mirror/api/bulletin-list/caaml",
)

# ---------------------------------------------------------------------------
# Local Open-Meteo mirror (dev only)
# ---------------------------------------------------------------------------
# Base URL of the development-only view at ``bulletins.dev_views.openmeteo_mirror``,
# which replays ``bulletins/local_mirrors/openmeteo_archive.ndjson`` in an
# Open-Meteo-compatible response shape. Only defined in development.py so that
# ``fetch_weather --source local-mirror`` and ``backfill_weather --source
# local-mirror`` error loudly if anyone tries to run them against a
# production-like environment.
WEATHER_API_LOCAL_MIRROR_BASE_URL = config(
    "WEATHER_API_LOCAL_MIRROR_BASE_URL",
    default="http://localhost:8000/dev/openmeteo-mirror/v1",
)
