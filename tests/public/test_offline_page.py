"""
tests/public/test_offline_page.py — the public /offline/ page (SNOW-930).

The regression this page exists to fix is one status code: an
unauthenticated visitor asking what this device has saved used to be
redirected to sign-in. Everything else here follows from that — the three
blocks are present for a reader with no account, the sync log stays behind
its flag, and settings no longer renders a second copy of any of them.

The panel's own markup contract with ``offline_audit.js`` is
tests/public/test_offline_audit_panel.py, which moved out of
``tests/accounts/`` with the panel. The report's arithmetic is
tests/js/test_offline_audit_core.js, and the service worker's warming and
principal partitioning are tests/js/test_sw.js — none of those needs a browser or a
Django client.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from waffle.testutils import override_flag

from tests.factories import AccountFactory

_TOKEN_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _signed_in_client() -> Client:
    """Return a client signed in as a fresh account."""
    client = Client()
    client.force_login(AccountFactory.create().user, backend=_TOKEN_BACKEND)
    return client


@pytest.mark.django_db
class TestReachability:
    """THE regression: the page you need with no signal was behind a login."""

    def test_an_anonymous_visitor_gets_the_page(self) -> None:
        """Not a redirect to sign-in, which is what /account/settings/ does."""
        response = Client().get(reverse("public:offline_page"))

        assert response.status_code == 200

    def test_a_signed_in_reader_gets_the_same_page(self) -> None:
        """Nothing on it is account-shaped, so nothing about it varies."""
        response = _signed_in_client().get(reverse("public:offline_page"))

        assert response.status_code == 200


@pytest.mark.django_db
class TestWhatItCarries:
    """The three blocks, for both kinds of reader."""

    @pytest.mark.parametrize("signed_in", [False, True])
    def test_the_audit_the_reset_and_its_breakdown_are_all_present(
        self, signed_in: bool
    ) -> None:
        """
        All three were on ``/account/settings/`` and all three moved. Each
        is computed client-side from this browser's own storage, so each
        renders identically whoever is asking.
        """
        client = _signed_in_client() if signed_in else Client()

        html = client.get(reverse("public:offline_page")).content.decode()

        assert 'data-testid="offline-audit-panel"' in html
        assert "data-pwa-reset-trigger" in html
        assert 'data-testid="reset-data-summary-panel"' in html

    def test_the_sync_log_is_absent_without_its_flag(self) -> None:
        """SNOW-482's gate travels with the panel — see docs/feature-flags.md."""
        html = Client().get(reverse("public:offline_page")).content.decode()

        assert 'data-testid="sync-log-panel"' not in html
        assert "js/sync_log.js" not in html

    @override_flag("sync_log", active=True)
    def test_the_sync_log_appears_with_it(self) -> None:
        """And the script that paints it comes with the markup."""
        html = Client().get(reverse("public:offline_page")).content.decode()

        assert 'data-testid="sync-log-panel"' in html
        assert "js/sync_log.js" in html

    def test_the_reset_breakdown_loads_the_reader_it_shares_with_the_map(
        self,
    ) -> None:
        """
        ``basemap_downloaded_areas.js`` is what stops this page and the
        map's Manage downloads sheet disagreeing about what is on the
        device (SNOW-860). It came across with the panel.
        """
        html = Client().get(reverse("public:offline_page")).content.decode()

        assert "js/basemap_downloaded_areas.js" in html
        assert "js/reset_data_summary.js" in html


@pytest.mark.django_db
class TestIntro:
    """SNOW-1137: the intro says what follows the account and what does not."""

    def test_it_does_not_claim_nothing_is_on_the_account(self) -> None:
        """The LIST of downloaded areas follows the account since SNOW-749."""
        html = Client().get(reverse("public:offline_page")).content.decode()

        assert "nothing here is stored on your account" not in html
        assert "follows your account to your other devices" in " ".join(html.split())
        assert "the maps themselves, your download budget" in " ".join(html.split())


