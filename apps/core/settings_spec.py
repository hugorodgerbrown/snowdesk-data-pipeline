"""
apps/core/settings_spec.py — Declarative spec for environment-derived settings.

A bare ``OPEN_METEO_API_BASE_URL=customer-api.open-meteo.com`` — no scheme,
no ``/v1`` — was accepted at startup and only surfaced as
``Invalid URL 'customer-api.open-meteo.com/elevation'`` part-way through an
elevation-resolution batch, because ``request_url()`` simply concatenates
the base and the endpoint. Nothing validated it (SNOW-580).

SNOW-554 had already solved that class of problem for one setting:
``check_site_base_url`` verifies ``SITE_BASE_URL`` is absolute and non-local,
and because both build scripts run ``manage.py migrate`` — which runs system
checks first — an ``Error`` aborts the deploy rather than shipping the broken
configuration. This module generalises that from one setting to the whole
environment surface.

Two things live here:

* ``SETTINGS_SPEC`` — one ``SettingSpec`` per environment-derived setting,
  carrying its validator and whether its value is a secret.
* ``iter_settings_report()`` — the redacted view of the current
  configuration, backing ``manage.py dump_settings``.

The checks themselves stay in ``apps/core/checks.py`` alongside the existing
ones; this module is deliberately Django-free apart from reading
``django.conf.settings``, so the spec can be reasoned about (and tested)
without standing up the check framework.

Scope, deliberately
-------------------
Validators here answer "is this value *shaped* like the thing it claims to
be" — never "is this the *right* value". Staging and production legitimately
differ, and a check that had to tell them apart would need its own
configuration to be wrong in a third way (the same reasoning as
``check_site_base_url``'s host-shape-only rule).

Settings with no validatable shape (free-text names, booleans that
``python-decouple`` has already cast, tuples built in code rather than read
from the environment) are listed with ``validator=None`` so they still appear
in the dump. A setting absent from the spec entirely is a gap, not a
statement that it needs no validation — ``test_settings_spec_covers_every_
config_call`` fails when one is added to ``config/settings/`` without a spec
entry.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings

# A validator takes the resolved setting value and returns an error message,
# or None when the value is acceptable.
Validator = Callable[[object], str | None]

# Shown in place of a secret's value. Not a truncation or a hash — a leaked
# prefix is still a leak in a pasted support thread.
REDACTED = "***redacted***"

# Shown for a setting whose value is empty, so "unset" is visually distinct
# from "set to the empty string" in the dump (they are the same thing to
# python-decouple, but the reader is usually asking "did I forget this?").
UNSET = "(unset)"


def absolute_url(value: object) -> str | None:
    """Return an error unless ``value`` is an absolute http(s) URL, or empty.

    Empty is accepted because several of these settings use ``""`` to mean
    "feature off". A spec entry that must be present sets
    ``required_in_production=True`` instead.
    """
    if not value:
        return None
    if not isinstance(value, str):
        return f"expected a string, got {type(value).__name__}"
    parts = urlsplit(value)
    if not parts.scheme:
        return (
            f"{value!r} has no scheme — it must start with http:// or https://. "
            "A bare host is concatenated straight into the request URL and "
            "fails only when the first outbound call is made."
        )
    if parts.scheme not in {"http", "https"}:
        return f"{value!r} has scheme {parts.scheme!r}; expected http or https"
    if not parts.hostname:
        return f"{value!r} has no host"
    return None


def postgres_dsn(value: object) -> str | None:
    """Return an error unless ``value`` is a Postgres connection URL, or empty.

    Shape only, per this module's scope — it never asks whether the DSN
    points at the *right* database. Empty means "no production sync
    configured", which is the default everywhere but the staging cron job.

    A bare host here would surface as an opaque driver error part-way
    through an unattended `bin/sync-staging-data` run (SNOW-729), which is
    the same failure mode ``absolute_url`` exists to prevent for the
    provider APIs. ``absolute_url`` itself cannot be reused: it requires an
    http(s) scheme.
    """
    if not value:
        return None
    if not isinstance(value, str):
        return f"expected a string, got {type(value).__name__}"
    parts = urlsplit(value)
    if parts.scheme not in {"postgres", "postgresql"}:
        return (
            f"{value!r} has scheme {parts.scheme!r}; expected postgres:// or "
            "postgresql://"
        )
    if not parts.hostname:
        return f"{value!r} has no host"
    if not parts.path.lstrip("/"):
        return f"{value!r} names no database"
    return None


def local_mirror_url(value: object) -> str | None:
    """Return an error unless ``value`` is a usable dev-mirror URI, or empty.

    Mirrors accept two forms, and each has its own failure mode:

    * ``http(s)://…`` — a mirror view served by the dev server. Validated
      exactly as ``absolute_url``.
    * ``file://…`` — a directory of payloads read straight off disk
      (``METEOFRANCE_API_LOCAL_MIRROR_URL`` does this). It **must** be the
      three-slash ``file:///abs/path`` form: ``file://relative/path``
      silently parses its first segment as the netloc, so the path the
      fetcher ends up with is not the one that was written. The settings
      comment in ``config/settings/development.py`` warns about this;
      nothing enforced it until now.

    """
    if not value:
        return None
    if not isinstance(value, str):
        return f"expected a string, got {type(value).__name__}"

    parts = urlsplit(value)
    if parts.scheme != "file":
        return absolute_url(value)

    if parts.netloc:
        return (
            f"{value!r} is a file:// URI with a host part ({parts.netloc!r}). "
            "Use the three-slash absolute form, file:///path/to/dir — a "
            "relative file:// path parses its first segment as the host, so "
            "the directory actually read is not the one written here."
        )
    if not parts.path.startswith("/"):
        return f"{value!r} is a file:// URI with a relative path; use file:///abs/path"
    return None


def positive_int(value: object) -> str | None:
    """Return an error unless ``value`` is an integer greater than zero."""
    if isinstance(value, bool) or not isinstance(value, int):
        return f"expected an integer, got {value!r}"
    if value <= 0:
        return f"expected a positive integer, got {value}"
    return None


def positive_number(value: object) -> str | None:
    """Return an error unless ``value`` is a number greater than zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"expected a number, got {value!r}"
    if value <= 0:
        return f"expected a positive number, got {value}"
    return None


