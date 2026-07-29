"""
apps/core/models.py — Shared abstract and concrete Django models.

Holds ``BaseModel`` so concrete-model apps (``regions``, ``bulletins``,
``accounts``, …) share a single source of truth for the standard
fields without depending on each other.

Also provides:

* ``RequestLog`` — a concrete model that captures request context
  (IP, geo, locale, UA, referer, session) at explicit inflection
  points (sign-up, sign-in, subscribe, share-click). Other domain
  models attach to it via FK so historical geo/language data is
  available for all downstream analytical work.
* ``IdempotencyRecord`` — cache table backing
  ``apps.core.idempotency.IdempotencyMiddleware`` (SNOW-371). One row per
  ``Idempotency-Key`` seen on a state-changing request, storing the
  original response so a replay within the 24h retention window
  returns the same result without re-invoking the view. The row is
  written *before* the view runs (SNOW-545) — see ``reserve()``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from django.db import IntegrityError, models, transaction
from django.http import HttpResponse
from django.utils import timezone

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)

# How many times ``IdempotencyRecordManager.reserve()`` retries when the
# row it collided with is deleted before it can be read back. Each retry
# needs a competing request to release its reservation in the same
# microsecond window, so anything above a couple of attempts is a
# runaway guard rather than a realistic path.
_RESERVE_ATTEMPTS: int = 3


class BaseModel(models.Model):
    """
    Abstract base model providing standard fields for all concrete models.

    Provides a BigAutoField primary key, a mutable uuid4 field, and
    created_at / updated_at timestamps.
    """

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        abstract = True
        ordering = ["-created_at"]

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return f"{self.__class__.__name__}({self.pk})"


# ---------------------------------------------------------------------------
# RequestLog
# ---------------------------------------------------------------------------


class RequestLogQuerySet(models.QuerySet["RequestLog"]):
    """Custom queryset for RequestLog."""


class RequestLogManager(models.Manager["RequestLog"]):
    """Manager for RequestLog with a from_request factory method."""

    def get_queryset(self) -> RequestLogQuerySet:
        """Return the custom queryset."""
        return RequestLogQuerySet(self.model, using=self._db)

    def from_request(self, request: "HttpRequest") -> "RequestLog":
        """Capture request context and persist a new RequestLog row.

        Extracts IP (leftmost XFF or REMOTE_ADDR), resolves geo fields via
        ``apps.bulletins.services.geoip.geo_lookup``, parses the primary language
        tag from ``Accept-Language``, and saves the row in one call.

        The account FK is set when ``request.user`` is authenticated.
        Anonymous requests (before sign-up / sign-in) leave it null.

        Args:
            request: The current Django HTTP request.

        Returns:
            The newly saved RequestLog instance.

        """
        # Import here to avoid circular dependencies at module load time.
        from apps.bulletins.services.geoip import geo_lookup  # noqa: PLC0415
        from apps.core.http import client_ip  # noqa: PLC0415
        from apps.core.locale import primary_language  # noqa: PLC0415

        ip = client_ip(request)
        geo = geo_lookup(ip) if ip else None

        accept_language: str = request.META.get("HTTP_ACCEPT_LANGUAGE", "")
        language = primary_language(accept_language)

        # Resolve the Account profile for the authenticated user.  A plain
        # staff User (created via createsuperuser) has no Account profile;
        # guard the reverse accessor so anonymous requests and staff-only
        # sessions both yield account=None.
        account = None
        if request.user.is_authenticated:
            from apps.accounts.models import Account  # noqa: PLC0415

            try:
                account = request.user.account
            except Account.DoesNotExist:
                account = None

        sec_purpose_raw = request.headers.get("Sec-Purpose", "")
        sec_purpose = sec_purpose_raw[:64]

        return self.create(
            account=account,
            session_key=(request.session.session_key or ""),
            method=request.method or "",
            path=request.path,
            referer=request.headers.get("Referer", ""),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            ip_address=ip or None,
            country_code=(geo.country if geo else ""),
            subdivision_code=(geo.subdivision if geo else ""),
            city=(geo.city if geo else ""),
            latitude=(geo.latitude if geo else None),
            longitude=(geo.longitude if geo else None),
            accuracy_radius_km=(geo.accuracy_radius_km if geo else None),
            accept_language=accept_language,
            language=language,
            sec_purpose=sec_purpose,
        )


class RequestLog(BaseModel):
    """A snapshot of request context captured at an explicit inflection point.

    Written once at sign-up, sign-in, subscribe, add-region, and share-click.
    Other domain models attach via FK (``acquisition_request``,
    ``subscribed_via``, ``request``) so geo/language data is available for
    downstream analytics without duplicating fields on every model.

    The ``anonymise()`` method nulls PII fields — it is a defensive primitive
    for future data-protection work and is never called automatically.
    """

    account = models.ForeignKey(
        "accounts.Account",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="request_logs",
        help_text=(
            "Authenticated account at request time, if any. "
            "Null for anonymous requests (e.g. sign-up before the row exists)."
        ),
    )
    session_key = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Django session key at request time.",
    )
    method = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text="HTTP method (e.g. GET, POST).",
    )
    path = models.CharField(
        max_length=2048,
        blank=True,
        default="",
        help_text="Request path (without query string).",
    )
    referer = models.TextField(
        blank=True,
        default="",
        help_text="HTTP Referer header.",
    )
    user_agent = models.TextField(
        blank=True,
        default="",
        help_text="HTTP User-Agent header.",
    )

    # IP address
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Client IP (leftmost XFF or REMOTE_ADDR). Null when indeterminate.",
    )

    # Geo fields from MaxMind GeoLite2-City
    country_code = models.CharField(
        max_length=2,
        blank=True,
        default="",
        db_index=True,
        help_text=(
            "ISO 3166-1 alpha-2 country code (e.g. 'CH'). Empty on lookup failure."
        ),
    )
    subdivision_code = models.CharField(
        max_length=10,
        blank=True,
        default="",
        help_text=(
            "ISO 3166-2 subdivision code without the country prefix (e.g. 'VS'). "
            "Empty when not in the database."
        ),
    )
    city = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="City name in English. Empty when not in the database.",
    )
    latitude = models.FloatField(
        null=True,
        blank=True,
        help_text="WGS-84 latitude. Null when not in the database.",
    )
    longitude = models.FloatField(
        null=True,
        blank=True,
        help_text="WGS-84 longitude. Null when not in the database.",
    )
    accuracy_radius_km = models.IntegerField(
        null=True,
        blank=True,
        help_text=(
            "MaxMind accuracy radius in kilometres. Null when not in the database."
        ),
    )

    # Misc request headers
    sec_purpose = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Sec-Purpose header — non-empty for prefetch/prerender requests.",
    )

    # Locale fields
    accept_language = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Raw HTTP Accept-Language header value.",
    )
    language = models.CharField(
        max_length=8,
        blank=True,
        default="",
        help_text=(
            "Primary language subtag parsed from Accept-Language "
            "(e.g. 'en', 'de', 'fr'). Empty when header is absent or unparseable."
        ),
    )

    objects = RequestLogManager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]

    def to_string(self) -> str:
        """Return a concise human-readable description of this log entry.

        Format: ``"{method} {path} from {ip} ({country_code})"``
        """
        return f"{self.method} {self.path} from {self.ip_address} ({self.country_code})"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()

    def anonymise(self) -> None:
        """Null PII fields in place and save the row.

        Clears ``ip_address``, ``session_key``, ``user_agent``, ``referer``,
        ``latitude``, and ``longitude``. Country, subdivision, and city are
        retained as they are not individually identifying.

        This is a defensive primitive for future data-protection work.
        It is never called automatically.
        """
        self.ip_address = None
        self.session_key = ""
        self.user_agent = ""
        self.referer = ""
        self.latitude = None
        self.longitude = None
        self.save(
            update_fields=[
                "ip_address",
                "session_key",
                "user_agent",
                "referer",
                "latitude",
                "longitude",
            ]
        )


# ---------------------------------------------------------------------------
# IdempotencyRecord (SNOW-371 — spec §5.5 / §12.3)
# ---------------------------------------------------------------------------


class IdempotencyRecordQuerySet(models.QuerySet["IdempotencyRecord"]):
    """Custom queryset for IdempotencyRecord."""

    def live(self) -> "IdempotencyRecordQuerySet":
        """Return only records whose retention window has not expired."""
        return self.filter(expires_at__gt=timezone.now())

    def completed(self) -> "IdempotencyRecordQuerySet":
        """Return only records holding a captured response."""
        return self.filter(status=IdempotencyRecord.STATUS.COMPLETED)

    def reserved(self) -> "IdempotencyRecordQuerySet":
        """Return only in-flight reservations with no response yet."""
        return self.filter(status=IdempotencyRecord.STATUS.RESERVED)


class IdempotencyRecordManager(models.Manager["IdempotencyRecord"]):
    """Manager for IdempotencyRecord with the ``reserve()`` primitive."""

    def get_queryset(self) -> IdempotencyRecordQuerySet:
        """Return the custom queryset."""
        return IdempotencyRecordQuerySet(self.model, using=self._db)

    def live(self) -> IdempotencyRecordQuerySet:
        """Shortcut for ``get_queryset().live()``."""
        return self.get_queryset().live()

    def get_live(self, key: str) -> "IdempotencyRecord | None":
        """Return the unexpired record for ``key`` or None if missing.

        A single indexed lookup — ``key`` is unique so ``get()`` is safe.
        The row may be either ``RESERVED`` (a concurrent request is
        running the view right now) or ``COMPLETED`` (a response is
        cached and replayable); callers must check ``status``.
        """
        try:
            return self.live().get(key=key)
        except self.model.DoesNotExist:
            return None

    def reserve(
        self,
        *,
        key: str,
        method: str,
        path: str,
        principal: str,
        body_hash: str,
        ttl: timedelta,
    ) -> tuple["IdempotencyRecord | None", bool]:
        """Claim ``key`` for execution, or report who already holds it.

        The at-most-once guarantee depends on this claim happening
        *before* the view runs (SNOW-545). Three cases are handled as one
        atomic operation so there is no read-then-write gap:

        * **Fresh key** — an ``INSERT`` succeeds and the caller owns the
          reservation.
        * **Expired key** — a conditional ``UPDATE ... WHERE expires_at
          <= now`` takes the row over, resetting the fingerprint,
          clearing the stale response, and renewing ``expires_at``. Only
          one of several concurrent claimants can win: the losers
          re-evaluate the predicate against the already-renewed row and
          match zero rows.
        * **Live key** — somebody else owns it. The existing row is
          returned unchanged.

        Args:
            key: The Idempotency-Key header value from the request.
            method: HTTP method (POST/PATCH/PUT/DELETE).
            path: Request path — used both diagnostically and as part of
                the replay fingerprint (see ``matches_fingerprint``).
            principal: server-derived identity of the requester, or
                ``""`` for anonymous.
            body_hash: sha256 hex digest of the raw request body.
            ttl: Retention window applied to the reservation.

        Returns:
            ``(record, claimed)``. When ``claimed`` is True the caller
            owns the reservation and must finish it with ``complete()``
            or drop it with ``release()``. When False, ``record`` is the
            row held by whoever got there first — or ``None`` in the
            pathological case where a competing request kept releasing
            the row out from under every attempt, which the caller must
            treat as a failed claim rather than permission to run.

        """
        defaults = {
            "method": method,
            "path": path[:2048],
            "principal": principal,
            "body_hash": body_hash,
        }

        for _ in range(_RESERVE_ATTEMPTS):
            now = timezone.now()

            # A savepoint isolates the INSERT so a unique-key collision
            # with a concurrent duplicate request doesn't poison the outer
            # request-level transaction.
            try:
                with transaction.atomic(using=self._db):
                    return (
                        self.create(
                            key=key,
                            status=self.model.STATUS.RESERVED,
                            response_status=None,
                            response_body=b"",
                            response_content_type="",
                            expires_at=now + ttl,
                            **defaults,
                        ),
                        True,
                    )
            except IntegrityError:
                pass

            claimed = self.filter(key=key, expires_at__lte=now).update(
                status=self.model.STATUS.RESERVED,
                response_status=None,
                response_body=b"",
                response_content_type="",
                expires_at=now + ttl,
                updated_at=now,
                **defaults,
            )

            try:
                return self.get(key=key), bool(claimed)
            except self.model.DoesNotExist:
                # The holder released its reservation (5xx, streaming, or
                # an exception) between our INSERT collision and this
                # read. The key is free again — retry the INSERT.
                continue

        logger.warning(
            "pwa.idempotency.reserve_exhausted attempts=%d", _RESERVE_ATTEMPTS
        )
        return None, False


class IdempotencyRecord(BaseModel):
    """One cached response per ``Idempotency-Key`` value.

    Written by ``apps.core.idempotency.IdempotencyMiddleware`` in two steps
    (SNOW-545): a ``RESERVED`` row is claimed *before* the view runs, and
    is promoted to ``COMPLETED`` once a cacheable response comes back. A
    replay of the same key within the retention window is served verbatim
    from a ``COMPLETED`` row without re-invoking the view — so the PWA
    mutation queue can retry after a connectivity blip without
    duplicating side effects.

    Reserving first is what makes the at-most-once guarantee hold under
    concurrency: a second request arriving while the first is still in
    the view cannot claim the key, so it never reaches the mutation.

    Rows are queryable via ``objects.live()``; expired rows are
    intentionally left in place for now — a purge job is a follow-up.
    """

    class STATUS(models.TextChoices):
        """Lifecycle of a reservation."""

        RESERVED = "RESERVED", "Reserved"
        COMPLETED = "COMPLETED", "Completed"

    key = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        help_text=(
            "The client-supplied Idempotency-Key header value. Opaque to "
            "the server; typically a uuid4. Unique — a replay hits the "
            "same row."
        ),
    )
    method = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text="HTTP method of the original request (POST/PATCH/PUT/DELETE).",
    )
    path = models.CharField(
        max_length=2048,
        blank=True,
        default="",
        help_text="Request path of the original request, for diagnostic use.",
    )
    principal = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "str(request.user.pk) of the original requester, or '' for an "
            "anonymous request. Server-derived — never taken from client "
            "input — so it cannot be spoofed. Part of the replay fingerprint."
        ),
    )
    body_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "sha256 hex digest of the original request's raw body. Part of "
            "the replay fingerprint."
        ),
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS.choices,
        default=STATUS.RESERVED,
        db_index=True,
        help_text=(
            "RESERVED while the view is executing, COMPLETED once a "
            "cacheable response has been captured. Only COMPLETED rows "
            "are replayable."
        ),
    )
    response_status = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            "HTTP status code of the cached response, or NULL while the "
            "row is still a RESERVED placeholder."
        ),
    )
    response_body = models.BinaryField(
        default=b"",
        help_text="Raw response body bytes, replayed verbatim on cache hit.",
    )
    response_content_type = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Content-Type header of the cached response.",
    )
    expires_at = models.DateTimeField(
        db_index=True,
        help_text=(
            "When the cached response stops being served. After this "
            "timestamp a replay with the same key re-executes the view."
        ),
    )

    objects = IdempotencyRecordManager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["expires_at"]),
        ]

    def to_string(self) -> str:
        """Return a concise human-readable description of this record."""
        redacted = self.key[:8] + "…" if len(self.key) > 8 else self.key
        return f"{self.method} {self.path} key={redacted} status={self.response_status}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()

    def is_live(self) -> bool:
        """Return True when the record has not yet expired."""
        return self.expires_at > timezone.now()

    def is_completed(self) -> bool:
        """Return True when a response has been captured and is replayable."""
        return self.status == self.STATUS.COMPLETED

    def complete(self, *, response: HttpResponse, ttl: timedelta) -> None:
        """Promote this reservation to a replayable cached response.

        Called by the middleware once the view it reserved for has
        returned something cacheable. The retention window is measured
        from completion — the same semantics the pre-SNOW-545 single-step
        write had, where the row was only created after the view ran.

        Args:
            response: The completed response to cache. Non-streaming
                only; the middleware filters streaming responses out
                upstream.
            ttl: Retention window applied from now.

        """
        content_type: str = str(response.get("Content-Type", ""))
        self.status = self.STATUS.COMPLETED
        self.response_status = response.status_code
        self.response_body = bytes(response.content)
        self.response_content_type = content_type[:255]
        self.expires_at = timezone.now() + ttl
        self.save(
            update_fields=[
                "status",
                "response_status",
                "response_body",
                "response_content_type",
                "expires_at",
                "updated_at",
            ]
        )

    def release(self) -> None:
        """Drop this reservation so the key is immediately reclaimable.

        Used when the reserved view produced nothing worth caching — a
        5xx, a streaming response, or an unhandled exception. Deleting
        rather than expiring keeps the "uncacheable responses leave no
        trace" property the 5xx exemption has always had. Guarded on
        ``status`` so a row that somehow reached COMPLETED is never
        removed by a late release.
        """
        type(self).objects.filter(pk=self.pk, status=self.STATUS.RESERVED).delete()

    def matches_fingerprint(
        self,
        *,
        method: str,
        path: str,
        principal: str,
        body_hash: str,
    ) -> bool:
        """Return True when the passed fingerprint matches this record.

        Compares ``method``, ``path``, ``principal``, and ``body_hash``
        against the values stored on this row. The middleware calls this
        on a cache hit to distinguish a
        legitimate replay (same request, retried) from a reused key
        (different method, path, requester, or payload) — the latter
        must be rejected rather than served the wrong cached response.
        """
        return (
            self.method == method
            and self.path == path
            and self.principal == principal
            and self.body_hash == body_hash
        )

    def build_response(self) -> HttpResponse:
        """Return a fresh HttpResponse reconstructed from the cached row.

        The response carries the original status, Content-Type, and body.
        Other headers (Set-Cookie, cache headers, HX-Redirect…) are NOT
        replayed — those are typically session-scoped or one-shot and
        replaying them can produce surprises. A cached POST that logged
        the user in the first time will not re-log them in on a replay.
        The client's mutation queue reconciles state via a subsequent
        GET, so this is the safer default.

        Raises:
            ValueError: If called on a row with no captured response — a
                RESERVED row has nothing to replay, and the middleware
                must answer 409 rather than invent a status code.

        """
        if self.response_status is None:
            raise ValueError(
                f"Cannot build a response from a {self.status} record — "
                "no response has been captured for this key yet."
            )
        body: bytes = bytes(self.response_body)
        response = HttpResponse(
            content=body,
            status=int(self.response_status),
            content_type=self.response_content_type or None,
        )
        return response
