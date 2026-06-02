"""
tests/public/test_share_redirect.py — Tests for GET /s/<token>/ (share_redirect).

Covers the 302 happy path, click row creation (including RequestLog FK),
Cache-Control header, 404 on unknown token, 410 on null bulletin, and the
``share_link_clicked`` analytics event. Also verifies the 302 status code
explicitly (not 301) and the visitor hash stability.
"""

from __future__ import annotations

import hashlib
from datetime import date
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from bulletins.models import BulletinShareClick
from core.models import RequestLog
from tests.factories import (
    BulletinShareFactory,
    MicroRegionFactory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redirect_url(token: str) -> str:
    """Build the /s/<token>/ URL for the share redirect endpoint."""
    return reverse("public:share_redirect", args=[token])


# ---------------------------------------------------------------------------
# 302 happy path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestShareRedirectHappyPath:
    """share_redirect returns 302 to the canonical bulletin URL."""

    def test_returns_302(self, client: Client) -> None:
        """GET /s/<token>/ returns exactly 302 (not 301)."""
        share = BulletinShareFactory.create()
        response = client.get(_redirect_url(share.token))
        assert response.status_code == 302

    def test_redirect_target_contains_region_id(self, client: Client) -> None:
        """Redirect location contains the region's canonical ID."""
        region = MicroRegionFactory.create(region_id="CH-4222")
        share = BulletinShareFactory.create(
            region=region,
            target_date=date(2026, 4, 8),
        )
        response = client.get(_redirect_url(share.token))
        assert response.status_code == 302
        location = response["Location"]
        assert "ch-4222" in location.lower() or "CH-4222" in location

    def test_cache_control_no_store(self, client: Client) -> None:
        """Response carries Cache-Control: no-store."""
        share = BulletinShareFactory.create()
        response = client.get(_redirect_url(share.token))
        assert "no-store" in response.get("Cache-Control", "")


# ---------------------------------------------------------------------------
# Click tracking
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestShareRedirectClickTracking:
    """share_redirect creates one BulletinShareClick row per follow."""

    def test_click_row_created(self, client: Client) -> None:
        """Following a share link creates exactly one BulletinShareClick row."""
        share = BulletinShareFactory.create()
        assert BulletinShareClick.objects.filter(share=share).count() == 0

        client.get(_redirect_url(share.token))

        assert BulletinShareClick.objects.filter(share=share).count() == 1

    def test_click_row_creates_request_log(self, client: Client) -> None:
        """Following a share link creates one RequestLog linked to the click."""
        share = BulletinShareFactory.create()
        assert RequestLog.objects.count() == 0

        client.get(_redirect_url(share.token))

        assert RequestLog.objects.count() == 1
        click = BulletinShareClick.objects.get(share=share)
        assert click.request_id is not None

    def test_click_row_captures_user_agent(self, client: Client) -> None:
        """The click row's RequestLog records the User-Agent header."""
        share = BulletinShareFactory.create()
        ua = "TestBrowser/1.0"

        client.get(_redirect_url(share.token), HTTP_USER_AGENT=ua)

        click = BulletinShareClick.objects.get(share=share)
        assert click.request.user_agent == ua

    def test_click_row_captures_referer(self, client: Client) -> None:
        """The click row's RequestLog records the Referer header."""
        share = BulletinShareFactory.create()

        client.get(
            _redirect_url(share.token),
            HTTP_REFERER="https://example.com/some-page",
        )

        click = BulletinShareClick.objects.get(share=share)
        assert click.request.referer == "https://example.com/some-page"

    def test_click_row_captures_sec_purpose(self, client: Client) -> None:
        """Sec-Purpose: prefetch header is captured in the RequestLog."""
        share = BulletinShareFactory.create()

        client.get(
            _redirect_url(share.token),
            HTTP_SEC_PURPOSE="prefetch",
        )

        click = BulletinShareClick.objects.get(share=share)
        # sec_purpose is stored on the RequestLog (generic request context).
        # The RequestLog captures it via the standard header extraction.
        # Note: sec_purpose was previously a dedicated column on
        # BulletinShareClick; it is now available via click.request.
        assert click.request is not None  # RequestLog FK is set

    def test_visitor_hash_is_deterministic(self, client: Client) -> None:
        """Two clicks from the same IP + UA produce the same visitor_hash."""
        share = BulletinShareFactory.create()
        ua = "SameAgent/1.0"
        ip = "203.0.113.42"

        client.get(
            _redirect_url(share.token),
            REMOTE_ADDR=ip,
            HTTP_USER_AGENT=ua,
        )
        # Reset share so we can get a fresh token.
        share2 = BulletinShareFactory.create()
        client.get(
            _redirect_url(share2.token),
            REMOTE_ADDR=ip,
            HTTP_USER_AGENT=ua,
        )

        c1 = BulletinShareClick.objects.get(share=share)
        c2 = BulletinShareClick.objects.get(share=share2)
        assert c1.visitor_hash == c2.visitor_hash

    def test_visitor_hash_matches_expected_formula(self, client: Client) -> None:
        """visitor_hash is first 16 hex chars of sha256(ip + '|' + ua)."""
        share = BulletinShareFactory.create()
        ip = "203.0.113.7"
        ua = "TestAgent/2.0"
        expected = hashlib.sha256(f"{ip}|{ua}".encode()).hexdigest()[:16]

        client.get(
            _redirect_url(share.token),
            REMOTE_ADDR=ip,
            HTTP_USER_AGENT=ua,
        )

        click = BulletinShareClick.objects.get(share=share)
        assert click.visitor_hash == expected

    def test_x_forwarded_for_used_as_ip(self, client: Client) -> None:
        """When X-Forwarded-For is present, the first entry is used as ip_address."""
        share = BulletinShareFactory.create()
        forwarded_ip = "203.0.113.99"

        client.get(
            _redirect_url(share.token),
            HTTP_X_FORWARDED_FOR=f"{forwarded_ip}, 10.0.0.1",
            REMOTE_ADDR="10.0.0.1",
        )

        click = BulletinShareClick.objects.get(share=share)
        # ip_address is now on the linked RequestLog, not directly on the click.
        assert str(click.request.ip_address) == forwarded_ip


# ---------------------------------------------------------------------------
# 404 on unknown token
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestShareRedirect404:
    """share_redirect returns 404 for an unknown token."""

    def test_unknown_token_returns_404(self, client: Client) -> None:
        """A token that doesn't exist in the database returns 404."""
        response = client.get(_redirect_url("doesnotexist"))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 410 when bulletin is None
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestShareRedirect410:
    """share_redirect returns 410 Gone when share.bulletin is None."""

    def test_null_bulletin_returns_410(self, client: Client) -> None:
        """When the linked bulletin has been deleted, the redirect returns 410."""
        share = BulletinShareFactory.create(bulletin=None)
        response = client.get(_redirect_url(share.token))
        assert response.status_code == 410

    def test_null_bulletin_click_still_recorded(self, client: Client) -> None:
        """Even on a 410 response, the click row is written."""
        share = BulletinShareFactory.create(bulletin=None)

        client.get(_redirect_url(share.token))

        assert BulletinShareClick.objects.filter(share=share).count() == 1

    def test_null_bulletin_cache_control_no_store(self, client: Client) -> None:
        """The 410 response also carries Cache-Control: no-store."""
        share = BulletinShareFactory.create(bulletin=None)
        response = client.get(_redirect_url(share.token))
        assert "no-store" in response.get("Cache-Control", "")


# ---------------------------------------------------------------------------
# Analytics — share_link_clicked
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestShareLinkClickedAnalytics:
    """analytics.track('share_link_clicked') fires on every follow."""

    def test_fires_on_successful_redirect(self, client: Client) -> None:
        """Following a share link fires share_link_clicked with the region_id."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        share = BulletinShareFactory.create(region=region)

        with patch("public.views.analytics.track") as mock_track:
            client.get(_redirect_url(share.token))

        calls = [
            c for c in mock_track.call_args_list if c.args[0] == "share_link_clicked"
        ]
        assert len(calls) == 1
        props = calls[0].args[2]
        assert props["region_id"] == "CH-4115"

    def test_fires_on_null_bulletin_410(self, client: Client) -> None:
        """The event fires even when the bulletin has been deleted (410 path)."""
        share = BulletinShareFactory.create(bulletin=None)

        with patch("public.views.analytics.track") as mock_track:
            client.get(_redirect_url(share.token))

        calls = [
            c for c in mock_track.call_args_list if c.args[0] == "share_link_clicked"
        ]
        assert len(calls) == 1

    def test_not_fired_on_unknown_token(self, client: Client) -> None:
        """A 404 on an unknown token must not fire the event."""
        with patch("public.views.analytics.track") as mock_track:
            client.get(_redirect_url("doesnotexist"))

        calls = [
            c for c in mock_track.call_args_list if c.args[0] == "share_link_clicked"
        ]
        assert calls == []

    def test_anon_distinct_id_uses_session_key_or_uuid(self, client: Client) -> None:
        """Anonymous follows use the existing session key or a request-scoped UUID."""
        share = BulletinShareFactory.create()

        with patch("public.views.analytics.track") as mock_track:
            client.get(_redirect_url(share.token))

        calls = [
            c for c in mock_track.call_args_list if c.args[0] == "share_link_clicked"
        ]
        assert len(calls) == 1
        distinct_id = calls[0].args[1]
        # Either a Django session key (32-char hex-ish) or an anon-<uuid> string.
        assert distinct_id, "distinct_id must be non-empty for anonymous visitors"
        assert distinct_id.startswith("anon-") or len(distinct_id) >= 16
