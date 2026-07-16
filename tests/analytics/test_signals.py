"""tests/analytics/test_signals.py — server-side PWA signal helper.

Covers ``analytics.signals.emit_server_signal`` (SNOW-381, spec §16.2)
and the five call sites where it's wired into existing code paths:

* ``public.api.version``                 — pwa.version.endpoint.hit
* ``public.api.sw_config``               — pwa.sw_config.hit
* ``subscriptions.push_service``         — pwa.push.sent / pwa.push.gone_410
* ``core.idempotency.IdempotencyMiddleware`` — pwa.idempotency.replay

Each test patches ``posthog.capture`` so the real network client is
never hit.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import Client, override_settings

from analytics.signals import SERVER_DISTINCT_ID, emit_server_signal

_POSTHOG = override_settings(
    POSTHOG_API_KEY="test-key", POSTHOG_HOST="https://eu.posthog.com"
)


class TestEmitServerSignal:
    """The helper delegates to ``posthog.capture`` under the server distinct_id."""

    @_POSTHOG
    def test_forwards_to_posthog(self) -> None:
        """Called with ``event`` + ``properties`` → posthog.capture receives both."""
        with patch("posthog.capture") as mock_capture:
            emit_server_signal("pwa.version.endpoint.hit", {"foo": 1})
        mock_capture.assert_called_once_with(
            event="pwa.version.endpoint.hit",
            distinct_id=SERVER_DISTINCT_ID,
            properties={"foo": 1},
        )

    @_POSTHOG
    def test_properties_default_to_empty_dict(self) -> None:
        """No properties → empty dict, not None (posthog.capture requires a dict)."""
        with patch("posthog.capture") as mock_capture:
            emit_server_signal("pwa.sw_config.hit")
        mock_capture.assert_called_once_with(
            event="pwa.sw_config.hit",
            distinct_id=SERVER_DISTINCT_ID,
            properties={},
        )

    @override_settings(POSTHOG_API_KEY="")
    def test_no_op_when_key_empty(self) -> None:
        """No API key → analytics.track short-circuits, nothing captured."""
        with patch("posthog.capture") as mock_capture:
            emit_server_signal("pwa.push.sent", {"subscription_pk": 1})
        mock_capture.assert_not_called()

    @override_settings(POSTHOG_API_KEY="")
    def test_analytics_track_not_called_when_key_empty(self) -> None:
        """SNOW-384: the gate lives in emit_server_signal itself — it never
        even calls ``analytics.track()`` when the key is unset, rather than
        relying solely on ``track()``'s own internal no-op.
        """
        with patch("analytics.signals.analytics.track") as mock_track:
            emit_server_signal("pwa.push.sent", {"subscription_pk": 1})
        mock_track.assert_not_called()

    @override_settings(POSTHOG_API_KEY="test-key")
    def test_analytics_track_called_when_key_set(self) -> None:
        """SNOW-384: with a key configured, ``analytics.track()`` is called."""
        with patch("analytics.signals.analytics.track") as mock_track:
            emit_server_signal("pwa.push.sent", {"subscription_pk": 1})
        mock_track.assert_called_once_with(
            "pwa.push.sent", SERVER_DISTINCT_ID, {"subscription_pk": 1}
        )


class TestVersionEndpointSignal:
    """/api/version fires ``pwa.version.endpoint.hit``."""

    @pytest.mark.django_db
    @_POSTHOG
    def test_get_version_emits_signal(self) -> None:
        with patch("posthog.capture") as mock_capture:
            response = Client().get("/api/version")
        assert response.status_code == 200
        events = [c.kwargs["event"] for c in mock_capture.call_args_list]
        assert "pwa.version.endpoint.hit" in events


class TestSwConfigSignal:
    """/api/sw-config fires ``pwa.sw_config.hit`` with sw_url + kill props."""

    @pytest.mark.django_db
    @_POSTHOG
    @override_settings(SW_URL="/sw.js", SW_KILL=False)
    def test_get_sw_config_emits_signal(self) -> None:
        with patch("posthog.capture") as mock_capture:
            response = Client().get(
                "/api/sw-config", HTTP_X_CLIENT_VERSION="2026.07.16.abc"
            )
        assert response.status_code == 200
        hits = [
            c
            for c in mock_capture.call_args_list
            if c.kwargs["event"] == "pwa.sw_config.hit"
        ]
        assert len(hits) == 1
        assert hits[0].kwargs["properties"] == {
            "sw_url": "/sw.js",
            "kill": False,
            "client_version": "2026.07.16.abc",
        }

    @pytest.mark.django_db
    @_POSTHOG
    @override_settings(SW_URL="/sw-kill.js", SW_KILL=True)
    def test_get_sw_config_emits_reason_when_killed(self) -> None:
        """SNOW-384: ``reason`` is stamped only when ``kill`` is true."""
        with patch("posthog.capture") as mock_capture:
            Client().get("/api/sw-config")
        hits = [
            c
            for c in mock_capture.call_args_list
            if c.kwargs["event"] == "pwa.sw_config.hit"
        ]
        assert len(hits) == 1
        assert hits[0].kwargs["properties"]["reason"] == "env_kill_switch"

    @pytest.mark.django_db
    @_POSTHOG
    def test_get_version_emits_client_version_property(self) -> None:
        """SNOW-384: client_version is read off the X-Client-Version header."""
        with patch("posthog.capture") as mock_capture:
            Client().get("/api/version", HTTP_X_CLIENT_VERSION="2026.07.16.abc")
        hits = [
            c
            for c in mock_capture.call_args_list
            if c.kwargs["event"] == "pwa.version.endpoint.hit"
        ]
        assert len(hits) == 1
        assert hits[0].kwargs["properties"] == {"client_version": "2026.07.16.abc"}


class TestPushSentSignals:
    """dispatch_push fires ``pwa.push.sent`` (2xx) and ``pwa.push.gone_410`` (410)."""

    @pytest.mark.django_db
    @_POSTHOG
    def test_success_emits_push_sent(self) -> None:
        """A 2xx response from pywebpush fires pwa.push.sent."""
        from subscriptions.push_service import dispatch_push
        from tests.factories import PushSubscriptionFactory

        sub = PushSubscriptionFactory.create()

        class _FakeResp:
            status_code = 201

        with (
            patch("subscriptions.push_service.webpush", return_value=_FakeResp()),
            patch("posthog.capture") as mock_capture,
        ):
            result = dispatch_push(sub, {"title": "t", "body": "b"})

        assert result["ok"] is True
        events = [c.kwargs["event"] for c in mock_capture.call_args_list]
        assert "pwa.push.sent" in events

    @pytest.mark.django_db
    @_POSTHOG
    def test_success_threads_client_version(self) -> None:
        """SNOW-384: client_version passed to dispatch_push lands in properties."""
        from subscriptions.push_service import dispatch_push
        from tests.factories import PushSubscriptionFactory

        sub = PushSubscriptionFactory.create()

        class _FakeResp:
            status_code = 201

        with (
            patch("subscriptions.push_service.webpush", return_value=_FakeResp()),
            patch("posthog.capture") as mock_capture,
        ):
            dispatch_push(
                sub, {"title": "t", "body": "b"}, client_version="2026.07.16.abc"
            )

        hits = [
            c
            for c in mock_capture.call_args_list
            if c.kwargs["event"] == "pwa.push.sent"
        ]
        assert len(hits) == 1
        assert hits[0].kwargs["properties"]["client_version"] == "2026.07.16.abc"

    @pytest.mark.django_db
    @_POSTHOG
    def test_410_emits_push_gone(self) -> None:
        """A 410 response fires pwa.push.gone_410 and soft-deletes the subscription.

        SNOW-380: 410 marks the row inactive (``inactive_at`` set) rather
        than deleting it, so the client-side re-verification loop has a
        record to reconcile against.
        """
        from pywebpush import WebPushException

        from subscriptions.push_service import dispatch_push
        from tests.factories import PushSubscriptionFactory

        sub = PushSubscriptionFactory.create()
        sub_pk = sub.pk

        class _FakeResp:
            status_code = 410

        exc = WebPushException("gone")
        exc.response = _FakeResp()

        with (
            patch("subscriptions.push_service.webpush", side_effect=exc),
            patch("posthog.capture") as mock_capture,
        ):
            result = dispatch_push(sub, {"title": "t", "body": "b"})

        assert result["ok"] is False
        assert result["status"] == 410
        events = [c.kwargs["event"] for c in mock_capture.call_args_list]
        assert "pwa.push.gone_410" in events
        # Subscription row is kept but marked inactive.
        from subscriptions.models import PushSubscription

        row = PushSubscription.objects.get(pk=sub_pk)
        assert row.inactive_at is not None


class TestIdempotencyReplaySignal:
    """A cache hit in IdempotencyMiddleware fires ``pwa.idempotency.replay``."""

    @pytest.mark.django_db
    @_POSTHOG
    def test_replay_emits_signal(self) -> None:
        """A second POST with the same Idempotency-Key fires the signal."""
        from django.http import HttpRequest, HttpResponse

        from core.idempotency import IdempotencyMiddleware

        def _view(_request: HttpRequest) -> HttpResponse:
            return HttpResponse(b"created", status=201, content_type="text/plain")

        middleware = IdempotencyMiddleware(_view)

        def _post(key: str) -> HttpRequest:
            r = HttpRequest()
            r.method = "POST"
            r.path = "/subscribe/"
            r.META["HTTP_IDEMPOTENCY_KEY"] = key
            return r

        # Prime the cache (first POST — no replay signal).
        with patch("posthog.capture") as first_capture:
            middleware(_post("replay-key-1"))
        first_events = [c.kwargs["event"] for c in first_capture.call_args_list]
        assert "pwa.idempotency.replay" not in first_events

        # Second POST with same key — replay signal expected.
        with patch("posthog.capture") as second_capture:
            middleware(_post("replay-key-1"))
        second_events = [c.kwargs["event"] for c in second_capture.call_args_list]
        assert "pwa.idempotency.replay" in second_events
