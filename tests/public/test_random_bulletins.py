"""
tests/public/test_random_bulletins.py — Tests for the random_bulletins view.

Covers the ``random_bulletins`` view and its private helpers in
``public.views``: ``_highest_danger_key``, ``_build_panel_context``,
``_format_elevation``, and ``_parse_bulletin_count``. The view now lists the
most recent bulletins for a single region (one per calendar day, reverse
chronological) with an optional ``?b=N`` query parameter.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse

from pipeline.models import Bulletin, Region
from public.views import (
    _build_panel_context,
    _format_elevation,
    _highest_danger_key,
    _parse_bulletin_count,
)
from tests.factories import BulletinFactory, RegionBulletinFactory, RegionFactory


def _wrap(properties: dict[str, Any]) -> dict[str, Any]:
    """Wrap a CAAML properties dict in a GeoJSON Feature envelope."""
    return {"type": "Feature", "geometry": None, "properties": properties}


def _make_bulletin(**properties: Any) -> Bulletin:
    """
    Create a Bulletin with the given CAAML properties.

    Keyword arguments are inserted into the ``properties`` dict of the
    GeoJSON envelope stored in ``raw_data``.
    """
    return BulletinFactory.create(
        raw_data=_wrap(properties),
        valid_from=datetime(2025, 2, 1, 7, 0, tzinfo=UTC),
        valid_to=datetime(2025, 2, 1, 16, 0, tzinfo=UTC),
    )


def _make_aggregation(
    problems: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Auto-generate aggregation entries from a list of CAAML problems.

    Groups problems by (category, validTimePeriod). Category is derived from
    problemType using PROBLEM_TYPE_TO_CATEGORY. Each unique (category, period)
    combination becomes one aggregation entry, preserving problem order.

    Args:
        problems: CAAML avalancheProblems list.

    Returns:
        A list of aggregation entry dicts suitable for customData.CH.aggregation.

    """
    from pipeline.services.render_model import PROBLEM_TYPE_TO_CATEGORY

    seen: dict[tuple[str, str], list[str]] = {}
    for p in problems:
        pt = p.get("problemType", "")
        vtp = p.get("validTimePeriod") or "all_day"
        cat = PROBLEM_TYPE_TO_CATEGORY.get(pt, "dry")
        key = (cat, vtp)
        if key not in seen:
            seen[key] = []
        if pt not in seen[key]:
            seen[key].append(pt)

    return [
        {
            "category": cat,
            "validTimePeriod": vtp,
            "problemTypes": pts,
            "title": f"{cat.capitalize()} avalanches",
        }
        for (cat, vtp), pts in seen.items()
    ]