@pytest.mark.django_db
class TestResetRow:
    """Ported from tests/accounts/test_account_layout.py with the row."""

    def test_the_row_carries_the_breakdown_panel(self) -> None:
        """
        SNOW-860 — the row states what the reset deletes, in four
        categories. Its helper line was the whole disclosure for a wipe
        that takes every downloaded map, every unsent change, every cached
        page and every preference. The panel is server-rendered chrome
        around a client-side paint, so what is assertable here is that the
        shell, its strings and the module that fills them all reach the
        page.
        """
        html = Client().get(reverse("public:offline_page")).content.decode()

        assert 'data-testid="reset-data-summary-panel"' in html
        assert 'data-role="reset-data-summary-placeholder"' in html
        # SNOW-860: shown outright, not behind a disclosure. It was a
        # collapsible titled "What this will delete", which put the one
        # thing the row exists to say behind a click.
        assert "What this will delete" not in html
        assert "<details" not in html.split('data-testid="reset-data-summary-panel"')[1]

    def test_the_breakdown_names_all_four_categories(self) -> None:
        """
        Each category ships its own translated label, none assumed. A
        category missing from the strings template does not fail loudly —
        reset_data_summary.js falls back to its English literal — so
        nothing else would catch a locale silently losing one.
        """
        html = Client().get(reverse("public:offline_page")).content.decode()

        template = html.split('id="reset-data-summary-strings-template"')[1].split(
            "</template>"
        )[0]
        for key in ("maps", "unsent", "cached", "preferences"):
            assert f'data-string="{key}"' in template, key

    def test_the_reset_control_is_a_button(self) -> None:
        """
        It was a ``text-link`` span of body copy until SNOW-746, which read
        as prose. ``data-pwa-reset-trigger`` is pwa_reset.js's hook and the
        thing that must stay on a ``<button>``.
        """
        html = Client().get(reverse("public:offline_page")).content.decode()

        opening = html[: html.index("data-pwa-reset-trigger")].rsplit("<", 1)[1]
        assert opening.startswith("button")
        assert (
            "text-link" not in html.split("data-pwa-reset-trigger")[0].rsplit("<", 1)[1]
        )


