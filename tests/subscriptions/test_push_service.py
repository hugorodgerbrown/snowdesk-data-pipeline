"""
tests/subscriptions/test_push_service.py — Tests for dispatch_push.

Patches pywebpush.webpush to test three branches:
  - 201 happy path: row survives, returns {ok: True, status: 201}.
  - WebPushException with 410 (subscription gone): row deleted, returns {ok: False}.
  - WebPushException with 500 (transient error): row survives, returns {ok: False}.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pywebpush import WebPushException

from subscriptions.models import PushSubscription
from subscriptions.push_service import dispatch_push
from tests.factories import PushSubscriptionFactory


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
