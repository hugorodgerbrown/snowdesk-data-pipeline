"""
tests/public/test_bulletin_page.py — Tests for the WhiteRisk-replica bulletin template.

Covers structural assertions on the six sections of bulletin.html as rendered
by the bulletin_detail view.

Fixtures use the same helper pattern as test_bulletin_detail.py (AM bulletin
factories) to stay consistent with the existing test suite.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils.translation import override as language_override
from pytest_django.fixtures import Settings

from apps.accounts.models import Account
from apps.bulletins.models import Bulletin
from apps.bulletins.services.render_model import RENDER_MODEL_VERSION
from apps.public.views import BULLETIN_SOURCE_LINKS
from apps.regions.models import MicroRegion
from tests.factories import (
    AccountFactory,
    BulletinFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    SubRegionFactory,
    SubscriptionFactory,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_am_bulletin(region: MicroRegion, day: date, **kwargs: Any) -> Bulletin:
    """Create a morning bulletin valid from 06:00 to 15:00 on *day*."""
    vf = datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)
    vt = datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC)
    bulletin = BulletinFactory.create(
        issued_at=vf - timedelta(minutes=30),
        valid_from=vf,
        valid_to=vt,
        **kwargs,
    )
    RegionBulletinFactory.create(
        bulletin=bulletin,
        region=region,
        region_name_at_time=region.name,
    )
    return bulletin


def _render_model_with_traits(
    traits: list, metadata: dict | None = None, prose: dict | None = None
) -> dict:
    """Build a minimal current-version render_model dict for testing."""
    default_prose: dict = {
        "snowpack_structure": "<p>The snowpack is generally stable.</p>",
        "weather_review": None,
        "weather_forecast": None,
        "tendency": [],
        "avalanche_activity": {"highlights": "", "comment": ""},
        "tendency_lead": None,
    }
    # Merge caller-supplied prose over the default so callers don't need to
    # repeat keys they don't care about; always ensure avalanche_activity and
    # tendency_lead are present so the current-version check doesn't trigger
    # a rebuild.
    merged_prose = {**default_prose, **(prose or {})}
    if "avalanche_activity" not in merged_prose:
        merged_prose["avalanche_activity"] = {"highlights": "", "comment": ""}
    if "tendency_lead" not in merged_prose:
        merged_prose["tendency_lead"] = None
    return {
        "version": RENDER_MODEL_VERSION,
        "source": "SLF",
        "danger": {
            "key": "moderate",
            "number": "2",
            "subdivision": None,
            "ratings": [],
        },
        "danger_patterns": [],
        "traits": traits,
        "metadata": metadata
        or {
            "publication_time": "2026-03-15T06:00:00+00:00",
            "valid_from": "2026-03-15T06:00:00+00:00",
            "valid_until": "2026-03-15T15:00:00+00:00",
            "next_update": "2026-03-15T15:00:00+00:00",
            "unscheduled": False,
            "lang": "en",
        },
        "prose": merged_prose,
    }


def _dry_trait_problems(problems: list) -> dict:
    """Build a dry trait dict with structured-geography problems."""
    return {
        "category": "dry",
        "time_period": "all_day",
        "title": "Dry avalanches",
        "geography": {"source": "problems"},
        "problems": problems,
        "prose": None,
        "danger_level": 2,
    }


def _wet_trait_prose(prose: str) -> dict:
    """Build a wet trait dict with prose-only geography."""
    return {
        "category": "wet",
        "time_period": "later",
        "title": "Wet avalanches",
        "geography": {"source": "prose_only"},
        "problems": [],
        "prose": prose,
        "danger_level": 3,
    }


def _problem(
    problem_type: str = "wind_slab",
    comment_html: str = "<p>Wind slab comment text.</p>",
    aspects: list | None = None,
    elevation: dict | None = None,
) -> dict:
    """
    Build a raw render-model problem dict (as stored in DB, pre-enrichment).

    ``apps.public.views.enrich_render_model`` converts these to the richer shape
    expected by templates at render time.  Tests must store only JSON-safe
    structures in the DB.
    """
    return {
        "problem_type": problem_type,
        "comment_html": comment_html,
        "aspects": aspects if aspects is not None else ["N", "NE", "E"],
        "elevation": elevation
        if elevation is not None
        else {"lower": 2200, "upper": None, "treeline": False},
        "time_period": "all_day",
        "core_zone_text": None,
        "danger_rating_value": "moderate",
    }


def _problem_no_geo(
    problem_type: str = "wet_snow",
    comment_html: str = "<p>Wet snow comment.</p>",
) -> dict:
    """Build a raw render-model problem dict with no aspects or elevation."""
    return {
        "problem_type": problem_type,
        "comment_html": comment_html,
        "aspects": [],
        "elevation": None,
        "time_period": "all_day",
        "core_zone_text": None,
        "danger_rating_value": "moderate",
    }


def _rating(
    level: str, period: str = "all_day", subdivision: str | None = None
) -> dict:
    """Build a CAAML dangerRating dict for use in raw_data fixtures."""
    r: dict = {"mainValue": level, "validTimePeriod": period}
    if subdivision:
        r["customData"] = {"CH": {"subdivision": subdivision}}
    return r


def _raw_data_with_ratings(ratings: list[dict]) -> dict:
    """Build a minimal raw_data GeoJSON envelope with the given dangerRatings."""
    return {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "dangerRatings": ratings,
            "customData": {"CH": {}},
        },
    }


def _raw_problem(
    problem_type: str = "wind_slab",
    comment: str = "<p>Wind slab comment text.</p>",
    aspects: list | None = None,
    elevation: dict | None = None,
    danger_rating_value: str = "moderate",
    valid_time_period: str = "all_day",
) -> dict:
    """Build a raw CAAML avalancheProblems entry (as stored in raw_data.properties)."""
    return {
        "problemType": problem_type,
        "comment": comment,
        "aspects": aspects if aspects is not None else ["N", "NE", "E"],
        "elevation": elevation if elevation is not None else {"lowerBound": "2200"},
        "dangerRatingValue": danger_rating_value,
        "validTimePeriod": valid_time_period,
    }


def _raw_problem_no_geo(
    problem_type: str = "wet_snow",
    comment: str = "<p>Wet snow comment.</p>",
    danger_rating_value: str = "moderate",
) -> dict:
    """Build a raw CAAML problem with no aspects or elevation (prose-only)."""
    return {
        "problemType": problem_type,
        "comment": comment,
        "aspects": [],
        "elevation": None,
        "dangerRatingValue": danger_rating_value,
        "validTimePeriod": "all_day",
    }


def _raw_data_with_problems(
    problems: list[dict], ratings: list[dict] | None = None
) -> dict:
    """Build a minimal raw_data GeoJSON envelope with the given avalancheProblems.

    Auto-generates a one-entry-per-problem aggregation preserving the problems
    list order, and includes a default dangerRatings if none is given.
    """
    _WET_TYPES = {"wet_snow", "gliding_snow"}
    aggregation = [
        {
            "category": "wet" if p.get("problemType") in _WET_TYPES else "dry",
            "problemTypes": [p.get("problemType", "wind_slab")],
        }
        for p in problems
    ]
    return {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "dangerRatings": ratings or [{"mainValue": "moderate"}],
            "avalancheProblems": problems,
            "customData": {"CH": {"aggregation": aggregation}},
        },
    }


def _raw_data_with_aggregation(
    aggregation: list[dict],
    problems: list[dict],
    ratings: list[dict] | None = None,
) -> dict:
    """Build a raw_data envelope with both aggregation and avalancheProblems."""
    return {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "dangerRatings": ratings or [{"mainValue": "moderate"}],
            "avalancheProblems": problems,
            "customData": {"CH": {"aggregation": aggregation}},
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def region() -> MicroRegion:
    """Return a test Region."""
    return MicroRegionFactory.create(region_id="CH-4115", name="Valais", slug="ch-4115")


@pytest.fixture()
def simple_bulletin(region: MicroRegion) -> Bulletin:
    """A bulletin with one dry problem (simple day)."""
    day = date(2026, 3, 15)
    rm = _render_model_with_traits([_dry_trait_problems([_problem()])])
    raw = _raw_data_with_problems([_raw_problem()])
    return _make_am_bulletin(
        region,
        day,
        render_model=rm,
        render_model_version=RENDER_MODEL_VERSION,
        raw_data=raw,
    )


@pytest.fixture()
def variable_bulletin(region: MicroRegion) -> Bulletin:
    """A bulletin with two traits (variable day — dry morning, wet afternoon).

    raw_data carries matching dangerRatings so _build_day_windows() renders
    the panel from the authoritative CAAML source.
    """
    day = date(2026, 3, 15)
    dry_trait = _dry_trait_problems([_problem()])
    dry_trait["danger_level"] = 2

    wet_trait = {
        "category": "wet",
        "time_period": "later",
        "title": "Wet avalanches, as the day progresses",
        "geography": {"source": "problems"},
        "problems": [_problem(problem_type="wet_snow")],
        "prose": None,
        "danger_level": 3,
    }
    rm = _render_model_with_traits([dry_trait, wet_trait])
    raw_data = {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "dangerRatings": [
                _rating("moderate", "all_day"),
                _rating("considerable", "later"),
            ],
            "avalancheProblems": [
                _raw_problem(danger_rating_value="moderate"),
                _raw_problem(
                    problem_type="wet_snow",
                    danger_rating_value="considerable",
                    valid_time_period="later",
                ),
            ],
            "customData": {
                "CH": {
                    "aggregation": [
                        {"category": "dry", "problemTypes": ["wind_slab"]},
                        {"category": "wet", "problemTypes": ["wet_snow"]},
                    ]
                }
            },
        },
    }
    return _make_am_bulletin(
        region,
        day,
        render_model=rm,
        render_model_version=RENDER_MODEL_VERSION,
        raw_data=raw_data,
    )


def _url(region_id: str, slug: str, date_str: str) -> str:
    """Build the bulletin date URL."""
    return reverse(
        "public:bulletin_date",
        kwargs={"region_id": region_id, "slug": slug, "date_str": date_str},
    )


# ---------------------------------------------------------------------------
# Test: template used is bulletin.html
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTemplateName:
    """bulletin_detail always renders public/bulletin.html."""

    def test_renders_bulletin_html(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """The view renders public/bulletin.html."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        assert response.status_code == 200
        assert "public/bulletin.html" in [t.name for t in response.templates]


