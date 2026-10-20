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
  manifest's ``name``, ``short_name``, ``theme_color``,
  ``background_color`` and ``icons[*].src`` so the browser's installation
  dialog, the OS launcher tile and Android's generated splash screen are
  already the environment-appropriate ones at install time.
- ``apps.public.context_processors.site_environment`` — exposes the same
  fields as template variables so ``base.html`` renders the matching
  ``<title>`` prefix, ``og:site_name``, ``apple-mobile-web-app-title``,
  ``apple-touch-icon`` href, browser-tab favicon, ``theme-color`` meta
  tag.
- ``apps.public.views.serve_favicon`` — redirects ``/favicon.ico`` to the
  environment's favicon.

Both consumers deliberately go through the same
``PWAEnvironmentIdentity.from_settings()`` call so the manifest response
and the page ``<head>`` cannot drift out of sync per environment.

The identity is keyed on ``settings.SITE_ENVIRONMENT`` (``"production"``
by default; anything else is treated as a staging-style non-production
install). The production values are the canonical Snowdesk brand
constants; the staging values reuse the same mountain glyph on an amber
tile, raised over the word "Staging", drawn by ``bin/build-pwa-icons`` — a
launcher tile that reads as unambiguously non-production from three feet
away.

SNOW-1112: the ICON is now the only staging tell. ``theme_color`` used to
tint the status bar amber on staging (and near-black on production), but
Android paints the status bar of its generated splash in
``background_color`` and only switches to ``theme_color`` once the page
paints — so any ``theme_color`` other than the background is a visible
colour flip at the exact moment the launch is meant to look continuous.
Both identities now carry ``theme_color == background_color``, and
``tests/public/test_pwa_launch_parity.py`` holds that line. The launch
contract is in ``docs/pwa-launch.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import ClassVar

from django.conf import settings
from django.templatetags.static import static

PRODUCTION_ENVIRONMENT = "production"


@dataclass(frozen=True)
class PWAEnvironmentIdentity:
    """
    Bundle the PWA-identity fields that differ per environment.

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
        icon_static_dir: The staticfiles directory (``icons/pwa/``) holding
            the environment's PWA icon set. Every icon URL — manifest,
            ``apple-touch-icon`` — is built from it through
            :meth:`icon_url`, so all of them are content-hashed in a
            deployed environment and none can name the other set.
        background_color: Hex string for the manifest ``background_color``
            — the colour Android paints its generated splash screen and
            status bar in.
            Mirrors ``--color-bg`` in ``src/css/main.css``.
        theme_color: Hex string baked into both the manifest ``theme_color``
            field and the ``<meta name="theme-color">`` tag. Equal to
            ``background_color`` on every identity (SNOW-1112), so the
            status bar does not change colour between the splash and the
            first paint.
        tile_color: The fill of the environment's icon tile, as drawn by
            ``bin/build-pwa-icons`` (``TILE_COLOUR_*``) and the favicon.
            Not emitted anywhere; it exists so a test can hold the favicon
            and the tile to the same colour now ``theme_color`` no longer
            carries it.
        favicon: The staticfiles path of the browser-tab SVG favicon.
            ``favicon.svg`` on production; the amber ``favicon-staging.svg``
            otherwise, so a staging tab is told apart from a production
            one in every browser, not only in those that fall back to the
            ``apple-touch-icon``.
        title_suffix: String appended to the ``<title>`` of every public
            page (outside the ``title`` block) so per-page titles
            already customised by child templates still get the
            environment suffix. Empty on production; ``" — Staging"``
            on staging.

    """

    environment: str
    name_display: str
    short_name: str
    icon_static_dir: str
    background_color: str
    theme_color: str
    tile_color: str
    favicon: str
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
        return replace(cls.STAGING, environment=env or "unknown")

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

    def icon_static_path(self, filename: str) -> str:
        """
        Return the staticfiles path of one file in this identity's icon set.

        Args:
            filename: The icon's file name, e.g. ``"icon-192.png"``.

        Returns:
            The path relative to the static root, e.g.
            ``"icons/pwa-staging/icon-192.png"``.

        """
        return f"{self.icon_static_dir}{filename}"

    def icon_url(self, filename: str) -> str:
        """
        Return the served URL of one file in this identity's icon set.

        Goes through the staticfiles storage, so on staging and production
        (``CompressedManifestStaticFilesStorage``) the URL carries a content
        hash. That is what lets an installed WebAPK notice a redrawn icon:
        Chrome only re-mints the APK when a manifest icon URL changes.

        Args:
            filename: The icon's file name, e.g. ``"icon-192.png"``.

        Returns:
            The URL the browser should request.

        """
        return static(self.icon_static_path(filename))


# One colour, two jobs: the manifest background_color (Android's splash
# and its status bar) and theme_color (the status bar once the page has
# painted). It is --color-bg in
# src/css/main.css; tests/public/test_pwa_launch_parity.py reads the token
# out of the stylesheet so the two cannot drift.
LAUNCH_BACKGROUND = "#f2f0ec"

PWAEnvironmentIdentity.PRODUCTION = PWAEnvironmentIdentity(
    environment=PRODUCTION_ENVIRONMENT,
    name_display="Snowdesk",
    short_name="Snowdesk",
    icon_static_dir="icons/pwa/",
    background_color=LAUNCH_BACKGROUND,
    theme_color=LAUNCH_BACKGROUND,
    tile_color="#2d4a3e",
    favicon="favicon.svg",
    title_suffix="",
)

# The amber tile is the staging tell — on the launcher and in Android's
# splash. TILE_COLOUR_STAGING in bin/build-pwa-icons and
# the rect fill in static/favicon-staging.svg are the same amber; keep the
# three together if any one moves.
PWAEnvironmentIdentity.STAGING = PWAEnvironmentIdentity(
    environment="staging",
    name_display="Snowdesk (Staging)",
    short_name="Snowdesk Staging",
    icon_static_dir="icons/pwa-staging/",
    background_color=LAUNCH_BACKGROUND,
    theme_color=LAUNCH_BACKGROUND,
    tile_color="#b45309",
    favicon="favicon-staging.svg",
    title_suffix=" — Staging",
)
