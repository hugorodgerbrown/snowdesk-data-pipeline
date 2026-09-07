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
dangerous failure of the two.

SNOW-856 changed how that second half is measured, and the change is
worth understanding before reading any assertion below. The shared z0-9
base layer covers everything the camera can reach, and MapLibre's
``findLoadedParent`` stretches a stored ancestor wherever a tile is
missing — so the map now draws *everywhere* offline, coarsely, with
detail only over downloads. "The canvas is blank" was only ever a proxy
for "no detail is stored here", and it is the proxy that broke. The
property is now asserted against the cache directly
(``stored_band_tiles_at``). The reader-facing half of it — being able to
SEE where detail ends — has no cue at all until the coverage boundary is
drawn (SNOW-857), and that is the one respect in which SNOW-856 left the
product worse.

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
#
# SNOW-856 moved BOTH low rows across the gap, and only one of them was a
# defect:
#
#   - "below the band, z7" measured 0.033 because nothing below z10 was
#     ever stored. That was the bug, and the shared base layer now answers
#     there.
#   - "outside coverage, z11" measured 0.033 because nothing was stored for
#     that ground at all. That was the PROMISE — and the base layer breaks
#     it as a pixel measurement, because MapLibre's `findLoadedParent`
#     stretches a stored z9 ancestor over undownloaded ground. The promise
#     itself survives; it is asserted against the cache now
#     (`stored_band_tiles_at`), because pixels can no longer express it.
#
# `_DREW` is therefore the only threshold left. The rows are kept above as
# the before-readings they were, and the value is unchanged: what a drawn
# viewport measures did not move, only which viewports are expected to be
# one.
_DREW = 0.15


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
def test_outside_the_downloaded_area_no_detail_is_stored(
    offline_map_page: OfflineMapPage,
) -> None:
    """Past the edge of coverage the DETAIL stops, even though pixels do not.

    The failure this guards against has not changed: a user who cannot
    tell where their stored map ends will plan on ground they have no data
    for, and the download panel will have told them they hold 200 MB of
    it. What changed is where that property can be measured.

    **This test asserted a blank canvas until SNOW-856.** The shared z0-9
    base layer covers everything the camera can reach, and MapLibre's
    ``findLoadedParent`` renders a cached ancestor wherever a tile is
    missing — so at z11 over undownloaded ground a stored z9 tile is
    stretched to fill the viewport. The map no longer goes blank at the
    edge of coverage, which is how every mapping app behaves and is a
    deliberate trade (SNOW-856, accepted 2026-09-07): coarse context
    everywhere beats a black hole one valley over.

    So the assertion moves from pixels to the cache. "Nothing drew" was
    only ever a proxy for "nothing detailed is stored here", and it is the
    proxy that broke, not the property. This asks the real question
    directly: no tile in the download band exists for this ground, in any
    pinned bucket, under any basemap.

    The pixel half of the promise — that a reader can SEE where detail
    ends — cannot be tested until the coverage boundary is drawn, which is
    SNOW-857. Until it lands the reader has no cue at all, and that is the
    one respect in which this ticket left the product worse.
    """
    subject = offline_map_page.subject
    latitude, longitude = subject.outside

    offline_map_page.choose_basemap(subject.basemap_key)
    offline_map_page.select_region(subject.region_name)
    offline_map_page.download_selected_region()
    offline_map_page.wait_for_base_layer()

    # See ``discard_passive_basemap_cache`` — without this the
    # assertion has no subject, because the passive cache answers
    # everywhere.
    offline_map_page.discard_passive_basemap_cache()
    offline_map_page.go_offline()
    offline_map_page.page.reload()
    offline_map_page.page.wait_for_load_state("load")

    offline_map_page.jump_to(longitude, latitude, subject.inside_zoom)
    stored = offline_map_page.stored_band_tiles_at(longitude, latitude)

    assert not stored, (
        f"A viewport {subject.outside} — well outside {subject.region_id}, "
        "the only area downloaded — has tiles stored for it INSIDE the "
        f"download band: {stored}. Either the region's clip covers more "
        "ground than it claims, or something pinned tiles for ground the "
        "user never asked for.\n"
        f"Reproduce with SNOWDESK_OFFLINE_SEED={subject.seed}.\n  "
        + offline_map_page.diagnostics()
    )
    # The page must still be alive out here. A frozen map and a drawn one
    # look identical in a screenshot, so this is checked separately:
    # `wait_for_map_idle` inside `jump_to` already returned, which means
    # MapLibre resolved every tile request rather than leaving them
    # pending — the bounded read paths doing their job.
    assert offline_map_page.page.locator("#map canvas").is_visible()


