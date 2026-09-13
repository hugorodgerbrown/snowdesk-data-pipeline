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

    def test_it_names_every_country_the_map_carries(self) -> None:
        """
        SNOW-931's lesson, applied where there is no ``pwaMapCountries`` to
        ask: a border area resolved against only the countries some client
        state happens to hold silently drops a country's bulletins.
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
