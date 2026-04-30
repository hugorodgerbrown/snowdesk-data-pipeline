"""
tests/public/test_bulletin_page.py — Tests for the WhiteRisk-replica bulletin template.

Covers structural assertions on the six sections of bulletin.html as rendered
by the bulletin_detail view.

Fixtures use the same helper pattern as test_bulletin_detail.py (AM bulletin
factories) to stay consistent with the existing test suite.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse

from tests.factories import (
    BulletinFactory,
    RegionBulletinFactory,
    RegionFactory,
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
        url = _url("CH-4115", "valais", "2026-03-15")
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
        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="rating-block"') == 1

    def test_two_traits_two_blocks(self, client: Client, variable_bulletin, region):
        """Two traits produce exactly two rating blocks."""
        url = _url("CH-4115", "valais", "2026-03-15")
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
        url = _url("CH-4115", "valais", "2026-03-15")
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

        url = _url("CH-4115", "valais", "2026-03-15")
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

        url = _url("CH-4115", "valais", "2026-03-15")
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

        url = _url("CH-4115", "valais", "2026-03-15")
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

        url = _url("CH-4115", "valais", "2026-03-15")
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

        url = _url("CH-4115", "valais", "2026-03-15")
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

        url = _url("CH-4115", "valais", "2026-03-15")
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

        url = _url("CH-4115", "valais", "2026-03-15")
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

        url = _url("CH-4115", "valais", "2026-03-15")
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

        url = _url("CH-4115", "valais", "2026-03-15")
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

        url = _url("CH-4115", "valais", "2026-03-15")
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
        url = _url("CH-4115", "valais", "2026-03-15")
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
        other_region = RegionFactory.create(name="Münstertal", slug="ch-4116")
        RegionBulletinFactory.create(
            bulletin=bulletin,
            region=other_region,
            region_name_at_time="Münstertal",
        )

        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert "Münstertal" not in content


# ---------------------------------------------------------------------------
# Test: font-sans class on outermost container
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTypography:
    """The outermost container carries font-sans to prevent serif leakage."""

    def test_font_sans_on_container(self, client: Client, simple_bulletin, region):
        """The outermost container has the font-sans class."""
        url = _url("CH-4115", "valais", "2026-03-15")
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
        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        assert response.status_code == 200
        assert response["X-Bulletin-Id"] == str(simple_bulletin.bulletin_id)

    def test_x_bulletin_id_header_absent_on_empty_state(self, client: Client, region):
        """No bulletin → no ``X-Bulletin-Id`` header."""
        url = _url("CH-4115", "valais", "2026-03-15")
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
        url = _url("CH-4115", "valais", "2026-03-17")
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
        url = _url("CH-4115", "valais", "2026-03-15")
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
        url = _url("CH-4115", "valais", "2026-03-18")
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
# Test: rating-block DOM order mirrors render_model.traits (aggregation order)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRatingBlockOrder:
    """
    Rating blocks must appear in the DOM in exactly the order given by
    ``render_model.traits``, which itself is taken verbatim from SLF's
    aggregation (see docs/day_character_rules_spec.md and the builder
    ordering guarantee in pipeline/services/render_model.py). Reordering
    at any layer (enrichment, panel context, or template loop) would
    violate SLF's editorial intent, so we assert the DOM order matches.
    """

    @staticmethod
    def _extract_dom_order(content: str) -> list[str]:
        """Return the sequence of category+level icons in DOM order."""
        import re

        segments = [
            content[m.start() : m.start() + 800]
            for m in re.finditer(r'data-testid="rating-block"', content)
        ]
        order: list[str] = []
        for seg in segments:
            m = re.search(r"(Dry|Wet)-Snow-([0-9\-]+)\.svg", seg)
            order.append(m.group(0) if m else "?")
        return order

    def test_dry_allday_before_wet_later(self, client: Client, region):
        """
        Mirrors bulletin 1931's shape: aggregation = [dry/all_day/L1,
        wet/later/L3]. The rendered DOM must show the dry (Low) block
        BEFORE the wet (Considerable) block — never the reverse.
        """
        day = date(2026, 3, 15)
        dry_trait = {
            "category": "dry",
            "time_period": "all_day",
            "title": "Dry avalanches, whole day",
            "geography": {"source": "problems"},
            "problems": [_problem_no_geo(problem_type="no_distinct_avalanche_problem")],
            "prose": None,
            "danger_level": 1,
        }
        wet_trait = {
            "category": "wet",
            "time_period": "later",
            "title": "Wet-snow avalanches, as the day progresses",
            "geography": {"source": "problems"},
            "problems": [_problem(problem_type="wet_snow")],
            "prose": None,
            "danger_level": 3,
        }
        rm = _render_model_with_traits([dry_trait, wet_trait])
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        assert response.status_code == 200

        order = self._extract_dom_order(response.content.decode())
        assert order == ["Dry-Snow-1.svg", "Wet-Snow-3.svg"], (
            f"rating blocks appeared in wrong DOM order: {order} "
            "(expected aggregation order: dry/L1 first, wet/L3 second)"
        )

    def test_wet_first_when_aggregation_lists_wet_first(self, client: Client, region):
        """
        When aggregation orders wet BEFORE dry (as in some SLF bulletins),
        the DOM must honour that — never silently reshuffle to a
        dry-first convention.
        """
        day = date(2026, 3, 16)
        wet_trait = {
            "category": "wet",
            "time_period": "all_day",
            "title": "Wet-snow avalanches",
            "geography": {"source": "problems"},
            "problems": [_problem(problem_type="wet_snow")],
            "prose": None,
            "danger_level": 3,
        }
        dry_trait = {
            "category": "dry",
            "time_period": "all_day",
            "title": "Dry avalanches",
            "geography": {"source": "problems"},
            "problems": [_problem_no_geo(problem_type="no_distinct_avalanche_problem")],
            "prose": None,
            "danger_level": 1,
        }
        rm = _render_model_with_traits([wet_trait, dry_trait])
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("CH-4115", "valais", "2026-03-16")
        response = client.get(url)
        assert response.status_code == 200

        order = self._extract_dom_order(response.content.decode())
        assert order == ["Wet-Snow-3.svg", "Dry-Snow-1.svg"], (
            f"rating blocks appeared in wrong DOM order: {order} "
            "(expected aggregation order: wet/L3 first, dry/L1 second)"
        )


# ---------------------------------------------------------------------------
# Test: Day Windows panel (SNOW-70 — design_handoff_day_windows)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDayWindowsPanel:
    """
    Day Windows panel — the day's hazard summary above the rating blocks.

    One row per validTimePeriod (earlier / all_day / later); ordered
    chronologically with all_day in the middle when present. Each row
    carries a numbered EAWS tile, the level name, an editorial caption
    derived from the matching trait's title, and a window pill.
    """

    def test_default_renders_panel(self, client: Client, variable_bulletin, region):
        """The day-windows panel renders by default — no headline band."""
        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="day-windows-panel"' in content
        assert 'data-testid="headline-band"' not in content

    def test_renders_day_risk_profile_heading_above_panel(
        self, client: Client, variable_bulletin, region
    ):
        """The 'Day Risk Profile' h2 sits above the day-windows panel."""
        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="day-risk-profile-heading"' in content
        assert "Day Risk Profile" in content
        # Heading must precede the panel in DOM order.
        heading_idx = content.index('data-testid="day-risk-profile-heading"')
        panel_idx = content.index('data-testid="day-windows-panel"')
        assert heading_idx < panel_idx

    def test_split_day_renders_two_rows_earlier_then_later(
        self, client: Client, region
    ):
        """A day with earlier+later traits renders two rows in that order."""
        # Mirror the variable_bulletin fixture but with explicit time periods
        # and one problem each so the panel caption (concatenated problem-type
        # labels) is populated.
        day = date(2026, 3, 18)
        earlier_trait: dict = {
            "category": "dry",
            "time_period": "earlier",
            "title": "Dry avalanches, morning",
            "geography": {"source": "problems"},
            "problems": [_problem(problem_type="wind_slab")],
            "prose": None,
            "danger_level": 2,
        }
        later_trait: dict = {
            "category": "wet",
            "time_period": "later",
            "title": "Wet-snow avalanches, as the day progresses",
            "geography": {"source": "problems"},
            "problems": [_problem(problem_type="wet_snow")],
            "prose": None,
            "danger_level": 3,
        }
        rm = _render_model_with_traits([earlier_trait, later_trait])
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("CH-4115", "valais", "2026-03-18")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 2
        # Ordered earlier then later.
        earlier_idx = content.index('data-window="earlier"')
        later_idx = content.index('data-window="later"')
        assert earlier_idx < later_idx
        # Each row's caption is the problem-type label for that period —
        # not the trait's editorial title.
        assert "Wind slab" in content
        assert "Wet snow" in content

    def test_all_day_only_renders_single_row(self, client: Client, region):
        """An all_day-only bulletin collapses to one row labelled ``All day``."""
        day = date(2026, 3, 19)
        rm = _render_model_with_traits([_dry_trait_problems([_problem()])])
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("CH-4115", "valais", "2026-03-19")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 1
        assert 'data-window="all_day"' in content
        # Pill copy comes from _DAY_WINDOW_PILL_LABELS.
        assert ">All day<" in content

    def test_two_row_bulletin_pills_read_earlier_then_later(
        self, client: Client, region
    ):
        """
        When two rows render (any combination of underlying periods), the
        pills are re-labelled as "Earlier" + "Later" in DOM order so the
        panel reads as chronological brackets even when the underlying
        data is something like (all_day, later).
        """
        day = date(2026, 3, 23)
        all_day_trait: dict = {
            "category": "dry",
            "time_period": "all_day",
            "title": "Persistent weak layers",
            "geography": {"source": "problems"},
            "problems": [_problem(problem_type="persistent_weak_layers")],
            "prose": None,
            "danger_level": 2,
        }
        later_trait: dict = {
            "category": "wet",
            "time_period": "later",
            "title": "Wet snow",
            "geography": {"source": "problems"},
            "problems": [_problem(problem_type="wet_snow")],
            "prose": None,
            "danger_level": 3,
        }
        rm = _render_model_with_traits([all_day_trait, later_trait])
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("CH-4115", "valais", "2026-03-23")
        response = client.get(url)
        content = response.content.decode()
        # Two rows, ordered: the all_day row sits first, the later row second.
        assert content.count('data-testid="day-window-row"') == 2
        all_day_idx = content.index('data-window="all_day"')
        later_idx = content.index('data-window="later"')
        assert all_day_idx < later_idx
        # The all_day row's pill is rebadged to "Earlier"; the later row's
        # pill stays "Later". "All day" must NOT appear inside the panel.
        panel_start = content.index('data-testid="day-windows-panel"')
        panel_end = content.index('data-testid="avalanche-problems-heading"')
        panel_html = content[panel_start:panel_end]
        assert ">Earlier<" in panel_html
        assert ">Later<" in panel_html
        assert ">All day<" not in panel_html

    def test_three_row_bulletin_pills_read_earlier_all_day_later(
        self, client: Client, region
    ):
        """Three rows keep the natural Earlier / All day / Later sequence."""
        day = date(2026, 3, 24)
        rm = _render_model_with_traits(
            [
                {
                    "category": "dry",
                    "time_period": "earlier",
                    "title": "Wind slab, morning",
                    "geography": {"source": "problems"},
                    "problems": [_problem(problem_type="wind_slab")],
                    "prose": None,
                    "danger_level": 2,
                },
                {
                    "category": "dry",
                    "time_period": "all_day",
                    "title": "Persistent weak layer",
                    "geography": {"source": "problems"},
                    "problems": [_problem(problem_type="persistent_weak_layers")],
                    "prose": None,
                    "danger_level": 2,
                },
                {
                    "category": "wet",
                    "time_period": "later",
                    "title": "Wet snow",
                    "geography": {"source": "problems"},
                    "problems": [_problem(problem_type="wet_snow")],
                    "prose": None,
                    "danger_level": 3,
                },
            ]
        )
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("CH-4115", "valais", "2026-03-24")
        response = client.get(url)
        content = response.content.decode()
        panel_start = content.index('data-testid="day-windows-panel"')
        panel_end = content.index('data-testid="avalanche-problems-heading"')
        panel_html = content[panel_start:panel_end]
        # All three pill labels must render with three rows.
        assert ">Earlier<" in panel_html
        assert ">All day<" in panel_html
        assert ">Later<" in panel_html

    def test_three_window_day_renders_three_rows_chronological(
        self, client: Client, region
    ):
        """Earlier + all_day + later → three rows in chronological order."""
        day = date(2026, 3, 20)
        rm = _render_model_with_traits(
            [
                {
                    "category": "dry",
                    "time_period": "earlier",
                    "title": "Wind slab, morning",
                    "geography": {"source": "problems"},
                    "problems": [],
                    "prose": None,
                    "danger_level": 2,
                },
                {
                    "category": "dry",
                    "time_period": "all_day",
                    "title": "Persistent weak layer",
                    "geography": {"source": "problems"},
                    "problems": [],
                    "prose": None,
                    "danger_level": 2,
                },
                {
                    "category": "wet",
                    "time_period": "later",
                    "title": "Wet-snow avalanches, as the day progresses",
                    "geography": {"source": "problems"},
                    "problems": [],
                    "prose": None,
                    "danger_level": 3,
                },
            ]
        )
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("CH-4115", "valais", "2026-03-20")
        response = client.get(url)
        content = response.content.decode()
        assert content.count('data-testid="day-window-row"') == 3
        earlier_idx = content.index('data-window="earlier"')
        all_day_idx = content.index('data-window="all_day"')
        later_idx = content.index('data-window="later"')
        assert earlier_idx < all_day_idx < later_idx

    def test_tile_carries_lv_class_and_level_number(self, client: Client, region):
        """The numbered tile uses ``lv-{level}`` so EAWS tokens drive the colour."""
        day = date(2026, 3, 21)
        rm = _render_model_with_traits(
            [
                {
                    "category": "wet",
                    "time_period": "all_day",
                    "title": "Wet-snow avalanches",
                    "geography": {"source": "problems"},
                    "problems": [],
                    "prose": None,
                    "danger_level": 3,
                }
            ]
        )
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("CH-4115", "valais", "2026-03-21")
        response = client.get(url)
        content = response.content.decode()
        # lv-considerable + the digit 3 inside the tile.
        assert "dw-tile lv-considerable" in content
        # Level label rendered.
        assert "Considerable" in content

    def test_caption_concatenates_problem_type_labels(self, client: Client, region):
        """Caption joins every covering trait's problem-type labels (deduped)."""
        day = date(2026, 3, 22)
        rm = _render_model_with_traits(
            [
                {
                    "category": "dry",
                    "time_period": "all_day",
                    "title": "Wind slab",
                    "geography": {"source": "problems"},
                    "problems": [_problem(problem_type="wind_slab")],
                    "prose": None,
                    "danger_level": 1,
                },
                {
                    "category": "wet",
                    "time_period": "all_day",
                    "title": "Wet-snow avalanches",
                    "geography": {"source": "problems"},
                    "problems": [_problem(problem_type="wet_snow")],
                    "prose": None,
                    "danger_level": 3,
                },
            ]
        )
        _make_am_bulletin(region, day, render_model=rm, render_model_version=3)

        url = _url("CH-4115", "valais", "2026-03-22")
        response = client.get(url)
        content = response.content.decode()
        # Single all_day row at the higher level (3 / Considerable). Caption
        # lists every problem type from all covering traits in render-model
        # order: Wind slab (from the dry L1 trait) then Wet snow (from the
        # wet L3 trait). The trait titles themselves are not used.
        assert content.count('data-testid="day-window-row"') == 1
        assert "dw-tile lv-considerable" in content
        assert "Wind slab, Wet snow" in content


