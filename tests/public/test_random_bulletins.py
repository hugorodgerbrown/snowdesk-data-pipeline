"""
tests/public/test_random_bulletins.py — Tests for the random_bulletins view.

Covers the ``random_bulletins`` view and its private helpers in
``public.views``: ``_highest_danger_key``, ``_panel_problems``,
``_panel_message``, ``_panel_footer_area``, ``_build_panel_context``, and
``_parse_bulletin_count``. The view now lists the most recent bulletins for
a single region (one per calendar day, reverse chronological) with an
optional ``?b=N`` query parameter.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse

from pipeline.models import Bulletin, Region
from public.views import (
    _build_panel_context,
    _format_elevation,
    _highest_danger_key,
    _panel_footer_area,
    _panel_message,
    _panel_problems,
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
    return cast(
        "Bulletin",
        BulletinFactory(
            raw_data=_wrap(properties),
            valid_from=datetime(2025, 2, 1, 7, 0, tzinfo=UTC),
            valid_to=datetime(2025, 2, 1, 16, 0, tzinfo=UTC),
        ),
    )


def _make_region_bulletin(
    region: Region,
    day: date,
    *,
    main_value: str = "moderate",
    problems: list[dict[str, Any]] | None = None,
    problem_type: str = "wind_slab",
) -> Bulletin:
    """
    Create a Bulletin valid on ``day`` and link it to ``region``.

    The bulletin's ``valid_from`` is 06:00 UTC and ``valid_to`` is 16:00
    UTC on ``day`` (mimicking the SLF morning issue shape). Pass
    ``problems`` to supply a full CAAML avalancheProblems list; otherwise a
    single problem of ``problem_type`` with no comment/period is used.
    """
    if problems is None:
        problems = [{"problemType": problem_type}]
    valid_from = datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)
    valid_to = datetime(day.year, day.month, day.day, 16, 0, tzinfo=UTC)
    bulletin = cast(
        "Bulletin",
        BulletinFactory(
            raw_data=_wrap(
                {
                    "dangerRatings": [{"mainValue": main_value}],
                    "avalancheProblems": problems,
                    "regions": [{"name": region.name, "regionID": region.region_id}],
                }
            ),
            issued_at=valid_from - timedelta(minutes=30),
            valid_from=valid_from,
            valid_to=valid_to,
        ),
    )
    RegionBulletinFactory(
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
        """An empty ratings list defaults to ``low``."""
        assert _highest_danger_key([]) == "low"

    def test_returns_highest_when_multiple_ratings(self) -> None:
        """The highest EAWS value across all entries wins."""
        ratings = [
            {"mainValue": "moderate"},
            {"mainValue": "high"},
            {"mainValue": "low"},
        ]
        assert _highest_danger_key(ratings) == "high"

    def test_unknown_values_are_ignored(self) -> None:
        """Unrecognised values should not confuse the ordering."""
        ratings = [{"mainValue": "moderate"}, {"mainValue": "definitely_not_valid"}]
        assert _highest_danger_key(ratings) == "moderate"

    def test_very_high_beats_high(self) -> None:
        """``very_high`` outranks ``high``."""
        ratings = [{"mainValue": "high"}, {"mainValue": "very_high"}]
        assert _highest_danger_key(ratings) == "very_high"


class TestFormatElevation:
    """Tests for ``_format_elevation``."""

    def test_none_returns_empty(self) -> None:
        """``None`` elevation → empty string."""
        assert _format_elevation(None) == ""

    def test_empty_dict_returns_empty(self) -> None:
        """Empty dict → empty string."""
        assert _format_elevation({}) == ""

    def test_lower_bound_only(self) -> None:
        """``lowerBound`` only → ``above Xm``."""
        assert _format_elevation({"lowerBound": "2200"}) == "above 2200m"

    def test_upper_bound_only(self) -> None:
        """``upperBound`` only → ``below Xm``."""
        assert _format_elevation({"upperBound": "2400"}) == "below 2400m"

    def test_both_bounds(self) -> None:
        """Both bounds → ``X\u2013Ym`` (en-dash)."""
        assert (
            _format_elevation({"lowerBound": "1800", "upperBound": "2400"})
            == "1800\u20132400m"
        )

    def test_treeline_literal(self) -> None:
        """``treeline`` is emitted as-is, without the ``m`` suffix."""
        assert _format_elevation({"lowerBound": "treeline"}) == "above treeline"
        assert _format_elevation({"upperBound": "treeline"}) == "below treeline"


class TestPanelProblems:
    """Tests for ``_panel_problems``."""

    def test_empty_properties_returns_empty_list(self) -> None:
        """No avalancheProblems → empty list."""
        assert _panel_problems({}) == []

    def test_maps_known_problem_types_to_labels(self) -> None:
        """Known problem types resolve to their human-readable labels."""
        props = {
            "avalancheProblems": [
                {"problemType": "new_snow"},
                {"problemType": "persistent_weak_layers"},
            ]
        }
        result = _panel_problems(props)
        assert result == [
            {
                "problem_type": "new_snow",
                "label": "New snow",
                "comment": "",
                "time_period": "",
                "time_period_label": "",
                "elevation": "",
                "hide_comment": False,
            },
            {
                "problem_type": "persistent_weak_layers",
                "label": "Persistent weak layers",
                "comment": "",
                "time_period": "",
                "time_period_label": "",
                "elevation": "",
                "hide_comment": False,
            },
        ]

    def test_includes_comment_and_time_period_label(self) -> None:
        """Each problem carries a plain-text comment and period label."""
        props = {
            "avalancheProblems": [
                {
                    "problemType": "wind_slab",
                    "comment": "<p>Fresh drifts on lee slopes.</p>",
                    "validTimePeriod": "earlier",
                    "elevation": {"lowerBound": "2200"},
                },
            ]
        }
        result = _panel_problems(props)
        assert result == [
            {
                "problem_type": "wind_slab",
                "label": "Wind slab",
                "comment": "Fresh drifts on lee slopes.",
                "time_period": "earlier",
                "time_period_label": "Earlier (morning)",
                "elevation": "above 2200m",
                "hide_comment": False,
            }
        ]

    def test_comment_is_truncated_on_word_boundary(self) -> None:
        """Comment text over ~240 chars is cut on a word boundary."""
        long_comment = "word " * 100
        props = {
            "avalancheProblems": [{"problemType": "new_snow", "comment": long_comment}]
        }
        result = _panel_problems(props)
        assert len(result[0]["comment"]) <= 240
        assert result[0]["comment"].endswith("\u2026")

    def test_does_not_deduplicate_repeated_problem_types(self) -> None:
        """Two entries with the same problemType both render separately."""
        props = {
            "avalancheProblems": [
                {
                    "problemType": "persistent_weak_layers",
                    "comment": "<p>Morning note.</p>",
                    "validTimePeriod": "earlier",
                },
                {
                    "problemType": "persistent_weak_layers",
                    "comment": "<p>Afternoon note.</p>",
                    "validTimePeriod": "later",
                },
            ]
        }
        result = _panel_problems(props)
        assert len(result) == 2
        assert result[0]["time_period_label"] == "Earlier (morning)"
        assert result[1]["time_period_label"] == "Later (afternoon)"
        assert result[0]["comment"] == "Morning note."
        assert result[1]["comment"] == "Afternoon note."
        # Different periods → signatures differ → both comments shown.
        assert result[0]["hide_comment"] is False
        assert result[1]["hide_comment"] is False

    def test_missing_time_period_gives_empty_label(self) -> None:
        """A problem without ``validTimePeriod`` has an empty label."""
        props = {"avalancheProblems": [{"problemType": "wet_snow"}]}
        result = _panel_problems(props)
        assert result[0]["time_period"] == ""
        assert result[0]["time_period_label"] == ""

    def test_identical_scope_hides_comment_on_earlier_occurrence(self) -> None:
        """
        When two problems share elevation, aspects, period, and comment, the
        earlier one has ``hide_comment`` set so only the later header owns
        the shared comment.
        """
        props = {
            "avalancheProblems": [
                {
                    "problemType": "persistent_weak_layers",
                    "comment": "<p>Buried weak layers on shady slopes.</p>",
                    "validTimePeriod": "all_day",
                    "elevation": {"lowerBound": "2200"},
                    "aspects": ["N", "NE", "E"],
                },
                {
                    "problemType": "wind_slab",
                    "comment": "<p>Buried weak layers on shady slopes.</p>",
                    "validTimePeriod": "all_day",
                    "elevation": {"lowerBound": "2200"},
                    "aspects": ["NE", "N", "E"],  # same set, different order
                },
            ]
        }
        result = _panel_problems(props)
        assert len(result) == 2
        # First (earlier) occurrence has its comment suppressed.
        assert result[0]["hide_comment"] is True
        assert result[0]["comment"] == "Buried weak layers on shady slopes."
        # Last occurrence keeps its comment visible.
        assert result[1]["hide_comment"] is False
        assert result[1]["comment"] == "Buried weak layers on shady slopes."

    def test_three_identical_problems_only_last_shows_comment(self) -> None:
        """In a run of three duplicates only the final one keeps its comment."""
        same: dict[str, Any] = {
            "comment": "<p>Shared note.</p>",
            "validTimePeriod": "all_day",
            "elevation": {"lowerBound": "2000"},
            "aspects": ["N"],
        }
        props = {
            "avalancheProblems": [
                {**same, "problemType": "wind_slab"},
                {**same, "problemType": "new_snow"},
                {**same, "problemType": "persistent_weak_layers"},
            ]
        }
        result = _panel_problems(props)
        assert [p["hide_comment"] for p in result] == [True, True, False]

    def test_different_elevation_keeps_both_comments(self) -> None:
        """Differing elevation → the comments are NOT considered duplicates."""
        props = {
            "avalancheProblems": [
                {
                    "problemType": "wind_slab",
                    "comment": "<p>Same text.</p>",
                    "elevation": {"lowerBound": "2000"},
                    "aspects": ["N"],
                    "validTimePeriod": "all_day",
                },
                {
                    "problemType": "wind_slab",
                    "comment": "<p>Same text.</p>",
                    "elevation": {"lowerBound": "2500"},
                    "aspects": ["N"],
                    "validTimePeriod": "all_day",
                },
            ]
        }
        result = _panel_problems(props)
        assert result[0]["hide_comment"] is False
        assert result[1]["hide_comment"] is False

    def test_different_aspects_keeps_both_comments(self) -> None:
        """Differing aspects → NOT duplicates."""
        base: dict[str, Any] = {
            "problemType": "wind_slab",
            "comment": "<p>Same text.</p>",
            "elevation": {"lowerBound": "2000"},
            "validTimePeriod": "all_day",
        }
        props = {
            "avalancheProblems": [
                {**base, "aspects": ["N"]},
                {**base, "aspects": ["S"]},
            ]
        }
        result = _panel_problems(props)
        assert result[0]["hide_comment"] is False
        assert result[1]["hide_comment"] is False

    def test_different_comment_keeps_both(self) -> None:
        """Differing comment text → NOT duplicates."""
        base: dict[str, Any] = {
            "problemType": "wind_slab",
            "validTimePeriod": "all_day",
            "elevation": {"lowerBound": "2000"},
            "aspects": ["N"],
        }
        props = {
            "avalancheProblems": [
                {**base, "comment": "<p>First note.</p>"},
                {**base, "comment": "<p>Second note.</p>"},
            ]
        }
        result = _panel_problems(props)
        assert result[0]["hide_comment"] is False
        assert result[1]["hide_comment"] is False


class TestPanelMessage:
    """Tests for ``_panel_message``."""

    def test_prefers_first_problem_comment(self) -> None:
        """The first avalanche problem's comment is the preferred blurb."""
        props = {
            "avalancheProblems": [
                {"problemType": "new_snow", "comment": "<p>Fresh snow. Be careful.</p>"}
            ],
            "snowpackStructure": {"comment": "<p>Deep weak layers.</p>"},
        }
        assert _panel_message(props) == (
            "Fresh snow. Be careful.",
            "avalancheProblems[0].comment",
        )

    def test_falls_back_to_snowpack_summary(self) -> None:
        """With no problem comment, the snowpack comment is used."""
        props = {
            "avalancheProblems": [],
            "snowpackStructure": {"comment": "<p>Deep weak layers.</p>"},
        }
        assert _panel_message(props) == (
            "Deep weak layers.",
            "snowpackStructure.comment",
        )

    def test_falls_back_to_weather_review(self) -> None:
        """With no problem or snowpack comment, weather review is used."""
        props = {
            "avalancheProblems": [],
            "weatherReview": {"comment": "<p>Light snow overnight.</p>"},
        }
        assert _panel_message(props) == (
            "Light snow overnight.",
            "weatherReview.comment",
        )

    def test_empty_when_nothing_available(self) -> None:
        """With no usable text, returns empty strings for both fields."""
        assert _panel_message({}) == ("", "")

    def test_truncates_long_messages(self) -> None:
        """Text exceeding ~240 chars is cut on a word boundary with an ellipsis."""
        long_text = "word " * 100  # 500 chars
        props = {"avalancheProblems": [{"comment": long_text}]}
        text, source = _panel_message(props)
        assert len(text) <= 240
        assert text.endswith("\u2026")
        assert source == "avalancheProblems[0].comment"


