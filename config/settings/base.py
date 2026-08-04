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
from urllib.parse import urlsplit

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
# PWA version + kill-switch contract (SNOW-369, SNOW-372, SNOW-609)
# ---------------------------------------------------------------------------
# Server-authoritative version + kill-switch state consumed by the PWA:
#
#   ``APP_VERSION``          — current build the server is serving. Reuses
#                              ``RELEASE_VERSION`` so an existing deploy
#                              pipeline only has to set one env var.
#   ``APP_BLOCKED_VERSIONS`` — comma-separated set of build identifiers the
#                              server refuses to serve. A client whose
#                              ``X-Client-Version`` is a member is told to
#                              force-update via ``/api/version``'s
#                              ``update_required``. Empty (the default) blocks
#                              nobody. SNOW-609 replaced the previous
#                              ``APP_MIN_VERSION`` floor: ``APP_VERSION``
#                              resolves to a git SHA, and SHAs have no
#                              ordering, so a minimum version was not
#                              expressible on either side of the wire — see
#                              docs/decisions/blocked-builds-not-a-version-floor.md.
#   ``APP_RELEASED_AT``      — ISO-8601 timestamp of when the current build was
#                              released. Defaults to process boot time on Render
#                              (matching deploy time within seconds); an explicit
#                              env var can override for deterministic tests.
#   ``SW_URL``               — path the client registers as its service worker.
#                              Flipping to ``/sw-kill.js`` swaps every client
#                              onto the kill-switch SW without a deploy
#                              (spec §6.4 Mechanism A escalation).
#   ``SW_KILL``              — when true, ``/api/sw-config`` returns kill=true
#                              and the client unregisters its SW without
#                              registering a new one.


def comma_separated_frozenset(raw: str) -> frozenset[str]:
    """Parse a comma-separated env value into a frozenset of trimmed entries.

    Empty entries (from a trailing comma, or an entirely empty value) are
    dropped, so ``""`` yields an empty frozenset rather than ``{""}`` —
    which matters for ``APP_BLOCKED_VERSIONS``, where a stray empty-string
    member would match a client that sent no version at all.

    Args:
        raw: The raw env value, e.g. ``"abc123, def456"``.

    Returns:
        The trimmed, de-duplicated entries.

    """
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


APP_VERSION: str = RELEASE_VERSION
APP_BLOCKED_VERSIONS: frozenset[str] = config(
    "APP_BLOCKED_VERSIONS",
    default="",
    cast=comma_separated_frozenset,
)
APP_RELEASED_AT: str = config(
    "APP_RELEASED_AT",
    default=datetime.now(UTC).isoformat(timespec="seconds"),
)
SW_URL: str = config("SW_URL", default="/sw.js")
SW_KILL: bool = config("SW_KILL", default=False, cast=bool)