@pytest.mark.django_db
class TestContentEndpoints:
    """SNOW-925: what the per-row control needs from a page with no map."""

    def test_the_page_renders_every_endpoint_the_plan_is_built_from(self) -> None:
        """
        The map reads the same endpoints off ``#map``'s own dataset; this
        page has no ``#map``, and re-deriving them client-side would be a
        second copy of routing only this view can answer for.
        """
        html = Client().get(reverse("public:offline_page")).content.decode()

        assert f'data-regions-url="{reverse("api:regions_geojson")}"' in html
        assert f'data-weather-url="{reverse("api:weather_geojson")}"' in html
        assert 'data-weather-detail-url="/api/weather/__SHORTID__/detail/"' in html
        assert (
            f'data-community-reports-url="{reverse("api:community_reports_geojson")}"'
            in html
        )

    def test_it_renders_the_endpoint_the_plan_now_comes_from(self) -> None:
        """
        SNOW-953: the plan is one bbox-keyed request, not four feeds parsed
        on the client.
        """
        html = Client().get(reverse("public:offline_page")).content.decode()

        assert f'data-area-content-url="{reverse("api:area_content")}"' in html

    def test_it_renders_the_forward_day_feeds(self) -> None:
        """
        SNOW-1312: the dated ratings feed and the map's region sheet, so an
        Update from here warms what the map's download does. The sheet
        template is the one ``#map`` carries.
        """
        html = Client().get(reverse("public:offline_page")).content.decode()

        assert f'data-ratings-url="{reverse("api:ratings")}"' in html
        sheet = reverse("public:region_sheet_partial", kwargs={"region_id": "XX-0000"})
        assert f'data-region-sheet-url="{sheet}"' in html

    def test_it_renders_the_resort_sheet_template(self) -> None:
        """
        SNOW-1318: each resort's place sheet, per forward day, composed from
        the ``__SLUG__`` template ``#map`` carries — for every reader, since
        the sheet names nobody.
        """
        html = Client().get(reverse("public:offline_page")).content.decode()

        sheet = reverse("public:resort_sheet_partial", kwargs={"slug": "__SLUG__"})
        assert f'data-resort-sheet-url="{sheet}"' in html

    def test_it_renders_the_favourite_sheet_template_signed_in(self) -> None:
        """
        SNOW-1313: the Update warms each pin's own place sheet, composed
        from the nil-uuid template ``#map`` carries.
        """
        html = _signed_in_client().get(reverse("public:offline_page")).content.decode()

        sheet = reverse(
            "public:favourite_sheet_partial",
            kwargs={"uuid": "00000000-0000-0000-0000-000000000000"},
        )
        assert f'data-favourite-sheet-url="{sheet}"' in html

    def test_it_renders_no_favourite_sheet_template_signed_out(self) -> None:
        """SNOW-1313: a signed-out reader has no pins to warm."""
        html = Client().get(reverse("public:offline_page")).content.decode()

        assert 'data-favourite-sheet-url=""' in html

    def test_it_loads_the_calendar_core_for_the_forward_window(self) -> None:
        """
        SNOW-1312: ``latestKnownDate`` is how the Update reaches the last
        published day, as the map's download does.
        """
        html = Client().get(reverse("public:offline_page")).content.decode()

        assert "js/calendar_core" in html

    def test_it_renders_no_reach_backwards(self) -> None:
        """
        SNOW-1312: a download looks forward only, from the map and from
        here alike, so the page no longer says how far back to reach.
        """
        html = Client().get(reverse("public:offline_page")).content.decode()

        assert "data-content-past-days" not in html

    def test_it_still_names_every_country_the_map_carries(self) -> None:
        """
        The four region feeds are still WARMED from here — the map needs
        them offline — so the page still names every country.

        What changed with SNOW-953 is why: this attribute used to decide
        which feeds the CONTENT PLAN was resolved against, and a missing
        country silently dropped its bulletins (SNOW-931). The plan comes
        from ``data-area-content-url`` now, which cannot be short of a
        country, so this list is a warm list and nothing more.
        """
        html = Client().get(reverse("public:offline_page")).content.decode()

        for code in settings.MAP_COUNTRY_CODES:
            assert code in html.split('data-content-countries="')[1].split('"')[0]

    def test_the_account_feeds_are_empty_for_a_signed_out_reader(self) -> None:
        """
        Favourites and routes need an account. An endpoint rendered for a
        reader who cannot use it is a fetch that 302s to sign-in and
        caches the redirect.
        """
        html = Client().get(reverse("public:offline_page")).content.decode()

        assert 'data-favourites-url=""' in html
        assert 'data-routes-url=""' in html

    def test_they_are_filled_in_for_a_signed_in_reader(self) -> None:
        html = _signed_in_client().get(reverse("public:offline_page")).content.decode()

        assert 'data-favourites-url=""' not in html
        assert 'data-routes-url=""' not in html

    def test_the_area_items_endpoint_is_for_a_signed_in_reader(self) -> None:
        """
        SNOW-965: the Update carries the reader's own trips and pins'
        forecasts, so it is asked only for somebody who can have them.
        """
        signed_out = Client().get(reverse("public:offline_page")).content.decode()
        signed_in = (
            _signed_in_client().get(reverse("public:offline_page")).content.decode()
        )

        assert 'data-area-items-url=""' in signed_out
        assert f'data-area-items-url="{reverse("api:area_items")}"' in signed_in

    def test_it_renders_the_trip_templates_an_item_is_composed_from(self) -> None:
        """SNOW-965: the same ``__UUID__`` templates ``#map`` carries."""
        html = Client().get(reverse("public:offline_page")).content.decode()

        assert 'data-trip-page-url-template="/trips/__UUID__/"' in html
        assert 'data-trip-route-url-template="/trips/__UUID__/route.geojson"' in html


