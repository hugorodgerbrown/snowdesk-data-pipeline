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

# ---------------------------------------------------------------------------
# Human-readable release number
# ---------------------------------------------------------------------------
# ``APP_VERSION`` above is a git SHA. It is the right identity for a machine —
# it changes on every deploy and names one build exactly, which is what the
# update check, the blocked-builds list and the ETags all need — and the
# wrong one for a person: "which version are you on?" cannot be answered with
# forty hex characters.
#
# ``APP_RELEASE`` is that second, human identity: the ordinal of the
# production release, shown in the account menu as ``v24``. The two are
# deliberately separate. Collapsing them would mean either a machine identity
# too coarse to tell two builds of one release apart, or a human identity
# nobody can read.
#
# It is read from the tracked ``VERSION`` file rather than from a tag or the
# environment, and that is the whole point of the design. Render's build gets
# ``RENDER_GIT_COMMIT`` and no tags; ``release.yml`` creates the CalVer tag
# AFTER the deploy has already started; and a redeployed commit would count
# tags differently from its first deploy. A file in the tree has none of
# those problems — it is present, identical and unambiguous at build time on
# every tier. ``bin/cut-release`` refuses to ship a release whose number has
# not moved, which is what keeps it honest.
#
# The env var wins when set, so a test or a one-off container can pin a value
# without editing the file.