def _make_region_bulletin(
    region: Region,
    day: date,
    *,
    main_value: str = "moderate",
    problems: list[dict[str, Any]] | None = None,
    problem_type: str = "wind_slab",
    aggregation: list[dict[str, Any]] | None = None,
) -> Bulletin:
    """
    Create a Bulletin valid on ``day`` and link it to ``region``.

    The bulletin's ``valid_from`` is 06:00 UTC and ``valid_to`` is 16:00
    UTC on ``day`` (mimicking the SLF morning issue shape). Pass
    ``problems`` to supply a full CAAML avalancheProblems list; otherwise a
    single problem of ``problem_type`` with no comment/period is used.

    Aggregation is auto-generated from problems when not provided explicitly.

    Args:
        region: The Region to link the bulletin to.
        day: The date the bulletin covers.
        main_value: The danger rating main value.
        problems: CAAML avalancheProblems list. Defaults to a single wind_slab.
        problem_type: Used as default problem type when ``problems`` is None.
        aggregation: Explicit aggregation list. Auto-generated when None.

    Returns:
        The created and linked Bulletin instance.

    """
    if problems is None:
        problems = [{"problemType": problem_type}]
    if aggregation is None:
        aggregation = _make_aggregation(problems)
    valid_from = datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)
    valid_to = datetime(day.year, day.month, day.day, 16, 0, tzinfo=UTC)
    bulletin = BulletinFactory.create(
        raw_data=_wrap(
            {
                "dangerRatings": [{"mainValue": main_value}],
                "avalancheProblems": problems,
                "regions": [{"name": region.name, "regionID": region.region_id}],
                "customData": {"CH": {"aggregation": aggregation}},
            }
        ),
        issued_at=valid_from - timedelta(minutes=30),
        valid_from=valid_from,
        valid_to=valid_to,
    )
    RegionBulletinFactory.create(
        bulletin=bulletin,
        region=region,
        region_name_at_time=region.name,
    )
    return bulletin


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHighestDangerKey:
    """Tests for ``_highest_danger_key``."""

    def test_returns_low_for_empty_ratings(self) -> None:
        """An empty ratings list defaults to ``low`` with no subdivision."""
        assert _highest_danger_key([]) == ("low", "")

    def test_returns_highest_when_multiple_ratings(self) -> None:
        """The highest EAWS value across all entries wins."""
        ratings = [
            {"mainValue": "moderate"},
            {"mainValue": "high"},
            {"mainValue": "low"},
        ]
        assert _highest_danger_key(ratings) == ("high", "")

    def test_unknown_values_are_ignored(self) -> None:
        """Unrecognised values should not confuse the ordering."""
        ratings = [{"mainValue": "moderate"}, {"mainValue": "definitely_not_valid"}]
        assert _highest_danger_key(ratings) == ("moderate", "")

    def test_very_high_beats_high(self) -> None:
        """``very_high`` outranks ``high``."""
        ratings = [{"mainValue": "high"}, {"mainValue": "very_high"}]
        assert _highest_danger_key(ratings) == ("very_high", "")

    def test_subdivision_minus(self) -> None:
        """A ``minus`` subdivision returns the minus sign suffix."""
        ratings = [
            {
                "mainValue": "high",
                "customData": {"CH": {"subdivision": "minus"}},
            }
        ]
        assert _highest_danger_key(ratings) == ("high", "-")

    def test_subdivision_plus(self) -> None:
        """A ``plus`` subdivision returns the ``+`` suffix."""
        ratings = [
            {
                "mainValue": "moderate",
                "customData": {"CH": {"subdivision": "plus"}},
            }
        ]
        assert _highest_danger_key(ratings) == ("moderate", "+")

    def test_subdivision_neutral(self) -> None:
        """A ``neutral`` subdivision returns the ``=`` suffix."""
        ratings = [
            {
                "mainValue": "considerable",
                "customData": {"CH": {"subdivision": "neutral"}},
            }
        ]
        assert _highest_danger_key(ratings) == ("considerable", "=")

    def test_subdivision_from_highest_rating(self) -> None:
        """The subdivision comes from the highest-rated entry."""
        ratings = [
            {
                "mainValue": "moderate",
                "customData": {"CH": {"subdivision": "plus"}},
            },
            {
                "mainValue": "considerable",
                "customData": {"CH": {"subdivision": "minus"}},
            },
        ]
        assert _highest_danger_key(ratings) == ("considerable", "-")


class TestFormatElevation:
    """Tests for ``_format_elevation``."""

    def test_none_returns_empty(self) -> None:
        """``None`` elevation → falsy ElevationBounds."""
        result = _format_elevation(None)
        assert not result
        assert result.display == ""
        assert result.bound_type == ""

    def test_empty_dict_returns_empty(self) -> None:
        """Empty dict → falsy ElevationBounds."""
        result = _format_elevation({})
        assert not result
        assert result.bound_type == ""

    def test_lower_bound_only(self) -> None:
        """``lowerBound`` only → ``above Xm`` with LOWER bound type."""
        result = _format_elevation({"lowerBound": "2200"})
        assert result.display == "above 2200m"
        assert result.lower == "2200"
        assert result.upper == ""
        assert result.bound_type == "LOWER"

    def test_upper_bound_only(self) -> None:
        """``upperBound`` only → ``below Xm`` with UPPER bound type."""
        result = _format_elevation({"upperBound": "2400"})
        assert result.display == "below 2400m"
        assert result.lower == ""
        assert result.upper == "2400"
        assert result.bound_type == "UPPER"

    def test_both_bounds(self) -> None:
        """Both bounds → ``X\u2013Ym`` (en-dash) with BOTH bound type."""
        result = _format_elevation({"lowerBound": "1800", "upperBound": "2400"})
        assert result.display == "1800\u20132400m"
        assert result.lower == "1800"
        assert result.upper == "2400"
        assert result.bound_type == "BOTH"

    def test_treeline_literal(self) -> None:
        """``treeline`` is emitted as-is, without the ``m`` suffix."""
        result = _format_elevation({"lowerBound": "treeline"})
        assert result.display == "above treeline"
        assert result.lower == "treeline"
        assert result.bound_type == "LOWER"
        result = _format_elevation({"upperBound": "treeline"})
        assert result.display == "below treeline"
        assert result.upper == "treeline"
        assert result.bound_type == "UPPER"


