"""
tests/accounts/test_account_hub.py — The /account/ root after SNOW-802.

``/account/manage/`` was one page stacking nine unranked sections. SNOW-667
split it into a hub at ``/account/`` (subscriptions) and ``/account/settings/``;
SNOW-803 sent the favourites page to the map's pins sheet and SNOW-802 sent
the hub there too — a subscription was a bookmark on a region, and bookmarks
are pins. Settings is the one account page left.

These tests pin the parts that are easy to regress:

  * the **redirects** — ``/account/`` and ``/account/manage/`` both 301 to
    the map with the pins sheet open, in every auth state, and the URL
    names still reverse so old bookmarks and in-flight emails land;
  * the **retired write paths** — the subscribe / add-region /
    remove-region endpoints no longer reverse, so a template that still
    named one fails loudly rather than posting into a 404;
  * the **renamed delete control**, which read "Unsubscribe from all alerts"
    while hard-deleting the account;
  * the **passkey Remove control** on settings, an icon control naming its
    passkey.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import NoReverseMatch, reverse

from apps.accounts.models import Account
from tests.factories import (
    AccountFactory,
    FavouriteFactory,
    MicroRegionFactory,
    PasskeyCredentialFactory,
)

_TOKEN_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _client_for(account: Account) -> Client:
    """Return a test client signed in as the account's User."""
    client = Client()
    client.force_login(account.user, backend=_TOKEN_BACKEND)
    return client


# ---------------------------------------------------------------------------
# Routing — the account root is a redirect to the map
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccountRouting:
    """The route shape after SNOW-802."""

    def test_hub_url_is_the_account_root(self) -> None:
        """The name survives, at the account root."""
        assert reverse("accounts:hub") == "/account/"

    @pytest.mark.parametrize("path", ["/account/", "/account/manage/"])
    def test_root_and_manage_redirect_to_the_pins_sheet(self, path: str) -> None:
        """Signed in, both land on the map with the pins sheet open."""
        response = _client_for(AccountFactory.create()).get(path)
        assert response.status_code == 301
        assert response["Location"] == "/?panel=favourites"

    @pytest.mark.parametrize("path", ["/account/", "/account/manage/"])
    def test_anonymous_gets_the_same_redirect(self, path: str) -> None:
        """A redirect renders nothing per-user, so there is no sign-in wall."""
        response = Client().get(path)
        assert response.status_code == 301
        assert response["Location"] == "/?panel=favourites"

    def test_manage_url_name_still_resolves(self) -> None:
        """``reverse("accounts:manage")`` keeps working after the split."""
        assert reverse("accounts:manage") == "/account/manage/"

    @pytest.mark.parametrize(
        "name",
        ["accounts:subscribe", "accounts:add_region", "accounts:remove_region"],
    )
    def test_the_subscription_write_paths_are_gone(self, name: str) -> None:
        """No code path creates or deletes a Subscription row any more."""
        with pytest.raises(NoReverseMatch):
            reverse(name, kwargs={"region_id": "CH-4115"} if "region" in name else {})

    def test_settings_get_renders(self) -> None:
        """GET /account/settings/ renders the settings page."""
        response = _client_for(AccountFactory.create()).get(
            reverse("accounts:settings")
        )
        assert response.status_code == 200

    def test_anonymous_settings_is_redirected_to_sign_in(self) -> None:
        """The one account page is not readable signed out."""
        response = Client().get(reverse("accounts:settings"))
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:sign_in")


# ---------------------------------------------------------------------------
# Reaching the settings page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSettingsIsReachable:
    """Settings has no on-page navigation, so the nav menu is the only route."""

    def test_nav_menu_links_to_settings(self) -> None:
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:settings")).content.decode()
        assert reverse("accounts:settings") in html


# ---------------------------------------------------------------------------
# The renamed destructive control
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeleteAccountControl:
    """The control that read "Unsubscribe from all alerts" while deleting."""

    def test_label_names_the_deletion(self) -> None:
        """The settings page calls it deletion, not unsubscription."""
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:settings")).content.decode()
        assert "Delete account" in html
        assert "Unsubscribe from all alerts" not in html

    def test_control_still_posts_to_delete_account(self) -> None:
        """Renaming the label must not have moved the target."""
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:settings")).content.decode()
        assert reverse("accounts:delete_account") in html

    def test_delete_account_still_hard_deletes(self) -> None:
        """Behaviour is unchanged: the User goes, and the Account and pins with it."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        FavouriteFactory.create(user=account.user, region=region, region_pin=True)
        user_pk = account.user.pk

        client = _client_for(account)
        response = client.post(
            reverse("accounts:delete_account"), HTTP_HX_REQUEST="true"
        )

        assert response.status_code in (200, 204)
        assert not Account.objects.filter(pk=account.pk).exists()
        from django.contrib.auth.models import User

        assert not User.objects.filter(pk=user_pk).exists()


# ---------------------------------------------------------------------------
# The Remove control on settings (SNOW-711)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRemoveControlsAreIconControls:
    """A passkey is removed the same way as everything else — a trash, named."""

    def test_passkey_remove_is_a_trash_naming_its_passkey(self) -> None:
        account = AccountFactory.create()
        passkey = PasskeyCredentialFactory.create(
            user=account.user, name="MacBook passkey"
        )

        html = _client_for(account).get(reverse("accounts:settings")).content.decode()

        assert f'aria-label="Remove {passkey.display_name}"' in html
        assert (
            reverse("accounts:passkey_delete", kwargs={"passkey_uuid": passkey.uuid})
            in html
        )

    def test_passkey_remove_keeps_its_confirmation(self) -> None:
        """The one guard on an irreversible action that just lost its words."""
        account = AccountFactory.create()
        PasskeyCredentialFactory.create(user=account.user)

        html = _client_for(account).get(reverse("accounts:settings")).content.decode()

        assert "Remove this passkey?" in html

    def test_change_email_is_the_shared_button_partial(self) -> None:
        """Not an icon control: it navigates to a flow, so it keeps its words."""
        account = AccountFactory.create()

        html = _client_for(account).get(reverse("accounts:settings")).content.decode()

        assert "Change email" in html
        assert reverse("accounts:change_email") in html
