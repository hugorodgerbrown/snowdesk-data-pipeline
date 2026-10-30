"""
tests/accounts/test_alert_views.py — The bulletin alert settings (SNOW-939).

The Alerts group on the settings page, its two HTMX posts, the test push,
the bell's list (SNOW-1333) and the one-click unsubscribe — all behind the
bulletin_alerts flag except the unsubscribe link, which must work for
anyone holding one.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory
from waffle.testutils import override_flag

from apps.accounts.alert_views import alert_list_url
from apps.accounts.models import Account
from apps.accounts.services.alert_email import unsubscribe_token
from tests.factories import (
    AccountFactory,
    BulletinAlertFactory,
    FavouriteFactory,
    MicroRegionFactory,
    PushSubscriptionFactory,
    UserFactory,
)

HTMX: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture()
def alerts_flag(db: Any) -> Any:
    """Turn the bulletin_alerts flag on for the test."""
    with override_flag("bulletin_alerts", active=True):
        yield


@pytest.fixture()
def account(db: Any) -> Account:
    """Return a verified account with one region pin."""
    account = AccountFactory.create()
    FavouriteFactory.create(
        user=account.user,
        region=MicroRegionFactory.create(name="Martigny-Verbier"),
        region_pin=True,
    )
    return account


@pytest.fixture()
def client_for(account: Account) -> Client:
    """Return a client signed in as ``account``."""
    c = Client()
    c.force_login(account.user)
    return c


@pytest.mark.usefixtures("alerts_flag")
class TestSettingsPage:
    """The group renders on /account/settings/."""

    def test_off_by_default(self, client_for: Client) -> None:
        """Off: the mode row only, Off selected."""
        html = client_for.get("/account/settings/").content.decode()
        assert 'id="settings-alerts"' in html
        assert 'value="OFF"' in html
        assert "data-alerts-optin" not in html
        assert 'name="vapid-public-key"' in html

    def test_flag_off_hides_the_group(self, client_for: Client) -> None:
        """Without the flag nothing about alerts renders."""
        with override_flag("bulletin_alerts", active=False):
            html = client_for.get("/account/settings/").content.decode()
        assert "settings-alerts" not in html


@pytest.mark.usefixtures("alerts_flag")
class TestAlertSettings:
    """POST /account/partials/alerts/."""

    def test_turning_on_stamps_enabled_at(
        self, client_for: Client, account: Account
    ) -> None:
        """Off → New bulletin stamps alerts_enabled_at and shows the rows."""
        response = client_for.post(
            "/account/partials/alerts/", {"notify_when": "NEW_BULLETIN"}, **HTMX
        )
        assert response.status_code == 200
        account.refresh_from_db()
        assert account.notify_when == Account.NotifyWhen.NEW_BULLETIN
        assert account.alerts_enabled_at is not None
        html = response.content.decode()
        assert "data-alerts-optin" in html
        assert "Martigny-Verbier" in html

    def test_changing_mode_keeps_enabled_at(
        self, client_for: Client, account: Account
    ) -> None:
        """A move between two on modes is not a fresh start."""
        client_for.post("/account/partials/alerts/", {"notify_when": "CHANGES"}, **HTMX)
        account.refresh_from_db()
        first = account.alerts_enabled_at
        client_for.post(
            "/account/partials/alerts/",
            {"notify_when": "NEW_BULLETIN", "alert_email": "ALWAYS"},
            **HTMX,
        )
        account.refresh_from_db()
        assert account.alerts_enabled_at == first
        assert account.alert_email == Account.AlertEmail.ALWAYS

    def test_bad_values_are_ignored(self, client_for: Client, account: Account) -> None:
        """An unknown choice leaves the stored one."""
        client_for.post(
            "/account/partials/alerts/",
            {"notify_when": "LOUD", "alert_email": "SOMETIMES"},
            **HTMX,
        )
        account.refresh_from_db()
        assert account.notify_when == Account.NotifyWhen.OFF
        assert account.alert_email == Account.AlertEmail.FALLBACK

    def test_requires_htmx(self, client_for: Client) -> None:
        """A plain POST is refused (Invariant 4)."""
        response = client_for.post("/account/partials/alerts/", {"notify_when": "OFF"})
        assert response.status_code == 400

    def test_flag_off_is_404(self, client_for: Client) -> None:
        """Without the flag the endpoint does not exist."""
        with override_flag("bulletin_alerts", active=False):
            response = client_for.post(
                "/account/partials/alerts/", {"notify_when": "CHANGES"}, **HTMX
            )
        assert response.status_code == 404


@pytest.mark.usefixtures("alerts_flag")
def test_alert_place_switch(client_for: Client, account: Account) -> None:
    """One place's switch turns its alerts off and on."""
    favourite = account.user.favourites.get()
    url = f"/account/partials/alerts/{favourite.uuid}/"
    client_for.post(url, {}, **HTMX)
    favourite.refresh_from_db()
    assert favourite.alerts is False
    client_for.post(url, {"alerts": "on"}, **HTMX)
    favourite.refresh_from_db()
    assert favourite.alerts is True


@pytest.mark.usefixtures("alerts_flag")
def test_alert_place_of_another_user_is_404(client_for: Client) -> None:
    """A favourite belonging to someone else cannot be switched."""
    other = FavouriteFactory.create(region=MicroRegionFactory.create(), region_pin=True)
    response = client_for.post(f"/account/partials/alerts/{other.uuid}/", {}, **HTMX)
    assert response.status_code == 404


