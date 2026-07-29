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

And the SNOW-545 reservation contract:

* A duplicate arriving while the first request is still inside the view
  never reaches the view; it gets a ``409`` and replays once the winner
  completes.
* A view that raises releases the key instead of holding it for the
  whole retention window.
* An expired row is *reclaimed* — renewed and overwritten — rather than
  colliding forever with a blind insert.
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
def test_expired_record_is_reclaimed_not_starved() -> None:
    """A post-TTL replay takes the expired row over rather than colliding.

    SNOW-545: ``record()`` used to ``create()`` blindly, so the INSERT
    collided with the still-present expired row and the IntegrityError
    was swallowed. The expired row was never replaced, so *every*
    subsequent replay re-executed the view indefinitely. The reservation
    path must instead reclaim the row — renewing ``expires_at`` and
    replacing the cached response — so the window closes again.
    """
    view = _CountingView(status=201, body=b"created")
    middleware = IdempotencyMiddleware(view)

    middleware(_post(key="expiring"))
    assert view.calls == 1
    original = IdempotencyRecord.objects.get(key="expiring")

    # Fast-forward the row into the past.
    IdempotencyRecord.objects.filter(key="expiring").update(
        expires_at=timezone.now() - timedelta(minutes=1),
    )

    view.body = b"recreated"
    middleware(_post(key="expiring"))
    assert view.calls == 2

    reclaimed = IdempotencyRecord.objects.get(key="expiring")
    assert IdempotencyRecord.objects.filter(key="expiring").count() == 1
    assert reclaimed.pk == original.pk
    assert reclaimed.status == IdempotencyRecord.STATUS.COMPLETED
    assert reclaimed.expires_at > timezone.now()
    assert bytes(reclaimed.response_body) == b"recreated"

    # A third replay inside the *renewed* window hits the cache. Before
    # SNOW-545 this re-ran the view for the rest of time.
    third = middleware(_post(key="expiring"))
    assert view.calls == 2
    assert third.content == b"recreated"


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


# ---------------------------------------------------------------------------
# Reservation tests (SNOW-545) — the key is claimed before the view runs, so a
# duplicate request arriving mid-flight cannot reach the mutation at all.
# ---------------------------------------------------------------------------


class _ReentrantView:
    """View that replays its own request through the middleware mid-flight.

    This is the deterministic stand-in for two concurrent requests: the
    inner call happens *while* the outer one is inside the view, which is
    precisely the window the pre-SNOW-545 read-then-write gap left open.
    Threads would exercise the same code path non-deterministically and
    against SQLite's connection-per-thread semantics.
    """

    def __init__(self, key: str) -> None:
        """Record the key to re-request; initialise counters and capture."""
        self.key = key
        self.calls = 0
        self.middleware: IdempotencyMiddleware | None = None
        self.inner_response: HttpResponse | None = None

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Count the call, fire a duplicate on the first pass, respond 201."""
        self.calls += 1
        if self.calls == 1 and self.middleware is not None:
            self.inner_response = self.middleware(_post(key=self.key))
        return HttpResponse(content=b"created", status=201, content_type="text/plain")


@pytest.mark.django_db
def test_duplicate_request_mid_flight_never_reaches_the_view() -> None:
    """A second request with the same key cannot run the mutation.

    The pre-SNOW-545 middleware read the cache, ran the view, then wrote;
    two requests in that gap both invoked the view and both created a
    side effect, with the loser's IntegrityError swallowed *after* its
    write had committed. Reserving first means the loser is turned away
    before ``get_response`` is ever called.

    The in-flight loser gets a ``409`` rather than the response — there
    is nothing cached to serve yet — and a retry after the winner
    finishes replays the winner's response verbatim.
    """
    view = _ReentrantView(key="racy")
    middleware = IdempotencyMiddleware(view)
    view.middleware = middleware

    outer = middleware(_post(key="racy"))

    assert view.calls == 1, "the duplicate request reached the view"
    assert outer.status_code == 201
    assert view.inner_response is not None
    assert view.inner_response.status_code == 409
    assert IdempotencyRecord.objects.filter(key="racy").count() == 1

    # Once the winner has completed, a retry replays its response.
    retry = middleware(_post(key="racy"))
    assert view.calls == 1
    assert retry.status_code == outer.status_code == 201
    assert retry.content == outer.content == b"created"


@pytest.mark.django_db
def test_view_exception_releases_the_reservation() -> None:
    """A view that raises must not hold the key for the whole window."""

    def _boom(request: HttpRequest) -> HttpResponse:
        raise RuntimeError("view exploded")

    middleware = IdempotencyMiddleware(_boom)

    with pytest.raises(RuntimeError):
        middleware(_post(key="explodes"))

    assert IdempotencyRecord.objects.filter(key="explodes").count() == 0

    # The key is immediately reusable.
    view = _CountingView()
    assert IdempotencyMiddleware(view)(_post(key="explodes")).status_code == 200
    assert view.calls == 1


def _reserve(key: str) -> tuple[IdempotencyRecord | None, bool]:
    """Claim ``key`` with a fixed fingerprint, as the middleware would."""
    return IdempotencyRecord.objects.reserve(
        key=key,
        method="POST",
        path="/account/",
        principal="",
        body_hash="abc",
        ttl=timedelta(hours=24),
    )


@pytest.mark.django_db
def test_reserve_claims_once_for_a_fresh_key() -> None:
    """Only the first ``reserve()`` for a live key wins the claim."""
    first, first_claimed = _reserve("claim-once")
    second, second_claimed = _reserve("claim-once")

    assert first_claimed is True
    assert second_claimed is False
    assert first is not None
    assert second is not None
    assert second.pk == first.pk
    assert second.status == IdempotencyRecord.STATUS.RESERVED
    assert IdempotencyRecord.objects.filter(key="claim-once").count() == 1


@pytest.mark.django_db
def test_reserve_of_an_expired_row_is_exclusive() -> None:
    """Two claimants of the same expired row: exactly one takes it over."""
    record, _ = _reserve("expired-claim")
    assert record is not None
    IdempotencyRecord.objects.filter(pk=record.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1),
    )

    _, first_claimed = _reserve("expired-claim")
    _, second_claimed = _reserve("expired-claim")

    assert first_claimed is True
    assert second_claimed is False


@pytest.mark.django_db
def test_unclaimable_key_returns_409_without_running_the_view() -> None:
    """Reserve exhaustion fails closed rather than risking a duplicate.

    ``reserve()`` returns ``(None, False)`` only if a competitor keeps
    releasing the row between the collision and the read-back. Whatever
    is going on, running the view would risk the exact double-execution
    the reservation exists to prevent.
    """
    view = _CountingView()
    middleware = IdempotencyMiddleware(view)

    with patch(
        "core.models.IdempotencyRecordManager.reserve",
        return_value=(None, False),
    ):
        response = middleware(_post(key="unclaimable"))

    assert view.calls == 0
    assert response.status_code == 409


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
