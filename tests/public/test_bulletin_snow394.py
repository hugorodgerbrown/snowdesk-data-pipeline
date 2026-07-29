"""
tests/public/test_bulletin_snow394.py — SNOW-394 JSON-LD + JSON endpoints.

Covers three additions to the bulletin surface:

1. Extra JSON-LD fields on the bulletin page (``_build_structured_data``):
   ``dateModified`` (from ``bulletin.updated_at``), ``geo`` (from
   ``MicroRegion.centre``), and ``isBasedOn`` (from ``bulletin.pdf_url``).
2. Two ``<link rel="alternate" type="application/json">`` entries in the
   page head — one per per-bulletin JSON endpoint.
3. The two per-bulletin JSON endpoints themselves —
   ``/api/bulletins/<id>/`` returning the render model and
   ``/api/bulletins/<id>.caaml.json`` returning the raw CAAML in its
   GeoJSON Feature envelope. Both return 200 with a public
   ``Cache-Control``; unknown ids 404.

Fixtures are lightweight and self-contained rather than reusing the
``simple_bulletin`` / ``region`` fixtures from ``test_bulletin_page.py`` —
keeps this module's failure mode isolated from broader test-file drift.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.bulletins.models import Bulletin
from apps.regions.models import MicroRegion
from tests.factories import (
    BulletinFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    SubRegionFactory,
)

_JSONLD_RE = re.compile(
    r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def _extract_jsonld(content: str) -> dict[str, Any] | None:
    """Return parsed JSON-LD from a rendered HTML response, or ``None``."""
    match = _JSONLD_RE.search(content)
    if match is None:
        return None
    parsed: dict[str, Any] = json.loads(match.group(1).strip())
    return parsed


def _make_bulletin_with_region(
    *,
    pdf_url: str = "",
    centre: dict[str, float] | None = None,
    day: date | None = None,
) -> tuple[Bulletin, MicroRegion]:
    """Build a MicroRegion + a linked Bulletin with the given ``pdf_url``.

    ``centre`` overrides the factory default when supplied (so tests can
    assert the exact lat/lon in JSON-LD). ``day`` defaults to today so
    the evergreen URL resolves to this bulletin without needing a date
    segment.
    """
    major = MajorRegionFactory.create(
        prefix="CH-4",
        country="CH",
        name_native="Valais",
        name_en="Valais",
    )
    subregion = SubRegionFactory.create(major=major)
    kwargs: dict[str, Any] = dict(
        region_id="CH-4115",
        name="Martigny-Verbier",
        subregion=subregion,
    )
    if centre is not None:
        kwargs["centre"] = centre
    region = MicroRegionFactory.create(**kwargs)
    when = (
        datetime(2026, 3, 15, 6, 0, tzinfo=UTC)
        if day is None
        else datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)
    )
    bulletin = BulletinFactory.create(
        valid_from=when,
        valid_to=when + timedelta(days=1),
        issued_at=when,
        pdf_url=pdf_url,
    )
    RegionBulletinFactory.create(bulletin=bulletin, region=region)
    return bulletin, region


def _bulletin_url(region: MicroRegion, day: date | None = None) -> str:
    """Return the canonical form-3 URL for the bulletin page."""
    d = day or date(2026, 3, 15)
    return reverse(
        "public:bulletin_date",
        kwargs={
            "region_id": region.canonical_region_id,
            "slug": region.name_slug,
            "date_str": d.isoformat(),
        },
    )


# ---------------------------------------------------------------------------
# 1. JSON-LD extra fields
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStructuredDataExtraFields:
    """SNOW-394 additions to the schema.org JSON-LD payload."""

    def test_date_modified_reflects_bulletin_updated_at(self, client: Client) -> None:
        """dateModified serialises bulletin.updated_at, not just valid_from."""
        bulletin, region = _make_bulletin_with_region()
        response = client.get(_bulletin_url(region))
        assert response.status_code == 200
        data = _extract_jsonld(response.content.decode())
        assert data is not None
        report = data["mainEntity"]
        assert report["dateModified"] == bulletin.updated_at.isoformat()

    def test_geo_coordinates_from_region_centre(self, client: Client) -> None:
        """spatialCoverage.geo carries MicroRegion.centre as GeoCoordinates."""
        _, region = _make_bulletin_with_region(centre={"lon": 7.2, "lat": 46.1})
        response = client.get(_bulletin_url(region))
        data = _extract_jsonld(response.content.decode())
        assert data is not None
        geo = data["mainEntity"]["spatialCoverage"]["geo"]
        assert geo["@type"] == "GeoCoordinates"
        assert geo["latitude"] == 46.1
        assert geo["longitude"] == 7.2

    def test_geo_absent_when_centre_missing(self, client: Client) -> None:
        """No geo block emitted when MicroRegion.centre is unset."""
        # Pass an empty dict (not None) so factory doesn't fill the default;
        # the JSON-LD builder only emits geo when both keys are numeric.
        _, region = _make_bulletin_with_region(centre={})
        response = client.get(_bulletin_url(region))
        data = _extract_jsonld(response.content.decode())
        assert data is not None
        assert "geo" not in data["mainEntity"]["spatialCoverage"]

    def test_is_based_on_points_at_pdf_url(self, client: Client) -> None:
        """isBasedOn carries bulletin.pdf_url as a CreativeWork URL."""
        pdf = "https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/example.pdf"
        _, region = _make_bulletin_with_region(pdf_url=pdf)
        response = client.get(_bulletin_url(region))
        data = _extract_jsonld(response.content.decode())
        assert data is not None
        based_on = data["mainEntity"]["isBasedOn"]
        assert based_on["@type"] == "CreativeWork"
        assert based_on["url"] == pdf
        assert based_on["encodingFormat"] == "application/pdf"

    def test_is_based_on_absent_when_pdf_url_empty(self, client: Client) -> None:
        """No isBasedOn field when the bulletin has no pdf_url."""
        _, region = _make_bulletin_with_region(pdf_url="")
        response = client.get(_bulletin_url(region))
        data = _extract_jsonld(response.content.decode())
        assert data is not None
        assert "isBasedOn" not in data["mainEntity"]


# ---------------------------------------------------------------------------
# 2. rel=alternate JSON links on the bulletin page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRelAlternateJsonLinks:
    """SNOW-394: two rel=alternate application/json entries on bulletin.html."""

    def test_render_model_alternate_link_present(self, client: Client) -> None:
        """The render-model endpoint is advertised as rel=alternate."""
        bulletin, region = _make_bulletin_with_region()
        response = client.get(_bulletin_url(region))
        body = response.content.decode()
        expected_href = reverse(
            "api:bulletin_render_model",
            kwargs={"bulletin_id": bulletin.bulletin_id},
        )
        pattern = re.compile(
            r'<link[^>]*rel="alternate"[^>]*type="application/json"[^>]*'
            rf'href="{re.escape(expected_href)}"',
            re.DOTALL,
        )
        assert pattern.search(body), (
            "expected rel=alternate JSON link to the render-model endpoint"
        )

    def test_caaml_alternate_link_present(self, client: Client) -> None:
        """The CAAML endpoint is advertised as rel=alternate."""
        bulletin, region = _make_bulletin_with_region()
        response = client.get(_bulletin_url(region))
        body = response.content.decode()
        expected_href = reverse(
            "api:bulletin_caaml",
            kwargs={"bulletin_id": bulletin.bulletin_id},
        )
        pattern = re.compile(
            r'<link[^>]*rel="alternate"[^>]*type="application/json"[^>]*'
            rf'href="{re.escape(expected_href)}"',
            re.DOTALL,
        )
        assert pattern.search(body), (
            "expected rel=alternate JSON link to the CAAML endpoint"
        )


# ---------------------------------------------------------------------------
# 3. Per-bulletin JSON endpoints
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBulletinRenderModelEndpoint:
    """``/api/bulletins/<bulletin_id>/`` returns the render model."""

    def test_returns_200_with_render_model_body(self, client: Client) -> None:
        """A known bulletin returns 200 with its render_model in the body."""
        bulletin, _ = _make_bulletin_with_region()
        response = client.get(
            reverse(
                "api:bulletin_render_model",
                kwargs={"bulletin_id": bulletin.bulletin_id},
            )
        )
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        payload = json.loads(response.content.decode())
        assert payload == (bulletin.render_model or {})

    def test_returns_404_for_unknown_bulletin(self, client: Client) -> None:
        """An unknown bulletin_id returns 404 rather than an empty payload."""
        response = client.get(
            reverse(
                "api:bulletin_render_model",
                kwargs={"bulletin_id": "does-not-exist"},
            )
        )
        assert response.status_code == 404

    def test_cache_control_public(self, client: Client) -> None:
        """Response carries Cache-Control: public, max-age=300."""
        bulletin, _ = _make_bulletin_with_region()
        response = client.get(
            reverse(
                "api:bulletin_render_model",
                kwargs={"bulletin_id": bulletin.bulletin_id},
            )
        )
        cache_control = response.get("Cache-Control", "")
        assert "public" in cache_control
        assert "max-age=300" in cache_control


@pytest.mark.django_db
class TestBulletinCaamlEndpoint:
    """``/api/bulletins/<bulletin_id>.caaml.json`` returns the raw CAAML."""

    def test_returns_200_with_raw_data_body(self, client: Client) -> None:
        """A known bulletin returns 200 with its raw_data GeoJSON envelope."""
        bulletin, _ = _make_bulletin_with_region()
        response = client.get(
            reverse(
                "api:bulletin_caaml",
                kwargs={"bulletin_id": bulletin.bulletin_id},
            )
        )
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        payload = json.loads(response.content.decode())
        assert payload == (bulletin.raw_data or {})

    def test_returns_404_for_unknown_bulletin(self, client: Client) -> None:
        """An unknown bulletin_id returns 404."""
        response = client.get(
            reverse(
                "api:bulletin_caaml",
                kwargs={"bulletin_id": "does-not-exist"},
            )
        )
        assert response.status_code == 404

    def test_cache_control_public(self, client: Client) -> None:
        """Response carries Cache-Control: public, max-age=300."""
        bulletin, _ = _make_bulletin_with_region()
        response = client.get(
            reverse(
                "api:bulletin_caaml",
                kwargs={"bulletin_id": bulletin.bulletin_id},
            )
        )
        cache_control = response.get("Cache-Control", "")
        assert "public" in cache_control
        assert "max-age=300" in cache_control
