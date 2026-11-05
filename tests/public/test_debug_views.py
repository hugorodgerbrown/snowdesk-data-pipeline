"""
tests/public/test_debug_views.py — Tests for the staff-only debug pages
hosted in apps/public/debug_views.py.

Covers:
  - The SW shell-version page (/_sw-version/, SNOW-517) requires staff and
    server-renders the committed CACHE_VERSION and APP_VERSION.
  - The route terrain page (/_route-terrain/<uuid>/, SNOW-1020) requires
    staff, renders one row per segment, and its CSV carries the same rows.
  - The ingest report page (/_ingest/, SNOW-1423).

The component library's tests moved to tests/design_system/test_views.py
with its views (SNOW-1427).
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import Account
from apps.core.models import CommandRun
from apps.core.sw_shell import cache_version
from apps.routes.models import Route
from tests.factories import (
    AccountFactory,
    CommandRunFactory,
    RouteFactory,
    UserFactory,
)
from track.slope_samples import stride_coordinates
from track.terrain_detail import COLUMNS


@pytest.fixture()
def staff_user(db: Any) -> User:
    """Return a staff User."""
    return UserFactory.create()


@pytest.fixture()
def regular_user(db: Any) -> Account:
    """Return a non-staff Account."""
    return AccountFactory.create()


@pytest.fixture()
def staff_client(staff_user: User) -> Client:
    """Return a logged-in staff client."""
    c = Client()
    c.force_login(staff_user)
    return c


@pytest.fixture()
def htmx_staff_client(staff_user: User) -> Client:
    """Return a logged-in staff client whose requests carry the HX-Request header."""
    c = Client()
    c.force_login(staff_user)
    c.defaults["HTTP_HX_REQUEST"] = "true"
    return c


def _sw_version_url() -> str:
    """Resolve the named URL for the SW shell-version debug page."""
    return reverse("public:sw_version")


@pytest.mark.django_db
class TestSwVersionPage:
    """Tests for the staff-only /_sw-version/ page (SNOW-517)."""

    def test_anonymous_user_redirected_to_admin_login(self) -> None:
        """A logged-out user is bounced to the admin login page."""
        response = Client().get(_sw_version_url())
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_non_staff_user_redirected_to_admin_login(
        self, regular_user: Account
    ) -> None:
        """A logged-in non-staff user is also bounced to admin login."""
        client = Client()
        client.force_login(regular_user.user)
        response = client.get(_sw_version_url())
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_staff_user_sees_committed_version_and_app_version(
        self, staff_client: Client
    ) -> None:
        """The baseline (JS-off) render carries the server-side version pair.

        Proven via both the response context and the rendered HTML.
        """
        response = staff_client.get(_sw_version_url())
        assert response.status_code == 200
        assert response.context["cache_version"] == cache_version()
        assert response.context["app_version"] == settings.APP_VERSION

        body = response.content.decode()
        assert cache_version() in body
        assert str(settings.APP_VERSION) in body

    def test_staff_user_sees_live_version_probe_script(
        self, staff_client: Client
    ) -> None:
        """The progressive-enhancement probe script is included on the page."""
        response = staff_client.get(_sw_version_url())
        assert response.status_code == 200
        body = response.content.decode()
        assert "pwa_sw_version_probe.js" in body
        assert 'data-testid="sw-live-version"' in body

    def test_staff_user_sees_the_worker_list(self, staff_client: Client) -> None:
        """The page carries the list every worker is drawn into (SNOW-1027).

        After a deploy a browser often holds a running and a waiting worker,
        and DevTools names them only by Chrome's own counter. The list and
        its row template are what ``pwa_sw_version_probe.js`` fills in with
        the version each one holds.
        """
        body = staff_client.get(_sw_version_url()).content.decode()
        assert 'data-testid="sw-workers"' in body
        assert "data-sw-workers-body" in body
        assert 'id="sw-worker-row-template"' in body

    @override_settings(SW_DEV_SHELL_BYPASS=True)
    def test_dev_shell_bypass_toggle_renders_when_setting_is_on(
        self, staff_client: Client
    ) -> None:
        """The opt-in checkbox and its script render when the bypass is on (SNOW-585)."""
        response = staff_client.get(_sw_version_url())
        assert response.status_code == 200
        assert response.context["sw_dev_shell_bypass"] is True
        body = response.content.decode()
        assert 'id="sw-dev-shell-cache-optin"' in body
        assert "pwa_dev_shell_toggle.js" in body

    @override_settings(SW_DEV_SHELL_BYPASS=False)
    def test_dev_shell_bypass_toggle_absent_when_setting_is_off(
        self, staff_client: Client
    ) -> None:
        """The opt-in checkbox and its script are absent when the bypass is off."""
        response = staff_client.get(_sw_version_url())
        assert response.status_code == 200
        assert response.context["sw_dev_shell_bypass"] is False
        body = response.content.decode()
        assert 'id="sw-dev-shell-cache-optin"' not in body
        assert "pwa_dev_shell_toggle.js" not in body


def _sampled_route() -> Route:
    """Return a route carrying a slope record built from its own points.

    The factory's three points are about 2.7 km long, so the walk lands a
    hundred-odd 25 m segments; every one is given the same angle, which
    is all these tests need of the ground.
    """
    route = RouteFactory.create()
    boundaries = [
        [round(lon, 6), round(lat, 6)]
        for lon, lat in stride_coordinates(route.points, 25.0)
    ]
    route.slope_samples = {
        "stride_m": 25.0,
        "points": boundaries,
        "segments": [
            {"angle_deg": 32.0, "aspect_deg": 200.0} for _ in range(len(boundaries) - 1)
        ],
    }
    route.save()
    return route


def _segment_count(route: Route) -> int:
    """Return how many segments a sampled route's record holds."""
    assert route.slope_samples is not None
    return len(route.slope_samples["segments"])


