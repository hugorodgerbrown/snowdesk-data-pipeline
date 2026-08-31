"""
tests/accounts/test_delete_account_erasure.py — deletion actually erases (SNOW-774).

The Privacy Policy offers the right to erasure. Before SNOW-774 the code did
not deliver it: ``RequestLog.account`` was ``SET_NULL``, so deleting an
account nulled the label on its request rows and left every identifier —
``ip_address``, ``city``, ``latitude``, ``longitude``, ``user_agent``,
``session_key`` — sitting in the table.

Two halves have to hold together, and each has its own failure mode:

* The ``CASCADE`` on ``RequestLog.account`` removes rows written while the
  person was signed in.
* ``_referenced_request_log_ids`` removes the rows written *before* the
  account existed. Sign-up is anonymous, so that row has ``account=None``
  and no cascade can reach it; it is tied to the account only by
  ``Account.acquisition_request`` and ``Subscription.subscribed_via``
  pointing at it, both ``SET_NULL``.

The second is the half that was missing and the half a future refactor is
most likely to drop, because the row it targets looks unrelated to the
account until you follow the FK backwards.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from apps.bulletins.models import BulletinShareClick
from apps.core.models import RequestLog
from tests.factories import (
    AccountFactory,
    BulletinShareFactory,
    DownloadAreaFactory,
    FavouriteFactory,
    FieldObservationFactory,
    MicroRegionFactory,
    PasskeyCredentialFactory,
    PushSubscriptionFactory,
    RequestLogFactory,
    RouteFactory,
    SubscriptionFactory,
)

# Every column that could identify the person behind a row. A test that
# asserts "the row is gone" is weaker than one that names what must not
# survive, because it keeps passing if someone later blanks the row instead
# of deleting it while leaving one of these populated.
IDENTIFYING_FIELDS = (
    "ip_address",
    "city",
    "latitude",
    "longitude",
    "user_agent",
    "session_key",
)


def _client_for(account: Account) -> Client:
    """Return a client signed in as *account*'s user."""
    client = Client()
    client.force_login(account.user)
    return client


def _delete(account: Account) -> None:
    """Post the account-deletion request as *account*."""
    response = _client_for(account).post(
        reverse("accounts:delete_account"), HTTP_HX_REQUEST="true"
    )
    assert response.status_code in (200, 204)


@pytest.mark.django_db
class TestSignedInRowsAreDeleted:
    """The CASCADE half — rows written while authenticated."""

    def test_rows_linked_to_the_account_go(self) -> None:
        """These carry account=<the account>, so the FK cascade takes them."""
        account = AccountFactory.create()
        log = RequestLogFactory.create(account=account, ip_address="203.0.113.7")

        _delete(account)

        assert not RequestLog.objects.filter(pk=log.pk).exists()

    def test_another_accounts_rows_are_untouched(self) -> None:
        """The cascade is scoped to the account being deleted.

        A cascade that took the whole table would pass every other test in
        this file while destroying everyone else's data.
        """
        account = AccountFactory.create()
        bystander = AccountFactory.create()
        theirs = RequestLogFactory.create(account=bystander)

        _delete(account)

        assert RequestLog.objects.filter(pk=theirs.pk).exists()

    def test_an_unrelated_anonymous_row_is_untouched(self) -> None:
        """A row belonging to nobody is not this deletion's business.

        Those are bounded by ``purge_request_logs`` instead.
        """
        account = AccountFactory.create()
        stray = RequestLogFactory.create(account=None)

        _delete(account)

        assert RequestLog.objects.filter(pk=stray.pk).exists()