# ---------------------------------------------------------------------------
# _build_panel_context
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildPanelContext:
    """Tests for ``_build_panel_context``."""

    def test_low_bulletin_context_shape(self) -> None:
        """A low-danger bulletin produces the expected context keys."""
        bulletin = _make_bulletin(
            dangerRatings=[{"mainValue": "low"}],
            avalancheProblems=[
                {
                    "problemType": "no_distinct_avalanche_problem",
                    "comment": "<p>No distinct problem to speak of.</p>",
                    "validTimePeriod": "all_day",
                    "elevation": {"lowerBound": "2200"},
                }
            ],
            regions=[{"name": "Jura", "regionID": "CH-2000"}],
        )
        ctx = _build_panel_context(bulletin)

        assert ctx["bulletin"] is bulletin
        assert ctx["danger_key"] == "low"
        assert ctx["danger_css"] == "low"
        assert ctx["danger_number"] == "1"
        assert ctx["danger_subdivision"] == ""
        assert ctx["danger_label"] == "Low"
        assert ctx["danger_icon"] == "Dry-Snow-1.svg"
        assert ctx["key_message"] == "<p>No distinct problem to speak of.</p>"
        assert ctx["footer_date_from"] == bulletin.valid_from
        assert ctx["footer_date_to"] == bulletin.valid_to

        # Provenance strings — one per visible field, for the tooltips.
        assert ctx["danger_source"] == "dangerRatings[*].mainValue (highest)"
        assert ctx["key_message_source"] == "avalancheProblems[0].comment"
        assert ctx["footer_date_source"] == "Bulletin.valid_from / valid_to"
        # Admin URL always populated; template gates it on user.is_staff.
        assert ctx["admin_url"] == f"/admin/pipeline/bulletin/{bulletin.pk}/change/"

    def test_snowpack_structure_extracted(self) -> None:
        """Snowpack structure comment is passed through as raw HTML."""
        bulletin = _make_bulletin(
            dangerRatings=[{"mainValue": "low"}],
            avalancheProblems=[],
            snowpackStructure={"comment": "<p>Deep weak layers.</p>"},
        )
        ctx = _build_panel_context(bulletin)
        assert ctx["snowpack_structure"] == "<p>Deep weak layers.</p>"

    def test_snowpack_structure_empty_when_absent(self) -> None:
        """Missing snowpackStructure gives an empty string."""
        bulletin = _make_bulletin(
            dangerRatings=[{"mainValue": "low"}],
            avalancheProblems=[],
        )
        ctx = _build_panel_context(bulletin)
        assert ctx["snowpack_structure"] == ""

    def test_bulletin_with_no_raw_data_defaults_to_low(self) -> None:
        """A bulletin with empty raw_data still renders a valid context."""
        bulletin = BulletinFactory.create(raw_data={})
        ctx = _build_panel_context(bulletin)
        assert ctx["danger_key"] == "low"
        assert ctx["key_message"] == ""
        assert ctx["key_message_source"] == ""
        assert ctx["snowpack_structure"] == ""

    def test_highest_rating_drives_panel_colour(self) -> None:
        """The panel picks up the highest rating across a bulletin."""
        bulletin = _make_bulletin(
            dangerRatings=[
                {"mainValue": "moderate"},
                {"mainValue": "high"},
            ],
            avalancheProblems=[],
            regions=[],
        )
        ctx = _build_panel_context(bulletin)
        assert ctx["danger_key"] == "high"
        assert ctx["danger_number"] == "4"

    def test_very_high_uses_hyphenated_css_key(self) -> None:
        """``very_high`` collapses to ``very-high`` for CSS class names."""
        bulletin = _make_bulletin(
            dangerRatings=[{"mainValue": "very_high"}],
            avalancheProblems=[],
            regions=[],
        )
        ctx = _build_panel_context(bulletin)
        assert ctx["danger_key"] == "very_high"
        assert ctx["danger_css"] == "very-high"

    def test_key_message_falls_back_to_snowpack(self) -> None:
        """With no problem comment, snowpackStructure is the fallback."""
        bulletin = _make_bulletin(
            dangerRatings=[{"mainValue": "low"}],
            avalancheProblems=[{"problemType": "wet_snow"}],
            snowpackStructure={"comment": "<p>Deep weak layers.</p>"},
        )
        ctx = _build_panel_context(bulletin)
        assert ctx["key_message"] == "<p>Deep weak layers.</p>"
        assert ctx["key_message_source"] == "snowpackStructure.comment"

    def test_key_message_falls_back_to_weather_review(self) -> None:
        """With no problem or snowpack comment, weatherReview is used."""
        bulletin = _make_bulletin(
            dangerRatings=[{"mainValue": "low"}],
            avalancheProblems=[],
            weatherReview={"comment": "<p>Light snow overnight.</p>"},
        )
        ctx = _build_panel_context(bulletin)
        assert ctx["key_message"] == "<p>Light snow overnight.</p>"
        assert ctx["key_message_source"] == "weatherReview.comment"

    def test_subdivision_included_in_context(self) -> None:
        """The CH subdivision suffix is passed through to the template."""
        bulletin = _make_bulletin(
            dangerRatings=[
                {
                    "mainValue": "considerable",
                    "customData": {"CH": {"subdivision": "plus"}},
                }
            ],
            avalancheProblems=[],
        )
        ctx = _build_panel_context(bulletin)
        assert ctx["danger_number"] == "3"
        assert ctx["danger_subdivision"] == "+"

    def test_key_message_not_truncated_by_view(self) -> None:
        """Full text is passed through — the template truncates instead."""
        long_text = "<p>" + "word " * 100 + "</p>"
        bulletin = _make_bulletin(
            dangerRatings=[{"mainValue": "low"}],
            avalancheProblems=[{"problemType": "new_snow", "comment": long_text}],
        )
        ctx = _build_panel_context(bulletin)
        assert len(ctx["key_message"]) > 240


