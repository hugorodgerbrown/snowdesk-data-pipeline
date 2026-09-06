"""
tests/offline/test_dead_network_conditions.py — the map survives a network
that hangs, not only one that refuses.

``bounded-offline-read-paths.md`` exists because the whole offline story was
written as ``catch`` branches, and a ``catch`` assumes a failing network
REJECTS. That is true when the radio is off. It is not true on the
Underground, in a valley with no coverage, or behind a captive portal, where
the radio is attached, ``navigator.onLine`` stays ``true``, and ``fetch``
neither resolves nor rejects — it hangs on TCP retries for minutes. The
catch never runs and the app shows a blank page while the data it needs is
on disk. That failure was reported from an actual Tube journey.

Nothing tests it, because no ordinary browser-test tool can produce it.
``set_offline`` and DevTools both simulate a refusal; ``page.route`` can
abort or fulfil but not hold. A proxy can simply not answer, which is what
``blackhole`` mode does.

The two conditions are kept in one file because the interesting assertion is
the comparison: the app must reach the same end state under both, and the
hang must be bounded rather than merely survivable.

Scenario: P7, D3
"""

from __future__ import annotations

import time

import pytest

from tests.offline.conftest import OfflineMapPage

# ``sw.js``'s own budgets are 5s for navigation and shell reads and 3s for
# basemap reads, and ``OFFLINE_LATCH_THRESHOLD`` is three consecutive
# expiries — so a view whose requests all hang should recognise the dead
# radio in roughly nine seconds and serve from cache immediately after.
# This ceiling is generous against that: it is here to catch "the app hangs
# indefinitely", which is the reported bug, not to police the exact budget.
_BOUNDED_S = 45.0


@pytest.mark.usefixtures("_load_offline_dataset")
def test_a_hanging_network_does_not_hang_the_map(
    offline_map_page: OfflineMapPage,
) -> None:
    """A network that accepts and never answers must not stall the page.

    The distinction this makes, and that no other test in the repo can
    make: the proxy holds every connection open rather than resetting it,
    so every ``fetch`` the app issues stays pending forever. If the read
    paths were unbounded the reload below would never finish, which is
    exactly what a user on the Underground saw.
    """
    subject = offline_map_page.subject
    latitude, longitude = subject.centre

    offline_map_page.choose_basemap(subject.basemap_key)
    offline_map_page.select_region(subject.region_name)
    offline_map_page.download_selected_region()

    # The radio is attached and the route is dead. No offline mode is
    # switched on: the app has to work this out for itself, which is the
    # whole point of the latch.
    offline_map_page.network.set_mode("blackhole")

    started = time.monotonic()
    offline_map_page.page.reload()
    offline_map_page.page.wait_for_load_state("load")
    offline_map_page.jump_to(longitude, latitude, subject.inside_zoom)
    elapsed = time.monotonic() - started

    assert elapsed < _BOUNDED_S, (
        f"With every request hanging, loading the map and moving to "
        f"{subject.region_id} took {elapsed:.0f}s. The read paths are "
        "supposed to abort on a budget and latch offline after three "
        "consecutive expiries, so a dead route costs seconds once and "
        "nothing thereafter — an unbounded wait here is the Underground "
        "bug.\\n  " + offline_map_page.diagnostics()
    )

    ink = offline_map_page.basemap_ink()
    assert ink >= 0.15, (
        f"{subject.region_id} is downloaded and the network is hanging "
        f"rather than refusing, and the map drew {ink:.1%} of the canvas. "
        "The tiles are on disk; a hanging route must not stop them being "
        "read.\\n  " + offline_map_page.diagnostics()
    )


@pytest.mark.usefixtures("_load_offline_dataset")
def test_the_header_reports_offline_when_the_route_dies_silently(
    offline_map_page: OfflineMapPage,
) -> None:
    """The connectivity symbol tells the truth ``navigator.onLine`` cannot.

    Under a black hole the radio is attached, so ``onLine`` stays ``true``.
    A header that believed it would show the user as connected while every
    read failed — and a user reading cached avalanche ratings needs to know
    they are cached. The symbol is the one surface that can say so, which
    is why it is asserted here rather than left to the eye.
    """
    offline_map_page.network.set_mode("blackhole")
    offline_map_page.page.reload()
    offline_map_page.page.wait_for_load_state("load")

    online_flag = offline_map_page.page.evaluate("() => navigator.onLine")
    offline_map_page.page.wait_for_selector(
        '[data-network-indicator][data-network-state="offline"]',
        timeout=int(_BOUNDED_S * 1000),
    )

    assert online_flag is True, (
        "This test is only meaningful while navigator.onLine is true — a "
        "black hole is supposed to leave the radio attached. It read "
        f"{online_flag!r}, so the proxy is refusing rather than hanging "
        "and the assertion above proved nothing."
    )
