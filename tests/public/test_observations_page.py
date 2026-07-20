"""
tests/public/test_observations_page.py — Tests for the /observations page (SNOW-476).

Covers:
  * ``observations_page`` flag off → 404, both anonymous and signed in.
  * Flag on + anonymous → 200 with a sign-in CTA and no observation rows.
  * Flag on + signed in → own report renders with a "You" chip and a
    full-precision timestamp.
  * ``community_reports`` off → another user's report is absent.
  * ``community_reports`` on → another user's report is present with its
    timestamp floored to the preceding 15-minute mark.
  * The 48-hour window excludes an older report.
  * Rows render newest-first.
  * A null ``region`` falls back to the "unknown region" label with no link.
  * A non-null ``region`` links to its bulletin page.

``override_flag`` only decorates plain functions/``unittest.TestCase``
subclasses — it raises ``TypeError`` on a pytest-style class — so every
test method below carries its own decorator(s) rather than one shared at
class level.
"""

from __future__ import annotations

import datetime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from waffle.testutils import override_flag

from observations.models import FieldObservation
from tests.factories import FieldObservationFactory, MicroRegionFactory, UserFactory


@pytest.mark.django_db
class TestObservationsPageFlagGating:
    """The route doesn't exist at all when the flag is inactive."""

    def test_flag_off_returns_404_for_anonymous(self, client: Client) -> None:
        response = client.get(reverse("public:observations"))
        assert response.status_code == 404

    def test_flag_off_returns_404_for_signed_in_user(self, client: Client) -> None:
        user = UserFactory.create()
        client.force_login(user)
        response = client.get(reverse("public:observations"))
        assert response.status_code == 404


@pytest.mark.django_db
class TestObservationsPageAnonymous:
    """An anonymous visitor gets a 200 with a sign-in CTA, no rows."""

    @override_flag("observations_page", active=True)
    def test_returns_200(self, client: Client) -> None:
        response = client.get(reverse("public:observations"))
        assert response.status_code == 200

    @override_flag("observations_page", active=True)
    def test_shows_signin_cta(self, client: Client) -> None:
        response = client.get(reverse("public:observations"))
        assert b'data-testid="observations-signin-cta"' in response.content

    @override_flag("observations_page", active=True)
    def test_no_rows_shown(self, client: Client) -> None:
        FieldObservationFactory.create()
        response = client.get(reverse("public:observations"))
        assert b'data-testid="observation-row"' not in response.content


@pytest.mark.django_db
class TestObservationsPageSignedIn:
    """A signed-in viewer sees their own reports, with community gating."""

    @override_flag("observations_page", active=True)
    def test_empty_window_shows_empty_state(self, client: Client) -> None:
        user = UserFactory.create()
        client.force_login(user)
        response = client.get(reverse("public:observations"))
        assert b'data-testid="observations-empty"' in response.content

    @override_flag("observations_page", active=True)
    def test_own_report_renders_with_you_chip_and_full_timestamp(
        self, client: Client
    ) -> None:
        user = UserFactory.create()
        observed_at = timezone.now() - datetime.timedelta(minutes=7)
        FieldObservationFactory.create(user=user, observed_at=observed_at)
        client.force_login(user)

        response = client.get(reverse("public:observations"))

        assert b'data-testid="observation-row"' in response.content
        content = response.content.decode()
        assert "You" in content
        # Full precision — the exact minute survives, not floored to :00/:15/etc.
        assert observed_at.strftime("%H:%M") in content

    @override_flag("observations_page", active=True)
    @override_flag("community_reports", active=False)
    def test_other_users_report_absent_without_community_reports(
        self, client: Client
    ) -> None:
        viewer = UserFactory.create()
        other = UserFactory.create()
        FieldObservationFactory.create(user=other)
        client.force_login(viewer)

        response = client.get(reverse("public:observations"))

        assert b'data-testid="observation-row"' not in response.content

    @override_flag("observations_page", active=True)
    @override_flag("community_reports", active=True)
    def test_other_users_report_present_with_floored_timestamp(
        self, client: Client
    ) -> None:
        viewer = UserFactory.create()
        other = UserFactory.create()
        # 07 minutes past the hour floors to 00.
        observed_at = timezone.now().replace(
            minute=7, second=30, microsecond=0
        ) - datetime.timedelta(hours=1)
        FieldObservationFactory.create(user=other, observed_at=observed_at)
        client.force_login(viewer)

        response = client.get(reverse("public:observations"))

        assert b'data-testid="observation-row"' in response.content
        content = response.content.decode()
        floored = observed_at.replace(minute=0)
        assert floored.strftime("%H:%M") in content
        assert observed_at.strftime("%H:%M") not in content

    @override_flag("observations_page", active=True)
    def test_48h_cutoff_excludes_older_report(self, client: Client) -> None:
        user = UserFactory.create()
        old = timezone.now() - datetime.timedelta(hours=49)
        FieldObservationFactory.create(user=user, observed_at=old)
        client.force_login(user)

        response = client.get(reverse("public:observations"))

        assert b'data-testid="observation-row"' not in response.content
        assert b'data-testid="observations-empty"' in response.content

    @override_flag("observations_page", active=True)
    def test_newest_first_ordering(self, client: Client) -> None:
        user = UserFactory.create()
        older = FieldObservationFactory.create(
            user=user,
            observed_at=timezone.now() - datetime.timedelta(hours=2),
            observation_type=FieldObservation.OBSERVATION_TYPE.PINWHEELS,
        )
        newer = FieldObservationFactory.create(
            user=user,
            observed_at=timezone.now() - datetime.timedelta(minutes=5),
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        client.force_login(user)

        response = client.get(reverse("public:observations"))
        content = response.content.decode()

        newer_label = newer.get_observation_type_display()
        older_label = older.get_observation_type_display()
        assert content.index(newer_label) < content.index(older_label)

    @override_flag("observations_page", active=True)
    def test_unknown_region_fallback(self, client: Client) -> None:
        user = UserFactory.create()
        FieldObservationFactory.create(user=user, region=None)
        client.force_login(user)

        response = client.get(reverse("public:observations"))

        assert b"unknown region" in response.content

    @override_flag("observations_page", active=True)
    def test_region_links_to_bulletin(self, client: Client) -> None:
        user = UserFactory.create()
        region = MicroRegionFactory.create()
        FieldObservationFactory.create(user=user, region=region)
        client.force_login(user)

        response = client.get(reverse("public:observations"))

        assert region.get_absolute_url().encode() in response.content
