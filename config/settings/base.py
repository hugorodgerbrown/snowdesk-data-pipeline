"""
config/settings/base.py — Shared Django settings for all environments.

Contains everything that is environment-agnostic: installed apps, middleware,
template configuration, logging, static files, and i18n. Sensitive or
environment-specific values live in development.py / production.py and are
read from the environment via python-decouple.
"""

import logging
from datetime import UTC, date, datetime
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
# PWA version + kill-switch contract (SNOW-369, SNOW-372)
# ---------------------------------------------------------------------------
# Server-authoritative version + kill-switch state consumed by the PWA:
#
#   ``APP_VERSION``     — current build the server is serving. Reuses
#                         ``RELEASE_VERSION`` so an existing deploy pipeline
#                         only has to set one env var.
#   ``APP_MIN_VERSION`` — minimum client build the server will accept.
#                         Any client below this must force-update. Empty
#                         string disables the check (default), which is
#                         the correct behaviour until we have a client
#                         population to gate against.
#   ``APP_RELEASED_AT`` — ISO-8601 timestamp of when the current build was
#                         released. Defaults to process boot time on Render
#                         (matching deploy time within seconds); an explicit
#                         env var can override for deterministic tests.
#   ``SW_URL``          — path the client registers as its service worker.
#                         Flipping to ``/sw-kill.js`` swaps every client
#                         onto the kill-switch SW without a deploy
#                         (spec §6.4 Mechanism A escalation).
#   ``SW_KILL``         — when true, ``/api/sw-config`` returns kill=true
#                         and the client unregisters its SW without
#                         registering a new one.

APP_VERSION: str = RELEASE_VERSION
APP_MIN_VERSION: str = config("APP_MIN_VERSION", default="")
APP_RELEASED_AT: str = config(
    "APP_RELEASED_AT",
    default=datetime.now(UTC).isoformat(timespec="seconds"),
)
SW_URL: str = config("SW_URL", default="/sw.js")
SW_KILL: bool = config("SW_KILL", default=False, cast=bool)

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
    # Required by django.contrib.sitemaps (SNOW-218).
    "django.contrib.sites",
    "django.contrib.sitemaps",
    # Third-party
    "django_htmx",
    # Provides the ORM-backed task queue (DatabaseBackend) and the db_worker /
    # prune_db_task_results management commands. django_tasks (the decorator
    # package) does not need to be in INSTALLED_APPS per its README.
    "django_tasks_db",
    # ``core.apps.BootstrapTolerantCSPTrackerConfig`` is a thin subclass of
    # ``csp.apps.CSPTrackerConfig`` that tolerates a missing ``django_cache``
    # table on first boot — see core/apps.py for the why.
    "core.apps.BootstrapTolerantCSPTrackerConfig",
    "waffle",
    # Local
    "core",
    "regions",
    "bulletins",
    "public",
    "subscriptions",
    "analytics",
    "observations",
    "mcp_server",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    # Idempotency-Key deduplication for state-changing requests (SNOW-371).
    # Runs before AuthenticationMiddleware so a cache hit short-circuits
    # before any auth work, and after CsrfViewMiddleware so a cached
    # response is not served to a request that would have failed CSRF on
    # first execution — the original request already passed CSRF when the
    # row was cached, so a replay of an already-successful mutation is
    # safe to serve without a second CSRF check.
    "core.idempotency.IdempotencyMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # django-waffle. Reads request.user (populated by AuthenticationMiddleware
    # immediately above) so per-user / superuser / staff flag targeting works.
    # Adds ``request.waffles`` for view-side flag checks; mounts no URL conf
    # because we don't expose a wafflejs endpoint (no JS-side flag checks
    # yet). See ``docs/feature-flags.md``.
    "waffle.middleware.WaffleMiddleware",
    # PostHog request context middleware. Placed immediately after
    # WaffleMiddleware so request.user is available for identity tagging.
    # Short-circuits via POSTHOG_MW_REQUEST_FILTER when POSTHOG_API_KEY is
    # empty so it is a guaranteed no-op in development and test runs.
    "posthog.integrations.django.PosthogContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    # Exposes X-DB-Query-Count on responses when QUERY_COUNT_HEADER_ENABLED
    # is True (dev + perf). No-op otherwise, so it is safe to leave mounted
    # in production.
    "core.middleware.QueryCountMiddleware",
    # Sets Referrer-Policy and Permissions-Policy on every response.
    # Per-view overrides (e.g. no-referrer on token-bearing views) are
    # applied by the view itself before this middleware runs.
    "core.middleware.SecurityHeadersMiddleware",
    # Stamps X-App-Version and X-App-Min-Version on every response so the
    # PWA client can detect a forced-update state on any response, not just
    # a poll of /api/version (SNOW-369, spec §5.3).
    "core.middleware.AppVersionHeaderMiddleware",
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
                # Injects nav_subscriptions for the subscriber avatar dropdown.
                "subscriptions.context_processors.nav_subscriptions",
                # Exposes SITE_BASE_URL for absolute-URL construction in OG tags.
                "public.context_processors.site_base_url",
                # Injects APP_VERSION / APP_MIN_VERSION into every template so
                # base.html can bake them into <meta> tags for the client-side
                # version check (SNOW-374).
                "public.context_processors.pwa_version",
                # SNOW-399: injects SITE_ENVIRONMENT and the derived
                # SITE_NAME_DISPLAY / PWA_ICON_DIR / PWA_THEME_COLOR so
                # base.html can render a distinct app name, icon, and theme
                # colour on staging vs production PWA installs.
                "public.context_processors.site_environment",
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
# Authentication
# ---------------------------------------------------------------------------
# Django's default auth.User is the user model.  Subscribers are linked via
# a OneToOneField on subscriptions.Subscriber (related_name="subscriber").