# ---------------------------------------------------------------------------
# _parse_bulletin_count
# ---------------------------------------------------------------------------


class TestParseBulletinCount:
    """Tests for ``_parse_bulletin_count``."""

    def test_missing_param_returns_default(self) -> None:
        """No ``?b=`` → default of 10."""
        request = RequestFactory().get("/ch-4115/random/")
        assert _parse_bulletin_count(request) == 10

    def test_valid_integer_is_returned(self) -> None:
        """``?b=3`` → 3."""
        request = RequestFactory().get("/ch-4115/random/", {"b": "3"})
        assert _parse_bulletin_count(request) == 3

    def test_non_numeric_falls_back_to_default(self) -> None:
        """``?b=banana`` → default of 10 (no 500)."""
        request = RequestFactory().get("/ch-4115/random/", {"b": "banana"})
        assert _parse_bulletin_count(request) == 10

    def test_value_above_max_is_clamped(self) -> None:
        """Values over the safety cap are clamped to 50."""
        request = RequestFactory().get("/ch-4115/random/", {"b": "9999"})
        assert _parse_bulletin_count(request) == 50

    def test_value_below_one_is_clamped(self) -> None:
        """Zero or negative values are clamped to 1."""
        request = RequestFactory().get("/ch-4115/random/", {"b": "0"})
        assert _parse_bulletin_count(request) == 1
        request = RequestFactory().get("/ch-4115/random/", {"b": "-5"})
        assert _parse_bulletin_count(request) == 1


