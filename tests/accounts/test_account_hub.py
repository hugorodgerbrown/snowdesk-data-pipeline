"""
tests/accounts/test_account_hub.py — The /account/ hub split (SNOW-667).

``/account/manage/`` was one page stacking nine unranked sections. SNOW-667
split it into a hub at ``/account/`` and a settings page at
``/account/settings/``, tied together by a sub-nav whose groups come from
the nav dropdown.

The sub-nav this ticket originally shipped was cut — it did not earn its
place. SNOW-705 designs the account area's navigation and layouts; until
then the two pages are reached from the nav menu.

These tests pin the parts of that split which are easy to regress:

  * the **route collision** — ``/account/`` used to answer 405, because
    ``subscribe_partial`` (``@require_POST``) held the ``""`` route. The hub
    owns it now and ``subscribe_partial`` moved to ``/account/subscribe/``
    keeping its URL name, so a reverse-by-name still reaches it;
  * the **legacy redirect** — ``/account/manage/`` 301s to the hub and
    ``reverse("accounts:manage")`` still resolves, so old bookmarks and
    in-flight emails land somewhere sensible;
  * the **renamed delete control**, which read "Unsubscribe from all alerts"
    while hard-deleting the account.

Section membership (which block lives on which page) is covered by the
existing classes in ``test_views.py``.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from tests.factories import AccountFactory, MicroRegionFactory, SubscriptionFactory

_TOKEN_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _client_for(account: Account) -> Client:
    """Return a test client signed in as the account's User."""
    client = Client()
    client.force_login(account.user, backend=_TOKEN_BACKEND)
    return client


# ---------------------------------------------------------------------------
# Routing — the collision and the legacy redirect
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccountRouting:
    """The route shape SNOW-667 introduced."""

    def test_hub_get_renders(self) -> None:
        """GET /account/ renders the hub.

        The regression test for the collision: before SNOW-667 this path was
        bound to ``subscribe_partial``, which is ``@require_POST``, so a GET
        of the account root answered **405** rather than a page.
        """
        client = _client_for(AccountFactory.create())
        response = client.get(reverse("accounts:hub"))
        assert response.status_code == 200

    def test_hub_url_is_the_account_root(self) -> None:
        """The hub is mounted at /account/, not a child path."""
        assert reverse("accounts:hub") == "/account/"

    def test_manage_redirects_permanently_to_hub(self) -> None:
        """/account/manage/ 301s to the hub, for old bookmarks and emails."""
        client = _client_for(AccountFactory.create())
        response = client.get("/account/manage/")
        assert response.status_code == 301
        assert response["Location"] == reverse("accounts:hub")

    def test_manage_url_name_still_resolves(self) -> None:
        """``reverse("accounts:manage")`` keeps working after the split.

        The name is retained deliberately — dropping it would turn every
        stale reverse into a ``NoReverseMatch`` at render time rather than a
        redirect at request time.
        """
        assert reverse("accounts:manage") == "/account/manage/"

    def test_subscribe_moved_but_keeps_its_url_name(self) -> None:
        """subscribe_partial answers on its new path under the same name."""
        assert reverse("accounts:subscribe") == "/account/subscribe/"

    def test_subscribe_still_rejects_a_plain_get(self) -> None:
        """The moved view keeps @require_POST + @require_htmx.

        Asserted so the move cannot silently relax the guards: a GET must
        still be refused, exactly as it was on the old path.
        """
        response = Client().get(reverse("accounts:subscribe"))
        assert response.status_code == 405

    def test_settings_get_renders(self) -> None:
        """GET /account/settings/ renders the settings page."""
        client = _client_for(AccountFactory.create())
        response = client.get(reverse("accounts:settings"))
        assert response.status_code == 200

    @pytest.mark.parametrize("name", ["accounts:hub", "accounts:settings"])
    def test_anonymous_is_redirected_to_sign_in(self, name: str) -> None:
        """Neither child route is readable signed out."""
        response = Client().get(reverse(name))
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:sign_in")


# ---------------------------------------------------------------------------
# Reaching the settings page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSettingsIsReachable:
    """Settings has no on-page navigation, so the nav menu is the only route.

    Pinned because it is easy to lose: nothing else on the hub links to
    /account/settings/, and SNOW-705 will replace this arrangement. If that
    nav entry disappears before the design lands, the page is orphaned.
    """

    def test_nav_menu_links_to_settings(self) -> None:
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:hub")).content.decode()
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
        assert "Delete your account" in html
        assert "Unsubscribe from all alerts" not in html

    def test_control_still_posts_to_delete_account(self) -> None:
        """Renaming the label must not have moved the target."""
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:settings")).content.decode()
        assert reverse("accounts:delete_account") in html

    def test_delete_account_still_hard_deletes(self) -> None:
        """Behaviour is unchanged: the User goes, and the Account with it."""
        account = AccountFactory.create()
        region = MicroRegionFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        user_pk = account.user.pk

        client = _client_for(account)
        response = client.post(
            reverse("accounts:delete_account"), HTTP_HX_REQUEST="true"
        )

        assert response.status_code in (200, 204)
        assert not Account.objects.filter(pk=account.pk).exists()
        from django.contrib.auth.models import User

        assert not User.objects.filter(pk=user_pk).exists()
