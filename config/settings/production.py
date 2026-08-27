"""
config/settings/production.py — Production-environment overrides.

Tightens security settings, requires explicit environment variables, and
configures the database from a DATABASE_URL connection string.
"""

from decouple import config

from .base import *  # noqa: F401, F403

DEBUG = False

# ---------------------------------------------------------------------------
# WhiteNoise — serve static files without a dedicated web server
# ---------------------------------------------------------------------------

MIDDLEWARE.insert(  # noqa: F405 — MIDDLEWARE imported via wildcard from base
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,  # noqa: F405
    "whitenoise.middleware.WhiteNoiseMiddleware",
)

# GZipMiddleware compresses dynamic responses (rendered HTML, JSON).
# WhiteNoise handles its own compression for static files.
# NOTE: GZip + HTTPS + reflected user input can be vulnerable to the BREACH
# attack. All sensitive endpoints here (magic-link verification) use tokens
# passed in URLs rather than reflected bodies; keep that in mind if adding
# authenticated pages that echo user-supplied content.
MIDDLEWARE.insert(  # noqa: F405
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,  # noqa: F405
    "django.middleware.gzip.GZipMiddleware",
)

STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

ALLOWED_HOSTS = config("ALLOWED_HOSTS").split(",")

# ---------------------------------------------------------------------------
# Database — expects DATABASE_URL in environment, e.g.:
#   postgresql://user:password@host:5432/dbname
# ---------------------------------------------------------------------------

import dj_database_url  # noqa: E402 — optional dep, add to requirements if needed

# CONN_HEALTH_CHECKS (SNOW-733) is load-bearing alongside CONN_MAX_AGE, not
# decoration. Persistent connections are reused for up to ten minutes without
# Django checking they are still open, so when Postgres closes one out of band
# — a restart, a failover, an idle timeout, a pooler recycling it — the next
# query dies on the dead socket *mid-flight* rather than at connect time:
#
#     OperationalError: consuming input failed: SSL error: unexpected eof
#
# Django then discards the broken connection, so the request after it
# succeeds and the whole thing reads as an unexplained one-off. Production
# /healthz returned 503 exactly this way on 2026-08-27. The health check ping
# turns that into a transparent reconnect, at the cost of one cheap round trip
# on a request that reuses a pooled connection.
#
# This covers staging too. staging.py does `from .production import *` and
# never overrides DATABASES["default"], so it inherits this connection config
# verbatim. That was easy to misread while staging.py still registered a
# second database alias with its own conn_max_age=0 (SNOW-736 removed it),
# and is the reason the guard in tests/config/test_database_settings.py
# asserts the pairing across every deployed overlay rather than trusting a
# per-module read.
DATABASES = {
    "default": dj_database_url.config(
        default=config("DATABASE_URL"),
        conn_max_age=600,
        conn_health_checks=True,
        ssl_require=True,
    )
}

# ---------------------------------------------------------------------------
# Cache — DatabaseCache is the baseline shared cache for django-ratelimit
# across workers.  Upgrade to Redis when traffic warrants.
# ---------------------------------------------------------------------------

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    },
}

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Render.com terminates TLS at the proxy and forwards requests to Django over
# HTTP. Without this header Django sees every request as plain HTTP, causing
# SECURE_SSL_REDIRECT redirect loops and http:// absolute URLs in emails.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# The health checks (SNOW-565) must answer the platform's prober, which
# reaches the instance directly rather than through the TLS-terminating
# proxy — so it does not set X-Forwarded-Proto, and SECURE_SSL_REDIRECT
# above would answer the probe with a 301 that Render scores as a failed
# check. Exempting the two paths keeps the redirect in force everywhere a
# browser can reach. Django matches these patterns against the path with
# the leading slash already stripped, hence the lstrip.
SECURE_REDIRECT_EXEMPT = [rf"^{path.lstrip('/')}$" for path in HEALTH_CHECK_PATHS]

# Trusted origins for CSRF — must match the production hostname(s).
# Comma-separated, e.g. "https://snowdesk.info,https://www.snowdesk.info".
CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    cast=lambda v: [s.strip() for s in v.split(",") if s.strip()],
)

# ---------------------------------------------------------------------------
# Content Security Policy — enabled, report-only initially
# ---------------------------------------------------------------------------
# Flip CSP_REPORT_ONLY=False to enforce once violation reports stabilise.
# Both flags are environment-overridable for a zero-redeploy kill switch
# if the enforcing policy causes an unexpected regression.
CSP_ENABLED = config("CSP_ENABLED", default=True, cast=bool)
CSP_REPORT_ONLY = config("CSP_REPORT_ONLY", default=True, cast=bool)

# ---------------------------------------------------------------------------
# django-tasks background task queue — production uses the ORM-backed backend
# ---------------------------------------------------------------------------
# DatabaseBackend persists tasks in the DB and retries on failure. Requires
# a Render Background Worker service running ``python manage.py db_worker``.
# Without an active worker, tasks accumulate in the DB but are not consumed.
# The base.py default (ImmediateBackend) acts as a safe fallback, but
# production explicitly sets DatabaseBackend for durability + off-thread send.

TASKS = {
    "default": {
        "BACKEND": "django_tasks_db.DatabaseBackend",
        "QUEUES": ["default"],
    }
}
