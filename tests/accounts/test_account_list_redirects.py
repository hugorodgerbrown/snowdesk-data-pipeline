"""
tests/accounts/test_account_list_redirects.py — SNOW-803: the three account
list pages are permanent redirects to the map with the matching sheet open.

Covers:
  - ``/account/favourites/`` → ``/?panel=favourites``
  - ``/account/observations/`` → ``/?panel=reports``
  - ``/account/routes/`` → ``/?panel=routes``
  - The redirect is the same in every auth state (a redirect renders
    nothing per-user), and following it lands on the map.
  - The old URL names still reverse — an old link resolves — but nothing
    the site renders links to them (tests/public/test_nav_partial.py).
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from tests.factories import UserFactory

CASES = [
    ("accounts:favourites", "/account/favourites/", "/?panel=favourites"),
    ("accounts:observations", "/account/observations/", "/?panel=reports"),
    ("accounts:routes", "/account/routes/", "/?panel=routes"),
]


@pytest.mark.django_db
class TestAccountListRedirects:
    """Each list page 301s to the map with its sheet named."""

    @pytest.mark.parametrize(("url_name", "path", "target"), CASES)
    def test_anonymous_is_redirected(
        self, client: Client, url_name: str, path: str, target: str
    ) -> None:
        """No sign-in wall first: the map itself offers the way in."""
        assert reverse(url_name) == path
        response = client.get(path)
        assert response.status_code == 301
        assert response["Location"] == target

    @pytest.mark.parametrize(("url_name", "path", "target"), CASES)
    def test_signed_in_is_redirected_to_the_same_place(
        self, client: Client, url_name: str, path: str, target: str
    ) -> None:
        """A signed-in user gets the same 301 — the sheet does the listing."""
        client.force_login(UserFactory.create())
        response = client.get(path)
        assert response.status_code == 301
        assert response["Location"] == target

    @pytest.mark.parametrize(("url_name", "path", "target"), CASES)
    def test_following_the_redirect_lands_on_the_map(
        self, client: Client, url_name: str, path: str, target: str
    ) -> None:
        """The target is the map page, carrying the sheet parameter."""
        client.force_login(UserFactory.create())
        response = client.get(path, follow=True)
        assert response.redirect_chain == [(target, 301)]
        assert response.status_code == 200
        assert 'id="map"' in response.content.decode()
