"""
tests/accounts/test_push_service.py — Tests for dispatch_push and the async worker.

Patches pywebpush.webpush to test several branches:
  - 201 happy path: row survives, returns {ok: True, status: 201}.
  - WebPushException with 410 (subscription gone): row soft-deleted
    (inactive_at set, not removed), returns {ok: False}.
  - WebPushException with 404 (transport error): row hard-deleted,
    returns {ok: False}.
  - WebPushException with 500 (transient error): row survives, returns {ok: False}.
  - Declarative vs sw payload shape (SNOW-380).
  - SNOW-311 caplog regression: log records contain pk=, never the account email.

Also tests _worker_dispatch_push (SNOW-319):
  - Happy path: loads the subscription by PK and calls dispatch_push.
  - DoesNotExist: exits silently without calling dispatch_push when the row is gone.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from pywebpush import WebPushException

from apps.accounts.models import PushSubscription
from apps.accounts.push_service import _worker_dispatch_push, dispatch_push
from tests.factories import AccountFactory, PushSubscriptionFactory


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
        with patch("apps.accounts.push_service.webpush", return_value=mock_response):
            result = dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})
        assert result == {"ok": True, "status": 201}
        # Row must still exist.
        assert PushSubscription.objects.filter(pk=sub.pk).exists()

    def test_dispatch_push_410_marks_inactive(self) -> None:
        """A 410 WebPushException soft-deletes: inactive_at set, row not deleted.

        SNOW-380: 410 Gone means the push service has confirmed the
        subscription is dead, but the row is kept (marked inactive) so the
        client-side re-verification loop has something to reconcile
        against, rather than hard-deleted.
        """
        # Factory default (inactive_at=None) is covered by
        # test_push_models.py::test_inactive_at_defaults_to_none; not
        # re-asserted here because mypy narrows the field's type after an
        # `is None` check and flags the later `is not None` check below as
        # unreachable.
        sub = PushSubscriptionFactory.create()
        before = timezone.now()
        exc = _make_webpush_exception(410)
        with (
            patch("apps.accounts.push_service.webpush", side_effect=exc),
            patch("apps.accounts.push_service.emit_server_signal") as mock_emit,
        ):
            result = dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})
        assert result["ok"] is False
        assert result["status"] == 410
        # Row must still exist, now marked inactive.
        sub.refresh_from_db()
        assert sub.inactive_at is not None
        assert sub.inactive_at >= before
        mock_emit.assert_any_call(
            "pwa.push.gone_410",
            {"subscription_pk": sub.pk, "client_version": ""},
        )

    def test_dispatch_push_404_still_deletes(self) -> None:
        """A 404 WebPushException still hard-deletes the row (equivalent to
        the pre-SNOW-380 behaviour) — 404 is a transport-layer error, not a
        confirmed-gone signal, so there's nothing useful to reconcile.
        """
        sub = PushSubscriptionFactory.create()
        exc = _make_webpush_exception(404)
        with patch("apps.accounts.push_service.webpush", side_effect=exc):
            result = dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})
        assert result["ok"] is False
        assert result["status"] == 404
        assert not PushSubscription.objects.filter(pk=sub.pk).exists()

    def test_500_survives_row(self) -> None:
        """A 500 WebPushException returns {ok: False} but does not delete the row."""
        sub = PushSubscriptionFactory.create()
        exc = _make_webpush_exception(500)
        with patch("apps.accounts.push_service.webpush", side_effect=exc):
            result = dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})
        assert result["ok"] is False
        assert result["status"] == 500
        # Row must survive — a 500 is a transient server error.
        assert PushSubscription.objects.filter(pk=sub.pk).exists()

    def test_webpush_exception_without_response_returns_none_status(self) -> None:
        """A WebPushException with no response returns status=None."""
        sub = PushSubscriptionFactory.create()
        exc = WebPushException("No response available", response=None)
        with patch("apps.accounts.push_service.webpush", side_effect=exc):
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
            "apps.accounts.push_service.webpush", return_value=mock_response
        ) as mock_webpush:
            dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})
        _, call_kwargs = mock_webpush.call_args
        assert call_kwargs["subscription_info"] == sub.to_dict()

    def test_dispatch_push_sw_payload_unchanged(self) -> None:
        """A 'sw' subscription still gets the plain {title, body, url} shape."""
        sub = PushSubscriptionFactory.create(mechanism=PushSubscription.Mechanism.SW)
        payload = {"title": "Hi", "body": "Test", "url": "/somewhere"}
        mock_response = MagicMock()
        mock_response.status_code = 201
        with patch(
            "apps.accounts.push_service.webpush", return_value=mock_response
        ) as mock_webpush:
            dispatch_push(sub, payload)
        _, call_kwargs = mock_webpush.call_args
        assert json.loads(call_kwargs["data"]) == payload

    def test_dispatch_push_declarative_payload_shape(self) -> None:
        """A 'declarative' subscription gets Apple's Declarative Web Push shape,
        not the sw {title, body, url} shape.
        """
        sub = PushSubscriptionFactory.create(
            mechanism=PushSubscription.Mechanism.DECLARATIVE
        )
        payload = {"title": "Hi", "body": "Test", "url": "/somewhere"}
        mock_response = MagicMock()
        mock_response.status_code = 201
        with patch(
            "apps.accounts.push_service.webpush", return_value=mock_response
        ) as mock_webpush:
            dispatch_push(sub, payload)
        _, call_kwargs = mock_webpush.call_args
        sent = json.loads(call_kwargs["data"])
        assert sent == {
            "web_push": 8030,
            "notification": {
                "title": "Hi",
                "body": "Test",
                "navigate": "/somewhere",
            },
        }


# ---------------------------------------------------------------------------
# SNOW-311 — caplog regression: no account email in push_service log output
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDispatchPushLogging:
    """SNOW-311: dispatch_push logs pk + endpoint, never the account email."""

    def test_410_log_contains_pk_not_email(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A 410 error log record contains pk= and never the account email.

        The accounts logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        monkeypatch.setattr(logging.getLogger("accounts"), "propagate", True)
        account = AccountFactory.create(user__email="push-caplog@example.com")
        sub = PushSubscriptionFactory.create(account=account)
        exc = WebPushException(
            "Push failed with 410",
            response=MagicMock(status_code=410),
        )
        with (
            caplog.at_level(logging.INFO, logger="apps.accounts.push_service"),
            patch("apps.accounts.push_service.webpush", side_effect=exc),
        ):
            dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})

        all_messages = [r.getMessage() for r in caplog.records]

        # Plaintext email must not appear.
        for msg in all_messages:
            assert "push-caplog@example.com" not in msg, (
                f"Account email found in log: {msg!r}"
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
        with patch("apps.accounts.push_service.dispatch_push") as mock_dispatch:
            _worker_dispatch_push.call(sub.pk, payload)
        mock_dispatch.assert_called_once()
        call_args = mock_dispatch.call_args
        loaded_sub = call_args[0][0]
        assert loaded_sub.pk == sub.pk
        assert call_args[0][1] == payload

    def test_does_not_exist_does_not_raise(self) -> None:
        """Worker exits silently when the PushSubscription row no longer exists."""
        payload = {"title": "Hi", "body": "Test", "url": "/"}
        with patch("apps.accounts.push_service.dispatch_push") as mock_dispatch:
            _worker_dispatch_push.call(999999, payload)
        mock_dispatch.assert_not_called()

    def test_threads_client_version_to_dispatch_push(self) -> None:
        """SNOW-384: client_version passed to the worker reaches dispatch_push."""
        sub = PushSubscriptionFactory.create()
        payload = {"title": "Hi", "body": "Test", "url": "/"}
        with patch("apps.accounts.push_service.dispatch_push") as mock_dispatch:
            _worker_dispatch_push.call(sub.pk, payload, "2026.07.16.abc")
        call_args = mock_dispatch.call_args
        assert call_args[0][2] == "2026.07.16.abc"

    def test_does_not_exist_log_contains_pk_not_email(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SNOW-311: DoesNotExist log record contains pk= and never an account email.

        Creates an account so there is a real email in the database, then
        exercises the DoesNotExist path with a non-existent PK. The log message
        must reference the pk= only — no email should be emitted.
        """
        monkeypatch.setattr(logging.getLogger("accounts"), "propagate", True)
        account = AccountFactory.create(user__email="worker-caplog@example.com")
        nonexistent_pk = account.pk + 999999
        payload = {"title": "Hi", "body": "Test", "url": "/"}

        with (
            caplog.at_level(logging.INFO, logger="apps.accounts.push_service"),
            patch("apps.accounts.push_service.dispatch_push"),
        ):
            _worker_dispatch_push.call(nonexistent_pk, payload)

        all_messages = [r.getMessage() for r in caplog.records]

        # Plaintext email must not appear.
        for msg in all_messages:
            assert "worker-caplog@example.com" not in msg, (
                f"Account email found in DoesNotExist log: {msg!r}"
            )

        # At least one record must mention pk=.
        assert any(f"pk={nonexistent_pk}" in msg for msg in all_messages), (
            f"No log record contains pk={nonexistent_pk}; records: {all_messages}"
        )