def _route_terrain_url(route: Route) -> str:
    """Resolve the SNOW-1020 terrain page for one route."""
    return reverse("public:route_terrain", kwargs={"route_uuid": route.uuid})


@pytest.mark.django_db
class TestRouteTerrainPage:
    """Tests for the staff-only /_route-terrain/<uuid>/ page (SNOW-1020)."""

    def test_anonymous_user_redirected_to_admin_login(self) -> None:
        """A logged-out user is bounced to the admin login page."""
        response = Client().get(_route_terrain_url(_sampled_route()))
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_non_staff_user_redirected_to_admin_login(
        self, regular_user: Account
    ) -> None:
        """A route's owner who is not staff is bounced too."""
        route = _sampled_route()
        client = Client()
        client.force_login(regular_user.user)
        response = client.get(_route_terrain_url(route))
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_staff_user_sees_one_row_per_segment(self, staff_client: Client) -> None:
        """The table carries a row for every stored segment."""
        route = _sampled_route()
        response = staff_client.get(_route_terrain_url(route))
        assert response.status_code == 200
        segment_count = _segment_count(route)
        assert len(response.context["rows"]) == segment_count
        assert list(response.context["columns"]) == list(COLUMNS)
        assert 'data-testid="route-terrain-table"' in response.content.decode()

    def test_csv_carries_the_same_rows_as_the_table(self, staff_client: Client) -> None:
        """One header line plus one line per segment, as an attachment."""
        route = _sampled_route()
        response = staff_client.get(_route_terrain_url(route), {"format": "csv"})
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        assert "attachment" in response["Content-Disposition"]
        lines = list(csv.reader(io.StringIO(response.content.decode())))
        assert lines[0] == list(COLUMNS)
        assert len(lines) - 1 == _segment_count(route)

    def test_json_carries_the_same_rows_as_the_table(
        self, staff_client: Client
    ) -> None:
        """One object per segment, keyed by the columns, for the debug rail."""
        route = _sampled_route()
        response = staff_client.get(_route_terrain_url(route), {"format": "json"})
        assert response.status_code == 200
        rows = response.json()["rows"]
        assert len(rows) == _segment_count(route)
        assert list(rows[0]) == list(COLUMNS)

    def test_unsampled_route_says_so(self, staff_client: Client) -> None:
        """A route with no record renders the page, not a table."""
        route = RouteFactory.create()
        response = staff_client.get(_route_terrain_url(route))
        assert response.status_code == 200
        body = response.content.decode()
        assert 'data-testid="route-terrain-unsampled"' in body
        assert 'data-testid="route-terrain-table"' not in body

    def test_unknown_route_is_404(self, staff_client: Client) -> None:
        """A uuid no route carries."""
        url = reverse(
            "public:route_terrain",
            kwargs={"route_uuid": "00000000-0000-4000-8000-000000000000"},
        )
        assert staff_client.get(url).status_code == 404


