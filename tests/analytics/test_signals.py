"""tests/analytics/test_signals.py — server-side PWA signal helper.

Covers ``apps.analytics.signals.emit_server_signal`` (SNOW-381, spec §16.2)
and the five call sites where it's wired into existing code paths:

* ``apps.public.api.version``                 — pwa.version.endpoint.hit
* ``apps.public.api.sw_config``               — pwa.sw_config.hit
* ``apps.accounts.push_service``               — pwa.push.sent / pwa.push.gone_410
* ``apps.core.idempotency.IdempotencyMiddleware`` — pwa.idempotency.replay

SNOW-1182: the signals are structured INFO log lines now that PostHog is
gone. Each test patches the module's ``logger`` and reads the event name
and the ``k=v`` properties off its ``info`` calls.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client, override_settings

from apps.analytics.signals import emit_server_signal, format_properties

_LOGGER = "apps.analytics.signals.logger"


def _events(logger: MagicMock) -> list[str]:
    """Return the event names logged through ``logger.info``."""
    return [c.args[1] for c in logger.info.call_args_list]


def _fields(logger: MagicMock, event: str) -> list[str]:
    """Return the ``k=v`` strings logged for each ``event``."""
    return [c.args[2] for c in logger.info.call_args_list if c.args[1] == event]


class TestEmitServerSignal:
    """The helper writes one structured INFO line per signal."""

    def test_logs_event_and_properties(self) -> None:
        """Called with ``event`` + ``properties`` → one INFO line with both."""
        with patch(_LOGGER) as logger:
            emit_server_signal("pwa.version.endpoint.hit", {"foo": 1})
        logger.info.assert_called_once_with(
            "telemetry.signal event=%s %s", "pwa.version.endpoint.hit", "foo=1"
        )

    def test_properties_default_to_empty(self) -> None:
        """No properties → an empty field list."""
        with patch(_LOGGER) as logger:
            emit_server_signal("pwa.sw_config.hit")
        assert _fields(logger, "pwa.sw_config.hit") == [""]

    @override_settings(PWA_TELEMETRY_ENABLED=False)
    def test_no_op_when_master_switch_off(self) -> None:
        """PWA_TELEMETRY_ENABLED=False silences the signals."""
        with patch(_LOGGER) as logger:
            emit_server_signal("pwa.version.endpoint.hit", {"foo": 1})
        logger.info.assert_not_called()

    def test_format_properties_is_sorted(self) -> None:
        """Fields log in name order, so one event always reads the same."""
        props: dict[str, Any] = {"b": 2, "a": "x"}
        assert format_properties(props) == "a=x b=2"


class TestVersionEndpointSignal:
    """/api/version fires ``pwa.version.endpoint.hit``."""

    @pytest.mark.django_db
    def test_get_version_emits_signal(self) -> None:
        with patch(_LOGGER) as mock_capture:
            response = Client().get("/api/version")
        assert response.status_code == 200
        events = [c.args[1] for c in mock_capture.info.call_args_list]
        assert "pwa.version.endpoint.hit" in events


class TestSwConfigSignal:
    """/api/sw-config fires ``pwa.sw_config.hit`` with sw_url + kill props."""

    @pytest.mark.django_db
    @override_settings(SW_URL="/sw.js", SW_KILL=False)
    def test_get_sw_config_emits_signal(self) -> None:
        with patch(_LOGGER) as mock_capture:
            response = Client().get(
                "/api/sw-config", HTTP_X_CLIENT_VERSION="2026.07.16.abc"
            )
        assert response.status_code == 200
        hits = _fields(mock_capture, "pwa.sw_config.hit")
        assert len(hits) == 1
        assert hits[0] == "client_version=2026.07.16.abc kill=False sw_url=/sw.js"

    @pytest.mark.django_db
    @override_settings(SW_URL="/sw-kill.js", SW_KILL=True)
    def test_get_sw_config_emits_reason_when_killed(self) -> None:
        """SNOW-384: ``reason`` is stamped only when ``kill`` is true."""
        with patch(_LOGGER) as mock_capture:
            Client().get("/api/sw-config")
        hits = _fields(mock_capture, "pwa.sw_config.hit")
        assert len(hits) == 1
        assert "reason=env_kill_switch" in hits[0]

    @pytest.mark.django_db
    def test_get_version_emits_client_version_property(self) -> None:
        """SNOW-384: client_version is read off the X-Client-Version header."""
        with patch(_LOGGER) as mock_capture:
            Client().get("/api/version", HTTP_X_CLIENT_VERSION="2026.07.16.abc")
        hits = _fields(mock_capture, "pwa.version.endpoint.hit")
        assert len(hits) == 1
        assert hits[0] == "client_version=2026.07.16.abc"


class TestPushSentSignals:
    """dispatch_push fires ``pwa.push.sent`` (2xx) and ``pwa.push.gone_410`` (410)."""

    @pytest.mark.django_db
    def test_success_emits_push_sent(self) -> None:
        """A 2xx response from pywebpush fires pwa.push.sent."""
        from apps.accounts.push_service import dispatch_push
        from tests.factories import PushSubscriptionFactory

        sub = PushSubscriptionFactory.create()

        class _FakeResp:
            status_code = 201

        with (
            patch("apps.accounts.push_service.webpush", return_value=_FakeResp()),
            patch(_LOGGER) as mock_capture,
        ):
            result = dispatch_push(sub, {"title": "t", "body": "b"})

        assert result["ok"] is True
        events = [c.args[1] for c in mock_capture.info.call_args_list]
        assert "pwa.push.sent" in events

    @pytest.mark.django_db
    def test_success_threads_client_version(self) -> None:
        """SNOW-384: client_version passed to dispatch_push lands in properties."""
        from apps.accounts.push_service import dispatch_push
        from tests.factories import PushSubscriptionFactory

        sub = PushSubscriptionFactory.create()

        class _FakeResp:
            status_code = 201

        with (
            patch("apps.accounts.push_service.webpush", return_value=_FakeResp()),
            patch(_LOGGER) as mock_capture,
        ):
            dispatch_push(
                sub, {"title": "t", "body": "b"}, client_version="2026.07.16.abc"
            )

        hits = _fields(mock_capture, "pwa.push.sent")
        assert len(hits) == 1
        assert "client_version=2026.07.16.abc" in hits[0]

    @pytest.mark.django_db
    def test_410_emits_push_gone(self) -> None:
        """A 410 response fires pwa.push.gone_410 and soft-deletes the subscription.

        SNOW-380: 410 marks the row inactive (``inactive_at`` set) rather
        than deleting it, so the client-side re-verification loop has a
        record to reconcile against.
        """
        from pywebpush import WebPushException

        from apps.accounts.push_service import dispatch_push
        from tests.factories import PushSubscriptionFactory

        sub = PushSubscriptionFactory.create()
        sub_pk = sub.pk

        class _FakeResp:
            status_code = 410

        exc = WebPushException("gone")
        exc.response = _FakeResp()

        with (
            patch("apps.accounts.push_service.webpush", side_effect=exc),
            patch(_LOGGER) as mock_capture,
        ):
            result = dispatch_push(sub, {"title": "t", "body": "b"})

        assert result["ok"] is False
        assert result["status"] == 410
        events = [c.args[1] for c in mock_capture.info.call_args_list]
        assert "pwa.push.gone_410" in events
        # Subscription row is kept but marked inactive.
        from apps.accounts.models import PushSubscription

        row = PushSubscription.objects.get(pk=sub_pk)
        assert row.inactive_at is not None


class TestIdempotencyReplaySignal:
    """A cache hit in IdempotencyMiddleware fires ``pwa.idempotency.replay``."""

    @pytest.mark.django_db
    def test_replay_emits_signal(self) -> None:
        """A second POST with the same Idempotency-Key fires the signal."""
        from django.http import HttpRequest, HttpResponse

        from apps.core.idempotency import IdempotencyMiddleware

        def _view(_request: HttpRequest) -> HttpResponse:
            return HttpResponse(b"created", status=201, content_type="text/plain")

        middleware = IdempotencyMiddleware(_view)

        def _post(key: str) -> HttpRequest:
            r = HttpRequest()
            r.method = "POST"
            r.path = "/account/"
            r._body = b""  # SNOW-463: middleware now reads request.body
            r.META["HTTP_IDEMPOTENCY_KEY"] = key
            return r

        # Prime the cache (first POST — no replay signal).
        with patch(_LOGGER) as first_capture:
            middleware(_post("replay-key-1"))
        first_events = [c.args[1] for c in first_capture.info.call_args_list]
        assert "pwa.idempotency.replay" not in first_events

        # Second POST with same key — replay signal expected.
        with patch(_LOGGER) as second_capture:
            middleware(_post("replay-key-1"))
        second_events = [c.args[1] for c in second_capture.info.call_args_list]
        assert "pwa.idempotency.replay" in second_events


def test_telemetry_lines_reach_the_log_at_info() -> None:
    """The INFO lines are not dropped by the WARNING root (Codex P1, #1158)."""
    import logging

    for name in ("apps.analytics.signals", "apps.analytics.views"):
        assert logging.getLogger(name).isEnabledFor(logging.INFO), name
