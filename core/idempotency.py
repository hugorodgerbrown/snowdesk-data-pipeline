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

The retention window is 24h in ``IDEMPOTENCY_RECORD_TTL_SECONDS``.
Cleanup of expired rows is not automated in this ticket — the table is
small (one row per unique key) and can be purged by a follow-up job.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.urls import Resolver404, resolve

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
        """Short-circuit on cache hit; otherwise run the view and store."""
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

        cached = IdempotencyRecord.objects.get_live(key)
        if cached is not None:
            logger.info(
                "pwa.idempotency.hit method=%s view=%s key=%s status=%d",
                request.method,
                _view_name(request),
                _redact_key(key),
                cached.response_status,
            )
            return cached.build_response()

        response = self.get_response(request)

        if not _is_cacheable(response):
            return response

        try:
            # A savepoint isolates the INSERT so a unique-key collision
            # (concurrent duplicate request) doesn't poison the outer
            # request-level transaction. Without this, a raced IntegrityError
            # would leave the transaction in an aborted state and every
            # subsequent query in this request would fail.
            with transaction.atomic():
                IdempotencyRecord.objects.record(
                    key=key,
                    method=request.method or "",
                    path=request.path,
                    response=response,
                    ttl=timedelta(seconds=IDEMPOTENCY_RECORD_TTL_SECONDS),
                )
        except IntegrityError:
            # A concurrent request stored the same key first; the cached
            # response is authoritative — we don't need to overwrite it.
            logger.info(
                "pwa.idempotency.race method=%s view=%s key=%s",
                request.method,
                _view_name(request),
                _redact_key(key),
            )

        return response


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