@pytest.mark.django_db
class TestIngestReportPage:
    """The staff ingest report at /_ingest/ and its HTMX panel (SNOW-1423)."""

    def test_anonymous_user_redirected_to_admin_login(self) -> None:
        """A logged-out user is bounced to the admin login page."""
        response = Client().get(reverse("public:ingest_report"))
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_non_staff_user_redirected(self, regular_user: Account) -> None:
        """A logged-in non-staff user is bounced too."""
        client = Client()
        client.force_login(regular_user.user)
        response = client.get(reverse("public:ingest_report"))
        assert response.status_code == 302

    def test_staff_sees_every_feed_with_its_own_labels(
        self, staff_client: Client
    ) -> None:
        """Each feed renders its own figures, not one fixed pair."""
        CommandRunFactory.create(
            command="backfill_weather",
            invoked_via=CommandRun.INVOKED_VIA.SCHEDULER,
            counts={"enqueued": 50},
        )
        response = staff_client.get(reverse("public:ingest_report"))
        assert response.status_code == 200
        html = response.content.decode()
        text = {}
        for chunk in html.split('data-testid="ingest-feed"')[1:]:
            key = re.search(r'data-feed="(\w+)"', chunk)
            assert key is not None
            metrics = re.findall(
                r'data-testid="ingest-metric">\s*<b[^>]*>(\d+)</b>\s*(\w+)\s*</span>',
                chunk,
            )
            text[key.group(1)] = " · ".join(f"{n} {label}" for n, label in metrics)
        assert text["forecast"] == "0 refreshed · 0 places"
        assert text["backfill"] == "50 queued · 0 failed"
        assert text["slf"] == "0 new · 0 changed"
        assert response.context["report"]["window"] == "24h"

    def test_window_param(self, staff_client: Client) -> None:
        """?window=7d builds the week's report."""
        response = staff_client.get(reverse("public:ingest_report"), {"window": "7d"})
        assert response.status_code == 200
        assert response.context["report"]["window"] == "7d"

    def test_unknown_window_is_400(self, staff_client: Client) -> None:
        """A window the report does not know is a bad request."""
        response = staff_client.get(reverse("public:ingest_report"), {"window": "9y"})
        assert response.status_code == 400

    def test_page_polls_the_panel(self, staff_client: Client) -> None:
        """The page refreshes its panel every minute and loads htmx."""
        html = staff_client.get(reverse("public:ingest_report")).content.decode()
        assert 'hx-trigger="every 60s"' in html
        assert reverse("public:ingest_report_panel") in html
        assert "js/htmx" in html

    def test_panel_rejects_a_plain_request(self, staff_client: Client) -> None:
        """The fragment view is HTMX-only."""
        response = staff_client.get(reverse("public:ingest_report_panel"))
        assert response.status_code == 400

    def test_panel_renders_the_fragment(self, htmx_staff_client: Client) -> None:
        """Under HTMX the panel returns the inner HTML, no page shell."""
        response = htmx_staff_client.get(
            reverse("public:ingest_report_panel"), {"window": "1h"}
        )
        assert response.status_code == 200
        html = response.content.decode()
        assert 'data-testid="ingest-feeds"' in html
        assert "<html" not in html

    def test_panel_unknown_window_is_400(self, htmx_staff_client: Client) -> None:
        """The panel validates the window as the page does."""
        response = htmx_staff_client.get(
            reverse("public:ingest_report_panel"), {"window": "x"}
        )
        assert response.status_code == 400
