"""
tests/public/test_bulletin_analytics.py — Tests for bulletin_viewed analytics events.

Covers:
  bulletin_viewed — fires for authenticated and anonymous users on canonical pages;
                    does NOT fire for /examples/* pages;
                    view_context reflects ?ref=email.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from bulletins.models import Bulletin
from regions.models import MicroRegion
from subscriptions.models import Subscriber
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    SubscriberFactory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOKEN_BACKEND = "subscriptions.backends.TokenBackend"

_FROZEN_NOW = datetime(2026, 3, 20, 12, 0, tzinfo=UTC)


def _make_am_bulletin(region: MicroRegion, day: date, **kwargs: Any) -> Bulletin:
    """Create a morning bulletin valid from 06:00 to 15:00 on *day*."""
    vf = datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)
    vt = datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC)
    bulletin = BulletinFactory.create(
        issued_at=vf - timedelta(minutes=30),
        valid_from=vf,
        valid_to=vt,
        **kwargs,
    )
    RegionBulletinFactory.create(
        bulletin=bulletin,
        region=region,
        region_name_at_time=region.name,
    )
    return bulletin


def _bulletin_url(region_id: str, slug: str, date_str: str, suffix: str = "") -> str:
    """Build the canonical dated bulletin URL, with optional query string.

    Region ID is lowercased so the URL matches the canonical form (the view
    302-redirects uppercase region IDs to the lowercase equivalent).
    """
    url = reverse(
        "public:bulletin_date",
        kwargs={"region_id": region_id.lower(), "slug": slug, "date_str": date_str},
    )
    return url + suffix


@pytest.fixture()
def region() -> MicroRegion:
    """Return a test Region with a slug that matches its name."""
    return MicroRegionFactory.create(region_id="CH-4115", name="Valais", slug="valais")


@pytest.fixture()
def bulletin(region: MicroRegion) -> Bulletin:
    """An AM bulletin for 2026-03-15."""
    return _make_am_bulletin(region, date(2026, 3, 15))


# ---------------------------------------------------------------------------
# bulletin_viewed event tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBulletinViewedEvent:
    """bulletin_viewed fires correctly on canonical bulletin pages."""

    def test_fires_for_anonymous_user(
        self, region: MicroRegion, bulletin: Bulletin
    ) -> None:
        client = Client()
        url = _bulletin_url(region.region_id, region.slug, "2026-03-15")
        with (
            patch("django.utils.timezone.now", return_value=_FROZEN_NOW),
            patch("public.views.analytics.track") as mock_track,
        ):
            client.get(url)
        calls = [c for c in mock_track.call_args_list if c.args[0] == "bulletin_viewed"]
        assert len(calls) == 1

    def test_fires_for_authenticated_user(
        self, region: MicroRegion, bulletin: Bulletin
    ) -> None:
        subscriber = SubscriberFactory.create(status=Subscriber.Status.ACTIVE)
        client = Client()
        client.force_login(subscriber, backend=_TOKEN_BACKEND)
        url = _bulletin_url(region.region_id, region.slug, "2026-03-15")
        with (
            patch("django.utils.timezone.now", return_value=_FROZEN_NOW),
            patch("public.views.analytics.track") as mock_track,
        ):
            client.get(url)
        calls = [c for c in mock_track.call_args_list if c.args[0] == "bulletin_viewed"]
        assert len(calls) == 1
        # Authenticated distinct_id is subscriber PK as string.
        assert calls[0].args[1] == str(subscriber.pk)

    def test_distinct_id_is_non_empty_for_anon(
        self, region: MicroRegion, bulletin: Bulletin
    ) -> None:
        # For anonymous users with no prior session, the distinct_id is a
        # request-scoped "anon-<uuid>" string (avoids forcing a session DB
        # write).  If a session cookie is already present the session key is
        # used instead, but either way the value is non-empty.
        client = Client()
        url = _bulletin_url(region.region_id, region.slug, "2026-03-15")
        with (
            patch("django.utils.timezone.now", return_value=_FROZEN_NOW),
            patch("public.views.analytics.track") as mock_track,
        ):
            client.get(url)
        calls = [c for c in mock_track.call_args_list if c.args[0] == "bulletin_viewed"]
        assert len(calls) == 1
        distinct_id = calls[0].args[1]
        assert distinct_id and len(distinct_id) > 0

    def test_view_context_is_direct_by_default(
        self, region: MicroRegion, bulletin: Bulletin
    ) -> None:
        client = Client()
        url = _bulletin_url(region.region_id, region.slug, "2026-03-15")
        with (
            patch("django.utils.timezone.now", return_value=_FROZEN_NOW),
            patch("public.views.analytics.track") as mock_track,
        ):
            client.get(url)
        calls = [c for c in mock_track.call_args_list if c.args[0] == "bulletin_viewed"]
        assert calls[0].args[2]["view_context"] == "direct"

    def test_view_context_is_email_link_with_ref_param(
        self, region: MicroRegion, bulletin: Bulletin
    ) -> None:
        client = Client()
        url = _bulletin_url(region.region_id, region.slug, "2026-03-15", "?ref=email")
        with (
            patch("django.utils.timezone.now", return_value=_FROZEN_NOW),
            patch("public.views.analytics.track") as mock_track,
        ):
            client.get(url)
        calls = [c for c in mock_track.call_args_list if c.args[0] == "bulletin_viewed"]
        assert len(calls) == 1
        assert calls[0].args[2]["view_context"] == "email_link"

    def test_region_id_in_properties(
        self, region: MicroRegion, bulletin: Bulletin
    ) -> None:
        client = Client()
        url = _bulletin_url(region.region_id, region.slug, "2026-03-15")
        with (
            patch("django.utils.timezone.now", return_value=_FROZEN_NOW),
            patch("public.views.analytics.track") as mock_track,
        ):
            client.get(url)
        calls = [c for c in mock_track.call_args_list if c.args[0] == "bulletin_viewed"]
        props = calls[0].args[2]
        assert props["region_id"] == region.region_id

    def test_days_since_publish_in_properties(
        self, region: MicroRegion, bulletin: Bulletin
    ) -> None:
        client = Client()
        url = _bulletin_url(region.region_id, region.slug, "2026-03-15")
        with (
            patch("django.utils.timezone.now", return_value=_FROZEN_NOW),
            patch("public.views.analytics.track") as mock_track,
        ):
            client.get(url)
        calls = [c for c in mock_track.call_args_list if c.args[0] == "bulletin_viewed"]
        props = calls[0].args[2]
        # Bulletin valid_from is 2026-03-15 06:00 UTC; now is 2026-03-20 — 5 days.
        assert props["days_since_publish"] == 5


@pytest.mark.django_db
class TestBulletinViewedNotFiredOnEmptyState:
    """bulletin_viewed must NOT fire when no bulletin exists for the requested date."""

    def test_not_fired_when_no_bulletin_selected(self, region: MicroRegion) -> None:
        """Empty-state page (no bulletin for this date) does not fire the event."""
        # No bulletin created — the view renders the empty-state template.
        client = Client()
        url = _bulletin_url(region.region_id, region.slug, "2026-03-15")
        with (
            patch("django.utils.timezone.now", return_value=_FROZEN_NOW),
            patch("public.views.analytics.track") as mock_track,
        ):
            client.get(url)
        calls = [c for c in mock_track.call_args_list if c.args[0] == "bulletin_viewed"]
        assert len(calls) == 0


@pytest.mark.django_db
class TestBulletinViewedNotFiredOnExamples:
    """bulletin_viewed must NOT fire on /examples/* pages.

    The ``_track_bulletin_viewed`` guard in ``public/views.py`` exits early
    when ``request.path.startswith("/examples/")``.  These tests exercise
    the actual ``/examples/random/`` and ``/examples/category/<level>/``
    URLs so that removing the guard would cause the tests to fail.
    """

    def test_not_fired_on_examples_random(self, region: MicroRegion) -> None:
        """bulletin_viewed is NOT emitted for /examples/random/ even with a live bulletin."""
        _make_am_bulletin(region, date(2026, 3, 15))
        client = Client()
        url = reverse("public:examples_random")
        with (
            patch("django.utils.timezone.now", return_value=_FROZEN_NOW),
            patch("public.views.analytics.track") as mock_track,
        ):
            client.get(url)
        calls = [c for c in mock_track.call_args_list if c.args[0] == "bulletin_viewed"]
        assert len(calls) == 0

    def test_not_fired_on_examples_category(self, region: MicroRegion) -> None:
        """bulletin_viewed is NOT emitted for /examples/category/<level>/ with a matching bulletin."""
        # Build a bulletin whose raw_data carries a "moderate" dangerRating so
        # examples_category can find it via the mainValue filter.
        vf = datetime(2026, 3, 15, 6, 0, tzinfo=UTC)
        vt = datetime(2026, 3, 15, 15, 0, tzinfo=UTC)
        raw = {
            "properties": {
                "dangerRatings": [{"mainValue": "moderate"}],
            }
        }
        bulletin = BulletinFactory.create(
            issued_at=vf,
            valid_from=vf,
            valid_to=vt,
            raw_data=raw,
        )
        RegionBulletinFactory.create(
            bulletin=bulletin,
            region=region,
            region_name_at_time=region.name,
        )
        client = Client()
        url = reverse("public:examples_category", kwargs={"danger_level": "moderate"})
        with (
            patch("django.utils.timezone.now", return_value=_FROZEN_NOW),
            patch("public.views.analytics.track") as mock_track,
        ):
            client.get(url)
        calls = [c for c in mock_track.call_args_list if c.args[0] == "bulletin_viewed"]
        assert len(calls) == 0