AUTHENTICATION_BACKENDS = [
    # Verifies signed magic-link tokens; used by account_view and passkey auth.
    "subscriptions.backends.TokenBackend",
    # Standard Django password backend; used by the admin login form for staff.
    "django.contrib.auth.backends.ModelBackend",
]

# ---------------------------------------------------------------------------
# Analytics (PostHog)
# ---------------------------------------------------------------------------
# Server-side event capture via the posthog-python client and the official
# PosthogContextMiddleware. The global posthog module-level client is
# initialised in ``analytics/apps.py`` ``AppConfig.ready()``. The wrappers
# in ``analytics/__init__.py`` are no-ops when POSTHOG_API_KEY is empty so
# no events are sent during local development or test runs unless the key is
# explicitly populated. Set to the EU project key in production via the
# environment.

POSTHOG_API_KEY = config("POSTHOG_API_KEY", default="")
POSTHOG_HOST = config("POSTHOG_HOST", default="https://eu.i.posthog.com")

# Capture unhandled view exceptions as PostHog events (default True). Set to
# False to disable exception capture without removing the middleware.
POSTHOG_MW_CAPTURE_EXCEPTIONS = config(
    "POSTHOG_MW_CAPTURE_EXCEPTIONS", default=True, cast=bool
)

# Capture local-variable values from each frame of captured exception
# tracebacks (default False — opt-in). Stack traces only by default; set the
# env var to True after reviewing the privacy implications, since enabling this
# ships frame-local variable values to PostHog. PostHog applies built-in
# mask/ignore patterns to redact common credential names, but frame-locals may
# still contain PII — review before enabling in any environment.
POSTHOG_CAPTURE_EXCEPTION_CODE_VARIABLES = config(
    "POSTHOG_CAPTURE_EXCEPTION_CODE_VARIABLES", default=False, cast=bool
)


