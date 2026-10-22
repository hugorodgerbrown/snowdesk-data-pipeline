"""
tests/core/test_sentry.py — Sentry wiring (SNOW-1181).

Covers ``apps/core/sentry.py``: ``init_sentry`` is a no-op without a DSN
and passes the privacy options when it has one; ``_before_send`` strips the
token-bearing parts of a request; ``capture_js_error`` builds one event per
browser error with the right grouping for opted-in and opted-out reports.
And the receiver hands ``js.error`` envelopes to it whatever the analytics
configuration. Nothing reaches Sentry: ``sentry_sdk`` calls are patched.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from django.test import Client, override_settings
from sentry_sdk.integrations.logging import LoggingIntegration

from apps.core import sentry

if TYPE_CHECKING:
    from sentry_sdk.types import Event


class TestInitSentry:
    """``init_sentry`` initialises only with a DSN, and privately."""

    @pytest.mark.parametrize("dsn", ["", "   "])
    def test_empty_dsn_leaves_the_sdk_off(self, dsn: str) -> None:
        """No DSN, no init."""
        with patch("sentry_sdk.init") as init:
            assert sentry.init_sentry(dsn, "production", "abc") is False
        init.assert_not_called()

    def test_dsn_initialises_with_the_privacy_options(self) -> None:
        """PII, locals and request bodies are all off; traces are off."""
        with patch("sentry_sdk.init") as init:
            assert sentry.init_sentry(
                " https://k@o1.ingest.de.sentry.io/2 ", "staging", "abc"
            )
        kwargs = init.call_args.kwargs
        assert kwargs["dsn"] == "https://k@o1.ingest.de.sentry.io/2"
        assert kwargs["environment"] == "staging"
        assert kwargs["release"] == "abc"
        assert kwargs["send_default_pii"] is False
        assert kwargs["include_local_variables"] is False
        assert kwargs["max_request_body_size"] == "never"
        assert kwargs["traces_sample_rate"] == 0.0
        assert kwargs["before_send"] is sentry._before_send
        logging_integration = next(
            i for i in kwargs["integrations"] if isinstance(i, LoggingIntegration)
        )
        assert logging_integration._breadcrumb_handler is None


class TestBeforeSend:
    """``_before_send`` drops every place a URL token could ride."""

    def test_strips_url_query_string_and_referer(self) -> None:
        """URL, query string and Referer go; other headers stay."""
        event: Event = {
            "request": {
                "url": "https://snowdesk.info/account/verify/secret/",
                "query_string": "route_share=secret",
                "headers": {
                    "Referer": "https://snowdesk.info/?trip_share=x",
                    "Accept": "*/*",
                },
                "method": "GET",
            }
        }
        cleaned = sentry._before_send(event, {})
        assert cleaned["request"] == {"headers": {"Accept": "*/*"}, "method": "GET"}

    def test_event_without_a_request_passes_through(self) -> None:
        """A management-command error has no request to clean."""
        event: Event = {"message": "boom"}
        assert sentry._before_send(event, {}) == {"message": "boom"}


class TestCaptureJsError:
    """``capture_js_error`` groups opted-in and opted-out reports sensibly."""

    def test_opted_in_report_groups_on_message_and_file(self) -> None:
        """The full report keeps its diagnostics as extras."""
        props = {
            "kind": "error",
            "pathname": "/map/",
            "message": "TypeError: x is undefined",
            "filename": "/static/js/map.js",
            "lineno": 10,
            "colno": 4,
            "stack": "at f (/static/js/map.js:10:4)",
        }
        with patch("sentry_sdk.capture_event") as capture:
            sentry.capture_js_error(props, client_version="v1", platform="ios")
        event = capture.call_args.args[0]
        assert event["level"] == "error"
        assert event["message"] == "TypeError: x is undefined"
        assert event["fingerprint"] == [
            "js.error",
            "TypeError: x is undefined",
            "/static/js/map.js",
        ]
        assert event["tags"]["pathname"] == "/map/"
        assert event["tags"]["client_version"] == "v1"
        assert event["tags"]["platform"] == "ios"
        assert event["extra"] == {
            "filename": "/static/js/map.js",
            "lineno": 10,
            "colno": 4,
            "stack": "at f (/static/js/map.js:10:4)",
        }

    def test_opted_out_report_groups_on_page_and_kind(self) -> None:
        """Only ``kind`` and ``pathname``: still one issue per broken page."""
        with patch("sentry_sdk.capture_event") as capture:
            sentry.capture_js_error(
                {"kind": "unhandledrejection", "pathname": "/map/"},
                client_version="v1",
                platform=None,
            )
        event = capture.call_args.args[0]
        assert event["fingerprint"] == ["js.error", "/map/", "unhandledrejection"]
        assert event["message"] == "JavaScript unhandledrejection on /map/"
        assert event["tags"]["platform"] == ""
        assert event["extra"] == {}


class TestTokenPathsNeverLeave:
    """A token in the page path is replaced by the route pattern (Codex P1)."""

    def test_opted_out_report_on_a_token_route(self) -> None:
        """The pathname tag, message and fingerprint carry the pattern."""
        with patch("sentry_sdk.capture_event") as capture:
            sentry.capture_js_error(
                {"kind": "error", "pathname": "/account/access/s3cret/"},
                client_version="v1",
                platform=None,
            )
        event = capture.call_args.args[0]
        assert "s3cret" not in repr(event)
        assert event["tags"]["pathname"] == "/account/access/<str:token>/"

    def test_inline_script_frames_on_a_token_route(self) -> None:
        """An inline script's filename and stack are the document URL."""
        props = {
            "kind": "error",
            "pathname": "/account/access/s3cret/",
            "message": "ReferenceError: y",
            "filename": "https://snowdesk.info/account/access/s3cret/",
            "lineno": 1,
            "stack": "at https://snowdesk.info/account/access/s3cret/:1:1",
        }
        with patch("sentry_sdk.capture_event") as capture:
            sentry.capture_js_error(props, client_version="v1", platform="web")
        event = capture.call_args.args[0]
        assert "s3cret" not in repr(event)
        assert event["extra"]["filename"] == (
            "https://snowdesk.info/account/access/<str:token>/"
        )

    def test_unresolvable_path_is_replaced(self) -> None:
        """A path the URLconf does not know could carry anything."""
        with patch("sentry_sdk.capture_event") as capture:
            sentry.capture_js_error(
                {"kind": "error", "pathname": "/nope/abc/"},
                client_version="v1",
                platform=None,
            )
        assert capture.call_args.args[0]["tags"]["pathname"] == "?"


