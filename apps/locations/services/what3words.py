"""
apps/locations/services/what3words.py — three word addresses for a coordinate.

Contains two functions:

  convert_to_3wa(latitude, longitude, base_url=None)
      Calls the what3words ``convert-to-3wa`` endpoint for one lat/lon pair
      and returns the address for the 3m square it falls in, e.g.
      ``"filled.count.soap"``.

  fill_what3words(location)
      Returns a ``Location``'s cached address, converting and caching it
      first if there is nothing fresh to return.

Modelled on ``apps.locations.services.elevation`` — module-level
``REQUEST_TIMEOUT``, plain ``requests.get``, a ``base_url`` override so
tests can point elsewhere — with ONE DELIBERATE DIVERGENCE: nothing here
raises. ``fetch_elevation`` lets an HTTP error bubble because a management
command calls it and a failed batch must exit non-zero. These two sit on a
PAGE RENDER, where the only useful answer to a failure is "no address" —
the trip page falls back to the coordinate pair it printed before SNOW-840,
and a what3words outage must not take a trip page down with it.

**The key travels in the ``X-Api-Key`` header**, not as a query parameter.
The API accepts both; a header cannot end up in an access log, a
``requests`` debug line or a proxy's URL capture.

**Language is hardcoded ``en``.** what3words publishes the same square in
many languages, and each is a different address. A locale-varying meeting
point would show two people on the same trip different words for the same
place — and then one of them would read theirs down a phone to the other.
One trip, one address; the language of the plan is not the language of the
reader's browser.

**Cost and licence.** ``convert-to-3wa`` left the free plan in November
2024, so every call is billed against a paid plan (Basic: 1,000/month).
That is why ``fill_what3words`` converts a square once per month rather
than once per view, and why an empty ``WHAT3WORDS_API_KEY`` makes no
request at all instead of a call that 401s. The 30-day ceiling on the cache
is the licence's, not a tuning choice — see ``Location.three_word_address``
and docs/decisions/what3words-cache-expires-at-thirty-days.md.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone

from apps.locations.models import Location

logger = logging.getLogger(__name__)

# Deliberately shorter than elevation's 30 seconds. That one runs in a
# management command where waiting is free; this one runs while a page
# render is blocked on it, and a meeting point nobody can read is a far
# better outcome than a trip page that hangs.
REQUEST_TIMEOUT = 5  # seconds


def convert_to_3wa(
    latitude: float,
    longitude: float,
    base_url: str | None = None,
) -> str | None:
    """Convert one lat/lon pair to its three word address.

    Calls ``GET {base}/convert-to-3wa`` with the coordinate and
    ``language=en``, and returns the ``words`` value from the response —
    ``"filled.count.soap"``, without the ``///`` prefix, which is
    presentation and belongs to the template.

    NEVER RAISES. Every failure — no key configured, a timeout, a refused
    connection, a 4xx quota or key error, a body that does not carry
    ``words`` — returns None, because the caller is rendering a page and
    has a coordinate pair to fall back on.

    Args:
        latitude: Latitude in degrees.
        longitude: Longitude in degrees.
        base_url: When set, overrides the configured host as the endpoint
            base; the request goes to ``f"{base_url}/convert-to-3wa"``.
            Defaults to None, which uses ``settings.WHAT3WORDS_API_BASE_URL``.

    Returns:
        The three word address, or None if it could not be obtained.

    """
    api_key: str = settings.WHAT3WORDS_API_KEY
    if not api_key:
        # Not an error, and not logged as one: an environment with no
        # subscription is a supported state, and this runs on every trip
        # page render with the flag on, so a warning here would be a log
        # flood describing a deliberate configuration.
        logger.debug("what3words: no API key configured, skipping conversion")
        return None

    url = f"{base_url or settings.WHAT3WORDS_API_BASE_URL}/convert-to-3wa"
    params = {
        "coordinates": f"{latitude},{longitude}",
        "language": "en",
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers={"X-Api-Key": api_key},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        # Covers the timeout, DNS and connection-refused cases together.
        # ``exception`` rather than ``warning`` so the traceback survives:
        # this is the branch that fires when the upstream is down, and
        # knowing which failure it was is the whole diagnosis.
        logger.exception(
            "what3words: request failed for latitude=%s longitude=%s",
            latitude,
            longitude,
        )
        return None

    if not response.ok:
        logger.warning(
            "what3words: %s for latitude=%s longitude=%s code=%s",
            response.status_code,
            latitude,
            longitude,
            _error_code(response),
        )
        return None

    try:
        data: dict[str, Any] = response.json()
    except ValueError:
        logger.warning("what3words: response body was not JSON (url=%s)", url)
        return None

    words = data.get("words")
    if not words or not isinstance(words, str):
        logger.warning(
            "what3words: no words in the response for latitude=%s longitude=%s",
            latitude,
            longitude,
        )
        return None

    return words


def _error_code(response: requests.Response) -> str:
    """Return the ``error.code`` a what3words failure body carries.

    A failure answers ``{"error": {"code": "...", "message": "..."}}``, and
    the code is the actionable half — ``InvalidKey`` and ``QuotaExceeded``
    need different people to do different things, where the status code
    alone says only "4xx". Best-effort: a body that is not the documented
    shape yields ``"unknown"`` rather than a second failure inside the
    failure handler.

    Args:
        response: The non-2xx response.

    Returns:
        The error code, or ``"unknown"``.

    """
    try:
        error = response.json().get("error") or {}
        return str(error.get("code", "unknown"))
    except ValueError, AttributeError:
        return "unknown"


def fill_what3words(location: Location) -> str | None:
    """Return a location's three word address, converting it if need be.

    The read path's entry point. Returns the cached address when
    ``Location.three_word_address`` still has one — which is the common
    case, since the cache lasts as long as the licence allows — and
    otherwise spends one conversion and writes the result back.

    IDEMPOTENT, and safe to run concurrently. Two requests for the same
    trip that both find the cache empty will both convert and both write;
    they write the same words, and the second save is a no-op in effect.
    Locking to save a duplicate call would cost more than the call.

    Writes with ``update_fields`` so a fill triggered by a GET touches the
    two cache columns and nothing else — it cannot clobber a concurrent
    edit of the location's coordinates.

    Args:
        location: The location to resolve. Saved in place when a
            conversion succeeds.

    Returns:
        The three word address, or None when there is no fresh cache and
        the conversion did not succeed.

    """
    cached = location.three_word_address
    if cached is not None:
        return cached

    words = convert_to_3wa(location.latitude, location.longitude)
    if words is None:
        # Deliberately no negative caching. A failure is nearly always the
        # upstream or the key rather than the square, so stamping "we
        # tried" would suppress the retry that fixes itself.
        return None

    location.what3words = words
    location.what3words_fetched_at = timezone.now()
    location.save(update_fields=["what3words", "what3words_fetched_at", "updated_at"])
    return words
