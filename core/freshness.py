"""
core/freshness.py — Data-freshness response headers for the PWA client.

Ships the ``X-Data-Generated-At`` / ``X-Data-Max-Age`` / ``X-Data-Unsafe-After``
contract from the offline-first spec (§5.4). The PWA client (SNOW-377)
reads these off every data-bearing API response to drive the freshness
indicator on stale-sensitive views — green dot within max-age, yellow
between max-age and unsafe-after, red (view blocked) past unsafe-after.

Snowdesk's ratings and bulletin payloads are safety-critical, so
``X-Data-Unsafe-After`` is emitted by default. The spec (§5.4) says to
omit that header for non-safety data — the ``unsafe_after=None`` argument
covers that case.

The default max-age is 24h (one bulletin cadence) and unsafe-after is
48h — after two missed cadences the client should refuse to show a
stale rating without an explicit refresh.
"""

from __future__ import annotations

from datetime import datetime

from django.http import HttpResponse

DEFAULT_MAX_AGE_SECONDS: int = 24 * 60 * 60
DEFAULT_UNSAFE_AFTER_SECONDS: int = 48 * 60 * 60

GENERATED_AT_HEADER = "X-Data-Generated-At"
MAX_AGE_HEADER = "X-Data-Max-Age"
UNSAFE_AFTER_HEADER = "X-Data-Unsafe-After"


def apply_freshness_headers(
    response: HttpResponse,
    generated_at: datetime,
    max_age: int = DEFAULT_MAX_AGE_SECONDS,
    unsafe_after: int | None = DEFAULT_UNSAFE_AFTER_SECONDS,
) -> HttpResponse:
    """
    Stamp the three freshness headers on ``response`` and return it.

    Args:
        response: The outgoing ``HttpResponse`` to mutate in place.
        generated_at: When the source data was produced. Must be
            timezone-aware — a naive datetime is a bug the client can't
            recover from because the timestamp is compared against
            ``navigator.now()`` in UTC.
        max_age: Seconds after which the client marks the data
            "stale but usable" (yellow dot).
        unsafe_after: Seconds after which the client refuses to display
            the data operationally (red — view blocked). Pass ``None`` to
            omit the header, which is correct for non-safety data.

    Returns:
        The same response, for call-chaining.

    Raises:
        ValueError: If ``generated_at`` is naive.

    """
    if generated_at.tzinfo is None:
        raise ValueError(
            "generated_at must be timezone-aware — the PWA client compares "
            "against UTC and a naive timestamp will misclassify freshness."
        )
    response[GENERATED_AT_HEADER] = generated_at.isoformat(timespec="seconds")
    response[MAX_AGE_HEADER] = str(int(max_age))
    if unsafe_after is not None:
        response[UNSAFE_AFTER_HEADER] = str(int(unsafe_after))
    return response
