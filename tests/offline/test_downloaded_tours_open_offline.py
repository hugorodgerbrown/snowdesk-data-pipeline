"""
tests/offline/test_downloaded_tours_open_offline.py — the SAC tours inside a
downloaded area open with the network gone (SNOW-1417).

A download now saves the tours inside its area: the tours layer's own box
answers, and each tour's Feature and height profile, which are what a tap
opens in the route panel. Vitest holds the plan (which urls, how the area is
cut into boxes, what a short answer means) and the worker's classification.
This holds what only a real Chromium can: that the worker kept those
answers, and serves them back from disk once the radio is off.

One tour is planted at the drawn region's centre, under the ``route_drawing``
flag the tours layer rides on, before the map page loads.

Scenario: D1
"""

from __future__ import annotations

from typing import Any

import pytest
from waffle.models import Flag

from tests.factories import SkiTourFactory
from tests.offline.conftest import OfflineMapPage
from tests.offline.fuzz import OfflineSubject

_FETCH_JS = """async (url) => {
    const response = await fetch(url, { credentials: 'same-origin' });
    let body = null;
    try { body = await response.json(); } catch (_err) {}
    return {
        status: response.status,
        cache: response.headers.get('X-SW-Cache'),
        body: body,
    };
}"""


@pytest.fixture()
def planted_tour(subject: OfflineSubject, django_db_blocker: Any) -> str:
    """One SAC tour at the region's centre, and the layer switched on.

    Requested before ``offline_map_page`` so both exist when the page
    renders: ``#map`` names the tours urls only under the flag.
    """
    latitude, longitude = subject.centre
    with django_db_blocker.unblock():
        Flag.objects.update_or_create(name="route_drawing", defaults={"everyone": True})
        tour = SkiTourFactory.create(
            geometry={
                "type": "MultiLineString",
                "coordinates": [
                    [[longitude, latitude], [longitude + 0.002, latitude + 0.001]]
                ],
            },
            west=longitude,
            south=latitude,
            east=longitude + 0.002,
            north=latitude + 0.001,
        )
    return str(tour.source_uuid)


def test_a_downloaded_tour_opens_offline(
    planted_tour: str, offline_map_page: OfflineMapPage
) -> None:
    """Download the region, cut the network: the tour and its profile answer."""
    page = offline_map_page.page
    subject = offline_map_page.subject
    offline_map_page.choose_basemap(subject.basemap_key)
    offline_map_page.select_region(subject.region_name)
    offline_map_page.download_selected_region()

    offline_map_page.go_offline()
    feature = page.evaluate(_FETCH_JS, f"/routes/ski-tours/{planted_tour}/feature.json")
    terrain = page.evaluate(_FETCH_JS, f"/routes/ski-tours/{planted_tour}/terrain.json")

    assert feature["status"] == 200 and feature["cache"] == "hit", (
        f"The tour planted in {subject.region_id} answered {feature['status']} "
        f"offline (cache {feature['cache']!r}). Seed {subject.seed}.\n  "
        + offline_map_page.diagnostics()
    )
    assert feature["body"] and feature["body"].get("feature"), feature
    assert terrain["status"] == 200 and terrain["cache"] == "hit", (
        f"The planted tour's profile answered {terrain['status']} offline "
        f"(cache {terrain['cache']!r}). Seed {subject.seed}."
    )