class TestPanelFooterArea:
    """Tests for ``_panel_footer_area``."""

    def test_empty_returns_empty_string(self) -> None:
        """No regions → empty string."""
        assert _panel_footer_area({}) == ""

    def test_joins_region_names_with_middot(self) -> None:
        """Up to the first three region names are joined with `` · ``."""
        props = {
            "regions": [
                {"name": "Valais", "regionID": "CH-4115"},
                {"name": "Grisons", "regionID": "CH-5123"},
                {"name": "Jura", "regionID": "CH-9999"},
                {"name": "Extra", "regionID": "CH-8888"},
            ]
        }
        assert _panel_footer_area(props) == "Valais · Grisons · Jura"


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
        assert ctx["danger_label"] == "Low"
        assert ctx["danger_icon"] == "Dry-Snow-1.svg"
        assert ctx["problems"] == [
            {
                "problem_type": "no_distinct_avalanche_problem",
                "label": "No distinct problem",
                "comment": "No distinct problem to speak of.",
                "time_period": "all_day",
                "time_period_label": "All day",
                "elevation": "above 2200m",
                "hide_comment": False,
            }
        ]
        assert ctx["key_message"] == "No distinct problem to speak of."
        assert ctx["footer_area"] == "Jura"
        assert ctx["footer_date_from"] == bulletin.valid_from
        assert ctx["footer_date_to"] == bulletin.valid_to

        # Provenance strings — one per visible field, for the tooltips.
        assert ctx["danger_source"] == "dangerRatings[*].mainValue (highest)"
        assert ctx["problems_source"] == "avalancheProblems[*].problemType"
        assert ctx["key_message_source"] == "avalancheProblems[0].comment"
        assert ctx["footer_date_source"] == "Bulletin.valid_from / valid_to"
        assert ctx["footer_area_source"] == "regions[*].name"

        # Admin URL always populated; template gates it on user.is_staff.
        assert ctx["admin_url"] == f"/admin/pipeline/bulletin/{bulletin.pk}/change/"

    def test_bulletin_with_no_raw_data_defaults_to_low(self) -> None:
        """A bulletin with empty raw_data still renders a valid context."""
        bulletin = cast("Bulletin", BulletinFactory(raw_data={}))
        ctx = _build_panel_context(bulletin)
        assert ctx["danger_key"] == "low"
        assert ctx["problems"] == []
        assert ctx["key_message"] == ""
        assert ctx["key_message_source"] == ""
        assert ctx["footer_area"] == ""

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
    return cast(
        "Region",
        RegionFactory(region_id="CH-4115", name="Valais", slug="ch-4115"),
    )


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
        evening = cast(
            "Bulletin",
            BulletinFactory(
                raw_data=_wrap(
                    {
                        "dangerRatings": [{"mainValue": "moderate"}],
                        "avalancheProblems": [{"problemType": "wind_slab"}],
                        "regions": [
                            {"name": region.name, "regionID": region.region_id}
                        ],
                    }
                ),
                issued_at=evening_from,
                valid_from=evening_from,
                valid_to=evening_to,
            ),
        )
        RegionBulletinFactory(
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
        other = cast(
            "Region",
            RegionFactory(region_id="CH-9999", name="Other", slug="ch-9999"),
        )
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
        assert b'class="card"' in response.content
        assert b'class="danger-band band-considerable"' in response.content
        assert b"random_bulletins.css" in response.content
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
        """Each avalanche problem renders its comment and period badge."""
        _make_region_bulletin(
            region,
            date(2025, 3, 1),
            problems=[
                {
                    "problemType": "persistent_weak_layers",
                    "comment": "<p>Buried weak layers on shady slopes.</p>",
                    "validTimePeriod": "all_day",
                },
                {
                    "problemType": "wind_slab",
                    "comment": "<p>Fresh drifts on lee slopes.</p>",
                    "validTimePeriod": "later",
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
        # Time-period badges appear with their human labels.
        assert b"All day" in body
        assert b"Later (afternoon)" in body
        # Tooltip titles on the comment and the period use the CAAML paths.
        assert b'title="avalancheProblems[*].comment"' in body
        assert b'title="avalancheProblems[*].validTimePeriod = all_day"' in body
        assert b'title="avalancheProblems[*].validTimePeriod = later"' in body
        # Two problem-block wrappers rendered.
        assert body.count(b'class="problem-block"') == 2

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
        assert body.count(b'class="problem-elevation"') == 2
        assert b'title="avalancheProblems[*].elevation"' in body

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
        assert body.count(b'class="problem-block"') == 2
        assert b"Persistent weak layers" in body
        assert b"Wind slab" in body
        # But the shared comment only appears once — under the later header.
        assert body.count(b"Buried weak layers on shady slopes.") == 1
        assert body.count(b'class="problem-comment"') == 1

    def test_same_problem_type_with_different_periods_both_render(
        self, client: Client, region: Region
    ) -> None:
        """Repeated problemType entries with distinct periods both show."""
        _make_region_bulletin(
            region,
            date(2025, 3, 1),
            problems=[
                {
                    "problemType": "wet_snow",
                    "comment": "<p>Morning crust.</p>",
                    "validTimePeriod": "earlier",
                },
                {
                    "problemType": "wet_snow",
                    "comment": "<p>Afternoon softening.</p>",
                    "validTimePeriod": "later",
                },
            ],
        )

        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )
        body = response.content

        assert body.count(b'class="problem-block"') == 2
        assert b"Morning crust." in body
        assert b"Afternoon softening." in body
        assert b"Earlier (morning)" in body
        assert b"Later (afternoon)" in body

    def test_tooltip_titles_expose_caaml_paths(
        self, client: Client, region: Region
    ) -> None:
        """Each card field carries a ``title=`` attribute naming its source."""
        _make_region_bulletin(region, date(2025, 3, 1))

        response = client.get(
            reverse("public:random_bulletins", kwargs={"region_id": "CH-4115"})
        )
        body = response.content

        # Danger band: title points to dangerRatings[*].mainValue.
        assert b'title="dangerRatings[*].mainValue (highest)"' in body
        # Problems section eyebrow + tags.
        assert b'title="avalancheProblems[*].problemType"' in body
        # Tag itself includes the specific problem type.
        assert b'title="avalancheProblems[*].problemType = wind_slab"' in body
        # Footer date comes from the Bulletin model, not the CAAML payload.
        assert b'title="Bulletin.valid_from / valid_to"' in body
        # Footer area comes from regions[*].name.
        assert b'title="regions[*].name"' in body


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
        assert b'class="admin-link"' in response.content
        expected_href = (
            f'href="/admin/pipeline/bulletin/{region_with_bulletin.pk}/change/"'
        )
        assert expected_href.encode() in response.content
