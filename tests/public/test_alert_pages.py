"""
tests/public/test_alert_pages.py — The bulletin alert surfaces on the
public site (SNOW-1325, SNOW-1328, SNOW-949).

Covers:
  - The region body's "Since <day>'s bulletin" strip: behind the
    bulletin_alerts flag, in the alert's own words, absent without a
    bulletin the day before.
  - The region body's one-line alerts prompt: an offer while alerts are
    off, a note when the region is followed.
  - /alerts/<date>/: sign-in required, one row per followed place, and a
    row for a saved trip that day linking to the trip (SNOW-947).
  - /_alerts-replay/: staff only, a season grouped by week.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from waffle.testutils import override_flag

from apps.accounts.models import Account
from apps.bulletins.services.render_model import RENDER_MODEL_VERSION
from apps.bulletins.services.replay import ReplayedAlert
from tests.factories import (
    AccountFactory,
    BulletinFactory,
    FavouriteFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
    TripFactory,
    UserFactory,
)
from tests.public.test_region_sheet import _render_model


def _publish(region: Any, day: datetime.date, key: str) -> None:
    """Make a bulletin at ``key`` authoritative for (region, day), linked too."""
    start = datetime.datetime.combine(day, datetime.time.min, tzinfo=datetime.UTC)
    rm = _render_model()
    rm["danger"]["key"] = key
    bulletin = BulletinFactory.create(
        issued_at=start,
        valid_from=start,
        valid_to=start + datetime.timedelta(days=1),
        render_model=rm,
        render_model_version=RENDER_MODEL_VERSION,
    )
    RegionBulletinFactory.create(bulletin=bulletin, region=region)
    RegionDayRatingFactory.create(
        region=region, date=day, max_rating=key, source_bulletin=bulletin
    )


def _sheet(client: Client, region: Any) -> str:
    """Return the region sheet body for today."""
    url = reverse(
        "public:region_sheet_partial", kwargs={"region_id": region.region_id.lower()}
    )
    return client.get(url, HTTP_HX_REQUEST="true").content.decode()


@pytest.fixture()
def flag(db: Any) -> Any:
    """Turn bulletin_alerts on."""
    with override_flag("bulletin_alerts", active=True):
        yield


@pytest.fixture()
def region(db: Any) -> Any:
    """Return a region rated moderate yesterday and considerable today."""
    region = MicroRegionFactory.create(name="Martigny-Verbier")
    today = timezone.localdate()
    _publish(region, today - datetime.timedelta(days=1), "moderate")
    _publish(region, today, "considerable")
    return region


@pytest.fixture()
def signed_in(client: Client, db: Any) -> None:
    """Sign the test client in: the strip is never shown to anonymous pages."""
    client.force_login(AccountFactory.create().user)


@pytest.mark.usefixtures("flag", "signed_in")
class TestSinceStrip:
    """The strip at the top of the region body."""

    def test_names_the_move(self, client: Client, region: Any) -> None:
        """Yesterday moderate, today considerable: "Up from Moderate (2)."."""
        content = _sheet(client, region)
        assert 'data-testid="since-strip"' in content
        assert "Up from Moderate (2)." in content

    def test_quiet_day(self, client: Client, db: Any) -> None:
        """A day that moved nothing says so in one line."""
        quiet = MicroRegionFactory.create()
        today = timezone.localdate()
        _publish(quiet, today - datetime.timedelta(days=1), "considerable")
        _publish(quiet, today, "considerable")
        assert "No change to the level or the problems." in _sheet(client, quiet)

    def test_absent_without_the_day_before(self, client: Client, db: Any) -> None:
        """No bulletin yesterday: no strip."""
        first = MicroRegionFactory.create()
        _publish(first, timezone.localdate(), "low")
        assert "since-strip" not in _sheet(client, first)


@pytest.mark.usefixtures("flag")
def test_strip_is_not_shown_signed_out(client: Client, region: Any) -> None:
    """Anonymous pages skip the strip, and the flag lookup with it."""
    assert "since-strip" not in _sheet(client, region)


@pytest.mark.usefixtures("signed_in")
def test_strip_is_flag_gated(client: Client, region: Any) -> None:
    """Without the flag the strip does not render."""
    with override_flag("bulletin_alerts", active=False):
        assert "since-strip" not in _sheet(client, region)


@pytest.mark.usefixtures("flag")
class TestAlertPrompt:
    """The one line about alerts under the strip."""

    def test_offer_while_off(self, client: Client, region: Any) -> None:
        """Signed in, alerts off: the offer and its link."""
        client.force_login(AccountFactory.create().user)
        content = _sheet(client, region)
        assert "Turn on alerts" in content

    def test_note_when_followed(self, client: Client, region: Any) -> None:
        """Signed in, alerts on, region pinned: the note."""
        account = AccountFactory.create(notify_when=Account.NotifyWhen.CHANGES)
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        client.force_login(account.user)
        assert "You get alerts for this region." in _sheet(client, region)

    def test_nothing_when_not_followed(self, client: Client, region: Any) -> None:
        """Alerts on but another region: no line."""
        account = AccountFactory.create(notify_when=Account.NotifyWhen.CHANGES)
        client.force_login(account.user)
        assert "alert-prompt" not in _sheet(client, region)

    def test_nothing_signed_out(self, client: Client, region: Any) -> None:
        """A visitor gets no prompt."""
        assert "alert-prompt" not in _sheet(client, region)


class TestAlertsDay:
    """/alerts/<date>/."""

    def test_sign_in_required(self, client: Client, db: Any) -> None:
        """A visitor is sent to sign in."""
        response = client.get("/alerts/2026-01-17/")
        assert response.status_code == 302

    def test_bad_date_is_404(self, client: Client, db: Any) -> None:
        """A malformed date is not a page."""
        client.force_login(AccountFactory.create().user)
        assert client.get("/alerts/not-a-day/").status_code == 404

    def test_one_row_per_place(self, client: Client, region: Any) -> None:
        """Each followed place, with its move and its map link."""
        account = AccountFactory.create()
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        FavouriteFactory.create(
            user=account.user, region=MicroRegionFactory.create(), region_pin=True
        )
        client.force_login(account.user)
        today = timezone.localdate().isoformat()
        content = client.get(f"/alerts/{today}/").content.decode()
        assert content.count('data-testid="alerts-day-row"') == 2
        assert "Up from Moderate (2)." in content
        assert f"/map/?d={today}#{region.region_id}" in content
        assert "No bulletin for this day." in content

    def test_trip_that_day_is_a_row(self, client: Client, region: Any) -> None:
        """A saved trip on the day is a row that opens the trip."""
        account = AccountFactory.create()
        today = timezone.localdate()
        trip = TripFactory.create(
            created_by=account.user, date=today, name="Mont Fort loop"
        )
        client.force_login(account.user)
        with patch("apps.bulletins.services.alerts.start_region", return_value=region):
            content = client.get(f"/alerts/{today.isoformat()}/").content.decode()
        assert content.count('data-testid="alerts-day-row"') == 1
        assert "Mont Fort loop" in content
        assert f"/trips/{trip.uuid}/" in content
        assert "Open the trip" in content

    def test_empty(self, client: Client, db: Any) -> None:
        """No places: how to get some."""
        client.force_login(AccountFactory.create().user)
        content = client.get("/alerts/2026-01-17/").content.decode()
        assert "Pin a region or save a resort" in content


class TestReplayPage:
    """/_alerts-replay/."""

    def test_staff_only(self, client: Client, db: Any) -> None:
        """A non-staff user is sent to the admin sign-in."""
        client.force_login(AccountFactory.create().user)
        assert client.get("/_alerts-replay/").status_code == 302

    def test_groups_by_week(self, client: Client, db: Any) -> None:
        """Sent alerts render, grouped by week; skipped only with all=1."""
        when = datetime.datetime(2026, 1, 16, 16, 0, tzinfo=datetime.UTC)
        alerts = [
            ReplayedAlert(
                when,
                when,
                datetime.date(2026, 1, 17),
                "new",
                "SENT",
                "Verbier · Saturday: Moderate (2)",
                "Body.",
                "/map/",
            ),
            ReplayedAlert(
                when,
                when,
                datetime.date(2026, 1, 17),
                "new",
                "SKIPPED",
                "Skipped title",
                "Body.",
                "/map/",
            ),
        ]
        client.force_login(UserFactory.create())
        with patch("apps.public.debug_views.replay_archive", return_value=alerts):
            content = client.get("/_alerts-replay/?source=bogus&mode=bogus").content
            assert b"Verbier \xc2\xb7 Saturday" in content
            assert b"Skipped title" not in content
            content = client.get("/_alerts-replay/?all=1").content
            assert b"Skipped title" in content
            assert content.count(b'data-testid="replay-week"') == 1