@pytest.mark.django_db
class TestReferencedRowsAreDeleted:
    """The half a cascade cannot reach — rows written before the account.

    This is the gap SNOW-774 closed. Each row here has ``account=None``, so
    it looks anonymous; it is tied to the person only by the account or
    subscription pointing at it.
    """

    def test_the_sign_up_row_goes(self) -> None:
        """``Account.acquisition_request`` — the sign-up request itself."""
        signup = RequestLogFactory.create(account=None, ip_address="203.0.113.9")
        account = AccountFactory.create(acquisition_request=signup)

        _delete(account)

        assert not RequestLog.objects.filter(pk=signup.pk).exists()

    def test_the_subscribe_row_goes(self) -> None:
        """``Subscription.subscribed_via`` — one per subscribed region."""
        via = RequestLogFactory.create(account=None, ip_address="203.0.113.10")
        account = AccountFactory.create()
        SubscriptionFactory.create(
            account=account,
            region=MicroRegionFactory.create(),
            subscribed_via=via,
        )

        _delete(account)

        assert not RequestLog.objects.filter(pk=via.pk).exists()

    def test_every_subscribed_region_is_covered(self) -> None:
        """Several subscriptions mean several rows, not just the first."""
        account = AccountFactory.create()
        vias = [RequestLogFactory.create(account=None) for _ in range(3)]
        for via in vias:
            SubscriptionFactory.create(
                account=account,
                region=MicroRegionFactory.create(),
                subscribed_via=via,
            )

        _delete(account)

        assert not RequestLog.objects.filter(pk__in=[v.pk for v in vias]).exists()


@pytest.mark.django_db
class TestNoIdentifierSurvives:
    """The end-to-end promise, stated the way the Privacy Policy states it."""

    def test_a_full_account_lifecycle_leaves_nothing(self) -> None:
        """Sign-up, subscribe and signed-in activity — all of it erased.

        Asserts on the columns rather than on row counts: a future change
        that blanked rows instead of deleting them should fail here unless
        it blanks every identifier.
        """
        signup = RequestLogFactory.create(
            account=None, ip_address="203.0.113.11", city="Zurich", latitude=47.4
        )
        account = AccountFactory.create(acquisition_request=signup)
        via = RequestLogFactory.create(
            account=None, ip_address="203.0.113.12", city="Bern", latitude=46.9
        )
        SubscriptionFactory.create(
            account=account,
            region=MicroRegionFactory.create(),
            subscribed_via=via,
        )
        signed_in = RequestLogFactory.create(
            account=account, ip_address="203.0.113.13", city="Sion", latitude=46.2
        )
        user_pk = account.user.pk

        _delete(account)

        assert not User.objects.filter(pk=user_pk).exists()
        assert not Account.objects.filter(pk=account.pk).exists()

        survivors = RequestLog.objects.filter(pk__in=[signup.pk, via.pk, signed_in.pk])
        assert not survivors.exists(), (
            "request rows survived account deletion: "
            f"{list(survivors.values('pk', *IDENTIFYING_FIELDS))}"
        )


