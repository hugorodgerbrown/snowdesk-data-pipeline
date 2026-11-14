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
  - A Delivery's TTL, Urgency and Topic reach webpush; none sends neither
    header (SNOW-1495).
  - SNOW-1494: an SW push carries a receipt over the TTL actually sent; a
    counted push is counted, uncounted on failure, reports trusted, and
    retires a dead device; a declarative or uncounted push is not counted.
  - SNOW-311 caplog regression: log records contain pk=, never the account email.

Also tests _worker_dispatch_push (SNOW-319):
  - Happy path: loads the subscription by PK and calls dispatch_push.
  - DoesNotExist: exits silently without calling dispatch_push when the row is gone.
"""

from __future__ import annotations

import binascii
import datetime
import json
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.core import mail
from django.utils import timezone
from freezegun import freeze_time
from pywebpush import WebPushException

from apps.accounts.models import PushSubscription
from apps.accounts.push_service import (
    DEFAULT_TTL_SECONDS,
    _worker_dispatch_push,
    dispatch_push,
)
from apps.accounts.services.push_health import (
    RECEIPT_GRACE_SECONDS,
    RETIRE_AFTER,
    RETIRE_AFTER_PUSHES,
    confirm,
)
from apps.bulletins.services.push_delivery import Delivery
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
        assert result == {"ok": True, "status": 201, "trusted": True, "retired": False}
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
        assert sub.retired_reason == PushSubscription.RetiredReason.GONE_410
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

    def test_unusable_vapid_key_returns_failure(self) -> None:
        """A key pywebpush cannot decode is a failed send, not a raised error.

        Staging's misread secret file raised binascii.Error from inside
        webpush on every send; the alert worker needs an outcome to settle on.
        """
        sub = PushSubscriptionFactory.create()
        exc = binascii.Error("Invalid base64-encoded string")
        with patch("apps.accounts.push_service.webpush", side_effect=exc):
            result = dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})
        assert result == {
            "ok": False,
            "status": None,
            "trusted": False,
            "retired": False,
            "error": "Invalid base64-encoded string",
        }
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

    def test_a_plain_push_lives_a_minute_with_no_urgency(self) -> None:
        """A test or demo push keeps the one-minute TTL and sends no Urgency."""
        sub = PushSubscriptionFactory.create()
        mock_response = MagicMock()
        mock_response.status_code = 201
        with patch(
            "apps.accounts.push_service.webpush", return_value=mock_response
        ) as mock_webpush:
            dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})
        _, call_kwargs = mock_webpush.call_args
        assert call_kwargs["ttl"] == DEFAULT_TTL_SECONDS == 60
        assert call_kwargs["headers"] is None

    def test_delivery_reaches_the_push_service(self) -> None:
        """An alert's TTL, Urgency and Topic go out on the push request."""
        sub = PushSubscriptionFactory.create()
        mock_response = MagicMock()
        mock_response.status_code = 201
        now = datetime.datetime(2026, 11, 14, 8, 0, tzinfo=datetime.UTC)
        delivery = Delivery(
            urgency="high",
            expires_at=now + datetime.timedelta(hours=5),
            topic="alert-20261114",
        )
        with (
            patch(
                "apps.accounts.push_service.webpush", return_value=mock_response
            ) as mock_webpush,
            patch("apps.accounts.push_service.timezone.now", return_value=now),
        ):
            dispatch_push(
                sub, {"title": "Hi", "body": "Test", "url": "/"}, delivery=delivery
            )
        _, call_kwargs = mock_webpush.call_args
        assert call_kwargs["ttl"] == 5 * 60 * 60
        assert call_kwargs["headers"] == {
            "Urgency": "high",
            "Topic": "alert-20261114",
        }

    def test_delivery_without_topic_sends_no_topic(self) -> None:
        """A delivery with no topic sends Urgency alone."""
        sub = PushSubscriptionFactory.create()
        mock_response = MagicMock()
        mock_response.status_code = 201
        delivery = Delivery(urgency="normal", expires_at=timezone.now())
        with patch(
            "apps.accounts.push_service.webpush", return_value=mock_response
        ) as mock_webpush:
            dispatch_push(sub, {"title": "Hi"}, delivery=delivery)
        _, call_kwargs = mock_webpush.call_args
        assert call_kwargs["headers"] == {"Urgency": "normal"}
        assert call_kwargs["ttl"] == 60 * 60

    def test_dispatch_push_sw_payload_unchanged(self) -> None:
        """A 'sw' subscription gets {title, body, url} plus its receipt token."""
        sub = PushSubscriptionFactory.create(mechanism=PushSubscription.Mechanism.SW)
        payload = {"title": "Hi", "body": "Test", "url": "/somewhere"}
        mock_response = MagicMock()
        mock_response.status_code = 201
        with patch(
            "apps.accounts.push_service.webpush", return_value=mock_response
        ) as mock_webpush:
            dispatch_push(sub, payload)
        _, call_kwargs = mock_webpush.call_args
        sent = json.loads(call_kwargs["data"])
        receipt = sent.pop("receipt")
        assert sent == payload
        assert confirm(receipt, timezone.now()) is True

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
# SNOW-1494 — receipts, counting and retirement
# ---------------------------------------------------------------------------


