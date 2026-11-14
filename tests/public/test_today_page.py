"""
tests/public/test_today_page.py — The Today page, where every alert lands
(SNOW-1497; SNOW-1328's /alerts/<date>/ before it).

Covers:
  - /today/: today until any followed place has tomorrow's bulletin, then
    tomorrow; the Today · Tomorrow switch, ?day=, the waiting card.
  - /today/<date>/: that date taken as today, with the another-day strip.
  - /alerts/<date>/: a permanent redirect to /today/<date>/.
  - Every followed region and resort, alerts on or off; never a dropped pin.
  - Signed out and following nothing.
  - The cards themselves: one per followed place A–Z with nothing merged,
    each with its level and move, problems, new snow, provider and links; a
    card for a saved trip that day linking to the trip (SNOW-947).
"""

from __future__ import annotations

import copy
import datetime
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.utils import timezone

from apps.bulletins.models import RegionDayRating
from tests.factories import (
    AccountFactory,
    FavouriteFactory,
    MicroRegionFactory,
    ResortFactory,
    TripFactory,
)
from tests.public.test_alert_pages import _publish


@pytest.fixture()
def region(db: Any) -> Any:
    """Return a region rated moderate yesterday and considerable today."""
    region = MicroRegionFactory.create(name="Martigny-Verbier")
    today = timezone.localdate()
    _publish(region, today - datetime.timedelta(days=1), "moderate")
    _publish(region, today, "considerable")
    return region


def _follow(region: Any, **kwargs: Any) -> Any:
    """Return an account that pins ``region``."""
    account = AccountFactory.create()
    FavouriteFactory.create(user=account.user, region=region, region_pin=True, **kwargs)
    return account


def _today() -> datetime.date:
    """Return the local date."""
    return timezone.localdate()


def _tomorrow() -> datetime.date:
    """Return the local date after today."""
    return _today() + datetime.timedelta(days=1)


class TestWhichDay:
    """/today/ opens on today, or tomorrow once it is out."""

    def test_today_before_the_release(self, client: Client, region: Any) -> None:
        """No bulletin for tomorrow: today, and no switch."""
        client.force_login(_follow(region).user)
        content = client.get("/today/").content.decode()
        assert "Considerable · up from Moderate" in content
        assert 'data-testid="today-switch"' not in content
        assert 'data-testid="another-day-strip"' not in content

    def test_tomorrow_after_the_release(self, client: Client, region: Any) -> None:
        """Tomorrow's bulletin is out: the page opens on it, Tomorrow selected."""
        _publish(region, _tomorrow(), "high")
        client.force_login(_follow(region).user)
        content = client.get("/today/").content.decode()
        assert "High · up from Considerable" in content
        switch = content.split('data-testid="today-switch"')[1].split("</nav>")[0]
        assert 'href="/today/?day=today"' in switch
        assert 'href="/today/?day=next"' in switch
        selected = switch.split('aria-current="page"')[0].rsplit("<a", 1)[1]
        assert "?day=next" in selected

    def test_today_on_request(self, client: Client, region: Any) -> None:
        """After the release, ?day=today shows today, still with the switch."""
        _publish(region, _tomorrow(), "high")
        client.force_login(_follow(region).user)
        content = client.get("/today/?day=today").content.decode()
        assert "Considerable · up from Moderate" in content
        assert 'data-testid="today-switch"' in content

    def test_unknown_day_is_ignored(self, client: Client, region: Any) -> None:
        """An unknown ?day= falls back to the rule."""
        client.force_login(_follow(region).user)
        content = client.get("/today/?day=yesterday").content.decode()
        assert "Considerable · up from Moderate" in content

    def test_a_place_still_waiting(self, client: Client, region: Any) -> None:
        """One place out is enough; the other keeps its card and waits."""
        _publish(region, _tomorrow(), "high")
        account = _follow(region)
        FavouriteFactory.create(
            user=account.user, region=MicroRegionFactory.create(), region_pin=True
        )
        client.force_login(account.user)
        content = client.get("/today/").content.decode()
        assert content.count('data-testid="alerts-day-row"') == 2
        weekday = _tomorrow().strftime("%A")
        assert f"{weekday}&#x27;s bulletin isn&#x27;t out yet." in content

    def test_heading_names_the_day(self, client: Client, region: Any) -> None:
        """The heading is the shown day in full."""
        _publish(region, _tomorrow(), "high")
        client.force_login(_follow(region).user)
        content = client.get("/today/").content.decode()
        assert _tomorrow().strftime("%A") in content


