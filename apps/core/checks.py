"""
apps/core/checks.py — Django system checks for project-wide settings.

Validates that ``settings.SITE_BASE_URL`` has been pointed at a real
origin before a non-debug deploy goes out. The setting carries a
``http://localhost:8000`` default so local development works with no
``.env`` entry, but that default is silently wrong everywhere else:
nothing raises, the app boots, pages render, and every absolute URL
built from it points at a machine the visitor doesn't have.

The blast radius is entirely off the rendered page, which is why it
survives both a log scan and a browser check — ``og:image`` /
``twitter:image`` on every page, the ``Sitemap:`` line in
``robots.txt``, the link targets in ``llms.txt``, and the ``id`` /
``start_url`` / ``scope`` fields in the PWA manifest.

``manage.py migrate`` runs system checks before applying anything and
``build.sh`` runs ``migrate`` on every deploy, so an ``Error`` here
aborts the deploy rather than shipping the broken configuration
(SNOW-554).

Deliberately host-shape-only: it asks "is this still a local default?",
never "is this the *right* domain". Staging and production legitimately
differ, and a check that had to tell them apart would need its own
configuration to be wrong in a third way.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Tags, register

# E001 — SITE_BASE_URL still points at a local host on a non-debug deploy.
# E002 — SITE_BASE_URL is not an absolute URL (no scheme, or no host).
CHECK_ID_PREFIX = "core.site_base_url"

# E001 — SW_DEV_SHELL_BYPASS is on while DEBUG is off (SNOW-585).
SW_DEV_SHELL_BYPASS_CHECK_ID_PREFIX = "core.sw_dev_shell_bypass"

# E001 — sw.js has no substitutable CACHE_VERSION assignment (SNOW-590).
SW_CACHE_VERSION_CHECK_ID_PREFIX = "core.sw_cache_version"

# E001 — an environment-derived setting failed its spec validator.
# E002 — a setting required in production is empty (SNOW-580).
SETTINGS_SPEC_CHECK_ID_PREFIX = "core.settings"

# E001 — an Open-Meteo key is set while both hosts are still free-tier.
# E002 — an Open-Meteo customer host is configured with no key (SNOW-580).
OPEN_METEO_CHECK_ID_PREFIX = "core.open_meteo"

# Host names that only ever resolve to the machine serving the request.
# ``urlsplit().hostname`` lower-cases and strips the port and any IPv6
# brackets, so these are compared against a normalised value.
LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


@register(Tags.compatibility)
def check_site_base_url(app_configs: Any, **kwargs: Any) -> list[Error]:
    """Verify ``SITE_BASE_URL`` is an absolute, non-local URL when ``DEBUG`` is off.

    Returns no errors under ``DEBUG`` — local development is exactly the
    case the localhost default exists to serve, and firing there would
    train everyone to ignore the check.
    """
    if settings.DEBUG:
        return []

    value = settings.SITE_BASE_URL
    parts = urlsplit(value)

    if not parts.scheme or not parts.hostname:
        return [
            Error(
                f"SITE_BASE_URL is not an absolute URL: {value!r}",
                hint=(
                    "Set SITE_BASE_URL to a full origin including the scheme, "
                    "e.g. 'https://snowdesk.info'. A value with no scheme "
                    "produces broken absolute links in robots.txt, llms.txt, "
                    "the PWA manifest, and every og:image tag."
                ),
                id=f"{CHECK_ID_PREFIX}.E002",
            )
        ]

    # ``hostname`` is already lower-cased by urlsplit; normalise anyway so
    # the comparison doesn't depend on that implementation detail.
    if parts.hostname.lower() not in LOCAL_HOSTNAMES:
        return []

    return [
        Error(
            f"SITE_BASE_URL points at a local host with DEBUG off: {value!r}",
            hint=(
                "Set the SITE_BASE_URL environment variable on this service "
                "to its public origin, e.g. 'https://snowdesk.info'. Left at "
                "the localhost default, share-card images, the robots.txt "
                "sitemap line, llms.txt links, and the PWA manifest identity "
                "all point at a machine the visitor doesn't have — none of "
                "which is visible in logs or in a browser. If this "
                "environment genuinely serves localhost (see "
                "config/settings/perf.py), silence it via "
                f"SILENCED_SYSTEM_CHECKS = ['{CHECK_ID_PREFIX}.E001']."
            ),
            id=f"{CHECK_ID_PREFIX}.E001",
        )
    ]


@register(Tags.compatibility)
def check_sw_dev_shell_bypass(app_configs: Any, **kwargs: Any) -> list[Error]:
    """Verify ``SW_DEV_SHELL_BYPASS`` is never on when ``DEBUG`` is off (SNOW-585).

    The bypass makes the service worker skip its shell cache entirely — the
    right behaviour for a local dev worktree, where the previous SW would
    otherwise keep serving stale ``map.js`` and friends after a ``git
    pull``, but wrong in production, where the shell cache is what makes the
    second page load instant. ``manage.py migrate`` runs system checks
    before applying anything and ``build.sh`` runs ``migrate`` on every
    deploy, so an ``Error`` here aborts the deploy rather than silently
    shipping the carve-out (mirrors ``check_site_base_url``'s SNOW-554
    rationale above).
    """
    if not settings.SW_DEV_SHELL_BYPASS or settings.DEBUG:
        return []

    return [
        Error(
            "SW_DEV_SHELL_BYPASS is enabled with DEBUG off.",
            hint=(
                "SW_DEV_SHELL_BYPASS is a local-development convenience — it "
                "makes the service worker skip its shell cache so a stale "
                "worker can never keep serving pre-pull assets. Shipping it "
                "to a non-debug deploy would disable the shell cache "
                "everywhere, including production. Unset the "
                "SW_DEV_SHELL_BYPASS environment variable (or set it to "
                "False) on this service."
            ),
            id=f"{SW_DEV_SHELL_BYPASS_CHECK_ID_PREFIX}.E001",
        )
    ]


@register(Tags.compatibility)
def check_sw_cache_version_substitutable(
    app_configs: Any, **kwargs: Any
) -> list[Error]:
    """Verify ``sw.js`` still carries a substitutable ``CACHE_VERSION`` line (SNOW-590).

    Since SNOW-590 the shell cache name is derived from the shell content
    hash and injected into the response by ``serve_sw``; the value in the
    committed file is only a placeholder. That makes the *shape* of the
    assignment load-bearing: edit it into something the substitution regex
    no longer matches and every client would keep whatever literal shipped,
    freezing the shell cache name — the SNOW-457 stale-shell regression,
    silently.

    ``inject_cache_version()`` raises rather than passing the body through
    unchanged, so the failure is loud either way. This check moves it
    earlier still: ``manage.py check`` runs in ``tox -e django-checks`` (a
    required CI job) and ``migrate`` runs it on every deploy, so a broken
    placeholder is caught before it can reach a browser.
    """
    from apps.core.sw_shell import SW_JS_PATH, inject_cache_version

    try:
        source = SW_JS_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return [
            Error(
                f"Could not read the service worker source at {SW_JS_PATH}: {exc}",
                hint=(
                    "serve_sw reads this file on every /sw.js request. If it "
                    "is missing from the deployed tree, the PWA cannot "
                    "register at all."
                ),
                id=f"{SW_CACHE_VERSION_CHECK_ID_PREFIX}.E002",
            )
        ]

    try:
        inject_cache_version(source, version="probe")
    except ValueError:
        return [
            Error(
                "No substitutable CACHE_VERSION assignment in static/js/sw.js.",
                hint=(
                    "serve_sw rewrites the `const CACHE_VERSION = '...';` line "
                    "at serve time with a name derived from the shell content "
                    "hash. Restore that exact single-quoted form — without it "
                    "every client keeps one frozen cache name and stops "
                    "picking up shell changes. See apps/core/sw_shell.py."
                ),
                id=f"{SW_CACHE_VERSION_CHECK_ID_PREFIX}.E001",
            )
        ]

    return []


@register(Tags.compatibility)
def check_settings_spec(app_configs: Any, **kwargs: Any) -> list[Error]:
    """Validate every environment-derived setting against its spec (SNOW-580).

    Generalises ``check_site_base_url``'s SNOW-554 mechanism from one
    setting to the whole environment surface. A bare
    ``OPEN_METEO_API_BASE_URL=customer-api.open-meteo.com`` used to be
    accepted at startup and only surfaced as an ``Invalid URL`` part-way
    through a fetch batch; the shape validators here turn that into a
    failed deploy, because ``build.sh`` runs ``migrate`` and ``migrate``
    runs system checks first.

    Shape only, never correctness — see ``apps.core.settings_spec``'s
    module docstring for why "is this the right host" is deliberately out
    of scope.
    """
    from apps.core.settings_spec import SETTINGS_SPEC, non_empty

    errors: list[Error] = []
    for spec in SETTINGS_SPEC:
        value = getattr(settings, spec.name, None)

        if spec.validator is not None:
            problem = spec.validator(value)
            if problem is not None:
                errors.append(
                    Error(
                        f"{spec.name}: {problem}",
                        hint=(
                            f"Set {spec.name} on this service to a valid value. "
                            f"Purpose: {spec.note or 'see config/settings/'}."
                        ),
                        id=f"{SETTINGS_SPEC_CHECK_ID_PREFIX}.E001",
                    )
                )
                continue

        # Emptiness is only an error off DEBUG: a fresh local checkout
        # deliberately runs with no .env entries for most of these.
        if spec.required_in_production and not settings.DEBUG:
            problem = non_empty(value)
            if problem is not None:
                errors.append(
                    Error(
                        f"{spec.name} is required with DEBUG off but {problem}.",
                        hint=(
                            f"Set the {spec.name} environment variable on this "
                            f"service. Purpose: {spec.note or 'see config/settings/'}."
                        ),
                        id=f"{SETTINGS_SPEC_CHECK_ID_PREFIX}.E002",
                    )
                )

    return errors


@register(Tags.compatibility)
def check_open_meteo_key_host_pairing(app_configs: Any, **kwargs: Any) -> list[Error]:
    """Verify the Open-Meteo key and hosts are configured as a pair (SNOW-580).

    Neither half is wrong on its own, so no per-field validator can catch
    either case:

    * A key set while both hosts are still the free public ones is silently
      ignored — ``request_url()`` only attaches ``apikey`` to a host that
      has been moved off its free default (SNOW-579). The configuration
      reads as "we are on the paid tier" while every request is still
      subject to the shared per-IP quota.
    * A customer host with no key 401s on every request. That is the live
      incident this ticket came from, and this rule catches it.

    Both are ``Error``s off ``DEBUG`` only — a local checkout legitimately
    runs the free hosts with no key.
    """
    from apps.core.settings_spec import FREE_OPEN_METEO_HOSTS

    if settings.DEBUG:
        return []

    hosts = (settings.OPEN_METEO_API_BASE_URL, settings.OPEN_METEO_ARCHIVE_BASE_URL)
    hostnames = {urlsplit(host).hostname for host in hosts if host}
    # A hostname we do not recognise is treated as a customer host: that is
    # the fail-safe direction, since the cost of a false positive is a
    # startup error and the cost of a false negative is every request 401ing.
    on_customer_host = bool(hostnames - FREE_OPEN_METEO_HOSTS)
    has_key = bool(settings.OPEN_METEO_API_KEY)

    if on_customer_host and not has_key:
        return [
            Error(
                "Open-Meteo is pointed at a customer host with no API key.",
                hint=(
                    "OPEN_METEO_API_BASE_URL / OPEN_METEO_ARCHIVE_BASE_URL are "
                    "set to a non-free host, but OPEN_METEO_API_KEY is empty — "
                    "every request will 401. Set the key, or point the hosts "
                    "back at https://api.open-meteo.com/v1 and "
                    "https://archive-api.open-meteo.com/v1."
                ),
                id=f"{OPEN_METEO_CHECK_ID_PREFIX}.E002",
            )
        ]

    if has_key and not on_customer_host:
        return [
            Error(
                "An Open-Meteo API key is set while both hosts are free-tier.",
                hint=(
                    "The key is only attached to a host that has been moved off "
                    "its free default (SNOW-579), so this key is never sent and "
                    "the deploy is still on the shared per-IP quota. Point "
                    "OPEN_METEO_API_BASE_URL / OPEN_METEO_ARCHIVE_BASE_URL at "
                    "the customer hosts, or unset OPEN_METEO_API_KEY."
                ),
                id=f"{OPEN_METEO_CHECK_ID_PREFIX}.E001",
            )
        ]

    return []