@pytest.mark.django_db
class TestASharedLinkDoesNotBlockDeletion:
    """A share click must not make its follower undeletable.

    ``BulletinShareClick.request`` used to be ``PROTECT``. That was
    survivable while ``RequestLog.account`` was ``SET_NULL`` — nothing tried
    to delete the log row — but the moment deletion started cascading, an
    account holder who had followed any ``/s/<token>/`` link could no longer
    delete their account: the cascade hit the protected row and the request
    raised ``ProtectedError``.

    The failure needed a signed-in user, a share link and a deletion in the
    same test to appear, which is why the first version of this suite went
    green without it.
    """

    def _click(self, log: RequestLog) -> BulletinShareClick:
        """Record a share-link follow against *log*."""
        return BulletinShareClick.objects.create(
            share=BulletinShareFactory.create(),
            request=log,
            visitor_hash="0123456789abcdef",
        )

    def test_deletion_succeeds_for_someone_who_followed_a_share(self) -> None:
        """The regression test: this raised ProtectedError and 500'd."""
        account = AccountFactory.create()
        self._click(RequestLogFactory.create(account=account))

        _delete(account)

        assert not Account.objects.filter(pk=account.pk).exists()

    def test_the_click_and_its_request_context_both_go(self) -> None:
        """``visitor_hash`` is derived from the person's IP and user agent.

        Leaving the click behind would keep a pseudonymous identifier for
        someone who asked to be forgotten, so the click is not merely
        collateral — removing it is the point.
        """
        account = AccountFactory.create()
        log = RequestLogFactory.create(account=account)
        click = self._click(log)

        _delete(account)

        assert not BulletinShareClick.objects.filter(pk=click.pk).exists()
        assert not RequestLog.objects.filter(pk=log.pk).exists()

    def test_a_click_from_the_sign_up_request_also_goes(self) -> None:
        """The orphan path and the cascade path must both survive a click."""
        signup = RequestLogFactory.create(account=None)
        account = AccountFactory.create(acquisition_request=signup)
        click = self._click(signup)

        _delete(account)

        assert not BulletinShareClick.objects.filter(pk=click.pk).exists()
        assert not RequestLog.objects.filter(pk=signup.pk).exists()

    def test_another_visitors_click_on_the_same_share_survives(self) -> None:
        """Erasure is scoped to one person, not to the share's whole audience.

        The share owner keeps every click that was not this account's.
        """
        share = BulletinShareFactory.create()
        account = AccountFactory.create()
        mine = BulletinShareClick.objects.create(
            share=share,
            request=RequestLogFactory.create(account=account),
            visitor_hash="mine",
        )
        theirs = BulletinShareClick.objects.create(
            share=share,
            request=RequestLogFactory.create(account=None),
            visitor_hash="theirs",
        )

        _delete(account)

        assert not BulletinShareClick.objects.filter(pk=mine.pk).exists()
        assert BulletinShareClick.objects.filter(pk=theirs.pk).exists()


@pytest.mark.django_db
class TestEveryKindOfAttachedDataDeletes:
    """A fully-populated account deletes without a ProtectedError.

    The share-click bug (``BulletinShareClick.request`` was ``PROTECT``)
    was invisible until an account happened to own the protected row, and
    it surfaced as a 500 on the one action a user is entitled to. Any
    future ``PROTECT`` added on a path that leads back to a User will fail
    here rather than in production, which is the point of loading one
    account up with every kind of thing an account can own.
    """

    def test_an_account_owning_everything_can_be_deleted(self) -> None:
        account = AccountFactory.create()
        user = account.user

        SubscriptionFactory.create(
            account=account,
            region=MicroRegionFactory.create(),
            subscribed_via=RequestLogFactory.create(account=None),
        )
        FavouriteFactory.create(user=user)
        FieldObservationFactory.create(user=user)
        RouteFactory.create(user=user)
        DownloadAreaFactory.create(user=user)
        PasskeyCredentialFactory.create(user=user)
        PushSubscriptionFactory.create(account=account)
        BulletinShareClick.objects.create(
            share=BulletinShareFactory.create(),
            request=RequestLogFactory.create(account=account),
            visitor_hash="0123456789abcdef",
        )

        _delete(account)

        assert not Account.objects.filter(pk=account.pk).exists()
        assert not User.objects.filter(pk=user.pk).exists()


@pytest.mark.django_db(transaction=True)
class TestErasureIsAllOrNothing:
    """A half-finished erasure is worse than a failed one.

    The account going while its request rows stay leaves the person with
    no way back in to ask again, and leaves the rows carrying their IP
    address and coordinates attached to nothing that would lead you to
    them. The two deletes are one transaction so that cannot happen.
    """

    def test_a_failure_in_the_second_delete_rolls_back_the_first(self) -> None:
        """If the orphan sweep raises, the account must still be there."""
        signup = RequestLogFactory.create(account=None)
        account = AccountFactory.create(acquisition_request=signup)
        user_pk = account.user.pk

        with mock.patch(
            "apps.accounts.services.deletion.RequestLog.objects.filter",
            side_effect=RuntimeError("database went away"),
        ):
            client = _client_for(account)
            with pytest.raises(RuntimeError):
                client.post(reverse("accounts:delete_account"), HTTP_HX_REQUEST="true")

        assert User.objects.filter(pk=user_pk).exists()
        assert RequestLog.objects.filter(pk=signup.pk).exists()
