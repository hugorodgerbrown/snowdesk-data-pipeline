"""
tests/public/test_routes_surface.py — the map's routes surface (SNOW-686).

The routes panel sat behind the ``routes`` waffle flag until SNOW-724
retired it. Two things stayed worth asserting after the gate went:

  1. The roundel in ``public/partials/_map_embed.html`` and the surface
     include in ``public/home.html`` arrive together — a half-removed gate
     would leave either a roundel that opens nothing or a sheet nothing
     opens — and ``routes_eligible`` (authentication) still splits the
     signed-in list from the anonymous sign-in CTA.

  2. ``routes.js`` stays OUTSIDE the map bundle's contiguous run.
     ``tests/public/test_map_script_order.py`` exercises the homepage as an
     anonymous visitor; this module renders it signed in, which is the state
     where the panel is at its fullest.

Everything the panel does once open is asserted elsewhere: the endpoint in
tests/routes/test_views.py, the client behaviour in
tests/js/test_routes_panel*.js, the exclusivity registration in
tests/js/test_map_overlay_exclusivity_surfaces.js. This module is only about
what the page ships.

Scoped to the Django test client — rendering a template needs no browser.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from tests.factories import UserFactory
from tests.public.test_map_script_order import _map_bundle

# `<script defer src="/static/js/map.js">` — captures the bare filename.
# Deliberately the same shape as test_map_script_order.py's own regex; the
# two modules assert different things about the same sequence.
_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src="[^"]*?/static/js/([^"]+)"')

# Bundle membership comes from ``_map_bundle`` — the same parse of
# tests/js/_load_map_bundle.js that test_map_script_order.py uses — rather
# than a ``map*.js`` prefix test. NINE map-named modules legitimately sit
# outside the bundle (map_sheet, map_help, map_overlay_bounds, … — that
# module's own docstring lists them), so a prefix heuristic spans from
# map_sheet.js in the content block to the last bundle member in extra_js
# and swallows every surface script in between, including this one. That is
# a false positive this test hit on its first run.


def _home(client: Client) -> str:
    """Render the homepage and return its decoded body."""
    response = client.get(reverse("public:home"))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
class TestRoutesSurfaceRenders:
    """The roundel and the sheet ship together, for every visitor."""

    def test_everything_renders_for_an_anonymous_visitor(self, client: Client) -> None:
        """SNOW-724: the roundel, the sheet and the script are all present.

        The inverse of the assertion this test replaced, which pinned the
        flag-off state. An anonymous visitor now gets the whole surface;
        what they do NOT get is eligibility, covered below.
        """
        body = _home(client)

        assert 'id="route-add-btn"' in body
        assert 'id="route-sheet"' in body
        assert "js/routes.js" in body

    def test_the_roundel_carries_its_attributes(self, client: Client) -> None:
        """The roundel carries every attribute routes.js reads off it.

        These five are the module's whole input — it reads nothing else from
        the page — so a renamed or dropped attribute silently disables the
        panel rather than failing loudly.
        """
        client.force_login(UserFactory.create())

        body = _home(client)

        assert 'id="route-add-btn"' in body
        assert 'data-routes-eligible="true"' in body
        assert 'data-route-create-url="/routes/partials/create/"' in body
        assert 'data-route-list-url="/routes/partials/list/?variant=map"' in body
        assert "data-route-rename-url-template=" in body
        assert "data-signin-url=" in body

    def test_the_surface_renders_for_a_signed_in_user(self, client: Client) -> None:
        """The sheet, the panel template, the file picker and the strings."""
        client.force_login(UserFactory.create())

        body = _home(client)

        assert 'id="route-sheet"' in body
        assert 'id="route-list-template"' in body
        assert 'id="route-upload-form"' in body
        assert 'id="route-upload-input"' in body
        assert 'id="routes-strings-template"' in body
        assert "js/routes.js" in body

    def test_an_anonymous_visitor_is_not_eligible(self, client: Client) -> None:
        """Rendered does not imply eligible.

        The roundel still renders — the affordance is discoverable rather
        than appearing on sign-in — but routes.js reads ``false`` here and
        shows its sign-in CTA instead of a list.
        """
        body = _home(client)

        assert 'id="route-add-btn"' in body
        assert 'data-routes-eligible="false"' in body

    def test_the_panel_carries_the_overlay_switch(self, client: Client) -> None:
        """The "Display on the map" switch, now there is a layer to drive.

        SNOW-686 asserted the inverse: the panel shipped before its map
        layer existed, so ``includes/_ugc_panel.html`` was included without
        a ``toggle_id`` and rendered no switch, on the grounds that a
        switch wired to nothing is a worse lie than an absent one. SNOW-687
        adds the layer and the ``window.pwaRoutesOverlay`` bridge, so the
        panel now passes a ``toggle_id`` and the switch appears.

        The id is the one static/js/routes.js delegates its ``change``
        listener on, which is why it is asserted here rather than left to
        the JS suite: a rename on either side silently unwires the switch.
        The sibling panels keep theirs — this was never a global change.
        """
        client.force_login(UserFactory.create())

        body = _home(client)

        assert 'id="map-routes-overlay-toggle"' in body
        assert 'id="map-favourites-overlay-toggle"' in body


@pytest.mark.django_db
class TestRoutesScriptStaysOutsideTheMapBundle:
    """``routes.js`` is a surface module, not a bundle member."""

    def test_it_is_not_interleaved_into_the_bundles_run(self, client: Client) -> None:
        """The bundle's contiguous run must not contain routes.js.

        A ``routes.js`` tag that drifted into the ``extra_js`` block — where
        the bundle lives — would break the bundle's run. Asserted signed in
        because that is the state carrying the whole surface.
        """
        client.force_login(UserFactory.create())
        rendered = _SCRIPT_SRC_RE.findall(_home(client))

        assert "routes.js" in rendered, "the tag should be on every page"

        bundle = set(_map_bundle())
        positions = [i for i, name in enumerate(rendered) if name in bundle]
        span = rendered[positions[0] : positions[-1] + 1]

        assert "routes.js" not in span, (
            "routes.js is interleaved into the map bundle's contiguous run. "
            "It is not a MAP_BUNDLE member (it is loaded from "
            "public/partials/_routes_surface.html and reads no map state), so "
            "its tag must sit above or below the bundle — see "
            "tests/public/test_map_script_order.py for why the run matters."
        )

    def test_it_loads_after_the_modules_it_depends_on(self, client: Client) -> None:
        """map_sheet.js and inline_rename.js must run before routes.js.

        routes.js calls ``window.MapSheet.attach`` at parse time, and reads
        ``window.pwaInlineRename``, ``window.pwaRowRenameCommit`` and
        ``window.pwaRowFocus`` on a click. These are deferred classic
        scripts, so document order is execution order — the first of those
        would throw if its module had not run.
        """
        client.force_login(UserFactory.create())
        rendered = _SCRIPT_SRC_RE.findall(_home(client))
        routes_at = rendered.index("routes.js")

        for name in (
            "map_sheet.js",
            "inline_rename.js",
            "row_rename_commit.js",
            "row_focus.js",
            "i18n_strings.js",
        ):
            assert rendered.index(name) < routes_at, (
                f"{name} loads after routes.js, but routes.js depends on it."
            )
