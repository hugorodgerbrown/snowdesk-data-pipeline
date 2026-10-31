"""
tests/public/test_problem_terrain.py — every region's problem-terrain rules
for a day (apps.public.problem_terrain, SNOW-1194).

Covers:
  - The payload: rules grouped by bulletin, each region pointing at the
    bulletin that rated its day (``RegionDayRating.source_bulletin``, the
    one its page shows), the rules read from the render model's stored
    ``derived.terrain_rules`` (SNOW-1341), a region whose
    bulletin describes no ground listed with an empty list, a region with
    no bulletin that day left out.
  - The gates: empty while the terrain-class tiles are unconfigured, and
    only the countries those tiles cover.
  - The view: today by default, a malformed ``?d=`` a 400, publicly
    cacheable for five minutes.
  - ``problem_terrain_key``: the region sheet's key lists only what its
    bulletin paints; the whole-scale key lists every level.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.bulletins.models import Bulletin, RegionDayRating
from apps.bulletins.services.day_rating import apply_bulletin_day_ratings
from apps.bulletins.services.render_model import RENDER_MODEL_VERSION
from apps.public.problem_terrain import (
    build_problem_terrain_payload,
    problem_terrain_key,
)
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
)

_TILES = "https://tiles.example/terrain-class/v1/{z}/{x}/{y}.png"


def _render_model(traits: list[Any] | None = None) -> dict[str, Any]:
    """A current-version render model; one dry wind slab unless told otherwise."""
    problem = {
        "problem_type": "wind_slab",
        "comment_html": "<p>Wind slab.</p>",
        "aspects": ["N", "NE", "E"],
        "elevation": {"lower": "2200", "upper": None, "treeline": False},
        "time_period": "all_day",
        "core_zone_text": None,
        "danger_rating_value": "considerable",
    }
    default = [
        {
            "category": "dry",
            "time_period": "all_day",
            "title": "Dry avalanches",
            "geography": {"source": "problems"},
            "problems": [problem],
            "prose": None,
            "danger_level": 3,
        }
    ]
    return {
        "version": RENDER_MODEL_VERSION,
        "source": "slf",
        "danger": {
            "key": "considerable",
            "number": "3",
            "subdivision": None,
            "ratings": [],
        },
        "danger_patterns": [],
        "traits": default if traits is None else traits,
        "metadata": {"lang": "en", "unscheduled": False},
        "prose": {},
    }


def _bulletin(day: datetime.date, regions: list[Any], **render: Any) -> Any:
    """A bulletin valid all of ``day``, linked to ``regions``."""
    start = datetime.datetime.combine(day, datetime.time.min, tzinfo=datetime.UTC)
    bulletin = BulletinFactory.create(
        issued_at=start,
        valid_from=start,
        valid_to=start + datetime.timedelta(days=1),
        render_model=_render_model(**render),
        render_model_version=RENDER_MODEL_VERSION,
    )
    for region in regions:
        RegionBulletinFactory.create(bulletin=bulletin, region=region)
    # The feed reads the day's ratings, as ingest writes them (SNOW-1341).
    apply_bulletin_day_ratings(bulletin)
    return bulletin


WIND_SLAB_RULE = {
    "rating": 3,
    "aspects": [0, 1, 2],
    "lower": 2200,
    "upper": None,
    "decided": True,
}


@pytest.mark.django_db
class TestPayload:
    """build_problem_terrain_payload."""

    def test_groups_rules_by_bulletin(self, settings: Any) -> None:
        """Two regions sharing one bulletin name it once."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        today = timezone.localdate()
        a, b = MicroRegionFactory.create(), MicroRegionFactory.create()
        bulletin = _bulletin(today, [a, b])
        payload = build_problem_terrain_payload(today)
        key = str(bulletin.bulletin_id)
        assert payload == {
            "date": today.isoformat(),
            "bulletins": {key: [WIND_SLAB_RULE]},
            "regions": {a.region_id: key, b.region_id: key},
        }

    def test_a_bulletin_without_problems_is_listed_empty(self, settings: Any) -> None:
        """Its steep ground paints as undescribed, never blank."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        today = timezone.localdate()
        region = MicroRegionFactory.create()
        bulletin = _bulletin(today, [region], traits=[])
        payload = build_problem_terrain_payload(today)
        assert payload["bulletins"] == {str(bulletin.bulletin_id): []}
        assert payload["regions"] == {region.region_id: str(bulletin.bulletin_id)}

    def test_leaves_out_a_region_without_a_bulletin_that_day(
        self, settings: Any
    ) -> None:
        """Yesterday's bulletin says nothing about today."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        today = timezone.localdate()
        region = MicroRegionFactory.create()
        _bulletin(today - datetime.timedelta(days=2), [region])
        assert build_problem_terrain_payload(today)["regions"] == {}

    def test_picks_the_issue_the_page_shows(self, settings: Any) -> None:
        """Two issues for one day: the shared rule picks one."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        today = timezone.localdate()
        region = MicroRegionFactory.create()
        _bulletin(today, [region])
        _bulletin(today, [region], traits=[])
        from apps.bulletins.services.selection import select_default_issue  # noqa: PLC0415
        from apps.public.views import _issues_for_date  # noqa: PLC0415

        expected = select_default_issue(_issues_for_date(region, today), today)
        assert expected is not None
        payload = build_problem_terrain_payload(today)
        assert payload["regions"] == {region.region_id: str(expected.bulletin_id)}

    def test_tonights_issue_belongs_to_tomorrow(self, settings: Any) -> None:
        """An issue valid from 15:00 today forecasts tomorrow (SNOW-1207)."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        today = timezone.localdate()
        region = MicroRegionFactory.create()
        start = datetime.datetime.combine(today, datetime.time(15), tzinfo=datetime.UTC)
        evening = BulletinFactory.create(
            issued_at=start,
            valid_from=start,
            valid_to=start + datetime.timedelta(days=1),
            render_model=_render_model(),
            render_model_version=RENDER_MODEL_VERSION,
        )
        RegionBulletinFactory.create(bulletin=evening, region=region)
        apply_bulletin_day_ratings(evening)
        tomorrow = today + datetime.timedelta(days=1)
        assert build_problem_terrain_payload(today)["regions"] == {}
        assert build_problem_terrain_payload(tomorrow)["regions"] == {
            region.region_id: str(evening.bulletin_id)
        }

    def test_reads_the_rules_stored_with_the_model(self, settings: Any) -> None:
        """SNOW-1341: derived.terrain_rules is served, not recomputed."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        today = timezone.localdate()
        region = MicroRegionFactory.create()
        stored = {**WIND_SLAB_RULE, "rating": 5, "aspects": [4]}
        bulletin = _bulletin(today, [region])
        model = {**bulletin.render_model, "derived": {"terrain_rules": [stored]}}
        Bulletin.objects.filter(pk=bulletin.pk).update(render_model=model)
        payload = build_problem_terrain_payload(today)
        assert payload["bulletins"] == {str(bulletin.bulletin_id): [stored]}

    def test_a_day_is_one_query(self, settings: Any) -> None:
        """The day's ratings with their bulletins; nothing per region."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        today = timezone.localdate()
        _bulletin(today, [MicroRegionFactory.create() for _ in range(3)])
        _bulletin(today, [MicroRegionFactory.create() for _ in range(2)])
        with CaptureQueriesContext(connection) as queries:
            payload = build_problem_terrain_payload(today)
        assert len(payload["regions"]) == 5
        assert len(queries) == 1
        assert "raw_data" not in queries[0]["sql"]

    def test_a_region_with_no_rated_bulletin_is_left_out(self, settings: Any) -> None:
        """No RegionDayRating.source_bulletin, no rules: it keeps its colour."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        today = timezone.localdate()
        region = MicroRegionFactory.create()
        _bulletin(today, [region])
        RegionDayRating.objects.filter(region=region).update(source_bulletin=None)
        assert build_problem_terrain_payload(today)["regions"] == {}

    def test_empty_while_the_tiles_are_unconfigured(self, settings: Any) -> None:
        """The terrain filter's own gate."""
        settings.TERRAIN_CLASS_TILE_URL = ""
        today = timezone.localdate()
        _bulletin(today, [MicroRegionFactory.create()])
        payload = build_problem_terrain_payload(today)
        assert payload["bulletins"] == {}
        assert payload["regions"] == {}

    def test_only_the_countries_the_tiles_cover(self, settings: Any) -> None:
        """A rule for ground the map cannot paint is a promise nothing keeps."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        today = timezone.localdate()
        region = MicroRegionFactory.create(region_id="AT-07-01")
        region.subregion.major.country = "AT"
        region.subregion.major.save()
        _bulletin(today, [region])
        assert build_problem_terrain_payload(today)["regions"] == {}


@pytest.mark.django_db
class TestView:
    """GET /api/problem-terrain/."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        """Each test reads its own payload, not the last one's."""
        cache.clear()

    def test_defaults_to_today(self, client: Client, settings: Any) -> None:
        """No ``?d=``: today's rules."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        region = MicroRegionFactory.create()
        _bulletin(timezone.localdate(), [region])
        response = client.get(reverse("api:problem_terrain"))
        assert response.status_code == 200
        body = response.json()
        assert body["date"] == timezone.localdate().isoformat()
        assert region.region_id in body["regions"]

    def test_reads_the_day_asked_for(self, client: Client, settings: Any) -> None:
        """``?d=`` picks the day."""
        settings.TERRAIN_CLASS_TILE_URL = _TILES
        day = timezone.localdate() - datetime.timedelta(days=3)
        region = MicroRegionFactory.create()
        _bulletin(day, [region])
        response = client.get(reverse("api:problem_terrain"), {"d": day.isoformat()})
        assert response.json()["regions"].keys() == {region.region_id}

    @pytest.mark.parametrize("raw", ["2026-4-27", "yesterday", "2026-02-30"])
    def test_malformed_date_is_a_400(self, client: Client, raw: str) -> None:
        """As /api/ratings/."""
        response = client.get(reverse("api:problem_terrain"), {"d": raw})
        assert response.status_code == 400

    def test_publicly_cacheable_for_five_minutes(self, client: Client) -> None:
        """The URL carries the date, so a shared cache may keep it."""
        response = client.get(reverse("api:problem_terrain"))
        cache_control = response["Cache-Control"]
        assert "public" in cache_control
        assert "max-age=300" in cache_control


class TestKey:
    """problem_terrain_key."""

    def test_levels_highest_first_and_undecided_when_any_rule_is(self) -> None:
        """Two decided levels, one undecided rule."""
        rules: list[Any] = [
            {"rating": 2, "aspects": [], "lower": None, "upper": None, "decided": True},
            {
                "rating": 4,
                "aspects": [4],
                "lower": None,
                "upper": None,
                "decided": False,
            },
            {
                "rating": 3,
                "aspects": [0],
                "lower": 2400,
                "upper": None,
                "decided": True,
            },
        ]
        key = problem_terrain_key(rules)
        assert [row["level"] for row in key["levels"]] == ["considerable", "moderate"]
        assert key["undecided"] is True

    def test_no_undecided_rule_no_undecided_row(self) -> None:
        """A day whose every problem can be placed."""
        rules: list[Any] = [
            {"rating": 1, "aspects": [], "lower": None, "upper": None, "decided": True}
        ]
        assert problem_terrain_key(rules) == {
            "levels": [{"level": "low", "label": "Low"}],
            "undecided": False,
        }

    def test_the_whole_scale(self) -> None:
        """The legend and the filter's key: every region is painted."""
        key = problem_terrain_key(None)
        assert [row["level"] for row in key["levels"]] == [
            "very_high",
            "high",
            "considerable",
            "moderate",
            "low",
        ]
        assert key["undecided"] is True
