"""
tests/public/test_map_defaults.py — the map's opening view, from the setting
to the rendered page.

SNOW-872 made three first-visit defaults configurable — which providers'
bulletins are painted, which EAWS boundary tier is drawn, and how strongly
the choropleth is painted — because each was hardcoded in four places that
had to agree by hand: ``map.js``'s boot IIFE, the same seed repeated in its
``styledata`` handler, ``map_season_ribbon.js``, and the ``aria-checked``
literals in ``_map_embed.html``.

Three things are checked here, and each guards a different way the change
can silently come undone.

**The value reaches the page.** ``settings.MAP_DEFAULT_*`` has to arrive as
``data-`` attributes on ``#map`` for ``mapDefaults()`` to read, and the
opacity one has to arrive as ``0.5`` rather than ``0,5``. Django localises
floats, so without ``{% localize off %}`` a comma-decimal locale renders a
value ``Number()`` reads as ``NaN`` and the configured default reverts —
with nothing anywhere to say so. The assertion on the attribute's exact
rendered text is what keeps that covered, so it is made under an explicitly
comma-decimal locale as well as the default one.

**The rows follow the value.** Every ``aria-checked`` in the Bulletins and
Boundaries sections is asserted against the configuration under
``override_settings``, not against a fixed expectation. A template that
drifted back to a literal would still pass a test that only ever renders the
shipped defaults, which is the whole failure this ticket removes.

**The two copies of the opacity scale agree.** ``MAP_OPACITY_STEPS`` is a
deliberate second copy of ``STEPS`` in ``static/js/layer_visibility_core.js``
— it earns its place by failing the deploy on a value off the scale instead
of letting ``nearestStep`` snap 0.37 to 0.25 behind an operator's back — and
the copy is compared against the JS declaration directly, the way
``tests/public/test_map_country_groups.py`` compares its two projections
against each other rather than against a third hardcoded copy.

Nothing here restates the overlay-key → country-code mapping. That lives in
``COUNTRY_GROUPS`` (static/js/map_state.js), the settings name providers, and
the page emits overlay keys; the parity test below only checks that every key
a provider resolves to is a key the menu actually renders.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, override_settings
from django.urls import reverse

_LAYER_VISIBILITY_SOURCE = (
    Path(__file__).resolve().parents[2] / "static" / "js" / "layer_visibility_core.js"
)

# ``const STEPS = Object.freeze([0, 0.25, 0.5, 0.75, 1]);``
_STEPS_RE = re.compile(r"const STEPS = Object\.freeze\(\[([^\]]*)\]\)")

# ``data-overlay-key="l4"`` … ``aria-checked="true"`` — one layers-menu row.
# djangofmt keeps the two attributes on adjacent lines of the same element,
# with ``data-country-codes`` optionally between them.
_ROW_RE = re.compile(
    r'data-overlay-key="([a-z0-9.]+)"\s+'
    r'(?:data-country-codes="[a-z ]+"\s+)?'
    r'aria-checked="(true|false)"'
)

# ``data-bulletins-step="0.5"`` is the ATTRIBUTE ORDER's mirror image of the
# rows above — the checked state comes first on the fill-strength segments.
_STEP_RE = re.compile(
    r'aria-checked="(true|false)"[^>]*?data-bulletins-step="([0-9.]+)"', re.DOTALL
)


def _page(client: Client) -> str:
    """Render the map page and return its HTML.

    ``public:map`` 301s to the homepage, which is where the map surface (and
    its layers menu) actually renders.

    Args:
        client: The Django test client.

    Returns:
        The decoded response body.

    """
    response = client.get(reverse("public:home"))
    assert response.status_code == 200
    return response.content.decode()


def _attribute(html: str, name: str) -> str:
    """Read one ``data-`` attribute off the ``#map`` root.

    Args:
        html: The rendered page.
        name: The attribute name, e.g. ``data-default-boundary``.

    Returns:
        The attribute's exact rendered text.

    """
    match = re.search(rf'{name}="([^"]*)"', html)
    assert match is not None, f"{name} is not rendered on the map page"
    return match.group(1)


def _rows(html: str) -> dict[str, bool]:
    """Read every layers-menu row's checked state off the rendered page.

    Args:
        html: The rendered page.

    Returns:
        Mapping of ``data-overlay-key`` to whether the row opens checked.

    """
    return {key: checked == "true" for key, checked in _ROW_RE.findall(html)}


def _declared_steps() -> list[float]:
    """Parse ``STEPS`` out of static/js/layer_visibility_core.js.

    Returns:
        The five opacity steps the Bulletins control offers, in order.

    """
    block = _STEPS_RE.search(_LAYER_VISIBILITY_SOURCE.read_text())
    assert block is not None, "STEPS not found in static/js/layer_visibility_core.js"
    return [float(part) for part in block.group(1).split(",")]


@pytest.mark.django_db
class TestDefaultsReachThePage:
    """Each setting arrives on ``#map`` as the attribute mapDefaults() reads."""

    def test_shipped_defaults_render(self, client: Client) -> None:
        """The values that shipped before SNOW-872, unchanged."""
        html = _page(client)

        assert _attribute(html, "data-default-overlays") == "country.ch"
        assert _attribute(html, "data-default-boundary") == "l4"
        assert _attribute(html, "data-default-opacity-step") == "0.5"

    @override_settings(
        MAP_DEFAULT_OVERLAYS=["country.ch", "country.albina"],
        MAP_DEFAULT_BOUNDARY="l1",
        MAP_DEFAULT_OPACITY_STEP=0.25,
    )
    def test_configured_defaults_render(self, client: Client) -> None:
        """A non-default configuration reaches the page in full."""
        html = _page(client)

        assert _attribute(html, "data-default-overlays") == "country.ch country.albina"
        assert _attribute(html, "data-default-boundary") == "l1"
        assert _attribute(html, "data-default-opacity-step") == "0.25"

    @override_settings(MAP_DEFAULT_OVERLAYS=[], MAP_DEFAULT_BOUNDARY="")
    def test_empty_configuration_renders_empty(self, client: Client) -> None:
        """Empty is a configuration — no provider on, no boundary drawn.

        The attributes stay PRESENT and empty rather than being dropped:
        ``mapDefaults()`` distinguishes an absent attribute (a fixture, a
        second map root) from a blank one (an operator's deliberate choice),
        and an omitted attribute would put SLF back.
        """
        html = _page(client)

        assert _attribute(html, "data-default-overlays") == ""
        assert _attribute(html, "data-default-boundary") == ""

    @override_settings(MAP_DEFAULT_OPACITY_STEP=0.75, LANGUAGE_CODE="de")
    def test_the_opacity_step_is_not_localised(self, client: Client) -> None:
        """``0.75``, never ``0,75``.

        The whole reason ``{% localize off %}`` wraps that attribute: under a
        comma-decimal locale ``Number('0,75')`` is ``NaN``, ``nearestStep``
        falls back to its own default, and the configured value silently
        stops applying.
        """
        assert _attribute(_page(client), "data-default-opacity-step") == "0.75"


