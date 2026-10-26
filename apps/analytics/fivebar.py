"""
apps/analytics/fivebar.py — fivebar server-events client (SNOW-1180).

fivebar (https://fiveb.ar) is the cookieless page analytics the site's
``<head>`` tag reports to. Product events happen on the server — a sign-in
requested, a region pinned, an account deleted — so they are sent from here
through fivebar's server events API rather than from the page:

    POST https://fiveb.ar/api/events
    Authorization: Bearer $FIVEBAR_TOKEN
    {"site": ..., "name": <page>, "event": <goal>, "id": ..., "props": {...}}

Every event name fivebar receives becomes a goal on its dashboard the first
time it is sent, so there is nothing to configure on the fivebar side; what
becomes a goal is decided here, by ``GOAL_EVENTS``. Anything not listed —
the PWA lifecycle events and the server signals — is never sent: they are
operational, high-volume, and every event counts towards fivebar's billable
pageviews.

Privacy
-------
* The caller is identified by ``id`` (the distinct_id the caller already
  computed — ``Account.uuid`` or a session-scoped id), never by ``ip``.
  fivebar hashes it with the site and a daily secret and keeps none of it.
* ``name`` is a route PATTERN (``page_of``), never the requested path: the
  account-access and share URLs carry tokens, and a token is a capability.
* ``props`` are reduced to what fivebar accepts (``_props``). The PII
  block-list in ``apps/analytics/__init__.py`` has already run.

Sending never waits on fivebar: each POST is handed to a small background
thread pool, and every failure is logged, never raised. At most
``_MAX_PENDING`` sends wait or run at once; past that, events are dropped
(and logged) rather than queued, so a slow fivebar cannot grow the backlog
without bound. No-op unless both
``FIVEBAR_TOKEN`` and ``FIVEBAR_DOMAIN`` are set, so dev and test never send.
"""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
from django.conf import settings
from django.http import HttpRequest

logger = logging.getLogger(__name__)

FIVEBAR_EVENTS_URL = "https://fiveb.ar/api/events"

# Seconds before a POST to fivebar is abandoned. It runs off the request
# thread, so this bounds how long a pool worker is tied up, not a page.
_TIMEOUT_SECONDS = 5

# Events that are fivebar goals. The first seven are the server-side
# product events; the ``map.*`` ones are the client's feature-use events
# from the telemetry receiver (apps/analytics/schema.py), less
# ``map.basemap.record_write_failed``, which is a failure signal for the
# logs rather than something a visitor did.
GOAL_EVENTS: frozenset[str] = frozenset(
    {
        "bulletin_viewed",
        "sign_in_requested",
        "subscription_confirmed",
        "unsubscribed",
        "region_added",
        "region_removed",
        "share_link_clicked",
        "map.favourite.created",
        "map.favourite.deleted",
        "map.favourite.overlay_toggled",
        "map.favourite.shared",
        "map.community_reports.overlay_toggled",
        "map.community_reports.marker_tapped",
        "map.route.created",
        "map.route.deleted",
        "map.route.overlay_toggled",
        "map.route.shared",
        "map.route.claimed",
        # SNOW-1230: the onboarding funnel — what the welcome card is
        # answered with, and how far the example walkthrough gets.
        "map.welcome.chosen",
        "map.example.opened",
        "map.example.exited",
        "map.example.finished",
        # SNOW-1231: how far the "Find a resort" walkthrough gets.
        "map.resort_guide.opened",
        "map.resort_guide.exited",
        "map.resort_guide.finished",
        "map.resort_guide.pinned",
    }
)

# fivebar's limits on an event's data (https://fiveb.ar/docs/events#data):
# up to 8 names of lowercase letters, digits, ``-`` and ``_`` (32 at most),
# each a text, number or boolean value of up to 255 characters.
_MAX_PROPS = 8
_MAX_VALUE_LENGTH = 255
_PROP_NAME = re.compile(r"^[a-z0-9_-]{1,32}$")

# Properties never forwarded. ``client_timestamp`` is the receiver's
# clock-skew cross-check: unique per event, so as a fivebar property it
# would only fill the breakdown with one-off values.
_SKIPPED_PROPS: frozenset[str] = frozenset({"client_timestamp"})