# ---------------------------------------------------------------------------
# View tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def region() -> Region:
    """Return a test Region for view tests."""
    return RegionFactory.create(region_id="CH-4115", name="Valais", slug="ch-4115")


@pytest.mark.django_db
class TestRandomBulletinsView:
    """Tests for the ``random_bulletins`` view."""

    def test_unknown_region_returns_404(self, client: Client) -> None:
        """A region_id that doesn't exist returns a 404."""
        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "XX-0000"})
        )
        assert response.status_code == 404

    def test_case_insensitive_region_lookup(
        self, client: Client, region: Region
    ) -> None:
        """``ch-4115`` resolves to the same region as ``CH-4115``."""
        _make_region_bulletin(region, date(2025, 3, 1))
        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "ch-4115"})
        )
        assert response.status_code == 200
        assert response.context["region"].pk == region.pk

    def test_empty_state_when_region_has_no_bulletins(
        self, client: Client, region: Region
    ) -> None:
        """A region with no bulletins renders the empty state."""
        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )
        assert response.status_code == 200
        assert response.context["panels"] == []
        assert b"No bulletins available" in response.content

    def test_default_count_is_ten(self, client: Client, region: Region) -> None:
        """Without ``?b=``, the default count in the context is 10."""
        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )
        assert response.status_code == 200
        assert response.context["count"] == 10

    def test_caps_at_ten_distinct_days_by_default(
        self, client: Client, region: Region
    ) -> None:
        """With 15 daily bulletins, only the most recent 10 render."""
        for i in range(15):
            _make_region_bulletin(region, date(2025, 3, 1) + timedelta(days=i))

        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )
        panels = response.context["panels"]
        assert len(panels) == 10

    def test_b_query_param_overrides_count(
        self, client: Client, region: Region
    ) -> None:
        """``?b=3`` returns the three most recent bulletins."""
        for i in range(8):
            _make_region_bulletin(region, date(2025, 3, 1) + timedelta(days=i))

        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"}),
            {"b": "3"},
        )
        assert response.context["count"] == 3
        assert len(response.context["panels"]) == 3

    def test_bulletins_in_reverse_chronological_order(
        self, client: Client, region: Region
    ) -> None:
        """Panels are ordered most-recent-first."""
        days = [date(2025, 3, 1) + timedelta(days=i) for i in range(5)]
        for day in days:
            _make_region_bulletin(region, day)

        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )
        panels = response.context["panels"]
        panel_dates = [p["footer_date_from"].date() for p in panels]
        assert panel_dates == sorted(panel_dates, reverse=True)
        # Also matches the input order reversed.
        assert panel_dates == list(reversed(days))

    def test_only_one_bulletin_per_day(self, client: Client, region: Region) -> None:
        """Two bulletins covering the same day collapse to one card."""
        day = date(2025, 3, 5)
        # Morning issue for the day.
        _make_region_bulletin(region, day)
        # Evening issue covering the same day (same valid_to date).
        evening_from = datetime(day.year, day.month, day.day - 1, 16, 0, tzinfo=UTC)
        evening_to = datetime(day.year, day.month, day.day, 16, 0, tzinfo=UTC)
        evening = BulletinFactory.create(
            raw_data=_wrap(
                {
                    "dangerRatings": [{"mainValue": "moderate"}],
                    "avalancheProblems": [{"problemType": "wind_slab"}],
                    "regions": [{"name": region.name, "regionID": region.region_id}],
                }
            ),
            issued_at=evening_from,
            valid_from=evening_from,
            valid_to=evening_to,
        )
        RegionBulletinFactory.create(
            bulletin=evening,
            region=region,
            region_name_at_time=region.name,
        )

        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )
        assert len(response.context["panels"]) == 1

    def test_filters_to_requested_region(self, client: Client, region: Region) -> None:
        """Bulletins linked to other regions are not shown."""
        other = RegionFactory.create(region_id="CH-9999", name="Other", slug="ch-9999")
        _make_region_bulletin(region, date(2025, 3, 1))
        _make_region_bulletin(other, date(2025, 3, 1))
        _make_region_bulletin(other, date(2025, 3, 2))

        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )
        panels = response.context["panels"]
        assert len(panels) == 1
        # Every panel's underlying bulletin must be linked to ``region``.
        for panel in panels:
            assert region in panel["bulletin"].regions.all()

    def test_page_uses_panel_template(self, client: Client, region: Region) -> None:
        """The view renders via ``public/random_bulletins.html``."""
        _make_region_bulletin(
            region,
            date(2025, 3, 1),
            main_value="considerable",
            problem_type="persistent_weak_layers",
        )
        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )

        templates = [t.name for t in response.templates if t.name]
        assert "public/random_bulletins.html" in templates
        assert "public/_bulletin_panel.html" in templates
        assert b"rounded-[16px]" in response.content
        assert b'data-level="considerable"' in response.content
        assert b"output.css" in response.content
        assert b"Valais" in response.content

    def test_screen_label_pluralises_count(
        self, client: Client, region: Region
    ) -> None:
        """The screen label pluralises ``day``/``days`` based on ``b``."""
        _make_region_bulletin(region, date(2025, 3, 1))

        response_singular = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"}),
            {"b": "1"},
        )
        assert b"last 1 day " in response_singular.content.replace(b"\n", b" ")

        response_plural = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"}),
            {"b": "5"},
        )
        assert b"last 5 days" in response_plural.content.replace(b"\n", b" ")

    def test_problem_blocks_render_comment_and_time_period(
        self, client: Client, region: Region
    ) -> None:
        """Each avalanche problem renders its label and time-period badge."""
        _make_region_bulletin(
            region,
            date(2025, 3, 1),
            problems=[
                {
                    "problemType": "persistent_weak_layers",
                    "comment": "<p>Buried weak layers on shady slopes.</p>",
                    "validTimePeriod": "all_day",
                    "aspects": ["N", "NE"],
                    "elevation": {"lowerBound": "2200"},
                },
                {
                    "problemType": "wind_slab",
                    "comment": "<p>Fresh drifts on lee slopes.</p>",
                    "validTimePeriod": "later",
                    "aspects": ["NW"],
                    "elevation": {"lowerBound": "2400"},
                },
            ],
        )

        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )
        body = response.content

        # Both problem labels appear.
        assert b"Persistent weak layers" in body
        assert b"Wind slab" in body
        # Both comments appear as text.
        assert b"Buried weak layers on shady slopes." in body
        assert b"Fresh drifts on lee slopes." in body
        # Time-period badge for later.
        assert b"Later (afternoon)" in body
        # Two problem-block wrappers rendered.
        assert body.count(b"bg-tag") >= 2

    def test_elevation_renders_for_each_problem(
        self, client: Client, region: Region
    ) -> None:
        """Each problem with elevation bounds shows a formatted subtitle."""
        _make_region_bulletin(
            region,
            date(2025, 3, 1),
            problems=[
                {
                    "problemType": "persistent_weak_layers",
                    "comment": "<p>Deep weak layer above 2200m.</p>",
                    "validTimePeriod": "all_day",
                    "elevation": {"lowerBound": "2200"},
                    "aspects": ["N"],
                },
                {
                    "problemType": "wet_snow",
                    "comment": "<p>Wet snow below 2400m.</p>",
                    "validTimePeriod": "later",
                    "elevation": {"upperBound": "2400"},
                    "aspects": ["S"],
                },
            ],
        )

        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )
        body = response.content

        assert b"above 2200m" in body
        assert b"below 2400m" in body

    def test_duplicate_problems_show_comment_only_once(
        self, client: Client, region: Region
    ) -> None:
        """
        Two problems with identical elevation/aspect/period/comment render
        only one comment paragraph (attached to the later of the pair).
        """
        shared = {
            "comment": "<p>Buried weak layers on shady slopes.</p>",
            "validTimePeriod": "all_day",
            "elevation": {"lowerBound": "2200"},
            "aspects": ["N", "NE", "E"],
        }
        _make_region_bulletin(
            region,
            date(2025, 3, 1),
            problems=[
                {**shared, "problemType": "persistent_weak_layers"},
                {**shared, "problemType": "wind_slab"},
            ],
        )

        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )
        body = response.content

        # Both problem blocks render.
        assert body.count(b"bg-tag") >= 2
        assert b"Persistent weak layers" in body
        assert b"Wind slab" in body
        # But the shared comment only appears once — under the later header.
        assert body.count(b"Buried weak layers on shady slopes.") == 1
        assert body.count(b"slf-prose") == 1

    def test_two_wet_problems_in_separate_aggregation_entries_both_render(
        self, client: Client, region: Region
    ) -> None:
        """Two wet problems in distinct aggregation entries (different periods) both show."""
        _make_region_bulletin(
            region,
            date(2025, 3, 1),
            problems=[
                {
                    "problemType": "wet_snow",
                    "comment": "<p>Morning crust.</p>",
                    "validTimePeriod": "earlier",
                    "aspects": ["S"],
                    "elevation": {"upperBound": "2000"},
                },
                {
                    "problemType": "gliding_snow",
                    "comment": "<p>Afternoon softening.</p>",
                    "validTimePeriod": "later",
                    "aspects": ["SE"],
                    "elevation": {"upperBound": "2200"},
                },
            ],
            aggregation=[
                {
                    "category": "wet",
                    "validTimePeriod": "earlier",
                    "problemTypes": ["wet_snow"],
                    "title": "Wet avalanches, earlier",
                },
                {
                    "category": "wet",
                    "validTimePeriod": "later",
                    "problemTypes": ["gliding_snow"],
                    "title": "Wet avalanches, later",
                },
            ],
        )

        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )
        body = response.content

        assert body.count(b"bg-tag") >= 2
        assert b"Morning crust." in body
        assert b"Afternoon softening." in body
        assert b"Earlier (morning)" in body
        assert b"Later (afternoon)" in body


