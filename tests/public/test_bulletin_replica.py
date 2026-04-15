"""
tests/public/test_bulletin_replica.py — Tests for the WhiteRisk-replica bulletin template.

Covers the ?replica=1 query-flag behaviour in bulletin_detail, plus structural
assertions on the six sections of bulletin_replica.html.

Fixtures use the same helper pattern as test_bulletin_detail.py (AM/PM bulletin
factories) to stay consistent with the existing test suite.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse

from tests.factories import BulletinFactory, RegionBulletinFactory, RegionFactory

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
        "fallback_key_message": None,
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

    The view's ``_enrich_render_model`` converts these to the richer shape
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def region():
    """Return a test Region."""
    return RegionFactory.create(region_id="CH-4115", name="Valais", slug="ch-4115")


@pytest.fixture()
def simple_bulletin(region):
    """A bulletin with one dry trait (simple day)."""
    day = date(2026, 3, 15)
    rm = _render_model_with_traits([_dry_trait_problems([_problem()])])
    return _make_am_bulletin(region, day, render_model=rm, render_model_version=3)


@pytest.fixture()
def variable_bulletin(region):
    """A bulletin with two traits (variable day — dry morning, wet afternoon)."""
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
    return _make_am_bulletin(region, day, render_model=rm, render_model_version=3)


def _url(region_id: str, slug: str, date_str: str, replica: bool = False) -> str:
    """Build the bulletin date URL, optionally appending ?replica=1."""
    base = reverse(
        "public:bulletin_date",
        kwargs={"region_id": region_id, "slug": slug, "date_str": date_str},
    )
    return f"{base}?replica=1" if replica else base


# ---------------------------------------------------------------------------
# Test: flag gates template selection
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReplicaFlag:
    """?replica=1 selects bulletin_replica.html; default returns bulletin.html."""

    def test_default_renders_bulletin_html(
        self, client: Client, simple_bulletin, region
    ):
        """Without ?replica=1 the standard bulletin.html is rendered."""
        url = _url("CH-4115", "valais", "2026-03-15", replica=False)
        response = client.get(url)
        assert response.status_code == 200
        assert "public/bulletin.html" in [t.name for t in response.templates]
        assert "public/bulletin_replica.html" not in [
            t.name for t in response.templates
        ]

    def test_replica_flag_renders_replica_html(
        self, client: Client, simple_bulletin, region
    ):
        """With ?replica=1 the bulletin_replica.html is rendered."""
        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        assert response.status_code == 200
        assert "public/bulletin_replica.html" in [t.name for t in response.templates]

    def test_replica_flag_other_values_do_not_select_replica(
        self, client: Client, simple_bulletin, region
    ):
        """?replica=0 or ?replica=yes do not select the replica template."""
        for val in ("0", "yes", "true", ""):
            url = _url("CH-4115", "valais", "2026-03-15") + f"?replica={val}"
            response = client.get(url)
            assert response.status_code == 200
            assert "public/bulletin_replica.html" not in [
                t.name for t in response.templates
            ], f"replica={val!r} should not select replica template"


# ---------------------------------------------------------------------------
# Test: headline band
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHeadlineBand:
    """Bulletin headline band renders correctly for simple and variable days."""

    def test_simple_day_single_rating(self, client: Client, simple_bulletin, region):
        """A bulletin with 1 trait shows a single rating in the headline band."""
        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # Panel danger label comes from _build_panel_context's danger_meta
        assert "Moderate" in content
        # No transition arrow for simple days
        assert 'data-testid="transition-arrow"' not in content

    def test_variable_day_two_ratings_with_arrow(
        self, client: Client, variable_bulletin, region
    ):
        """A bulletin with 2 traits shows both ratings and a → arrow."""
        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        # Both labels should appear
        assert "Moderate" in content
        assert "Considerable" in content
        # Transition arrow via data-testid
        assert 'data-testid="transition-arrow"' in content
        # The HTML entity for →
        assert "&#8594;" in content or "→" in content


# ---------------------------------------------------------------------------
# Test: rating blocks count matches traits
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRatingBlockCount:
    """Number of rendered rating blocks equals number of traits."""

    def test_one_trait_one_block(self, client: Client, simple_bulletin, region):
        """One trait produces exactly one rating block."""
        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="rating-block"') == 1

    def test_two_traits_two_blocks(self, client: Client, variable_bulletin, region):
        """Two traits produce exactly two rating blocks."""
        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="rating-block"') == 2


# ---------------------------------------------------------------------------
# Test: aspect/elevation row presence
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAspectElevationRow:
    """Aspect/elevation row is present only when geography.source == 'problems'."""

    def test_row_present_when_geography_is_problems(
        self, client: Client, simple_bulletin, region
    ):
        """Rating block has aspect/elevation row when geography.source is 'problems'."""
        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="aspect-elevation-row"' in content

    def test_row_absent_when_geography_is_prose_only(self, client: Client, region):
        """No aspect/elevation row when geography.source is 'prose_only'."""
        day = date(2026, 3, 15)
        wet_trait = _wet_trait_prose(
            "<p>Wet snow on south-facing slopes below treeline.</p>"
        )
        rm = _render_model_with_traits([wet_trait])
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="aspect-elevation-row"' not in content

    def test_row_absent_when_problem_has_no_aspects_or_elevation(
        self, client: Client, region
    ):
        """Row absent when problems branch but first problem has neither aspects nor elevation."""
        day = date(2026, 3, 15)
        trait = {
            "category": "dry",
            "time_period": "all_day",
            "title": "Dry avalanches",
            "geography": {"source": "problems"},
            "problems": [_problem_no_geo()],
            "prose": None,
            "danger_level": 2,
        }
        rm = _render_model_with_traits([trait])
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="aspect-elevation-row"' not in content


# ---------------------------------------------------------------------------
# Test: SLF prose in full, no truncation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProseFull:
    """Problem prose comment appears verbatim and in full in the output."""

    def test_full_prose_comment_rendered(self, client: Client, region):
        """The full text of a problem's comment_html appears verbatim in the response."""
        day = date(2026, 3, 15)
        full_prose = (
            "<p>Wind slabs have formed on the lee side of ridges and in gullies. "
            "They can be released even by low additional loads. "
            "Careful route selection is essential. "
            "Particularly dangerous are north and east facing slopes above 2200m.</p>"
        )
        problem = _problem(comment_html=full_prose)
        trait = _dry_trait_problems([problem])
        rm = _render_model_with_traits([trait])
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
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

        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="weather-review-heading"' not in content

    def test_weather_review_rendered_when_present(self, client: Client, region):
        """'Weather review' heading and content appear when prose.weather_review is set."""
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

        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="weather-review-heading"' in content
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

        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="snowpack-weather-section"' not in content

    def test_weather_forecast_heading_rendered(self, client: Client, region):
        """'Weather forecast' heading appears when prose.weather_forecast is set."""
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

        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="weather-forecast-heading"' in content
        assert "Warm and sunny tomorrow" in content

    def test_outlook_rendered_from_tendency(self, client: Client, region):
        """Outlook sub-block renders tendency comments."""
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

        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="outlook-heading"' in content
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

        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
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

        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="metadata-strip"' in content
        # "06:00" appears in the issued/valid fields
        assert "06:00" in content
        # "15:00" appears in the valid-until and next-update fields
        assert "15:00" in content