# ---------------------------------------------------------------------------
# Test: rating blocks count matches traits
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRatingBlockCount:
    """Number of rendered rating blocks equals number of traits."""

    def test_one_trait_one_block(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """One trait produces exactly one rating block."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="rating-block"') == 1

    def test_two_traits_two_blocks(
        self, client: Client, variable_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """Two traits produce exactly two rating blocks."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="rating-block"') == 2


# ---------------------------------------------------------------------------
# Test: aspect/elevation row presence
# ---------------------------------------------------------------------------
# Test: aspect clockwise ordering — _enrich_avalanche_problem (SNOW-297)
# ---------------------------------------------------------------------------


class TestEnrichAvalancheProblemAspectOrder:
    """Aspects are sorted clockwise by _enrich_avalanche_problem (SNOW-297)."""

    def _enrich(self, aspects: list[str]) -> dict[str, Any]:
        """Call _enrich_avalanche_problem with a minimal CAAML problem dict."""
        from apps.public.views import _enrich_avalanche_problem

        problem = {
            "problemType": "wind_slab",
            "validTimePeriod": "all_day",
            "aspects": aspects,
            "comment": "",
            "elevation": None,
        }
        return _enrich_avalanche_problem(problem, [problem], 0)

    def test_out_of_order_aspects_sorted_clockwise(self) -> None:
        """Out-of-order aspects are reordered to the canonical clockwise sequence."""
        result = self._enrich(["E", "NE", "W", "N", "NW"])
        assert result["aspects"] == ["N", "NE", "E", "W", "NW"]

    def test_all_eight_aspects_preserves_length(self) -> None:
        """All eight aspects yield a length-8 list (drives 'All aspects' guard)."""
        all_aspects = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"]
        result = self._enrich(all_aspects)
        assert len(result["aspects"]) == 8

    def test_empty_aspects_remain_empty(self) -> None:
        """Empty aspect list is returned unchanged without error."""
        result = self._enrich([])
        assert result["aspects"] == []


# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAspectElevationRow:
    """Aspect/elevation row is present when the first problem has aspects or elevation."""

    def test_row_present_when_problem_has_aspects_and_elevation(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """Rating block has aspect/elevation row when the first problem has geographic data."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="aspect-elevation-row"' in content

    def test_row_absent_when_problem_has_no_aspects_or_elevation(
        self, client: Client, region: MicroRegion
    ) -> None:
        """No aspect/elevation row when the first problem has neither aspects nor elevation."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_problems([_raw_problem_no_geo(problem_type="wet_snow")])
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="aspect-elevation-row"' not in content

    def test_row_absent_when_aspects_empty_but_elevation_present(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Elevation alone is enough to show the row; empty aspects list is ignored."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_problems(
            [_raw_problem(aspects=[], elevation={"lowerBound": "2200"})]
        )
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="aspect-elevation-row"' in content


# ---------------------------------------------------------------------------
# Test: SLF prose in full, no truncation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProseFull:
    """Problem prose comment appears verbatim and in full in the output."""

    def test_full_prose_comment_rendered(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The full text of a problem's comment appears verbatim in the response."""
        day = date(2026, 3, 15)
        full_prose = (
            "<p>Wind slabs have formed on the lee side of ridges and in gullies. "
            "They can be released even by low additional loads. "
            "Careful route selection is essential. "
            "Particularly dangerous are north and east facing slopes above 2200m.</p>"
        )
        raw = _raw_data_with_problems([_raw_problem(comment=full_prose)])
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        # The prose is sanitised by snowdesk_html but the text content remains
        assert "Wind slabs have formed on the lee side of ridges" in content
        assert "Careful route selection is essential" in content
        assert (
            "Particularly dangerous are north and east facing slopes above 2200m"
            in content
        )


# ---------------------------------------------------------------------------
# Test: snowpack/weather section sub-block skipping
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSnowpackWeatherSection:
    """Sub-blocks are skipped entirely when their source is None/empty."""

    def test_weather_review_skipped_when_none(
        self, client: Client, region: MicroRegion
    ) -> None:
        """No 'Weather review' heading when prose.weather_review is None."""
        day = date(2026, 3, 15)
        prose: dict = {
            "snowpack_structure": "<p>Some snowpack text.</p>",
            "weather_review": None,
            "weather_forecast": None,
            "tendency": [],
        }
        rm = _render_model_with_traits(
            [_dry_trait_problems([_problem()])],
            prose=prose,
        )
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="weather-review-heading"' not in content

    def test_weather_review_rendered_when_present(
        self, client: Client, region: MicroRegion
    ) -> None:
        """prose.weather_review content appears in the Snowpack & Weather section."""
        day = date(2026, 3, 15)
        prose: dict = {
            "snowpack_structure": None,
            "weather_review": "<p>Cold and clear overnight. 5cm new snow at altitude.</p>",
            "weather_forecast": None,
            "tendency": [],
        }
        rm = _render_model_with_traits(
            [_dry_trait_problems([_problem()])],
            prose=prose,
        )
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="snowpack-weather-section"' in content
        assert "Cold and clear overnight" in content

    def test_snowpack_section_absent_when_all_prose_empty(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Entire snowpack/weather section absent when all prose fields are None."""
        day = date(2026, 3, 15)
        prose: dict = {
            "snowpack_structure": None,
            "weather_review": None,
            "weather_forecast": None,
            "tendency": [],
        }
        rm = _render_model_with_traits(
            [_dry_trait_problems([_problem()])],
            prose=prose,
        )
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="snowpack-weather-section"' not in content

    def test_weather_forecast_rendered_when_present(
        self, client: Client, region: MicroRegion
    ) -> None:
        """prose.weather_forecast content appears in the Snowpack & Weather section."""
        day = date(2026, 3, 15)
        prose: dict = {
            "snowpack_structure": None,
            "weather_review": None,
            "weather_forecast": "<p>Warm and sunny tomorrow. Rain below 2000m.</p>",
            "tendency": [],
        }
        rm = _render_model_with_traits(
            [_dry_trait_problems([_problem()])],
            prose=prose,
        )
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="snowpack-weather-section"' in content
        assert "Warm and sunny tomorrow" in content

    def test_outlook_rendered_from_tendency(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Tendency comments render inside the Snowpack & Weather section."""
        day = date(2026, 3, 15)
        prose: dict = {
            "snowpack_structure": None,
            "weather_review": None,
            "weather_forecast": None,
            "tendency": [
                {
                    "comment": "<p>Hazard will increase over the coming days.</p>",
                    "tendency_type": "increasing",
                    "valid_from": None,
                    "valid_until": None,
                }
            ],
        }
        rm = _render_model_with_traits(
            [_dry_trait_problems([_problem()])],
            prose=prose,
        )
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="snowpack-weather-section"' in content
        assert "Hazard will increase over the coming days" in content


# ---------------------------------------------------------------------------
# Test: metadata strip None timestamps render as —
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMetadataStrip:
    """Metadata strip renders em-dash for None timestamp fields."""

    def test_none_next_update_renders_em_dash(
        self, client: Client, region: MicroRegion
    ) -> None:
        """When metadata.next_update is None, the next-update cell shows —."""
        day = date(2026, 3, 15)
        metadata = {
            "publication_time": "2026-03-15T06:00:00+00:00",
            "valid_from": "2026-03-15T06:00:00+00:00",
            "valid_until": "2026-03-15T15:00:00+00:00",
            "next_update": None,
            "unscheduled": False,
            "lang": "en",
        }
        rm = _render_model_with_traits(
            [_dry_trait_problems([_problem()])],
            metadata=metadata,
        )
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        # Em-dash character should appear in the next-update cell
        assert "—" in content or "&mdash;" in content

    def test_valid_timestamps_render(self, client: Client, region: MicroRegion) -> None:
        """Valid ISO timestamps in metadata are rendered as formatted dates."""
        day = date(2026, 3, 15)
        metadata = {
            "publication_time": "2026-03-15T06:00:00+00:00",
            "valid_from": "2026-03-15T06:00:00+00:00",
            "valid_until": "2026-03-15T15:00:00+00:00",
            "next_update": "2026-03-15T15:00:00+00:00",
            "unscheduled": False,
            "lang": "en",
        }
        rm = _render_model_with_traits(
            [_dry_trait_problems([_problem()])],
            metadata=metadata,
        )
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="metadata-strip"' in content
        # "06:00" appears in the issued/valid fields
        assert "06:00" in content
        # "15:00" appears in the valid-until and next-update fields
        assert "15:00" in content

    def test_source_cell_renders_link_for_known_source(
        self, client: Client, region: MicroRegion
    ) -> None:
        """When render_model.source is a known value, source-cell shows a link."""
        day = date(2026, 3, 15)
        # _render_model_with_traits already defaults source to "slf".
        rm = _render_model_with_traits([_dry_trait_problems([_problem()])])
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()

        assert 'data-testid="source-cell"' in content
        assert 'href="https://www.slf.ch"' in content
        assert ">SLF<" in content

    def test_source_cell_renders_em_dash_for_missing_source(
        self, client: Client, region: MicroRegion
    ) -> None:
        """When render_model has no source key, source-cell shows an em-dash."""
        day = date(2026, 3, 15)
        rm = _render_model_with_traits([_dry_trait_problems([_problem()])])
        # Remove the source key to simulate a bulletin without a known source.
        rm.pop("source", None)
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()

        assert 'data-testid="source-cell"' in content
        # Extract only the source cell fragment to avoid false positives from
        # the SLF attribution link in the global site footer.
        source_cell_start = content.find('data-testid="source-cell"')
        # The next sibling div starts just after the closing tag of the source cell.
        source_cell_end = content.find("</div>", source_cell_start) + len("</div>")
        source_cell_html = content[source_cell_start:source_cell_end]
        assert "—" in source_cell_html or "&mdash;" in source_cell_html
        assert "<a" not in source_cell_html


# ---------------------------------------------------------------------------
# Test: bulletin page no longer renders a per-page footer (SNOW-80)
# ---------------------------------------------------------------------------
#
# The bulletin's section 6 footer (focal region label, SLF feedback link,
# DEBUG admin shortcut) was removed in SNOW-80 — the global
# ``_site_footer.html`` already carries the SLF licence attribution, so the
# per-page footer was duplicating context. Adjacent regions are reachable
# via the SNOW-81 deep-link in the masthead, so ``related_regions`` no
# longer needs to be displayed in template chrome either.
#
# Global site-footer coverage lives in ``test_slf_attribution.py``.


@pytest.mark.django_db
class TestNoBulletinPageFooter:
    """SNOW-80: the bulletin page no longer renders its own footer landmark.

    The global ``data-testid="site-footer"`` block from base.html still
    renders (covered by test_slf_attribution.py); only the page-local
    section 6 footer was removed.
    """

    def test_no_page_footer_landmark(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """The page-local footer landmark is gone."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="page-footer"' not in content
        assert 'data-testid="focal-region"' not in content

    def test_sibling_regions_not_rendered_anywhere_on_page(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A sibling region's name does not appear in the rendered HTML.

        On a multi-region bulletin, the focal region's bulletin page
        must not name the other regions covered by the same bulletin —
        adjacent regions are surfaced from the map, not the bulletin.
        """
        day = date(2026, 3, 15)
        rm = _render_model_with_traits([_dry_trait_problems([_problem()])])
        bulletin = _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )
        other_region = MicroRegionFactory.create(name="Münstertal", slug="ch-4116")
        RegionBulletinFactory.create(
            bulletin=bulletin,
            region=other_region,
            region_name_at_time="Münstertal",
        )

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert "Münstertal" not in content


@pytest.mark.django_db
class TestRegionNameSource:
    """The page header uses the EAWS canonical name, not SLF's per-bulletin label.

    SLF's CAAML payload includes a ``name`` for every region entry inside a
    bulletin's ``regions[]`` array. That label is **not** the EAWS canonical
    name — SLF sometimes uses a sub-region or marketing label that disagrees
    with the EAWS reference data we load from the fixture. Previously the
    view fell back to ``RegionBulletin.region_name_at_time`` (the stored SLF
    label) and only used ``region.name`` when the column was empty; that
    produced visibly-wrong headers like "Stoos" on the page for CH-2133
    (whose EAWS name is "Küssnacht - Arth"). This test locks the post-fix
    behaviour: when the two disagree, the EAWS canonical name wins.
    """

    def test_header_uses_eaws_canonical_name_not_slf_label(
        self, client: Client, region: MicroRegion
    ) -> None:
        """region.name is shown on the page header even when region_name_at_time disagrees."""
        rm = _render_model_with_traits([_dry_trait_problems([_problem()])])
        bulletin = BulletinFactory.create(
            issued_at=datetime(2026, 3, 15, 6, 0, tzinfo=UTC) - timedelta(minutes=30),
            valid_from=datetime(2026, 3, 15, 6, 0, tzinfo=UTC),
            valid_to=datetime(2026, 3, 15, 15, 0, tzinfo=UTC),
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
        )
        # SLF labels this region "Stoos" in its CAAML payload, but the
        # EAWS canonical name (loaded from the fixture into ``region.name``)
        # is "Valais". The page header must show the EAWS canonical name.
        RegionBulletinFactory.create(
            bulletin=bulletin,
            region=region,
            region_name_at_time="Stoos",
        )

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()

        # Header carries the canonical name…
        assert (
            ">\n                Valais\n            <" in content or "Valais" in content
        )
        # …and never the disagreeing SLF label.
        assert "Stoos" not in content


# ---------------------------------------------------------------------------
# Test: font-sans class on outermost container
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTypography:
    """The outermost container carries font-sans to prevent serif leakage."""

    def test_font_sans_on_container(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """The outermost container has the font-sans class."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        # The outermost div must carry font-sans
        assert 'class="font-sans' in content


# ---------------------------------------------------------------------------
# Test: X-Bulletin-Id header and DEBUG raw-data embed
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDebuggingAids:
    """
    The bulletin page always carries an ``X-Bulletin-Id`` header so
    operators can identify the rendered row from network tools.  When
    ``settings.DEBUG`` is True (and a bulletin is present) the raw CAAML
    ``raw_data`` is embedded as a ``<script type="application/json">``
    tag for source-level inspection; the tag is absent in production.
    """

    def test_x_bulletin_id_header_present(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """Response carries the bulletin UUID in ``X-Bulletin-Id``."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        assert response.status_code == 200
        assert response["X-Bulletin-Id"] == str(simple_bulletin.bulletin_id)

    def test_x_bulletin_id_header_absent_on_empty_state(
        self, client: Client, region: MicroRegion
    ) -> None:
        """No bulletin → no ``X-Bulletin-Id`` header."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        assert response.status_code == 200
        assert "X-Bulletin-Id" not in response

    def test_raw_data_embedded_when_debug_true(
        self, client: Client, region: MicroRegion, settings: Settings
    ) -> None:
        """DEBUG=True → raw_data JSON embedded in page source."""
        settings.DEBUG = True
        day = date(2026, 3, 17)
        bulletin = _make_am_bulletin(
            region,
            day,
            render_model=_render_model_with_traits([_dry_trait_problems([_problem()])]),
            render_model_version=RENDER_MODEL_VERSION,
            raw_data={"properties": {"bulletinID": "sentinel-uuid-12345"}},
        )
        url = _url("ch-4115", "valais", "2026-03-17")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # The raw-data block now carries a CSP nonce attribute, so match
        # on the stable id= marker rather than the full opening tag.
        assert 'id="bulletin-raw-data"' in content
        assert 'type="application/json"' in content
        assert "sentinel-uuid-12345" in content
        # Header still present.
        assert response["X-Bulletin-Id"] == str(bulletin.bulletin_id)

    def test_raw_data_absent_when_debug_false(
        self,
        client: Client,
        simple_bulletin: Bulletin,
        region: MicroRegion,
        settings: Settings,
    ) -> None:
        """DEBUG=False → no raw_data script tag, header still present."""
        settings.DEBUG = False
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="bulletin-raw-data"' not in content
        assert response["X-Bulletin-Id"] == str(simple_bulletin.bulletin_id)

    def test_script_breakout_payload_is_escaped(
        self, client: Client, region: MicroRegion, settings: Settings
    ) -> None:
        """A ``</script>`` substring in raw_data must not break out of the tag."""
        settings.DEBUG = True
        day = date(2026, 3, 18)
        _make_am_bulletin(
            region,
            day,
            render_model=_render_model_with_traits([_dry_trait_problems([_problem()])]),
            render_model_version=RENDER_MODEL_VERSION,
            raw_data={"properties": {"comment": "hostile </script><b>pwn</b>"}},
        )
        url = _url("ch-4115", "valais", "2026-03-18")
        response = client.get(url)
        content = response.content.decode()
        # The literal ``</script>`` must not appear inside the raw-data
        # block — it must be escaped as ``<\/script>``. The block carries
        # a CSP nonce so start from the id= marker and find the end of
        # that specific opening tag.
        id_pos = content.index('id="bulletin-raw-data"')
        start = content.index(">", id_pos) + 1
        end = content.index("</script>", start)
        embedded = content[start:end]
        assert "</script>" not in embedded
        assert "<\\/script>" in embedded

    def test_raw_data_embedded_for_superuser_without_debug(
        self,
        client: Client,
        region: MicroRegion,
        settings: Settings,
    ) -> None:
        """Superuser sees raw_data script even when DEBUG=False."""
        settings.DEBUG = False
        superuser = AccountFactory.create(
            user__email="super@example.com",
            user__is_superuser=True,
            user__is_staff=True,
        )
        day = date(2026, 3, 19)
        _make_am_bulletin(
            region,
            day,
            render_model=_render_model_with_traits([_dry_trait_problems([_problem()])]),
            render_model_version=RENDER_MODEL_VERSION,
            raw_data={"properties": {"bulletinID": "superuser-raw-check"}},
        )
        client.force_login(superuser.user)
        url = _url("ch-4115", "valais", "2026-03-19")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="bulletin-raw-data"' in content
        assert "superuser-raw-check" in content

    def test_raw_data_absent_for_regular_user_without_debug(
        self,
        client: Client,
        region: MicroRegion,
        settings: Settings,
    ) -> None:
        """Regular (non-superuser) authenticated user does not see raw_data."""
        settings.DEBUG = False
        user = AccountFactory.create(
            user__email="regular@example.com",
            user__is_superuser=False,
            user__is_staff=False,
        )
        day = date(2026, 3, 20)
        _make_am_bulletin(
            region,
            day,
            render_model=_render_model_with_traits([_dry_trait_problems([_problem()])]),
            render_model_version=RENDER_MODEL_VERSION,
            raw_data={"properties": {"bulletinID": "regular-hidden-check"}},
        )
        client.force_login(user.user)
        url = _url("ch-4115", "valais", "2026-03-20")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="bulletin-raw-data"' not in content

    def test_raw_data_absent_for_anon_without_debug(
        self,
        client: Client,
        region: MicroRegion,
        settings: Settings,
    ) -> None:
        """Anonymous visitor does not see raw_data script when DEBUG=False."""
        settings.DEBUG = False
        day = date(2026, 3, 21)
        _make_am_bulletin(
            region,
            day,
            render_model=_render_model_with_traits([_dry_trait_problems([_problem()])]),
            render_model_version=RENDER_MODEL_VERSION,
            raw_data={"properties": {"bulletinID": "anon-hidden-check"}},
        )
        url = _url("ch-4115", "valais", "2026-03-21")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="bulletin-raw-data"' not in content


# ---------------------------------------------------------------------------
# Test: superuser debug affordances (SNOW-295)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSuperuserDebugAffordances:
    """
    Superuser-only debug block: PDF link + raw-JSON ``<details>`` viewer.
    Only rendered when ``request.user.is_superuser`` is True.
    """

    def test_debug_block_visible_for_superuser(
        self,
        client: Client,
        region: MicroRegion,
        settings: Settings,
    ) -> None:
        """Superuser sees the debug block with the correct data-testid."""
        settings.DEBUG = False
        superuser = AccountFactory.create(
            user__email="super2@example.com",
            user__is_superuser=True,
            user__is_staff=True,
        )
        day = date(2026, 3, 22)
        _make_am_bulletin(
            region,
            day,
            render_model=_render_model_with_traits([_dry_trait_problems([_problem()])]),
            render_model_version=RENDER_MODEL_VERSION,
        )
        client.force_login(superuser.user)
        url = _url("ch-4115", "valais", "2026-03-22")
        response = client.get(url)
        assert response.status_code == 200
        assert 'data-testid="superuser-debug"' in response.content.decode()

    def test_pdf_link_present_when_pdf_url_set(
        self,
        client: Client,
        region: MicroRegion,
        settings: Settings,
    ) -> None:
        """PDF link is rendered when bulletin.pdf_url is truthy."""
        settings.DEBUG = False
        superuser = AccountFactory.create(
            user__email="super3@example.com",
            user__is_superuser=True,
            user__is_staff=True,
        )
        day = date(2026, 3, 23)
        _make_am_bulletin(
            region,
            day,
            render_model=_render_model_with_traits([_dry_trait_problems([_problem()])]),
            render_model_version=RENDER_MODEL_VERSION,
            pdf_url="https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/test.pdf",
        )
        client.force_login(superuser.user)
        url = _url("ch-4115", "valais", "2026-03-23")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "www.slf.ch" in content
        assert "View source PDF" in content

    def test_pdf_link_absent_when_pdf_url_empty(
        self,
        client: Client,
        region: MicroRegion,
        settings: Settings,
    ) -> None:
        """PDF link is not rendered when bulletin.pdf_url is empty."""
        settings.DEBUG = False
        superuser = AccountFactory.create(
            user__email="super4@example.com",
            user__is_superuser=True,
            user__is_staff=True,
        )
        day = date(2026, 3, 24)
        _make_am_bulletin(
            region,
            day,
            render_model=_render_model_with_traits([_dry_trait_problems([_problem()])]),
            render_model_version=RENDER_MODEL_VERSION,
            pdf_url="",
        )
        client.force_login(superuser.user)
        url = _url("ch-4115", "valais", "2026-03-24")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "View source PDF" not in content

    def test_debug_block_absent_for_anon(
        self,
        client: Client,
        region: MicroRegion,
        settings: Settings,
    ) -> None:
        """Anonymous visitor does not see the debug block."""
        settings.DEBUG = False
        day = date(2026, 3, 25)
        _make_am_bulletin(
            region,
            day,
            render_model=_render_model_with_traits([_dry_trait_problems([_problem()])]),
            render_model_version=RENDER_MODEL_VERSION,
            pdf_url="https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/test.pdf",
        )
        url = _url("ch-4115", "valais", "2026-03-25")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="superuser-debug"' not in content
        assert "View source PDF" not in content

    def test_debug_block_absent_for_non_superuser(
        self,
        client: Client,
        region: MicroRegion,
        settings: Settings,
    ) -> None:
        """Staff-but-not-superuser does not see the debug block."""
        settings.DEBUG = False
        staff_user = AccountFactory.create(
            user__email="staffonly@example.com",
            user__is_superuser=False,
            user__is_staff=True,
        )
        day = date(2026, 3, 26)
        _make_am_bulletin(
            region,
            day,
            render_model=_render_model_with_traits([_dry_trait_problems([_problem()])]),
            render_model_version=RENDER_MODEL_VERSION,
            pdf_url="https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/test.pdf",
        )
        client.force_login(staff_user.user)
        url = _url("ch-4115", "valais", "2026-03-26")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="superuser-debug"' not in content


# ---------------------------------------------------------------------------
# Test: rating-block grouping and ordering (SNOW-135)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRatingBlockGrouping:
    """
    _group_avalanche_problems sorts by danger level high-to-low (tiebreak:
    kind order dry → wet → gliding) and clusters consecutive (kind,
    danger_level) pairs into one card each.
    """

    def test_single_problem_produces_one_block(
        self, client: Client, region: MicroRegion
    ) -> None:
        """One raw problem → one rating block."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_problems([_raw_problem()])
        _make_am_bulletin(region, day, raw_data=raw)
        response = client.get(_url("ch-4115", "valais", "2026-03-15"))
        assert response.content.decode().count('data-testid="rating-block"') == 1

    def test_two_problems_same_kind_and_level_produce_two_blocks(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Two problems with same (kind, danger_level) → 2 separate cards."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_problems(
            [
                _raw_problem(problem_type="wind_slab", danger_rating_value="moderate"),
                _raw_problem(problem_type="new_snow", danger_rating_value="moderate"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert content.count('data-testid="rating-block"') == 2
        assert "Wind slab" in content
        assert "New snow" in content

    def test_same_level_different_kind_produces_two_blocks_dry_before_wet(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Same danger level, dry vs wet → 2 cards; dry appears first (kind tiebreak)."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_problems(
            [
                _raw_problem(problem_type="wind_slab", danger_rating_value="moderate"),
                _raw_problem(problem_type="wet_snow", danger_rating_value="moderate"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert content.count('data-testid="rating-block"') == 2
        # Scope to the problems section to avoid matching labels embedded in
        # the DEBUG raw-data JSON script block that appears earlier in the page.
        probs_start = content.index('data-testid="avalanche-problems-heading"')
        dry_idx = content.index("Wind slab", probs_start)
        wet_idx = content.index("Wet snow", probs_start)
        assert dry_idx < wet_idx

    def test_different_levels_produces_two_blocks_high_danger_first(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Higher danger level appears first — aggregation order drives display order."""
        day = date(2026, 3, 15)
        # Put the higher-danger problem first in the aggregation (and problems list).
        raw = _raw_data_with_problems(
            [
                _raw_problem(
                    problem_type="wet_snow", danger_rating_value="considerable"
                ),
                _raw_problem(problem_type="wind_slab", danger_rating_value="low"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert content.count('data-testid="rating-block"') == 2
        # Scope to the problems section to avoid matching labels embedded in
        # the DEBUG raw-data JSON script block that appears earlier in the page.
        probs_start = content.index('data-testid="avalanche-problems-heading"')
        wet_idx = content.index("Wet snow", probs_start)
        dry_idx = content.index("Wind slab", probs_start)
        assert wet_idx < dry_idx

    def test_three_problems_produce_three_blocks_in_order(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Three problems → 3 cards in aggregation order (highest danger first)."""
        day = date(2026, 3, 15)
        # Put wet_snow (considerable) first in the aggregation order.
        raw = _raw_data_with_problems(
            [
                _raw_problem(
                    problem_type="wet_snow", danger_rating_value="considerable"
                ),
                _raw_problem(problem_type="wind_slab", danger_rating_value="moderate"),
                _raw_problem(problem_type="new_snow", danger_rating_value="moderate"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert content.count('data-testid="rating-block"') == 3
        # wet/considerable ranks highest, so it appears before the two dry/moderate cards.
        # Scope search to the problems section to avoid the DEBUG JSON embed.
        probs_start = content.index('data-testid="avalanche-problems-heading"')
        wet_idx = content.index("Wet snow", probs_start)
        wind_idx = content.index("Wind slab", probs_start)
        assert wet_idx < wind_idx

    def test_prose_only_problem_shows_no_aspect_elevation_row(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Problem with no aspects and no elevation → no aspect/elevation row."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_problems([_raw_problem_no_geo(problem_type="wet_snow")])
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert 'data-testid="rating-block"' in content
        assert 'data-testid="aspect-elevation-row"' not in content

    def test_problem_labels_appear_in_cards(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Each problem type's display label appears in its card header."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_problems(
            [
                _raw_problem(problem_type="wind_slab", danger_rating_value="moderate"),
                _raw_problem(problem_type="wet_snow", danger_rating_value="low"),
                _raw_problem(problem_type="gliding_snow", danger_rating_value="low"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert content.count('data-testid="rating-block"') == 3
        assert "Wind slab" in content
        assert "Wet snow" in content
        assert "Gliding snow" in content

    def test_empty_problems_shows_no_problems_card(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Bulletin with avalancheProblems=[] → 'No avalanche problems reported.' empty state."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_problems([])
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert 'data-testid="rating-block"' not in content
        assert "No avalanche problems reported." in content


# ---------------------------------------------------------------------------
# Test: prose-only empty-state three-way branches (SNOW-263)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProseOnlyEmptyState:
    """
    Prose-only problems (no structured aspects or elevation) render one of
    two empty-state rows depending on whether detect_prose_spatial fires:

    - prose mentions scope  → data-testid="aspect-elevation-fallback"
    - no spatial scope      → data-testid="aspect-elevation-allscope"
    - structured data       → data-testid="aspect-elevation-row" (regression)
    """

    def test_structured_geography_shows_aspect_elevation_row(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Problem with aspects and elevation → the structured rose/chip row."""
        day = date(2026, 3, 15)
        rm = _render_model_with_traits(
            [
                _dry_trait_problems(
                    [
                        _problem(
                            aspects=["N", "NE"],
                            elevation={"lower": 2200, "upper": None, "treeline": False},
                        )
                    ]
                )
            ]
        )
        raw = _raw_data_with_problems([_raw_problem()])
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
            raw_data=raw,
        )
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert 'data-testid="aspect-elevation-row"' in content
        assert 'data-testid="aspect-elevation-fallback"' not in content
        assert 'data-testid="aspect-elevation-allscope"' not in content

    def test_prose_mentions_spatial_shows_fallback_row(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Prose-only problem whose comment mentions scope → 'See description below'."""
        day = date(2026, 3, 15)
        # comment_html includes north-facing and an elevation token so
        # detect_prose_spatial returns True.
        spatial_comment = (
            "<p>Wet-snow slides likely on steep north-facing slopes "
            "between approximately 2000 and 2400 m.</p>"
        )
        rm = _render_model_with_traits(
            [
                {
                    "category": "wet",
                    "time_period": "all_day",
                    "title": "Wet avalanches",
                    "geography": {"source": "prose_only"},
                    "problems": [
                        _problem_no_geo(
                            problem_type="wet_snow", comment_html=spatial_comment
                        )
                    ],
                    "prose": None,
                    "danger_level": 2,
                }
            ]
        )
        raw = _raw_data_with_problems(
            [_raw_problem_no_geo(problem_type="wet_snow", comment=spatial_comment)]
        )
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
            raw_data=raw,
        )
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert 'data-testid="aspect-elevation-fallback"' in content
        assert "See description below" in content
        assert 'data-testid="aspect-elevation-row"' not in content
        assert 'data-testid="aspect-elevation-allscope"' not in content

    def test_no_spatial_scope_shows_allscope_row(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Prose-only problem with generic comment → 'All aspects · all elevations'."""
        day = date(2026, 3, 15)
        # Generic no-scope template; detect_prose_spatial must return False.
        generic_comment = "<p>Moist snow slides expected as the day warms.</p>"
        rm = _render_model_with_traits(
            [
                {
                    "category": "wet",
                    "time_period": "all_day",
                    "title": "Wet avalanches",
                    "geography": {"source": "prose_only"},
                    "problems": [
                        _problem_no_geo(
                            problem_type="wet_snow", comment_html=generic_comment
                        )
                    ],
                    "prose": None,
                    "danger_level": 2,
                }
            ]
        )
        raw = _raw_data_with_problems(
            [_raw_problem_no_geo(problem_type="wet_snow", comment=generic_comment)]
        )
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
            raw_data=raw,
        )
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert 'data-testid="aspect-elevation-allscope"' in content
        assert "All aspects" in content
        assert 'data-testid="aspect-elevation-row"' not in content
        assert 'data-testid="aspect-elevation-fallback"' not in content


# ---------------------------------------------------------------------------
# Test: aggregation-driven card ordering and titles (SNOW-135)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAggregationDriven:
    """
    When customData.CH.aggregation is present, cards are built from it:
    one card per (aggregation entry, problem type), in aggregation order.
    """

    def test_aggregation_order_preserved(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Cards appear in aggregation order, not sorted by danger level."""
        day = date(2026, 3, 15)
        # aggregation lists wet first (at low), dry second (at considerable)
        raw = _raw_data_with_aggregation(
            aggregation=[
                {
                    "category": "wet",
                    "validTimePeriod": "all_day",
                    "problemTypes": ["wet_snow"],
                },
                {
                    "category": "dry",
                    "validTimePeriod": "all_day",
                    "problemTypes": ["wind_slab"],
                },
            ],
            problems=[
                _raw_problem(problem_type="wet_snow", danger_rating_value="low"),
                _raw_problem(
                    problem_type="wind_slab", danger_rating_value="considerable"
                ),
            ],
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        # aggregation order (wet first) overrides the fallback danger-level sort.
        # Scope search to the problems section to avoid the DEBUG JSON embed.
        probs_start = content.index('data-testid="avalanche-problems-heading"')
        wet_idx = content.index("Wet snow", probs_start)
        dry_idx = content.index("Wind slab", probs_start)
        assert wet_idx < dry_idx

    def test_two_problems_in_one_entry_produce_one_card_with_combined_label(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Two problem types in one aggregation entry → one card with combined label."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_aggregation(
            aggregation=[
                {
                    "category": "wet",
                    "validTimePeriod": "later",
                    "problemTypes": ["wet_snow", "gliding_snow"],
                    "title": "Wet-snow and gliding avalanches, later",
                }
            ],
            problems=[
                _raw_problem(
                    problem_type="wet_snow", danger_rating_value="considerable"
                ),
                _raw_problem(
                    problem_type="gliding_snow", danger_rating_value="moderate"
                ),
            ],
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert content.count('data-testid="rating-block"') == 1
        probs_start = content.index('data-testid="avalanche-problems-heading"')
        assert "Wet snow + Gliding snow" in content[probs_start:]

    def test_core_zone_text_as_aria_label(
        self, client: Client, region: MicroRegion
    ) -> None:
        """coreZoneText from customData.CH appears as aria-label on aspect/elevation row."""
        day = date(2026, 3, 15)
        core_text = "Danger level moderate in N to E facing aspects above 2000m."
        raw = _raw_data_with_aggregation(
            aggregation=[
                {
                    "category": "dry",
                    "validTimePeriod": "all_day",
                    "problemTypes": ["wind_slab"],
                }
            ],
            problems=[
                {
                    "problemType": "wind_slab",
                    "comment": "<p>Wind slab hazard.</p>",
                    "aspects": ["N", "NE", "E"],
                    "elevation": {"lowerBound": "2000"},
                    "dangerRatingValue": "moderate",
                    "validTimePeriod": "all_day",
                    "customData": {"CH": {"coreZoneText": core_text}},
                }
            ],
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert f'aria-label="{core_text}"' in content


# ---------------------------------------------------------------------------
# Test: Day Windows panel (SNOW-70 — design_handoff_day_windows)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDayWindowsPanel:
    """
    Day Windows panel — the day's hazard summary above the rating blocks.

    Driven directly from the bulletin's CAAML ``dangerRatings`` field.
    Always one row for the ``all_day_*`` rating; optionally a second row
    for the ``later_*`` rating when it differs meaningfully. Captions are
    absent; each row is badge + rating-name + chip.
    """

    def test_default_renders_panel(
        self, client: Client, variable_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """The day-windows panel renders by default — no headline band."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="day-windows-panel"' in content
        assert 'data-testid="headline-band"' not in content

    def test_renders_day_risk_profile_heading_above_panel(
        self, client: Client, variable_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """The 'Day Risk Profile' h2 sits above the day-windows panel."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="day-risk-profile-heading"' in content
        assert "Day Risk Profile" in content
        # Heading must precede the panel in DOM order.
        heading_idx = content.index('data-testid="day-risk-profile-heading"')
        panel_idx = content.index('data-testid="day-windows-panel"')
        assert heading_idx < panel_idx

    def test_all_day_only_renders_single_row(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A bulletin with only an all_day rating renders one row, and no chip.

        all_day is the default window: with one row there is nothing for a
        label to distinguish it from (SNOW-727).
        """
        day = date(2026, 3, 19)
        raw = _raw_data_with_ratings([_rating("moderate", "all_day")])
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-19")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 1
        assert 'data-window="all_day"' in content
        assert 'data-testid="day-window-pill"' not in content

    def test_two_row_pills_read_all_day_and_later(
        self, client: Client, region: MicroRegion
    ) -> None:
        """all_day + later → two rows, and only the 'later' row takes a chip.

        The all_day row is the baseline the later row departs from, so
        labelling it adds nothing (SNOW-727).
        """
        day = date(2026, 3, 22)
        raw = _raw_data_with_ratings(
            [
                _rating("low", "all_day"),
                _rating("moderate", "later"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-22")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 2
        all_day_idx = content.index('data-window="all_day"')
        later_idx = content.index('data-window="later"')
        assert all_day_idx < later_idx
        panel_start = content.index('data-testid="day-windows-panel"')
        panel_end = content.index('data-testid="avalanche-problems-heading"')
        panel_html = content[panel_start:panel_end]
        assert panel_html.count('data-testid="day-window-pill"') == 1
        assert ">Later<" in panel_html
        assert ">All day<" not in panel_html
        assert ">Earlier<" not in panel_html

    def test_tile_carries_lv_class_and_level_number(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The numbered tile uses ``lv-{level}`` so EAWS tokens drive the colour."""
        day = date(2026, 3, 20)
        raw = _raw_data_with_ratings([_rating("considerable", "all_day")])
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-20")
        response = client.get(url)
        content = response.content.decode()
        assert "dw-tile lv-considerable" in content
        assert "Considerable" in content

    def test_caption_is_absent(self, client: Client, region: MicroRegion) -> None:
        """No dw-caption element renders — captions are dropped in this design."""
        day = date(2026, 3, 21)
        raw = _raw_data_with_ratings([_rating("considerable", "all_day")])
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-21")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 1
        assert "dw-caption" not in content

    # ------------------------------------------------------------------
    # Badge display — sublevel modifier
    # ------------------------------------------------------------------

    def test_single_all_day_considerable_badge(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Single all_day considerable → badge '3', chip 'All day'."""
        day = date(2026, 3, 23)
        raw = _raw_data_with_ratings([_rating("considerable", "all_day")])
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-23")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 1
        assert "dw-tile lv-considerable" in content
        assert ">3<" in content
        # all_day carries no pill — it is the default window (SNOW-727).
        assert 'data-testid="day-window-pill"' not in content

    def test_sublevel_modifier_minus_on_badge(
        self, client: Client, region: MicroRegion
    ) -> None:
        """all_day moderate minus → badge '2-' in the tile."""
        day = date(2026, 3, 24)
        raw = _raw_data_with_ratings([_rating("moderate", "all_day", "minus")])
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-24")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 1
        assert "dw-tile lv-moderate" in content
        assert ">2-<" in content

    # ------------------------------------------------------------------
    # later_ filter — cross-category (always shown)
    # ------------------------------------------------------------------

    def test_cross_category_later_up_renders_two_rows(
        self, client: Client, region: MicroRegion
    ) -> None:
        """all_day low + later moderate (cross-category up) → 2 rows."""
        day = date(2026, 3, 25)
        raw = _raw_data_with_ratings(
            [
                _rating("low", "all_day"),
                _rating("moderate", "later"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-25")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 2
        assert "lv-low" in content
        assert "lv-moderate" in content

    def test_cross_category_later_two_level_jump_renders_two_rows(
        self, client: Client, region: MicroRegion
    ) -> None:
        """all_day low + later considerable (two-level jump) → 2 rows."""
        day = date(2026, 3, 26)
        raw = _raw_data_with_ratings(
            [
                _rating("low", "all_day"),
                _rating("considerable", "later"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-26")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 2

    def test_cross_category_later_down_shows_two_rows(
        self, client: Client, region: MicroRegion
    ) -> None:
        """all_day considerable minus + later moderate (cross-band lower) → 2 rows (SNOW-291).

        The strictly-greater gate has been dropped: any later period is now
        always shown, including when the afternoon level is lower than the
        morning level.  The flat-but-split case (same level, different problem
        mix) is the primary motivation, but the same logic applies here.
        """
        day = date(2026, 3, 27)
        raw = _raw_data_with_ratings(
            [
                _rating("considerable", "all_day", "minus"),
                _rating("moderate", "later"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-27")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 2

    # ------------------------------------------------------------------
    # later_ filter — within-category sublevel shift (always shown)
    # ------------------------------------------------------------------

    def test_within_category_later_up_renders_two_rows_with_badge_differential(
        self, client: Client, region: MicroRegion
    ) -> None:
        """all_day considerable minus + later considerable → 2 rows, badges '3-' / '3'."""
        day = date(2026, 3, 28)
        raw = _raw_data_with_ratings(
            [
                _rating("considerable", "all_day", "minus"),
                _rating("considerable", "later"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-28")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 2
        # Both rows use the same level CSS.
        assert content.count("lv-considerable") == 2
        # Badge differential: the all_day tile shows the minus suffix.
        assert ">3-<" in content
        # The later tile has no suffix.
        panel_start = content.index('data-testid="day-windows-panel"')
        panel_end = content.index('data-testid="avalanche-problems-heading"')
        panel_html = content[panel_start:panel_end]
        assert ">3<" in panel_html

    def test_within_category_later_down_shows_two_rows(
        self, client: Client, region: MicroRegion
    ) -> None:
        """all_day moderate plus + later moderate minus (within-band lower) → 2 rows (SNOW-291).

        The strictly-greater gate has been dropped: any later period is always
        shown regardless of whether the afternoon subdivision is lower.
        """
        day = date(2026, 3, 29)
        raw = _raw_data_with_ratings(
            [
                _rating("moderate", "all_day", "plus"),
                _rating("moderate", "later", "minus"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-29")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 2

    # ------------------------------------------------------------------
    # later_ filter — same-band no-op (filtered)
    # ------------------------------------------------------------------

    def test_same_band_noop_considerable_shows_two_rows(
        self, client: Client, region: MicroRegion
    ) -> None:
        """all_day considerable neutral + later considerable → 2 rows (SNOW-291).

        The strictly-greater gate is dropped: flat-but-split days (same level
        AM/PM, different problem mix) now show two rows.
        """
        day = date(2026, 3, 30)
        raw = _raw_data_with_ratings(
            [
                _rating("considerable", "all_day", "neutral"),
                _rating("considerable", "later"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-30")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 2

    def test_same_band_noop_moderate_shows_two_rows(
        self, client: Client, region: MicroRegion
    ) -> None:
        """all_day moderate neutral + later moderate → 2 rows (SNOW-291).

        The strictly-greater gate is dropped: flat-but-split days now show two
        rows even when the danger level does not change between AM and PM.
        """
        day = date(2026, 3, 31)
        raw = _raw_data_with_ratings(
            [
                _rating("moderate", "all_day", "neutral"),
                _rating("moderate", "later"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-31")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 2

    # ------------------------------------------------------------------
    # later_ filter — cross-band lower (always suppressed)
    # ------------------------------------------------------------------

    def test_cross_band_lower_considerable_to_moderate_shows_two_rows(
        self, client: Client, region: MicroRegion
    ) -> None:
        """all_day considerable + later moderate (lower band) → 2 rows (SNOW-291).

        The strictly-greater gate is dropped: later periods are always shown.
        """
        day = date(2026, 4, 1)
        raw = _raw_data_with_ratings(
            [
                _rating("considerable", "all_day"),
                _rating("moderate", "later"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-04-01")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 2

    def test_same_band_plus_blocks_plain_later_shows_two_rows(
        self, client: Client, region: MicroRegion
    ) -> None:
        """all_day moderate plus + later moderate plain → 2 rows (SNOW-291).

        The strictly-greater gate is dropped: later periods are always shown
        even when the afternoon subdivision is lower.
        """
        day = date(2026, 4, 2)
        raw = _raw_data_with_ratings(
            [
                _rating("moderate", "all_day", "plus"),
                _rating("moderate", "later"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-04-02")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 2

    def test_same_band_minus_to_plain_shows_two_rows(
        self, client: Client, region: MicroRegion
    ) -> None:
        """all_day moderate minus + later moderate plain (higher sub) → 2 rows."""
        day = date(2026, 4, 3)
        raw = _raw_data_with_ratings(
            [
                _rating("moderate", "all_day", "minus"),
                _rating("moderate", "later"),
            ]
        )
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-04-03")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 2


# ---------------------------------------------------------------------------
# Test: Day Risk Profile elevation-band split (ALBINA)
# ---------------------------------------------------------------------------


class TestDayWindowsElevationSplit:
    """
    Unit tests for the elevation-band split applied to ALBINA's per-period
    danger ratings by ``_day_windows_from_rm_ratings``.

    ALBINA pairs every banded rating ("below X" + "above X" at the same
    pivot); when the two bands disagree the day-windows panel should surface
    both as separate rows with elevation captions. When they agree, the
    split is suppressed because the elevation differentiation carries no
    information. SLF is never affected because it publishes a single rating
    per period.
    """

    def _rm_rating(
        self,
        key: str,
        period: str = "all_day",
        *,
        lower: int | None = None,
        upper: int | None = None,
        treeline_side: str | None = None,
    ) -> dict[str, Any]:
        """Build a single projected ``danger.ratings`` entry."""
        elevation: dict[str, Any] | None = None
        if lower is not None or upper is not None or treeline_side is not None:
            elevation = {
                "lower": lower,
                "upper": upper,
                "treeline": treeline_side is not None,
                "treeline_side": treeline_side,
            }
        return {
            "period": period,
            "key": key,
            "subdivision": None,
            "elevation": elevation,
        }

    def test_single_rating_per_period_unchanged(self) -> None:
        """One rating in a period → one row with empty caption (SLF baseline)."""
        from apps.public.views import _day_windows_from_rm_ratings

        rows = _day_windows_from_rm_ratings([self._rm_rating("moderate")])
        assert len(rows) == 1
        assert rows[0]["level_key"] == "moderate"
        assert rows[0]["caption"] == ""

    def test_matching_bands_collapse_to_single_row(self) -> None:
        """Two band ratings with same level → one row, no elevation caption."""
        from apps.public.views import _day_windows_from_rm_ratings

        rows = _day_windows_from_rm_ratings(
            [
                self._rm_rating("considerable", upper=2200),  # below 2200
                self._rm_rating("considerable", lower=2200),  # above 2200
            ]
        )
        assert len(rows) == 1
        assert rows[0]["caption"] == ""

    def test_differing_numeric_bands_emit_two_rows(self) -> None:
        """Differing band levels with numeric pivot → two rows, ordered low→high."""
        from apps.public.views import _day_windows_from_rm_ratings

        rows = _day_windows_from_rm_ratings(
            [
                self._rm_rating("considerable", lower=2200),  # above 2200 (upper band)
                self._rm_rating("moderate", upper=2200),  # below 2200 (lower band)
            ]
        )
        assert len(rows) == 2
        # Lower band emitted first regardless of source order.
        assert rows[0]["level_key"] == "moderate"
        assert "2200" in rows[0]["caption"]
        assert rows[1]["level_key"] == "considerable"
        assert "2200" in rows[1]["caption"]
        # Captions differ — the two bands carry distinct elevation wording.
        assert rows[0]["caption"] != rows[1]["caption"]

    def test_treeline_pivot_emits_distinct_captions(self) -> None:
        """Treeline-pivoted bands produce 'below treeline' / 'above treeline'."""
        from apps.public.views import _day_windows_from_rm_ratings

        rows = _day_windows_from_rm_ratings(
            [
                # "above treeline" — treeline was the lowerBound.
                self._rm_rating("considerable", treeline_side="lower"),
                # "below treeline" — treeline was the upperBound.
                self._rm_rating("low", treeline_side="upper"),
            ]
        )
        assert len(rows) == 2
        assert rows[0]["level_key"] == "low"  # below treeline first
        assert "treeline" in rows[0]["caption"]
        assert rows[1]["level_key"] == "considerable"
        assert "treeline" in rows[1]["caption"]
        assert rows[0]["caption"] != rows[1]["caption"]

    def test_albina_no_all_day_emits_per_period(self) -> None:
        """No ``all_day`` rating → emit earlier then later, each potentially split."""
        from apps.public.views import _day_windows_from_rm_ratings

        rows = _day_windows_from_rm_ratings(
            [
                self._rm_rating("low", period="earlier"),
                self._rm_rating("moderate", period="later", upper=2200),
                self._rm_rating("considerable", period="later", lower=2200),
            ]
        )
        # 1 earlier row + 2 later band rows.
        assert len(rows) == 3
        assert rows[0]["type"] == "earlier"
        assert rows[1]["type"] == "later"
        assert rows[2]["type"] == "later"
        # Later band rows are split with captions.
        assert rows[1]["caption"] != ""
        assert rows[2]["caption"] != ""

    def test_all_day_split_with_later_overlay(self) -> None:
        """A split all_day with a higher later overlay emits both, in order."""
        from apps.public.views import _day_windows_from_rm_ratings

        rows = _day_windows_from_rm_ratings(
            [
                self._rm_rating("low", upper=2200),  # all_day, below 2200
                self._rm_rating("moderate", lower=2200),  # all_day, above 2200
                self._rm_rating("considerable", period="later"),
            ]
        )
        # 2 all_day band rows + 1 later row (peak rank > all_day peak).
        assert len(rows) == 3
        assert rows[0]["type"] == "all_day"
        assert rows[1]["type"] == "all_day"
        assert rows[2]["type"] == "later"
        assert rows[2]["caption"] == ""

    # ------------------------------------------------------------------
    # Suppression of stray unbanded ratings (SNOW-292)
    # ------------------------------------------------------------------

    def test_unbanded_suppressed_when_banded_present(self) -> None:
        """Stray unbanded rating is dropped when banded ratings co-exist in same period.

        ALBINA can emit a triple like [considerable/below-2400, moderate/above-2400,
        low/no-elevation]. The banded pair already partitions the whole mountain; the
        unbanded 'low' is redundant and must be suppressed so only 2 rows render.
        """
        from apps.public.views import _day_windows_from_rm_ratings

        rows = _day_windows_from_rm_ratings(
            [
                self._rm_rating("considerable", upper=2400),  # below 2400
                self._rm_rating("moderate", lower=2400),  # above 2400
                self._rm_rating("low"),  # no elevation — stray unbanded
            ]
        )
        assert len(rows) == 2
        keys = [r["level_key"] for r in rows]
        assert "considerable" in keys
        assert "moderate" in keys
        assert "low" not in keys
        # Both surviving rows carry elevation captions.
        for row in rows:
            assert row["caption"] != ""

    def test_only_unbanded_rating_kept_unchanged(self) -> None:
        """When the period has no banded ratings, an unbanded rating is kept as-is.

        SLF all_day regression: a single unbanded rating must not be suppressed.
        """
        from apps.public.views import _day_windows_from_rm_ratings

        rows = _day_windows_from_rm_ratings([self._rm_rating("moderate")])
        assert len(rows) == 1
        assert rows[0]["level_key"] == "moderate"

    def test_banded_only_kept_unchanged(self) -> None:
        """Two banded ratings with no unbanded entry are not affected by the rule."""
        from apps.public.views import _day_windows_from_rm_ratings

        rows = _day_windows_from_rm_ratings(
            [
                self._rm_rating("considerable", lower=2200),  # above 2200
                self._rm_rating("moderate", upper=2200),  # below 2200
            ]
        )
        assert len(rows) == 2
        keys = [r["level_key"] for r in rows]
        assert "considerable" in keys
        assert "moderate" in keys


# ---------------------------------------------------------------------------
# Test: MF elevation-band split — full-page render (SNOW-293)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMFElevationBandSplitBulletinPage:
    """
    Full-render test for Météo-France bulletins with an elevation-band split.

    Builds a factory MF bulletin whose ``danger.ratings`` list carries two
    ``all_day`` entries with different keys (low below 2400 m / moderate above
    2400 m), renders the bulletin page, and asserts that the Day Risk Profile
    panel shows two rows with the expected ``level_key``s and elevation
    captions.
    """

    def _make_mf_band_split_bulletin(self, region: MicroRegion) -> Bulletin:
        """
        Create a morning MF bulletin with two all_day elevation-band ratings.

        Returns:
            A Bulletin with render_model containing two danger.ratings entries
            for distinct elevation bands (low below / moderate above 2400 m)
            and two ``all_day`` traits at the same levels.

        """
        day = date(2026, 3, 15)
        vf = datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)
        vt = datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC)

        rm = {
            "version": RENDER_MODEL_VERSION,
            "source": "METEOFRANCE",
            "danger": {
                "key": "moderate",
                "number": "2",
                "subdivision": None,
                "ratings": [
                    {
                        "period": "all_day",
                        "key": "low",
                        "subdivision": None,
                        "elevation": {
                            "lower": None,
                            "upper": 2400,
                            "treeline": False,
                            "treeline_side": None,
                        },
                    },
                    {
                        "period": "all_day",
                        "key": "moderate",
                        "subdivision": None,
                        "elevation": {
                            "lower": 2400,
                            "upper": None,
                            "treeline": False,
                            "treeline_side": None,
                        },
                    },
                ],
            },
            "danger_patterns": [],
            "traits": [
                {
                    "category": "wet",
                    "time_period": "all_day",
                    "title": "Wet avalanches",
                    "geography": {"source": "problems"},
                    "problems": [],
                    "prose": None,
                    "danger_level": 2,
                }
            ],
            "metadata": {
                "publication_time": "2026-03-15T06:00:00+00:00",
                "valid_from": "2026-03-15T06:00:00+00:00",
                "valid_until": "2026-03-15T15:00:00+00:00",
                "next_update": None,
                "unscheduled": False,
                "lang": "fr",
            },
            "prose": {
                "snowpack_structure": None,
                "weather_review": None,
                "weather_forecast": None,
                "tendency": [],
                "avalanche_activity": {
                    "highlights": "Risque de plaques.",
                    "comment": "",
                },
                "tendency_lead": None,
            },
        }
        bulletin = BulletinFactory.create(
            issued_at=vf - timedelta(minutes=30),
            valid_from=vf,
            valid_to=vt,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
        )
        RegionBulletinFactory.create(
            bulletin=bulletin,
            region=region,
            region_name_at_time=region.name,
        )
        return bulletin

    def test_day_risk_panel_row_level_keys(self) -> None:
        """Two-row MF panel has 'low' (lower band) and 'moderate' (upper band)."""
        from apps.public.views import _day_windows_from_rm_ratings

        rm_ratings = [
            {
                "period": "all_day",
                "key": "low",
                "subdivision": None,
                "elevation": {
                    "lower": None,
                    "upper": 2400,
                    "treeline": False,
                    "treeline_side": None,
                },
            },
            {
                "period": "all_day",
                "key": "moderate",
                "subdivision": None,
                "elevation": {
                    "lower": 2400,
                    "upper": None,
                    "treeline": False,
                    "treeline_side": None,
                },
            },
        ]
        rows = _day_windows_from_rm_ratings(rm_ratings)
        # Lower band first (sorted by elevation), upper band second.
        assert rows[0]["level_key"] == "low"
        assert rows[1]["level_key"] == "moderate"

    def test_day_risk_panel_elevation_captions(self) -> None:
        """Both rows carry non-empty elevation captions with '2400' in them."""
        from apps.public.views import _day_windows_from_rm_ratings

        rm_ratings = [
            {
                "period": "all_day",
                "key": "low",
                "subdivision": None,
                "elevation": {
                    "lower": None,
                    "upper": 2400,
                    "treeline": False,
                    "treeline_side": None,
                },
            },
            {
                "period": "all_day",
                "key": "moderate",
                "subdivision": None,
                "elevation": {
                    "lower": 2400,
                    "upper": None,
                    "treeline": False,
                    "treeline_side": None,
                },
            },
        ]
        rows = _day_windows_from_rm_ratings(rm_ratings)
        # Both rows should carry elevation captions.
        assert "2400" in rows[0]["caption"]
        assert "2400" in rows[1]["caption"]
        # Captions differ — "below 2400 m" vs "above 2400 m".
        assert rows[0]["caption"] != rows[1]["caption"]

    def test_full_page_renders_day_windows_panel(self, client: Client) -> None:
        """Full-page render of an MF bulletin includes the day-windows panel."""
        # MicroRegionFactory auto-creates a linked SubRegion (via subregion
        # SubFactory), so no manual SubRegion creation is needed.
        region = MicroRegionFactory.create(region_id="CH-4115")
        self._make_mf_band_split_bulletin(region)
        url = _url("ch-4115", region.slug, "2026-03-15")
        # follow=True handles any slug-canonicalisation redirect.
        response = client.get(url, follow=True)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="day-windows-panel"' in content


# ---------------------------------------------------------------------------
# Test: day-window elevation bounds — band metadata for the glyph
# ---------------------------------------------------------------------------


class TestDayWindowsElevationBounds:
    """Unit tests for the elevation metadata attached to banded day-window rows.

    Banded periods attach an ``ElevationBounds`` (carrying a ``bound_type``)
    to each row so the panel can render the mountain elevation glyph via the
    ``elevation_icon`` filter; single rows leave it unset. ``bound_type`` is
    ``LOWER`` for an "above X" band and ``UPPER`` for a "below X" band.
    """

    def _rm_rating(
        self,
        key: str,
        *,
        lower: int | None = None,
        upper: int | None = None,
        treeline_side: str | None = None,
    ) -> dict[str, Any]:
        """Build a single projected ``danger.ratings`` entry."""
        elevation: dict[str, Any] | None = None
        if lower is not None or upper is not None or treeline_side is not None:
            elevation = {
                "lower": lower,
                "upper": upper,
                "treeline": treeline_side is not None,
                "treeline_side": treeline_side,
            }
        return {
            "period": "all_day",
            "key": key,
            "subdivision": None,
            "elevation": elevation,
        }

    def test_above_band_is_lower_bound_type(self) -> None:
        """A lowerBound-only band ("above X") resolves to bound_type LOWER."""
        from apps.public.views import _rm_elevation_bounds

        bounds = _rm_elevation_bounds(
            {"lower": 2400, "upper": None, "treeline": False, "treeline_side": None}
        )
        assert bounds.bound_type == "LOWER"
        assert "above" in bounds.display

    def test_below_band_is_upper_bound_type(self) -> None:
        """An upperBound-only band ("below X") resolves to bound_type UPPER."""
        from apps.public.views import _rm_elevation_bounds

        bounds = _rm_elevation_bounds(
            {"lower": None, "upper": 2400, "treeline": False, "treeline_side": None}
        )
        assert bounds.bound_type == "UPPER"
        assert "below" in bounds.display

    def test_treeline_side_is_reconstructed(self) -> None:
        """The treeline token is put back on the correct bound for the glyph."""
        from apps.public.views import _rm_elevation_bounds

        above = _rm_elevation_bounds(
            {"lower": None, "upper": None, "treeline": True, "treeline_side": "lower"}
        )
        assert above.bound_type == "LOWER"
        assert "treeline" in above.display
        below = _rm_elevation_bounds(
            {"lower": None, "upper": None, "treeline": True, "treeline_side": "upper"}
        )
        assert below.bound_type == "UPPER"
        assert "treeline" in below.display

    def test_empty_elevation_is_falsey(self) -> None:
        """A missing elevation yields an empty (falsey) ElevationBounds."""
        from apps.public.views import _rm_elevation_bounds

        assert not _rm_elevation_bounds(None)

    def test_banded_rows_carry_elevation_bounds(self) -> None:
        """Each row of a banded pair carries an ElevationBounds with a bound_type."""
        from apps.public.views import _day_windows_from_rm_ratings

        rows = _day_windows_from_rm_ratings(
            [
                self._rm_rating("considerable", lower=2400),  # above 2400
                self._rm_rating("moderate", upper=2400),  # below 2400
            ]
        )
        assert len(rows) == 2
        bound_types = {row["elevation_bounds"].bound_type for row in rows}
        assert bound_types == {"LOWER", "UPPER"}

    def test_single_row_has_no_elevation_bounds(self) -> None:
        """An unbanded (single) row leaves ``elevation_bounds`` unset — no glyph."""
        from apps.public.views import _day_windows_from_rm_ratings

        rows = _day_windows_from_rm_ratings([self._rm_rating("moderate")])
        assert len(rows) == 1
        assert "elevation_bounds" not in rows[0]


# ---------------------------------------------------------------------------
# Test: banded full-page render — two glyph rows vs plain chip
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDayWindowsBandedRender:
    """Full-page render assertions for the banded elevation path.

    Banded bulletins (two same-type rows from ``_rows_for_period``) render two
    chip rows, each carrying the mountain elevation glyph
    (``data-testid="day-window-elevation-icon"``); single-band bulletins keep
    the plain chip row with no glyph (SNOW-298).
    """

    def _make_banded_bulletin(self, region: MicroRegion) -> Bulletin:
        """Create a bulletin with two all_day elevation-band ratings."""
        from apps.bulletins.services.render_model import RENDER_MODEL_VERSION

        day = date(2026, 3, 15)
        vf = datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)
        vt = datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC)
        rm = {
            "version": RENDER_MODEL_VERSION,
            "source": "METEOFRANCE",
            "danger": {
                "key": "moderate",
                "number": "2",
                "subdivision": None,
                "ratings": [
                    {
                        "period": "all_day",
                        "key": "low",
                        "subdivision": None,
                        "elevation": {
                            "lower": None,
                            "upper": 2400,
                            "treeline": False,
                            "treeline_side": None,
                        },
                    },
                    {
                        "period": "all_day",
                        "key": "moderate",
                        "subdivision": None,
                        "elevation": {
                            "lower": 2400,
                            "upper": None,
                            "treeline": False,
                            "treeline_side": None,
                        },
                    },
                ],
            },
            "danger_patterns": [],
            "traits": [],
            "metadata": {
                "publication_time": "2026-03-15T06:00:00+00:00",
                "valid_from": "2026-03-15T06:00:00+00:00",
                "valid_until": "2026-03-15T15:00:00+00:00",
                "next_update": None,
                "unscheduled": False,
                "lang": "fr",
            },
            "prose": {
                "snowpack_structure": None,
                "weather_review": None,
                "weather_forecast": None,
                "tendency": [],
                "avalanche_activity": {"highlights": "", "comment": ""},
                "tendency_lead": None,
            },
        }
        bulletin = BulletinFactory.create(
            issued_at=vf - timedelta(minutes=30),
            valid_from=vf,
            valid_to=vt,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
        )
        RegionBulletinFactory.create(
            bulletin=bulletin,
            region=region,
            region_name_at_time=region.name,
        )
        return bulletin

    def test_banded_bulletin_renders_two_rows_with_glyphs(self, client: Client) -> None:
        """A banded bulletin renders two chip rows, each with the elevation glyph."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        self._make_banded_bulletin(region)
        url = _url("ch-4115", region.slug, "2026-03-15")
        response = client.get(url, follow=True)
        assert response.status_code == 200
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 2
        assert content.count('data-testid="day-window-elevation-icon"') == 2
        # The old pyramid markup must be gone.
        assert 'data-testid="day-window-pyramid"' not in content

    def test_banded_bulletin_tiles_carry_band_lv_classes(self, client: Client) -> None:
        """Each banded row keeps its EAWS-coloured level tile (``dw-tile lv-*``)."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        self._make_banded_bulletin(region)
        url = _url("ch-4115", region.slug, "2026-03-15")
        response = client.get(url, follow=True)
        content = response.content.decode()
        assert "dw-tile lv-low" in content
        assert "dw-tile lv-moderate" in content

    def test_banded_bulletin_captions_render(self, client: Client) -> None:
        """The elevation captions (built from the pivot, '2400') still render."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        self._make_banded_bulletin(region)
        url = _url("ch-4115", region.slug, "2026-03-15")
        response = client.get(url, follow=True)
        content = response.content.decode()
        assert "2400" in content

    def test_unbanded_bulletin_renders_chip_row_without_glyph(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A single-band bulletin keeps the chip row and renders no elevation glyph."""
        day = date(2026, 3, 19)
        raw = _raw_data_with_ratings([_rating("moderate", "all_day")])
        _make_am_bulletin(region, day, raw_data=raw)
        url = _url("ch-4115", "valais", "2026-03-19")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="day-window-row"' in content
        assert 'data-testid="day-window-elevation-icon"' not in content


# ---------------------------------------------------------------------------
# Test: bulletin page content — subregion names, day-risk panel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBulletinPageContent:
    """
    Miscellaneous content assertions for the bulletin page that are not
    tied to a specific partial — subregion name resolution and the
    day-risk-profile panel that sits below the header.
    """

    def test_subregion_uses_english_name_when_present(
        self, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """``SubRegion.name_en`` wins over native when SLF publishes one."""
        sub = region.subregion
        sub.name_en = "Lower Valais"
        sub.name_native = "Bas-Valais"
        sub.save(update_fields=["name_en", "name_native"])
        url = _url("ch-4115", "valais", "2026-03-15")
        response = Client().get(url)
        content = response.content.decode()
        assert "Lower Valais" in content

    def test_subregion_falls_back_to_native_when_english_blank(
        self, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """When ``name_en`` is blank the H2 uses ``name_native``."""
        sub = region.subregion
        sub.name_en = ""
        sub.name_native = "Bas-Valais"
        sub.save(update_fields=["name_en", "name_native"])
        url = _url("ch-4115", "valais", "2026-03-15")
        response = Client().get(url)
        content = response.content.decode()
        assert "Bas-Valais" in content

    def test_still_renders_day_risk_profile_panel(
        self, client: Client, variable_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """The Day Risk Profile heading + day-windows panel render below the header."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="day-risk-profile-heading"' in content
        assert 'data-testid="day-windows-panel"' in content


# ---------------------------------------------------------------------------
# Test: season heatmap sheet (SNOW-117)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSeasonSheet:
    """
    Season heatmap sheet — slide-down dialog surfaced from the page nav's
    ``🗓 SEASON`` trigger. Replaces the old month-grid drawer.

    Markup contract: a `[data-season-sheet="closed"]` wrapper holding a
    backdrop and a `role="dialog"` body. The sheet only renders when
    ``season_calendar`` is non-empty — before SEASON_START_DATE the page
    drops the trigger and the sheet entirely.

    The grid markup is deferred — served by the season_calendar_partial
    view on first open. The bulletin page itself does NOT contain
    data-testid="season-calendar", calendar-cell-today, or
    calendar-cell-selected.
    """

    def test_renders_sheet_and_trigger_when_season_active(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """A bulletin with a populated season header renders trigger + closed sheet shell."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-season-sheet="closed"' in content
        assert "data-season-trigger" in content
        assert 'data-testid="season-sheet"' in content
        # Grid is deferred — the bulletin page must NOT contain the grid markup.
        # The JS toggle script references '.calendar-cell-today' as a selector
        # string, so we check for the HTML class attribute form specifically.
        assert 'data-testid="season-calendar"' not in content
        assert 'class="rounded-full calendar-cell calendar-cell-today"' not in content
        assert (
            'class="rounded-full calendar-cell calendar-cell-selected"' not in content
        )

    def test_omits_sheet_when_season_grid_empty(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """With SEASON_START_DATE in the future, season_header is None and the sheet is omitted."""
        future_start = date(2099, 12, 1)
        with patch("django.conf.settings.SEASON_START_DATE", future_start):
            url = _url("ch-4115", "valais", "2026-03-15")
            response = client.get(url)
        content = response.content.decode()
        # The bare string 'data-season-sheet' appears in a JS querySelector
        # outside the {% if season_calendar %} block, so assert the specific
        # HTML attribute+value form that only exists when the sheet renders.
        assert 'data-season-sheet="closed"' not in content
        assert 'data-testid="season-sheet"' not in content
        assert "data-season-trigger" not in content

    def test_season_grid_placeholder_in_sheet(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """When the season is active the shell contains the #season-grid HTMX placeholder."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'id="season-grid"' in content
        assert "hx-trigger" in content
        assert "snowdesk:load" in content


# ---------------------------------------------------------------------------
# Test: day-character callout banner (SNOW-8, redesigned SNOW-127)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDayCharacterEyebrow:
    """
    Day-character callout banner above the Day Risk Profile heading.

    The banner surfaces the label produced by ``compute_day_character``
    alongside a one-line static explainer, preceded by the Snowdesk favicon
    as a leading icon. It is suppressed in the error state
    (``render_model.version == 0``) where the bulletin body is replaced by
    a warning panel.
    """

    def test_renders_label_and_explainer(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """A normal bulletin renders the callout banner with favicon, label, and explainer."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        # simple_bulletin is danger=2 with a wind_slab problem → the
        # cascade resolves to Manageable day.
        assert 'data-testid="day-character"' in content
        assert 'data-testid="day-character-label"' in content
        assert "Manageable day" in content
        assert 'data-testid="day-character-explainer"' in content
        assert "favicon.svg" in content

    def test_renders_hard_to_read_for_persistent_weak_layers(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A bulletin with persistent weak layers renders the hard-to-read callout."""
        day = date(2026, 3, 20)
        trait = {
            "category": "dry",
            "time_period": "all_day",
            "title": "Persistent weak layers",
            "geography": {"source": "problems"},
            "problems": [_problem(problem_type="persistent_weak_layers")],
            "prose": None,
            "danger_level": 3,
        }
        rm = _render_model_with_traits([trait])
        rm["danger"] = {"key": "considerable", "number": "3", "subdivision": None}
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )

        url = _url("ch-4115", "valais", "2026-03-20")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="day-character"' in content
        assert "Hard-to-read day" in content
        assert "favicon.svg" in content
        assert "<strong" in content

    def test_callout_precedes_day_risk_profile_heading(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """The callout banner sits above the Day Risk Profile heading in DOM order."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        callout_idx = content.index('data-testid="day-character"')
        heading_idx = content.index('data-testid="day-risk-profile-heading"')
        assert callout_idx < heading_idx

    def test_callout_absent_in_error_state(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A version=0 error bulletin replaces the body and suppresses the callout."""
        from apps.bulletins.services.render_model import RENDER_MODEL_VERSION

        day = date(2026, 3, 21)
        _make_am_bulletin(
            region,
            day,
            render_model={
                "version": 0,
                "error": "Synthetic test error — do not display",
                "error_type": "RenderModelBuildError",
            },
            render_model_version=RENDER_MODEL_VERSION,
            raw_data={
                "type": "Feature",
                "geometry": None,
                "properties": {"dangerRatings": [{"mainValue": "moderate"}]},
            },
        )
        url = _url("ch-4115", "valais", "2026-03-21")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="day-character"' not in content


# ---------------------------------------------------------------------------
# SNOW-169: self-hosted asset smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_bulletin_page_loads_htmx_from_static(client: Client) -> None:
    """bulletin.html must reference the vendored htmx from /static/, not unpkg.

    SNOW-169 vendored htmx 2.0.4 into static/js/htmx.min.js so the page no
    longer depends on an external CDN at runtime or in the CSP allow-list.
    """
    region = MicroRegionFactory.create(region_id="ch-4115", name="Valais")
    day = date(2026, 3, 15)
    _make_am_bulletin(region, day)
    url = _url("ch-4115", "valais", "2026-03-15")
    response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    assert "htmx.min" in body
    assert "unpkg.com" not in body


# ---------------------------------------------------------------------------
# SNOW-183: context-aware Map back-link in the nav bar
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMapBackLink:
    """The nav bar Map back-link carries date + region context (SNOW-183).

    When the bulletin page represents a past date the link includes
    ``?d=YYYY-MM-DD`` so the map scrubber boots to that day.  For today's
    page the query string is omitted (the map defaults to today).  In both
    cases the URL fragment ``#<region_id>`` opens the region sheet at peek.
    """

    def test_dated_bulletin_includes_date_and_fragment(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """A dated bulletin URL produces a map back-link with ``?d=`` and ``#``.

        SNOW-344: the map page is now / so the back-link points there.
        """
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'href="/?d=2026-03-15#CH-4115"' in content

    def test_today_bulletin_omits_date_query_string(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Today's bulletin URL produces a map back-link with fragment only, no ``?d=``.

        SNOW-344: the map page is now / so the back-link points there.
        """
        today = date.today()
        _make_am_bulletin(region, today)
        # Use the region_root form (/<region_id>/) — it resolves to today without
        # requiring a slug and never redirects for today's bulletin.
        url = reverse("public:region_root", kwargs={"region_id": "ch-4115"})
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'href="/#CH-4115"' in content
        # Confirm the date query string is absent from the nav map link.
        assert "/?d=" not in content

    def test_empty_state_includes_date_and_fragment(self, client: Client) -> None:
        """The empty-state (no bulletin) page still produces a dated map back-link.

        SNOW-344: the map page is now / so the back-link points there.
        """
        # Use a region whose slug matches the URL component to avoid a slug-
        # correction redirect. ``slugify("Test Region")`` → ``test-region``, so
        # pass ``"test-region"`` as both the factory slug and the URL segment.
        MicroRegionFactory.create(
            region_id="CH-9999", name="Test Region", slug="test-region"
        )
        url = _url("ch-9999", "test-region", "2025-12-01")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'href="/?d=2025-12-01#CH-9999"' in content


# ---------------------------------------------------------------------------
# Share button smoke tests (SNOW-217)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestShareButtonSmoke:
    """Share button renders when a bulletin exists; absent in empty state."""

    def test_share_button_present_with_bulletin(self, client: Client) -> None:
        """The share button is rendered when the page has a bulletin."""
        region = MicroRegionFactory.create(
            region_id="CH-4222", name="Zermatt", slug="zermatt"
        )
        day = date(2026, 4, 8)
        _make_am_bulletin(region, day)
        # Use canonical (lowercase) region_id to avoid the slug-correction redirect.
        url = _url("ch-4222", "zermatt", "2026-04-08")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "data-bulletin-share-button" in content
        assert 'data-region-id="CH-4222"' in content
        assert 'data-date="2026-04-08"' in content

    def test_share_button_absent_in_empty_state(self, client: Client) -> None:
        """The share button is not rendered on the empty-state page."""
        MicroRegionFactory.create(region_id="CH-4222", name="Zermatt", slug="zermatt")
        # No bulletin created — page renders empty state.
        # Use canonical (lowercase) region_id to avoid the slug-correction redirect.
        url = _url("ch-4222", "zermatt", "2026-04-08")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "data-bulletin-share-button" not in content

    def test_share_button_has_touch_target_padding(self, client: Client) -> None:
        """The share button carries p-2.5 padding to meet the WCAG 2.5.5 44×44pt target.

        The -m-2.5 negative margin offsets the padding so the visual footprint
        of the 20×20 SVG icon is unchanged (SNOW-253).
        """
        region = MicroRegionFactory.create(
            region_id="CH-4222", name="Zermatt", slug="zermatt"
        )
        day = date(2026, 4, 8)
        _make_am_bulletin(region, day)
        url = _url("ch-4222", "zermatt", "2026-04-08")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # Verify the testid and both touch-target classes are present on the button.
        assert 'data-testid="bulletin-share-button"' in content
        assert "p-2.5" in content
        assert "-m-2.5" in content


# ---------------------------------------------------------------------------
# Test: OpenGraph / Twitter card meta tags (SNOW-218)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOGMetaTags:
    """Bulletin page renders the correct OpenGraph and Twitter card meta tags."""

    def test_og_site_name_present(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """og:site_name is always Snowdesk."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'property="og:site_name" content="Snowdesk"' in content

    def test_og_type_is_article_not_website(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """og:type is 'article' on a bulletin page (SNOW-555).

        This assertion previously read ``content="website"`` — og:type was
        a site-wide constant in base.html's og_tags block. A dated
        bulletin is dated, revisable content, so it is an article; see
        ``TestArticleOpenGraph`` for the timestamps that unlocks.
        """
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'property="og:type" content="article"' in content

    def test_twitter_card_summary_large_image(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """twitter:card is summary_large_image."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'name="twitter:card" content="summary_large_image"' in content

    def test_og_image_contains_og_default_png(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """og:image and twitter:image both reference the placeholder image."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'property="og:image"' in content
        assert "og-default.png" in content

    def test_og_image_width_and_height(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """og:image:width and og:image:height are 1200 and 630."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'property="og:image:width" content="1200"' in content
        assert 'property="og:image:height" content="630"' in content

    def test_og_title_contains_region_name(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """og:title contains the region name on a bulletin page."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'property="og:title"' in content
        assert "Valais" in content

    def test_og_description_present(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """og:description is present when the panel has a danger rating."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'property="og:description"' in content

    def test_og_url_matches_canonical(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """og:url matches the canonical URL link already in extra_head."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'property="og:url"' in content
        assert "/ch-4115/valais/2026-03-15/" in content

    def test_og_locale_is_formatted(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """og:locale is present and uses underscore format (e.g. en_GB)."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        # The og_locale filter converts "en-gb" → "en_GB".
        assert 'property="og:locale"' in content
        assert "en_GB" in content

    def test_htmx_partial_omits_og_tags(
        self, client: Client, region: MicroRegion
    ) -> None:
        """An HTMX partial response does not include OG tags (no base.html inheritance)."""
        url = reverse("public:season_partial", kwargs={"region_id": "ch-4115"})
        response = client.get(url, HTTP_HX_REQUEST="true")
        # The partial returns 404 because there is no bulletin, but even a 200
        # partial does not extend base.html so it contains no og:site_name.
        content = response.content.decode()
        assert 'property="og:site_name"' not in content


# ---------------------------------------------------------------------------
# Unit tests for _build_og_description (pure-function, no DB required)
# ---------------------------------------------------------------------------


class TestBuildOgDescription:
    """Unit tests for the _build_og_description view helper."""

    def test_short_panel_contains_label_and_number(self) -> None:
        """A panel with label and number produces a description containing both."""
        from apps.public.views import _build_og_description

        panel = {
            "danger_key": "considerable",
            "danger_label": "Considerable",
            "danger_number": "3",
            "key_message": "",
        }
        result = _build_og_description(panel)
        assert "Considerable" in result
        assert "3" in result

    def test_long_key_message_truncates_to_155_chars(self) -> None:
        """When label + key_message exceeds 155 chars the result is at most 155 chars."""
        from apps.public.views import _build_og_description

        long_message = "word " * 50  # 250 chars
        panel = {
            "danger_key": "considerable",
            "danger_label": "Considerable",
            "danger_number": "3",
            "key_message": long_message,
        }
        result = _build_og_description(panel)
        assert len(result) <= 155

    def test_long_key_message_truncates_on_word_boundary(self) -> None:
        """Truncation never cuts a word mid-way; result does not end with a space."""
        from apps.public.views import _build_og_description

        long_message = "word " * 50
        panel = {
            "danger_key": "considerable",
            "danger_label": "Considerable",
            "danger_number": "3",
            "key_message": long_message,
        }
        result = _build_og_description(panel)
        assert not result.endswith(" ")
        # No fragment that is only a partial word (every token ends at a space boundary).
        assert " " not in result or result == result.rstrip()

    def test_html_tags_stripped_from_key_message(self) -> None:
        """HTML markup in key_message is stripped; no angle brackets survive."""
        from apps.public.views import _build_og_description

        panel = {
            "danger_key": "high",
            "danger_label": "High",
            "danger_number": "4",
            "key_message": "<p>danger headline</p>",
        }
        result = _build_og_description(panel)
        assert "<" not in result
        assert ">" not in result
        assert "danger headline" in result

    def test_panel_none_returns_empty_string(self) -> None:
        """A None panel returns an empty string."""
        from apps.public.views import _build_og_description

        assert _build_og_description(None) == ""

    def test_panel_without_danger_key_returns_empty_string(self) -> None:
        """A panel missing danger_key (empty-state) returns an empty string."""
        from apps.public.views import _build_og_description

        panel = {
            "danger_label": "High",
            "danger_number": "4",
            "key_message": "Some message",
        }
        assert _build_og_description(panel) == ""

    def test_panel_with_label_only_no_number(self) -> None:
        """When only danger_label is present (no number), label still appears."""
        from apps.public.views import _build_og_description

        panel = {
            "danger_key": "low",
            "danger_label": "Low",
            "danger_number": "",
            "key_message": "",
        }
        result = _build_og_description(panel)
        assert "Low" in result


# ---------------------------------------------------------------------------
# Test: JSON-LD structured data block (SNOW-220)
# ---------------------------------------------------------------------------

_JSONLD_SCRIPT_RE = re.compile(
    r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL,
)
"""Regex that extracts the first ``application/ld+json`` script body."""


def _extract_jsonld(content: str) -> dict | None:
    """Return parsed JSON-LD payload from *content*, or ``None`` if absent."""
    match = _JSONLD_SCRIPT_RE.search(content)
    if match is None:
        return None
    return json.loads(match.group(1).strip())  # type: ignore[no-any-return]


@pytest.mark.django_db
class TestStructuredData:
    """
    Schema.org JSON-LD block on the bulletin detail page (SNOW-220).

    Each test renders a factory-built bulletin via the canonical URL pattern
    and either extracts the ``<script type="application/ld+json">`` block or
    asserts its absence.
    """

    def test_jsonld_block_present_on_bulletin_page(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """An ``application/ld+json`` script tag is emitted when a bulletin exists."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'type="application/ld+json"' in content
        data = _extract_jsonld(content)
        assert data is not None
        # Must be valid JSON — already asserted by _extract_jsonld not returning None.

    def test_jsonld_shape(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """The JSON-LD block has the expected schema.org WebPage + Report shape."""
        url = _url("ch-4115", "valais", "2026-03-15")
        with language_override("en-gb"):
            response = client.get(url)
        content = response.content.decode()
        data = _extract_jsonld(content)
        assert data is not None

        # Top-level WebPage.
        assert data["@type"] == "WebPage"
        assert data["publisher"]["name"] == settings.SITE_NAME
        assert data["publisher"]["url"] == settings.SITE_BASE_URL

        # mainEntity is a Report (string, not a list).
        main = data["mainEntity"]
        assert main["@type"] == "Report"

        # Source organisation — simple_bulletin uses the default "slf" source.
        slf_name, slf_url = BULLETIN_SOURCE_LINKS[Bulletin.Source.SLF]
        assert main["sourceOrganization"]["name"] == slf_name
        assert main["sourceOrganization"]["url"] == slf_url

        # datePublished matches the fixture's publication_time exactly.
        # The render model metadata sets publication_time to "2026-03-15T06:00:00+00:00".
        assert main["datePublished"] == "2026-03-15T06:00:00+00:00"

        # temporalCoverage is an ISO-8601 interval covering the bulletin window.
        temporal = main["temporalCoverage"]
        assert "/" in temporal
        from_part, to_part = temporal.split("/", 1)
        assert from_part  # non-empty ISO timestamp
        assert to_part

        # inLanguage is the overridden language, not a runtime default.
        assert data["inLanguage"] == "en-gb"

        # spatialCoverage carries the region name.
        assert main["spatialCoverage"]["name"] == region.name

        # containedInPlace is populated with the MajorRegion name.
        # MicroRegionFactory.create(region_id="CH-4115") → SubRegionFactory.create(prefix="CH-41") →
        # MajorRegionFactory.create(prefix="CH-4") → name_en = "Major CH-4".
        contained = main["spatialCoverage"]["containedInPlace"]
        assert contained["name"] == "Major CH-4"

        # about carries the danger level DefinedTerm.
        about = main["about"]
        assert about["@type"] == "DefinedTerm"
        # simple_bulletin uses "moderate" danger — number "2".
        assert about["termCode"] == "2"
        assert about["name"]  # non-empty label

    def test_jsonld_inlanguage_none_fallback(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """When ``get_language()`` returns ``None``, ``inLanguage`` falls back to ``"en-gb"``."""
        url = _url("ch-4115", "valais", "2026-03-15")
        with language_override(None):
            response = client.get(url)
        content = response.content.decode()
        data = _extract_jsonld(content)
        assert data is not None
        assert data["inLanguage"] == "en-gb"
        assert data["mainEntity"]["inLanguage"] == "en-gb"

    def test_jsonld_absent_on_empty_state(
        self, client: Client, region: MicroRegion
    ) -> None:
        """No ``application/ld+json`` block is emitted when there is no bulletin."""
        # Request a date for which no bulletin has been created.
        url = _url("ch-4115", "valais", "2099-01-01")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "application/ld+json" not in content

    def test_jsonld_escapes_closing_script_tag(
        self, client: Client, region: MicroRegion
    ) -> None:
        r"""
        A ``</script>`` substring inside a field value is escaped.

        Checks that ``<\/`` escaping prevents a crafted string in the bulletin
        data from terminating the embedding ``<script>`` tag prematurely.
        """
        # Build a MicroRegion whose name contains the dangerous substring.
        major = MajorRegionFactory.create(
            prefix="CH-X", name_en="Major </script> Region"
        )
        sub = SubRegionFactory.create(prefix="CH-X1", major=major, name_en="Sub X1")
        tricky_region = MicroRegionFactory.create(
            region_id="CH-9999",
            name="Valais </script> Test",
            slug="ch-9999",
            subregion=sub,
        )
        day = date(2026, 3, 15)
        rm = _render_model_with_traits([_dry_trait_problems([_problem()])])
        raw = _raw_data_with_problems([_raw_problem()])
        _make_am_bulletin(
            tricky_region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
            raw_data=raw,
        )

        url = _url("ch-9999", "valais-script-test", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()

        # The script block must be present in the response.
        assert 'type="application/ld+json"' in content
        # The raw unescaped form must NOT appear inside the JSON-LD script block.
        script_body = content.split('type="application/ld+json"')[1].split("</script>")[
            0
        ]
        assert "</script>" not in script_body
        # The escaped form must be present somewhere in the response.
        assert "<\\/script>" in content


# ---------------------------------------------------------------------------
# SNOW-222: subscribe panel states
# ---------------------------------------------------------------------------

_TOKEN_BACKEND = "apps.accounts.backends.TokenBackend"


def _make_session_client(account: Account) -> Client:
    """Return a test client logged in as account via Django auth."""
    client = Client()
    client.force_login(account.user, backend=_TOKEN_BACKEND)
    return client


@pytest.mark.django_db
class TestSubscribePanelStates:
    """Bulletin page subscribe panel renders the correct state for each user/subscription combo.

    Three states (SNOW-222):
      1. Anonymous — email-input form.
      2. Authenticated + not subscribed to this region — "Add region" CTA.
      3. Authenticated + already subscribed to this region — "Unsubscribe" CTA.
    """

    def test_panel_renders_anonymous_form_when_logged_out(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """Anonymous visitor sees the email-input form."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="email"' in content
        assert 'type="email"' in content

    def test_panel_renders_add_cta_when_authed_but_not_subscribed(
        self, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """Authenticated visitor with no subscription sees the 'Add region' CTA."""
        account = AccountFactory.create()
        client = _make_session_client(account)
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # Add-region form POSTs to the add_region endpoint.
        assert "/account/add/" in content
        # Should NOT show the email input (anonymous form).
        assert 'name="email"' not in content

    def test_panel_renders_unsubscribe_cta_when_authed_and_subscribed(
        self, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """Authenticated visitor already subscribed sees the 'Unsubscribe' CTA."""
        account = AccountFactory.create()
        SubscriptionFactory.create(account=account, region=region)
        client = _make_session_client(account)
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # Unsubscribe form POSTs to the remove_region_from_bulletin endpoint.
        assert "/account/remove-region/" in content
        # Should NOT show the email input (anonymous form).
        assert 'name="email"' not in content


# ── _best_rating_from_rm_entries unit tests ───────────────────────────────────


class TestBestRatingFromRmEntries:
    """
    Unit tests for the _best_rating_from_rm_entries helper.

    This function is the core of _resolve_period_danger_from_rm.  The key
    contract is that it returns None when no entry carries a recognised
    danger key, so the caller can fall through to the traits fallback.
    A tautological guard in the original implementation meant it never
    returned None; these tests pin the corrected behaviour.
    """

    def _call(self, entries: list[dict[str, Any]]) -> tuple[str, str] | None:
        """Call the helper under test."""
        from apps.public.views import _best_rating_from_rm_entries

        return _best_rating_from_rm_entries(entries)

    def test_returns_none_for_empty_list(self) -> None:
        """An empty entry list returns None so the caller can use its fallback."""
        assert self._call([]) is None

    def test_returns_none_for_unrecognised_keys_only(self) -> None:
        """Entries with only unrecognised keys return None, not a default 'low'."""
        entries: list[dict[str, Any]] = [
            {"period": "all_day", "key": "extreme", "subdivision": None},
            {"period": "all_day", "key": "", "subdivision": None},
        ]
        assert self._call(entries) is None

    def test_returns_highest_recognised_key(self) -> None:
        """The highest recognised key wins when multiple entries are present."""
        entries: list[dict[str, Any]] = [
            {"period": "all_day", "key": "low", "subdivision": None},
            {"period": "all_day", "key": "considerable", "subdivision": "+"},
            {"period": "all_day", "key": "moderate", "subdivision": None},
        ]
        result = self._call(entries)
        assert result == ("considerable", "+")

    def test_unrecognised_key_is_ignored_when_recognised_present(self) -> None:
        """Unrecognised keys are skipped; the best recognised key is returned."""
        entries: list[dict[str, Any]] = [
            {"period": "all_day", "key": "extreme", "subdivision": None},
            {"period": "all_day", "key": "high", "subdivision": "-"},
        ]
        result = self._call(entries)
        assert result == ("high", "-")

    def test_subdivision_none_becomes_empty_string(self) -> None:
        """A None subdivision is normalised to an empty string in the result."""
        entries: list[dict[str, Any]] = [
            {"period": "all_day", "key": "moderate", "subdivision": None}
        ]
        result = self._call(entries)
        assert result == ("moderate", "")


# ── _normalise_danger_pattern unit tests (SNOW-254) ──────────────────────────


class TestNormaliseDangerPattern:
    """
    Unit tests for the _normalise_danger_pattern helper.

    The LWD_Tyrol ``dangerPatterns`` field stores patterns as ``"DP1"``–``"DP10"``
    (sometimes lowercase). The helper must:
    - Normalise to ``GM.N`` label form.
    - Resolve the full English name as a tooltip.
    - Handle unrecognised formats gracefully.
    """

    def _call(self, raw: str) -> dict[str, str]:
        """Call the helper under test."""
        from apps.public.views import _normalise_danger_pattern

        return _normalise_danger_pattern(raw)

    def test_dp1_produces_gm1_label(self) -> None:
        """``DP1`` normalises to label ``GM.1``."""
        result = self._call("DP1")
        assert result["label"] == "GM.1"

    def test_dp10_produces_gm10_label(self) -> None:
        """``DP10`` normalises to label ``GM.10``."""
        result = self._call("DP10")
        assert result["label"] == "GM.10"

    def test_lowercase_dp_normalised(self) -> None:
        """Lowercase ``dp1`` is accepted and normalises to ``GM.1``."""
        result = self._call("dp1")
        assert result["label"] == "GM.1"

    def test_dp1_title_is_deep_persistent_weak_layer(self) -> None:
        """``DP1`` maps to "Deep persistent weak layer"."""
        result = self._call("DP1")
        assert result["title"] == "Deep persistent weak layer"

    def test_dp10_title_is_spring_scenario(self) -> None:
        """``DP10`` maps to "Spring scenario"."""
        result = self._call("DP10")
        assert result["title"] == "Spring scenario"

    def test_all_known_patterns_have_titles(self) -> None:
        """All DP1–DP10 produce non-empty titles."""
        for i in range(1, 11):
            result = self._call(f"DP{i}")
            assert result["title"], f"DP{i} produced empty title"

    def test_gm_dot_form_accepted(self) -> None:
        """``gm.1`` (already normalised) is also accepted."""
        result = self._call("gm.1")
        assert result["label"] == "GM.1"
        assert result["title"] == "Deep persistent weak layer"

    def test_unrecognised_pattern_returned_verbatim(self) -> None:
        """Unrecognised patterns are returned verbatim with an empty title."""
        result = self._call("XYZ99")
        assert result["label"] == "XYZ99"
        assert result["title"] == ""


# ── _problem_cards_from_render_model_traits danger-pattern propagation (SNOW-254) ──


class TestDangerPatternPropagation:
    """
    Unit tests asserting that bulletin-level danger patterns are threaded through
    to every card produced by _problem_cards_from_render_model_traits.
    """

    def _minimal_trait(self, problem_type: str = "wind_slab") -> dict[str, Any]:
        """Return the smallest valid trait dict for card building."""
        return {
            "category": "dry",
            "time_period": "all_day",
            "title": "Dry avalanches",
            "geography": {"source": "problems"},
            "problems": [
                {
                    "problem_type": problem_type,
                    "comment_html": "",
                    "aspects": ["N"],
                    "elevation": {"lower": 2000, "upper": None, "treeline": False},
                    "time_period": "all_day",
                    "core_zone_text": None,
                    "danger_rating_value": "moderate",
                    "avalanche_type": None,
                    "avalanche_size": None,
                    "frequency": None,
                    "snowpack_stability": None,
                }
            ],
            "prose": None,
            "danger_level": 2,
        }

    def test_no_patterns_produces_empty_list_on_card(self) -> None:
        """Calling with no danger_patterns leaves each card with an empty list."""
        from apps.public.views import _problem_cards_from_render_model_traits

        cards = _problem_cards_from_render_model_traits([self._minimal_trait()])
        assert cards[0]["danger_patterns"] == []

    def test_patterns_propagated_to_single_card(self) -> None:
        """Danger patterns passed in are normalised and placed on the card."""
        from apps.public.views import _problem_cards_from_render_model_traits

        cards = _problem_cards_from_render_model_traits(
            [self._minimal_trait()], danger_patterns=["DP1"]
        )
        assert len(cards[0]["danger_patterns"]) == 1
        assert cards[0]["danger_patterns"][0]["label"] == "GM.1"
        assert cards[0]["danger_patterns"][0]["title"] == "Deep persistent weak layer"

    def test_patterns_propagated_to_all_cards(self) -> None:
        """When multiple traits are built, all cards receive the same pattern list."""
        from apps.public.views import _problem_cards_from_render_model_traits

        traits = [
            self._minimal_trait("wind_slab"),
            self._minimal_trait("new_snow"),
        ]
        cards = _problem_cards_from_render_model_traits(
            traits, danger_patterns=["DP6", "DP4"]
        )
        assert len(cards) == 2
        for card in cards:
            assert len(card["danger_patterns"]) == 2
            assert card["danger_patterns"][0]["label"] == "GM.6"
            assert card["danger_patterns"][1]["label"] == "GM.4"

    def test_slf_card_has_empty_patterns(self) -> None:
        """SLF cards built with no patterns carry an empty danger_patterns list."""
        from apps.public.views import _problem_cards_from_render_model_traits

        cards = _problem_cards_from_render_model_traits(
            [self._minimal_trait()], danger_patterns=[]
        )
        assert cards[0]["danger_patterns"] == []


# ── _rating_block.html danger-pattern row (SNOW-254) — integration ──────────


@pytest.mark.django_db()
class TestDangerPatternRow:
    """
    Integration tests confirming that danger-pattern tags render in the
    bulletin page HTML when the card carries patterns, and are absent for
    SLF cards with an empty list.

    Uses the module-level ``region`` fixture (region_id="CH-4115", name="Valais")
    so canonical URL resolution produces ``/ch-4115/valais/<date>/``.
    """

    def _albina_trait(self) -> dict[str, Any]:
        """Build a minimal ALBINA trait dict with EAWS matrix fields."""
        return {
            "category": "dry",
            "time_period": "all_day",
            "title": "Dry avalanches",
            "geography": {"source": "problems"},
            "problems": [
                {
                    "problem_type": "wind_slab",
                    "comment_html": "",
                    "aspects": ["N", "NE"],
                    "elevation": {"lower": 2200, "upper": None, "treeline": False},
                    "time_period": "all_day",
                    "core_zone_text": None,
                    "danger_rating_value": "considerable",
                    "avalanche_type": "slab",
                    "avalanche_size": 3,
                    "frequency": "some",
                    "snowpack_stability": "poor",
                }
            ],
            "prose": None,
            "danger_level": 3,
        }

    def test_danger_pattern_row_present_when_patterns_populated(
        self, client: Client, region: MicroRegion
    ) -> None:
        """When cards carry danger patterns, the pattern row and GM.N tags render."""
        day = date(2026, 4, 10)
        rm = _render_model_with_traits([self._albina_trait()])
        rm["source"] = "albina"
        rm["danger_patterns"] = ["DP1", "DP6"]
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )
        url = _url("ch-4115", "valais", "2026-04-10")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="danger-pattern-row"' in content
        assert 'data-testid="danger-pattern-tag"' in content
        assert "GM.1" in content
        assert "GM.6" in content

    def test_danger_pattern_row_absent_for_slf_card(
        self, client: Client, region: MicroRegion
    ) -> None:
        """SLF cards with no danger patterns produce no pattern row in the HTML."""
        day = date(2026, 4, 11)
        trait: dict[str, Any] = {
            "category": "dry",
            "time_period": "all_day",
            "title": "Dry avalanches",
            "geography": {"source": "problems"},
            "problems": [_problem()],
            "prose": None,
            "danger_level": 2,
        }
        rm = _render_model_with_traits([trait])
        rm["danger_patterns"] = []
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )
        url = _url("ch-4115", "valais", "2026-04-11")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="danger-pattern-row"' not in content

    def test_eaws_matrix_row_renders_for_albina_card(
        self, client: Client, region: MicroRegion
    ) -> None:
        """ALBINA cards with size/frequency/stability render the EAWS matrix row."""
        day = date(2026, 4, 12)
        rm = _render_model_with_traits([self._albina_trait()])
        rm["source"] = "albina"
        rm["danger_patterns"] = []
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )
        url = _url("ch-4115", "valais", "2026-04-12")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="eaws-matrix-row"' in content
        assert 'data-testid="eaws-size-chip"' in content
        assert 'data-testid="eaws-frequency-chip"' in content
        assert 'data-testid="eaws-stability-chip"' in content

    def test_albina_card_has_no_level_number_chip(
        self, client: Client, region: MicroRegion
    ) -> None:
        """ALBINA cards carry no subdivision → level_number is empty → chip absent.

        SNOW-291 scope: the level-number chip is SLF-only.  ALBINA bulletins
        have no subdivision data so the chip must not appear in their rendered
        rating blocks.
        """
        day = date(2026, 4, 13)
        rm = _render_model_with_traits([self._albina_trait()])
        rm["source"] = "albina"
        rm["danger_patterns"] = []
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )
        url = _url("ch-4115", "valais", "2026-04-13")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="level-number-chip"' not in content


# ---------------------------------------------------------------------------
# Test: hero rating badge (SNOW-246)
# ---------------------------------------------------------------------------


def _render_model_with_ratings(
    key: str,
    number: str,
    subdivision: str | None,
    period: str = "all_day",
    source: str = "slf",
) -> dict:
    """Build a v5 render_model carrying a projected ``danger.ratings`` entry.

    Used to drive the morning-rating badge tests without going through the
    raw CAAML path — the projected ratings list is the primary source for
    :func:`apps.public.views._resolve_period_danger_from_rm`.
    """
    return {
        "version": 5,
        "source": source,
        "danger": {
            "key": key,
            "number": number,
            "subdivision": subdivision,
            "ratings": [
                {
                    "period": period,
                    "key": key,
                    "subdivision": subdivision,
                    "elevation": None,
                }
            ],
        },
        "danger_patterns": [],
        "traits": [],
        "metadata": {
            "publication_time": "2026-03-15T06:00:00+00:00",
            "valid_from": "2026-03-15T06:00:00+00:00",
            "valid_until": "2026-03-15T15:00:00+00:00",
            "next_update": "2026-03-15T15:00:00+00:00",
            "unscheduled": False,
            "lang": "en",
        },
        "prose": {
            "snowpack_structure": None,
            "weather_review": None,
            "weather_forecast": None,
            "tendency": [],
            "avalanche_activity": {"highlights": "", "comment": ""},
            "tendency_lead": None,
        },
    }


def _make_region_with_subregion(
    region_id: str = "CH-4115", name: str = "Valais", slug: str = "ch-4115"
) -> "MicroRegion":
    """Create a MicroRegion with the SubRegion/MajorRegion chain required by views."""
    major = MajorRegionFactory.create()
    sub = SubRegionFactory.create(major=major)
    return MicroRegionFactory.create(
        region_id=region_id, name=name, slug=slug, subregion=sub
    )


@pytest.mark.django_db
class TestHeroRatingBadge:
    """The hero rating badge is removed from the bulletin page (SNOW-286)."""

    def _url(self, region_id: str = "ch-4115", date_str: str = "2026-03-15") -> str:
        return reverse(
            "public:bulletin_date",
            kwargs={"region_id": region_id, "slug": "valais", "date_str": date_str},
        )

    def test_badge_absent_on_empty_state_page(self, client: Client) -> None:
        """No bulletin → no hero badge (``morning_rating`` is None)."""
        _make_region_with_subregion()
        # No bulletin created — empty-state page.
        response = client.get(self._url())
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-testid="bulletin-hero-rating"' not in content


# ---------------------------------------------------------------------------
# Unit tests: _build_morning_rating helper
# ---------------------------------------------------------------------------


class TestBuildMorningRating:
    """Unit tests for the ``_build_morning_rating`` view helper."""

    def _call(self, panel: dict) -> dict | None:
        from apps.public.views import _build_morning_rating

        return _build_morning_rating(panel)

    def test_returns_none_when_no_morning_key(self) -> None:
        """An empty panel dict returns None."""
        assert self._call({}) is None

    def test_returns_none_for_no_rating_key(self) -> None:
        """morning_key == 'no_rating' returns None (badge hidden)."""
        assert (
            self._call(
                {
                    "morning_key": "no_rating",
                    "morning_number": "0",
                    "morning_subdivision": "",
                }
            )
            is None
        )

    def test_returns_dict_for_valid_key(self) -> None:
        """A valid morning_key produces the expected dict."""
        result = self._call(
            {
                "morning_key": "considerable",
                "morning_number": "3",
                "morning_subdivision": "-",
            }
        )
        assert result == {
            "level_key": "considerable",
            "level_number": "3",
            "subdivision": "-",
        }

    def test_subdivision_none_normalised_to_empty_string(self) -> None:
        """None subdivision is coerced to empty string so the template renders nothing."""
        result = self._call(
            {
                "morning_key": "moderate",
                "morning_number": "2",
                "morning_subdivision": None,
            }
        )
        assert result is not None
        assert result["subdivision"] == ""

    def test_returns_none_for_empty_morning_key(self) -> None:
        """An empty string morning_key returns None."""
        assert (
            self._call(
                {"morning_key": "", "morning_number": "0", "morning_subdivision": ""}
            )
            is None
        )


# ---------------------------------------------------------------------------
# Unit tests: compute_period_transition (SNOW-248)
# ---------------------------------------------------------------------------


class TestComputePeriodTransition:
    """Unit tests for the ``compute_period_transition`` pure function.

    Covers: all four SLF/EUREGIO patterns, plus the no-split and empty
    cases that must return ``None``.
    """

    def _make_rm(self, ratings: list[dict]) -> dict:
        """Wrap projected ratings in a minimal render model dict."""
        return {
            "danger": {
                "key": "moderate",
                "number": "2",
                "subdivision": None,
                "ratings": ratings,
            },
        }

    def _rm_rating(
        self,
        key: str,
        period: str = "all_day",
        subdivision: str | None = None,
        lower: int | None = None,
        upper: int | None = None,
    ) -> dict:
        """Build a single projected danger rating dict."""
        elevation = None
        if lower is not None or upper is not None:
            elevation = {
                "lower": lower,
                "upper": upper,
                "treeline": False,
                "treeline_side": None,
            }
        return {
            "period": period,
            "key": key,
            "subdivision": subdivision,
            "elevation": elevation,
        }

    def test_returns_none_for_no_ratings(self) -> None:
        """An empty ratings list returns None."""
        from apps.bulletins.services.render_model import compute_period_transition

        assert compute_period_transition(self._make_rm([])) is None

    def test_returns_none_for_single_all_day_rating(self) -> None:
        """A single all_day rating has no split — returns None."""
        from apps.bulletins.services.render_model import compute_period_transition

        rm = self._make_rm([self._rm_rating("moderate")])
        assert compute_period_transition(rm) is None

    def test_slf_escalating_all_day_to_later(self) -> None:
        """all_day moderate → later considerable: direction=rise, temporal."""
        from apps.bulletins.services.render_model import compute_period_transition

        rm = self._make_rm(
            [
                self._rm_rating("moderate", "all_day"),
                self._rm_rating("considerable", "later"),
            ]
        )
        pt = compute_period_transition(rm)
        assert pt is not None
        assert pt.direction == "rise"
        assert pt.destination_key == "considerable"
        assert pt.destination_number == "3"
        assert pt.partition_type == "temporal"
        assert pt.partition_label == ""
        assert pt.has_split is True

    def test_slf_deescalating_all_day_to_later(self) -> None:
        """all_day considerable → later moderate: direction=fall, temporal."""
        from apps.bulletins.services.render_model import compute_period_transition

        rm = self._make_rm(
            [
                self._rm_rating("considerable", "all_day"),
                self._rm_rating("moderate", "later"),
            ]
        )
        pt = compute_period_transition(rm)
        assert pt is not None
        assert pt.direction == "fall"
        assert pt.destination_key == "moderate"
        assert pt.partition_type == "temporal"

    def test_slf_flat_but_split_all_day_to_later(self) -> None:
        """all_day considerable → later considerable: direction=none, temporal."""
        from apps.bulletins.services.render_model import compute_period_transition

        rm = self._make_rm(
            [
                self._rm_rating("considerable", "all_day"),
                self._rm_rating("considerable", "later"),
            ]
        )
        pt = compute_period_transition(rm)
        assert pt is not None
        assert pt.direction == "none"
        assert pt.destination_key == "considerable"
        assert pt.partition_type == "temporal"
        assert pt.has_split is True

    def test_euregio_elevation_banded(self) -> None:
        """ALBINA earlier/later with elevation bounds: partition_type=elevation."""
        from apps.bulletins.services.render_model import compute_period_transition

        rm = self._make_rm(
            [
                self._rm_rating("low", "earlier"),
                self._rm_rating("low", "later", upper=2600),  # below 2600
                self._rm_rating("moderate", "later", lower=2600),  # above 2600
            ]
        )
        pt = compute_period_transition(rm)
        assert pt is not None
        assert pt.direction == "rise"
        assert pt.destination_key == "moderate"
        assert pt.partition_type == "elevation"
        assert "2600" in pt.partition_label
        assert pt.has_split is True

    def test_temporal_earlier_to_later_without_elevation(self) -> None:
        """ALBINA earlier/later without elevation → temporal, not elevation."""
        from apps.bulletins.services.render_model import compute_period_transition

        rm = self._make_rm(
            [
                self._rm_rating("low", "earlier"),
                self._rm_rating("moderate", "later"),
            ]
        )
        pt = compute_period_transition(rm)
        assert pt is not None
        assert pt.partition_type == "temporal"
        assert pt.partition_label == ""

    def test_subdivision_included_in_rank_comparison(self) -> None:
        """Subdivision modifies the rank: moderate+ ranks above moderate."""
        from apps.bulletins.services.render_model import compute_period_transition

        rm = self._make_rm(
            [
                self._rm_rating("moderate", "all_day"),
                self._rm_rating("moderate", "later", subdivision="+"),
            ]
        )
        pt = compute_period_transition(rm)
        assert pt is not None
        assert pt.direction == "rise"
        assert pt.destination_subdivision == "+"


# ---------------------------------------------------------------------------
# Integration tests: period_transition on the bulletin page (SNOW-248)
# ---------------------------------------------------------------------------


def _render_model_with_split_ratings(
    source_key: str,
    source_period: str,
    dest_key: str,
    dest_period: str,
    source_source: str = "slf",
) -> dict:
    """Build a v5 render_model with two projected ratings (one split day)."""
    return {
        "version": 5,
        "source": source_source,
        "danger": {
            "key": dest_key,
            "number": str(
                {"low": 1, "moderate": 2, "considerable": 3, "high": 4, "very_high": 5}[
                    dest_key
                ]
            ),
            "subdivision": None,
            "ratings": [
                {
                    "period": source_period,
                    "key": source_key,
                    "subdivision": None,
                    "elevation": None,
                },
                {
                    "period": dest_period,
                    "key": dest_key,
                    "subdivision": None,
                    "elevation": None,
                },
            ],
        },
        "danger_patterns": [],
        "traits": [],
        "metadata": {
            "publication_time": "2026-03-15T06:00:00+00:00",
            "valid_from": "2026-03-15T06:00:00+00:00",
            "valid_until": "2026-03-15T15:00:00+00:00",
            "next_update": "2026-03-15T15:00:00+00:00",
            "unscheduled": False,
            "lang": "en",
        },
        "prose": {
            "snowpack_structure": None,
            "weather_review": None,
            "weather_forecast": None,
            "tendency": [],
            "avalanche_activity": {"highlights": "", "comment": ""},
            "tendency_lead": None,
        },
    }


@pytest.mark.django_db
class TestPeriodTransitionBulletinPage:
    """Integration tests for the period-transition chip and Day Risk Profile (SNOW-248)."""

    def _url(self, date_str: str = "2026-03-15") -> str:
        return reverse(
            "public:bulletin_date",
            kwargs={"region_id": "ch-4115", "slug": "valais", "date_str": date_str},
        )

    def _make_split_bulletin(
        self,
        region: "MicroRegion",
        source_key: str,
        source_period: str,
        dest_key: str,
        dest_period: str,
        day: date = date(2026, 3, 15),
    ) -> Bulletin:
        """Create a bulletin with a temporal split in the projected ratings."""
        rm = _render_model_with_split_ratings(
            source_key, source_period, dest_key, dest_period
        )
        raw = _raw_data_with_ratings(
            [
                {"mainValue": source_key, "validTimePeriod": source_period},
                {"mainValue": dest_key, "validTimePeriod": dest_period},
            ]
        )
        raw["properties"]["customData"] = {"CH": {}}
        return _make_am_bulletin(
            region, day, render_model=rm, render_model_version=5, raw_data=raw
        )

    def test_flat_but_split_no_chip(self, client: Client, region: MicroRegion) -> None:
        """all_day considerable → later considerable: no chip, but flat-split caption present."""
        self._make_split_bulletin(
            region, "considerable", "all_day", "considerable", "later"
        )
        response = client.get(self._url())
        content = response.content.decode()
        assert 'data-testid="period-transition-chip"' not in content
        assert 'data-testid="day-risk-profile-flat-split-caption"' in content
        assert "Problem type changes" in content

    def test_no_transition_row_for_all_day_only(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A single all_day rating: no transition row and no chip."""
        day = date(2026, 3, 15)
        rm = _render_model_with_ratings("moderate", "2", None)
        raw = _raw_data_with_ratings(
            [{"mainValue": "moderate", "validTimePeriod": "all_day"}]
        )
        raw["properties"]["customData"] = {"CH": {}}
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=5, raw_data=raw
        )
        response = client.get(self._url())
        content = response.content.decode()
        assert 'data-testid="period-transition-chip"' not in content
        assert 'data-testid="day-windows-transition-row"' not in content

    def test_chip_absent_on_empty_state_page(self, client: Client) -> None:
        """No bulletin → no period-transition chip (period_transition is None)."""
        _make_region_with_subregion()
        response = client.get(
            reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4115",
                    "slug": "valais",
                    "date_str": "2026-03-15",
                },
            )
        )
        content = response.content.decode()
        assert 'data-testid="period-transition-chip"' not in content


# ---------------------------------------------------------------------------
# Unit tests: _build_period_transition_chip helper (SNOW-248)
# ---------------------------------------------------------------------------


class TestBuildPeriodTransitionChip:
    """Unit tests for the ``_build_period_transition_chip`` view helper."""

    def _call(self, pt: Any) -> dict | None:
        from apps.public.views import _build_period_transition_chip

        return _build_period_transition_chip(pt)

    def test_returns_none_for_none_input(self) -> None:
        """None input returns None."""
        assert self._call(None) is None

    def test_returns_none_for_flat_but_split(self) -> None:
        """direction='none' returns None (chip suppressed for flat-but-split)."""
        from apps.bulletins.services.render_model import PeriodTransition

        pt = PeriodTransition(
            direction="none",
            destination_key="considerable",
            destination_number="3",
            destination_subdivision="",
            partition_type="temporal",
            partition_label="",
            has_split=True,
        )
        assert self._call(pt) is None

    def test_temporal_rise_chip_text(self) -> None:
        """Temporal rise: chip_text is 'rises to L3'."""
        from apps.bulletins.services.render_model import PeriodTransition

        pt = PeriodTransition(
            direction="rise",
            destination_key="considerable",
            destination_number="3",
            destination_subdivision="",
            partition_type="temporal",
            partition_label="",
            has_split=True,
        )
        result = self._call(pt)
        assert result is not None
        assert result["level_key"] == "considerable"
        assert result["direction"] == "rise"
        assert "rises" in result["chip_text"]
        assert "L3" in result["chip_text"]

    def test_temporal_fall_chip_text(self) -> None:
        """Temporal fall: chip_text is 'falls to L2'."""
        from apps.bulletins.services.render_model import PeriodTransition

        pt = PeriodTransition(
            direction="fall",
            destination_key="moderate",
            destination_number="2",
            destination_subdivision="",
            partition_type="temporal",
            partition_label="",
            has_split=True,
        )
        result = self._call(pt)
        assert result is not None
        assert "falls" in result["chip_text"]
        assert "L2" in result["chip_text"]

    def test_elevation_rise_includes_partition_label(self) -> None:
        """Elevation rise with partition_label: chip text includes the label."""
        from apps.bulletins.services.render_model import PeriodTransition

        pt = PeriodTransition(
            direction="rise",
            destination_key="moderate",
            destination_number="2",
            destination_subdivision="",
            partition_type="elevation",
            partition_label="above 2600 m",
            has_split=True,
        )
        result = self._call(pt)
        assert result is not None
        assert "2600" in result["chip_text"]
        assert "L2" in result["chip_text"]

    def test_subdivision_included_in_label(self) -> None:
        """Subdivision suffix is included in the chip text level label."""
        from apps.bulletins.services.render_model import PeriodTransition

        pt = PeriodTransition(
            direction="rise",
            destination_key="moderate",
            destination_number="2",
            destination_subdivision="+",
            partition_type="temporal",
            partition_label="",
            has_split=True,
        )
        result = self._call(pt)
        assert result is not None
        assert "L2+" in result["chip_text"]


# ---------------------------------------------------------------------------
# Test: the title bar carries the EAWS level colour (SNOW-727)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTitleBarCarriesLevel:
    """SNOW-727: the title bar is the card's level identifier.

    It replaced a 4px saturated stripe on the hazard band's top edge, so the
    row now needs a ``data-level`` attribute for the CSS to key off. The row
    is also unconditional: every render-model trait carries a title, and a
    missing row would take the level colour with it.
    """

    def test_title_bar_carries_the_cards_danger_level(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The title bar stamps data-level with the card's level key."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_problems(
            [_raw_problem(problem_type="wind_slab", danger_rating_value="considerable")]
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert 'data-testid="panel-title"' in content
        assert 'class="panel-title' in content
        assert 'data-level="considerable"' in content

    def test_title_bar_level_tracks_the_rating(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A different danger rating stamps a different level key."""
        day = date(2026, 3, 16)
        raw = _raw_data_with_problems(
            [_raw_problem(problem_type="wind_slab", danger_rating_value="low")]
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-16")).content.decode()
        panel_title_row = content.split('data-testid="panel-title"')[0].rsplit(
            "<div", 1
        )[1]
        assert 'data-level="low"' in panel_title_row

    def test_title_bar_renders_even_without_a_provider_title(
        self, client: Client, region: MicroRegion
    ) -> None:
        """No panel_title still renders the row, falling back to the category.

        The row carries the level colour now, so it cannot be conditional on
        wording the provider may not supply.
        """
        day = date(2026, 3, 17)
        raw = _raw_data_with_problems(
            [_raw_problem(problem_type="wind_slab", danger_rating_value="moderate")]
        )
        # Strip the aggregation titles the helper would otherwise supply.
        for entry in raw["properties"]["customData"]["CH"]["aggregation"]:
            entry.pop("title", None)
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-17")).content.decode()
        assert 'data-testid="panel-title"' in content
        assert "avalanches" in content.casefold()

    def test_level_stripe_is_gone(self, client: Client, region: MicroRegion) -> None:
        """The hazard band no longer carries the saturated top stripe."""
        day = date(2026, 3, 18)
        raw = _raw_data_with_problems(
            [_raw_problem(problem_type="wind_slab", danger_rating_value="moderate")]
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-18")).content.decode()
        # The band survives — only its stripe moved to the title bar.
        assert "danger-band" in content
        assert 'data-testid="level-number-chip"' not in content


# ---------------------------------------------------------------------------
# Test: the problem card states its own danger level
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProblemCardLevelTile:
    """SNOW-739: every card carries a tile with the level of that problem.

    A trait routinely sits below the day's peak — a level-1 wet problem
    under a "2+" day is the ordinary shape of a December bulletin. Until the
    tile arrived the only figure on the page was the peak, on the Day Risk
    Profile row, so the lower level was stated nowhere and the card read as
    though it shared the higher one.
    """

    def _tiles(self, content: str) -> list[str]:
        """Return each level tile's markup, in page order."""
        return re.findall(
            r'<span[^>]*data-testid="problem-danger-tile"[^>]*>[^<]*</span>',
            content,
        )

    def test_card_carries_a_tile_for_its_own_level(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A single moderate card shows a moderate tile reading 2."""
        day = date(2026, 4, 1)
        raw = _raw_data_with_problems(
            [_raw_problem(problem_type="wind_slab", danger_rating_value="moderate")]
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-04-01")).content.decode()
        assert 'data-testid="problem-danger-tile"' in content
        assert 'data-level="moderate"' in content
        assert ">2</span>" in content

    def test_lower_card_shows_its_own_level_not_the_days_peak(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A low wet card under a considerable day reads 1, not 3."""
        day = date(2026, 4, 2)
        raw = _raw_data_with_problems(
            [
                _raw_problem(
                    problem_type="wind_slab", danger_rating_value="considerable"
                ),
                _raw_problem(problem_type="wet_snow", danger_rating_value="low"),
            ],
            ratings=[_rating("considerable", "all_day")],
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-04-02")).content.decode()
        tiles = self._tiles(content)
        assert len(tiles) == 2
        assert ">3</span>" in tiles[0]
        assert ">1</span>" in tiles[1]
        assert 'data-level="low"' in tiles[1]

    def test_suffix_is_not_borrowed_from_a_higher_rating(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The subdivision belongs to its own level, so the low card takes none.

        The day's rating is "3+". Reading the suffix by period alone would
        stamp the level-1 wet card "1+", asserting a within-band grading SLF
        never published for it.
        """
        day = date(2026, 4, 3)
        raw = _raw_data_with_problems(
            [
                _raw_problem(
                    problem_type="wind_slab", danger_rating_value="considerable"
                ),
                _raw_problem(problem_type="wet_snow", danger_rating_value="low"),
            ],
            ratings=[_rating("considerable", "all_day", subdivision="plus")],
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-04-03")).content.decode()
        tiles = self._tiles(content)
        assert ">3+</span>" in tiles[0]
        assert ">1</span>" in tiles[1]

    def test_tile_names_its_level_for_a_screen_reader(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The tile's aria-label says the level in words, subdivision included."""
        day = date(2026, 4, 4)
        raw = _raw_data_with_problems(
            [_raw_problem(problem_type="wind_slab", danger_rating_value="moderate")],
            ratings=[_rating("moderate", "all_day", subdivision="plus")],
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-04-04")).content.decode()
        tile = self._tiles(content)[0]
        assert "Danger level Moderate, upper end of the band" in tile


# ---------------------------------------------------------------------------
# Test: the title bar names the window the card covers
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTitleBarWindowSuffix:
    """SNOW-739: the window follows the title after a middot — once only."""

    def test_suffix_added_when_the_title_is_silent(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A title that names no window gains "· all day"."""
        day = date(2026, 4, 5)
        raw = _raw_data_with_aggregation(
            [
                {
                    "category": "dry",
                    "validTimePeriod": "all_day",
                    "problemTypes": ["wind_slab"],
                    "title": "Dry avalanches",
                }
            ],
            [_raw_problem(problem_type="wind_slab", danger_rating_value="moderate")],
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-04-05")).content.decode()
        assert 'data-testid="panel-title-window"' in content
        assert "all day" in content

    def test_suffix_suppressed_when_the_provider_names_the_window(
        self, client: Client, region: MicroRegion
    ) -> None:
        """SLF's own ", whole day" stands alone — the row never says it twice."""
        day = date(2026, 4, 6)
        raw = _raw_data_with_aggregation(
            [
                {
                    "category": "dry",
                    "validTimePeriod": "all_day",
                    "problemTypes": ["wind_slab"],
                    "title": "Dry avalanches, whole day",
                }
            ],
            [_raw_problem(problem_type="wind_slab", danger_rating_value="moderate")],
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-04-06")).content.decode()
        assert "Dry avalanches, whole day" in content
        assert 'data-testid="panel-title-window"' not in content


# ---------------------------------------------------------------------------
# Test: the chip rail after SNOW-727 — category pill gone, time pill gated
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTimePillIsGatedOnASplitDay:
    """SNOW-727: all_day is the default window and is never labelled."""

    def test_all_day_bulletin_takes_no_window_pill(
        self, client: Client, region: MicroRegion
    ) -> None:
        """all_day is the default window and never earns a pill."""
        day = date(2026, 3, 19)
        raw = _raw_data_with_problems(
            [_raw_problem(problem_type="wind_slab", danger_rating_value="moderate")]
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-19")).content.decode()
        assert 'data-testid="time-period-pill"' not in content
        # Nor does the Day Risk Profile row label it — there is one window,
        # and naming it says nothing (SNOW-727).
        assert 'data-testid="day-window-pill"' not in content

    def test_card_title_still_names_the_window(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The card's title bar does name it, after a middot.

        SNOW-727 read the window off every surface at once. On the card that
        went too far: the title bar is where the card states what it covers,
        and a reader comparing two cards on a split day needs it there. The
        pill stays gone (SNOW-739) — this is wording inside a row that
        already exists, not a fifth chip.
        """
        day = date(2026, 3, 19)
        raw = _raw_data_with_problems(
            [_raw_problem(problem_type="wind_slab", danger_rating_value="moderate")]
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-19")).content.decode()
        assert 'data-testid="panel-title-window"' in content
        assert "all day" in content


@pytest.mark.django_db
class TestTypePillsVsTimePills:
    """SNOW-727: the problem card's chip rail is empty.

    The category pill was the fourth telling of the category on one card —
    the title bar, the hazard pictogram and the problem label all carry it
    already, and across every trait in production the title has never been
    absent. The time pill went the same way: on all 734 split-day traits in
    production the title bar already names the window in the provider's own
    words ("…, whole day", "…, as the day progresses", "…, earlier"), and on
    an all-day bulletin there is no window to name at all.
    """

    def test_dry_card_has_no_category_pill(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A dry-category card renders no category pill (SNOW-727)."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_problems(
            [_raw_problem(problem_type="wind_slab", danger_rating_value="moderate")]
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert 'data-testid="category-type-pill"' not in content
        # The title bar still names the category, and now carries the level.
        assert 'data-testid="panel-title"' in content

    def test_wet_card_has_no_category_pill(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A wet-category card renders no category pill either (SNOW-727)."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_problems(
            [_raw_problem(problem_type="wet_snow", danger_rating_value="moderate")]
        )
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert 'data-testid="category-type-pill"' not in content

    def test_later_window_is_named_by_the_title_bar_not_a_pill(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A 'later' card names its window in the title bar, and takes no pill.

        The provider's own wording carries it — SLF says "as the day
        progresses", ALBINA's fallback says ", later" — on all 734 split-day
        traits in production (SNOW-727).
        """
        day = date(2026, 3, 15)
        # A "later" time-period wet trait carries a time_period_label.
        raw_data = {
            "type": "Feature",
            "geometry": None,
            "properties": {
                "dangerRatings": [
                    _rating("moderate", "all_day"),
                    _rating("considerable", "later"),
                ],
                "avalancheProblems": [
                    _raw_problem(
                        problem_type="wet_snow",
                        danger_rating_value="considerable",
                        valid_time_period="later",
                    )
                ],
                "customData": {
                    "CH": {
                        "aggregation": [
                            {
                                "category": "wet",
                                "validTimePeriod": "later",
                                "problemTypes": ["wet_snow"],
                            }
                        ]
                    }
                },
            },
        }
        _make_am_bulletin(region, day, raw_data=raw_data)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert 'data-testid="time-period-pill"' not in content
        # The window survives in the title bar's wording and on the
        # Day Risk Profile row, where "later" is the news.
        assert "later" in content.casefold()
        assert 'data-testid="day-window-pill"' in content

    def test_split_day_drops_both_card_pills(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Even on a split day the card carries neither pill."""
        day = date(2026, 3, 15)
        # Variable-day bulletin: dry all-day + wet later.
        raw_data = {
            "type": "Feature",
            "geometry": None,
            "properties": {
                "dangerRatings": [
                    _rating("moderate", "all_day"),
                    _rating("considerable", "later"),
                ],
                "avalancheProblems": [
                    _raw_problem(
                        problem_type="wind_slab",
                        danger_rating_value="moderate",
                        valid_time_period="all_day",
                    ),
                    _raw_problem(
                        problem_type="wet_snow",
                        danger_rating_value="considerable",
                        valid_time_period="later",
                    ),
                ],
                "customData": {
                    "CH": {
                        "aggregation": [
                            {
                                "category": "dry",
                                "validTimePeriod": "all_day",
                                "problemTypes": ["wind_slab"],
                            },
                            {
                                "category": "wet",
                                "validTimePeriod": "later",
                                "problemTypes": ["wet_snow"],
                            },
                        ]
                    }
                },
            },
        }
        _make_am_bulletin(region, day, raw_data=raw_data)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        # Neither axis needs a chip: the title bar names the window in the
        # provider's words, and the Day Risk Profile row carries "Later".
        assert 'data-testid="time-period-pill"' not in content
        assert 'data-testid="category-type-pill"' not in content
        assert 'data-testid="category-pill"' not in content
        # "Later" is the news on a split day and does get a pill, on the
        # Day Risk Profile row.
        assert 'data-testid="day-window-pill"' in content


# ---------------------------------------------------------------------------
# SNOW-292 — ALBINA elevation-band headings
# ---------------------------------------------------------------------------


def _albina_render_model_with_bands(
    traits: list,
) -> dict:
    """Build a current-version ALBINA render_model dict with band_id traits."""
    return {
        "version": RENDER_MODEL_VERSION,
        "source": "ALBINA",
        "danger": {
            "key": "considerable",
            "number": "3",
            "subdivision": None,
            "ratings": [],
        },
        "danger_patterns": [],
        "traits": traits,
        "metadata": {
            "publication_time": "2026-03-15T06:00:00+00:00",
            "valid_from": "2026-03-15T06:00:00+00:00",
            "valid_until": "2026-03-15T15:00:00+00:00",
            "next_update": None,
            "unscheduled": False,
            "lang": "en",
        },
        "prose": {
            "snowpack_structure": "<p>Weak layers.</p>",
            "weather_review": None,
            "weather_forecast": None,
            "tendency": [],
            "avalanche_activity": {
                "highlights": "Persistent weak layers remain the main danger.",
                "comment": "<p>Avalanche danger is considerable above 2200 m.</p>",
            },
            "tendency_lead": None,
        },
    }


def _albina_trait(
    band_id: str,
    elevation: dict | None,
    time_period: str = "all_day",
    category: str = "dry",
    danger_level: int = 3,
) -> dict:
    """Build a minimal ALBINA-style trait with band_id and elevation."""
    return {
        "category": category,
        "time_period": time_period,
        "title": f"{category.capitalize()} avalanches",
        "geography": {"source": "problems"},
        "problems": [
            {
                "problem_type": "persistent_weak_layers",
                "time_period": time_period,
                "elevation": elevation,
                "aspects": ["N", "NE", "E"],
                "comment_html": "",
                "core_zone_text": None,
                "danger_rating_value": "considerable",
                "avalanche_type": None,
                "extras": {},
                "avalanche_size": 3,
                "frequency": "some",
                "snowpack_stability": "poor",
            }
        ],
        "prose": None,
        "danger_level": danger_level,
        "band_id": band_id,
        "elevation": elevation,
    }


@pytest.mark.django_db
class TestAlbinaBandHeadings:
    """Tests for ALBINA elevation-band headings on the bulletin page (SNOW-292)."""

    @pytest.fixture
    def region(self) -> MicroRegion:
        """Return a region suitable for ALBINA bulletin tests."""
        major = MajorRegionFactory.create(prefix="AT-7")
        sub = SubRegionFactory.create(prefix="AT-71", major=major)
        return MicroRegionFactory.create(
            region_id="at-07-23-02",
            subregion=sub,
        )

    def test_elevation_only_bulletin_renders_two_band_headings(
        self,
        client: Client,
        region: MicroRegion,
    ) -> None:
        """ALBINA bulletin with 2-band elevation split renders 2 band-heading elements."""
        day = date(2026, 3, 15)
        above_elev = {
            "lower": 2200,
            "upper": None,
            "treeline": False,
            "treeline_side": None,
        }
        below_elev = {
            "lower": None,
            "upper": 2200,
            "treeline": False,
            "treeline_side": None,
        }
        traits = [
            _albina_trait("above-2200", above_elev, danger_level=3),
            _albina_trait("below-2200", below_elev, danger_level=1),
        ]
        rm = _albina_render_model_with_bands(traits)
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
        )
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "at-07-23-02",
                "slug": region.name_slug,
                "date_str": "2026-03-15",
            },
        )
        content = client.get(url).content.decode()
        # Two band headings should appear.
        heading_count = content.count('data-testid="band-heading"')
        assert heading_count == 2, f"Expected 2 band headings, got {heading_count}"

    def test_band_heading_text_correct(
        self,
        client: Client,
        region: MicroRegion,
    ) -> None:
        """Band headings show 'Above N m' and 'Below N m' labels."""
        day = date(2026, 3, 15)
        above_elev = {
            "lower": 2200,
            "upper": None,
            "treeline": False,
            "treeline_side": None,
        }
        below_elev = {
            "lower": None,
            "upper": 2200,
            "treeline": False,
            "treeline_side": None,
        }
        traits = [
            _albina_trait("above-2200", above_elev, danger_level=3),
            _albina_trait("below-2200", below_elev, danger_level=1),
        ]
        rm = _albina_render_model_with_bands(traits)
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
        )
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "at-07-23-02",
                "slug": region.name_slug,
                "date_str": "2026-03-15",
            },
        )
        content = client.get(url).content.decode()
        assert "Above 2200 m" in content
        assert "Below 2200 m" in content

    def test_slf_bulletin_has_no_band_headings(
        self,
        client: Client,
    ) -> None:
        """SLF bulletin renders zero band-heading elements."""
        day = date(2026, 3, 15)
        major = MajorRegionFactory.create(prefix="CH-4")
        sub = SubRegionFactory.create(prefix="CH-41", major=major)
        region = MicroRegionFactory.create(region_id="ch-4115", subregion=sub)
        traits = [
            {
                "category": "dry",
                "time_period": "all_day",
                "title": "Dry avalanches",
                "geography": {"source": "problems"},
                "problems": [_problem()],
                "prose": None,
                "danger_level": 2,
                "band_id": None,
                "elevation": None,
            }
        ]
        rm = _render_model_with_traits(traits)
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
        )
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert 'data-testid="band-heading"' not in content

    def test_constant_danger_albina_no_band_headings(
        self,
        client: Client,
        region: MicroRegion,
    ) -> None:
        """ALBINA constant-danger bulletin (band_id='all-elevations') has no band headings.

        Production constant-danger ALBINA bulletins have no elevation on their
        problems, so band_id_for_problem returns "all-elevations" (not None).
        This test uses that real production sentinel and asserts no band heading
        is rendered (the "all-elevations" sentinel is not a real elevation split).
        """
        day = date(2026, 3, 15)
        traits: list[dict] = [
            {
                "category": "dry",
                "time_period": "all_day",
                "title": "Dry avalanches",
                "geography": {"source": "problems"},
                "problems": [
                    {
                        "problem_type": "persistent_weak_layers",
                        "time_period": "all_day",
                        "elevation": None,
                        "aspects": ["N"],
                        "comment_html": "",
                        "core_zone_text": None,
                        "danger_rating_value": "considerable",
                        "avalanche_type": None,
                        "extras": {},
                        "avalanche_size": 2,
                        "frequency": "some",
                        "snowpack_stability": "poor",
                    }
                ],
                "prose": None,
                "danger_level": 3,
                "band_id": "all-elevations",
                "elevation": None,
            }
        ]
        rm = _albina_render_model_with_bands(traits)
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
        )
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "at-07-23-02",
                "slug": region.name_slug,
                "date_str": "2026-03-15",
            },
        )
        content = client.get(url).content.decode()
        assert 'data-testid="band-heading"' not in content

    def test_two_by_two_bulletin_renders_band_time_subheader(
        self,
        client: Client,
        region: MicroRegion,
    ) -> None:
        """2×2 ALBINA bulletin (migrating wet line) renders the pivot sub-header.

        The f628 case: wet line at 2500 m earlier, 2800 m later.  Four distinct
        band_ids (above-2500/earlier, below-2500/earlier, above-2800/later,
        below-2800/later) — the sub-header must still appear even though no
        single band_id has both earlier and later traits.
        """
        day = date(2026, 4, 15)
        earlier_above_elev = {
            "lower": 2500,
            "upper": None,
            "treeline": False,
            "treeline_side": None,
        }
        earlier_below_elev = {
            "lower": None,
            "upper": 2500,
            "treeline": False,
            "treeline_side": None,
        }
        later_above_elev = {
            "lower": 2800,
            "upper": None,
            "treeline": False,
            "treeline_side": None,
        }
        later_below_elev = {
            "lower": None,
            "upper": 2800,
            "treeline": False,
            "treeline_side": None,
        }
        traits = [
            _albina_trait(
                "above-2500", earlier_above_elev, time_period="earlier", danger_level=4
            ),
            _albina_trait(
                "below-2500", earlier_below_elev, time_period="earlier", danger_level=2
            ),
            _albina_trait(
                "above-2800", later_above_elev, time_period="later", danger_level=4
            ),
            _albina_trait(
                "below-2800", later_below_elev, time_period="later", danger_level=3
            ),
        ]
        rm = _albina_render_model_with_bands(traits)
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
        )
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "at-07-23-02",
                "slug": region.name_slug,
                "date_str": "2026-04-15",
            },
        )
        content = client.get(url).content.decode()
        assert 'data-testid="band-time-subheader"' in content, (
            "Expected pivot sub-header for migrating wet line"
        )
        assert "2500 m" in content
        assert "2800 m" in content

    def test_stray_unbanded_rating_suppressed_in_day_windows(
        self,
        client: Client,
        region: MicroRegion,
    ) -> None:
        """ALBINA 3-rating shape (banded×2 + stray unbanded) renders 2 day-window rows.

        Some ALBINA bulletins include a redundant unbanded rating alongside the
        two banded ones. The day-windows panel must suppress the unbanded entry and
        show only the two banded rows. The masthead headline must still reflect the
        maximum across all ratings (considerable, level 3).
        """
        day = date(2026, 4, 10)
        above_elev = {
            "lower": 2400,
            "upper": None,
            "treeline": False,
            "treeline_side": None,
        }
        below_elev = {
            "lower": None,
            "upper": 2400,
            "treeline": False,
            "treeline_side": None,
        }
        traits = [
            _albina_trait("above-2400", above_elev, danger_level=2),
            _albina_trait("below-2400", below_elev, danger_level=3),
        ]
        rm = _albina_render_model_with_bands(traits)
        # Inject the 3-rating danger shape: banded×2 + stray unbanded, matching
        # the real-world ALBINA pattern documented in SNOW-292.
        rm["danger"] = {
            "key": "considerable",
            "number": "3",
            "subdivision": None,
            "ratings": [
                {
                    "period": "all_day",
                    "key": "considerable",
                    "subdivision": None,
                    "elevation": below_elev,  # below 2400 m
                },
                {
                    "period": "all_day",
                    "key": "moderate",
                    "subdivision": None,
                    "elevation": above_elev,  # above 2400 m
                },
                {
                    "period": "all_day",
                    "key": "low",
                    "subdivision": None,
                    "elevation": None,  # stray unbanded — must be suppressed
                },
            ],
        }
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
        )
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "at-07-23-02",
                "slug": region.name_slug,
                "date_str": "2026-04-10",
            },
        )
        content = client.get(url).content.decode()

        # The two banded all_day rows render as two chip rows, each with the
        # elevation glyph — the stray unbanded 'low' is suppressed upstream.
        assert content.count('data-testid="day-window-row"') == 2, (
            "Expected the two surviving banded ratings to render two rows"
        )
        assert 'data-testid="day-window-pyramid"' not in content

        # Extract the day-windows panel section for targeted assertions.
        panel_start = content.index('data-testid="day-windows-panel"')
        panel_end = content.index('data-testid="avalanche-problems-heading"')
        panel_html = content[panel_start:panel_end]

        # Both surviving rows carry the elevation glyph.
        assert panel_html.count('data-testid="day-window-elevation-icon"') == 2

        # The suppressed 'low' (level 1) rating must not appear in the panel.
        assert "lv-low" not in panel_html, (
            "Suppressed 'low' rating leaked into the day-windows panel"
        )

        # The surviving rows must still read Considerable (level 3) — suppression
        # must not alter the danger computed outside this function.
        #
        # Asserted against the extracted panel, not a ">Considerable<" adjacency
        # on the whole page. That adjacency held only while the label happened
        # to sit tight against its tags; djangofmt reflows the row the moment
        # anything else joins it, which is what SNOW-727 discovered. The comment
        # here also used to say "masthead" — .dw-level is the only place the
        # page renders a danger label at all.
        assert "Considerable" in panel_html
        assert "lv-considerable" in panel_html


# ---------------------------------------------------------------------------
# Test: ALBINA band-card ordering by descending peak danger (SNOW-292)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAlbinaBandCardOrder:
    """
    Tests that ALBINA elevation-band cards are ordered by descending peak
    danger level so the highest-risk band renders first.

    Covers the reference bulletin shape: considerable below 2400 m (the
    day's headline hazard) should render BEFORE moderate above 2400 m.
    Equal-danger bands fall back to elevation-descending order.
    """

    @pytest.fixture
    def region(self) -> MicroRegion:
        """Return an ALBINA-type region."""
        major = MajorRegionFactory.create(prefix="AT-7")
        sub = SubRegionFactory.create(prefix="AT-71", major=major)
        return MicroRegionFactory.create(region_id="at-07-15", subregion=sub)

    def _make_bulletin_with_bands(
        self,
        region: MicroRegion,
        day: date,
        *,
        above_danger: int,
        below_danger: int,
        pivot: int = 2400,
    ) -> None:
        """Create an ALBINA bulletin with above/below bands at given danger levels."""
        above_elev = {
            "lower": pivot,
            "upper": None,
            "treeline": False,
            "treeline_side": None,
        }
        below_elev = {
            "lower": None,
            "upper": pivot,
            "treeline": False,
            "treeline_side": None,
        }
        traits = [
            _albina_trait(f"above-{pivot}", above_elev, danger_level=above_danger),
            _albina_trait(f"below-{pivot}", below_elev, danger_level=below_danger),
        ]
        rm = _albina_render_model_with_bands(traits)
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
        )

    def test_below_band_renders_first_when_higher_danger(
        self,
        client: Client,
        region: MicroRegion,
    ) -> None:
        """When below-band has higher danger than above-band, it renders first.

        Mirrors the reference bulletin fcb5ffe4 (AT-07-15, 2026-04-10):
        considerable below 2400 m, moderate above 2400 m.  The considerable
        card must appear before the moderate card in the rendered HTML.
        """
        day = date(2026, 4, 10)
        # considerable below 2400 (danger_level=3), moderate above 2400 (danger_level=2)
        self._make_bulletin_with_bands(
            region, day, above_danger=2, below_danger=3, pivot=2400
        )
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "at-07-15",
                "slug": region.name_slug,
                "date_str": "2026-04-10",
            },
        )
        content = client.get(url).content.decode()

        # Extract the positions of the two band headings.
        below_pos = content.find("Below 2400 m")
        above_pos = content.find("Above 2400 m")
        assert below_pos != -1, "Below 2400 m heading not found"
        assert above_pos != -1, "Above 2400 m heading not found"
        assert below_pos < above_pos, (
            f"Expected 'Below 2400 m' (considerable) before 'Above 2400 m' (moderate), "
            f"but found positions {below_pos} and {above_pos}"
        )

    def test_above_band_renders_first_when_higher_danger(
        self,
        client: Client,
        region: MicroRegion,
    ) -> None:
        """When above-band has higher danger, it still renders first."""
        day = date(2026, 4, 11)
        # high above (danger=4), moderate below (danger=2)
        self._make_bulletin_with_bands(
            region, day, above_danger=4, below_danger=2, pivot=2200
        )
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "at-07-15",
                "slug": region.name_slug,
                "date_str": "2026-04-11",
            },
        )
        content = client.get(url).content.decode()

        above_pos = content.find("Above 2200 m")
        below_pos = content.find("Below 2200 m")
        assert above_pos != -1, "Above 2200 m heading not found"
        assert below_pos != -1, "Below 2200 m heading not found"
        assert above_pos < below_pos, (
            f"Expected 'Above 2200 m' (high) before 'Below 2200 m' (moderate), "
            f"but found positions {above_pos} and {below_pos}"
        )

    def test_equal_danger_falls_back_to_elevation_order(
        self,
        client: Client,
        region: MicroRegion,
    ) -> None:
        """When both bands share the same danger level, above renders before below."""
        day = date(2026, 4, 12)
        # Both considerable (danger=3) — elevation tie-break should apply.
        self._make_bulletin_with_bands(
            region, day, above_danger=3, below_danger=3, pivot=2600
        )
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "at-07-15",
                "slug": region.name_slug,
                "date_str": "2026-04-12",
            },
        )
        content = client.get(url).content.decode()

        above_pos = content.find("Above 2600 m")
        below_pos = content.find("Below 2600 m")
        assert above_pos != -1, "Above 2600 m heading not found"
        assert below_pos != -1, "Below 2600 m heading not found"
        assert above_pos < below_pos, (
            "Equal-danger bands should fall back to elevation-descending order "
            f"(above before below), but found above={above_pos}, below={below_pos}"
        )


# ---------------------------------------------------------------------------
# Test: avalanche size rendered as EAWS word (SNOW-292)
# ---------------------------------------------------------------------------


class TestAvalancheSizeLabel:
    """
    Unit tests for ALBINA avalanche-size chip rendering the EAWS word
    instead of the bare integer.
    """

    def _card_from_trait(self, avalanche_size: int | None) -> dict:
        """Build a card dict from a trait with the given avalanche_size."""
        from apps.public.views import _build_single_trait_card

        trait = {
            "category": "dry",
            "time_period": "all_day",
            "title": "Dry avalanches",
            "geography": {"source": "problems"},
            "problems": [
                {
                    "problem_type": "wind_slab",
                    "comment_html": "",
                    "aspects": ["N"],
                    "elevation": {"lower": 2200, "upper": None, "treeline": False},
                    "time_period": "all_day",
                    "core_zone_text": None,
                    "danger_rating_value": "considerable",
                    "avalanche_type": "slab",
                    "avalanche_size": avalanche_size,
                    "frequency": "some",
                    "snowpack_stability": "poor",
                }
            ],
            "prose": None,
            "danger_level": 3,
        }
        card = _build_single_trait_card(trait, [])
        assert card is not None
        return card

    def test_size_3_produces_large_label(self) -> None:
        """avalanche_size=3 maps to the EAWS word 'Large'."""
        card = self._card_from_trait(3)
        assert str(card["avalanche_size_label"]) == "Large"

    def test_size_1_produces_small_label(self) -> None:
        """avalanche_size=1 maps to 'Small'."""
        card = self._card_from_trait(1)
        assert str(card["avalanche_size_label"]) == "Small"

    def test_size_5_produces_extremely_large_label(self) -> None:
        """avalanche_size=5 maps to 'Extremely large'."""
        card = self._card_from_trait(5)
        assert str(card["avalanche_size_label"]) == "Extremely large"

    def test_no_size_produces_none_label(self) -> None:
        """avalanche_size=None leaves avalanche_size_label as None."""
        card = self._card_from_trait(None)
        assert card["avalanche_size_label"] is None


@pytest.mark.django_db
class TestAvalancheSizeChipText:
    """
    Integration test that the size chip in the rendered HTML shows the EAWS
    word rather than 'Size N'.

    Uses the module-level ``region`` fixture (region_id="CH-4115", name="Valais")
    so canonical URL resolution produces ``/ch-4115/valais/<date>/``.
    """

    def _albina_trait(self) -> dict:
        """Build a minimal ALBINA trait with avalanche_size=3."""
        return {
            "category": "dry",
            "time_period": "all_day",
            "title": "Dry avalanches",
            "geography": {"source": "problems"},
            "problems": [
                {
                    "problem_type": "wind_slab",
                    "comment_html": "",
                    "aspects": ["N", "NE"],
                    "elevation": {"lower": 2200, "upper": None, "treeline": False},
                    "time_period": "all_day",
                    "core_zone_text": None,
                    "danger_rating_value": "considerable",
                    "avalanche_type": "slab",
                    "avalanche_size": 3,
                    "frequency": "some",
                    "snowpack_stability": "poor",
                }
            ],
            "prose": None,
            "danger_level": 3,
        }

    def test_size_chip_renders_large_not_size_3(
        self, client: Client, region: MicroRegion
    ) -> None:
        """The EAWS size chip renders 'Large' for avalanche_size=3, not 'Size 3'."""
        day = date(2026, 4, 20)
        rm = _render_model_with_traits([self._albina_trait()])
        rm["source"] = "albina"
        rm["danger_patterns"] = []
        _make_am_bulletin(
            region, day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )
        url = _url("ch-4115", "valais", "2026-04-20")
        content = client.get(url).content.decode()
        assert 'data-testid="eaws-size-chip"' in content
        assert "Large" in content
        assert "Size 3" not in content


# ---------------------------------------------------------------------------
# Test: day-character callout — no leading period when label is empty (SNOW-292)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDayCharacterNoLeadingPeriod:
    """
    Tests that the day-character callout does not emit a leading '.' when
    the DayCharacter label is empty (ALBINA tendency-lead path).
    """

    @pytest.fixture
    def region(self) -> MicroRegion:
        """Return an ALBINA-type region."""
        major = MajorRegionFactory.create(prefix="AT-7")
        sub = SubRegionFactory.create(prefix="AT-72", major=major)
        return MicroRegionFactory.create(region_id="at-07-22", subregion=sub)

    def test_albina_tendency_lead_has_no_leading_period(
        self,
        client: Client,
        region: MicroRegion,
    ) -> None:
        """ALBINA bulletins now show the computed day-character label, not tendency_lead.

        After SNOW-296, _resolve_day_lead always delegates to compute_day_character.
        The tendency_lead prose moves to the tendency outlook block, not the callout.
        The callout must show the computed label (e.g. "Manageable day") and must
        not show the tendency_lead text in the day-character callout area.
        """
        day = date(2026, 4, 15)
        rm = _render_model_with_traits(
            [
                {
                    "category": "wet",
                    "time_period": "all_day",
                    "title": "Wet avalanches",
                    "geography": {"source": "problems"},
                    "problems": [
                        {
                            "problem_type": "wet_snow",
                            "comment_html": "",
                            "aspects": ["S", "SW"],
                            "elevation": None,
                            "time_period": "all_day",
                            "core_zone_text": None,
                            "danger_rating_value": "moderate",
                            "avalanche_type": None,
                            "avalanche_size": None,
                            "frequency": None,
                            "snowpack_stability": None,
                        }
                    ],
                    "prose": None,
                    "danger_level": 2,
                }
            ],
            prose={"tendency_lead": "Increase in danger during the day."},
        )
        rm["source"] = "albina"
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
        )
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "at-07-22",
                "slug": region.name_slug,
                "date_str": "2026-04-15",
            },
        )
        content = client.get(url).content.decode()

        # The computed day-character label must be present (wet_snow at moderate → Manageable).
        assert 'data-testid="day-character-label"' in content
        assert "Manageable day" in content

        # The tendency_lead text must NOT appear anywhere on the page: the
        # callout no longer renders it, and with no tendency entry the outlook
        # block is suppressed. (Outlook-block highlights rendering is covered
        # by TestTendencyOutlook.test_highlights_render_as_supporting_text.)
        assert 'data-testid="day-character-explainer"' in content
        assert "Increase in danger during the day." not in content

    def test_slf_bulletin_still_renders_label_and_period(
        self,
        client: Client,
    ) -> None:
        """SLF bulletins with a non-empty label still render '<label>.' before explainer."""
        day = date(2026, 3, 15)
        major = MajorRegionFactory.create(prefix="CH-4")
        sub = SubRegionFactory.create(prefix="CH-41", major=major)
        region = MicroRegionFactory.create(region_id="ch-4116", subregion=sub)
        # danger=2 + wind_slab → Manageable day (non-empty label)
        trait = {
            "category": "dry",
            "time_period": "all_day",
            "title": "Wind slab",
            "geography": {"source": "problems"},
            "problems": [
                {
                    "problem_type": "wind_slab",
                    "comment_html": "",
                    "aspects": ["N"],
                    "elevation": {"lower": 2200, "upper": None, "treeline": False},
                    "time_period": "all_day",
                    "core_zone_text": None,
                    "danger_rating_value": "moderate",
                    "avalanche_type": None,
                    "avalanche_size": None,
                    "frequency": None,
                    "snowpack_stability": None,
                }
            ],
            "prose": None,
            "danger_level": 2,
        }
        rm = _render_model_with_traits([trait])
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
        )
        content = client.get(
            reverse(
                "public:bulletin_date",
                kwargs={
                    "region_id": "ch-4116",
                    "slug": region.name_slug,
                    "date_str": "2026-03-15",
                },
            )
        ).content.decode()
        # Label element must be present and followed by a period.
        assert 'data-testid="day-character-label"' in content
        assert "Manageable day." in content


# ---------------------------------------------------------------------------
# SNOW-291 — flat-but-split: two dw-row entries + editorial panel titles
# ---------------------------------------------------------------------------


def _flat_split_render_model(
    dry_title: str = "Dry avalanches, whole day",
    wet_title: str = "Wet-snow avalanches, as the day progresses",
    dry_level: int = 2,
    wet_level: int = 2,
    dry_subdivision: str | None = None,
    wet_subdivision: str | None = None,
) -> dict:
    """Build a render model for a flat-but-split day with SLF editorial titles.

    The canonical SNOW-291 fixture: moderate (2-) dry all day, moderate (2)
    wet as the day progresses. The ``danger.ratings`` list carries the
    per-period subdivision suffix so panel cards can resolve it.
    """
    dry_rating: dict = {
        "period": "all_day",
        "key": "moderate",
        "subdivision": dry_subdivision or "",
        "elevation": None,
    }
    wet_rating: dict = {
        "period": "later",
        "key": "moderate",
        "subdivision": wet_subdivision or "",
        "elevation": None,
    }
    dry_trait = {
        "category": "dry",
        "time_period": "all_day",
        "title": dry_title,
        "geography": {"source": "problems"},
        "problems": [
            {
                "problem_type": "wind_slab",
                "comment_html": "<p>Dry slab comment.</p>",
                "aspects": ["N", "NE"],
                "elevation": {"lower": 2200, "upper": None, "treeline": False},
                "time_period": "all_day",
                "core_zone_text": None,
                "danger_rating_value": "moderate",
            }
        ],
        "prose": None,
        "danger_level": dry_level,
    }
    wet_trait = {
        "category": "wet",
        "time_period": "later",
        "title": wet_title,
        "geography": {"source": "problems"},
        "problems": [
            {
                "problem_type": "wet_snow",
                "comment_html": "<p>Wet snow comment.</p>",
                "aspects": ["S", "SW", "SE"],
                "elevation": {"lower": None, "upper": 2400, "treeline": False},
                "time_period": "later",
                "core_zone_text": None,
                "danger_rating_value": "moderate",
            }
        ],
        "prose": None,
        "danger_level": wet_level,
    }
    rm = _render_model_with_traits([dry_trait, wet_trait])
    rm["danger"]["ratings"] = [dry_rating, wet_rating]
    return rm


@pytest.mark.django_db
class TestSnow291FlatButSplit:
    """
    SNOW-291 — flat-but-split day: two rating panels with editorial titles.

    Canonical fixture: CH-4115, moderate (2) dry whole day + moderate (2)
    wet-snow as the day progresses. Same danger level, different problem mix.
    """

    def test_flat_split_renders_two_day_window_rows(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Flat-but-split bulletin renders two dw-row entries on the Day Risk Profile."""
        day = date(2026, 5, 7)
        rm = _flat_split_render_model()
        raw = _raw_data_with_ratings(
            [_rating("moderate", "all_day"), _rating("moderate", "later")]
        )
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
            raw_data=raw,
        )
        url = _url("ch-4115", "valais", "2026-05-07")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 2

    def test_flat_split_renders_two_rating_blocks(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Flat-but-split bulletin renders two rating-block panels."""
        day = date(2026, 5, 8)
        rm = _flat_split_render_model()
        raw = _raw_data_with_ratings(
            [_rating("moderate", "all_day"), _rating("moderate", "later")]
        )
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
            raw_data=raw,
        )

        url = _url("ch-4115", "valais", "2026-05-08")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="rating-block"') == 2

    def test_flat_split_renders_editorial_panel_titles(
        self, client: Client, region: MicroRegion
    ) -> None:
        """Both rating-block panels render their editorial title as panel-title rows."""
        day = date(2026, 5, 9)
        dry_title = "Dry avalanches, whole day"
        wet_title = "Wet-snow avalanches, as the day progresses"
        rm = _flat_split_render_model(
            dry_title=dry_title,
            wet_title=wet_title,
        )
        raw = _raw_data_with_ratings(
            [_rating("moderate", "all_day"), _rating("moderate", "later")]
        )
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
            raw_data=raw,
        )

        url = _url("ch-4115", "valais", "2026-05-09")
        response = client.get(url)
        content = response.content.decode()
        # Both editorial titles must appear in the output.
        assert dry_title in content
        assert wet_title in content
        # Two panel-title rows.
        assert content.count('data-testid="panel-title"') == 2

    def test_flat_split_carries_subdivision_words_in_day_risk_row(
        self, client: Client, region: MicroRegion
    ) -> None:
        """
        Subdivision from danger.ratings reaches the Day Risk Profile row in
        words (SNOW-727).

        Canonical case: the all_day rating has subdivision="-" → the row reads
        "lower end of the band" beside the level word. It used to render only
        as a "2-" glyph on the card's level-number chip, which is gone, and on
        the day-window tile, which is aria-hidden — so no screen reader ever
        reached it.
        """
        day = date(2026, 5, 10)
        rm = _flat_split_render_model(
            dry_subdivision="-",
        )
        raw = _raw_data_with_ratings(
            [_rating("moderate", "all_day", "minus"), _rating("moderate", "later")]
        )
        _make_am_bulletin(
            region,
            day,
            render_model=rm,
            render_model_version=RENDER_MODEL_VERSION,
            raw_data=raw,
        )

        url = _url("ch-4115", "valais", "2026-05-10")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # The subdivision is stated in words on the Day Risk Profile row.
        assert 'data-testid="day-window-subdivision"' in content
        assert "lower end of the band" in content
        # The card's level-number chip is gone (SNOW-727).
        assert 'data-testid="level-number-chip"' not in content


# ---------------------------------------------------------------------------
# Observation counts strip (SNOW-324)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestObservationCountsStrip:
    """Counts strip shows human-readable labels, not raw enum keys."""

    def test_counts_strip_shows_human_labels(self, client: Client) -> None:
        """Observation counts strip renders OBSERVATION_TYPE labels, not raw keys.

        WIND_STRIATIONS / SHOOTING_CRACKS are the most illustrative because their
        raw key contains an underscore that ``lower|title`` would preserve as
        "Wind_Striations" — the bug the fix addresses.  The view must pass
        ``(label, count)`` pairs so the template can render "Wind striations"
        directly.
        """
        from datetime import date as _date

        region = MicroRegionFactory.create(
            region_id="CH-9100", name="Test Region", slug="test-counts"
        )
        today = _date.today()
        _make_am_bulletin(region, today)

        # Fake counts: two types with underscores in their raw keys.
        fake_counts = [
            ("Wind striations", 3),
            ("Shooting cracks", 1),
        ]

        url = reverse("public:region_root", kwargs={"region_id": "ch-9100"})
        with patch(
            "apps.public.views._get_observation_counts", return_value=fake_counts
        ):
            response = client.get(url)

        assert response.status_code == 200
        content = response.content.decode()

        # Human labels must appear.
        assert "Wind striations" in content
        assert "Shooting cracks" in content
        # Raw underscore-joined enum keys must NOT appear.
        assert "WIND_STRIATIONS" not in content
        assert "SHOOTING_CRACKS" not in content
        # The underscored form the old lower|title filter produced must not appear.
        assert "Wind_Striations" not in content
        assert "Shooting_Cracks" not in content
        # Counts themselves must appear.
        assert ">3<" in content or "3" in content
        assert ">1<" in content or "1" in content

    def test_counts_strip_absent_on_historic_page(self, client: Client) -> None:
        """Counts strip is absent on non-today pages (observation_counts is [])."""
        region = MicroRegionFactory.create(
            region_id="CH-9101", name="Historic Region", slug="historic-region"
        )
        past_day = date(2026, 3, 1)
        _make_am_bulletin(region, past_day)

        url = _url("ch-9101", "historic-region", "2026-03-01")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # The section heading must not appear for historic pages.
        assert "Reported today" not in content

    def test_user_located_footnote_shown_when_manual_report_exists(
        self, client: Client
    ) -> None:
        """Footnote 'Some reports were placed manually' appears when a user-located
        report exists for this region today.
        """
        from datetime import date as _date

        region = MicroRegionFactory.create(
            region_id="CH-9102", name="Footnote Region", slug="footnote-region"
        )
        today = _date.today()
        _make_am_bulletin(region, today)

        fake_counts = [("Whumpfing", 2)]

        url = reverse("public:region_root", kwargs={"region_id": "ch-9102"})
        with (
            patch(
                "apps.public.views._get_observation_counts", return_value=fake_counts
            ),
            patch(
                "apps.public.views._get_observation_has_user_located", return_value=True
            ),
        ):
            response = client.get(url)

        assert response.status_code == 200
        assert "Some reports were placed manually" in response.content.decode()

    def test_user_located_footnote_absent_when_no_manual_reports(
        self, client: Client
    ) -> None:
        """Footnote is absent when no user-located (MANUAL/GPS_REFINED) reports
        exist for this region today.
        """
        from datetime import date as _date

        region = MicroRegionFactory.create(
            region_id="CH-9103", name="GPS Only Region", slug="gps-only-region"
        )
        today = _date.today()
        _make_am_bulletin(region, today)

        fake_counts = [("Whumpfing", 1)]

        url = reverse("public:region_root", kwargs={"region_id": "ch-9103"})
        with (
            patch(
                "apps.public.views._get_observation_counts", return_value=fake_counts
            ),
            patch(
                "apps.public.views._get_observation_has_user_located",
                return_value=False,
            ),
        ):
            response = client.get(url)

        assert response.status_code == 200
        assert "Some reports were placed manually" not in response.content.decode()


# ---------------------------------------------------------------------------
# SNOW-296 — tendency outlook block (ALBINA directional arrow + label)
# ---------------------------------------------------------------------------


def _albina_tendency_render_model(
    tendency_type: str | None,
    valid_until: str | None = "2026-04-16T23:59:59+00:00",
    tendency_lead: str = "Conditions will change tomorrow.",
) -> dict:
    """Build an ALBINA-style render model with a tendency entry."""
    tendency_entry: dict = {
        "comment": "",
        "tendency_type": tendency_type,
        "valid_from": "2026-04-15T00:00:00+00:00",
        "valid_until": valid_until,
    }
    return _render_model_with_traits(
        [
            {
                "category": "wet",
                "time_period": "all_day",
                "title": "Wet avalanches",
                "geography": {"source": "problems"},
                "problems": [
                    {
                        "problem_type": "wet_snow",
                        "comment_html": "",
                        "aspects": ["S", "SW"],
                        "elevation": None,
                        "time_period": "all_day",
                        "core_zone_text": None,
                        "danger_rating_value": "moderate",
                        "avalanche_type": None,
                        "avalanche_size": None,
                        "frequency": None,
                        "snowpack_stability": None,
                    }
                ],
                "prose": None,
                "danger_level": 2,
            }
        ],
        prose={
            "tendency": [tendency_entry],
            "tendency_lead": tendency_lead,
        },
    )


@pytest.mark.django_db
class TestTendencyOutlook:
    """Tests for the SNOW-296 tendency outlook block."""

    @pytest.fixture
    def albina_region(self) -> MicroRegion:
        """Return an ALBINA-type micro region."""
        major = MajorRegionFactory.create(prefix="AT-7")
        sub = SubRegionFactory.create(prefix="AT-72", major=major)
        return MicroRegionFactory.create(region_id="at-07-22", subregion=sub)

    def _make_bulletin(
        self, region: MicroRegion, rm: dict, day: date | None = None
    ) -> str:
        """Create a bulletin and return the rendered page content."""
        _day = day or date(2026, 4, 15)
        rm["source"] = "albina"
        _make_am_bulletin(
            region, _day, render_model=rm, render_model_version=RENDER_MODEL_VERSION
        )
        url = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "at-07-22",
                "slug": region.name_slug,
                "date_str": "2026-04-15",
            },
        )
        return Client().get(url).content.decode()

    def test_steady_arrow_and_label(self, albina_region: MicroRegion) -> None:
        """tendency_type='steady' renders → arrow and 'Constant avalanche danger'."""
        rm = _albina_tendency_render_model("steady")
        content = self._make_bulletin(albina_region, rm)
        assert 'data-testid="tendency-outlook"' in content
        assert 'data-testid="tendency-outlook-arrow"' in content
        assert "→" in content
        assert "Constant avalanche danger" in content

    def test_increasing_arrow_and_label(self, albina_region: MicroRegion) -> None:
        """tendency_type='increasing' renders ↗ arrow and 'Increasing avalanche danger'."""
        rm = _albina_tendency_render_model("increasing")
        content = self._make_bulletin(albina_region, rm)
        assert 'data-testid="tendency-outlook"' in content
        assert "↗" in content
        assert "Increasing avalanche danger" in content

    def test_decreasing_arrow_and_label(self, albina_region: MicroRegion) -> None:
        """tendency_type='decreasing' renders ↘ arrow and 'Decreasing avalanche danger'."""
        rm = _albina_tendency_render_model("decreasing")
        content = self._make_bulletin(albina_region, rm)
        assert 'data-testid="tendency-outlook"' in content
        assert "↘" in content
        assert "Decreasing avalanche danger" in content

    def test_valid_until_renders_as_formatted_date(
        self, albina_region: MicroRegion
    ) -> None:
        """valid_until ISO string renders as a formatted date in the outlook block."""
        rm = _albina_tendency_render_model(
            "increasing", valid_until="2026-04-16T23:59:59+00:00"
        )
        content = self._make_bulletin(albina_region, rm)
        assert 'data-testid="tendency-outlook-date"' in content
        # parse_iso|date:"j F Y" → "16 April 2026"
        assert "16 April 2026" in content

    def test_highlights_render_as_supporting_text(
        self, albina_region: MicroRegion
    ) -> None:
        """tendency_lead prose renders as supporting text inside the outlook block."""
        rm = _albina_tendency_render_model(
            "steady", tendency_lead="Stay cautious on north-facing slopes."
        )
        content = self._make_bulletin(albina_region, rm)
        assert 'data-testid="tendency-outlook"' in content
        assert "Stay cautious on north-facing slopes." in content

    def test_outlook_suppressed_when_no_tendency(
        self, albina_region: MicroRegion
    ) -> None:
        """When tendency list is empty, the outlook block is suppressed."""
        rm = _render_model_with_traits(
            [
                {
                    "category": "dry",
                    "time_period": "all_day",
                    "title": "Dry avalanches",
                    "geography": {"source": "problems"},
                    "problems": [
                        {
                            "problem_type": "wind_slab",
                            "comment_html": "",
                            "aspects": ["N"],
                            "elevation": {
                                "lower": 2200,
                                "upper": None,
                                "treeline": False,
                            },
                            "time_period": "all_day",
                            "core_zone_text": None,
                            "danger_rating_value": "moderate",
                            "avalanche_type": None,
                            "avalanche_size": None,
                            "frequency": None,
                            "snowpack_stability": None,
                        }
                    ],
                    "prose": None,
                    "danger_level": 2,
                }
            ],
            prose={"tendency": [], "tendency_lead": None},
        )
        content = self._make_bulletin(albina_region, rm)
        assert 'data-testid="tendency-outlook"' not in content

    def test_outlook_suppressed_when_tendency_type_is_none(
        self, albina_region: MicroRegion
    ) -> None:
        """tendency_type=None suppresses the outlook block (no warning)."""
        rm = _albina_tendency_render_model(None)
        content = self._make_bulletin(albina_region, rm)
        assert 'data-testid="tendency-outlook"' not in content

    def test_unknown_tendency_type_renders_neutral_fallback_and_logs_warning(
        self, albina_region: MicroRegion, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An unknown tendency_type renders a neutral fallback and logs a warning."""
        import logging

        rm = _albina_tendency_render_model("future_unknown_type")
        with caplog.at_level(logging.WARNING, logger="apps.public.views"):
            content = self._make_bulletin(albina_region, rm)

        # The outlook block must still render (neutral fallback, not suppressed).
        assert 'data-testid="tendency-outlook"' in content
        assert "Avalanche danger outlook" in content
        # No directional arrow for the neutral fallback.
        assert 'data-testid="tendency-outlook-arrow"' not in content
        # A warning must have been emitted.
        assert any(
            "unknown tendency_type" in r.message and "future_unknown_type" in r.message
            for r in caplog.records
        )

    def test_outlook_collapsible_not_shown_for_albina_empty_comment(
        self, albina_region: MicroRegion
    ) -> None:
        """ALBINA tendency with empty comment does not show the collapsible Outlook panel."""
        rm = _albina_tendency_render_model("increasing")
        content = self._make_bulletin(albina_region, rm)
        # The tendency panel (collapsible) must not appear.
        assert 'data-testid="tendency-panel"' not in content


@pytest.mark.django_db
class TestArticleOpenGraph:
    """og:type=article and the two article timestamps (SNOW-555).

    A dated avalanche bulletin is dated, revisable content, so ``article``
    is the correct type — and it unlocks ``article:published_time`` and
    ``article:modified_time``, which X and LinkedIn surface on the card.
    That matters more here than usual: a bulletin's value is almost
    entirely a function of how recent it is, so a card carrying no date is
    indistinguishable from one for a bulletin three weeks old.
    """

    def test_dated_bulletin_page_is_an_article(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """og:type is article, not the site-wide website default."""
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert 'property="og:type" content="article"' in content

    def test_both_article_timestamps_are_emitted(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """article:published_time and article:modified_time are both present."""
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert 'property="article:published_time"' in content
        assert 'property="article:modified_time"' in content

    def test_article_times_match_the_json_ld(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """The card and the structured data agree — one derivation feeds both.

        This is the assertion the shared ``_build_article_times`` helper
        exists for. Deriving the two independently is exactly how they
        would drift.
        """
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        data = _extract_jsonld(content)
        assert data is not None
        report = data["mainEntity"]

        published = re.search(
            r'<meta property="article:published_time" content="([^"]*)"', content
        )
        modified = re.search(
            r'<meta property="article:modified_time" content="([^"]*)"', content
        )
        assert published is not None and modified is not None
        assert published.group(1) == report["datePublished"]
        assert modified.group(1) == report["dateModified"]

    def test_article_times_are_timezone_aware(
        self, client: Client, simple_bulletin: Bulletin, region: MicroRegion
    ) -> None:
        """Both timestamps carry an offset, as OG and schema.org expect."""
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        for prop in ("article:published_time", "article:modified_time"):
            match = re.search(rf'<meta property="{prop}" content="([^"]*)"', content)
            assert match is not None, f"{prop} missing"
            # datetime.fromisoformat raises on a malformed value, and a naive
            # one has no tzinfo — both are failures.
            assert datetime.fromisoformat(match.group(1)).tzinfo is not None

    def test_empty_state_page_is_a_website_with_no_article_properties(
        self, client: Client, region: MicroRegion
    ) -> None:
        """A date with no bulletin has no timestamps, so it stays a website."""
        content = client.get(_url("ch-4115", "valais", "2020-01-01")).content.decode()
        assert 'property="og:type" content="website"' in content
        assert "article:published_time" not in content
        assert "article:modified_time" not in content


# ---------------------------------------------------------------------------
# SNOW-670 — the off-schedule (``unscheduled``) marker
# ---------------------------------------------------------------------------


_SENTINEL_DIR = Path(__file__).resolve().parents[1] / "sentinels"


def _albina_unscheduled_props() -> dict[str, Any]:
    """Return the ALBINA A-single-level sentinel's CAAML properties.

    This sentinel is the one committed payload that genuinely carries
    ``unscheduled: true``, so the assertions below are tied to a real
    provider shape rather than a hand-built dict that could drift from
    what ALBINA actually sends.
    """
    path = _SENTINEL_DIR / "albina" / "A-single-level" / "source.json"
    props: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    assert props["unscheduled"] is True, (
        f"{path}: sentinel no longer carries unscheduled=true — these tests "
        "depend on it; pick another sentinel or restore the payload."
    )
    return props


@pytest.mark.django_db
class TestUnscheduledMarker:
    """
    The off-schedule marker and metadata cell on the bulletin page.

    ``unscheduled`` means the provider reissued mid-cycle rather than
    waiting for the normal publication slot. It reaches
    ``render_model["metadata"]["unscheduled"]`` and, before SNOW-670, no
    template read it — the reader had no way to know they were looking at
    a revision.

    Both surfaces are all-or-nothing: a false (or absent) flag renders
    neither, so a normal bulletin carries no trace of the check.
    """

    def _bulletin_for(self, unscheduled: bool | None) -> tuple[MicroRegion, Bulletin]:
        """Build an ALBINA-sentinel bulletin with *unscheduled* set as given.

        Args:
            unscheduled: Value for the payload's ``unscheduled`` key, or
                ``None`` to delete the key entirely.

        Returns:
            The region the bulletin is attached to, and the bulletin.

        """
        props = _albina_unscheduled_props()
        if unscheduled is None:
            props.pop("unscheduled", None)
        else:
            props["unscheduled"] = unscheduled

        region = MicroRegionFactory.create(
            region_id=props["regions"][0]["regionID"],
            name="Allgäu Alps East",
            slug="at-07-01",
        )
        valid_from = datetime.fromisoformat(
            props["validTime"]["startTime"].replace("Z", "+00:00")
        )
        valid_to = datetime.fromisoformat(
            props["validTime"]["endTime"].replace("Z", "+00:00")
        )
        bulletin = BulletinFactory.create(
            source=Bulletin.Source.ALBINA,
            # render_model_version stays at the factory default of 0 so the
            # view rebuilds the render model from raw_data — which is what
            # makes the edited payload above actually reach the page.
            raw_data={"type": "Feature", "geometry": None, "properties": props},
            issued_at=valid_from,
            valid_from=valid_from,
            valid_to=valid_to,
        )
        RegionBulletinFactory.create(
            bulletin=bulletin,
            region=region,
            region_name_at_time=region.name,
        )
        return region, bulletin

    def _render(self, client: Client, unscheduled: bool | None) -> str:
        """Render the bulletin page for a payload with the given flag."""
        region, bulletin = self._bulletin_for(unscheduled)
        response = client.get(region.get_absolute_url(bulletin.target_date))
        assert response.status_code == 200
        return response.content.decode()

    def test_marker_renders_when_flag_is_true(self, client: Client) -> None:
        """An off-schedule bulletin carries the header marker."""
        content = self._render(client, True)
        assert 'data-testid="unscheduled-marker"' in content
        assert "Updated off-schedule" in content

    def test_marker_names_the_publication_time(self, client: Client) -> None:
        """The marker body carries the reissue time, not just the fact of it.

        Knowing a bulletin was reissued is only actionable next to *when* —
        that is what tells a reader whether the version they saw earlier
        predates it.

        The assertion is scoped to the marker's own markup on purpose: the
        metadata strip renders the same timestamp in its Issued cell, so a
        whole-page substring search would pass with the marker body empty.
        """
        content = self._render(client, True)
        marker = re.search(
            r'<div data-testid="unscheduled-marker">(.*?)</div>\s*</div>',
            content,
            re.S,
        )
        assert marker is not None, "unscheduled marker not found in the page"
        # publicationTime is 2025-11-28T18:06:01Z.
        assert "28 Nov 18:06 UTC" in marker.group(1)

    def test_metadata_cell_renders_when_flag_is_true(self, client: Client) -> None:
        """The metadata strip gains a Schedule / Off-schedule cell."""
        content = self._render(client, True)
        assert 'data-testid="unscheduled-cell"' in content
        assert "Off-schedule" in content

    def test_neither_surface_renders_when_flag_is_false(self, client: Client) -> None:
        """A normally-scheduled bulletin shows no marker and no extra cell."""
        content = self._render(client, False)
        assert 'data-testid="unscheduled-marker"' not in content
        assert 'data-testid="unscheduled-cell"' not in content
        assert "off-schedule" not in content.lower()

    def test_neither_surface_renders_when_key_is_absent(self, client: Client) -> None:
        """A payload with no ``unscheduled`` key behaves as if false."""
        content = self._render(client, None)
        assert 'data-testid="unscheduled-marker"' not in content
        assert 'data-testid="unscheduled-cell"' not in content

    def test_strings_are_translated(self, client: Client) -> None:
        """Both user-facing strings go through the translation catalogue.

        Rendering under a locale with no catalogue entries still yields the
        English source strings, so this asserts the wrapping rather than a
        translation: it fails if a later edit inlines a bare literal, which
        ``makemessages`` would then never see.
        """
        region, bulletin = self._bulletin_for(True)
        url = region.get_absolute_url(bulletin.target_date)
        with language_override("de"):
            content = client.get(
                url, headers={"accept-language": "de"}
            ).content.decode()
        assert 'data-testid="unscheduled-marker"' in content
        assert 'data-testid="unscheduled-cell"' in content
