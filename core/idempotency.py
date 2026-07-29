"""
core/idempotency.py — Idempotency-Key request deduplication (SNOW-371).

Implements the server half of spec §5.5 / §12.3: state-changing requests
(POST/PATCH/PUT/DELETE) that carry an ``Idempotency-Key`` header are
deduplicated by that key for the retention window (24h by default). A
replay of the same key within the window returns the cached response
verbatim without re-invoking the view — so the PWA mutation queue can
retry after connectivity blips without duplicating side effects.

Design notes
------------
* Middleware, not decorator — attaching to every state-changing endpoint
  by opt-in decoration is a footgun for new views. Middleware makes the
  guarantee global.
* Reserve, then execute (SNOW-545). The key is claimed with an atomic
  INSERT (or an atomic takeover of an expired row) *before*
  ``get_response`` is called, and the reservation is promoted to a
  cached response afterwards. Writing the row only after the view ran
  left a read-then-write gap: two concurrent requests with the same key
  both missed the lookup, both ran the mutation, and the loser's
  ``IntegrityError`` was swallowed after its side effect had committed.
  A request that fails to claim the key never reaches the view — it
  either replays the cached response or gets a ``409``.
* Only 1xx/2xx/3xx/4xx responses are cached. 5xx responses are treated
  as transient and left uncached so a retry re-executes the view.
* Streaming responses are never cached — the body cannot be captured
  once, and callers of streaming endpoints do not need idempotency
  (they're typically GET anyway).
* Missing header on a state-changing request logs
  ``pwa.idempotency.missing`` at INFO so we can flag callers that skip
  the contract. It is not a hard failure — backwards compatibility with
  existing HTMX callers that don't yet mint keys.
* The cached record stores the raw body + status + content type. Reading
  is a single indexed lookup on ``key`` with an ``expires_at`` filter.
* Fingerprint hardening (SNOW-463): a key match alone is not sufficient
  to replay a cached response. The middleware also compares method,
  path, principal (``str(request.user.pk)``, server-derived so it can't
  be spoofed), and a sha256 hash of the raw request body against the
  values stored at cache-write time. A mismatch — reusing a key across a
  different path, body, or user — returns ``409`` instead of serving the
  wrong cached response or running the view again.

The retention window is 24h in ``IDEMPOTENCY_RECORD_TTL_SECONDS``.
Cleanup of expired rows is not automated in this ticket — the table is
small (one row per unique key) and can be purged by a follow-up job.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING

from django.http import HttpRequest, HttpResponse
from django.urls import Resolver404, resolve

if TYPE_CHECKING:
    from core.models import IdempotencyRecord as IdempotencyRecordType

logger = logging.getLogger(__name__)

STATE_CHANGING_METHODS: frozenset[str] = frozenset({"POST", "PATCH", "PUT", "DELETE"})

KEY_HEADER: str = "Idempotency-Key"
_KEY_HEADER_META: str = "HTTP_IDEMPOTENCY_KEY"

# Reasonable upper bound. Spec doesn't pin one, but keeping the header
# short prevents pathological indexes and matches the practical range
# used by uuid4 (36 chars) plus a small prefix.
MAX_KEY_LENGTH: int = 128

# Retention window — one day. Matches spec §5.5 ("default 24h").
IDEMPOTENCY_RECORD_TTL_SECONDS: int = 24 * 60 * 60


class IdempotencyMiddleware:
    """Deduplicate state-changing requests by ``Idempotency-Key`` header.

    Applied to every request via ``MIDDLEWARE``. GETs and HEADs pass
    through untouched. State-changing methods without the header pass
    through but log ``pwa.idempotency.missing``. State-changing methods
    with a valid header check the cache first, and if a cached response
    exists within the retention window, return it verbatim. Otherwise
    the view runs and its response is cached before being returned.
    """

    def __init__(
        self,
        get_response: Callable[[HttpRequest], HttpResponse],
    ) -> None:
        """Bind the next middleware callable."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Reserve the key, then run the view; replay on a lost claim."""
        if request.method not in STATE_CHANGING_METHODS:
            return self.get_response(request)

        raw_key: str = request.META.get(_KEY_HEADER_META, "") or ""
        key: str = raw_key.strip()

        if not key:
            logger.info(
                "pwa.idempotency.missing method=%s view=%s",
                request.method,
                _view_name(request),
            )
            return self.get_response(request)

        if not _is_well_formed_key(key):
            logger.warning(
                "pwa.idempotency.invalid method=%s view=%s len=%d",
                request.method,
                _view_name(request),
                len(key),
            )
            return self.get_response(request)

        # Deferred import — the model lives in core.models which pulls in
        # BaseModel; middleware modules are imported at settings-load time
        # and we want the model import to happen after the app registry
        # is ready.
        from core.models import IdempotencyRecord  # noqa: PLC0415

        principal = _principal(request)
        body_hash = _body_hash(request)
        ttl = timedelta(seconds=IDEMPOTENCY_RECORD_TTL_SECONDS)

        reservation, claimed = IdempotencyRecord.objects.reserve(
            key=key,
            method=request.method or "",
            path=request.path,
            principal=principal,
            body_hash=body_hash,
            ttl=ttl,
        )

        if not claimed or reservation is None:
            return self._resolve_lost_claim(
                request,
                key=key,
                reservation=reservation,
                principal=principal,
                body_hash=body_hash,
            )

        try:
            response = self.get_response(request)
        except Exception:
            # The view blew up, so there is no response worth caching and
            # the mutation may not have committed. Drop the reservation
            # rather than holding the key hostage for the full window.
            reservation.release()
            raise

        if not _is_cacheable(response):
            reservation.release()
            return response

        reservation.complete(response=response, ttl=ttl)
        return response

    def _resolve_lost_claim(
        self,
        request: HttpRequest,
        *,
        key: str,
        reservation: IdempotencyRecordType | None,
        principal: str,
        body_hash: str,
    ) -> HttpResponse:
        """Return the response for a request that failed to claim ``key``.

        Called when another request already owns the key. A ``COMPLETED``
        row replays verbatim (after the SNOW-463 fingerprint check); a
        ``RESERVED`` row means the original request is still in the view,
        so there is nothing to replay yet and the caller gets a ``409``
        to retry with. Either way this request never reaches the view —
        that is the whole point of reserving first.
        """
        if reservation is None:
            # reserve() exhausted its attempts. Failing closed risks a
            # spurious 409; running the view risks a duplicate mutation.
            logger.warning(
                "pwa.idempotency.unclaimable method=%s view=%s key=%s",
                request.method,
                _view_name(request),
                _redact_key(key),
            )
            return HttpResponse(status=409)

        if not reservation.matches_fingerprint(
            method=request.method or "",
            path=request.path,
            principal=principal,
            body_hash=body_hash,
        ):
            logger.warning(
                "pwa.idempotency.fingerprint_mismatch method=%s view=%s key=%s",
                request.method,
                _view_name(request),
                _redact_key(key),
            )
            return HttpResponse(status=409)

        if not reservation.is_completed():
            logger.info(
                "pwa.idempotency.in_flight method=%s view=%s key=%s",
                request.method,
                _view_name(request),
                _redact_key(key),
            )
            return HttpResponse(status=409)

        logger.info(
            "pwa.idempotency.hit method=%s view=%s key=%s status=%d",
            request.method,
            _view_name(request),
            _redact_key(key),
            reservation.response_status,
        )
        # SNOW-381 (spec §16.2): observability dashboards need a
        # PostHog-visible counter for cache-served replays so we can
        # measure how often the queue drain retries the same key.
        # Deferred import — analytics.signals pulls in the analytics
        # app which shouldn't load at middleware-import time.
        from analytics.signals import emit_server_signal  # noqa: PLC0415

        emit_server_signal(
            "pwa.idempotency.replay",
            {
                "view": _view_name(request),
                "status": reservation.response_status,
                # SNOW-384: client_version threaded from the
                # X-Client-Version request header, same convention as
                # every other server-side signal. Client sends this
                # header via static/js/pwa_client_version.js (SNOW-388).
                "client_version": request.headers.get("X-Client-Version", ""),
            },
        )
        return reservation.build_response()