# Sends queued or in flight before new ones are dropped. Two workers with a
# five-second timeout drain ~24 a minute when fivebar hangs; the receiver
# admits far more than that from one client, so the pool's own unbounded
# queue is not a limit.
_MAX_PENDING = 200

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fivebar")
_pending = threading.BoundedSemaphore(_MAX_PENDING)


def page_of(request: HttpRequest) -> str:
    """Return the route pattern ``request`` resolved to, as a fivebar page name.

    The pattern (``/account/<str:token>/``), not the path
    (``/account/abc123/``), so a token in the URL never leaves the server.
    Falls back to ``/`` when the request did not resolve.

    Args:
        request: The request the event happened on.

    Returns:
        The route pattern with a leading slash.

    """
    match = getattr(request, "resolver_match", None)
    route = getattr(match, "route", "") or ""
    return "/" + route.lstrip("/")


def _props(properties: dict[str, object]) -> dict[str, object]:
    """Reduce event properties to the ones fivebar keeps.

    Drops names fivebar would reject, ``None`` and non-scalar values, and
    ``_SKIPPED_PROPS``; truncates long text; keeps at most ``_MAX_PROPS``,
    in the caller's order.

    Args:
        properties: The event properties as the caller built them.

    Returns:
        The subset to send as fivebar ``props``.

    """
    kept: dict[str, object] = {}
    for name, value in properties.items():
        if len(kept) == _MAX_PROPS:
            break
        if name in _SKIPPED_PROPS or not _PROP_NAME.match(name):
            continue
        if isinstance(value, str):
            kept[name] = value[:_MAX_VALUE_LENGTH]
        elif isinstance(value, bool | int | float):
            kept[name] = value
    return kept


def _post(token: str, payload: dict[str, object]) -> None:
    """POST one event to fivebar, logging rather than raising on failure.

    Runs on the background pool. fivebar answers 202 when it has the event;
    anything else is logged at WARNING with fivebar's error body.

    Args:
        token: The fivebar send-only API token.
        payload: The event JSON.

    """
    try:
        response = requests.post(
            FIVEBAR_EVENTS_URL,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        logger.warning("fivebar send failed: event=%s", payload.get("event"))
        return
    if response.status_code != 202:
        logger.warning(
            "fivebar rejected event=%s status=%s body=%.200s",
            payload.get("event"),
            response.status_code,
            response.text,
        )


def _post_and_release(token: str, payload: dict[str, object]) -> None:
    """Run ``_post`` and free its ``_pending`` slot, however it ends.

    Args:
        token: The fivebar send-only API token.
        payload: The event JSON.

    """
    try:
        _post(token, payload)
    finally:
        _pending.release()


def send(
    event: str,
    distinct_id: str,
    properties: dict[str, object],
    page: str = "/",
) -> None:
    """Send one goal event to fivebar, in the background.

    No-op when the event is not in ``GOAL_EVENTS`` or when ``FIVEBAR_TOKEN``
    or ``FIVEBAR_DOMAIN`` is unset. Dropped, with a WARNING, when
    ``_MAX_PENDING`` sends are already waiting.

    Args:
        event: The event name; becomes the fivebar goal's name.
        distinct_id: The caller's identifier, sent as fivebar's ``id``.
        properties: Event properties (already PII-checked).
        page: The route pattern the event happened on (``page_of``).

    """
    if event not in GOAL_EVENTS:
        return
    token = (getattr(settings, "FIVEBAR_TOKEN", "") or "").strip()
    domain = (getattr(settings, "FIVEBAR_DOMAIN", "") or "").strip()
    if not token or not domain:
        logger.debug("fivebar no-op (FIVEBAR_TOKEN/DOMAIN unset): event=%s", event)
        return
    payload: dict[str, object] = {
        "site": domain,
        "name": page,
        "event": event,
        "id": distinct_id,
        "props": _props(properties),
    }
    if not _pending.acquire(blocking=False):
        logger.warning("fivebar backlog full, dropped event=%s", event)
        return
    try:
        _executor.submit(_post_and_release, token, payload)
    except RuntimeError:
        # The pool is shutting down (interpreter exit).
        _pending.release()