# ---------------------------------------------------------------------------
# Admin-link visibility
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAdminLinkVisibility:
    """The ``Open in admin`` link is gated on ``user.is_staff``."""

    @pytest.fixture()
    def region_with_bulletin(self, region: Region) -> Bulletin:
        """Return a bulletin linked to ``region`` on a fixed day."""
        return _make_region_bulletin(region, date(2025, 3, 1))

    def test_anonymous_users_do_not_see_admin_link(
        self,
        client: Client,
        region: Region,
        region_with_bulletin: Bulletin,
    ) -> None:
        """An unauthenticated visitor must not see the admin link."""
        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )
        assert response.status_code == 200
        assert b"Open in admin" not in response.content
        assert b'class="admin-link"' not in response.content

    def test_non_staff_users_do_not_see_admin_link(
        self,
        client: Client,
        django_user_model: Any,
        region: Region,
        region_with_bulletin: Bulletin,
    ) -> None:
        """A logged-in but non-staff user must not see the admin link."""
        user = django_user_model.objects.create_user(
            username="alice",
            password="hunter2",  # noqa: S106 test fixture, not a real password
            is_staff=False,
        )
        client.force_login(user)

        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )
        assert response.status_code == 200
        assert b"Open in admin" not in response.content

    def test_staff_users_see_admin_link_to_change_page(
        self,
        client: Client,
        django_user_model: Any,
        region: Region,
        region_with_bulletin: Bulletin,
    ) -> None:
        """An authenticated staff user sees the admin link with the right URL."""
        staff = django_user_model.objects.create_user(
            username="editor",
            password="hunter2",  # noqa: S106 test fixture, not a real password
            is_staff=True,
        )
        client.force_login(staff)

        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )
        assert response.status_code == 200
        assert b"Open in admin" in response.content
        expected_href = (
            f'href="/admin/pipeline/bulletin/{region_with_bulletin.pk}/change/"'
        )
        assert expected_href.encode() in response.content
