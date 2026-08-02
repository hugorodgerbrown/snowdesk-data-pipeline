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