# Paths of cacheable public endpoints that must be exempt from
# PosthogContextMiddleware. The middleware reads ``request.user`` to tag
# identities whenever an API key is set; that access marks the session as
# accessed, causing ``SessionMiddleware.process_response`` to append
# ``Vary: Cookie`` to the response — which defeats the
# ``Cache-Control: public`` CDN caching these anonymous, high-volume
# endpoints are designed for.  Returning False from the filter
# short-circuits the middleware before ``request.user`` is touched.
#
# DESIGN DECISION — request-phase path filter vs. response-phase predicate:
# The session is accessed at *request* time (when PosthogContextMiddleware
# reads ``request.user``), so the ``Vary: Cookie`` header is already baked
# into the response before any response-phase middleware can inspect
# ``Cache-Control: public``.  A response-phase predicate keyed on
# ``Cache-Control: public`` therefore cannot prevent the corruption — it
# would see the header too late.  Stripping ``Vary: Cookie`` in a
# post-hoc middleware would be fragile (it could mask legitimate variation
# on other public pages).  The only correct fix is a *request-phase* path
# filter, which is what ``_posthog_request_filter`` below implements.
#
# SNOW-299: keep this set in sync with the ``@cache_control(public=True)``
# GET endpoints declared in ``public/api_urls.py``.
# SNOW-338: also keep in sync with the ``Cache-Control: public`` static
# routes declared in ``config/urls.py``.
_POSTHOG_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        # Map-data JSON/GeoJSON API endpoints (public/api_urls.py) — SNOW-299.
        "/api/ratings/",
        "/api/resorts-by-region/",
        "/api/resorts.geojson",
        "/api/regions.geojson",
        "/api/major-regions.geojson",
        "/api/sub-regions.geojson",
        "/api/bulletin-groupings.geojson",
        # Static public-good documents (config/urls.py) — SNOW-338.
        "/robots.txt",
        "/llms.txt",
        "/llms-full.txt",
        "/manifest.webmanifest",
        # Favicon has two routes: bare path and trailing-slash variant.
        "/favicon.ico",
        "/favicon.ico/",
        # NOTE — ``/sitemap.xml`` is deliberately NOT listed.  It sets no
        # ``Cache-Control: public`` header, and its ``Vary: Cookie`` is added
        # by Django's sitemap-view middleware path, not PosthogContextMiddleware
        # (verified: the header persists with POSTHOG_API_KEY unset).  Exempting
        # it from PostHog would therefore be dead config — it would not remove
        # the header nor make the response cacheable.  Making the sitemap a
        # genuine public-cacheable surface is tracked separately (SNOW-340).
    }
)


# Skip the middleware entirely when no API key is configured — avoids
# per-request context and tag work in dev/test with no key.
# The function is called at request time — importing django.conf.settings
# inside it is safe and reads the live value so @override_settings works.
def _posthog_request_filter(request: object) -> bool:
    """Return True only when POSTHOG_API_KEY is non-empty and the path is not exempt.

    Short-circuits on two conditions (returning False skips the middleware):

    1. ``POSTHOG_API_KEY`` is empty — no analytics in dev/test.
    2. The request path is in ``_POSTHOG_EXEMPT_PATHS`` — SNOW-299/SNOW-338:
       these cacheable public endpoints must not trigger a ``request.user``
       access, which would cause ``SessionMiddleware`` to append
       ``Vary: Cookie`` and defeat ``Cache-Control: public`` CDN caching.

    Reads ``django.conf.settings`` at call time so that ``@override_settings``
    in tests takes effect without capturing a stale module-level binding.
    """
    from django.conf import settings as _s  # noqa: PLC0415 — intentional late import

    if not (getattr(_s, "POSTHOG_API_KEY", "") or "").strip():
        return False
    return getattr(request, "path", "") not in _POSTHOG_EXEMPT_PATHS


POSTHOG_MW_REQUEST_FILTER = _posthog_request_filter


# Strip ``email`` from the user-tag dict before PostHog sees it to honour
# the PII invariant (email is never transmitted in event properties).
def _posthog_tag_map(tags: dict[str, object]) -> dict[str, object]:
    """Return ``tags`` with the ``email`` key removed."""
    return {k: v for k, v in tags.items() if k != "email"}


POSTHOG_MW_TAG_MAP = _posthog_tag_map

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
SLF_ARCHIVE_PATH = BASE_DIR / "bulletins" / "local_mirrors" / "slf_archive.ndjson"