def _release_from_file() -> str:
    """Read the release number from the tracked ``VERSION`` file.

    Returns:
        The stripped contents, or ``""`` when the file is missing or
        unreadable — an unnumbered build simply shows no version rather
        than failing to boot over a cosmetic string.

    """
    try:
        return (BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


APP_RELEASE: str = config("APP_RELEASE", default=_release_from_file())
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
    # Provides the ``naturaltime`` filter — the relative "2 hours ago" form
    # the map's field-observation rows report their age in.
    "django.contrib.humanize",
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
    "apps.locations",
    "apps.regions",
    "apps.weather",
    "apps.bulletins",
    "apps.public",
    "apps.accounts",
    "apps.analytics",
    "apps.observations",
    "apps.favourites",
    "apps.routes",
    "apps.downloads",
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
    # SNOW-791: honours ?icons=<name> in DEBUG so the candidate weather icon
    # sets can be compared against real data without a restart. Inert when
    # DEBUG is off.
    "apps.core.middleware.WeatherIconSetMiddleware",
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
                # SNOW-549: injects PWA_USER_ID (Account.uuid) so base.html can
                # bake the signed-in user's public identifier into the
                # pwa-user-id meta tag the mutation queue reads as its
                # principal — never the sequential auth.User PK.
                "apps.accounts.context_processors.pwa_user_identity",
                # Exposes SITE_BASE_URL for absolute-URL construction in OG tags.
                # SNOW-791: injects WEATHER_ICON_DIR so the weather partials and
                # the map layer resolve icon paths against the active set.
                "apps.public.context_processors.weather_icon_set",
                "apps.public.context_processors.site_base_url",
                # Injects APP_VERSION into every template so base.html can
                # bake it into a <meta> tag for the client-side version
                # check (SNOW-374; SNOW-609 removed APP_MIN_VERSION).
                "apps.public.context_processors.pwa_version",
                # Injects PWA_TELEMETRY_ENABLED so base.html can bake the
                # telemetry master switch into a <meta> tag read by
                # static/js/telemetry.js (docs/telemetry-pipeline.md).
                "apps.public.context_processors.pwa_telemetry",
                # SNOW-812: injects debug_log_visible (the debug_log waffle
                # flag, scoped to GRP_DEBUG) so base.html can decide whether
                # to render the on-device debug-trace panel and load its
                # recorder at all. A context processor because the panel is a
                # universal surface, not one view's.
                "apps.public.context_processors.debug_log_visible",
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
        # SNOW-761: the map's Weather overlay feed. Public and shared-cached
        # for the same reason as the geojson endpoints above, so it needs the
        # same exemption or Vary: Cookie defeats it.
        "/api/weather.geojson",
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
# Open-Meteo elevation API — apps/locations
# ---------------------------------------------------------------------------
# SNOW-762 stripped the weather app; what survives is the elevation
# lookup, which is location domain (how high a Location is) rather than
# weather. The env var names are unchanged — nothing on Render needs
# editing, though the archive host and history flag are no longer read.
#
# Live endpoint:
#   GET {OPEN_METEO_API_BASE_URL}/elevation
#
# The default is the free public host, which needs no key and enforces a
# shared per-IP quota (600/min, 5,000/hour, 10,000/day). A paid
# subscription is served from its own hostname and authenticates with an
# ``apikey`` query parameter, so cutting over is an environment change on
# Render — no deploy required. The documented customer host is
# https://customer-api.open-meteo.com/v1; confirm it against the
# subscription confirmation before setting it. The key is sent only to a
# host that has been moved off its free default (SNOW-579).
OPEN_METEO_API_BASE_URL = config(
    "OPEN_METEO_API_BASE_URL",
    default="https://api.open-meteo.com/v1",
)

# Empty means the free tier: no ``apikey`` parameter is sent at all.
OPEN_METEO_API_KEY = config("OPEN_METEO_API_KEY", default="")

# ---------------------------------------------------------------------------
# Open-Meteo historical forecast API — apps/weather backfill (SNOW-731)
# ---------------------------------------------------------------------------
# Live endpoint:
#   GET {OPEN_METEO_HISTORY_BASE_URL}/forecast?start_date=…&end_date=…
#
# Named *history*, not *archive*, on purpose. This is the historical
# **forecast** API — the model's own past runs, stitched into a continuous
# timeline. It is not ERA5 (``archive-api.open-meteo.com``), which was
# probed on 2026-09-01 and returns ``freezing_level_height`` as a key whose
# values are null throughout. Freezing level is the figure that decides
# rain against snow, so ERA5 is ruled out; pointing a variable called
# "archive" at a non-archive host would be the quiet lie.
#
# The default is the free public host, which takes no key. As with the
# forecast host above, the key is sent only to a host that has been moved
# off its free default (SNOW-579), so cutting over to
# ``customer-historical-forecast-api.open-meteo.com`` is an environment
# change on Render rather than a deploy.
OPEN_METEO_HISTORY_BASE_URL = config(
    "OPEN_METEO_HISTORY_BASE_URL",
    default="https://historical-forecast-api.open-meteo.com/v1",
)

# The earliest day ``backfill_weather`` and the LocationAdmin action will
# ask the upstream for. The API reaches back to roughly 2021 (probed), but
# one full season across the estate is already ~95,000 rows against the
# ~1,000 held today, so the floor is a season start rather than "everything
# there is".
#
# Deliberately NOT ``SEASON_START_DATE``, despite sharing today's value:
# that setting is ``fetch_bulletins``' start date, and folding the two
# together would mean moving the bulletin window silently moved the weather
# one too.
WEATHER_BACKFILL_FLOOR = config(
    "WEATHER_BACKFILL_FLOOR",
    default="2025-11-01",
    cast=date.fromisoformat,
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
#
# The derivation and the policy are both callables (SNOW-626) rather than
# inline module-level code: everything here is evaluated once at import, so a
# test that wants to exercise a different basemap origin cannot re-run the
# derivation by overriding OPENFREEMAP_STYLE_URL. Driving the same two
# functions the module itself calls keeps the test's overrides from drifting
# away from what production computes. Lower-case names — Django's settings
# object only exposes UPPER_CASE attributes, so these stay off
# ``django.conf.settings``.


def basemap_origin(style_url: str) -> str:
    """Return the ``scheme://host[:port]`` origin of a basemap style URL.

    Raises ``ImproperlyConfigured`` when ``style_url`` is not absolute — a
    scheme-less value yields empty ``scheme``/``netloc`` from ``urlsplit``,
    which would otherwise reach the CSP as a meaningless ``://`` entry and
    silently break tile loading rather than failing at startup.
    """
    parts = urlsplit(style_url)
    if not parts.scheme or not parts.netloc:
        # SNOW-691: the message names the VALUE rather than one setting —
        # SLOPE_TILE_URL is a second caller now, and naming
        # OPENFREEMAP_STYLE_URL for a bad slope template would send whoever
        # hits this to the wrong line of the wrong .env.
        raise ImproperlyConfigured(
            f"Tile/style URL {style_url!r} must be an absolute URL "
            f"(e.g. https://tiles.openfreemap.org/styles/liberty) — the CSP "
            f"origin is derived from it."
        )
    return f"{parts.scheme}://{parts.netloc}"


def csp_defaults(
    tile_origin: str, *, slope_origin: str | None = None
) -> dict[str, list[str]]:
    """Return the baseline CSP directives, allowlisting ``tile_origin``.

    ``tile_origin`` is the basemap origin the map page fetches its style
    JSON and vector tiles from — the sole part of the policy that varies
    with the environment.

    ``slope_origin`` (SNOW-691) is the origin serving the slope-angle
    raster overlay's WMTS tiles. Keyword-only and defaulted so the direct
    callers in ``tests/test_csp.py`` keep working unchanged; ``None`` omits
    it entirely, which is what a deployment with the overlay's tile URL
    unset should get. It is appended to BOTH ``connect-src`` and
    ``img-src`` because MapLibre fetches raster tiles through ``fetch()``
    but decodes them as images, and ``img-src`` is otherwise
    ``'self' data:`` only.

    Args:
        tile_origin: ``scheme://host[:port]`` of the basemap tile origin.
        slope_origin: ``scheme://host[:port]`` of the slope-raster origin,
            or None to leave it out of the policy.

    Returns:
        The CSP directive name → source-list mapping.

    """
    return {
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
        # SNOW-691: the slope raster is decoded as an image, so its origin
        # has to be here as well as in connect-src below.
        "img-src": ["'self'", "data:", *([slope_origin] if slope_origin else [])],
        "font-src": ["'self'", "data:"],
        # MapLibre creates its tile-parser workers from blob: URLs; /sw.js is
        # our own service worker (served from /).
        "worker-src": ["'self'", "blob:"],
        # MapLibre fetches the Liberty style + vector tiles from the OpenFreeMap
        # origin via fetch(); derived from OPENFREEMAP_STYLE_URL
        # (env-configurable, SNOW-242) so the two settings never drift. Leave
        # self in for XHRs issued against our own API endpoints.
        "connect-src": [
            "'self'",
            tile_origin,
            # swisstopo winter/light styles + tiles.
            "https://vectortiles.geo.admin.ch",
            # Regional national basemaps: IGN Plan IGN (France) and
            # basemap.at (Austria) — style JSON, vector tiles, sprites, glyphs.
            "https://data.geopf.fr",
            "https://mapsneu.wien.gv.at",
            # SNOW-691: the slope-angle raster's WMTS origin (swisstopo by
            # default). Env-derived like tile_origin above, so the setting
            # and the policy cannot drift.
            *([slope_origin] if slope_origin else []),
        ],
        "manifest-src": ["'self'"],
        "report-uri": ["{report_uri}"],
    }


OPENFREEMAP_STYLE_URL = config(
    "OPENFREEMAP_STYLE_URL",
    default="https://tiles.openfreemap.org/styles/liberty",
)
OPENFREEMAP_ORIGIN = basemap_origin(OPENFREEMAP_STYLE_URL)

# SNOW-691: the slope-angle raster overlay's XYZ tile template. swisstopo's
# ``ch.swisstopo.hangneigung-ueber_30`` WMTS layer, in the ``3857_17`` matrix
# set (z0–17; z18 answers HTTP 400). It is derived from a 10 m COMBINED DEM —
# swissALTI3D (CH/LI), RGE ALTI (FR), TINITALY/01 (IT), DGM10 (AT), DGM1
# (Bavaria), EU-DEM (Baden-Württemberg) — so it is a multi-country layer
# clipped to a rectangle, not "Switzerland plus a buffer"; the rectangle
# itself lives in ``static/js/slope_overlay_core.js`` because the client is
# what has to keep requests inside it.
#
# Env-overridable so a self-hosted or replacement raster (SNOW-693) can be
# swapped in without a code deploy, exactly as the basemap style URL is.
SLOPE_TILE_URL = config(
    "SLOPE_TILE_URL",
    default=(
        "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.hangneigung-ueber_30"
        "/default/current/3857/{z}/{x}/{y}.png"
    ),
)
SLOPE_TILE_ORIGIN = basemap_origin(SLOPE_TILE_URL)

CSP_ENABLED = False
CSP_REPORT_ONLY = True
CSP_DEFAULTS = csp_defaults(OPENFREEMAP_ORIGIN, slope_origin=SLOPE_TILE_ORIGIN)


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
# introduced by adding an entry to ``apps/core/fixtures/waffle_flags.json``
# and nothing else — ``sync_waffle_flags`` reconciles the DB to that
# manifest on every deploy, so a migration-seeded row would be created and
# then deleted in the same build (see ``docs/feature-flags.md``).
#
# ``WAFFLE_FLAG_DEFAULT = False`` — a flag with no DB row evaluates to off.
# This is the only safe default: a typo in a ``flag_is_active(...)`` call
# fails closed instead of silently exposing the gated code path.
#
# ``WAFFLE_CREATE_MISSING_FLAGS = False`` — looking up an unknown flag must
# not auto-create it. Flag rows are intentional configuration; we want them
# declared in the manifest so reviewers see them in the diff.

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
# Routes (SNOW-685)
# ---------------------------------------------------------------------------
# Maximum number of Route rows a single user may hold at once, enforced by
# apps.routes.services.routes.create_route.

ROUTES_MAX_PER_USER = config("ROUTES_MAX_PER_USER", default=25, cast=int)

# Largest .gpx upload apps.routes.views.route_create will accept, in bytes.
# 5 MB is comfortably above a full-day 1 Hz recording (a 30,000-point track
# is roughly 3 MB of GPX) while keeping a hostile upload away from the XML
# parser. Django's DATA_UPLOAD_MAX_MEMORY_SIZE is a separate, larger backstop
# on the request body as a whole.

ROUTE_UPLOAD_MAX_BYTES = config(
    "ROUTE_UPLOAD_MAX_BYTES", default=5 * 1024 * 1024, cast=int
)

# SNOW-764 — route sharing.
#
# How long a RouteShare link stays claimable, in days. A share link is
# reusable (a group chat is the ordinary case, and a single-use token would
# work for the first person to tap it and nobody else), so the window is
# the only thing that ever revokes it — an unbounded token would leave a
# standing grant on the user's own data alive forever. 30 days is longer
# than a trip is planned and shorter than a season, so a link shared in
# March cannot still be claimed the following winter.

ROUTE_SHARE_MAX_AGE_DAYS = config("ROUTE_SHARE_MAX_AGE_DAYS", default=30, cast=int)

# How many pending (followed-but-unclaimed) share tokens one session may
# hold. The list lives in request.session, which is a signed cookie, so it
# is bounded for the same reason any cookie-backed list is: an unbounded
# one grows the cookie on every share link a visitor follows and eventually
# breaks the request. Five is more pending shares than anyone accumulates
# in one browsing session — the ordinary count is one, followed and claimed
# in the same minute — and the oldest is dropped rather than the newest
# refused, because the link just followed is the one the visitor means.

ROUTE_SHARE_MAX_PENDING = config("ROUTE_SHARE_MAX_PENDING", default=5, cast=int)


# ---------------------------------------------------------------------------
# Offline download areas (SNOW-749)
# ---------------------------------------------------------------------------
# Maximum number of DownloadArea rows a single user may hold at once,
# enforced by apps.downloads.views.area_sync.
#
# Comfortably above what a device could hold: the per-run download ceiling is
# 200 MB (basemap_download_core.js's DOWNLOAD_CEILING_MB) and the largest
# offerable standing budget is a few times that, so a real user runs out of
# disk long before they run out of rows. The cap is here to bound a scripted
# client, not to ration a legitimate one — a user who has genuinely
# downloaded areas across several devices should never meet it.

DOWNLOAD_AREAS_MAX_PER_USER = config(
    "DOWNLOAD_AREAS_MAX_PER_USER", default=100, cast=int
)


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

# SNOW-729: read-only production DSN, used only by `bin/sync-staging-data`
# to refresh staging's bulletins and resorts.
#
# Nothing in Django connects to it — the script drives pg_dump/psql and reads
# the variable from the environment itself (SNOW-736 removed the second
# DATABASES alias that used to exist for an ORM-based copy). It is declared
# here so it still resolves under every settings module, is shape-checked at
# deploy time by the SETTINGS_SPEC validator, and appears — redacted — in
# `manage.py dump_settings`.
PRODUCTION_DATABASE_URL = config("PRODUCTION_DATABASE_URL", default="")

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

# SNOW-791: which drawing of the weather icons to serve. Snowdesk draws its
# own (bin/build-weather-icons) and that is the default; the other sets are
# kept for comparison at /_icon-sets/. See apps/weather/icon_sets.py for the
# registry and why every set shares one filename scheme. In DEBUG a
# ``?icons=<name>`` query parameter overrides this for the session, so the
# sets can be flipped against real data without a restart.
WEATHER_ICON_SET = config("WEATHER_ICON_SET", default="snowdesk")

try:
    BASEMAP_STYLE_URL = BASEMAP_STYLES[BASEMAP]
except KeyError as exc:
    raise ImproperlyConfigured(
        f"BASEMAP={BASEMAP!r} is not a known basemap. "
        f"Valid keys: {sorted(BASEMAP_STYLES)}"
    ) from exc

# Origin of the basemap the map page loads by default, for the <link
# rel="preconnect"> in home.html. Derived with the same callable the CSP uses
# so the hint and the policy cannot name different hosts.
#
# The active basemap, not OPENFREEMAP_STYLE_URL: a BASEMAP= override points
# first paint at swisstopo or IGN instead, and preconnecting to a host the
# page never contacts would open a connection to nothing. A visitor who has
# since picked a different basemap does get the wrong hint — that choice lives
# in localStorage and cannot be known server-side — which costs one unused
# socket, not correctness.
BASEMAP_ORIGIN = basemap_origin(BASEMAP_STYLE_URL)

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
