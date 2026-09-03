"""
tests/public/test_observations_redirect.py — SNOW-804: ``/observations/`` is a
permanent redirect to the map with the reports sheet open.

Covers:
  - 301 to ``/?panel=reports`` in every auth state — the page used to show
    an anonymous visitor a sign-in wall; the map offers the way in itself.
  - The query string is forwarded, the ``/map/`` and ``/terms/`` precedent.
  - The help page no longer carries the "The Observations page" topic; the
    "Field observations" topic, about submitting and the overlay, stays.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from tests.factories import UserFactory


@pytest.mark.django_db
class TestObservationsRedirect:
    """The old page URL lands on the map's reports sheet."""

    def test_anonymous_gets_301_to_the_reports_sheet(self, client: Client) -> None:
        """No sign-in wall: the redirect is the same for everyone."""
        response = client.get(reverse("public:observations"))
        assert response.status_code == 301
        assert response["Location"] == "/?panel=reports"

    def test_signed_in_gets_the_same_redirect(self, client: Client) -> None:
        """A signed-in user is sent to the same place."""
        client.force_login(UserFactory.create())
        response = client.get("/observations/")
        assert response.status_code == 301
        assert response["Location"] == "/?panel=reports"

    def test_query_string_is_forwarded(self, client: Client) -> None:
        """``?d=`` survives the hop, so a dated link still opens on its day."""
        response = client.get("/observations/?d=2026-02-16")
        assert response.status_code == 301
        assert response["Location"] == "/?panel=reports&d=2026-02-16"

    def test_following_the_redirect_lands_on_the_map(self, client: Client) -> None:
        """The target renders the map page."""
        response = client.get("/observations/", follow=True)
        assert response.redirect_chain == [("/?panel=reports", 301)]
        assert response.status_code == 200
        assert 'id="map"' in response.content.decode()


@pytest.mark.django_db
class TestHelpPageTopic:
    """The help page describes what exists."""

    def test_the_observations_page_topic_is_gone(self, client: Client) -> None:
        """No panel for a page that no longer exists; the submit topic stays."""
        content = client.get(reverse("public:help")).content.decode()
        assert 'data-testid="help-topic-recent-observations"' not in content
        assert 'data-testid="help-topic-observations"' in content