def _accepted() -> MagicMock:
    """Return a mock 201 push-service response."""
    response = MagicMock()
    response.status_code = 201
    return response


_PAYLOAD = {"title": "Hi", "body": "Test", "url": "/"}


@pytest.mark.django_db
class TestDispatchPushReceipts:
    """SNOW-1494: the receipt token and the unconfirmed count."""

    @pytest.mark.parametrize("hours", [None, 12])
    def test_the_receipt_lasts_the_ttl_actually_sent(self, hours: int | None) -> None:
        """The token is good to the TTL sent plus the grace, and no longer.

        ``None`` is the 60 s default (the test button); 12 h is an alert's.
        """
        sub = PushSubscriptionFactory.create()
        now = datetime.datetime(2026, 11, 14, 8, 0, tzinfo=datetime.UTC)
        delivery = (
            None
            if hours is None
            else Delivery(
                urgency="normal",
                expires_at=now + datetime.timedelta(hours=hours),
                topic="",
            )
        )
        ttl = DEFAULT_TTL_SECONDS if hours is None else hours * 3600
        with (
            freeze_time(now),
            patch(
                "apps.accounts.push_service.webpush", return_value=_accepted()
            ) as mock_webpush,
        ):
            dispatch_push(sub, _PAYLOAD, delivery=delivery)
        _, call_kwargs = mock_webpush.call_args
        assert call_kwargs["ttl"] == ttl
        receipt = json.loads(call_kwargs["data"])["receipt"]
        edge = now + datetime.timedelta(seconds=ttl + RECEIPT_GRACE_SECONDS)
        assert confirm(receipt, edge + datetime.timedelta(seconds=1)) is False
        assert confirm(receipt, edge) is True

    def test_an_uncounted_push_counts_nothing(self) -> None:
        """The test button's push leaves the count alone and is trusted."""
        sub = PushSubscriptionFactory.create(
            unconfirmed_pushes=2, unconfirmed_since=timezone.now()
        )
        with patch("apps.accounts.push_service.webpush", return_value=_accepted()):
            result = dispatch_push(sub, _PAYLOAD)
        assert result["trusted"] is True
        sub.refresh_from_db()
        assert sub.unconfirmed_pushes == 2

    def test_a_counted_push_to_a_confirmed_device_is_trusted(self) -> None:
        """The first counted push is trusted, and leaves one owed."""
        sub = PushSubscriptionFactory.create()
        with patch("apps.accounts.push_service.webpush", return_value=_accepted()):
            result = dispatch_push(sub, _PAYLOAD, count_unconfirmed=True)
        assert result["ok"] is True
        assert result["trusted"] is True
        assert result["retired"] is False
        sub.refresh_from_db()
        assert sub.unconfirmed_pushes == 1
        assert sub.unconfirmed_since is not None

    def test_a_counted_push_to_a_device_owing_a_receipt_is_untrusted(self) -> None:
        """Accepted, but the device never confirmed the last one."""
        sub = PushSubscriptionFactory.create(
            unconfirmed_pushes=1, unconfirmed_since=timezone.now()
        )
        with patch("apps.accounts.push_service.webpush", return_value=_accepted()):
            result = dispatch_push(sub, _PAYLOAD, count_unconfirmed=True)
        assert result["ok"] is True
        assert result["trusted"] is False

    @pytest.mark.parametrize("status", [500, 410])
    def test_a_refused_push_is_uncounted(self, status: int) -> None:
        """A push the push service did not take is not owed a receipt."""
        sub = PushSubscriptionFactory.create()
        exc = _make_webpush_exception(status)
        with patch("apps.accounts.push_service.webpush", side_effect=exc):
            result = dispatch_push(sub, _PAYLOAD, count_unconfirmed=True)
        assert result["ok"] is False
        assert result["trusted"] is False
        sub.refresh_from_db()
        assert sub.unconfirmed_pushes == 0
        assert sub.unconfirmed_since is None

    def test_a_push_that_cannot_be_attempted_is_uncounted(self) -> None:
        """A raise before any request is made takes the count back too."""
        sub = PushSubscriptionFactory.create()
        with patch(
            "apps.accounts.push_service.webpush",
            side_effect=binascii.Error("bad key"),
        ):
            result = dispatch_push(sub, _PAYLOAD, count_unconfirmed=True)
        assert result["ok"] is False
        sub.refresh_from_db()
        assert sub.unconfirmed_pushes == 0

    def test_a_declarative_push_is_never_counted(self) -> None:
        """No service worker runs, so no receipt can come and none is counted."""
        sub = PushSubscriptionFactory.create(
            mechanism=PushSubscription.Mechanism.DECLARATIVE
        )
        with patch(
            "apps.accounts.push_service.webpush", return_value=_accepted()
        ) as mock_webpush:
            result = dispatch_push(sub, _PAYLOAD, count_unconfirmed=True)
        assert result["trusted"] is True
        assert "receipt" not in json.dumps(
            json.loads(mock_webpush.call_args[1]["data"])
        )
        sub.refresh_from_db()
        assert sub.unconfirmed_pushes == 0

    def test_the_push_that_crosses_the_threshold_retires_the_device(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        """Accepted, untrusted, and the device is retired with an email."""
        now = timezone.now()
        sub = PushSubscriptionFactory.create(
            account=AccountFactory.create(),
            unconfirmed_pushes=RETIRE_AFTER_PUSHES - 1,
            unconfirmed_since=now - RETIRE_AFTER,
        )
        with (
            patch("apps.accounts.push_service.webpush", return_value=_accepted()),
            django_capture_on_commit_callbacks(execute=True),
        ):
            result = dispatch_push(sub, _PAYLOAD, count_unconfirmed=True)
        assert result == {"ok": True, "status": 201, "trusted": False, "retired": True}
        sub.refresh_from_db()
        assert sub.inactive_at is not None
        assert sub.retired_reason == PushSubscription.RetiredReason.UNCONFIRMED
        assert len(mail.outbox) == 1


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
        monkeypatch.setattr(logging.getLogger("apps.accounts"), "propagate", True)
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
        monkeypatch.setattr(logging.getLogger("apps.accounts"), "propagate", True)
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


@pytest.mark.django_db
class TestDispatchPushSuccessLogging:
    """SNOW-874: a successful dispatch says so, and says what came back."""

    def test_success_logs_the_push_service_status(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A 201 leaves a log record carrying the pk and the status code.

        Before this, ``dispatch_push`` warned on failure and was silent on
        success, so reading a production log to answer "did the push service
        take it?" meant proving a negative — no warning, therefore fine.
        That reasoning is sound (``webpush`` raises above 202) but it is not
        something a log reader can see, and on 2026-09-08 it cost an hour of
        a live investigation.

        The accounts logger has propagate=False in base.py; flip it for the
        test so caplog can capture the records.
        """
        monkeypatch.setattr(logging.getLogger("apps.accounts"), "propagate", True)
        sub = PushSubscriptionFactory.create()
        mock_response = MagicMock()
        mock_response.status_code = 201

        with (
            patch("apps.accounts.push_service.webpush", return_value=mock_response),
            caplog.at_level(logging.INFO, logger="apps.accounts.push_service"),
        ):
            dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})

        messages = [record.getMessage() for record in caplog.records]
        assert any(f"pk={sub.pk}" in msg and "201" in msg for msg in messages), (
            f"No success record carries pk={sub.pk} and the status; got {messages}"
        )

    def test_success_log_omits_the_account_email(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The new success record obeys SNOW-311 like every other one."""
        monkeypatch.setattr(logging.getLogger("apps.accounts"), "propagate", True)
        account = AccountFactory.create(user__email="success-caplog@example.com")
        sub = PushSubscriptionFactory.create(account=account)
        mock_response = MagicMock()
        mock_response.status_code = 201

        with (
            patch("apps.accounts.push_service.webpush", return_value=mock_response),
            caplog.at_level(logging.INFO, logger="apps.accounts.push_service"),
        ):
            dispatch_push(sub, {"title": "Hi", "body": "Test", "url": "/"})

        for message in (record.getMessage() for record in caplog.records):
            assert "success-caplog@example.com" not in message, (
                f"Account email found in log: {message!r}"
            )