# On-disk archive of every Open-Meteo weather record captured by
# ``fetch_weather --stash`` runs.
# NDJSON: one record per ``(region_id, date)`` pair per line, sorted
# ascending by ``(region_id, date)``, deduped by ``(region_id, date)``
# with the later ``captured_at`` winning. Both the stash writer and the
# local Open-Meteo mirror view read from this path.
OPENMETEO_ARCHIVE_PATH = (
    BASE_DIR / "bulletins" / "local_mirrors" / "openmeteo_archive.ndjson"
)

# ALBINA bulletin API. The CDN publishes per-date, per-region files
# at ``{base}/{date}/{date}_{region}_en_CAAMLv6.json``. Each file is a JSON
# array of bulletins (same shape the SLF list API returns). Covers the
# Tyrol / South Tyrol / Trentino EUREGIO area.
ALBINA_API_BASE_URL = config(
    "ALBINA_API_BASE_URL",
    default="https://static.avalanche.report/bulletins",
)

# On-disk archive of ALBINA bulletins captured from the avalanche.report CDN.
# NDJSON: one unwrapped CAAML record per line; deduped by ``bulletinID``.
ALBINA_ARCHIVE_PATH = BASE_DIR / "bulletins" / "local_mirrors" / "albina_archive.ndjson"

# ALBINA region identifiers covered by the fetcher. These map to the three
# top-level avalanche.report CDN paths: Tyrol (AT-07), South Tyrol (IT-32-BZ),
# and Trentino (IT-32-TN).
ALBINA_REGIONS: tuple[str, ...] = ("AT-07", "IT-32-BZ", "IT-32-TN")

# MeteoFrance / DPBRA bulletin API.
# Live endpoint:
#   GET {METEOFRANCE_API_BASE_URL}/massif/{id}/BRA
#   apikey: {METEOFRANCE_API_KEY}
# For local-mirror / integration testing set METEOFRANCE_API_LOCAL_MIRROR_URL
# to a ``file://`` directory URI; the fetcher then reads
# ``massif-{NN:03d}.xml`` files from that directory instead of calling the
# live APIM, so no API key is required.
METEOFRANCE_API_BASE_URL = config(
    "METEOFRANCE_API_BASE_URL",
    default="https://public-api.meteofrance.fr/public/DPBRA/v1",
)

METEOFRANCE_API_KEY = config("METEOFRANCE_API_KEY", default="")

# When non-empty, overrides METEOFRANCE_API_BASE_URL with a file:// URI so
# the fetcher reads from a local directory instead of calling the live API.
# Populated in development.py and by tests; empty in production.
METEOFRANCE_API_LOCAL_MIRROR_URL = config(
    "METEOFRANCE_API_LOCAL_MIRROR_URL",
    default="",
)

# On-disk archive of MeteoFrance bulletins captured by ``fetch_bulletins
# --stash`` runs. NDJSON: one translated CAAML record per line, sorted
# ascending by ``validTime.startTime``, deduped by ``bulletinID``.
METEOFRANCE_ARCHIVE_PATH = (
    BASE_DIR / "bulletins" / "local_mirrors" / "meteofrance_archive.ndjson"
)

# MeteoFrance DPBRA massif IDs covered by the fetcher.
# Alps: 1–23. Corse: 40–41. Pyrenees: 64–70, 72–74.
# Massif 71 (Andorre / Andorra) is delegated to the Spanish agency and raises
# MeteoFranceDelegatedRegionError — excluded here to keep the loop clean.
METEOFRANCE_MASSIF_IDS: tuple[int, ...] = (
    *range(1, 24),  # Alps (1–23)
    40,
    41,  # Corse
    *range(64, 71),  # Pyrenees first group (64–70)
    *range(72, 75),  # Pyrenees second group (72–74)
)

