"""
apps/public/pwa_splash.py — iOS launch-screen (splash) link data.

SNOW-878. Reads the manifest written by ``bin/build-pwa-splash`` and
turns it into the ``<link rel="apple-touch-startup-image">`` rows that
``templates/includes/_pwa_splash.html`` emits into every public page's
``<head>``.

Why iOS needs this at all
-------------------------
Chrome builds an Android splash from the web app manifest (icon +
``background_color`` + name) with no artwork from us. iOS reads none of
that for the launch screen: unless a startup image's media query matches
the device exactly, a home-screen launch paints plain white for the
whole cold boot. That white screen is what Snowdesk shipped until this
module existed.

Why the device matrix is not here
---------------------------------
It lives in ``bin/build-pwa-splash`` alone, and this module reads back
what that script actually rendered. The alternative — a device list in
Python and a matching one in the builder — is two lists that drift, and
the failure mode of drift is a 404 on a launch screen, which nothing
surfaces: iOS just falls back to white, silently, on one device model.
Reading the manifest makes an unrendered file impossible to link.

Adding a device is therefore a row in the builder plus
``npm run build:splash``; nothing here changes.

Production artwork on every environment
---------------------------------------
Unlike the icons (SNOW-399), there is no staging splash set, so these
paths do not resolve against ``PWA_ICON_DIR``. The staging/production
distinction has to be legible where the user CHOOSES the app — the
home-screen tile — and on the status bar beside it; both of those stay
amber. Rendering 68 more PNGs to tint a half-second launch screen is a
poor trade.
"""

from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path
from typing import NamedTuple

from django.conf import settings
from django.templatetags.static import static

logger = logging.getLogger(__name__)

# Written by ``bin/build-pwa-splash`` alongside the PNGs it renders.
MANIFEST_PATH = (
    Path(settings.BASE_DIR)
    / "static"
    / "icons"
    / "pwa"
    / "splash"
    / "splash-manifest.json"
)

# Prefix every entry's ``file`` is joined onto to build its static path.
STATIC_PREFIX = "icons/pwa/splash/"


class SplashLink(NamedTuple):
    """One ``<link rel="apple-touch-startup-image">`` row.

    Attributes:
        href: The resolved static URL of the PNG.
        media: The media query iOS matches the device against — device
            dimensions in CSS pixels, device-pixel ratio, orientation and
            colour scheme.

    """

    href: str
    media: str


@cache
def splash_links() -> tuple[SplashLink, ...]:
    """
    Return every iOS startup-image link, in manifest order.

    Cached for the process's lifetime: the manifest is a checked-in build
    artefact that cannot change under a running server, and this is read
    on every public page render.

    A missing or malformed manifest is logged and returns an empty tuple
    rather than raising. The consequence of returning nothing is the
    white launch screen that predates SNOW-878 — bad, but a page that
    still renders. The consequence of raising is every public page 500ing
    because a build step was skipped, which is worse by a wide margin.

    Returns:
        A tuple of ``SplashLink``, empty if the manifest is unreadable.

    """
    try:
        raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning(
            "PWA splash manifest not found at %s — iOS launches will paint white. "
            "Run `npm run build:icons && npm run build:splash`.",
            MANIFEST_PATH,
        )
        return ()
    except OSError, json.JSONDecodeError:
        logger.exception("PWA splash manifest at %s could not be read", MANIFEST_PATH)
        return ()

    return tuple(
        SplashLink(href=static(f"{STATIC_PREFIX}{entry['file']}"), media=entry["media"])
        for entry in raw.get("entries", ())
    )