@pytest.mark.usefixtures("alerts_flag")
def test_alert_test_pushes_every_device(client_for: Client, account: Account) -> None:
    """The test alert goes to each active subscription and says what landed."""
    PushSubscriptionFactory.create(account=account)
    PushSubscriptionFactory.create(account=account)
    with patch(
        "apps.accounts.alert_views.dispatch_push",
        side_effect=[{"ok": True, "status": 201}, {"ok": False, "status": 410}],
    ) as push:
        response = client_for.post("/account/alerts/test/")
    assert response.json() == {"ok": True, "devices": 2, "delivered": 1}
    assert push.call_count == 2


@pytest.mark.usefixtures("alerts_flag")
def test_alert_test_reports_a_failed_send(client_for: Client, account: Account) -> None:
    """A send the push service refused is not reported as on its way."""
    PushSubscriptionFactory.create(account=account)
    with patch(
        "apps.accounts.alert_views.dispatch_push",
        return_value={"ok": False, "status": None, "error": "bad key"},
    ):
        response = client_for.post("/account/alerts/test/")
    assert response.json() == {"ok": False, "devices": 1, "delivered": 0}


@pytest.mark.usefixtures("alerts_flag")
def test_alert_test_with_no_device(client_for: Client) -> None:
    """An account with no subscription is told so."""
    response = client_for.post("/account/alerts/test/")
    assert response.json() == {"ok": False, "devices": 0, "delivered": 0}


class TestUnsubscribe:
    """The link in every alert email."""

    def test_get_confirms_without_changing(self, account: Account) -> None:
        """GET shows the button and changes nothing."""
        url = f"/account/alerts/unsubscribe/{unsubscribe_token(account)}/"
        response = Client().get(url)
        assert response.status_code == 200
        account.refresh_from_db()
        assert account.alert_email == Account.AlertEmail.FALLBACK

    def test_one_click_post_without_session_or_csrf(self, account: Account) -> None:
        """The mail provider's POST turns alert email off."""
        url = f"/account/alerts/unsubscribe/{unsubscribe_token(account)}/"
        response = Client(enforce_csrf_checks=True).post(url)
        assert response.status_code == 200
        assert b"alert-unsubscribe-done" in response.content
        account.refresh_from_db()
        assert account.alert_email == Account.AlertEmail.NEVER

    def test_bad_token_is_404(self, db: Any) -> None:
        """A forged link names nobody."""
        assert Client().get("/account/alerts/unsubscribe/nope/").status_code == 404


@pytest.mark.usefixtures("alerts_flag")
class TestAlertRecent:
    """SNOW-1333: the map's bell reads the past week's alerts as JSON."""

    def test_lists_the_sent_messages(
        self, client_for: Client, account: Account
    ) -> None:
        """Each message comes back with its words, link and send time."""
        account.notify_when = Account.NotifyWhen.NEW_BULLETIN
        account.save(update_fields=["notify_when"])
        BulletinAlertFactory.create(
            account=account,
            title="Verbier bulletin",
            body="Valid from Friday at 5 pm to Saturday at 5 pm.",
            url="/alerts/2026-10-31/",
        )
        response = client_for.get("/account/alerts/recent/")
        assert response.status_code == 200
        assert "no-cache" in response["Cache-Control"]
        (alert,) = response.json()["alerts"]
        assert alert["title"] == "Verbier bulletin"
        assert alert["url"] == "/alerts/2026-10-31/"
        assert alert["sent_at"]
        assert alert["sent"]

    def test_alerts_off_is_empty(self, client_for: Client, account: Account) -> None:
        """With alerts off nothing is listed, so the bell stays hidden."""
        BulletinAlertFactory.create(account=account, title="Verbier bulletin")
        account.notify_when = Account.NotifyWhen.OFF
        account.save(update_fields=["notify_when"])
        assert client_for.get("/account/alerts/recent/").json() == {"alerts": []}

    def test_flag_off_is_404(self, client_for: Client) -> None:
        """Behind the flag, as every alert endpoint is."""
        with override_flag("bulletin_alerts", active=False):
            assert client_for.get("/account/alerts/recent/").status_code == 404

    def test_signed_out_redirects(self, db: Any) -> None:
        """A visitor is sent to sign in, never handed a list."""
        assert Client().get("/account/alerts/recent/").status_code == 302

    def test_post_is_refused(self, client_for: Client) -> None:
        """GET only."""
        assert client_for.post("/account/alerts/recent/").status_code == 405


class TestAlertListUrl:
    """SNOW-1333: the map draws a bell only for an account with alerts on."""

    def _request(self, user: Any) -> Any:
        request = RequestFactory().get("/map/")
        request.user = user
        return request

    def test_alerts_on(self, alerts_flag: Any, account: Account) -> None:
        """An account with alerts on gets the list's url."""
        account.notify_when = Account.NotifyWhen.NEW_BULLETIN
        account.save(update_fields=["notify_when"])
        assert alert_list_url(self._request(account.user)) == "/account/alerts/recent/"

    def test_alerts_off(self, alerts_flag: Any, account: Account) -> None:
        """Alerts off: no bell."""
        account.notify_when = Account.NotifyWhen.OFF
        account.save(update_fields=["notify_when"])
        assert alert_list_url(self._request(account.user)) == ""

    def test_no_account(self, alerts_flag: Any) -> None:
        """A user with no Account profile: no bell."""
        assert alert_list_url(self._request(UserFactory.create())) == ""

    def test_signed_out(self, alerts_flag: Any) -> None:
        """A visitor: no bell."""
        assert alert_list_url(self._request(AnonymousUser())) == ""
