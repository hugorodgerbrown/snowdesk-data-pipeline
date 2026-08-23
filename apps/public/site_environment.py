"""
apps/public/site_environment.py — PWA identity strings per site environment (SNOW-399).

The Django app runs on staging (``*.onrender.com``) and production
(``snowdesk.info``) from the same codebase. Once a user installs the PWA
from both, the two home-screen icons are identical — same name, same
artwork — and there is no way to tell them apart without launching each
one. This footgun bites during testing.

This module owns the single source of truth for the visible differences
between a production install and any non-production install. It is
consumed in two places:

- ``apps.public.views.serve_manifest`` — bakes the identity into the
  manifest's ``name``, ``short_name``, ``theme_color``, and the
  ``icons[*].src`` / ``screenshots[*].src`` paths so the browser's
  installation dialog and the OS launcher tile are already the
  environment-appropriate ones at install time.
- ``apps.public.context_processors.site_environment`` — exposes the same
  fields as template variables so ``base.html`` renders the matching
  ``<title>`` prefix, ``og:site_name``, ``apple-mobile-web-app-title``,
  ``apple-touch-icon`` href, and ``theme-color`` meta tag.

Both consumers deliberately go through the same
``PWAEnvironmentIdentity.from_settings()`` call so the manifest response
and the page ``<head>`` cannot drift out of sync per environment.

The identity is keyed on ``settings.SITE_ENVIRONMENT`` (``"production"``
by default; anything else is treated as a staging-style non-production
install). The production values are the canonical Snowdesk brand
constants; the staging values reuse the same rectangle glyph on an amber
tile with an asterisk set off the mountain's top right by
``bin/build-pwa-icons`` — a launcher tile that reads as unambiguously
non-production from three feet away. Both sets also carry
the season label ("25/26") in a band at the foot of the tile; that is a
brand constant, not an environment one, so it lives in the build script
rather than here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from django.conf import settings

PRODUCTION_ENVIRONMENT = "production"


@dataclass(frozen=True)
class PWAEnvironmentIdentity:
    """
    Bundle the four PWA-identity fields that differ per environment.

    Instances are constructed via :meth:`from_settings` (or the class-level
    ``PRODUCTION`` / ``STAGING`` constants); the dataclass is deliberately
    frozen so a downstream consumer cannot mutate it and re-inject a
    mismatched value elsewhere in the same request.

    Attributes:
        environment: The raw ``SITE_ENVIRONMENT`` value (``"production"``,
            ``"staging"``, or any other user-supplied string treated as
            non-production).
        name_display: The human-facing brand string surfaced in
            ``<title>``, ``og:site_name``, ``apple-mobile-web-app-title``,
            and the manifest ``name``.
        short_name: The short brand string surfaced in the manifest
            ``short_name`` and used by launcher shortcuts on constrained
            surfaces (Android home-screen labels, Chrome new-tab tiles).
        icon_dir: The ``/static/icons/…/`` prefix under which the
            environment's PWA icon set lives. Both the manifest icons and
            the ``apple-touch-icon`` template link resolve their filenames
            under this prefix.
        theme_color: Hex string baked into both the manifest ``theme_color``
            field and the ``<meta name="theme-color">`` tag so the OS
            chrome tint (installed PWA) and the browser chrome tint
            (open tab) match per environment.
        title_suffix: String appended to the ``<title>`` of every public
            page (outside the ``title`` block) so per-page titles
            already customised by child templates still get the
            environment suffix. Empty on production; ``" — Staging"``
            on staging.

    """

    environment: str
    name_display: str
    short_name: str
    icon_dir: str
    theme_color: str
    title_suffix: str

    PRODUCTION: ClassVar["PWAEnvironmentIdentity"]
    STAGING: ClassVar["PWAEnvironmentIdentity"]

    @classmethod
    def from_settings(cls) -> PWAEnvironmentIdentity:
        """
        Return the identity for ``settings.SITE_ENVIRONMENT``.

        Any value that is not exactly ``"production"`` (case-insensitive,
        with surrounding whitespace stripped) resolves to the staging
        identity. This is deliberate: the production install must be the
        least error-prone default, so a typo like ``prodction`` or an
        empty string surfaces as a visibly-staging install rather than
        silently masquerading as production.

        Returns:
            The matching :class:`PWAEnvironmentIdentity`.

        """
        raw = getattr(settings, "SITE_ENVIRONMENT", PRODUCTION_ENVIRONMENT)
        env = str(raw).strip().lower()
        if env == PRODUCTION_ENVIRONMENT:
            return cls.PRODUCTION
        return PWAEnvironmentIdentity(
            environment=env or "unknown",
            name_display=cls.STAGING.name_display,
            short_name=cls.STAGING.short_name,
            icon_dir=cls.STAGING.icon_dir,
            theme_color=cls.STAGING.theme_color,
            title_suffix=cls.STAGING.title_suffix,
        )

    @property
    def is_production(self) -> bool:
        """
        Whether this identity represents the canonical production install.

        Used by ``serve_manifest`` and templates that need to gate
        production-only behaviour (e.g. ``noindex`` on staging) off the
        same signal that drives the visible identity swap.

        Returns:
            ``True`` if ``environment == "production"``, else ``False``.

        """
        return self.environment == PRODUCTION_ENVIRONMENT


PWAEnvironmentIdentity.PRODUCTION = PWAEnvironmentIdentity(
    environment=PRODUCTION_ENVIRONMENT,
    name_display="Snowdesk",
    short_name="Snowdesk",
    icon_dir="/static/icons/pwa/",
    theme_color="#1a1a1a",
    title_suffix="",
)

# Amber theme colour keeps the staging PWA tile clearly non-production on
# both the OS status bar (installed app) and the browser chrome (open
# tab). The matching icon tile colour lives in bin/build-pwa-icons as
# TILE_COLOUR_STAGING — keep the two in the same amber family if either is
# changed so the theme-color meta tag and the icon don't clash.
PWAEnvironmentIdentity.STAGING = PWAEnvironmentIdentity(
    environment="staging",
    name_display="Snowdesk (Staging)",
    short_name="Snowdesk Staging",
    icon_dir="/static/icons/pwa-staging/",
    theme_color="#b45309",
    title_suffix=" — Staging",
)