# ---------------------------------------------------------------------------
# Test: footer focal region first
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFooter:
    """Footer renders focal region before related regions."""

    def test_focal_region_appears_in_footer(
        self, client: Client, simple_bulletin, region
    ):
        """The focal region name appears in the footer."""
        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="focal-region"' in content
        assert "Valais" in content

    def test_focal_region_before_related(self, client: Client, region):
        """Focal region appears before any related regions in the HTML."""
        day = date(2026, 3, 15)
        rm = _render_model_with_traits([_dry_trait_problems([_problem()])])
        bulletin = _make_am_bulletin(
            region, day, render_model=rm, render_model_version=3
        )

        # Add a second region linked to the same bulletin
        other_region = RegionFactory.create(name="Münstertal", slug="ch-4116")
        RegionBulletinFactory.create(
            bulletin=bulletin,
            region=other_region,
            region_name_at_time="Münstertal",
        )

        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        content = response.content.decode()

        focal_pos = content.find('data-testid="focal-region"')
        related_pos = content.find("Münstertal")
        assert focal_pos != -1
        assert related_pos != -1
        assert focal_pos < related_pos, (
            "Focal region should appear before related regions"
        )


# ---------------------------------------------------------------------------
# Test: font-sans class on outermost container
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTypography:
    """The replica's outermost container carries font-sans to prevent serif leakage."""

    def test_font_sans_on_container(self, client: Client, simple_bulletin, region):
        """The outermost replica container has the font-sans class."""
        url = _url("CH-4115", "valais", "2026-03-15", replica=True)
        response = client.get(url)
        content = response.content.decode()
        # The outermost div must carry font-sans
        assert 'class="font-sans' in content
