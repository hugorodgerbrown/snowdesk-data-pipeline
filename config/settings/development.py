"""
config/settings/development.py — Development-environment overrides.

Enables DEBUG, uses SQLite by default, and relaxes security settings that
would be inappropriate in local development.
"""

from decouple import config

from .base import *  # noqa: F401, F403

DEBUG = True

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1").split(",")

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
# URL of the development-only view at ``pipeline.dev_views.slf_mirror``,
# which replays ``sample_data/slf_archive.ndjson`` with the same
# limit/offset paging contract as the upstream SLF API. Only defined in
# development.py so that ``fetch_bulletins --source local-mirror`` errors
# loudly if anyone tries to run it against a production-like environment.
SLF_API_LOCAL_MIRROR_URL = config(
    "SLF_API_LOCAL_MIRROR_URL",
    default="http://localhost:8000/dev/slf-mirror/api/bulletin-list/caaml",
)