def _is_well_formed_key(key: str) -> bool:
    """Reject overlong or non-ASCII keys before touching the DB.

    Idempotency-Key values are opaque to the server but should be short,
    URL-safe strings (uuid4 is typical). Rejecting garbage cheaply keeps
    the cache table clean and prevents pathological index rows.
    """
    if len(key) > MAX_KEY_LENGTH:
        return False
    if not key.isascii():
        return False
    if not key.isprintable():
        return False
    return True


def _is_cacheable(response: HttpResponse) -> bool:
    """Return True if the response is safe to cache-and-replay.

    Cacheable: any response with a 1xx/2xx/3xx/4xx status. 5xx is treated
    as transient — the client should retry and get a fresh execution.
    Streaming responses cannot be captured once and are never cached.
    """
    if getattr(response, "streaming", False):
        return False
    if response.status_code >= 500:
        return False
    return True


def _redact_key(key: str) -> str:
    """Return a log-safe fragment of an Idempotency-Key.

    Idempotency-Keys are not secrets, but they are user-supplied strings;
    logging only the first eight characters keeps the log noise down
    without losing correlation ability.
    """
    if len(key) <= 8:
        return key
    return f"{key[:8]}…"


def _principal(request: HttpRequest) -> str:
    """Return the server-derived principal fingerprint component.

    ``str(request.user.pk)`` for an authenticated requester, or ``""``
    for anonymous. Read from ``request.user`` (populated by
    ``AuthenticationMiddleware``, which now runs before this middleware)
    rather than any client-supplied value, so it cannot be spoofed.
    Defensive against bare ``HttpRequest`` objects in unit tests, which
    have no ``user`` attribute at all.
    """
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return str(user.pk)
    return ""


def _body_hash(request: HttpRequest) -> str:
    """Return the sha256 hex digest of the raw request body.

    Hashes ``request.body`` verbatim — not canonicalised JSON — because
    the PWA mutation queue replays stored bytes exactly, so a legitimate
    retry is byte-identical and matches. Accessing ``request.body`` here
    caches it on the request object; the view can still read
    ``request.POST`` afterwards since Django re-parses form/multipart
    data from the cached body.
    """
    return hashlib.sha256(request.body).hexdigest()


def _view_name(request: HttpRequest) -> str:
    """Return the resolved view name for ``request`` or ``"unresolved"``.

    Preferred over ``request.path`` in log lines because some URL patterns
    embed sensitive data in the path itself (e.g. the unsubscribe token
    contains a lower-cased email address). Logging the view name gives
    the same diagnostic value — "which endpoint is missing a key?" —
    without leaking PII into log files.
    """
    try:
        return resolve(request.path).view_name or "unresolved"
    except Resolver404:
        return "unresolved"
