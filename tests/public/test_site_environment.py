"""tests/public/test_site_environment.py — PWA identity resolver (SNOW-399).

Unit-level guards for ``apps.public.site_environment.PWAEnvironmentIdentity``.
The manifest view and the template context processor both consume
``from_settings()``; keeping a direct unit test here means a regression
in the resolver surfaces without depending on the HTTP layer or the
template rendering pipeline. The higher-level integration cases
(``test_pwa_manifest.py`` and ``test_pwa_apple_meta.py``) then trust
this contract to hold when they only assert the request-shaped surface.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from django.conf import settings
from django.test import override_settings

from apps.public.site_environment import PWAEnvironmentIdentity


@override_settings(SITE_ENVIRONMENT="production")
def test_production_setting_yields_production_identity() -> None:
    """``"production"`` resolves to the canonical brand strings."""
    identity = PWAEnvironmentIdentity.from_settings()
    assert identity is PWAEnvironmentIdentity.PRODUCTION
    assert identity.is_production is True
    assert identity.name_display == "Snowdesk"
    assert identity.short_name == "Snowdesk"
    assert identity.icon_static_dir == "icons/pwa/"
    assert identity.background_color == "#f2f0ec"
    assert identity.theme_color == "#f2f0ec"
    assert identity.tile_color == "#2d4a3e"
    assert identity.favicon == "favicon.svg"
    assert identity.title_suffix == ""


@override_settings(SITE_ENVIRONMENT="staging")
def test_staging_setting_yields_staging_identity() -> None:
    """``"staging"`` resolves to the amber tile identity."""
    identity = PWAEnvironmentIdentity.from_settings()
    assert identity.environment == "staging"
    assert identity.is_production is False
    assert identity.name_display == "Snowdesk (Staging)"
    assert identity.short_name == "Snowdesk Staging"
    assert identity.icon_static_dir == "icons/pwa-staging/"
    assert identity.background_color == "#f2f0ec"
    assert identity.theme_color == "#f2f0ec"
    assert identity.tile_color == "#b45309"
    assert identity.favicon == "favicon-staging.svg"
    assert identity.title_suffix == " — Staging"


@override_settings(SITE_ENVIRONMENT="  Production  ")
def test_production_matching_ignores_case_and_whitespace() -> None:
    """A user-entered ``SITE_ENVIRONMENT`` with padding still counts as production.

    Render's dashboard trims leading/trailing whitespace in some flows
    but not in others; a bare ``.env`` file preserves everything. The
    resolver normalises so a padded value doesn't silently downgrade.
    """
    identity = PWAEnvironmentIdentity.from_settings()
    assert identity.is_production is True


@override_settings(SITE_ENVIRONMENT="preview")
def test_unknown_setting_yields_staging_identity() -> None:
    """A non-production value that isn't the string ``"staging"`` still
    lands as a staging install.

    Preserves the raw label as ``environment`` so a downstream consumer
    (e.g. a debug page) can display the original value, but the visible
    identity (name, icon, colour) is the staging one — the guarantee
    the resolver makes is "anything not exactly production is visibly
    not production", not "we recognise every possible label".
    """
    identity = PWAEnvironmentIdentity.from_settings()
    assert identity.is_production is False
    assert identity.name_display == "Snowdesk (Staging)"
    assert identity.environment == "preview"
    # Every other field is the staging one — none inherited from production.
    assert (
        dataclasses.replace(identity, environment="staging")
        == PWAEnvironmentIdentity.STAGING
    )


@override_settings(SITE_ENVIRONMENT="")
def test_empty_setting_yields_staging_identity() -> None:
    """An empty label is treated as staging.

    A misconfigured deploy that clears the variable renders an
    obviously-not-production tile — the mistake is visible at install
    time, not silently masqueraded as production.
    """
    identity = PWAEnvironmentIdentity.from_settings()
    assert identity.is_production is False
    assert identity.name_display == "Snowdesk (Staging)"


def test_pwa_environment_identity_is_frozen() -> None:
    """The identity dataclass rejects mutation.

    Prevents a downstream consumer from mutating a shared
    ``PRODUCTION`` / ``STAGING`` constant and leaking a mismatched value
    into another request in the same process.
    """
    identity = PWAEnvironmentIdentity.PRODUCTION
    try:
        identity.theme_color = "#ffffff"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("expected FrozenInstanceError on identity mutation")


@pytest.mark.parametrize(
    ("identity", "favicon", "build_constant"),
    [
        (PWAEnvironmentIdentity.PRODUCTION, "favicon.svg", "TILE_COLOUR_PRODUCTION"),
        (PWAEnvironmentIdentity.STAGING, "favicon-staging.svg", "TILE_COLOUR_STAGING"),
    ],
)
def test_tile_colour_matches_the_favicon_and_the_icon_build(
    identity: PWAEnvironmentIdentity, favicon: str, build_constant: str
) -> None:
    """The identity's ``tile_color`` is the favicon fill and the built tile.

    Since SNOW-1112 the tile is the only thing that tells staging from
    production, so the browser tab and the installed icon must read as the
    same colour. This used to be pinned to ``theme_color``, which no longer
    carries the environment.
    """
    svg = (Path(settings.BASE_DIR) / "static" / favicon).read_text()
    assert f'fill="{identity.tile_color}"' in svg
    script = (Path(settings.BASE_DIR) / "bin" / "build-pwa-icons").read_text()
    assert f"const {build_constant} = '{identity.tile_color}';" in script


def test_icon_url_resolves_inside_the_identitys_own_set() -> None:
    """``icon_url`` builds every icon URL from the identity's static dir."""
    assert PWAEnvironmentIdentity.STAGING.icon_url("icon-192.png") == (
        "/static/icons/pwa-staging/icon-192.png"
    )
    assert PWAEnvironmentIdentity.PRODUCTION.icon_static_path("icon-192.png") == (
        "icons/pwa/icon-192.png"
    )
