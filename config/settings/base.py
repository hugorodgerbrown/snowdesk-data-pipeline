"""
config/settings/base.py — Shared Django settings for all environments.

Contains everything that is environment-agnostic: installed apps, middleware,
template configuration, logging, static files, and i18n. Sensitive or
environment-specific values live in development.py / production.py and are
read from the environment via python-decouple.
"""

from datetime import date
from pathlib import Path

from decouple import config
from django.core.exceptions import ImproperlyConfigured

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

SECRET_KEY = config("SECRET_KEY")

# ---------------------------------------------------------------------------
# Release identifier
# ---------------------------------------------------------------------------
# Baked into ETags on cacheable pages so every deploy invalidates stale
# browser / CDN entries when template HTML, CSS, or view logic changes —
# not just when the underlying bulletin data changes. On Render.com the
# RENDER_GIT_COMMIT env var is auto-populated with the build commit SHA;
# locally it falls back to "dev" so the ETag is still stable across a
# development session.

RELEASE_VERSION = config(
    "RELEASE_VERSION",
    default=config("RENDER_GIT_COMMIT", default="dev"),
)

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "django_htmx",
    "csp",
    # Local
    "pipeline",
    "public",
    "subscriptions",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    # Exposes X-DB-Query-Count on responses when QUERY_COUNT_HEADER_ENABLED
    # is True (dev + perf). No-op otherwise, so it is safe to leave mounted
    # in production.
    "pipeline.middleware.QueryCountMiddleware",
    # Sets Referrer-Policy and Permissions-Policy on every response.
    # Per-view overrides (e.g. no-referrer on token-bearing views) are
    # applied by the view itself before this middleware runs.
    "pipeline.middleware.SecurityHeadersMiddleware",
    # django-csp-plus. NonceMiddleware populates request.csp_nonce (used by
    # inline <script nonce="…"> tags in templates); HeaderMiddleware emits
    # the Content-Security-Policy(-Report-Only) header. The nonce middleware
    # must run before any view that reads request.csp_nonce.
    "csp.middleware.CspNonceMiddleware",
    "csp.middleware.CspHeaderMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation."
        "UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-gb"
