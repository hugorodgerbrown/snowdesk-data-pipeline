"""
tests/accounts/test_identity.py — the public-facing user identifier (SNOW-549).

Covers ``apps.accounts.identity`` and the ``pwa-user-id`` meta tag it feeds:

* an authenticated user resolves to ``Account.uuid``, never to the
  sequential ``auth.User`` primary key;
* anonymous users, ``None``, and authenticated users with no ``Account``
  row (superusers made with ``createsuperuser``) resolve to ``""``;
* ``base.html`` renders the uuid for a signed-in visitor and an empty tag
  for an anonymous one.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import Client

from apps.accounts.identity import request_identity, user_identity
from tests.factories import AccountFactory, UserFactory

_META_RE = re.compile(rb'<meta name="pwa-user-id" content="([^"]*)"')


def _meta_user_id(body: bytes) -> str:
    """Return the ``pwa-user-id`` meta tag content from a rendered page."""
    match = _META_RE.search(body)
    assert match is not None, "pwa-user-id meta tag missing from the page"
    return match.group(1).decode()


@pytest.mark.django_db
class TestUserIdentity:
    """``user_identity`` maps a user to its public identifier."""

    def test_returns_account_uuid(self) -> None:
        """An authenticated user with an Account resolves to its uuid."""
        account = AccountFactory.create()
        assert user_identity(account.user) == str(account.uuid)

    def test_never_returns_the_user_pk(self) -> None:
        """The identifier is not the sequential auth.User primary key."""
        account = AccountFactory.create()
        identity = user_identity(account.user)
        assert identity != str(account.user.pk)
        assert identity != str(account.pk)

    def test_anonymous_user_is_empty(self) -> None:
        """AnonymousUser has no identity here."""
        assert user_identity(AnonymousUser()) == ""

    def test_none_is_empty(self) -> None:
        """A missing user is tolerated rather than raising."""
        assert user_identity(None) == ""

    def test_user_without_an_account_is_empty(self) -> None:
        """A superuser made with createsuperuser has no Account profile."""
        user = UserFactory.create()
        assert user_identity(user) == ""


@pytest.mark.django_db
class TestRequestIdentity:
    """``request_identity`` reads the user off the request defensively."""

    def test_reads_the_request_user(self) -> None:
        """An authenticated request resolves to that user's account uuid."""
        account = AccountFactory.create()
        request = HttpRequest()
        request.user = account.user
        assert request_identity(request) == str(account.uuid)

    def test_request_without_a_user_attribute_is_empty(self) -> None:
        """A bare HttpRequest — pre-AuthenticationMiddleware — is tolerated."""
        assert request_identity(HttpRequest()) == ""


@pytest.mark.django_db
class TestPwaUserIdMetaTag:
    """``base.html`` bakes the identifier into the meta tag the PWA reads."""

    def test_renders_the_account_uuid_for_a_signed_in_user(self) -> None:
        """The tag carries Account.uuid, not the user PK."""
        account = AccountFactory.create()
        client = Client()
        client.force_login(account.user)

        response = client.get("/")

        rendered = _meta_user_id(response.content)
        assert rendered == str(account.uuid)
        assert rendered != str(account.user.pk)

    def test_renders_empty_for_anonymous(self) -> None:
        """Anonymous visitors get an empty tag — db.js treats it as null."""
        response = Client().get("/")
        assert _meta_user_id(response.content) == ""
