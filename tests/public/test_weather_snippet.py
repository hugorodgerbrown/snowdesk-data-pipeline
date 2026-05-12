"""
tests/public/test_weather_snippet.py — Tests for the fetch_weather_snippet view.

Covers the HTMX POST endpoint that fetches weather just-in-time when a
bulletin page renders without a WeatherSnapshot for the current
(region, date) pair.

Test matrix:
  - 400 on non-HTMX POST (require_htmx guard)
  - 405 on HTMX GET (require_POST guard)
  - 404 on unknown region_id
  - 400 on malformed date_str
  - Forecast path success (target_date == today)
  - Archive path success (target_date < today)
  - Fetch failure — response is 200 with no-weather fallback, no hx-post attr
  - Integration: bulletin_detail includes hx-post on header when no snapshot exists
  - CSRF: POST without token is rejected with 403 (enforce_csrf_checks=True)
  - CSRF: POST with valid X-CSRFToken header returns 200
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import requests
from django.core.cache import cache
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    WeatherSnapshotFactory,
)

# HTMX header required by the require_htmx decorator.
_HTMX_HEADERS = {"HTTP_HX_REQUEST": "true"}


def _weather_url(region_id: str, date_str: str) -> str:
    """Build the URL for the weather_snippet endpoint."""
    return reverse(
        "public:weather_snippet",
        kwargs={"region_id": region_id, "date_str": date_str},
    )


# ---------------------------------------------------------------------------
# Guard tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFetchWeatherSnippetGuards:
    """Tests for request-guard behaviour (non-HTMX, wrong method, bad inputs)."""

    def test_non_htmx_post_returns_400(self):
        """Plain POST without HX-Request header is rejected with 400."""
        client = Client()
        region = MicroRegionFactory.create()
        url = _weather_url(region.region_id, "2026-01-15")
        response = client.post(url)
        assert response.status_code == 400

    def test_htmx_get_returns_405(self):
        """HTMX GET is rejected with 405 (require_POST)."""
        client = Client()
        region = MicroRegionFactory.create()
        url = _weather_url(region.region_id, "2026-01-15")
        response = client.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 405

    def test_unknown_region_returns_404(self):
        """Unknown region_id returns 404."""
        client = Client()
        url = _weather_url("CH-NOTEXIST", "2026-01-15")
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 404

    def test_malformed_date_returns_400(self):
        """Non-ISO date string returns 400."""
        client = Client()
        region = MicroRegionFactory.create()
        url = _weather_url(region.region_id, "not-a-date")
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFetchWeatherSnippetForecastPath:
    """Forecast path: target_date == today, uses fetch_weather_for_region."""

    def test_forecast_path_returns_populated_header(self, monkeypatch):
        """Forecast path returns the weather header with icon and condition label."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()
        # Use .build() (no DB write) so the view does not find a snapshot in the
        # DB and short-circuits before reaching the monkeypatched fetcher.  This
        # keeps the test covering the "no snapshot → call API" path.  CLAUDE.md
        # normally requires .create(); this is a deliberate exception.
        snapshot = WeatherSnapshotFactory.build(
            region=region,
            valid_for_date=today,
            weather_code=0,  # clear sky
            sunrise=datetime(today.year, today.month, today.day, 6, 0, tzinfo=UTC),
            sunset=datetime(today.year, today.month, today.day, 20, 0, tzinfo=UTC),
        )

        monkeypatch.setattr(
            "public.views.fetch_weather_for_region",
            lambda *args, **kwargs: (snapshot, True),
        )

        client = Client()
        url = _weather_url(region.region_id, today.isoformat())
        response = client.post(url, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        content = response.content.decode()
        # Populated header contains the icon img tag.
        assert 'data-testid="bulletin-header-hero-icon"' in content
        # Condition label is present (clear sky → "Clear").
        assert "Clear" in content
        # No HTMX retry trigger on a successful response.
        assert "hx-post" not in content

    def test_forecast_path_no_htmx_trigger_on_response(self, monkeypatch):
        """weather_htmx_trigger is always False in the snippet response."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()
        # Use .build() so there is no DB row — the API path is exercised.
        # Deliberate exception to the .create() rule; see sibling test above.
        snapshot = WeatherSnapshotFactory.build(
            region=region,
            valid_for_date=today,
            weather_code=1,
        )
        monkeypatch.setattr(
            "public.views.fetch_weather_for_region",
            lambda *args, **kwargs: (snapshot, False),
        )

        client = Client()
        url = _weather_url(region.region_id, today.isoformat())
        response = client.post(url, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        assert "hx-post" not in response.content.decode()


@pytest.mark.django_db
class TestFetchWeatherSnippetArchivePath:
    """Archive path: target_date < today, uses fetch_archive_for_region."""

    def test_archive_path_returns_populated_header(self, monkeypatch):
        """Archive path returns the weather header with icon and condition label."""
        region = MicroRegionFactory.create()
        past_date = timezone.localdate().replace(year=2026, month=1, day=10)
        # Use .build() so no DB row exists — the API path is exercised.
        # Deliberate exception to the .create() rule; see forecast sibling tests.
        snapshot = WeatherSnapshotFactory.build(
            region=region,
            valid_for_date=past_date,
            weather_code=3,  # overcast
            sunrise=datetime(
                past_date.year, past_date.month, past_date.day, 7, 0, tzinfo=UTC
            ),
            sunset=datetime(
                past_date.year, past_date.month, past_date.day, 17, 0, tzinfo=UTC
            ),
        )

        monkeypatch.setattr(
            "public.views.fetch_archive_for_region",
            lambda *args, **kwargs: [(snapshot, True)],
        )

        client = Client()
        url = _weather_url(region.region_id, past_date.isoformat())
        response = client.post(url, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        content = response.content.decode()
        # Populated header has hero icon.
        assert 'data-testid="bulletin-header-hero-icon"' in content
        # Overcast → condition label "Overcast".
        assert "Overcast" in content
        # No retry trigger.
        assert "hx-post" not in content

    def test_archive_path_empty_results_returns_fallback(self, monkeypatch):
        """Empty archive result returns the no-weather fallback fragment."""
        region = MicroRegionFactory.create()
        past_date = timezone.localdate().replace(year=2026, month=1, day=10)

        monkeypatch.setattr(
            "public.views.fetch_archive_for_region",
            lambda *args, **kwargs: [],
        )

        client = Client()
        url = _weather_url(region.region_id, past_date.isoformat())
        response = client.post(url, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-weather-bucket="none"' in content
        assert "hx-post" not in content


# ---------------------------------------------------------------------------
# Existing-snapshot path (DB-first guard — SNOW-161)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFetchWeatherSnippetExistingSnapshot:
    """DB-first guard: if a snapshot already exists the API must not be called."""

    def test_existing_snapshot_skips_forecast_fetch(self, monkeypatch):
        """When a snapshot exists for (region, today) the forecast API is not called."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=today,
            weather_code=0,  # clear sky
            sunrise=datetime(today.year, today.month, today.day, 6, 0, tzinfo=UTC),
            sunset=datetime(today.year, today.month, today.day, 20, 0, tzinfo=UTC),
        )

        def _must_not_be_called(*args, **kwargs):
            raise AssertionError("API must not be called")

        monkeypatch.setattr(
            "public.views.fetch_weather_for_region", _must_not_be_called
        )

        client = Client()
        url = _weather_url(region.region_id, today.isoformat())
        response = client.post(url, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        content = response.content.decode()
        # Snapshot was present → populated header rendered.
        assert 'data-testid="bulletin-header-hero-icon"' in content
        assert 'data-weather-bucket="none"' not in content

    def test_existing_snapshot_skips_archive_fetch(self, monkeypatch):
        """When a snapshot exists for (region, past_date) the archive API is not called."""
        region = MicroRegionFactory.create()
        past_date = timezone.localdate().replace(year=2026, month=1, day=10)
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=past_date,
            weather_code=3,  # overcast
            sunrise=datetime(
                past_date.year, past_date.month, past_date.day, 7, 0, tzinfo=UTC
            ),
            sunset=datetime(
                past_date.year, past_date.month, past_date.day, 17, 0, tzinfo=UTC
            ),
        )

        def _must_not_be_called(*args, **kwargs):
            raise AssertionError("API must not be called")

        monkeypatch.setattr(
            "public.views.fetch_archive_for_region", _must_not_be_called
        )

        client = Client()
        url = _weather_url(region.region_id, past_date.isoformat())
        response = client.post(url, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        content = response.content.decode()
        # Snapshot was present → populated header rendered.
        assert 'data-testid="bulletin-header-hero-icon"' in content
        assert 'data-weather-bucket="none"' not in content


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFetchWeatherSnippetFailure:
    """Fetch failure: fetcher raises an exception; view returns safe fallback."""

    def test_fetch_exception_returns_200_fallback(self, monkeypatch):
        """Fetcher raising HTTPError returns 200 with no-weather fallback."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()

        monkeypatch.setattr(
            "public.views.fetch_weather_for_region",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                requests.HTTPError("503 Service Unavailable")
            ),
        )

        client = Client()
        url = _weather_url(region.region_id, today.isoformat())
        response = client.post(url, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-weather-bucket="none"' in content
        # No HTMX retry trigger — must not loop.
        assert "hx-post" not in content

    def test_archive_fetch_exception_returns_200_fallback(self, monkeypatch):
        """Archive fetcher raising an exception returns 200 with no-weather fallback."""
        region = MicroRegionFactory.create()
        past_date = timezone.localdate().replace(year=2026, month=1, day=10)

        monkeypatch.setattr(
            "public.views.fetch_archive_for_region",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                requests.HTTPError("503 Service Unavailable")
            ),
        )

        client = Client()
        url = _weather_url(region.region_id, past_date.isoformat())
        response = client.post(url, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-weather-bucket="none"' in content
        assert "hx-post" not in content


# ---------------------------------------------------------------------------
# CSRF tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFetchWeatherSnippetCsrf:
    """CSRF enforcement: the endpoint must reject requests missing a valid token."""

    def test_post_without_csrf_token_returns_403(self, monkeypatch):
        """POST without X-CSRFToken header is rejected with 403 when CSRF checks are enforced."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()

        monkeypatch.setattr(
            "public.views.fetch_weather_for_region",
            lambda *args, **kwargs: (None, False),
        )

        client = Client(enforce_csrf_checks=True)
        url = _weather_url(region.region_id, today.isoformat())
        response = client.post(url, HTTP_HX_REQUEST="true")

        assert response.status_code == 403

    def test_post_with_valid_csrf_token_returns_200(self, monkeypatch):
        """POST with a valid X-CSRFToken header returns 200 when CSRF checks are enforced."""
        region = MicroRegionFactory.create()
        today = timezone.localdate()

        monkeypatch.setattr(
            "public.views.fetch_weather_for_region",
            lambda *args, **kwargs: (None, False),
        )

        client = Client(enforce_csrf_checks=True)
        # Obtain a real CSRF token by running get_token() against a throwaway
        # request and injecting the resulting cookie into the test client.  The
        # Django test Client only sets csrftoken when a response actually calls
        # get_token() or uses the {% csrf_token %} tag; using get_token()
        # directly is the simplest way to bootstrap the cookie in a unit test.
        dummy_request = RequestFactory().get("/")
        dummy_request.META["SERVER_NAME"] = "testserver"
        csrf_token = get_token(dummy_request)
        client.cookies["csrftoken"] = csrf_token

        url = _weather_url(region.region_id, today.isoformat())
        response = client.post(
            url,
            HTTP_HX_REQUEST="true",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Integration test: bulletin_detail emits hx-post when no snapshot exists
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBulletinDetailWeatherTrigger:
    """Integration: bulletin page includes hx-post trigger when no WeatherSnapshot."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        """Clear the cache before and after each test."""
        cache.clear()
        yield
        cache.clear()

    def _make_today_bulletin_for_region(self, region):
        """Create a bulletin valid today for ``region`` (happy-path render)."""
        today = timezone.localdate()
        vf = datetime(today.year, today.month, today.day, 6, 0, tzinfo=UTC)
        vt = datetime(today.year, today.month, today.day, 15, 0, tzinfo=UTC)
        bulletin = BulletinFactory.create(
            valid_from=vf,
            valid_to=vt,
            issued_at=vf - timedelta(minutes=30),
        )
        RegionBulletinFactory.create(
            bulletin=bulletin, region=region, region_name_at_time=region.name
        )
        return bulletin

    def _bulletin_url(self, region, target_date):
        """Build the canonical bulletin URL for ``(region, target_date)``."""
        return reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": region.region_id,
                "slug": region.slug,
                "date_str": target_date.isoformat(),
            },
        )

    def test_bulletin_detail_includes_hx_post_when_no_snapshot(self):
        """When bulletin_detail renders with no WeatherSnapshot, hx-post is in the HTML."""
        region = MicroRegionFactory.create(
            region_id="CH-4115", name="Valais", slug="ch-4115"
        )
        today = timezone.localdate()
        self._make_today_bulletin_for_region(region)
        # Deliberately do NOT create a WeatherSnapshot.

        client = Client()
        response = client.get(self._bulletin_url(region, today), follow=True)

        assert response.status_code == 200
        content = response.content.decode()
        assert "hx-post" in content
        expected_snippet_url = reverse(
            "public:weather_snippet",
            kwargs={"region_id": region.region_id, "date_str": today.isoformat()},
        )
        assert expected_snippet_url in content

    def test_no_snapshot_response_is_uncacheable(self):
        """The no-snapshot response must not be browser-cacheable.

        SNOW-161 follow-up: when the page bakes in the HTMX trigger, the
        browser must hit the server on every reload — otherwise the cached
        HTML-with-trigger fires HTMX again after the snapshot has landed,
        producing a visible header swap (flash). Assert the Cache-Control
        header carries the strict never-cache directives Django emits via
        ``add_never_cache_headers``.
        """
        region = MicroRegionFactory.create(
            region_id="CH-4118", name="Glarus", slug="ch-4118"
        )
        today = timezone.localdate()
        self._make_today_bulletin_for_region(region)
        # Deliberately do NOT create a WeatherSnapshot.

        client = Client()
        response = client.get(self._bulletin_url(region, today), follow=True)

        assert response.status_code == 200
        cache_control = response.headers.get("Cache-Control", "")
        assert "no-store" in cache_control
        assert "no-cache" in cache_control
        assert "must-revalidate" in cache_control

    def test_snapshot_present_response_is_cacheable(self):
        """When weather_display is populated, the existing Cache-Control strategy is preserved."""
        region = MicroRegionFactory.create(
            region_id="CH-4119", name="Uri", slug="ch-4119"
        )
        today = timezone.localdate()
        self._make_today_bulletin_for_region(region)
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=today,
            weather_code=0,
            sunrise=datetime(today.year, today.month, today.day, 6, 0, tzinfo=UTC),
            sunset=datetime(today.year, today.month, today.day, 20, 0, tzinfo=UTC),
        )

        client = Client()
        response = client.get(self._bulletin_url(region, today), follow=True)

        assert response.status_code == 200
        cache_control = response.headers.get("Cache-Control", "")
        # Public, with a non-zero max-age — exactly the regular today-page policy.
        assert "public" in cache_control
        assert "max-age=" in cache_control
        assert "no-store" not in cache_control