def _js_error_envelope(**properties: object) -> dict[str, object]:
    """Return a minimal ``js.error`` envelope."""
    return {
        "event": "js.error",
        "timestamp": "2026-10-22T10:00:00+00:00",
        "client_version": "v1",
        "platform": "web",
        "properties": properties,
    }


@pytest.mark.django_db
class TestReceiverForwardsJsErrors:
    """``/api/telemetry`` hands ``js.error`` to Sentry, and nothing else."""

    @override_settings(POSTHOG_API_KEY="")
    def test_js_error_is_forwarded_without_an_analytics_key(self) -> None:
        """Error reporting does not depend on PostHog being configured."""
        with patch("apps.core.sentry.capture_js_error") as capture:
            response = Client().post(
                "/api/telemetry",
                data=json.dumps(_js_error_envelope(kind="error", pathname="/map/")),
                content_type="application/json",
            )
        assert response.status_code == 204
        capture.assert_called_once_with(
            {"kind": "error", "pathname": "/map/"},
            client_version="v1",
            platform="web",
        )

    def test_other_events_are_not_forwarded(self) -> None:
        """A PWA lifecycle event is not an error."""
        envelope = _js_error_envelope()
        envelope["event"] = "pwa.sw.installed"
        with patch("apps.core.sentry.capture_js_error") as capture:
            Client().post(
                "/api/telemetry",
                data=json.dumps(envelope),
                content_type="application/json",
            )
        capture.assert_not_called()


def test_sentry_is_off_in_the_test_suite() -> None:
    """The settings-time init did not start a sending client for tests."""
    import sentry_sdk

    client = sentry_sdk.get_client()
    assert not client.dsn
    assert getattr(client, "transport", None) is None
