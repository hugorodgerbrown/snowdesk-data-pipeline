"""
apps/locations/services/what3words.py — three word addresses for a coordinate.

Contains three functions:

  convert_to_3wa(latitude, longitude, base_url=None)
      Calls the what3words ``convert-to-3wa`` endpoint for one lat/lon pair
      and returns the address for the 3m square it falls in, e.g.
      ``"filled.count.soap"``.

  fill_what3words(location)
      Returns a ``Location``'s stored address, converting and storing it
      first if there is not one yet.

  what3words_map_url(words)
      Returns where one address links to on what3words' own map. Lives
      here rather than in a view because two apps render an address
      (SNOW-882).

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

**There is a local fake**, ``WHAT3WORDS_FAKE``, which invents a
deterministic address instead of calling anything. It is scaffolding for
UX work rather than a test double: the endpoint is behind a paid plan, so
without it nobody can see this feature — or review a change to it —
without buying a subscription first. It requires ``DEBUG`` on top of the
setting, because a fabricated meeting point reaching a real group would
send them to a square that does not exist. See ``_fake_address``.

**Cost and licence.** ``convert-to-3wa`` left the free plan in November
2024, so it needs a paid plan (Basic, £7.99/mo) — but it is UNMETERED on
every paid plan and does not draw on the 1,000-a-month allowance, which
belongs to ``convert-to-coordinates`` and which Snowdesk never calls.
Conversions are therefore free at the margin, and the reason to convert
once and store rather than once per view is LATENCY, not cost: the call
below carries a five-second timeout. An empty ``WHAT3WORDS_API_KEY`` still
makes no request at all rather than one that 401s.

An address we derived from our own coordinate may be stored INDEFINITELY;
the 30-day cache ceiling in the terms governs the other direction of
travel. See docs/decisions/what3words-addresses-are-stored-indefinitely.md.
"""

from __future__ import annotations

import hashlib
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

# The vocabulary the local fake draws on. Ordinary English words in the
# register what3words itself uses, so a faked address is indistinguishable
# from a real one AT A GLANCE — which is the point, since the thing being
# judged is how the address sits in the layout. 48 words gives 110,592
# combinations, far past anything a local database will exercise.
_FAKE_WORDS = (
    "amber",
    "anchor",
    "aspen",
    "basin",
    "beacon",
    "bracket",
    "cabin",
    "cairn",
    "cedar",
    "cliff",
    "cobble",
    "cornice",
    "crest",
    "dawn",
    "drift",
    "ember",
    "ferry",
    "flint",
    "gable",
    "glacier",
    "harbour",
    "hazel",
    "hollow",
    "kettle",
    "lantern",
    "ledge",
    "marble",
    "meadow",
    "moraine",
    "nettle",
    "orchard",
    "pewter",
    "pillar",
    "quarry",
    "ridge",
    "saddle",
    "shutter",
    "silver",
    "slate",
    "spruce",
    "summit",
    "thicket",
    "timber",
    "traverse",
    "vault",
    "willow",
    "windward",
    "yarrow",
)

# How precisely a coordinate is pinned before it is hashed. Five decimal
# places is about 1.1m, comfortably inside a 3m square, so the fake is
# stable against the float jitter a pin drag produces while still giving
# two genuinely different places two different addresses.
_FAKE_PRECISION = 5


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
    if settings.WHAT3WORDS_FAKE and settings.DEBUG:
        return _fake_address(latitude, longitude)

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
        #
        # NO COORDINATE IN ANY MESSAGE HERE (SNOW-718). A lat/lon pair is a
        # precise location — somebody's meeting point — and a log is the
        # wrong place for one; ``apps.public.api`` logs the row id for the
        # same reason. This function is handed bare floats and has no id to
        # name instead, so it names none: which square failed is not what
        # the log is for, and ``fill_what3words`` records the row.
        logger.exception("what3words: request failed (url=%s)", url)
        return None

    if not response.ok:
        logger.warning(
            "what3words: %s from the API (code=%s)",
            response.status_code,
            _error_code(response),
        )
        return None

    # The ``.get`` is INSIDE the try, and that is the point of the shape.
    # ``response.json()`` succeeding does not mean the body is an object:
    # a proxy, a WAF or a future API version can answer valid JSON that is
    # a list, a string or null, and ``[].get`` is an AttributeError that
    # would leave this function by the one route its docstring promises
    # does not exist — straight through fill_what3words and into a trip
    # page render, 500ing both surfaces instead of falling back to the
    # coordinates. ``_error_code`` below already guards the same shape.
    try:
        data: Any = response.json()
        words = data.get("words")
    except ValueError, AttributeError:
        logger.warning(
            "what3words: body was not JSON, or not a JSON object (url=%s)", url
        )
        return None

    if not words or not isinstance(words, str):
        logger.warning("what3words: the response carried no usable words")
        return None

    return words


