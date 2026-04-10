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
# Security headers
# ---------------------------------------------------------------------------

SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
