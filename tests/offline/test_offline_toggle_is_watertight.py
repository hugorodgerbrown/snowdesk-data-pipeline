"""
tests/offline/test_offline_toggle_is_watertight.py — with Offline mode
switched on, nothing leaves the device.

This is the suite's foundational test, and every other test in the
directory is worthless without it. They all say "the map drew with no
network", and they all establish "no network" by pressing the same switch.
If that switch leaks, those tests are measuring a map that quietly had the
internet the whole time.

The condition is deliberately hostile to the app: the network is fully
available. The proxy is in ``pass`` mode, the radio is on,
``navigator.onLine`` is ``true``, and the only thing standing between the
app and the server is the user's stated intention. That is the real
scenario — a metered roam, a battery to nurse, a tunnel coming up — and it
is the one where a leak costs the user something they were promised they
would not spend.

The measuring instrument is the proxy rather than
``page.context.set_offline``, because that call cannot see a service
worker's own fetches and ``page.route`` cannot see them either. A proxy
sits below all of it.

This test was RED when it was written, and that was the finding rather
than a fault in the test: SNOW-852, ``sw.js``'s fetch handler classified
same-origin API GETs, HTMX fragments and mutation POSTs as
``sync === 'network'`` and returned without calling ``respondWith``, so
``_networkMode`` never got a say and those requests left the device under
``offline-forced``. It is green as of that fix.

The assertion is zero, and it was zero while it failed. Anyone tempted to
relax it to match some future behaviour should note that doing so deletes
the only thing standing behind every other test in this directory.

Scenario: P8, D3
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.offline.conftest import OfflineMapPage

# Requests the BROWSER issues, which no application code and no service
# worker can prevent. Everything else is the app's, and is a leak.
#
# Kept deliberately short and reasoned, one entry at a time — an exemption
# list is how a watertightness test quietly stops testing anything.
#
#   /sw.js            The worker update check, issued by the browser on
#                     navigation. Not app code, and not interceptable: a
#                     worker cannot intercept the fetch of itself.
#   /csp/report-uri/  A CSP violation report. Issued by the browser's own
#                     policy engine and specified to bypass service
#                     workers entirely, so there is no layer at which the
#                     app could stop it.
_BROWSER_OWNED_PATHS = frozenset({"/sw.js", "/csp/report-uri/"})


# The modules ``sw.js`` pulls in with ``importScripts``. These are fetched
# by the browser's worker script loader when the worker starts, NOT through
# the worker's own fetch handler — a worker cannot intercept its own
# imports any more than it can intercept itself.
#
# Read out of the source rather than listed here, because a hand-kept copy
# would drift the first time someone adds an import, and it would drift
# SILENTLY in the direction that weakens the test: the new module's fetch
# would be reported as an application leak, and the obvious fix would be to
# paste another string into an exemption list.
def _service_worker_imports() -> frozenset[str]:
    """Return the ``/static/js/*.js`` paths ``sw.js`` imports at startup."""
    source = (
        Path(__file__).resolve().parents[2] / "static" / "js" / "sw.js"
    ).read_text()
    return frozenset(re.findall(r"importScripts\(\s*'([^']+)'", source))


def _leaks(page: OfflineMapPage, watermark: int) -> list[str]:
    """Return every request seen since ``watermark`` that the app owns.

    Args:
        page: The surface under test.
        watermark: A ``NetworkRecorder.mark()`` taken before the phase.

    Returns:
        Human-readable lines, one per leaked request.

    """
    exempt = _BROWSER_OWNED_PATHS | _service_worker_imports()
    return [
        exchange.to_string()
        for exchange in page.network.since(watermark)
        if urlsplit(exchange.url).path not in exempt
    ]


@pytest.mark.usefixtures("_load_offline_dataset")
def test_offline_mode_stops_all_traffic_while_the_network_is_available(
    offline_map_page: OfflineMapPage,
) -> None:
    """Switching on Offline mode silences the app on a working connection.

    The user gestures afterwards are chosen to be the ones that DO reach
    the network in normal operation — a reload, a date change, a camera
    move that wants new tiles. A test that went offline and then sat still
    would pass on an app that leaked constantly.
    """
    page = offline_map_page.page
    offline_map_page.switch_offline_mode(True)

    watermark = offline_map_page.network.mark()

    # 1. A reload — the navigation the shell cache exists for.
    page.reload()
    page.wait_for_load_state("load")

    # 2. A camera move that asks for tiles the app has never fetched.
    offline_map_page.jump_to(7.6, 46.1, offline_map_page.subject.inside_zoom)

    # 3. A date change — the scrubber's own fetch path.
    page.goto(f"{offline_map_page.live_server_url}/?d=2026-04-08")
    page.wait_for_load_state("load")

    # 4. Time for anything on a timer — telemetry's 30s flush is the one
    #    that matters, and the freshness probe's first backoff step.
    page.wait_for_timeout(3_000)

    leaked = _leaks(offline_map_page, watermark)
    assert not leaked, (
        "Offline mode is switched on, but the app still used the network.\n"
        f"{len(leaked)} request(s) left the device:\n"
        + "\n".join(f"    {line}" for line in leaked)
        + "\n\nEach line is a request that reached the proxy while the user "
        "had asked the app not to use the network."
    )


@pytest.mark.usefixtures("_load_offline_dataset")
def test_switching_offline_mode_back_off_restores_the_network(
    offline_map_page: OfflineMapPage,
) -> None:
    """The switch is a switch, not a one-way door.

    The counterpart to the test above, and not a formality: an
    implementation that satisfied the first test by latching permanently —
    or by leaving the worker in a mode nothing lifts — would strand a user
    on cached avalanche ratings with no way back. ``bounded-offline-read-paths.md``
    names that as the one genuinely dangerous outcome in this area.
    """
    offline_map_page.switch_offline_mode(True)
    offline_map_page.switch_offline_mode(False)

    watermark = offline_map_page.network.mark()
    offline_map_page.page.reload()
    offline_map_page.page.wait_for_load_state("load")

    assert offline_map_page.network.since(watermark), (
        "After switching Offline mode back off, a full page reload produced "
        "no network traffic at all — the app is still offline. The switch "
        "has to be reversible: a user who cannot get back online is reading "
        "cached avalanche ratings with no way to refresh them."
    )