@pytest.mark.django_db
class TestTerrainClassTiles:
    """SNOW-1138: the report judges an opted-in area on its class tiles."""

    def test_it_renders_the_template_when_the_filter_is_configured(
        self, settings: Any
    ) -> None:
        """The same template the map renders as ``data-terrain-class-tile-url``."""
        settings.TERRAIN_CLASS_TILE_URL = "https://tiles.example/tc/{z}/{x}/{y}.png"
        html = Client().get(reverse("public:offline_page")).content.decode()

        assert (
            'data-terrain-class-tile-url="https://tiles.example/tc/{z}/{x}/{y}.png"'
            in html
        )

    def test_it_renders_none_while_the_filter_is_unconfigured(
        self, settings: Any
    ) -> None:
        """Empty, which the report reads as "make no claim", as the sheet does."""
        settings.TERRAIN_CLASS_TILE_URL = ""
        html = Client().get(reverse("public:offline_page")).content.decode()

        assert 'data-terrain-class-tile-url=""' in html

    def test_it_loads_the_cores_the_derivation_needs(self) -> None:
        """The tileset's rectangle and band, and the rule that walks them."""
        html = Client().get(reverse("public:offline_page")).content.decode()

        for name in (
            "basemap_download_core",
            "slope_classes_core",
            "terrain_filter_core",
        ):
            assert f"js/{name}" in html
        # slope_classes_core.js is read by terrain_filter_core.js at parse time.
        assert html.index("js/slope_classes_core") < html.index(
            "js/terrain_filter_core"
        )


@pytest.mark.django_db
class TestSettingsNoLongerCarriesThem:
    """One copy, in one place — a second would drift."""

    def test_settings_links_to_the_page_instead(self) -> None:
        """A pointer where the block was, so a reader who knew finds it."""
        html = _signed_in_client().get(reverse("accounts:settings")).content.decode()

        assert 'data-testid="settings-offline-link"' in html
        assert reverse("public:offline_page") in html

    def test_settings_renders_none_of_the_three_panels(self) -> None:
        """
        The failure this guards is silent duplication: a panel left on both
        pages paints twice from one module's bindings, and the two copies
        answer independently.
        """
        html = _signed_in_client().get(reverse("accounts:settings")).content.decode()

        assert 'data-testid="offline-audit-panel"' not in html
        assert "data-pwa-reset-trigger" not in html
        assert 'data-testid="sync-log-panel"' not in html

    def test_settings_loads_none_of_their_scripts(self) -> None:
        """Eight script tags went with the blocks; a stray one is dead weight."""
        html = _signed_in_client().get(reverse("accounts:settings")).content.decode()

        for script in (
            "js/offline_audit_core.js",
            "js/offline_audit.js",
            "js/reset_data_summary.js",
            "js/basemap_downloaded_areas.js",
            "js/sync_log.js",
        ):
            assert script not in html

    def test_settings_keeps_the_theme_control(self) -> None:
        """
        The one thing that stayed. It is a display preference with no
        relationship to the network, and moving it would have made
        /offline/ a second settings page.
        """
        html = _signed_in_client().get(reverse("accounts:settings")).content.decode()

        assert "js/theme_preference.js" in html


@pytest.mark.django_db
class TestShellWarming:
    """The page has to be openable offline, which is most of the point."""

    def test_the_worker_warms_it_on_activation(self) -> None:
        """
        ``SHELL_PAGES`` is what ``_rewarmShell`` walks. A login-gated page
        could never have been in it — the warm would fetch a redirect.
        """
        from pathlib import Path

        from django.conf import settings as django_settings

        worker = (
            Path(django_settings.BASE_DIR) / "static" / "js" / "sw.js"
        ).read_text()

        assert "const SHELL_PAGES = [SHELL_PAGE, '/offline/'];" in worker
        assert "_warmCache(SHELL_PAGES)" in worker

    def test_its_template_is_hashed_into_the_cache_version(self) -> None:
        """
        Otherwise editing the page would not change ``CACHE_VERSION``, and
        a returning client would keep serving the old copy — the SNOW-457
        failure mode.
        """
        from apps.core.sw_shell import _shell_template_paths

        names = [path.name for path in _shell_template_paths()]

        assert names.count("offline.html") == 2  # public/ and static/
