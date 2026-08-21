"""
tests/public/test_privacy.py — Tests for the /privacy page (SNOW-153, SNOW-387).

Covers:

  * ``GET /privacy/`` returns HTTP 200 for an anonymous user.
  * The page contains its section ``data-testid`` markers.
  * SNOW-387: sections 5 and 8 no longer claim "no analytics platforms" /
    "no analytics ... cookies of any kind" — those claims were true before
    the SNOW-381/385 telemetry pipeline shipped but are misleading now.
    The page instead describes the anonymous ``pwa.*`` event stream,
    names PostHog as the downstream processor, and links to the
    manage-page toggle.

No factories or database fixtures are required — the page is entirely
static and carries no model queries.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

# `client` is pytest-django's built-in fixture (an anonymous ``Client()``);
# no local override needed.


@pytest.mark.django_db
class TestPrivacyPage:
    """The /privacy page satisfies the SNOW-153 acceptance criteria."""

    def test_returns_200_for_anonymous_user(self, client: Client) -> None:
        response = client.get(reverse("public:privacy"))
        assert response.status_code == 200

    def test_has_heading(self, client: Client) -> None:
        response = client.get(reverse("public:privacy"))
        assert b'data-testid="privacy-heading"' in response.content

    def test_url_reverses_correctly(self) -> None:
        assert reverse("public:privacy") == "/privacy/"


@pytest.mark.django_db
class TestPrivacyTelemetryCopy:
    """SNOW-387: the privacy page accurately describes first-party telemetry."""

    def test_sharing_section_no_longer_claims_no_analytics_platforms(
        self, client: Client
    ) -> None:
        """Section 5 no longer claims Snowdesk uses no analytics platforms."""
        response = client.get(reverse("public:privacy"))
        assert b"analytics platforms" not in response.content

    def test_cookies_section_no_longer_claims_no_analytics_cookies_of_any_kind(
        self, client: Client
    ) -> None:
        """Section 8 no longer claims no tracking/analytics cookies "of any kind"."""
        response = client.get(reverse("public:privacy"))
        assert b"cookies of any kind" not in response.content

    def test_sharing_section_names_posthog(self, client: Client) -> None:
        """Section 5 names PostHog as the anonymous-event processor."""
        response = client.get(reverse("public:privacy"))
        assert b"PostHog" in response.content

    def test_sharing_section_mentions_telemetry_events(self, client: Client) -> None:
        """Section 5 describes the anonymous pwa.* event stream."""
        response = client.get(reverse("public:privacy"))
        assert b"anonymous" in response.content.lower()
        assert b"pwa." in response.content

    def test_sharing_section_links_to_manage_page_toggle(self, client: Client) -> None:
        """Section 5 links to the manage-page toggle so users can opt out."""
        response = client.get(reverse("public:privacy"))
        manage_url = reverse("accounts:hub")
        assert manage_url.encode() in response.content
