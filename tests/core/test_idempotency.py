"""
Tests for ``core.idempotency.IdempotencyMiddleware`` (SNOW-371, fingerprint
hardening SNOW-463).

Covers the spec §5.5 / §12.3 contract:

* Same key twice → same response, view called once.
* Different keys → separate execution.
* Key past ``expires_at`` → re-executes (fresh row).
* Missing header → falls through to the view, logs
  ``pwa.idempotency.missing``.
* Non-state-changing methods (GET/HEAD) → header ignored, no row created.
* 5xx responses → not cached.
* Malformed keys (too long, non-ASCII, non-printable) → not cached and a
  warning is logged.

And the SNOW-463 fingerprint contract:

* Same key, different path/body/principal → ``409``, view not re-run,
  cached row untouched.
* Same key, identical method/path/principal/body → cache hit as before.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.test import Client
from django.utils import timezone

from core.idempotency import (
    IDEMPOTENCY_RECORD_TTL_SECONDS,
    IdempotencyMiddleware,
)
from core.models import IdempotencyRecord
from tests.factories import UserFactory

# ---------------------------------------------------------------------------
# Middleware-level tests — drive the middleware directly with a stub view.
# ---------------------------------------------------------------------------


class _CountingView:
    """Callable that returns a canned response and counts invocations."""

    def __init__(self, status: int = 200, body: bytes = b"ok") -> None:
        """Store the canned status and body, initialise the call counter."""
        self.status = status
        self.body = body
        self.calls = 0

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Increment the counter and return a new response object each time."""
        self.calls += 1
        return HttpResponse(
            content=self.body,
            status=self.status,
            content_type="text/plain",
        )


def _post(
    path: str = "/account/",
    key: str | None = None,
    body: bytes = b"",
    user: User | None = None,
) -> HttpRequest:
    """Build a bare-bones POST request with an optional Idempotency-Key.

    ``body`` seeds ``request._body`` so the middleware's ``request.body``
    read (via ``_body_hash``) works without a real WSGI environ. ``user``
    sets ``request.user`` so ``_principal`` can read it — bare
    ``HttpRequest`` objects otherwise lack the attribute at all, matching
    a request that reached the middleware before ``AuthenticationMiddleware``
    ran (defensive branch), and a passed-in user matches the ordinary case.
    """
    request = HttpRequest()
    request.method = "POST"
    request.path = path
    request._body = body
    if user is not None:
        request.user = user
    if key is not None:
        request.META["HTTP_IDEMPOTENCY_KEY"] = key
    return request


@pytest.mark.django_db
def test_replay_returns_cached_response_and_skips_view() -> None:
    """Two POSTs with the same key run the view once and return the same body."""
    view = _CountingView(status=201, body=b"created")
    middleware = IdempotencyMiddleware(view)

    first = middleware(_post(key="abc-123"))
    second = middleware(_post(key="abc-123"))

    assert view.calls == 1
    assert first.status_code == second.status_code == 201
    assert first.content == second.content == b"created"
    assert IdempotencyRecord.objects.filter(key="abc-123").count() == 1


@pytest.mark.django_db
def test_different_keys_execute_separately() -> None:
    """Distinct keys run the view once each and store two rows."""
    view = _CountingView()
    middleware = IdempotencyMiddleware(view)

    middleware(_post(key="key-a"))
    middleware(_post(key="key-b"))

    assert view.calls == 2
    assert IdempotencyRecord.objects.count() == 2


@pytest.mark.django_db
def test_expired_record_re_executes_view() -> None:
    """Once the retention window elapses, a replay re-invokes the view."""
    view = _CountingView()
    middleware = IdempotencyMiddleware(view)

    middleware(_post(key="expiring"))
    assert view.calls == 1

    # Fast-forward the row into the past.
    IdempotencyRecord.objects.filter(key="expiring").update(
        expires_at=timezone.now() - timedelta(minutes=1),
    )

    middleware(_post(key="expiring"))
    assert view.calls == 2