def non_empty(value: object) -> str | None:
    """Return an error when ``value`` is empty or whitespace-only."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "must not be empty"
    return None


@dataclass(frozen=True)
class SettingSpec:
    """One environment-derived setting and how to validate it.

    Attributes:
        name: The ``django.conf.settings`` attribute name.
        validator: Returns an error message, or None when acceptable.
            ``None`` means the value has no validatable shape; the setting
            still appears in the dump.
        secret: Whether the value must be redacted in the dump.
        required_in_production: Whether an empty value is an error when
            ``DEBUG`` is off. Local development is exempt so a fresh
            checkout runs with no ``.env`` entries.
        note: One-line description shown in the dump.

    """

    name: str
    validator: Validator | None = None
    secret: bool = False
    required_in_production: bool = False
    note: str = ""


SETTINGS_SPEC: tuple[SettingSpec, ...] = (
    # --- Identity and environment -----------------------------------------
    SettingSpec(
        "SECRET_KEY",
        validator=non_empty,
        secret=True,
        required_in_production=True,
        note="Django signing key",
    ),
    SettingSpec("SITE_ENVIRONMENT", note="staging | production — labels the deploy"),
    # Read on every tier but only wired to a connection by staging.py, which
    # is where bin/sync-staging-data reads it from (SNOW-729/736).
    SettingSpec(
        "PRODUCTION_DATABASE_URL",
        validator=postgres_dsn,
        secret=True,
        note="read-only production DSN for bin/sync-staging-data (staging only)",
    ),
    # The note used to read "CalVer tag of the running release", which this
    # has never been: it resolves to RENDER_GIT_COMMIT, a git SHA. The CalVer
    # tag is created by release.yml AFTER the deploy starts and never reaches
    # the running process. The human-readable release ordinal is APP_RELEASE
    # below — a separate value, from a separate source, for a separate job.
    SettingSpec(
        "RELEASE_VERSION", note="git SHA of the running build (ETags, APP_VERSION)"
    ),
    # Ordinal of the production release ("24"), shown in the account menu as
    # v24. Sourced from the tracked VERSION file; the env var exists so a
    # test or a one-off container can pin it. No validator: any non-empty
    # string is a legitimate release name, and an empty one simply hides the
    # menu row.
    SettingSpec("APP_RELEASE", note="human-readable release number — account menu"),
    # SITE_BASE_URL keeps its own dedicated check (check_site_base_url,
    # SNOW-554) for the localhost-in-production rule, which no generic
    # validator expresses. The shape check here is complementary.
    SettingSpec(
        "SITE_BASE_URL",
        validator=absolute_url,
        required_in_production=True,
        note="Public origin — absolute URLs, share cards, robots.txt, manifest",
    ),
    # --- Provider APIs ----------------------------------------------------
    SettingSpec("SLF_API_BASE_URL", validator=absolute_url, note="SLF CAAML list API"),
    SettingSpec(
        "SLF_API_LOCAL_MIRROR_URL",
        validator=local_mirror_url,
        note="Dev mirror; empty disables",
    ),
    SettingSpec("ALBINA_API_BASE_URL", validator=absolute_url, note="ALBINA CDN base"),
    SettingSpec(
        "ALBINA_API_LOCAL_MIRROR_URL",
        validator=local_mirror_url,
        note="Dev mirror; empty disables",
    ),
    SettingSpec(
        "METEOFRANCE_API_BASE_URL", validator=absolute_url, note="DPBRA API base"
    ),
    SettingSpec(
        "METEOFRANCE_API_LOCAL_MIRROR_URL",
        validator=local_mirror_url,
        note="Dev mirror; empty disables",
    ),
    SettingSpec("METEOFRANCE_API_KEY", secret=True, note="DPBRA apikey header"),
    # --- Open-Meteo (the SNOW-580 incident) -------------------------------
    SettingSpec(
        "OPEN_METEO_API_BASE_URL",
        validator=absolute_url,
        required_in_production=True,
        note="Elevation host",
    ),
    SettingSpec(
        "OPEN_METEO_API_KEY",
        secret=True,
        note="Customer apikey; empty means the free tier",
    ),
    SettingSpec(
        "OPEN_METEO_HISTORY_BASE_URL",
        validator=absolute_url,
        required_in_production=True,
        note="Historical forecast host (SNOW-731 backfill)",
    ),
    # --- what3words (SNOW-840) --------------------------------------------
    # NOT required_in_production: the feature is gated on a waffle flag and
    # an empty key makes no request at all, so an environment with no
    # subscription is a supported state rather than a misconfiguration.
    SettingSpec(
        "WHAT3WORDS_API_BASE_URL",
        validator=absolute_url,
        note="convert-to-3wa host",
    ),
    SettingSpec(
        "WHAT3WORDS_API_KEY",
        secret=True,
        note="X-Api-Key header; empty means the feature is inert",
    ),
    SettingSpec(
        "WHAT3WORDS_FAKE",
        note="local UX work only — invent an address instead of calling the API",
    ),
    # --- Third-party services ---------------------------------------------
    SettingSpec("POSTHOG_HOST", validator=absolute_url, note="PostHog ingest host"),
    SettingSpec("POSTHOG_API_KEY", secret=True, note="PostHog project key"),
    SettingSpec(
        "OPENFREEMAP_STYLE_URL", validator=absolute_url, note="Basemap style JSON"
    ),
    # SNOW-691: an XYZ tile TEMPLATE, not a plain URL — it carries the
    # literal {z}/{x}/{y} placeholders MapLibre substitutes per tile, so
    # ``absolute_url`` is the right shape check (scheme + host) and nothing
    # stricter would accept it.
    SettingSpec(
        "SLOPE_TILE_URL",
        validator=absolute_url,
        note="Slope-angle raster XYZ tile template",
    ),
    SettingSpec("MAXMIND_ACCOUNT_ID", secret=True, note="GeoIP download account"),
    SettingSpec("MAXMIND_LICENSE_KEY", secret=True, note="GeoIP download key"),
    # --- Email ------------------------------------------------------------
    SettingSpec("EMAIL_HOST", note="SMTP host (Mailpit locally)"),
    SettingSpec("EMAIL_PORT", validator=positive_int, note="SMTP port"),
    SettingSpec("EMAIL_HOST_USER", note="SMTP username"),
    SettingSpec("EMAIL_HOST_PASSWORD", secret=True, note="SMTP password"),
    SettingSpec("DEFAULT_FROM_EMAIL", note="From: on outbound mail"),
    SettingSpec("EMAIL_USE_TLS", note="STARTTLS on the SMTP connection"),
    # --- WebAuthn ---------------------------------------------------------
    SettingSpec("WEBAUTHN_RP_ID", note="Relying-party ID — the registrable domain"),
    SettingSpec(
        "WEBAUTHN_ORIGIN",
        validator=absolute_url,
        required_in_production=True,
        note="Relying-party origin",
    ),
    SettingSpec("WEBAUTHN_RP_NAME", note="Name shown in the passkey prompt"),
    # --- Numeric limits ---------------------------------------------------
    SettingSpec(
        "ACCOUNT_TOKEN_MAX_AGE", validator=positive_int, note="Signed-token TTL (s)"
    ),
    SettingSpec("FAVOURITES_MAX_PER_USER", validator=positive_int, note="Per-user cap"),
    SettingSpec("ROUTES_MAX_PER_USER", validator=positive_int, note="Per-user cap"),
    SettingSpec("TRIPS_MAX_PER_USER", validator=positive_int, note="Per-user cap"),
    SettingSpec(
        "DOWNLOAD_AREAS_MAX_PER_USER", validator=positive_int, note="Per-user cap"
    ),
    SettingSpec(
        "ROUTE_UPLOAD_MAX_BYTES",
        validator=positive_int,
        note="Largest accepted .gpx upload (bytes)",
    ),
    SettingSpec(
        "TRIP_SHARE_MAX_AGE_DAYS",
        validator=positive_int,
        note="How long a trip-share link outlives the trip's own date (days)",
    ),
    SettingSpec(
        "ROUTE_SHARE_MAX_AGE_DAYS",
        validator=positive_int,
        note="How long a route-share link stays claimable (days)",
    ),
    SettingSpec(
        "ROUTE_SHARE_MAX_PENDING",
        validator=positive_int,
        note="Followed-but-unclaimed shares one session may hold",
    ),
    SettingSpec(
        "FIELD_OBSERVATION_RADIUS_KM",
        validator=positive_number,
        note="Observation match radius",
    ),
    # --- Feature flags and toggles (already cast by python-decouple) ------
    SettingSpec("SEASON_START_DATE", note="Season boundary (ISO date)"),
    SettingSpec(
        "WEATHER_BACKFILL_FLOOR",
        note="Earliest day the weather backfill will request (ISO date)",
    ),
    SettingSpec("SW_DEV_SHELL_BYPASS", note="Dev-only SW shell bypass (SNOW-585)"),
    SettingSpec("QUERY_COUNT_HEADER_ENABLED", note="X-Query-Count debug header"),
    SettingSpec("CSP_ENABLED", note="Content-Security-Policy on/off"),
    SettingSpec("CSP_REPORT_ONLY", note="CSP in report-only mode"),
    SettingSpec("POSTHOG_MW_CAPTURE_EXCEPTIONS", note="Middleware exception capture"),
    SettingSpec(
        "POSTHOG_CAPTURE_EXCEPTION_CODE_VARIABLES",
        note="Include local variables in captured exceptions",
    ),
    SettingSpec("BASEMAP", note="Basemap provider key"),
    SettingSpec("WEATHER_ICON_SET", note="Which weather icon set to serve"),
    # --- Host allowlists (lists built by decouple's Csv cast) -------------
    SettingSpec("ALLOWED_HOSTS", note="Django host allowlist"),
    SettingSpec("CSRF_TRUSTED_ORIGINS", note="CSRF trusted origins"),
)

# Free-tier Open-Meteo hosts. A key sent to these is silently ignored; a
# customer host reached without one 401s on every request.
#
# Must stay in step with ``apps.locations.services.open_meteo.FREE_HOSTNAMES``
# and with the shipped ``OPEN_METEO_*_BASE_URL`` defaults: a default host
# absent from this set reads as a customer host, and
# ``check_open_meteo_key_host_pairing`` would then fail every production
# boot that has no key.
FREE_OPEN_METEO_HOSTS: frozenset[str] = frozenset(
    {
        "api.open-meteo.com",
        "historical-forecast-api.open-meteo.com",
    }
)


def spec_by_name() -> dict[str, SettingSpec]:
    """Return the spec keyed by setting name."""
    return {spec.name: spec for spec in SETTINGS_SPEC}


def display_value(spec: SettingSpec, value: object) -> str:
    """Return the value as it should appear in a dump — redacted if secret."""
    if value is None or value == "" or value == []:
        return UNSET
    if spec.secret:
        return REDACTED
    return str(value)


@dataclass(frozen=True)
class SettingReport:
    """One row of the redacted settings dump."""

    name: str
    value: str
    note: str
    problem: str | None


def iter_settings_report() -> Iterator[SettingReport]:
    """Yield one redacted row per spec entry, in spec order.

    Includes each setting's validation problem (if any) so the dump doubles
    as a diagnosis, rather than only telling you what is set.
    """
    for spec in SETTINGS_SPEC:
        value = getattr(settings, spec.name, None)
        problem = spec.validator(value) if spec.validator else None
        if problem is None and spec.required_in_production and not settings.DEBUG:
            problem = non_empty(value)
        yield SettingReport(
            name=spec.name,
            value=display_value(spec, value),
            note=spec.note,
            problem=problem,
        )
