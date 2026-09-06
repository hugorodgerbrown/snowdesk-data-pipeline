"""
tests/offline/test_downloaded_area_renders_offline.py — a downloaded region
draws with the network switched off, and coverage stops where it should.

The product's central offline promise, tested the way a user would find out
whether it held: download an area at home, switch the network off, and look
at the map.

Two halves, and the second is not a formality. An implementation that
served *something* everywhere — a fallback tile, a cached neighbouring
zoom, a silent re-fetch — would pass the first half while telling the user
they have map data they do not have. On a mountain that is the more
dangerous failure of the two, so the suite insists that outside coverage
the map goes honestly blank.

Everything about the subject is fuzzed (``tests/offline/fuzz.py``): which
region, under which basemap, at which zooms, and which direction "outside"
lies in. The seed is printed at the start of every run.

Scenario: D1, D3
"""

from __future__ import annotations

import pytest

from tests.offline.conftest import OfflineMapPage

# The two thresholds, set from measurement rather than taste. Both are
# fractions of the map canvas that differ from the style's background
# colour, sampled with Snowdesk's own overlays hidden
# (``OfflineMapPage._overlays_hidden``) so the number is about the basemap
# and nothing else.
#
# Measured on CH-2212 (Uri Rot Stock, 206 tiles, OpenFreeMap), offline,
# with the passive cache discarded so only the pinned bucket remains:
#
#     inside coverage, z11        0.558
#     inside coverage, z14        0.565
#     inside coverage, z10        0.282   <- the floor of "it drew"
#     above the band,  z16        0.378   (stored z14 tiles, overzoomed)
#     below the band,  z7         0.033
#     outside coverage, z11       0.033   <- the ceiling of "it did not"
#
# An order of magnitude separates the two groups, and the thresholds sit in
# the middle of that gap rather than close to either edge. A run that lands
# between them is not a threshold that needs nudging — it is a viewport
# that half drew, which is a finding.
_DREW = 0.15
_BLANK = 0.06


@pytest.mark.usefixtures("_load_offline_dataset")
def test_a_downloaded_region_draws_with_the_network_switched_off(
    offline_map_page: OfflineMapPage,
) -> None:
    """Download a region, switch Offline mode on, and look at the map.

    The assertion is about pixels rather than cache entries on purpose.
    SNOW-843 shipped three separate defects in which every surface agreed
    the area was downloaded and the map was blank, because every surface
    was asking about tile coverage and tile coverage is not what makes an
    area render. The only question that cannot be answered wrongly is
    whether anything appeared on screen.
    """
    subject = offline_map_page.subject
    latitude, longitude = subject.centre

    offline_map_page.choose_basemap(subject.basemap_key)
    offline_map_page.select_region(subject.region_name)
    offline_map_page.download_selected_region()

    offline_map_page.go_offline()
    offline_map_page.page.reload()
    offline_map_page.page.wait_for_load_state("load")

    offline_map_page.jump_to(longitude, latitude, subject.inside_zoom)
    ink = offline_map_page.basemap_ink()

    assert ink >= _DREW, (
        f"{subject.region_id} ({subject.region_name}) reported a completed "
        f"download of {subject.tile_count} tiles under "
        f"{subject.basemap_key}, but with Offline mode on its own centre "
        f"at z{subject.inside_zoom} drew almost nothing "
        f"({ink:.1%} of the canvas differs from the background).\n"
        f"Reproduce with SNOWDESK_OFFLINE_SEED={subject.seed}.\n  "
        + offline_map_page.diagnostics()
    )


@pytest.mark.usefixtures("_load_offline_dataset")
def test_outside_the_downloaded_area_the_basemap_is_honestly_blank(
    offline_map_page: OfflineMapPage,
) -> None:
    """Past the edge of coverage the basemap stops, and the page does not.

    The failure this guards against is a map that looks the same
    everywhere. A user who cannot see where their stored map ends will
    plan on ground they have no data for, and the download panel will
    have told them they hold 200 MB of it.
    """
    subject = offline_map_page.subject
    latitude, longitude = subject.outside

    offline_map_page.choose_basemap(subject.basemap_key)
    offline_map_page.select_region(subject.region_name)
    offline_map_page.download_selected_region()

    # See ``discard_passive_basemap_cache`` — without this the
    # assertion has no subject, because the passive cache answers
    # everywhere.
    offline_map_page.discard_passive_basemap_cache()
    offline_map_page.go_offline()
    offline_map_page.page.reload()
    offline_map_page.page.wait_for_load_state("load")

    offline_map_page.jump_to(longitude, latitude, subject.inside_zoom)
    ink = offline_map_page.basemap_ink()

    assert ink <= _BLANK, (
        f"A viewport {subject.outside} — well outside {subject.region_id}, "
        "the only area downloaded — still drew a basemap "
        f"({ink:.1%} of the canvas). Either coverage extends further than "
        "the region download claims, or something served tiles for ground "
        "the user has not stored.\n"
        f"Reproduce with SNOWDESK_OFFLINE_SEED={subject.seed}.\n  "
        + offline_map_page.diagnostics()
    )
    # The page must still be alive out here. A frozen map and a blank
    # basemap look identical in a screenshot, so this is checked
    # separately: `wait_for_map_idle` inside `jump_to` already returned,
    # which means MapLibre resolved every tile request rather than leaving
    # them pending — the bounded read paths doing their job.
    assert offline_map_page.page.locator("#map canvas").is_visible()


@pytest.mark.usefixtures("_load_offline_dataset")
def test_stored_tiles_overzoom_past_the_band_and_stop_below_it(
    offline_map_page: OfflineMapPage,
) -> None:
    """The stored band is z10–14, and both its edges behave as documented.

    Past z14 the stored tiles overzoom — bigger, no new detail, never
    blank. Below z10 nothing was stored, so the basemap is blank. Both are
    correct; a user who zooms out and sees nothing has not hit a bug, and a
    user who zooms in past 14 and sees nothing has.
    """
    subject = offline_map_page.subject
    latitude, longitude = subject.centre

    offline_map_page.choose_basemap(subject.basemap_key)
    offline_map_page.select_region(subject.region_name)
    offline_map_page.download_selected_region()

    # See ``discard_passive_basemap_cache`` — without this the
    # assertion has no subject, because the passive cache answers
    # everywhere.
    offline_map_page.discard_passive_basemap_cache()
    offline_map_page.go_offline()
    offline_map_page.page.reload()
    offline_map_page.page.wait_for_load_state("load")

    offline_map_page.jump_to(longitude, latitude, subject.above_band_zoom)
    above = offline_map_page.basemap_ink()
    assert above >= _DREW, (
        f"At z{subject.above_band_zoom}, past the top of the stored band, "
        f"{subject.region_id} drew {above:.1%} of the canvas. Stored z14 "
        "tiles should overzoom to fill the view — larger, with no new "
        "detail — rather than leaving it blank.\n"
        f"Reproduce with SNOWDESK_OFFLINE_SEED={subject.seed}.\n  "
        + offline_map_page.diagnostics()
    )

    offline_map_page.jump_to(longitude, latitude, subject.below_band_zoom)
    below = offline_map_page.basemap_ink()
    assert below <= _BLANK, (
        f"At z{subject.below_band_zoom}, below the stored band, "
        f"{subject.region_id} drew {below:.1%} of the canvas. Nothing below "
        "z10 was ever stored, so anything drawing here came from somewhere "
        "the user did not download.\n"
        f"Reproduce with SNOWDESK_OFFLINE_SEED={subject.seed}.\n  "
        + offline_map_page.diagnostics()
    )