# ---------------------------------------------------------------------------
# Dev-only shell-cache bypass (SNOW-585)
# ---------------------------------------------------------------------------
# After a ``git pull`, the previous service worker stays in control (it
# deliberately never calls ``skipWaiting()`` — see the "Update contract" in
# ``static/js/sw.js``) and keeps serving the old shell out of its
# ``CACHE_VERSION`` cache, even though the page looks up to date. Turning
# this on makes ``_staleWhileRevalidate`` skip the cache entirely — no read,
# no write — so the very next reload always serves the current bytes off
# disk, including from a worker that hasn't picked up the new one yet.
#
# ``DEBUG`` alone is the wrong key: ``tox -e e2e`` runs under
# ``config.settings.development`` (``DEBUG = True``) and asserts the update
# banner + ``pwa.sw.update_available`` fire, both of which this bypass
# suppresses (see ``static/js/sw_register.js::showUpdateBanner`` and
# ``static/js/pwa_version_check.js::showSoftBanner``). So this is a named
# setting: defaults False here, development.py flips the default to True,
# and the e2e tox env pins it back to False so that suite keeps testing
# production semantics. ``apps.core.checks`` errors if this is ever True
# with ``DEBUG`` off — see that module for the release gate.
SW_DEV_SHELL_BYPASS: bool = config("SW_DEV_SHELL_BYPASS", default=False, cast=bool)

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
    # ``apps.core.apps.BootstrapTolerantCSPTrackerConfig`` is a thin subclass of
    # ``csp.apps.CSPTrackerConfig`` that tolerates a missing ``django_cache``
    # table on first boot — see apps/core/apps.py for the why.
    "apps.core.apps.BootstrapTolerantCSPTrackerConfig",
    "waffle",
    # Local
    "apps.core",
    "apps.regions",
    "apps.bulletins",
    "apps.public",
    "apps.accounts",
    "apps.analytics",
    "apps.observations",
    "apps.favourites",
    "apps.mcp_server",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Idempotency-Key deduplication for state-changing requests (SNOW-371,
    # fingerprint hardening SNOW-463). Runs after AuthenticationMiddleware
    # so the principal fingerprint (request.user.pk) is available — the
    # cached record is only replayed when method, path, principal, and
    # body hash all match the original request. Still after
    # CsrfViewMiddleware, so a cached response is not served to a request
    # that would have failed CSRF on first execution — the original
    # request already passed CSRF when the row was cached, so a replay of
    # an already-successful mutation is safe to serve without a second
    # CSRF check.
    "apps.core.idempotency.IdempotencyMiddleware",
    # django-waffle. Reads request.user (populated by AuthenticationMiddleware
    # above) so per-user / superuser / staff flag targeting works.
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
    "apps.core.middleware.QueryCountMiddleware",
    # Sets Referrer-Policy and Permissions-Policy on every response.
    # Per-view overrides (e.g. no-referrer on token-bearing views) are
    # applied by the view itself before this middleware runs.
    "apps.core.middleware.SecurityHeadersMiddleware",
    # Stamps X-App-Version on every response so the PWA client can notice a
    # new build on any response, not just a poll of /api/version (SNOW-369,
    # spec §5.3). SNOW-609 dropped the companion X-App-Min-Version header —
    # the forced-update verdict is now a server decision returned by
    # /api/version, never a client-side version comparison.
    "apps.core.middleware.AppVersionHeaderMiddleware",
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
                "apps.accounts.context_processors.nav_subscriptions",
                # SNOW-549: injects PWA_USER_ID (Account.uuid) so base.html can
                # bake the signed-in user's public identifier into the
                # pwa-user-id meta tag the mutation queue reads as its
                # principal — never the sequential auth.User PK.
                "apps.accounts.context_processors.pwa_user_identity",
                # Exposes SITE_BASE_URL for absolute-URL construction in OG tags.
                "apps.public.context_processors.site_base_url",
                # Injects APP_VERSION into every template so base.html can
                # bake it into a <meta> tag for the client-side version
                # check (SNOW-374; SNOW-609 removed APP_MIN_VERSION).
                "apps.public.context_processors.pwa_version",
                # Injects PWA_TELEMETRY_ENABLED so base.html can bake the
                # telemetry master switch into a <meta> tag read by
                # static/js/telemetry.js (docs/telemetry-pipeline.md).
                "apps.public.context_processors.pwa_telemetry",
                # SNOW-585: injects SW_DEV_SHELL_BYPASS so base.html can bake
                # the dev-only shell-cache bypass flag into a <meta> tag read
                # synchronously at startup by sw_register.js and
                # pwa_version_check.js (docs/decisions/dev-bypasses-the-shell-cache.md).
                "apps.public.context_processors.pwa_dev_shell_bypass",
                # SNOW-399: injects SITE_ENVIRONMENT and the derived
                # SITE_NAME_DISPLAY / PWA_ICON_DIR / PWA_THEME_COLOR so
                # base.html can render a distinct app name, icon, and theme
                # colour on staging vs production PWA installs.
                "apps.public.context_processors.site_environment",
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
# Django's default auth.User is the user model.  Public-user accounts are
# linked via a OneToOneField on accounts.Account (related_name="account").

AUTHENTICATION_BACKENDS = [
    # Verifies signed magic-link tokens; used by account_view and passkey auth.
    "apps.accounts.backends.TokenBackend",
    # Standard Django password backend; used by the admin login form for staff.
    "django.contrib.auth.backends.ModelBackend",
]

# ---------------------------------------------------------------------------
# Analytics (PostHog)
# ---------------------------------------------------------------------------
# Server-side event capture via the posthog-python client and the official
# PosthogContextMiddleware. The global posthog module-level client is
# initialised in ``apps/analytics/apps.py`` ``AppConfig.ready()``. The wrappers
# in ``apps/analytics/__init__.py`` are no-ops when POSTHOG_API_KEY is empty so
# no events are sent during local development or test runs unless the key is
# explicitly populated. Set to the EU project key in production via the
# environment.

POSTHOG_API_KEY = config("POSTHOG_API_KEY", default="")
POSTHOG_HOST = config("POSTHOG_HOST", default="https://eu.i.posthog.com")

# ---------------------------------------------------------------------------
# PWA telemetry master switch
# ---------------------------------------------------------------------------
# Single authoritative kill switch for the first-party PWA telemetry
# pipeline (spec §16, docs/telemetry-pipeline.md). When False:
#
#   * ``static/js/telemetry.js`` becomes an inert no-op — no event
#     buffering, no ``navigator.sendBeacon``, no ``/api/telemetry`` POSTs
#     (the ``pwa-telemetry-enabled`` meta tag in base.html carries the
#     value to the client via ``apps.public.context_processors.pwa_telemetry``).
#   * The ``/api/telemetry`` receiver accepts and drops (still 204 so a
#     stale shell drains its local queue cleanly).
#   * The server-emitted §16.2 signals (``emit_server_signal``) no-op.
#
# This is distinct from ``POSTHOG_API_KEY`` (which only stops *forwarding*
# to PostHog) and from the per-user opt-in (spec §16.6): this switch is an
# operator/deploy-level off switch that silences the whole pipeline,
# including the operational-safety "critical" events. Default True; set
# ``PWA_TELEMETRY_ENABLED=False`` in ``.env`` to silence telemetry when
# running locally.
PWA_TELEMETRY_ENABLED: bool = config("PWA_TELEMETRY_ENABLED", default=True, cast=bool)

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
# GET endpoints declared in ``apps/public/api_urls.py``.
# SNOW-338: also keep in sync with the ``Cache-Control: public`` static
# routes declared in ``config/urls.py``.
# Health-check probe paths (SNOW-565), named once because four places have
# to agree on them: the URLconf, ``_POSTHOG_EXEMPT_PATHS`` below,
# ``SECURE_REDIRECT_EXEMPT`` in production.py, and ``healthCheckPath`` in
# render.yaml. The first three derive from this tuple; render.yaml is
# outside Python and is covered by a test instead.
HEALTH_CHECK_PATHS: tuple[str, ...] = ("/livez", "/healthz")

_POSTHOG_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        # Map-data JSON/GeoJSON API endpoints (apps/public/api_urls.py) — SNOW-299.
        "/api/ratings/",
        "/api/resorts-by-region/",
        "/api/resorts.geojson",
        "/api/regions.geojson",
        "/api/major-regions.geojson",
        "/api/sub-regions.geojson",
        "/api/bulletin-groupings.geojson",
        # SNOW-521: full basemap_download blob for one region (id-keyed,
        # not country-filtered) — same static-reference-data caching
        # rationale as the geojson endpoints above.
        "/api/region-basemap-tiles/",
        # SNOW-419's community-reports overlay is deliberately NOT listed.
        # SNOW-459 made it private/no-store (its waffle-flag gate is per-user,
        # so the response can't be shared-cached), so there is no
        # Cache-Control: public for Vary: Cookie to defeat — exempting it here
        # would be dead config. It returns to this set only if the gate
        # becomes global and public caching is restored (SNOW-469).
        # Health checks (config/urls.py) — SNOW-565, spliced in from
        # HEALTH_CHECK_PATHS above. These are exempt for a DIFFERENT reason
        # from every other entry in this set: the rest are here so
        # ``Vary: Cookie`` cannot defeat CDN caching, and the health checks
        # are ``no-store``, so that rationale does not apply to them. They
        # are listed because PosthogContextMiddleware reads ``request.user``
        # for identity tagging, and that access triggers a session lookup —
        # a database query on every probe. On ``/livez`` that would silently
        # reintroduce the database dependency the endpoint exists to avoid;
        # on both it is per-probe work and per-probe analytics noise from an
        # unattended prober.
        *HEALTH_CHECK_PATHS,
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
SLF_ARCHIVE_PATH = (
    BASE_DIR / "apps" / "bulletins" / "local_mirrors" / "slf_archive.ndjson"
)

# On-disk archive of every Open-Meteo weather record captured by
# ``fetch_weather --stash`` runs.
# NDJSON: one record per ``(region_id, date)`` pair per line, sorted
# ascending by ``(region_id, date)``, deduped by ``(region_id, date)``
# with the later ``captured_at`` winning. Both the stash writer and the
# local Open-Meteo mirror view read from this path.
OPENMETEO_ARCHIVE_PATH = (
    BASE_DIR / "apps" / "bulletins" / "local_mirrors" / "openmeteo_archive.ndjson"
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
ALBINA_ARCHIVE_PATH = (
    BASE_DIR / "apps" / "bulletins" / "local_mirrors" / "albina_archive.ndjson"
)

# ALBINA region identifiers covered by the fetcher. These map to the three
# top-level avalanche.report CDN paths: Tyrol (AT-07), South Tyrol (IT-32-BZ),
# and Trentino (IT-32-TN).
ALBINA_REGIONS: tuple[str, ...] = ("AT-07", "IT-32-BZ", "IT-32-TN")

# Open-Meteo weather / elevation API (SNOW-577).
# Live endpoints:
#   GET {OPEN_METEO_API_BASE_URL}/elevation
#   GET {OPEN_METEO_API_BASE_URL}/forecast
#   GET {OPEN_METEO_ARCHIVE_BASE_URL}/archive
# The defaults are the free public hosts, which need no key and enforce a
# shared per-IP quota (600/min, 5,000/hour, 10,000/day) across all three.
# A paid subscription is served from its own hostnames and authenticates
# with an ``apikey`` query parameter, so cutting over is an environment
# change on Render — no deploy required. The documented customer hosts are
# https://customer-api.open-meteo.com/v1 and
# https://customer-archive-api.open-meteo.com/v1; confirm them against the
# subscription confirmation before setting them.
#
# The two hosts may sit on different tiers: the key is sent only to a host
# that has been moved off its free default (SNOW-579), so setting the
# archive host alone keeps forecast and elevation free and unkeyed.
OPEN_METEO_API_BASE_URL = config(
    "OPEN_METEO_API_BASE_URL",
    default="https://api.open-meteo.com/v1",
)

OPEN_METEO_ARCHIVE_BASE_URL = config(
    "OPEN_METEO_ARCHIVE_BASE_URL",
    default="https://archive-api.open-meteo.com/v1",
)

# Empty means the free tier: no ``apikey`` parameter is sent at all.
OPEN_METEO_API_KEY = config("OPEN_METEO_API_KEY", default="")

# Whether the scheduled ``fetch_weather`` run also retains a
# ForecastPointWeatherHistory row per stored day (SNOW-575).
#
# History is analysis data for future forecast-convergence work — nothing
# user-facing reads it, and it grows by one row per point per day of each
# point's window. This setting is what ``schedule.py`` reads to decide
# whether to pass ``--add-history``, so the retention can be turned on or
# off by changing the Render environment variable and restarting the
# scheduler — no deploy required. Ad-hoc runs pass the flag directly.
FETCH_WEATHER_ADD_HISTORY = config(
    "FETCH_WEATHER_ADD_HISTORY",
    default=False,
    cast=bool,
)

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
    BASE_DIR / "apps" / "bulletins" / "local_mirrors" / "meteofrance_archive.ndjson"
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
# ``apps.bulletins.services.geoip.geo_lookup`` to resolve a client IP to country,
# subdivision, city, and coordinates at each request inflection point.
# Downloaded by ``bin/fetch-geoip-data`` on deploy and locally (see
# reference_data/geoip/README.md). Set to None to disable GeoIP lookups
# (geo_lookup will return None for every IP).
#
# Credentials for downloading the GeoLite2-City database from MaxMind.
# Obtain a free account at https://www.maxmind.com/en/geolite2/signup.
# Leave empty to skip the download (local dev without a MaxMind account
# will still boot — geo fields will simply be empty).

GEOIP_PATH = BASE_DIR / "reference_data" / "geoip" / "GeoLite2-City.mmdb"

MAXMIND_ACCOUNT_ID = config("MAXMIND_ACCOUNT_ID", default="")
MAXMIND_LICENSE_KEY = config("MAXMIND_LICENSE_KEY", default="")

# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------
# When True, ``apps.core.middleware.QueryCountMiddleware`` forces the debug
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

# Basemap origin (SNOW-242) — env-configurable so production can flip the
# OpenFreeMap Liberty basemap onto a self-hosted origin (SNOW-485) without a
# code deploy. Defaults to the public volunteer tier. The CSP connect-src
# origin below is derived from this same value so the two never drift.
OPENFREEMAP_STYLE_URL = config(
    "OPENFREEMAP_STYLE_URL",
    default="https://tiles.openfreemap.org/styles/liberty",
)
_ofm_parts = urlsplit(OPENFREEMAP_STYLE_URL)
if not _ofm_parts.scheme or not _ofm_parts.netloc:
    raise ImproperlyConfigured(
        f"OPENFREEMAP_STYLE_URL={OPENFREEMAP_STYLE_URL!r} must be an absolute "
        f"URL (e.g. https://tiles.openfreemap.org/styles/liberty)."
    )
OPENFREEMAP_ORIGIN = f"{_ofm_parts.scheme}://{_ofm_parts.netloc}"
del _ofm_parts

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
    # MapLibre fetches the Liberty style + vector tiles from the OpenFreeMap
    # origin via fetch(); derived from OPENFREEMAP_STYLE_URL (env-configurable,
    # SNOW-242) so the two settings never drift. Leave self in for XHRs
    # issued against our own API endpoints.
    "connect-src": [
        "'self'",
        OPENFREEMAP_ORIGIN,
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
# apps/accounts/services/token.py.  Defaults to 24 hours.

ACCOUNT_TOKEN_MAX_AGE = config("ACCOUNT_TOKEN_MAX_AGE", default=86400, cast=int)


# ---------------------------------------------------------------------------
# Favourites (SNOW-413)
# ---------------------------------------------------------------------------
# Maximum number of Favourite rows a single user may hold at once, enforced
# by apps.favourites.services.create_favourite.

FAVOURITES_MAX_PER_USER = config("FAVOURITES_MAX_PER_USER", default=25, cast=int)


# ---------------------------------------------------------------------------
# Field observations (SNOW-508)
# ---------------------------------------------------------------------------
# Radius, in kilometres, used by FieldObservation.objects.counts_near_point_for_day
# to scope reports to "near" a point (e.g. a resort). Configurable so the
# radius can be tightened without a code change.

FIELD_OBSERVATION_RADIUS_KM = config(
    "FIELD_OBSERVATION_RADIUS_KM", default=10.0, cast=float
)

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
# by the PWA manifest view (``apps.public.views.serve_manifest``) and the site
# ``<head>`` (``apps/public/templates/public/base.html``).
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
# development.py also sets ImmediateBackend explicitly (inline send into Mailpit).
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
# Email — SMTP everywhere.  Dev uses Mailpit (localhost:1025, no auth, no
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
# resolved catalogue is passed through ``apps.public.views.map_view`` context
# and rendered as one ``<button data-basemap-key data-basemap-url>`` per
# style inside the ``#basemap-menu`` popover (SNOW-58); the env-resolved
# default key is rendered as ``data-default-basemap-key`` on ``#map``.
# ``static/js/map.js`` reads the catalogue from the menu's DOM at boot and
# resolves the active style from localStorage × the default key. To add a
# candidate: drop a new ``{key: url}`` entry here and set ``BASEMAP=<key>``
# in ``.env``. An unknown key raises at startup.
# ``openfreemap_liberty`` reads its URL from ``OPENFREEMAP_STYLE_URL``
# (SNOW-242) so its origin can move to a self-hosted deployment
# (SNOW-485) without a code deploy.

BASEMAP_STYLES = {
    "openfreemap_liberty": OPENFREEMAP_STYLE_URL,
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
        "apps.core": {
            "handlers": ["console", "file_pipeline", "file_errors"],
            "level": "DEBUG",
            "propagate": False,
        },
        "apps.regions": {
            "handlers": ["console", "file_pipeline", "file_errors"],
            "level": "DEBUG",
            "propagate": False,
        },
        "apps.bulletins": {
            "handlers": ["console", "file_pipeline", "file_errors"],
            "level": "DEBUG",
            "propagate": False,
        },
        "apps.accounts": {
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
