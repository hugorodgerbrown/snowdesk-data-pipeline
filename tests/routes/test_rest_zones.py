"""
tests/routes/test_rest_zones.py — FOEN's rest zones on the map (SNOW-1384).

The services in apps/routes/services/rest_zones.py: whether a protected
period covers a day, in a year and across the new year, with text it
cannot read taken to be in force; the rules page and its national
fallback; and one zone as the Feature the layer draws.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.utils import translation

from apps.routes.services.rest_zones import (
    NATIONAL_RULES_URL,
    in_force,
    rules_url,
    zone_feature,
)
from tests.factories import RestZoneFactory


@pytest.mark.parametrize(
    ("period", "day", "expected"),
    [
        # Across the new year: both ends, inside, and either side.
        ("21.12. - 30.04.", date(2026, 12, 21), True),
        ("21.12. - 30.04.", date(2027, 1, 1), True),
        ("21.12. - 30.04.", date(2027, 4, 30), True),
        ("21.12. - 30.04.", date(2027, 5, 1), False),
        ("21.12. - 30.04.", date(2026, 12, 20), False),
        ("21.12. - 30.04.", date(2026, 7, 1), False),
        # Inside one year.
        ("01.05. - 15.07.", date(2026, 5, 1), True),
        ("01.05. - 15.07.", date(2026, 7, 15), True),
        ("01.05. - 15.07.", date(2026, 7, 16), False),
        ("01.05. - 15.07.", date(2026, 4, 30), False),
        # A leap day is a day, and the 28th of a common year is before it.
        ("01.12. - 29.02.", date(2027, 2, 28), True),
        ("01.12. - 29.02.", date(2027, 3, 1), False),
    ],
)
def test_a_period_covers_its_days(period: str, day: date, expected: bool) -> None:
    """Inclusive at both ends, in one year or across the new year."""
    assert in_force(period, day) is expected


@pytest.mark.parametrize(
    "period",
    [
        "",
        "ganzjährig",
        "Winter",
        "31.04. - 30.05.",
        "01.13. - 01.01.",
        "20.12 - 30.04",
        "32.01. - 30.04.",
        "31.02. - 01.03.",
        "20.12. -",
        "01.05.\u201315.07.",
        "En cas de neige",
        "Betriebszeiten der Bahn",
        "16.12. bis Ende Skisaison",
        "keine definierte Periode",
    ],
)
def test_a_period_it_cannot_read_is_in_force(period: str) -> None:
    """No text, prose, or a day that does not exist: taken as protected."""
    assert in_force(period, date(2026, 8, 1)) is True


@pytest.mark.django_db
def test_the_rules_page_is_the_cantons_or_the_national_site() -> None:
    """A zone with no page of its own sends the reader to the national site."""
    with_page = RestZoneFactory.create(url="https://www.gr.ch/wildruhe")
    without = RestZoneFactory.create(url="")
    assert rules_url(with_page) == "https://www.gr.ch/wildruhe"
    assert rules_url(without) == NATIONAL_RULES_URL == "https://www.wildruhezonen.ch"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "www.gr.ch/wildruhe",
        "https://",
        "ftp://example.ch/zone",
    ],
)
def test_a_rules_page_that_is_not_http_is_the_national_site(url: str) -> None:
    """FOEN's url is outside data: only an absolute http(s) URL is passed on."""
    zone = RestZoneFactory.create()
    zone.url = url
    assert rules_url(zone) == NATIONAL_RULES_URL


@pytest.mark.django_db
def test_a_zone_is_drawn_as_a_feature() -> None:
    """Its uuid, its geometry, and what the sheet reads, on the map's day."""
    zone = RestZoneFactory.create(
        name="Piz Buin",
        binding=False,
        restriction={"de": "Betretungsverbot", "fr": "Interdiction"},
        period="21.12. - 30.04.",
        canton="GR",
        url="",
    )
    with translation.override("fr"):
        feature = zone_feature(zone, date(2027, 1, 10))
    assert feature == {
        "type": "Feature",
        "id": str(zone.source_uuid),
        "geometry": zone.geometry,
        "properties": {
            "name": "Piz Buin",
            "binding": False,
            "restriction": "Interdiction",
            "period": "21.12. - 30.04.",
            "in_force": True,
            "canton": "GR",
            "url": NATIONAL_RULES_URL,
        },
    }


@pytest.mark.django_db
def test_the_restriction_falls_back_to_german() -> None:
    """A language FOEN does not state the restriction in reads the German."""
    zone = RestZoneFactory.create(restriction={"de": "Betretungsverbot"})
    with translation.override("en"):
        props = zone_feature(zone, date(2026, 8, 1))["properties"]
    assert props["restriction"] == "Betretungsverbot"
    assert props["in_force"] is False


@pytest.mark.django_db
def test_a_zone_with_no_restriction_reads_empty() -> None:
    """No restriction at all is an empty line, not an error."""
    zone = RestZoneFactory.create(restriction={})
    assert zone_feature(zone, date(2026, 8, 1))["properties"]["restriction"] == ""