class TestAsOf:
    """/today/<date>/ takes that date as today."""

    def test_a_past_day(self, client: Client, db: Any) -> None:
        """The date itself, a switch to the next day, and the strip."""
        place = MicroRegionFactory.create()
        tenth = datetime.date(2026, 11, 10)
        _publish(place, tenth, "low")
        _publish(place, tenth + datetime.timedelta(days=1), "moderate")
        client.force_login(_follow(place).user)
        content = client.get("/today/2026-11-10/").content.decode()
        assert "Tuesday 10 November" in content
        assert "Low" in content
        assert 'href="/today/2026-11-10/?day=next"' in content
        assert 'data-testid="another-day-strip"' in content
        assert 'href="/today/"' in content
        following = client.get("/today/2026-11-10/?day=next").content.decode()
        assert "Wednesday 11 November" in following
        assert "Moderate · up from Low" in following

    def test_the_real_today_has_no_strip(self, client: Client, region: Any) -> None:
        """Dated with today's date: no another-day strip."""
        client.force_login(_follow(region).user)
        content = client.get(f"/today/{_today().isoformat()}/").content.decode()
        assert 'data-testid="another-day-strip"' not in content

    def test_bad_date_is_404(self, client: Client, db: Any) -> None:
        """A malformed date is not a page, on either route."""
        client.force_login(AccountFactory.create().user)
        assert client.get("/today/not-a-day/").status_code == 404
        assert client.get("/alerts/not-a-day/").status_code == 404

    def test_old_alert_links_redirect(self, client: Client, db: Any) -> None:
        """/alerts/<date>/ is a permanent redirect to the same day."""
        response = client.get("/alerts/2026-11-14/")
        assert response.status_code == 301
        assert response["Location"] == "/today/2026-11-14/"


class TestWhichPlaces:
    """Every region and resort followed; no dropped pin."""

    def test_alerts_off_is_listed(self, client: Client, region: Any) -> None:
        """A place with its alerts switched off still has a card."""
        client.force_login(_follow(region, alerts=False).user)
        content = client.get("/today/").content.decode()
        assert content.count('data-testid="alerts-day-row"') == 1

    def test_dropped_pin_is_not(self, client: Client, db: Any) -> None:
        """A dropped pin marks a spot, not a place followed."""
        account = AccountFactory.create()
        FavouriteFactory.create(user=account.user)
        client.force_login(account.user)
        content = client.get("/today/").content.decode()
        assert 'data-testid="alerts-day-row"' not in content
        assert 'data-testid="today-empty"' in content


class TestNoCards:
    """Signed out, and following nothing."""

    def test_signed_out(self, client: Client, db: Any) -> None:
        """A visitor sees what the page is for, sign-in back here, and the map."""
        response = client.get("/today/")
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="today-signed-out"' in content
        assert "/account/sign-in/?next=/today/" in content
        assert 'href="/map/"' in content
        assert 'data-testid="alerts-day-row"' not in content

    def test_follows_nothing(self, client: Client, db: Any) -> None:
        """No places: what the page is for and one way to get some."""
        client.force_login(AccountFactory.create().user)
        content = client.get("/today/2026-01-17/").content.decode()
        assert 'data-testid="today-empty"' in content
        assert "Find a place on the map" in content
        assert "Pin a region or save a resort" in content


