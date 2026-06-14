"""
tests/subscriptions/test_push_service.py — Tests for dispatch_push and the async worker.

Patches pywebpush.webpush to test three branches:
  - 201 happy path: row survives, returns {ok: True, status: 201}.
  - WebPushException with 410 (subscription gone): row deleted, returns {ok: False}.
  - WebPushException with 500 (transient error): row survives, returns {ok: False}.
  - SNOW-311 caplog regression: log records contain pk=, never the subscriber email.

Also tests _worker_dispatch_push (SNOW-319):
  - Happy path: loads the subscription by PK and calls dispatch_push.
  - DoesNotExist: exits silently without calling dispatch_push when the row is gone.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from pywebpush import WebPushException

from subscriptions.models import PushSubscription
from subscriptions.push_service import _worker_dispatch_push, dispatch_push
from tests.factories import PushSubscriptionFactory, SubscriberFactory


def _make_webpush_exception(status_code: int) -> WebPushException:
    """Construct a WebPushException with a mock response carrying status_code."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    exc = WebPushException(f"Push failed with {status_code}", response=mock_response)
    return exc


@pytest.mark.django_db
class TestDispatchPush:
    """Unit tests for dispatch_push."""

    def test_201_happy_path_returns_ok(self) -> None:
        """A 201 response from the push service returns {ok: True, status: 201}."""
        sub = PushSubscriptionFactory.create()
        mock_response = MagicMock()
        mock_response.status_code = 201
        with patch("subscriptions.push_service.webpush", return_value=mock_response):
            result = dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})
        assert result == {"ok": True, "status": 201}
        # Row must still exist.
        assert PushSubscription.objects.filter(pk=sub.pk).exists()

    def test_410_deletes_dead_subscription(self) -> None:
        """A 410 WebPushException deletes the row and returns {ok: False}."""
        sub = PushSubscriptionFactory.create()
        exc = _make_webpush_exception(410)
        with patch("subscriptions.push_service.webpush", side_effect=exc):
            result = dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})
        assert result["ok"] is False
        assert result["status"] == 410
        # Row must be deleted.
        assert not PushSubscription.objects.filter(pk=sub.pk).exists()

    def test_404_also_deletes_dead_subscription(self) -> None:
        """A 404 WebPushException also deletes the row (equivalent to 410)."""
        sub = PushSubscriptionFactory.create()
        exc = _make_webpush_exception(404)
        with patch("subscriptions.push_service.webpush", side_effect=exc):
            result = dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})
        assert result["ok"] is False
        assert result["status"] == 404
        assert not PushSubscription.objects.filter(pk=sub.pk).exists()

    def test_500_survives_row(self) -> None:
        """A 500 WebPushException returns {ok: False} but does not delete the row."""
        sub = PushSubscriptionFactory.create()
        exc = _make_webpush_exception(500)
        with patch("subscriptions.push_service.webpush", side_effect=exc):
            result = dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})
        assert result["ok"] is False
        assert result["status"] == 500
        # Row must survive — a 500 is a transient server error.
        assert PushSubscription.objects.filter(pk=sub.pk).exists()

    def test_webpush_exception_without_response_returns_none_status(self) -> None:
        """A WebPushException with no response returns status=None."""
        sub = PushSubscriptionFactory.create()
        exc = WebPushException("No response available", response=None)
        with patch("subscriptions.push_service.webpush", side_effect=exc):
            result = dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})
        assert result["ok"] is False
        assert result["status"] is None
        # Row survives when status is None (unknown error).
        assert PushSubscription.objects.filter(pk=sub.pk).exists()

    def test_webpush_called_with_correct_subscription_info(self) -> None:
        """dispatch_push forwards sub.to_dict() as subscription_info to webpush."""
        sub = PushSubscriptionFactory.create()
        mock_response = MagicMock()
        mock_response.status_code = 201
        with patch(
            "subscriptions.push_service.webpush", return_value=mock_response
        ) as mock_webpush:
            dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})
        _, call_kwargs = mock_webpush.call_args
        assert call_kwargs["subscription_info"] == sub.to_dict()


# ---------------------------------------------------------------------------
# SNOW-311 — caplog regression: no subscriber email in push_service log output
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDispatchPushLogging:
    """SNOW-311: dispatch_push logs pk + endpoint, never the subscriber email."""

    def test_410_log_contains_pk_not_email(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A 410 error log record contains pk= and never the subscriber email.

        The subscriptions logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        monkeypatch.setattr(logging.getLogger("subscriptions"), "propagate", True)
        subscriber = SubscriberFactory.create(user__email="push-caplog@example.com")
        sub = PushSubscriptionFactory.create(subscriber=subscriber)
        exc = WebPushException(
            "Push failed with 410",
            response=MagicMock(status_code=410),
        )
        with (
            caplog.at_level(logging.INFO, logger="subscriptions.push_service"),
            patch("subscriptions.push_service.webpush", side_effect=exc),
        ):
            dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})

        all_messages = [r.getMessage() for r in caplog.records]

        # Plaintext email must not appear.
        for msg in all_messages:
            assert "push-caplog@example.com" not in msg, (
                f"Subscriber email found in log: {msg!r}"
            )

        # At least one record must mention pk=.
        assert any(f"pk={sub.pk}" in msg for msg in all_messages), (
            f"No log record contains pk={sub.pk}; records: {all_messages}"
        )


# ---------------------------------------------------------------------------
# SNOW-319 — _worker_dispatch_push async worker
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWorkerDispatchPush:
    """Unit tests for _worker_dispatch_push."""

    def test_happy_path_calls_dispatch_push(self) -> None:
        """Worker loads the subscription by PK and calls dispatch_push once."""
        sub = PushSubscriptionFactory.create()
        payload = {"title": "Hi", "body": "Test", "url": "/"}
        with patch("subscriptions.push_service.dispatch_push") as mock_dispatch:
            _worker_dispatch_push.call(sub.pk, payload)
        mock_dispatch.assert_called_once()
        call_args = mock_dispatch.call_args
        loaded_sub = call_args[0][0]
        assert loaded_sub.pk == sub.pk
        assert call_args[0][1] == payload

    def test_does_not_exist_does_not_raise(self) -> None:
        """Worker exits silently when the PushSubscription row no longer exists."""
        payload = {"title": "Hi", "body": "Test", "url": "/"}
        with patch("subscriptions.push_service.dispatch_push") as mock_dispatch:
            _worker_dispatch_push.call(999999, payload)
        mock_dispatch.assert_not_called()
