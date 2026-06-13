"""
core/models.py — Shared abstract and concrete Django models.

Holds ``BaseModel`` so concrete-model apps (``regions``, ``bulletins``,
``subscriptions``, …) share a single source of truth for the standard
fields without depending on each other.

Also provides ``RequestLog`` — a concrete model that captures request
context (IP, geo, locale, UA, referer, session) at explicit inflection
points (sign-up, sign-in, subscribe, share-click). Other domain models
attach to it via FK so historical geo/language data is available for
all downstream analytical work.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from django.db import models

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

        The subscriber FK is set when ``request.user`` is authenticated.
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

        # Resolve the Subscriber profile for the authenticated user.  A plain
        # staff User (created via createsuperuser) has no Subscriber profile;
        # guard the reverse accessor so anonymous requests and staff-only
        # sessions both yield subscriber=None.
        subscriber = None
        if request.user.is_authenticated:
            from subscriptions.models import Subscriber  # noqa: PLC0415

            try:
                subscriber = request.user.subscriber
            except Subscriber.DoesNotExist:
                subscriber = None

        sec_purpose_raw = request.headers.get("Sec-Purpose", "")
        sec_purpose = sec_purpose_raw[:64]

        return self.create(
            subscriber=subscriber,
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

    subscriber = models.ForeignKey(
        "subscriptions.Subscriber",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="request_logs",
        help_text=(
            "Authenticated subscriber at request time, if any. "
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