def _fake_address(latitude: float, longitude: float) -> str:
    """Invent a stable three word address for a coordinate.

    LOCAL UX WORK AND DEMOS ONLY. The words are made up and name nowhere;
    reaching this in production would send a group to a square that does
    not exist, which is why ``convert_to_3wa`` requires ``DEBUG`` as well
    as the setting before it calls this.

    It exists because ``convert-to-3wa`` is behind a paid plan, so without
    it nobody can look at this feature — review the layout, exercise the
    flag, judge whether the address reads better than the coordinates —
    without buying a subscription first.

    DETERMINISTIC, via ``blake2b`` over the rounded coordinate rather than
    the built-in ``hash``, whose string seed is randomised per process:
    that would hand the same trip different words after every restart, and
    a meeting point that changes when you restart the server is a worse
    lie than the invented words themselves.

    Args:
        latitude: Latitude in degrees.
        longitude: Longitude in degrees.

    Returns:
        Three lowercase words joined by full stops, e.g.
        ``"cornice.saddle.willow"``.

    """
    pinned = f"{latitude:.{_FAKE_PRECISION}f},{longitude:.{_FAKE_PRECISION}f}"
    digest = hashlib.blake2b(pinned.encode(), digest_size=6).digest()
    count = len(_FAKE_WORDS)
    return ".".join(
        _FAKE_WORDS[int.from_bytes(digest[index * 2 : index * 2 + 2]) % count]
        for index in range(3)
    )


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


def what3words_map_url(words: str | None) -> str | None:
    """Return the what3words map URL for one address, or None.

    SNOW-840, moved here from ``apps.trips.views`` by SNOW-882 when a
    second app needed it. ``{settings.WHAT3WORDS_MAP_BASE_URL}/{words}`` —
    their own map is the only place the 3m square can actually be SEEN, and
    a reader deciding whether they can find the spot needs to see it rather
    than take three words on trust.

    Built in Python rather than in the template on the codebase's usual
    rule: a URL is not presentation, and a template that concatenated a
    setting onto a variable would be the one place a trailing slash in the
    environment turned into a broken link. The base is stripped of one for
    that reason.

    Args:
        words: The address without its ``///`` prefix, or None when there
            is no address — flag off, no key, upstream down, not yet
            resolved.

    Returns:
        The absolute URL, or None when there is nothing to link to.

    """
    if not words:
        return None
    return f"{settings.WHAT3WORDS_MAP_BASE_URL.rstrip('/')}/{words}"


def fill_what3words(location: Location) -> str | None:
    """Return a location's three word address, converting it if need be.

    The single-row filler every path goes through — the ``fill_what3words``
    management command, the services that mint a location, and the trip
    view. Returns the stored address when there is one, and otherwise
    spends one conversion and writes the result back.

    A STORED ADDRESS IS NEVER RE-CONVERTED. It encodes a fixed 3m square,
    so it cannot go stale; the only thing that invalidates one is the pin
    moving, which clears the column at the point of the move rather than
    here.

    IDEMPOTENT, and safe to run concurrently. Two callers that both find
    the column empty will both convert and both write; they write the same
    words, and the second save is a no-op in effect. Locking to save a
    duplicate call would cost more than the call.

    Writes with ``update_fields`` so a fill touches the two columns and
    nothing else — it cannot clobber a concurrent edit of the location's
    coordinates.

    Args:
        location: The location to resolve. Saved in place when a
            conversion succeeds.

    Returns:
        The three word address, or None when there is none stored and the
        conversion did not succeed.

    """
    stored = location.three_word_address
    if stored is not None:
        return stored

    words = convert_to_3wa(location.latitude, location.longitude)
    if words is None:
        # By ROW ID, never by coordinate (SNOW-718) — this is the level
        # that has an id to name, which is why ``convert_to_3wa`` logs no
        # identity at all. The id is also the more useful of the two: it
        # is what you would look the row up by.
        logger.warning("what3words: no address for location id=%s", location.pk)
        # Deliberately no negative caching. A failure is nearly always the
        # upstream or the key rather than the square, so stamping "we
        # tried" would suppress the retry that fixes itself.
        return None

    location.what3words = words
    location.what3words_fetched_at = timezone.now()
    location.save(update_fields=["what3words", "what3words_fetched_at", "updated_at"])
    return words