LANGUAGES = [("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# Default primary key
# ---------------------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Pipeline / data fetching
# ---------------------------------------------------------------------------
# Default --start-date for the fetch_bulletins management command. Set to
# the start of the avalanche season so a bare invocation captures the full
# snowpack build-up. Override via env when backfilling earlier seasons.

SEASON_START_DATE = config(
    "SEASON_START_DATE",
    default="2025-11-01",
    cast=date.fromisoformat,
)

# Base URL for the SLF CAAML bulletin-list endpoint. Promoted from a
# module constant so the ``fetch_bulletins`` command can flip between
# the live API and a local mirror that replays a stored archive.
SLF_API_BASE_URL = config(
    "SLF_API_BASE_URL",
    default="https://aws.slf.ch/api/bulletin-list/caaml",
)

# On-disk archive of every bulletin captured by ``fetch_bulletins
# --stash`` runs. NDJSON: one un-wrapped CAAML record per line, sorted
# ascending by ``validTime.startTime``, deduped by ``bulletinID``. Both
# the stash writer and the local mirror view read from this path.
SLF_ARCHIVE_PATH = BASE_DIR / "sample_data" / "slf_archive.ndjson"

# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------
# When True, ``pipeline.middleware.QueryCountMiddleware`` forces the debug
# cursor and writes an ``X-DB-Query-Count`` header on every response. Off
# by default so production pays no cost; development.py and perf.py turn
# it on so local pages and the ``monitor_query_counts`` command can see
# the numbers.

QUERY_COUNT_HEADER_ENABLED = config(
    "QUERY_COUNT_HEADER_ENABLED",
    default=False,
    cast=bool,
)

# ---------------------------------------------------------------------------
# Content Security Policy (django-csp-plus)
# ---------------------------------------------------------------------------
# Off by default — production.py flips CSP_ENABLED=True and initially runs
# in report-only mode so violations surface via the CspRule admin without
# breaking the page. Flip CSP_REPORT_ONLY=False once reports stabilise.
#
# The /admin/ surface is exempted: Django admin relies on many inline
# scripts and styles that would need per-tag nonces, and hardening admin
# is outside the scope of this change. Staff-only URL — low blast radius.
#
# The {report_uri} placeholder is replaced at request time with the local
# CSP report endpoint mounted under /csp/ in config/urls.py.

CSP_ENABLED = False
CSP_REPORT_ONLY = True
CSP_DEFAULTS = {
    "default-src": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
    "frame-ancestors": ["'none'"],
    "script-src": [
        "'self'",
        "'nonce-{nonce}'",
        "https://unpkg.com",
    ],
    # 'unsafe-inline' is required because (a) map.html uses inline style=""
    # attributes on legend swatches and the debug pill, and (b) map.js +
    # MapLibre GL set element.style programmatically, which CSP treats as
    # inline-style. Refactoring these into CSS classes is tracked as a
    # follow-up and is out of scope for the initial policy.
    "style-src": [
        "'self'",
        "'unsafe-inline'",
        "https://unpkg.com",
    ],
    "img-src": ["'self'", "data:"],
    "font-src": ["'self'", "data:"],
    # MapLibre creates its tile-parser workers from blob: URLs; /sw.js is
    # our own service worker (served from /).
    "worker-src": ["'self'", "blob:"],
    # MapLibre fetches the Liberty style + vector tiles from
    # tiles.openfreemap.org via fetch(); leave self in for XHRs issued
    # against our own API endpoints.
    "connect-src": [
        "'self'",
        "https://tiles.openfreemap.org",
    ],
    "manifest-src": ["'self'"],
    "report-uri": ["{report_uri}"],
}


def _csp_filter_request(request):  # type: ignore[no-untyped-def]
    """Skip CSP header emission for /admin/ — see note above."""
    return not request.path.startswith("/admin/")


CSP_FILTER_REQUEST_FUNC = _csp_filter_request


# ---------------------------------------------------------------------------
# Account-access token
# ---------------------------------------------------------------------------
# Maximum age (in seconds) for account-access tokens verified by
# subscriptions/services/token.py.  Defaults to 24 hours.

ACCOUNT_TOKEN_MAX_AGE = config("ACCOUNT_TOKEN_MAX_AGE", default=86400, cast=int)

# Base URL used when building absolute links in emails sent outside a request
# context (e.g. from management commands or background tasks).
SITE_BASE_URL = config("SITE_BASE_URL", default="http://localhost:8000")

# Run outbound email on a background daemon thread so SMTP round-trip does not
# block the request thread (closes the timing-side-channel on
# POST /subscribe/manage/, SNOW-26).  Tests force this False in
# tests/conftest.py so existing locmem mail.outbox assertions stay synchronous.
SUBSCRIPTIONS_EMAIL_ASYNC = config(
    "SUBSCRIPTIONS_EMAIL_ASYNC",
    default=True,
    cast=bool,
)

# ---------------------------------------------------------------------------
# Email — SMTP everywhere.  Dev uses Mailhog (localhost:1025, no auth, no
# TLS); prod uses Resend's SMTP relay (smtp.resend.com:587, STARTTLS).
# ---------------------------------------------------------------------------

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = config("EMAIL_HOST", default="localhost")
EMAIL_PORT = config("EMAIL_PORT", default=1025, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=False, cast=bool)

DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="noreply@snowdesk.ch")

# ---------------------------------------------------------------------------
# Map — basemap style JSON URL consumed by MapLibre on /map/
# ---------------------------------------------------------------------------
# Changing basemap is a rare, deliberate event, so the vendor URLs live
# in this catalogue and the env picks a key rather than a raw URL. The
# resolved URL is passed through ``public.views.map_view`` context and
# rendered onto the ``#map`` element as ``data-basemap-style``;
# ``static/js/map.js`` reads it from ``mapEl.dataset.basemapStyle``. To
# add a candidate: drop a new ``{key: url}`` entry here and set
# ``BASEMAP=<key>`` in ``.env``. An unknown key raises at startup.

BASEMAP_STYLES = {
    "openfreemap_liberty": "https://tiles.openfreemap.org/styles/liberty",
    "swisstopo_winter": (
        "https://vectortiles.geo.admin.ch/styles/"
        "ch.swisstopo.basemap-winter.vt/style.json"
    ),
    "swisstopo_light": (
        "https://vectortiles.geo.admin.ch/styles/"
        "ch.swisstopo.lightbasemap.vt/style.json"
    ),
}

BASEMAP = config("BASEMAP", default="openfreemap_liberty")

try:
    BASEMAP_STYLE_URL = BASEMAP_STYLES[BASEMAP]
except KeyError as exc:
    raise ImproperlyConfigured(
        f"BASEMAP={BASEMAP!r} is not a known basemap. "
        f"Valid keys: {sorted(BASEMAP_STYLES)}"
    ) from exc

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname} {name} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "filters": {
        "require_debug_true": {
            "()": "django.utils.log.RequireDebugTrue",
        },
        "require_debug_false": {
            "()": "django.utils.log.RequireDebugFalse",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
        "file_django": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOGS_DIR / "django.log",
            "maxBytes": 10 * 1024 * 1024,  # 10 MB
            "backupCount": 5,
            "formatter": "verbose",
        },
        "file_pipeline": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOGS_DIR / "pipeline.log",
            "maxBytes": 10 * 1024 * 1024,  # 10 MB
            "backupCount": 5,
            "formatter": "verbose",
        },
        "file_errors": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOGS_DIR / "errors.log",
            "maxBytes": 10 * 1024 * 1024,  # 10 MB
            "backupCount": 5,
            "formatter": "verbose",
            "level": "ERROR",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file_django"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["file_errors", "console"],
            "level": "ERROR",
            "propagate": False,
        },
        "pipeline": {
            "handlers": ["console", "file_pipeline", "file_errors"],
            "level": "DEBUG",
            "propagate": False,
        },
        "subscriptions": {
            "handlers": ["console", "file_pipeline", "file_errors"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console", "file_errors"],
        "level": "WARNING",
    },
}