# ---------------------------------------------------------------------------
# GeoIP
# ---------------------------------------------------------------------------
# Path to the MaxMind GeoLite2-City mmdb database file. Used by
# ``bulletins.services.geoip.geo_lookup`` to resolve a client IP to country,
# subdivision, city, and coordinates at each request inflection point.
# Downloaded by ``bin/fetch-geoip-data`` on deploy and locally (see
# data/geoip/README.md). Set to None to disable GeoIP lookups (geo_lookup
# will return None for every IP).
#
# Credentials for downloading the GeoLite2-City database from MaxMind.
# Obtain a free account at https://www.maxmind.com/en/geolite2/signup.
# Leave empty to skip the download (local dev without a MaxMind account
# will still boot — geo fields will simply be empty).

GEOIP_PATH = BASE_DIR / "data" / "geoip" / "GeoLite2-City.mmdb"

MAXMIND_ACCOUNT_ID = config("MAXMIND_ACCOUNT_ID", default="")
MAXMIND_LICENSE_KEY = config("MAXMIND_LICENSE_KEY", default="")

# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------
# When True, ``core.middleware.QueryCountMiddleware`` forces the debug
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
        "{nonce}",
    ],
    # 'unsafe-inline' is required because (a) map.html uses inline style=""
    # attributes on legend swatches and the debug pill, and (b) map.js +
    # MapLibre GL set element.style programmatically, which CSP treats as
    # inline-style. Refactoring these into CSS classes is tracked as a
    # follow-up and is out of scope for the initial policy.
    "style-src": [
        "'self'",
        "'unsafe-inline'",
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
        # swisstopo winter/light styles + tiles.
        "https://vectortiles.geo.admin.ch",
        # Regional national basemaps: IGN Plan IGN (France) and
        # basemap.at (Austria) — style JSON, vector tiles, sprites, glyphs.
        "https://data.geopf.fr",
        "https://mapsneu.wien.gv.at",
    ],
    "manifest-src": ["'self'"],
    "report-uri": ["{report_uri}"],
}


def _csp_filter_request(request):  # type: ignore[no-untyped-def]
    """Skip CSP header emission for /admin/ — see note above."""
    return not request.path.startswith("/admin/")


CSP_FILTER_REQUEST_FUNC = _csp_filter_request


# ---------------------------------------------------------------------------
# WebAuthn / Passkeys (FIDO2)
# ---------------------------------------------------------------------------
# RP_ID must exactly match the domain served — e.g. "snowdesk.info".
# ORIGIN must be the full https:// origin — e.g. "https://snowdesk.info".
# Both default to localhost values so development works without extra config.

WEBAUTHN_RP_ID = config("WEBAUTHN_RP_ID", default="localhost")
WEBAUTHN_RP_NAME = config("WEBAUTHN_RP_NAME", default="Snowdesk")
WEBAUTHN_ORIGIN = config("WEBAUTHN_ORIGIN", default="http://localhost:8000")


# ---------------------------------------------------------------------------
# Feature flags (django-waffle)
# ---------------------------------------------------------------------------
# Server-side feature flagging via the ``waffle`` app. Flags target users
# (``superusers``, ``staff``, individual ``users``, ``groups``, percentages)
# and live in the DB; toggle them at ``/admin/waffle/flag/``. New flags are
# introduced via a data migration in the relevant app's ``migrations/`` (see
# ``docs/feature-flags.md`` for the template).
#
# ``WAFFLE_FLAG_DEFAULT = False`` — a flag with no DB row evaluates to off.
# This is the only safe default: a typo in a ``flag_is_active(...)`` call
# fails closed instead of silently exposing the gated code path.
#
# ``WAFFLE_CREATE_MISSING_FLAGS = False`` — looking up an unknown flag must
# not auto-create it. Flag rows are intentional configuration; we want them
# created via the admin or a migration so reviewers see them in the diff.

WAFFLE_FLAG_DEFAULT = False
WAFFLE_CREATE_MISSING_FLAGS = False


# ---------------------------------------------------------------------------
# Account-access token
# ---------------------------------------------------------------------------
# Maximum age (in seconds) for account-access tokens verified by
# subscriptions/services/token.py.  Defaults to 24 hours.

