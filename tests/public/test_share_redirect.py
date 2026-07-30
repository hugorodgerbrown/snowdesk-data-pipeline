"""
tests/public/test_share_redirect.py — Tests for GET /s/<token>/ (share_redirect).

Covers the 302 happy path, click row creation (including RequestLog FK),
Cache-Control header, 404 on unknown token, 410 on null bulletin, and the
``share_link_clicked`` analytics event. Also verifies the 302 status code
explicitly (not 301) and the visitor hash stability.

SNOW-551 adds the abuse bounds: only GET/HEAD are accepted, speculative
requests (HEAD, ``Sec-Purpose: prefetch`` / ``prerender``) redirect
without recording anything, and follows are capped per (token, IP).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import date
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import SettingsWrapper

from apps.bulletins.models import BulletinShareClick
from apps.core.models import RequestLog
from apps.public.views import SHARE_CLICK_RATE
from tests.factories import (
    BulletinShareFactory,
    MicroRegionFactory,
)

# How many follows of one link from one IP are allowed before the 429.
_SHARE_RATE_LIMIT = int(SHARE_CLICK_RATE.split("/")[0])

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

    def test_click_row_captures_sec_purpose_on_a_real_visit(
        self, client: Client
    ) -> None:
        """A non-speculative Sec-Purpose is still captured on the RequestLog.

        SNOW-551 skips the writes for ``prefetch`` / ``prerender``; any
        other value is a real navigation and still logs, header and all.
        """
        share = BulletinShareFactory.create()

        client.get(_redirect_url(share.token), HTTP_SEC_PURPOSE="navigate")

        click = BulletinShareClick.objects.get(share=share)
        assert click.request.sec_purpose == "navigate"

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

        with patch("apps.public.views.analytics.track") as mock_track:
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

        with patch("apps.public.views.analytics.track") as mock_track:
            client.get(_redirect_url(share.token))

        calls = [
            c for c in mock_track.call_args_list if c.args[0] == "share_link_clicked"
        ]
        assert len(calls) == 1

    def test_not_fired_on_unknown_token(self, client: Client) -> None:
        """A 404 on an unknown token must not fire the event."""
        with patch("apps.public.views.analytics.track") as mock_track:
            client.get(_redirect_url("doesnotexist"))

        calls = [
            c for c in mock_track.call_args_list if c.args[0] == "share_link_clicked"
        ]
        assert calls == []

    def test_anon_distinct_id_uses_session_key_or_uuid(self, client: Client) -> None:
        """Anonymous follows use the existing session key or a request-scoped UUID."""
        share = BulletinShareFactory.create()

        with patch("apps.public.views.analytics.track") as mock_track:
            client.get(_redirect_url(share.token))

        calls = [
            c for c in mock_track.call_args_list if c.args[0] == "share_link_clicked"
        ]
        assert len(calls) == 1
        distinct_id = calls[0].args[1]
        # Either a Django session key (32-char hex-ish) or an anon-<uuid> string.
        assert distinct_id, "distinct_id must be non-empty for anonymous visitors"
        assert distinct_id.startswith("anon-") or len(distinct_id) >= 16


# ---------------------------------------------------------------------------
# Abuse bounds (SNOW-551) — method allowlist, speculative-request skip,
# per-(token, IP) rate limit.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestShareRedirectAbuseBounds:
    """The endpoint writes three rows on arrival; those writes are bounded."""

    @pytest.fixture(autouse=True)
    def _live_rate_limit(self, settings: SettingsWrapper) -> Iterator[None]:
        """Switch the rate limiter on and isolate its counters per test.

        ``config.settings.development`` sets ``RATELIMIT_ENABLE = False``,
        so the decorator is inert under the test settings. Turning it on —
        rather than taking the ``request.limited = True`` shortcut used
        elsewhere in the suite — is what makes these tests prove the
        decorator is wired with the right *key function* and *rate*, which
        is the substance of SNOW-551's rate-limit decision; the view's
        handling of ``request.limited`` is trivial by comparison.

        django_ratelimit keeps its counters in the default cache, which is
        process-wide and outlives a single test, so the tests that
        deliberately exhaust a budget would otherwise leak into the ones
        that expect a fresh one.
        """
        settings.RATELIMIT_ENABLE = True
        cache.clear()
        yield
        cache.clear()

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_non_get_methods_are_rejected(self, client: Client, method: str) -> None:
        """A state-changing method gets 405 rather than silently writing."""
        share = BulletinShareFactory.create()

        response = getattr(client, method)(_redirect_url(share.token))

        assert response.status_code == 405
        assert BulletinShareClick.objects.count() == 0
        assert RequestLog.objects.count() == 0

    @pytest.mark.parametrize("sec_purpose", ["prefetch", "prerender"])
    def test_prefetch_redirects_without_recording_a_click(
        self, client: Client, sec_purpose: str
    ) -> None:
        """A speculative request still redirects but records nothing.

        Previously proven to be stored *as a click*, inflating the metric
        with links nobody chose to follow.
        """
        share = BulletinShareFactory.create()

        with patch("apps.public.views.analytics.track") as mock_track:
            response = client.get(
                _redirect_url(share.token),
                HTTP_SEC_PURPOSE=sec_purpose,
            )

        assert response.status_code == 302
        assert BulletinShareClick.objects.count() == 0
        assert RequestLog.objects.count() == 0
        assert mock_track.call_args_list == []

    def test_structured_sec_purpose_value_is_recognised(self, client: Client) -> None:
        """``prefetch;anonymous-client-ip`` is a prefetch too."""
        share = BulletinShareFactory.create()

        client.get(
            _redirect_url(share.token),
            HTTP_SEC_PURPOSE="prefetch;anonymous-client-ip",
        )

        assert BulletinShareClick.objects.count() == 0

    def test_head_redirects_without_recording_a_click(self, client: Client) -> None:
        """HEAD is answered but never counted."""
        share = BulletinShareFactory.create()

        response = client.head(_redirect_url(share.token))

        assert response.status_code == 302
        assert BulletinShareClick.objects.count() == 0
        assert RequestLog.objects.count() == 0

    def test_prefetch_on_a_dead_share_still_returns_410(self, client: Client) -> None:
        """The skip covers the 410 branch as well as the redirect."""
        share = BulletinShareFactory.create(bulletin=None)

        response = client.get(_redirect_url(share.token), HTTP_SEC_PURPOSE="prefetch")

        assert response.status_code == 410
        assert BulletinShareClick.objects.count() == 0

    def test_ordinary_get_still_records_a_click(self, client: Client) -> None:
        """Positive control — a plain GET is unaffected by any of the above."""
        share = BulletinShareFactory.create()

        with patch("apps.public.views.analytics.track") as mock_track:
            response = client.get(_redirect_url(share.token))

        assert response.status_code == 302
        assert BulletinShareClick.objects.count() == 1
        assert [
            c for c in mock_track.call_args_list if c.args[0] == "share_link_clicked"
        ]

    def test_rate_limit_bounds_one_token_from_one_ip(self, client: Client) -> None:
        """The 31st follow of one link from one IP within the hour is refused."""
        share = BulletinShareFactory.create()
        url = _redirect_url(share.token)

        for _ in range(_SHARE_RATE_LIMIT):
            assert client.get(url, REMOTE_ADDR="203.0.113.1").status_code == 302

        blocked = client.get(url, REMOTE_ADDR="203.0.113.1")

        assert blocked.status_code == 429
        assert BulletinShareClick.objects.count() == _SHARE_RATE_LIMIT

    def test_rate_limit_is_per_token_not_per_ip(self, client: Client) -> None:
        """Exhausting one link's budget must not block a different link.

        A flat per-IP cap would make one NATed office network share a
        single budget across unrelated share links.
        """
        exhausted = BulletinShareFactory.create()
        other = BulletinShareFactory.create()
        ip = "203.0.113.2"

        for _ in range(_SHARE_RATE_LIMIT):
            client.get(_redirect_url(exhausted.token), REMOTE_ADDR=ip)
        assert (
            client.get(_redirect_url(exhausted.token), REMOTE_ADDR=ip).status_code
            == 429
        )

        assert client.get(_redirect_url(other.token), REMOTE_ADDR=ip).status_code == 302

    def test_rate_limit_is_per_ip_not_global(self, client: Client) -> None:
        """One abusive IP must not lock everyone else out of the same link."""
        share = BulletinShareFactory.create()
        url = _redirect_url(share.token)

        for _ in range(_SHARE_RATE_LIMIT):
            client.get(url, REMOTE_ADDR="203.0.113.3")
        assert client.get(url, REMOTE_ADDR="203.0.113.3").status_code == 429

        assert client.get(url, REMOTE_ADDR="198.51.100.7").status_code == 302