@pytest.mark.usefixtures("_load_offline_dataset")
def test_the_base_layer_reaches_ground_no_area_covers(
    offline_map_page: OfflineMapPage,
) -> None:
    """Outside every download, the map still draws — coarsely, but it draws.

    The other side of SNOW-856's trade, and worth its own assertion
    because it is the half a nervous fix would quietly undo: clipping the
    base layer to the downloaded areas would restore the old blank edge
    and pass every other test in this module.
    """
    subject = offline_map_page.subject
    latitude, longitude = subject.outside

    offline_map_page.choose_basemap(subject.basemap_key)
    offline_map_page.select_region(subject.region_name)
    offline_map_page.download_selected_region()
    offline_map_page.wait_for_base_layer()

    offline_map_page.discard_passive_basemap_cache()
    offline_map_page.go_offline()
    offline_map_page.page.reload()
    offline_map_page.page.wait_for_load_state("load")

    offline_map_page.jump_to(longitude, latitude, subject.below_band_zoom)
    ink = offline_map_page.basemap_ink()

    assert ink >= _DREW, (
        f"At z{subject.below_band_zoom} over {subject.outside} — outside "
        f"{subject.region_id}, the only area downloaded — the map drew "
        f"only {ink:.1%} of the canvas. The shared base layer covers "
        "everything the camera can reach, not just downloaded ground, so "
        "a blank view here means it was clipped to the areas, stored "
        "under urls the map does not ask for, or never stored at all.\n"
        f"Reproduce with SNOWDESK_OFFLINE_SEED={subject.seed}.\n  "
        + offline_map_page.diagnostics()
    )


@pytest.mark.usefixtures("_load_offline_dataset")
def test_stored_tiles_overzoom_past_the_band_and_the_base_layer_holds_below_it(
    offline_map_page: OfflineMapPage,
) -> None:
    """The stored band is z10–14, and both its edges are covered.

    Past z14 the area's own tiles overzoom — bigger, no new detail, never
    blank. Below z10 the SHARED BASE LAYER takes over (SNOW-856), so
    zooming out no longer falls off the edge of the download.

    **This test asserted the opposite until SNOW-856**, and was right to:
    nothing below z10 was stored, so anything drawing there had come from
    somewhere the user had not downloaded. That was true — and the reason
    it was true is the defect. The map's camera goes to z4 while a
    download's floor was z10, so an offline reader who zoomed out saw
    nothing and no surface said why. The old assertion is preserved as the
    ``_BLANK`` check in
    ``test_outside_the_downloaded_area_the_basemap_is_honestly_blank``,
    which is where "the map stops where the data stops" still belongs: the
    base layer widens coverage in ZOOM, never in ground.
    """
    subject = offline_map_page.subject
    latitude, longitude = subject.centre

    offline_map_page.choose_basemap(subject.basemap_key)
    offline_map_page.select_region(subject.region_name)
    offline_map_page.download_selected_region()
    # The top-up runs behind the roundel's `done` on purpose — see
    # `wait_for_base_layer`. Without this the z-out assertion below races
    # several hundred real tiles.
    offline_map_page.wait_for_base_layer()

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
    assert below >= _DREW, (
        f"At z{subject.below_band_zoom}, below the stored band, "
        f"{subject.region_id} drew only {below:.1%} of the canvas. The "
        "shared z0-9 base layer should be answering here — it is fetched "
        "on the tail of every download and covers the whole area the "
        "camera can reach. A blank view means it was never stored, was "
        "stored under urls the map does not ask for, or was evicted by "
        "something that should never have been able to pick it.\n"
        f"Reproduce with SNOWDESK_OFFLINE_SEED={subject.seed}.\n  "
        + offline_map_page.diagnostics()
    )