@pytest.mark.django_db
class TestRowsFollowTheConfiguration:
    """The rendered ticks agree with the settings, not with a literal."""

    def test_shipped_defaults_tick_slf_and_micro(self, client: Client) -> None:
        """The opening view as it was before this ticket."""
        rows = _rows(_page(client))

        assert rows["country.ch"] is True
        assert rows["country.fr"] is False
        assert rows["country.albina"] is False
        assert rows["l1"] is False
        assert rows["l2"] is False
        assert rows["l4"] is True

    @override_settings(
        MAP_DEFAULT_OVERLAYS=["country.fr", "country.albina"],
        MAP_DEFAULT_BOUNDARY="l2",
    )
    def test_configured_rows_tick(self, client: Client) -> None:
        """Change the configuration and every one of the six rows follows."""
        rows = _rows(_page(client))

        assert rows["country.ch"] is False
        assert rows["country.fr"] is True
        assert rows["country.albina"] is True
        assert rows["l1"] is False
        assert rows["l2"] is True
        assert rows["l4"] is False

    @override_settings(MAP_DEFAULT_BOUNDARY="")
    def test_no_boundary_leaves_every_tier_unticked(self, client: Client) -> None:
        """An empty tier is not a missing one — no row claims to be on."""
        rows = _rows(_page(client))

        assert [rows["l1"], rows["l2"], rows["l4"]] == [False, False, False]

    @override_settings(MAP_DEFAULT_OPACITY_STEP=0.25)
    def test_the_fill_control_ticks_the_configured_step(self, client: Client) -> None:
        """First paint must not tick a step the map is not painting.

        ``applyBulletinsVisibility`` rewrites these segments the moment
        map.js runs, so this only has to be true for first paint — but a
        first paint that ticks the wrong step is still a lie about what is
        on screen.
        """
        checked = {
            step: state == "true" for state, step in _STEP_RE.findall(_page(client))
        }

        assert checked == {
            "0": False,
            "0.25": True,
            "0.5": False,
            "0.75": False,
            "1": False,
        }