# ---------------------------------------------------------------------------
# Test: bulletin masthead (date+calendar above region, subregion H2)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBulletinMasthead:
    """
    Bulletin masthead — date eyebrow + calendar trigger sit above the
    region H1, and the parent EAWS L2 sub-region renders as a quiet H2
    below the H1. The calendar drawer is the only date picker; the top
    nav is wayfinding-only.
    """

    def test_renders_masthead(self, client: Client, simple_bulletin, region):
        """The masthead landmark renders on the bulletin page by default."""
        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="bulletin-masthead"' in content

    def test_eyebrow_precedes_region_h1(self, client: Client, simple_bulletin, region):
        """Date eyebrow + calendar trigger sit above the region H1 in DOM order."""
        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        eyebrow_idx = content.index('class="bm-eyebrow"')
        region_idx = content.index('class="bm-region"')
        assert eyebrow_idx < region_idx

    def test_renders_subregion_h2_below_h1(
        self, client: Client, simple_bulletin, region
    ):
        """Parent EAWS L2 sub-region renders as an H2 below the region H1."""
        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="bulletin-masthead-subregion"' in content
        # Region H1 precedes the subregion H2.
        region_idx = content.index('class="bm-region"')
        subregion_idx = content.index('data-testid="bulletin-masthead-subregion"')
        assert region_idx < subregion_idx

    def test_subregion_uses_english_name_when_present(self, simple_bulletin, region):
        """``EawsSubRegion.name_en`` wins over native when SLF publishes one."""
        sub = region.subregion
        sub.name_en = "Lower Valais"
        sub.name_native = "Bas-Valais"
        sub.save(update_fields=["name_en", "name_native"])
        url = _url("CH-4115", "valais", "2026-03-15")
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
        url = _url("CH-4115", "valais", "2026-03-15")
        response = Client().get(url)
        content = response.content.decode()
        assert "Bas-Valais" in content

    def test_calendar_trigger_inline_in_masthead(
        self, client: Client, simple_bulletin, region
    ):
        """The calendar HTMX button sits in the masthead, not the top nav."""
        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="bm-calendar-trigger"' in content
        # And the trigger sits inside the masthead landmark.
        masthead_idx = content.index('data-testid="bulletin-masthead"')
        trigger_idx = content.index('data-testid="bm-calendar-trigger"')
        assert masthead_idx < trigger_idx

    def test_renders_map_deep_link_beside_h1(
        self, client: Client, simple_bulletin, region
    ):
        """A map-pin link to ``/map/#<region_id>`` sits beside the region H1.

        The map's existing hash router opens the bottom sheet for the
        region at peek state on load (SNOW-81 / SNOW-63 auto-zoom).
        """
        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="bm-map-link"' in content
        expected_href = f'href="{reverse("public:map")}#CH-4115"'
        assert expected_href in content
        # H1 precedes the map link in DOM order; both sit inside the row.
        region_idx = content.index('class="bm-region"')
        link_idx = content.index('data-testid="bm-map-link"')
        assert region_idx < link_idx

    def test_top_nav_omits_calendar_button(
        self, client: Client, simple_bulletin, region
    ):
        """The top nav is wayfinding-only — no calendar button."""
        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        # Only one calendar button on the page (in the masthead, not the nav).
        assert content.count("Show monthly calendar") == 1
        nav_block_end = content.index("</nav>")
        assert "Show monthly calendar" not in content[:nav_block_end]

    def test_still_renders_day_risk_profile_panel(
        self, client: Client, variable_bulletin, region
    ):
        """The Day Risk Profile heading + day-windows panel render below."""
        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="day-risk-profile-heading"' in content
        assert 'data-testid="day-windows-panel"' in content