class TestCards:
    """The cards, on /today/<date>/ for the real today, as on /today/<date>/ before."""

    def test_one_row_per_place(self, client: Client, region: Any) -> None:
        """Each followed place, with its move and its map link."""
        account = AccountFactory.create()
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        FavouriteFactory.create(
            user=account.user, region=MicroRegionFactory.create(), region_pin=True
        )
        client.force_login(account.user)
        today = timezone.localdate().isoformat()
        content = client.get(f"/today/{today}/").content.decode()
        assert content.count('data-testid="alerts-day-row"') == 2
        assert "Considerable · up from Moderate" in content
        assert f"/map/?d={today}#{region.region_id}" in content
        assert "No bulletin for this day." in content

    def test_a_card_carries_the_bulletin(self, client: Client, region: Any) -> None:
        """The tile, the problems, the provider and its own bulletin."""
        account = AccountFactory.create()
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        client.force_login(account.user)
        today = timezone.localdate()
        content = client.get(f"/today/{today.isoformat()}/").content.decode()
        assert (
            'class="danger-tile' in content and 'data-level="considerable"' in content
        )
        assert content.count('data-testid="alerts-day-problem"') == 1
        assert "Wind slab" in content
        # The band as the bulletin page writes it.
        assert "N–E · above 2200m" in content
        assert "<svg" in content  # the aspect rose
        assert "SLF · released" in content
        assert 'href="https://www.slf.ch/bulletin.pdf"' in content
        assert "SLF bulletin" in content

    def test_new_snow_is_named(self, client: Client, region: Any) -> None:
        """A forecast of five centimetres or more is named on the card."""
        account = AccountFactory.create()
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        client.force_login(account.user)
        today = timezone.localdate()
        with patch(
            "apps.bulletins.services.alerts.forecast_new_snow", return_value=20.0
        ):
            content = client.get(f"/today/{today.isoformat()}/").content.decode()
        assert "20 cm new snow forecast." in content

    def test_two_names_in_one_region_are_two_cards(
        self, client: Client, region: Any
    ) -> None:
        """Nothing is merged: each name is its own card, A–Z."""
        account = AccountFactory.create()
        for name in ("Verbier", "Bruson"):
            FavouriteFactory.create(
                user=account.user,
                region=None,
                resort=ResortFactory.create(name=name, region=region),
            )
        client.force_login(account.user)
        today = timezone.localdate().isoformat()
        content = client.get(f"/today/{today}/").content.decode()
        assert content.count('data-testid="alerts-day-row"') == 2
        assert "Bruson, Verbier" not in content
        assert content.index("Bruson") < content.index("Verbier")

    def test_a_split_day_keeps_each_period(self, client: Client, db: Any) -> None:
        """Each period's problem is its own line, named by its period."""
        today = timezone.localdate()
        region = MicroRegionFactory.create()
        _publish(region, today, "considerable")
        bulletin = RegionDayRating.objects.get(
            region=region, date=today
        ).source_bulletin
        assert bulletin is not None
        rm = bulletin.render_model
        (trait,) = rm["traits"]
        later = copy.deepcopy(trait)
        trait["time_period"] = trait["problems"][0]["time_period"] = "earlier"
        later["time_period"] = later["problems"][0]["time_period"] = "later"
        later["problems"][0]["aspects"] = ["E", "W"]
        rm["traits"] = [trait, later]
        bulletin.save(update_fields=["render_model"])
        account = AccountFactory.create()
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        client.force_login(account.user)
        content = client.get(f"/today/{today.isoformat()}/").content.decode()
        assert content.count('data-testid="alerts-day-problem"') == 2
        assert "Earlier" in content and "Later" in content
        assert "N–E · above" in content and "E, W · above" in content

    def test_two_trips_in_one_region_open_their_own_pages(
        self, client: Client, region: Any
    ) -> None:
        """Two trips folded into a pin: each card opens its own trip, the pin the map."""
        account = AccountFactory.create()
        today = timezone.localdate()
        FavouriteFactory.create(
            user=account.user, region=region, region_pin=True, name="Pin"
        )
        first = TripFactory.create(created_by=account.user, date=today, name="Alpha")
        second = TripFactory.create(created_by=account.user, date=today, name="Beta")
        client.force_login(account.user)
        with patch(
            "apps.bulletins.services.alerts.track_regions", return_value=[region]
        ):
            content = client.get(f"/today/{today.isoformat()}/").content.decode()
        cards = content.split('data-testid="alerts-day-row"')[1:]
        assert len(cards) == 3
        alpha, beta, pin = cards
        assert (
            f"/trips/{first.uuid}/" in alpha and f"/trips/{second.uuid}/" not in alpha
        )
        assert f"/trips/{second.uuid}/" in beta and f"/trips/{first.uuid}/" not in beta
        assert "/trips/" not in pin and f"#{region.region_id}" in pin

    def test_trip_that_day_is_a_row(self, client: Client, region: Any) -> None:
        """A saved trip on the day is a row that opens the trip."""
        account = AccountFactory.create()
        today = timezone.localdate()
        trip = TripFactory.create(
            created_by=account.user, date=today, name="Mont Fort loop"
        )
        client.force_login(account.user)
        with patch(
            "apps.bulletins.services.alerts.track_regions", return_value=[region]
        ):
            content = client.get(f"/today/{today.isoformat()}/").content.decode()
        assert content.count('data-testid="alerts-day-row"') == 1
        assert "Mont Fort loop" in content
        assert f"/trips/{trip.uuid}/" in content
        assert "Open the trip" in content

    def test_a_trip_across_regions_names_each(
        self, client: Client, region: Any
    ) -> None:
        """A multi-region trip is a card per region, each with its own level.

        The second region is unchanged and the first rose, so neither card
        may read the other's level (SNOW-945).
        """
        account = AccountFactory.create()
        today = timezone.localdate()
        second = MicroRegionFactory.create(name="Arolla")
        _publish(second, today - datetime.timedelta(days=1), "moderate")
        _publish(second, today, "moderate")
        trip = TripFactory.create(created_by=account.user, date=today, name="Haute")
        client.force_login(account.user)
        with patch(
            "apps.bulletins.services.alerts.track_regions",
            return_value=[region, second],
        ):
            content = client.get(f"/today/{today.isoformat()}/").content.decode()
        assert content.count('data-testid="alerts-day-row"') == 2
        assert f"Haute · {region.name}" in content
        assert "Haute · Arolla" in content
        assert "Considerable · up from Moderate" in content
        assert "Moderate · no change" in content
        assert content.count(f"/trips/{trip.uuid}/") == 2

    def test_todays_trip_stays_on_tomorrows_page(
        self, client: Client, region: Any
    ) -> None:
        """On tomorrow, today's trip keeps a row of its own day."""
        account = AccountFactory.create()
        today = timezone.localdate()
        tomorrow = today + datetime.timedelta(days=1)
        TripFactory.create(created_by=account.user, date=today, name="Mont Fort")
        client.force_login(account.user)
        with patch(
            "apps.bulletins.services.alerts.track_regions", return_value=[region]
        ):
            content = client.get("/today/?day=next").content.decode()
            past = client.get(
                f"/today/{tomorrow.isoformat()}/?day=next"
            ).content.decode()
        assert content.count('data-testid="alerts-day-row"') == 1
        assert "Mont Fort ·" in content
        assert f"/map/?d={today.isoformat()}#{region.region_id}" not in content
        assert "Considerable · up from Moderate" in content
        # Once the trip's day is past, it is not carried onto later pages.
        assert "Mont Fort" not in past