@pytest.mark.django_db
class TestParityWithTheClient:
    """The two copies of each catalogue agree with each other."""

    def test_opacity_steps_match_the_js_declaration(self) -> None:
        """``MAP_OPACITY_STEPS`` is ``STEPS``, or the validation is a lie.

        Settings that rejected a step the control offers — or accepted one it
        does not — would fail deploys for a legal value, or let an operator
        configure a value ``nearestStep`` silently snaps somewhere else.
        """
        from django.conf import settings

        assert list(settings.MAP_OPACITY_STEPS) == _declared_steps()

    def test_every_provider_resolves_to_a_rendered_row(self, client: Client) -> None:
        """No provider may name an overlay key the menu does not render.

        The country codes behind each key are ``COUNTRY_GROUPS``'s business
        (static/js/map_state.js, guarded by test_map_country_groups.py) and
        are deliberately not restated server-side. What has to hold here is
        only that the keys line up.
        """
        from django.conf import settings

        rendered = set(_rows(_page(client)))

        for provider, key in settings.MAP_BULLETIN_PROVIDERS.items():
            assert key in rendered, f"{provider} maps to {key}, which no row carries"


class TestValidation:
    """A misconfigured environment fails the deploy rather than the visitor."""

    def _reload_settings(self, **env: str) -> None:
        """Re-import config.settings.base with ``env`` set.

        The validation runs at import, which is the point of it — the deploy
        stops rather than a visitor getting an opening view nobody chose — so
        exercising it means importing the module again under a patched
        environment.

        Args:
            env: Environment variables to set for the re-import.

        """
        import importlib
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, env):
            importlib.reload(importlib.import_module("config.settings.base"))

    def teardown_method(self) -> None:
        """Re-import the module cleanly so later tests see the real settings."""
        self._reload_settings()

    def test_an_unknown_provider_raises(self) -> None:
        """A typo'd provider name is caught at startup, not at first paint."""
        with pytest.raises(ImproperlyConfigured, match="MAP_DEFAULT_PROVIDERS"):
            self._reload_settings(MAP_DEFAULT_PROVIDERS="slf,aineva")

    def test_an_unknown_boundary_tier_raises(self) -> None:
        """``l3`` has no row and no storage key — it is not a tier to open on."""
        with pytest.raises(ImproperlyConfigured, match="MAP_DEFAULT_BOUNDARY"):
            self._reload_settings(MAP_DEFAULT_BOUNDARY="l3")

    def test_an_off_scale_opacity_step_raises(self) -> None:
        """0.37 is not a step, and snapping it silently is worse than failing."""
        with pytest.raises(ImproperlyConfigured, match="MAP_DEFAULT_OPACITY_STEP"):
            self._reload_settings(MAP_DEFAULT_OPACITY_STEP="0.37")
