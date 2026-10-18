"""
tests/public/test_map_zoom_control.py — the map's zoom control partial
(SNOW-1077).

``public/partials/_map_zoom_control.html`` renders empty on /map/, where
static/js/map_zoom_control.js fills it, and with values on the component
library at /_components/?slug=map-zoom-control. These cover both: the
empty state the script starts from, a filled variant, the tick's position
surviving a comma-decimal locale, and the library panel rendering.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from django.utils import translation

from apps.public._component_fixtures import MAP_ZOOM_CONTROL_VARIANTS
from tests.factories import UserFactory

PARTIAL = "public/partials/_map_zoom_control.html"


@pytest.fixture()
def staff_client(db: Any) -> Client:
    """Return a logged-in staff client carrying the HX-Request header."""
    user: User = UserFactory.create()
    client = Client()
    client.force_login(user)
    client.defaults["HTTP_HX_REQUEST"] = "true"
    return client


def _variant(caption_start: str) -> dict[str, Any]:
    """Return the library variant whose caption starts with ``caption_start``."""
    return next(
        v for v in MAP_ZOOM_CONTROL_VARIANTS if v["caption"].startswith(caption_start)
    )


def test_empty_render_is_a_closed_roundel_named_zoom_level() -> None:
    """With no context the roundel is unnamed by number and the flyout shut."""
    html = render_to_string(PARTIAL, {})
    assert "aria-label='Zoom level'" in html
    assert 'aria-expanded="false"' in html
    flyout = html[html.index('id="map-zoom-flyout"') :]
    assert "hidden" in flyout[: flyout.index(">")]
    # The script draws the ticks, and no DEBUG line without DEBUG.
    ticks = html[html.index("data-zoom-ticks") :]
    assert "<li" not in ticks[: ticks.index("</ol>")]
    assert "data-zoom-camera" not in html


def test_strings_template_carries_a_label_and_description_per_gate() -> None:
    """Each key window.pwaZoomGates publishes has a translatable label."""
    html = render_to_string(PARTIAL, {})
    for key in ("region_select", "terrain_filter", "route_slopes"):
        assert f'data-string="gate-{key}"' in html
        assert f'data-string="desc-{key}"' in html
    assert 'data-string="toggle-label"' in html


def test_a_filled_variant_names_the_zoom_and_draws_its_ticks() -> None:
    """The library's z12 variant: every feature named, and every level numbered."""
    context = _variant("At z12")["context"]
    html = render_to_string(PARTIAL, context)
    assert 'aria-label="Zoom level 12"' in html
    assert 'aria-expanded="true"' in html
    assert "Some features are only available at certain zoom levels." in html
    for name in ("Region selector", "Terrain overlay", "Route slope angle"):
        assert name in html
    assert html.count('class="map-zoom-level"') == 15
    terrain = html[html.index('data-zoom-tick="terrain_filter"') :]
    assert "data-reached" in terrain[: terrain.index(">")]
    region = html[html.index('data-zoom-tick="region_select"') :]
    assert "data-reached" not in region[: region.index(">")]
    # Each row is a button aimed at its threshold, which reads its direction.
    assert 'data-zoom-target="10"' in region
    assert "≤10" in region
    assert "≥14" in html


def test_bound_variants_disable_the_matching_button() -> None:
    """+ is disabled at z18 and − at z4."""
    at_max = render_to_string(PARTIAL, _variant("At the maximum")["context"])
    plus = at_max[at_max.index('data-zoom-step="in"') :]
    assert "disabled" in plus[: plus.index(">")]
    at_min = render_to_string(PARTIAL, _variant("At the minimum")["context"])
    minus = at_min[at_min.index('data-zoom-step="out"') :]
    assert "disabled" in minus[: minus.index(">")]


def test_the_maximum_zoom_puts_the_marker_at_the_top() -> None:
    """At z18 the marker sits at ``top: 0.0%``, not the empty-render fallback."""
    html = render_to_string(PARTIAL, _variant("At the maximum")["context"])
    marker = html[html.index("data-zoom-marker") :]
    assert "top: 0.0%" in marker[: marker.index(">")]


def test_positions_are_not_localised() -> None:
    """A comma-decimal locale must not write ``top: 42,86%``."""
    context = _variant("At z12")["context"]
    with translation.override("de"):
        html = render_to_string(PARTIAL, context)
    assert "top: 42.86%" in html
    assert "42,86" not in html


def test_the_library_panel_renders(staff_client: Client) -> None:
    """Every variant renders on /_components/, in both themes."""
    response = staff_client.get(
        reverse("public:components_panel", kwargs={"slug": "map-zoom-control"})
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert body.count('class="map-zoom-control-demo"') == 2 * len(
        MAP_ZOOM_CONTROL_VARIANTS
    )
    assert "Camera zoom 12.47" in body