ACCOUNT_TOKEN_MAX_AGE = config("ACCOUNT_TOKEN_MAX_AGE", default=86400, cast=int)

# Base URL used when building absolute links in emails sent outside a request
# context (e.g. from management commands or background tasks).
SITE_BASE_URL = config("SITE_BASE_URL", default="http://localhost:8000")

# Human-readable site name — used in structured data (JSON-LD), email
# subjects, and any other context that needs the brand string.
SITE_NAME = "Snowdesk"

# SNOW-399: environment label used to make a staging PWA install visibly
# distinct from a production one — different app name, theme colour, and
# icon set so the two home-screen icons can be told apart at a glance.
# Anything other than "production" is treated as a non-production install
# by the PWA manifest view (``public.views.serve_manifest``) and the site
# ``<head>`` (``public/templates/public/base.html``).
SITE_ENVIRONMENT = config("SITE_ENVIRONMENT", default="production")

# django.contrib.sites — required by django.contrib.sitemaps (SNOW-218).
# Set to 1 (the default "example.com" site created by the sites migration).
# Overridden at deploy time to match the production domain via
# the django_site fixture or by editing the Site table directly.
SITE_ID = 1

# ---------------------------------------------------------------------------
# django-tasks background task queue
# ---------------------------------------------------------------------------
# Base default: ImmediateBackend — tasks run synchronously in the same process.
# This is a safe fallback (email is sent, just not off-thread) that prevents
# silent message loss if a deployment forgets to override to DatabaseBackend.
# development.py also sets ImmediateBackend explicitly (inline send into Mailhog).
# production.py overrides this to DatabaseBackend for durability + off-thread
# dispatch (requires a Render Background Worker running ``db_worker``).

TASKS = {
    "default": {
        "BACKEND": "django_tasks.backends.immediate.ImmediateBackend",
    }
}

# Warm weather snapshots on a background daemon thread when bulletin_detail
# renders a past-date page with no snapshot (SNOW-164). Default True; tests
# pin this False in tests/conftest.py so the fetch runs synchronously and
# the test assertion sees the written snapshot.
WEATHER_FETCH_ASYNC = config(
    "WEATHER_FETCH_ASYNC",
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
    # Regional national basemaps — a per-country equivalent to the
    # swisstopo styles (which cover CH only). Both are self-contained
    # MapLibre v8 style JSONs (absolute sprite/glyph URLs), free and
    # token-less. Coverage is national — outside their country they render
    # blank, so they are a comparison aid, not a replacement for the global
    # Standard style. IGN "Plan IGN" covers France; basemap.at covers Austria.
    # Italy (South Tyrol / Trentino) publishes only raster WMTS, which needs a
    # hand-built style object rather than a URL, so it is not included here.
    "ign_plan": (
        "https://data.geopf.fr/annexes/ressources/vectorTiles/styles/"
        "PLAN.IGN/standard.json"
    ),
    "basemap_at": "https://mapsneu.wien.gv.at/basemapvectorneu/root.json",
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
        "core": {
            "handlers": ["console", "file_pipeline", "file_errors"],
            "level": "DEBUG",
            "propagate": False,
        },
        "regions": {
            "handlers": ["console", "file_pipeline", "file_errors"],
            "level": "DEBUG",
            "propagate": False,
        },
        "bulletins": {
            "handlers": ["console", "file_pipeline", "file_errors"],
            "level": "DEBUG",
            "propagate": False,
        },
        "subscriptions": {
            "handlers": ["console", "file_pipeline", "file_errors"],
            "level": "DEBUG",
            "propagate": False,
        },
        # Python `warnings.warn(...)` calls are routed here via
        # `logging.captureWarnings(True)` below, so DeprecationWarning and
        # friends land in errors.log alongside everything else.
        "py.warnings": {
            "handlers": ["console", "file_errors"],
            "level": "WARNING",
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console", "file_errors"],
        "level": "WARNING",
    },
}

logging.captureWarnings(True)