@pytest.mark.django_db
def test_missing_header_falls_through_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A POST without the header runs normally and logs the missing event."""
    view = _CountingView()
    middleware = IdempotencyMiddleware(view)

    with caplog.at_level(logging.INFO, logger="core.idempotency"):
        middleware(_post())

    assert view.calls == 1
    assert IdempotencyRecord.objects.count() == 0
    assert any("pwa.idempotency.missing" in record.message for record in caplog.records)


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_non_state_changing_methods_ignore_header(method: str) -> None:
    """Header on a GET/HEAD/OPTIONS request is a no-op — no row is created."""
    view = _CountingView()
    middleware = IdempotencyMiddleware(view)

    request = HttpRequest()
    request.method = method
    request.path = "/whatever/"
    request.META["HTTP_IDEMPOTENCY_KEY"] = "ignored"

    middleware(request)
    middleware(request)

    assert view.calls == 2
    assert IdempotencyRecord.objects.count() == 0


@pytest.mark.django_db
def test_five_hundred_responses_are_not_cached() -> None:
    """A 5xx response is transient — retry should re-execute the view."""
    view = _CountingView(status=500, body=b"boom")
    middleware = IdempotencyMiddleware(view)

    middleware(_post(key="fail-once"))
    middleware(_post(key="fail-once"))

    assert view.calls == 2
    assert IdempotencyRecord.objects.filter(key="fail-once").count() == 0


@pytest.mark.django_db
def test_four_hundred_responses_are_cached() -> None:
    """A 400 validation failure is deterministic and gets replayed."""
    view = _CountingView(status=400, body=b"bad")
    middleware = IdempotencyMiddleware(view)

    middleware(_post(key="always-bad"))
    middleware(_post(key="always-bad"))

    assert view.calls == 1
    assert IdempotencyRecord.objects.filter(key="always-bad").count() == 1


@pytest.mark.django_db
def test_malformed_key_is_not_cached(caplog: pytest.LogCaptureFixture) -> None:
    """Overlong / non-printable keys log a warning and fall through."""
    view = _CountingView()
    middleware = IdempotencyMiddleware(view)

    with caplog.at_level(logging.WARNING, logger="core.idempotency"):
        middleware(_post(key="x" * 500))
        middleware(_post(key="\x00\x01\x02"))

    assert view.calls == 2
    assert IdempotencyRecord.objects.count() == 0
    assert any("pwa.idempotency.invalid" in record.message for record in caplog.records)


@pytest.mark.django_db
def test_ttl_matches_setting() -> None:
    """The recorded expiry is exactly ``IDEMPOTENCY_RECORD_TTL_SECONDS`` ahead."""
    view = _CountingView()
    middleware = IdempotencyMiddleware(view)

    before = timezone.now()
    middleware(_post(key="ttl-check"))
    after = timezone.now()

    record = IdempotencyRecord.objects.get(key="ttl-check")
    expected_min = before + timedelta(seconds=IDEMPOTENCY_RECORD_TTL_SECONDS)
    expected_max = after + timedelta(seconds=IDEMPOTENCY_RECORD_TTL_SECONDS)
    assert expected_min <= record.expires_at <= expected_max


@pytest.mark.django_db
def test_concurrent_race_swallows_integrity_error() -> None:
    """A concurrent duplicate insert is logged and does not propagate."""
    view = _CountingView()
    middleware = IdempotencyMiddleware(view)

    # Seed a live row so the record() call inside the middleware collides.
    # principal/body_hash are left at their "" defaults — get_live is patched
    # to return None below, so the fingerprint comparison is never reached and
    # the seeded values are irrelevant to what this test exercises.
    IdempotencyRecord.objects.create(
        key="racy",
        method="POST",
        path="/",
        response_status=200,
        response_body=b"first",
        response_content_type="text/plain",
        expires_at=timezone.now() + timedelta(hours=24),
    )

    # Force get_live() to return None so the middleware attempts to record
    # a second row, colliding on the unique key.
    with patch(
        "core.models.IdempotencyRecordManager.get_live",
        return_value=None,
    ):
        response = middleware(_post(key="racy"))

    # View runs, response is returned, but no second row is written.
    assert view.calls == 1
    assert response.status_code == 200
    assert IdempotencyRecord.objects.filter(key="racy").count() == 1


# ---------------------------------------------------------------------------
# Fingerprint tests (SNOW-463) — key reuse across a different path, body, or
# principal is rejected rather than served the wrong cached response.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_identical_fingerprint_replays_as_before() -> None:
    """Same key, method, path, principal, and body → cache hit as before."""
    view = _CountingView(status=201, body=b"created")
    middleware = IdempotencyMiddleware(view)
    user = UserFactory.create()

    first = middleware(
        _post(path="/account/", key="fp-match", body=b"payload", user=user)
    )
    second = middleware(
        _post(path="/account/", key="fp-match", body=b"payload", user=user)
    )

    assert view.calls == 1
    assert first.status_code == second.status_code == 201
    assert first.content == second.content == b"created"
    assert IdempotencyRecord.objects.filter(key="fp-match").count() == 1


@pytest.mark.django_db
def test_reused_key_different_path_returns_409() -> None:
    """Same key, different path → 409, view not re-run, row untouched."""
    view = _CountingView(status=201, body=b"created")
    middleware = IdempotencyMiddleware(view)

    middleware(_post(path="/account/", key="fp-path"))
    response = middleware(_post(path="/favourites/", key="fp-path"))

    assert view.calls == 1
    assert response.status_code == 409
    record = IdempotencyRecord.objects.get(key="fp-path")
    assert record.path == "/account/"


@pytest.mark.django_db
def test_reused_key_different_body_returns_409() -> None:
    """Same key, different body → 409, view not re-run, row untouched."""
    view = _CountingView(status=201, body=b"created")
    middleware = IdempotencyMiddleware(view)

    middleware(_post(key="fp-body", body=b"first-payload"))
    response = middleware(_post(key="fp-body", body=b"second-payload"))

    assert view.calls == 1
    assert response.status_code == 409
    record = IdempotencyRecord.objects.get(key="fp-body")
    assert record.response_body == b"created"


@pytest.mark.django_db
def test_reused_key_different_principal_returns_409() -> None:
    """Same key, different authenticated user → 409, view not re-run."""
    view = _CountingView(status=201, body=b"created")
    middleware = IdempotencyMiddleware(view)
    user_a = UserFactory.create()
    user_b = UserFactory.create()

    middleware(_post(key="fp-principal", body=b"payload", user=user_a))
    response = middleware(_post(key="fp-principal", body=b"payload", user=user_b))

    assert view.calls == 1
    assert response.status_code == 409
    record = IdempotencyRecord.objects.get(key="fp-principal")
    assert record.principal == str(user_a.pk)


@pytest.mark.django_db
def test_fingerprint_mismatch_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A fingerprint mismatch logs ``pwa.idempotency.fingerprint_mismatch``."""
    view = _CountingView()
    middleware = IdempotencyMiddleware(view)

    middleware(_post(path="/account/", key="fp-log"))
    with caplog.at_level(logging.WARNING, logger="core.idempotency"):
        middleware(_post(path="/favourites/", key="fp-log"))

    assert any(
        "pwa.idempotency.fingerprint_mismatch" in record.message
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# End-to-end tests via the Django test client — exercise real views.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_end_to_end_replay_via_test_client() -> None:
    """A POST to a real endpoint is deduplicated by the middleware chain.

    Uses ``/account/`` which returns an HTML fragment for invalid input.
    Two POSTs with the same Idempotency-Key must produce exactly one
    IdempotencyRecord row and matching response bodies.
    """
    client = Client()

    first = client.post(
        "/account/",
        data={"region_id": ""},  # invalid — validation branch
        HTTP_HX_REQUEST="true",
        HTTP_IDEMPOTENCY_KEY="e2e-test-key",
    )
    second = client.post(
        "/account/",
        data={"region_id": ""},
        HTTP_HX_REQUEST="true",
        HTTP_IDEMPOTENCY_KEY="e2e-test-key",
    )

    assert first.status_code == second.status_code
    assert first.content == second.content
    assert IdempotencyRecord.objects.filter(key="e2e-test-key").count() == 1
