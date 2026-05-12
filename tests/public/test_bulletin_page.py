"""
tests/public/test_bulletin_page.py — Tests for the WhiteRisk-replica bulletin template.

Covers structural assertions on the six sections of bulletin.html as rendered
by the bulletin_detail view.

Fixtures use the same helper pattern as test_bulletin_detail.py (AM bulletin
factories) to stay consistent with the existing test suite.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_am_bulletin(region, day, **kwargs):
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
    """Build a minimal v3 render_model dict for testing."""
    return {
        "version": 3,
        "danger": {"key": "moderate", "number": "2", "subdivision": None},
        "traits": traits,
        "snowpack_structure": None,
        "metadata": metadata
        or {
            "publication_time": "2026-03-15T06:00:00+00:00",
            "valid_from": "2026-03-15T06:00:00+00:00",
            "valid_until": "2026-03-15T15:00:00+00:00",
            "next_update": "2026-03-15T15:00:00+00:00",
            "unscheduled": False,
            "lang": "en",
        },
        "prose": prose
        or {
            "snowpack_structure": "<p>The snowpack is generally stable.</p>",
            "weather_review": None,
            "weather_forecast": None,
            "tendency": [],
        },
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

    ``public.views.enrich_render_model`` converts these to the richer shape
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
        "properties": {"dangerRatings": ratings},
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
def region():
    """Return a test Region."""
    return MicroRegionFactory.create(region_id="CH-4115", name="Valais", slug="ch-4115")


@pytest.fixture()
def simple_bulletin(region):
    """A bulletin with one dry problem (simple day)."""
    day = date(2026, 3, 15)
    rm = _render_model_with_traits([_dry_trait_problems([_problem()])])
    raw = _raw_data_with_problems([_raw_problem()])
    return _make_am_bulletin(
        region, day, render_model=rm, render_model_version=3, raw_data=raw
    )


@pytest.fixture()
def variable_bulletin(region):
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
        region, day, render_model=rm, render_model_version=3, raw_data=raw_data
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

    def test_renders_bulletin_html(self, client: Client, simple_bulletin, region):
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

    def test_one_trait_one_block(self, client: Client, simple_bulletin, region):
        """One trait produces exactly one rating block."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="rating-block"') == 1

    def test_two_traits_two_blocks(self, client: Client, variable_bulletin, region):
        """Two traits produce exactly two rating blocks."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="rating-block"') == 2


# ---------------------------------------------------------------------------
# Test: aspect/elevation row presence
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAspectElevationRow:
    """Aspect/elevation row is present when the first problem has aspects or elevation."""

    def test_row_present_when_problem_has_aspects_and_elevation(
        self, client: Client, simple_bulletin, region
    ):
        """Rating block has aspect/elevation row when the first problem has geographic data."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="aspect-elevation-row"' in content

    def test_row_absent_when_problem_has_no_aspects_or_elevation(
        self, client: Client, region
    ):
        """No aspect/elevation row when the first problem has neither aspects nor elevation."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_problems([_raw_problem_no_geo(problem_type="wet_snow")])
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="aspect-elevation-row"' not in content

    def test_row_absent_when_aspects_empty_but_elevation_present(
        self, client: Client, region
    ):
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

    def test_full_prose_comment_rendered(self, client: Client, region):
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

    def test_weather_review_skipped_when_none(self, client: Client, region):
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
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="weather-review-heading"' not in content

    def test_weather_review_rendered_when_present(self, client: Client, region):
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
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="snowpack-weather-section"' in content
        assert "Cold and clear overnight" in content

    def test_snowpack_section_absent_when_all_prose_empty(self, client: Client, region):
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
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="snowpack-weather-section"' not in content

    def test_weather_forecast_rendered_when_present(self, client: Client, region):
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
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="snowpack-weather-section"' in content
        assert "Warm and sunny tomorrow" in content

    def test_outlook_rendered_from_tendency(self, client: Client, region):
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
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

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

    def test_none_next_update_renders_em_dash(self, client: Client, region):
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
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        # Em-dash character should appear in the next-update cell
        assert "—" in content or "&mdash;" in content

    def test_valid_timestamps_render(self, client: Client, region):
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
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="metadata-strip"' in content
        # "06:00" appears in the issued/valid fields
        assert "06:00" in content
        # "15:00" appears in the valid-until and next-update fields
        assert "15:00" in content


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

    def test_no_page_footer_landmark(self, client: Client, simple_bulletin, region):
        """The page-local footer landmark is gone."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="page-footer"' not in content
        assert 'data-testid="focal-region"' not in content

    def test_sibling_regions_not_rendered_anywhere_on_page(
        self, client: Client, region
    ):
        """A sibling region's name does not appear in the rendered HTML.

        On a multi-region bulletin, the focal region's bulletin page
        must not name the other regions covered by the same bulletin —
        adjacent regions are surfaced from the map, not the bulletin.
        """
        day = date(2026, 3, 15)
        rm = _render_model_with_traits([_dry_trait_problems([_problem()])])
        bulletin = _make_am_bulletin(
            region, day, render_model=rm, render_model_version=3
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
        self, client: Client, region
    ) -> None:
        """region.name is shown on the page header even when region_name_at_time disagrees."""
        rm = _render_model_with_traits([_dry_trait_problems([_problem()])])
        bulletin = BulletinFactory.create(
            issued_at=datetime(2026, 3, 15, 6, 0, tzinfo=UTC) - timedelta(minutes=30),
            valid_from=datetime(2026, 3, 15, 6, 0, tzinfo=UTC),
            valid_to=datetime(2026, 3, 15, 15, 0, tzinfo=UTC),
            render_model=rm,
            render_model_version=3,
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

    def test_font_sans_on_container(self, client: Client, simple_bulletin, region):
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
        self, client: Client, simple_bulletin, region
    ):
        """Response carries the bulletin UUID in ``X-Bulletin-Id``."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        assert response.status_code == 200
        assert response["X-Bulletin-Id"] == str(simple_bulletin.bulletin_id)

    def test_x_bulletin_id_header_absent_on_empty_state(self, client: Client, region):
        """No bulletin → no ``X-Bulletin-Id`` header."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        assert response.status_code == 200
        assert "X-Bulletin-Id" not in response

    def test_raw_data_embedded_when_debug_true(self, client: Client, region, settings):
        """DEBUG=True → raw_data JSON embedded in page source."""
        settings.DEBUG = True
        day = date(2026, 3, 17)
        bulletin = _make_am_bulletin(
            region,
            day,
            render_model=_render_model_with_traits([_dry_trait_problems([_problem()])]),
            render_model_version=3,
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
        self, client: Client, simple_bulletin, region, settings
    ):
        """DEBUG=False → no raw_data script tag, header still present."""
        settings.DEBUG = False
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="bulletin-raw-data"' not in content
        assert response["X-Bulletin-Id"] == str(simple_bulletin.bulletin_id)

    def test_script_breakout_payload_is_escaped(self, client: Client, region, settings):
        """A ``</script>`` substring in raw_data must not break out of the tag."""
        settings.DEBUG = True
        day = date(2026, 3, 18)
        _make_am_bulletin(
            region,
            day,
            render_model=_render_model_with_traits([_dry_trait_problems([_problem()])]),
            render_model_version=3,
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

    def test_single_problem_produces_one_block(self, client: Client, region):
        """One raw problem → one rating block."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_problems([_raw_problem()])
        _make_am_bulletin(region, day, raw_data=raw)
        response = client.get(_url("ch-4115", "valais", "2026-03-15"))
        assert response.content.decode().count('data-testid="rating-block"') == 1

    def test_two_problems_same_kind_and_level_produce_two_blocks(
        self, client: Client, region
    ):
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
        self, client: Client, region
    ):
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
        self, client: Client, region
    ):
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

    def test_three_problems_produce_three_blocks_in_order(self, client: Client, region):
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
        self, client: Client, region
    ):
        """Problem with no aspects and no elevation → no aspect/elevation row."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_problems([_raw_problem_no_geo(problem_type="wet_snow")])
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert 'data-testid="rating-block"' in content
        assert 'data-testid="aspect-elevation-row"' not in content

    def test_problem_labels_appear_in_cards(self, client: Client, region):
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

    def test_empty_problems_shows_no_problems_card(self, client: Client, region):
        """Bulletin with avalancheProblems=[] → 'No avalanche problems reported.' empty state."""
        day = date(2026, 3, 15)
        raw = _raw_data_with_problems([])
        _make_am_bulletin(region, day, raw_data=raw)
        content = client.get(_url("ch-4115", "valais", "2026-03-15")).content.decode()
        assert 'data-testid="rating-block"' not in content
        assert "No avalanche problems reported." in content


# ---------------------------------------------------------------------------
# Test: aggregation-driven card ordering and titles (SNOW-135)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAggregationDriven:
    """
    When customData.CH.aggregation is present, cards are built from it:
    one card per (aggregation entry, problem type), in aggregation order.
    """

    def test_aggregation_order_preserved(self, client: Client, region):
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
        self, client: Client, region
    ):
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

    def test_core_zone_text_as_aria_label(self, client: Client, region):
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

    def test_default_renders_panel(self, client: Client, variable_bulletin, region):
        """The day-windows panel renders by default — no headline band."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="day-windows-panel"' in content
        assert 'data-testid="headline-band"' not in content

    def test_renders_day_risk_profile_heading_above_panel(
        self, client: Client, variable_bulletin, region
    ):
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

    def test_all_day_only_renders_single_row(self, client: Client, region):
        """A bulletin with only an all_day rating renders one row, chip = 'All day'."""
        day = date(2026, 3, 19)
        raw = _raw_data_with_ratings([_rating("moderate", "all_day")])
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-19")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 1
        assert 'data-window="all_day"' in content
        assert ">All day<" in content

    def test_two_row_pills_read_all_day_and_later(self, client: Client, region):
        """all_day + later cross-category → two rows with chips 'All day' / 'Later'."""
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
        assert ">All day<" in panel_html
        assert ">Later<" in panel_html
        assert ">Earlier<" not in panel_html

    def test_tile_carries_lv_class_and_level_number(self, client: Client, region):
        """The numbered tile uses ``lv-{level}`` so EAWS tokens drive the colour."""
        day = date(2026, 3, 20)
        raw = _raw_data_with_ratings([_rating("considerable", "all_day")])
        _make_am_bulletin(region, day, raw_data=raw)

        url = _url("ch-4115", "valais", "2026-03-20")
        response = client.get(url)
        content = response.content.decode()
        assert "dw-tile lv-considerable" in content
        assert "Considerable" in content

    def test_caption_is_absent(self, client: Client, region):
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

    def test_single_all_day_considerable_badge(self, client: Client, region):
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
        assert ">All day<" in content

    def test_sublevel_modifier_minus_on_badge(self, client: Client, region):
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

    def test_cross_category_later_up_renders_two_rows(self, client: Client, region):
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
        self, client: Client, region
    ):
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

    def test_cross_category_later_down_suppressed(self, client: Client, region):
        """all_day considerable minus + later moderate (cross-band lower) → 1 row (suppressed)."""
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
        assert content.count('data-testid="day-window-row"') == 1

    # ------------------------------------------------------------------
    # later_ filter — within-category sublevel shift (always shown)
    # ------------------------------------------------------------------

    def test_within_category_later_up_renders_two_rows_with_badge_differential(
        self, client: Client, region
    ):
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

    def test_within_category_later_down_suppressed(self, client: Client, region):
        """all_day moderate plus + later moderate minus (within-band lower) → 1 row (suppressed)."""
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
        assert content.count('data-testid="day-window-row"') == 1

    # ------------------------------------------------------------------
    # later_ filter — same-band no-op (filtered)
    # ------------------------------------------------------------------

    def test_same_band_noop_considerable_filtered(self, client: Client, region):
        """all_day considerable neutral + later considerable → 1 row (later filtered)."""
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
        assert content.count('data-testid="day-window-row"') == 1

    def test_same_band_noop_moderate_filtered(self, client: Client, region):
        """all_day moderate neutral + later moderate → 1 row (later filtered)."""
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
        assert content.count('data-testid="day-window-row"') == 1

    # ------------------------------------------------------------------
    # later_ filter — cross-band lower (always suppressed)
    # ------------------------------------------------------------------

    def test_cross_band_lower_considerable_to_moderate_suppressed(
        self, client: Client, region
    ):
        """all_day considerable + later moderate (lower band) → 1 row."""
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
        assert content.count('data-testid="day-window-row"') == 1

    def test_same_band_plus_blocks_plain_later(self, client: Client, region):
        """all_day moderate plus + later moderate plain (lower sub) → 1 row."""
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
        assert content.count('data-testid="day-window-row"') == 1

    def test_same_band_minus_to_plain_shows_two_rows(self, client: Client, region):
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
# Test: bulletin page content — subregion names, day-risk panel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBulletinPageContent:
    """
    Miscellaneous content assertions for the bulletin page that are not
    tied to a specific partial — subregion name resolution and the
    day-risk-profile panel that sits below the header.
    """

    def test_subregion_uses_english_name_when_present(self, simple_bulletin, region):
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
        self, simple_bulletin, region
    ):
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
        self, client: Client, variable_bulletin, region
    ):
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
    """

    def test_renders_sheet_and_trigger_when_season_active(
        self, client: Client, simple_bulletin, region
    ):
        """A bulletin with a populated season grid renders trigger + closed sheet."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-season-sheet="closed"' in content
        assert "data-season-trigger" in content
        assert 'data-testid="season-sheet"' in content

    def test_omits_sheet_when_season_grid_empty(
        self, client: Client, simple_bulletin, region
    ):
        """With SEASON_START_DATE in the future, build_season_grid is empty and the sheet is omitted."""
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

    def test_today_cell_carries_today_modifier(
        self, client: Client, simple_bulletin, region
    ):
        """Today's cell in the heatmap is flagged with calendar-cell-today."""
        # The cell is keyed by date alone — no RegionDayRating row needed
        # for the modifier class to render.
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert "calendar-cell-today" in content

    def test_selected_cell_carries_selected_modifier(self, client: Client, region):
        """A non-today page_date renders the cell with calendar-cell-selected."""
        # Pin "today" two days after the page date so is_selected is True
        # for the page-date cell (the SeasonGrid suppresses is_selected
        # when the page date coincides with today).
        page_day = date(2026, 3, 13)
        rm = _render_model_with_traits([_dry_trait_problems([_problem()])])
        _make_am_bulletin(region, page_day, render_model=rm, render_model_version=3)
        with patch(
            "public.views.timezone.now",
            return_value=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        ):
            url = _url("ch-4115", "valais", "2026-03-13")
            response = client.get(url)
        content = response.content.decode()
        assert "calendar-cell-selected" in content


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

    def test_renders_label_and_explainer(self, client: Client, simple_bulletin, region):
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
        self, client: Client, region
    ):
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
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("ch-4115", "valais", "2026-03-20")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="day-character"' in content
        assert "Hard-to-read day" in content
        assert "favicon.svg" in content
        assert "<strong" in content

    def test_callout_precedes_day_risk_profile_heading(
        self, client: Client, simple_bulletin, region
    ):
        """The callout banner sits above the Day Risk Profile heading in DOM order."""
        url = _url("ch-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        callout_idx = content.index('data-testid="day-character"')
        heading_idx = content.index('data-testid="day-risk-profile-heading"')
        assert callout_idx < heading_idx

    def test_callout_absent_in_error_state(self, client: Client, region):
        """A version=0 error bulletin replaces the body and suppresses the callout."""
        from bulletins.services.render_model import RENDER_MODEL_VERSION

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
