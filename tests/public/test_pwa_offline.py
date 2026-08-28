"""
Server-side tests for the SNOW-377 offline / freshness UI.

Covers the server-rendered half of the contract:

* ``apps.core.freshness.freshness_state()`` — the three-state classifier that
  the client mirrors. Boundary conditions exercised.
* ``pwa_freshness.freshness_state`` template tag — usable from a
  template, ``None`` / naive datetimes downgrade safely.
* Connection-status toast ships closed on every page, carrying every
  state's copy; the header symbol ships visible (SNOW-748).
* Freshness indicator partial renders the correct dot colour + label
  for each state.
* Subscribe form is marked ``data-network-required``.
* pwa_offline.js is loaded on every page.

The JS half is exercised by Playwright in a later ticket.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from django.template import Context, Template
from django.test import Client

from apps.core.freshness import freshness_state

# ---------------------------------------------------------------------------
# freshness_state() classifier
# ---------------------------------------------------------------------------


def _at(seconds_ago: int) -> datetime:
    """Return a tz-aware datetime ``seconds_ago`` seconds before ``ref``."""
    return _NOW - timedelta(seconds=seconds_ago)


_NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("seconds_ago", "expected"),
    [
        (0, "fresh"),
        (60, "fresh"),
        (23 * 60 * 60, "fresh"),
        (24 * 60 * 60, "stale"),
        (24 * 60 * 60 + 1, "stale"),
        (47 * 60 * 60, "stale"),
        (48 * 60 * 60, "unsafe"),
        (100 * 60 * 60, "unsafe"),
    ],
)
def test_freshness_state_default_thresholds(seconds_ago: int, expected: str) -> None:
    """The default 24h/48h thresholds classify at the documented boundaries."""
    generated = _at(seconds_ago)
    assert freshness_state(generated, now=_NOW) == expected


def test_freshness_state_naive_datetime_raises() -> None:
    """A naive datetime is a bug — refuse to classify."""
    with pytest.raises(ValueError, match="timezone-aware"):
        freshness_state(datetime(2026, 7, 15, 12, 0), now=_NOW)


def test_freshness_state_unsafe_after_none_saturates_at_stale() -> None:
    """Non-safety data (``unsafe_after=None``) never escalates to unsafe."""
    generated = _at(100 * 60 * 60)  # 100 hours old
    assert freshness_state(generated, unsafe_after=None, now=_NOW) == "stale"


def test_freshness_state_custom_thresholds() -> None:
    """Custom max_age / unsafe_after override the defaults."""
    generated = _at(70)  # 70 seconds old
    assert freshness_state(generated, max_age=60, unsafe_after=120, now=_NOW) == "stale"
    assert freshness_state(generated, max_age=60, unsafe_after=60, now=_NOW) == "unsafe"


# ---------------------------------------------------------------------------
# freshness_state template tag
# ---------------------------------------------------------------------------


def _render(template: str, ctx: dict) -> str:
    """Render a bare Template string with a Context dict."""
    return Template(template).render(Context(ctx))


def test_freshness_tag_returns_state_string() -> None:
    """The tag can be assigned via ``as`` and rendered inline.

    The template tag reads the current time from ``django.utils.timezone.now()``
    at render time, so ``generated`` must be relative to the real "now"
    rather than the module-level ``_NOW`` fixture — which is a fixed
    2026-07-15 value used for the classifier tests, and would put
    ``generated`` more than 24h in the past when the calendar rolls
    forward.
    """
    from django.utils import timezone as django_timezone

    generated = django_timezone.now() - timedelta(hours=1)
    out = _render(
        "{% load pwa_freshness %}{% freshness_state generated as s %}{{ s }}",
        {"generated": generated},
    )
    assert out.strip() == "fresh"


def test_freshness_tag_none_downgrades_to_unsafe() -> None:
    """A ``None`` timestamp cannot claim freshness — return ``unsafe``."""
    out = _render(
        "{% load pwa_freshness %}{% freshness_state generated as s %}{{ s }}",
        {"generated": None},
    )
    assert out.strip() == "unsafe"


def test_freshness_tag_naive_datetime_downgrades_to_unsafe() -> None:
    """A naive datetime cannot be trusted — return ``unsafe`` without raising."""
    out = _render(
        "{% load pwa_freshness %}{% freshness_state generated as s %}{{ s }}",
        {"generated": datetime(2026, 7, 15, 12, 0)},
    )
    assert out.strip() == "unsafe"


# ---------------------------------------------------------------------------
# Freshness indicator partial
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "colour_class"),
    [
        ("fresh", "bg-status-success-text"),
        ("stale", "bg-status-warning-text"),
        ("unsafe", "bg-status-error-text"),
    ],
)
def test_freshness_indicator_dot_colour(state: str, colour_class: str) -> None:
    """Each state renders its own status-palette dot colour."""
    generated = datetime(2026, 7, 15, 9, 15, tzinfo=UTC)
    out = _render(
        '{% include "includes/_freshness_indicator.html" '
        "with state=state generated_at=generated %}",
        {"state": state, "generated": generated},
    )
    assert f'data-freshness-state="{state}"' in out
    assert colour_class in out


def test_freshness_indicator_label_formatted() -> None:
    """The visible label shows the timestamp in "HH:MM DD/MM" in UTC.

    Django's ``|date`` filter with ``USE_TZ=True`` converts to
    ``settings.TIME_ZONE`` (UTC in Snowdesk), so a 09:15+01:00 input
    renders as 08:15 UTC.
    """
    generated = datetime(2026, 7, 15, 9, 15, tzinfo=timezone(timedelta(hours=1)))
    out = _render(
        '{% include "includes/_freshness_indicator.html" '
        "with state='fresh' generated_at=generated %}",
        {"generated": generated},
    )
    # Djangofmt may wrap "Updated" and the timestamp onto separate lines
    # inside the blocktrans; collapse whitespace before asserting.
    collapsed = " ".join(out.split())
    assert "Updated 08:15 15/07" in collapsed


# ---------------------------------------------------------------------------
# End-to-end — symbol + toast + script + subscribe form
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_connection_toast_ships_closed_on_home_page() -> None:
    """The connection-status toast renders on every page, closed by default.

    SNOW-748 replaced the offline banner with this. It is fixed over the
    page rather than in flow — the banner pushed the nav down, which is
    why it could not be permanent — and it opens only when the user
    presses the header symbol, so it ships with the ``hidden`` class.
    """
    body = Client().get("/").content.decode("utf-8")
    assert 'id="pwa-offline-toast"' in body
    assert 'id="pwa-offline-banner"' not in body
    assert "hidden fixed" in body


@pytest.mark.django_db
def test_connection_toast_is_height_bounded_and_scrolls() -> None:
    """Fixed over the map, so the house rule applies: bound it and scroll it.

    ``dvh``, not ``vh``: on mobile ``vh`` ignores the browser chrome, so a
    ``vh``-bounded panel is taller than the visible viewport exactly where
    the constraint matters.
    """
    body = Client().get("/").content.decode("utf-8")
    toast = body.split('id="pwa-offline-toast"', 1)[1].split(">", 1)[0]
    assert "max-h-[60dvh]" in toast
    assert "overflow-y-auto" in toast


@pytest.mark.django_db
def test_connection_toast_carries_every_state_and_the_way_back() -> None:
    """All four explanations and both CTA labels are server-rendered.

    ``makemessages`` never scans ``static/js``, so a string set from a JS
    literal ships as English to every locale — ``bin/i18n-lint`` fails on
    exactly that. Every variant is rendered here and toggled by ``hidden``
    in ``static/js/pwa_offline.js``.

    The CTA is the way back to the network, and for an anonymous reader
    latched offline by the service worker it is the ONLY one: the "Offline
    mode" switch lives in the account menu.
    """
    body = Client().get("/").content.decode("utf-8")
    toast = body.split('id="pwa-offline-toast"', 1)[1].split("</div>", 1)[0]
    for role in (
        "online-message",
        "offline-message",
        "latched-message",
        "synced-at",
        "online-explainer",
        "offline-explainer",
        "latched-explainer",
        "forced-explainer",
        "reconnect-label",
        "resume-label",
    ):
        assert f'data-role="{role}"' in toast


@pytest.mark.django_db
def test_connection_toast_has_no_auto_dismiss_timeout() -> None:
    """No ``timeout``: this is reference information, not a notification.

    The user asked to see it by pressing the symbol, so it stays until
    they dismiss it or press again — unlike the map's offline toasts,
    which opt into the shared 6s auto-dismiss.
    """
    body = Client().get("/").content.decode("utf-8")
    toast = body.split('id="pwa-offline-toast"', 1)[1].split(">", 1)[0]
    assert "data-toast-timeout" not in toast


@pytest.mark.django_db
def test_connectivity_symbol_ships_visible_on_home_page() -> None:
    """The header symbol is permanent — never ``hidden``, on any page.

    This is what made deleting the banner an improvement rather than a
    subtraction: the banner said nothing at all while the app was healthy,
    so a user only ever discovered it by losing their connection.
    """
    body = Client().get("/").content.decode("utf-8")
    symbol = body.split("data-network-indicator", 1)[1].split(">", 1)[0]
    assert "hidden" not in symbol
    assert 'aria-controls="pwa-offline-toast"' in symbol


@pytest.mark.django_db
def test_pwa_offline_script_loaded_on_home_page() -> None:
    """The offline JS is referenced from the home page shell."""
    response = Client().get("/")
    body = response.content.decode("utf-8")
    assert "pwa_offline.js" in body


@pytest.mark.django_db
def test_subscribe_form_marked_network_required() -> None:
    """The subscribe form ships ``data-network-required`` for JS pickup."""
    response = Client().get("/account/")
    # Regardless of auth state we should be able to see /sign-in/, which
    # renders another form via the flow. Instead exercise the partial
    # directly against a fake context.
    _ = response
    out = _render(
        '{% include "accounts/partials/subscribe_form.html" '
        "with region_id='CH-4123' %}",
        {},
    )
    assert "data-network-required" in out
