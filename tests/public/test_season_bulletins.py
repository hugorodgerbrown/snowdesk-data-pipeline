"""
tests/public/test_season_bulletins.py — Tests for the season_bulletins view.

Covers the ``season_bulletins`` view and its helpers: ``_season_date_range``
and ``_select_season_bulletins``.  The season view renders up to 100
bulletin cards for a single region across the current Nov–May season in a
responsive CSS-grid layout.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from pipeline.models import Bulletin, Region
from public.views import _season_date_range
from tests.factories import BulletinFactory, RegionBulletinFactory, RegionFactory


def _wrap(properties: dict[str, Any]) -> dict[str, Any]:
    """Wrap a CAAML properties dict in a GeoJSON Feature envelope."""
    return {"type": "Feature", "geometry": None, "properties": properties}


def _make_region_bulletin(
    region: Region,
    day: date,
    *,
    main_value: str = "moderate",
) -> Bulletin:
    """
    Create a Bulletin valid on ``day`` and link it to ``region``.

    The bulletin's ``valid_from`` is 06:00 UTC and ``valid_to`` is 16:00
    UTC on ``day`` (mimicking the SLF morning issue shape).
    """
    valid_from = datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)
    valid_to = datetime(day.year, day.month, day.day, 16, 0, tzinfo=UTC)
    bulletin = BulletinFactory.create(
        raw_data=_wrap(
            {
                "dangerRatings": [{"mainValue": main_value}],
                "avalancheProblems": [{"problemType": "wind_slab"}],
                "regions": [{"name": region.name, "regionID": region.region_id}],
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
# _season_date_range
# ---------------------------------------------------------------------------


class TestSeasonDateRange:
    """Tests for ``_season_date_range``."""

    def test_december_date_is_current_winter(self) -> None:
        """Dec 2025 belongs to the 2025-11 → 2026-05 season."""
        start, end = _season_date_range(date(2025, 12, 15))
        assert start == date(2025, 11, 1)
        assert end == date(2026, 5, 31)

    def test_november_first_starts_new_season(self) -> None:
        """Nov 1 belongs to the season that starts on that same date."""
        start, end = _season_date_range(date(2025, 11, 1))
        assert start == date(2025, 11, 1)
        assert end == date(2026, 5, 31)

    def test_march_date_belongs_to_previous_november(self) -> None:
        """Mar 2026 belongs to the 2025-11 → 2026-05 season."""
        start, end = _season_date_range(date(2026, 3, 10))
        assert start == date(2025, 11, 1)
        assert end == date(2026, 5, 31)

    def test_may_date_belongs_to_previous_november(self) -> None:
        """May 2026 is still within the 2025-11 → 2026-05 season."""
        start, end = _season_date_range(date(2026, 5, 31))
        assert start == date(2025, 11, 1)
        assert end == date(2026, 5, 31)

    def test_october_belongs_to_previous_season(self) -> None:
        """Oct 2026 (before Nov) belongs to the 2025-11 → 2026-05 season."""
        start, end = _season_date_range(date(2026, 10, 15))
        assert start == date(2025, 11, 1)
        assert end == date(2026, 5, 31)


# ---------------------------------------------------------------------------
# View tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def region() -> Region:
    """Return a test Region for view tests."""
    return RegionFactory.create(region_id="CH-4115", name="Valais", slug="ch-4115")


def _freeze(dt_str: str):
    """Return a patch that freezes django.utils.timezone.now to ``dt_str``."""
    frozen = datetime.fromisoformat(dt_str)
    return patch("django.utils.timezone.now", return_value=frozen)


@pytest.mark.django_db
class TestSeasonBulletinsView:
    """Tests for the ``season_bulletins`` view."""

    def test_unknown_region_returns_404(self, client: Client) -> None:
        """A region_id that doesn't exist returns a 404."""
        response = client.get(
            reverse("public:season_bulletins", kwargs={"region_id": "XX-0000"})
        )
        assert response.status_code == 404

    def test_empty_state_when_region_has_no_bulletins(
        self, client: Client, region: Region
    ) -> None:
        """A region with no bulletins renders the empty state."""
        response = client.get(
            reverse("public:season_bulletins", kwargs={"region_id": "CH-4115"})
        )
        assert response.status_code == 200
        assert response.context["panels"] == []
        assert b"No bulletins for this region" in response.content

    def test_renders_bulletins_within_season_only(
        self, client: Client, region: Region
    ) -> None:
        """Only bulletins whose valid_to falls within the season are shown."""
        # Freeze time to mid-January 2026 → season is 2025-11-01 to 2026-05-31.
        with _freeze("2026-01-15T12:00:00+00:00"):
            # In-season bulletin (Dec 2025).
            _make_region_bulletin(region, date(2025, 12, 1))
            # In-season bulletin (Jan 2026).
            _make_region_bulletin(region, date(2026, 1, 10))
            # Out-of-season bulletin (Sep 2025 — before Nov 1).
            _make_region_bulletin(region, date(2025, 9, 15))

            response = client.get(
                reverse(
                    "public:season_bulletins",
                    kwargs={"region_id": "CH-4115"},
                )
            )

        panels = response.context["panels"]
        assert len(panels) == 2

    def test_caps_at_100_panels(self, client: Client, region: Region) -> None:
        """At most 100 bulletins render even if more exist in the season."""
        with _freeze("2026-01-15T12:00:00+00:00"):
            # Create 110 daily bulletins within the season window.
            for i in range(110):
                day = date(2025, 11, 1) + timedelta(days=i)
                _make_region_bulletin(region, day)

            response = client.get(
                reverse(
                    "public:season_bulletins",
                    kwargs={"region_id": "CH-4115"},
                )
            )

        panels = response.context["panels"]
        assert len(panels) == 100

    def test_reverse_chronological_order(self, client: Client, region: Region) -> None:
        """Panels are ordered most-recent-first."""
        days = [date(2025, 12, 1) + timedelta(days=i) for i in range(5)]
        with _freeze("2026-01-15T12:00:00+00:00"):
            for day in days:
                _make_region_bulletin(region, day)

            response = client.get(
                reverse(
                    "public:season_bulletins",
                    kwargs={"region_id": "CH-4115"},
                )
            )

        panels = response.context["panels"]
        panel_dates = [p["footer_date_from"].date() for p in panels]
        assert panel_dates == sorted(panel_dates, reverse=True)

    def test_uses_season_template_and_css(self, client: Client, region: Region) -> None:
        """The view uses ``season_bulletins.html`` and loads the right CSS."""
        with _freeze("2026-01-15T12:00:00+00:00"):
            _make_region_bulletin(region, date(2025, 12, 1))
            response = client.get(
                reverse(
                    "public:season_bulletins",
                    kwargs={"region_id": "CH-4115"},
                )
            )

        templates = [t.name for t in response.templates if t.name]
        assert "public/season_bulletins.html" in templates
        assert "public/_bulletin_panel.html" in templates
        assert b"output.css" in response.content
        assert b"grid-cols-1" in response.content
        assert b"Valais" in response.content

    def test_context_includes_season_label(
        self, client: Client, region: Region
    ) -> None:
        """The context provides a human-readable season label."""
        with _freeze("2026-01-15T12:00:00+00:00"):
            response = client.get(
                reverse(
                    "public:season_bulletins",
                    kwargs={"region_id": "CH-4115"},
                )
            )

        assert response.context["season_label"] == "Nov 2025 – May 2026"

    def test_case_insensitive_region_lookup(
        self, client: Client, region: Region
    ) -> None:
        """Both ``ch-4115`` and ``CH-4115`` resolve to the same region."""
        response = client.get(
            reverse("public:season_bulletins", kwargs={"region_id": "ch-4115"})
        )
        assert response.status_code == 200
        assert response.context["region"].pk == region.pk

    def test_context_includes_panel_count(self, client: Client, region: Region) -> None:
        """``panel_count`` in the context matches the actual panel list length."""
        with _freeze("2026-01-15T12:00:00+00:00"):
            for i in range(3):
                _make_region_bulletin(region, date(2025, 12, 1) + timedelta(days=i))
            response = client.get(
                reverse(
                    "public:season_bulletins",
                    kwargs={"region_id": "CH-4115"},
                )
            )

        assert response.context["panel_count"] == 3
        assert len(response.context["panels"]) == 3
