"""
core/models.py — Shared abstract and concrete Django models.

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
  ``core.idempotency.IdempotencyMiddleware`` (SNOW-371). One row per
  ``Idempotency-Key`` seen on a state-changing request, storing the
  original response so a replay within the 24h retention window
  returns the same result without re-invoking the view.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from django.db import models
from django.http import HttpResponse
from django.utils import timezone

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)


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
        ``bulletins.services.geoip.geo_lookup``, parses the primary language
        tag from ``Accept-Language``, and saves the row in one call.

        The account FK is set when ``request.user`` is authenticated.
        Anonymous requests (before sign-up / sign-in) leave it null.

        Args:
            request: The current Django HTTP request.

        Returns:
            The newly saved RequestLog instance.

        """
        # Import here to avoid circular dependencies at module load time.
        from bulletins.services.geoip import geo_lookup  # noqa: PLC0415
        from core.http import client_ip  # noqa: PLC0415
        from core.locale import primary_language  # noqa: PLC0415

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
            from accounts.models import Account  # noqa: PLC0415

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


class IdempotencyRecordManager(models.Manager["IdempotencyRecord"]):
    """Manager for IdempotencyRecord with the ``record()`` factory."""

    def get_queryset(self) -> IdempotencyRecordQuerySet:
        """Return the custom queryset."""
        return IdempotencyRecordQuerySet(self.model, using=self._db)

    def live(self) -> IdempotencyRecordQuerySet:
        """Shortcut for ``get_queryset().live()``."""
        return self.get_queryset().live()

    def get_live(self, key: str) -> "IdempotencyRecord | None":
        """Return the unexpired record for ``key`` or None if missing.

        A single indexed lookup — ``key`` is unique so ``get()`` is safe.
        Callers use the return value to short-circuit and replay the
        original response.
        """
        try:
            return self.live().get(key=key)
        except self.model.DoesNotExist:
            return None

    def record(
        self,
        *,
        key: str,
        method: str,
        path: str,
        principal: str,
        body_hash: str,
        response: HttpResponse,
        ttl: timedelta,
    ) -> "IdempotencyRecord":
        """Persist a fresh record capturing the completed response.

        Args:
            key: The Idempotency-Key header value from the request.
            method: HTTP method (POST/PATCH/PUT/DELETE).
            path: Request path — used both diagnostically and as part of
                the replay fingerprint (see ``matches_fingerprint``).
            principal: ``str(request.user.pk)`` for an authenticated
                requester, or ``""`` for anonymous.
            body_hash: sha256 hex digest of the raw request body.
            response: The completed response to cache. Non-streaming
                only; the middleware filters streaming responses out
                upstream.
            ttl: Retention window. The row is queryable via ``live()``
                until ``created_at + ttl`` passes.

        Returns:
            The newly created row.

        """
        body: bytes = bytes(response.content)
        content_type: str = str(response.get("Content-Type", ""))
        return self.create(
            key=key,
            method=method,
            path=path[:2048],
            principal=principal,
            body_hash=body_hash,
            response_status=response.status_code,
            response_body=body,
            response_content_type=content_type[:255],
            expires_at=timezone.now() + ttl,
        )


class IdempotencyRecord(BaseModel):
    """One cached response per ``Idempotency-Key`` value.

    Populated by ``core.idempotency.IdempotencyMiddleware`` when a
    state-changing request completes with a non-5xx response. A replay
    of the same key within the retention window is served verbatim from
    this row without re-invoking the view — so the PWA mutation queue
    can retry after a connectivity blip without duplicating side effects.

    Rows are queryable via ``objects.live()``; expired rows are
    intentionally left in place for now — a purge job is a follow-up.
    """

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
    response_status = models.PositiveSmallIntegerField(
        help_text="HTTP status code of the cached response.",
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
        """
        body: bytes = bytes(self.response_body)
        response = HttpResponse(
            content=body,
            status=int(self.response_status),
            content_type=self.response_content_type or None,
        )
        return response
