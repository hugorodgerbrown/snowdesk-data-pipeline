"""
tests/regions/test_fixture_consistency.py — SNOW-178 regression guard.

Loads the committed ``regions/fixtures/eaws_ch.json`` and
``regions/fixtures/resorts.json`` fixtures and asserts that every geocoded
resort's (longitude, latitude) point lies inside its FK MicroRegion's
``boundary`` polygon.

This test exists to catch two classes of drift:
  1. SLF renames a region and our CSV/fixture falls out of sync with the
     bulletin ingest names (caught by ``audit_microregion_names``).
  2. A Resort's region FK points to the wrong polygon (caught by
     ``audit_resort_regions``).

If this test fails in CI, run:
  poetry run python manage.py audit_resort_regions --commit
  poetry run python manage.py loaddata regions/fixtures/resorts.json

then re-run the test to confirm the fix.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from regions.models import Resort


@pytest.mark.django_db(transaction=True)
def test_geocoded_resorts_inside_region_polygon() -> None:
    """Every geocoded resort's lat/lon must be inside its FK polygon.

    Loads the committed eaws_ch.json and resorts.json fixtures, then iterates
    every Resort with non-null latitude/longitude and asserts that
    shapely.geometry.Point(longitude, latitude).within(boundary_polygon)
    is True.

    Failures here mean the data fix (SNOW-178) has not been applied or
    a new Resort/region mis-alignment has been introduced.
    """
    from shapely.geometry import Point, shape

    # Load the committed fixtures into the test DB.
    call_command("loaddata", "regions/fixtures/eaws_ch.json", verbosity=0)
    call_command("loaddata", "regions/fixtures/resorts.json", verbosity=0)

    geocoded = list(
        Resort.objects.select_related("region")
        .filter(latitude__isnull=False, longitude__isnull=False)
        .order_by("name")
    )

    assert geocoded, "No geocoded resorts found — was the fixture loaded?"

    failures: list[str] = []

    for resort in geocoded:
        boundary = resort.region.boundary
        if boundary is None:
            # Skip: region has no polygon in the fixture (shouldn't happen
            # after the data fix, but we don't want a crash here).
            continue

        point = Point(resort.longitude, resort.latitude)
        polygon = shape(boundary)

        if not polygon.contains(point):
            failures.append(
                f"{resort.name!r}: ({resort.latitude}, {resort.longitude}) "
                f"is NOT inside {resort.region.region_id!r}"
            )

    assert not failures, (
        f"{len(failures)} resort(s) outside their FK polygon:\n"
        + "\n".join(failures)
        + "\n\nFix: poetry run python manage.py audit_resort_regions --commit"
        + "\n     poetry run python manage.py loaddata regions/fixtures/resorts.json"
    )
