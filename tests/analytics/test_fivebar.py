"""
tests/analytics/test_fivebar.py — the fivebar server-events client (SNOW-1180).

Covers ``apps/analytics/fivebar.py``: which events are sent (the goal
allowlist), the no-op when unconfigured, the payload shape, the reduction of
properties to fivebar's rules, the background POST's error handling,
``page_of``'s route-pattern naming, and ``analytics.track()``'s hand-off.
No test reaches the network: the executor and ``requests.post`` are patched.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import RequestFactory, override_settings
from django.urls import resolve

from apps import analytics
from apps.analytics import fivebar
from apps.analytics.exceptions import AnalyticsPIIError

_CONFIGURED = override_settings(
    FIVEBAR_TOKEN="fb-token", FIVEBAR_DOMAIN="snowdesk.info"
)


class _InlineExecutor:
    """Stand-in for the thread pool that runs each submitted call at once."""

    def submit(self, fn: Any, *args: Any) -> None:
        """Call ``fn`` synchronously."""
        fn(*args)


@pytest.fixture
def posted() -> Any:
    """Patch the background pool inline and capture each ``_post`` call."""
    with patch.object(fivebar, "_executor", _InlineExecutor()):
        with patch.object(fivebar, "_post") as mock_post:
            yield mock_post


class TestSend:
    """``fivebar.send`` sends goal events only, and only when configured."""

    @_CONFIGURED
    def test_goal_event_is_posted_with_the_full_payload(
        self, posted: MagicMock
    ) -> None:
        """A goal event goes out with site, page, event, id and props."""
        fivebar.send("region_added", "acct-1", {"region_id": "CH-4115"}, "/x/")
        posted.assert_called_once_with(
            "fb-token",
            {
                "site": "snowdesk.info",
                "name": "/x/",
                "event": "region_added",
                "id": "acct-1",
                "props": {"region_id": "CH-4115"},
            },
        )

    @_CONFIGURED
    def test_non_goal_event_is_not_sent(self, posted: MagicMock) -> None:
        """PWA lifecycle events and server signals never reach fivebar."""
        fivebar.send("pwa.sw.installed", "acct-1", {})
        fivebar.send("pwa.version.endpoint.hit", "_server", {})
        posted.assert_not_called()

    @pytest.mark.parametrize(
        "overrides",
        [
            {"FIVEBAR_TOKEN": "", "FIVEBAR_DOMAIN": "snowdesk.info"},
            {"FIVEBAR_TOKEN": "fb-token", "FIVEBAR_DOMAIN": ""},
            {"FIVEBAR_TOKEN": "  ", "FIVEBAR_DOMAIN": "snowdesk.info"},
        ],
    )
    def test_no_op_unless_token_and_domain_are_set(
        self, posted: MagicMock, overrides: dict[str, str]
    ) -> None:
        """Either setting empty (or blank) means nothing is sent."""
        with override_settings(**overrides):
            fivebar.send("region_added", "acct-1", {})
        posted.assert_not_called()

    def test_record_write_failure_is_not_a_goal(self) -> None:
        """The one failure-shaped ``map.*`` event stays off the goal list."""
        assert "map.basemap.record_write_failed" not in fivebar.GOAL_EVENTS


class TestProps:
    """``_props`` keeps only what fivebar accepts."""

    def test_drops_invalid_names_none_and_non_scalars(self) -> None:
        """Capitals, dots, None, lists and dicts are all dropped."""
        props = fivebar._props(
            {
                "ok": "yes",
                "Capital": "x",
                "has.dot": "x",
                "none": None,
                "a_list": [1],
                "a_dict": {"k": 1},
                "flag": True,
                "count": 3,
                "ratio": 0.5,
            }
        )
        assert props == {"ok": "yes", "flag": True, "count": 3, "ratio": 0.5}

    def test_skips_the_client_timestamp(self) -> None:
        """The receiver's clock-skew cross-check is never a fivebar property."""
        assert fivebar._props({"client_timestamp": "2026-10-22T10:00:00Z"}) == {}

    def test_truncates_long_text(self) -> None:
        """Text over 255 characters is cut to 255."""
        assert fivebar._props({"long": "x" * 300}) == {"long": "x" * 255}

    def test_keeps_at_most_eight_in_order(self) -> None:
        """Only the first eight valid names survive."""
        props = fivebar._props({f"p{i}": i for i in range(12)})
        assert list(props) == [f"p{i}" for i in range(8)]


class TestPost:
    """``_post`` logs every failure and raises none."""

    _PAYLOAD: dict[str, object] = {"event": "region_added"}

    def test_accepted_event_logs_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A 202 is the success answer."""
        with patch("requests.post", return_value=MagicMock(status_code=202)) as post:
            fivebar._post("fb-token", self._PAYLOAD)
        assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer fb-token"}
        assert post.call_args.kwargs["json"] == self._PAYLOAD
        assert post.call_args.args == (fivebar.FIVEBAR_EVENTS_URL,)
        assert "fivebar" not in caplog.text

    def test_rejection_is_logged_with_status(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Anything but 202 is a WARNING naming the event and status."""
        response = MagicMock(status_code=401, text='{"error": "invalid token"}')
        with patch("requests.post", return_value=response):
            fivebar._post("fb-token", self._PAYLOAD)
        assert "fivebar rejected event=region_added status=401" in caplog.text

    def test_network_error_is_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A connection failure never propagates out of the worker."""
        with patch("requests.post", side_effect=requests.ConnectionError):
            fivebar._post("fb-token", self._PAYLOAD)
        assert "fivebar send failed: event=region_added" in caplog.text


class TestPageOf:
    """``page_of`` names the route pattern, never the requested path."""

    def test_resolved_request_names_its_pattern_not_its_path(self) -> None:
        """A token in the URL never reaches the page name."""
        request = RequestFactory().get("/account/verify/secret-token/")
        request.resolver_match = resolve("/account/verify/secret-token/")
        page = fivebar.page_of(request)
        assert "secret-token" not in page
        assert page.startswith("/account/verify/")

    def test_unresolved_request_is_root(self) -> None:
        """No resolver match falls back to ``/``."""
        assert fivebar.page_of(RequestFactory().get("/")) == "/"


class TestTrackHandOff:
    """``analytics.track()`` hands every event to fivebar with its page."""

    def test_track_passes_event_props_and_page(self) -> None:
        """The page keyword reaches ``fivebar.send``."""
        with patch("apps.analytics.fivebar.send") as send:
            analytics.track("region_added", "acct-1", {"region_id": "X"}, page="/p/")
        send.assert_called_once_with(
            "region_added", "acct-1", {"region_id": "X"}, "/p/"
        )

    def test_pii_is_rejected_before_fivebar_sees_it(self) -> None:
        """The PII guard runs first, so fivebar never receives a banned key."""
        with patch("apps.analytics.fivebar.send") as send:
            with pytest.raises(AnalyticsPIIError):
                analytics.track("region_added", "acct-1", {"email": "a@b.c"})
        send.assert_not_called()