# ---------------------------------------------------------------------------
# Test: calendar drawer outside-click dismissal
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCalendarDismissScript:
    """
    The bulletin page mounts a small inline script that closes the
    calendar drawer (``#bulletin-calendar-host``) when the user clicks
    anywhere outside the drawer or its trigger button. The script lives
    in the page chrome so v1/v2/v3/v4 all benefit from the same dismiss.
    """

    def test_dismiss_script_is_present(self, client: Client, simple_bulletin, region):
        """The dismiss listener is rendered on the bulletin page."""
        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        # The listener targets the calendar host and matches any trigger
        # by its hx-target attribute.
        assert "bulletin-calendar-host" in content
        assert "hx-target=&quot;#bulletin-calendar-host&quot;" in content or (
            'hx-target="#bulletin-calendar-host"' in content
        )


# ---------------------------------------------------------------------------
# Test: day-character eyebrow (SNOW-8)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDayCharacterEyebrow:
    """
    Day-character eyebrow above the Day Risk Profile heading.

    The eyebrow surfaces the label produced by
    ``compute_day_character`` along with a one-line static explainer.
    It is suppressed in the error state (``render_model.version == 0``)
    where the bulletin body is replaced by a warning panel.
    """

    def test_renders_label_and_explainer(self, client: Client, simple_bulletin, region):
        """A normal bulletin renders the label + explainer in the eyebrow."""
        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        # simple_bulletin is danger=2 with a wind_slab problem → the
        # cascade resolves to Manageable day.
        assert 'data-testid="day-character"' in content
        assert 'data-testid="day-character-label"' in content
        assert "Manageable day" in content
        assert 'data-testid="day-character-explainer"' in content

    def test_renders_hard_to_read_for_persistent_weak_layers(
        self, client: Client, region
    ):
        """A bulletin with persistent weak layers renders the hard-to-read label."""
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

        url = _url("CH-4115", "valais", "2026-03-20")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="day-character"' in content
        assert "Hard-to-read day" in content

    def test_eyebrow_precedes_day_risk_profile_heading(
        self, client: Client, simple_bulletin, region
    ):
        """The eyebrow sits above the Day Risk Profile heading in DOM order."""
        url = _url("CH-4115", "valais", "2026-03-15")
        response = client.get(url)
        content = response.content.decode()
        eyebrow_idx = content.index('data-testid="day-character"')
        heading_idx = content.index('data-testid="day-risk-profile-heading"')
        assert eyebrow_idx < heading_idx

    def test_eyebrow_absent_in_error_state(self, client: Client, region):
        """A version=0 error bulletin replaces the body and suppresses the eyebrow."""
        from pipeline.services.render_model import RENDER_MODEL_VERSION

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
        url = _url("CH-4115", "valais", "2026-03-21")
        response = client.get(url)
        content = response.content.decode()
        assert 'data-testid="day-character"' not in content
