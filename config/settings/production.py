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

DATABASES = {
    "default": dj_database_url.config(
        default=config("DATABASE_URL"),
        conn_max_age=600,
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

# Trusted origins for CSRF — must match the production hostname(s).
# Comma-separated, e.g. "https://snowdesk.info,https://www.snowdesk.info".
CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    cast=lambda v: [s.strip() for s in v.split(",") if s.strip()],
)
