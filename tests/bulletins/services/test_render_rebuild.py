"""
tests/bulletins/services/test_render_rebuild.py

Covers ``apps.bulletins.services.render_rebuild`` (SNOW-1346), one
bulletin's render model rebuilt as one task, on both sides:
  - A stale row is rebuilt and its (region, day) pairs recomputed.
  - A current row, or a gone pk, is skipped: a second enqueue is a no-op.
  - ``force`` rebuilds a current row.
  - A build error commits the sentinel, recomputes nothing, and fails the
    task.
  - A failed recompute rolls the row write back.
  - A committed rebuild deletes the region's cached season calendar; a
    rolled-back one leaves it (SNOW-1172).
  - Two sibling bulletins on one pair end with the governing one's rating,
    whichever task runs first.
  - The rating-row lock creates a missing row and retries a racing create.
  - A stale ``SourceDayRating`` is recomputed once, then skipped.
  - Every task is enqueued below the default priority.
  - Each task locks its bulletin FOR NO KEY UPDATE, then its pairs' rating
    rows FOR UPDATE in sorted order, before reading the candidates.
  - A sibling that commits just before a task locks is read by it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, connection
from django.db.models import QuerySet
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_tasks import TaskResultStatus

from apps.bulletins.models import Bulletin, RegionDayRating
from apps.bulletins.services.day_rating import (
    recompute_region_day,
    season_calendar_cache_key,
)
from apps.bulletins.services.render_model import RENDER_MODEL_VERSION
from apps.bulletins.services.render_rebuild import (
    RENDER_TASK_PRIORITY,
    _lock_region_day_ratings,
    rebuild_bulletin_task,
    rebuild_one_bulletin,
    rebuild_one_source_bulletin,
    rebuild_source_bulletin_task,
    refresh_one_source_day_rating,
    refresh_source_day_rating_task,
)
from apps.ingest.public import (
    DAY_RATING_VERSION,
    OUTCOME_UNCHANGED,
    REBUILD_FAILED,
    REBUILT,
    SourceBulletin,
    SourceDayRating,
    lock_source_day_ratings,
    refresh_source_day_ratings,
)
from apps.regions.models import MicroRegion
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    SourceBulletinFactory,
    SourceDayRatingFactory,
)

pytestmark = pytest.mark.django_db


def _seed_calendar(region: MicroRegion) -> list[str]:
    """Cache a season-calendar body for ``region`` in every language."""
    today_iso = timezone.localdate().isoformat()
    keys = [
        season_calendar_cache_key(region.canonical_region_id, today_iso, code)
        for code, _name in settings.LANGUAGES
    ]
    for key in keys:
        cache.set(key, "<stale calendar>", 3600)
    return keys


DAY = date(2026, 1, 2)
_INSTANCE_BUILD = "apps.bulletins.services.render_rebuild.build_render_model"
_INSTANCE_RECOMPUTE = "apps.bulletins.services.render_rebuild.recompute_region_day"
_SOURCE_BUILD = "apps.ingest.store.build_render_model"
_SOURCE_REFRESH = "apps.bulletins.services.render_rebuild.refresh_source_day_ratings"
_INSTANCE_LOCK = "apps.bulletins.services.render_rebuild._lock_region_day_ratings"
_SOURCE_LOCK = "apps.bulletins.services.render_rebuild.lock_source_day_ratings"


def _raw(level: str) -> dict[str, Any]:
    """Return an instance bulletin's raw_data whose headline is ``level``."""
    return {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "bulletinID": "raw",
            "dangerRatings": [{"mainValue": level}],
            "avalancheProblems": [],
            "customData": {"CH": {}},
        },
    }


def _bulletin(
    region: MicroRegion,
    *,
    level: str = "low",
    hour: int = 7,
    version: int = 0,
) -> Bulletin:
    """Return a stale instance bulletin linked to ``region``, targeting DAY.

    An ``hour`` from noon is the evening issue of the day before.
    """
    valid_from = datetime(2026, 1, 2 if hour < 12 else 1, hour, tzinfo=UTC)
    bulletin = BulletinFactory.create(
        held=_raw(level)["properties"],
        render_model={"version": version, "traits": []},
        render_model_version=version,
        issued_at=valid_from,
        valid_from=valid_from,
    )
    RegionBulletinFactory.create(bulletin=bulletin, region=region)
    return bulletin


def _caaml(bulletin_id: str, hour: int, codes: list[str]) -> dict[str, Any]:
    """Return a service CAAML valid from ``hour`` UTC, targeting DAY."""
    day = 2 if hour < 12 else 1
    return {
        "bulletinID": bulletin_id,
        "validTime": {"startTime": f"2026-01-0{day}T{hour:02d}:00Z"},
        "regions": [{"regionID": code, "name": code} for code in codes],
    }


def _model(key: str) -> dict[str, Any]:
    """Return a current service render model whose headline is ``key``."""
    return {
        "version": RENDER_MODEL_VERSION,
        "source": "SLF",
        "danger": {"key": key, "subdivision": "", "ratings": []},
        "traits": [],
    }


def _by_caaml(caaml: dict[str, Any]) -> dict[str, Any]:
    """Build a service model from the level the test hid in the CAAML."""
    return _model(caaml.get("level", "low"))


def _source(
    codes: list[str], *, hour: int = 7, level: str = "low", **kwargs: Any
) -> SourceBulletin:
    """Return a never-built service bulletin on ``codes``."""
    caaml = {**_caaml(f"b-{hour}-{level}", hour, codes), "level": level}
    return SourceBulletinFactory.create(caaml=caaml, **kwargs)


class TestInstanceTask:
    """``rebuild_bulletin_task`` over an instance ``Bulletin``."""

    def test_a_stale_row_is_rebuilt_and_its_pair_rated(self) -> None:
        """Version and danger_levels move; the (region, day) is recomputed."""
        region = MicroRegionFactory.create(region_id="CH-1001")
        bulletin = _bulletin(region, level="considerable")

        result = rebuild_bulletin_task.enqueue(bulletin.pk)

        assert result.status == TaskResultStatus.SUCCESSFUL
        assert result.return_value == REBUILT
        bulletin.refresh_from_db()
        assert bulletin.render_model_version == RENDER_MODEL_VERSION
        assert bulletin.danger_levels == "|considerable|"
        rating = RegionDayRating.objects.get(region=region, date=DAY)
        assert rating.max_rating == "considerable"
        assert rating.source_bulletin_id == bulletin.pk

    def test_a_current_row_is_skipped_without_a_write(self) -> None:
        """A duplicate enqueue changes nothing, updated_at included."""
        region = MicroRegionFactory.create(region_id="CH-1002")
        bulletin = _bulletin(region)
        rebuild_one_bulletin(bulletin.pk)
        bulletin.refresh_from_db()
        stamp, model = bulletin.updated_at, bulletin.render_model

        with patch(_INSTANCE_BUILD) as build:
            assert rebuild_one_bulletin(bulletin.pk) is None
        build.assert_not_called()
        bulletin.refresh_from_db()
        assert (bulletin.updated_at, bulletin.render_model) == (stamp, model)

    def test_a_gone_pk_is_skipped(self) -> None:
        """A bulletin deleted since the enqueue needs nothing."""
        assert rebuild_one_bulletin(999_999) is None

    def test_a_row_the_service_holds_no_caaml_for_is_left(self) -> None:
        """No SourceBulletin: nothing is written and the task says it failed."""
        region = MicroRegionFactory.create(region_id="CH-1009")
        bulletin = BulletinFactory.create(
            render_model={"version": 0, "traits": [], "kept": True},
            render_model_version=0,
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=region)

        assert rebuild_one_bulletin(bulletin.pk) == REBUILD_FAILED
        bulletin.refresh_from_db()
        assert bulletin.render_model == {"version": 0, "traits": [], "kept": True}
        assert not RegionDayRating.objects.filter(region=region).exists()

    def test_force_rebuilds_a_current_row(self) -> None:
        """``--all`` and ``--bulletin-id`` rebuild even a current model."""
        region = MicroRegionFactory.create(region_id="CH-1003")
        bulletin = _bulletin(region, version=RENDER_MODEL_VERSION)

        assert rebuild_one_bulletin(bulletin.pk, force=True) == REBUILT
        bulletin.refresh_from_db()
        assert bulletin.render_model["version"] == RENDER_MODEL_VERSION
        assert bulletin.danger_levels == "|low|"

    def test_refresh_ratings_false_rates_nothing(self) -> None:
        """``--skip-day-ratings``: the row is rebuilt, no rating written."""
        region = MicroRegionFactory.create(region_id="CH-1004")
        bulletin = _bulletin(region)

        rebuild_one_bulletin(bulletin.pk, refresh_ratings=False)

        bulletin.refresh_from_db()
        assert bulletin.render_model_version == RENDER_MODEL_VERSION
        assert not RegionDayRating.objects.exists()

    def test_a_build_error_commits_the_sentinel_and_fails_the_task(self) -> None:
        """The sentinel is kept, nothing is rated, the result reads FAILED."""
        from apps.bulletins.services.render_model import RenderModelBuildError

        region = MicroRegionFactory.create(region_id="CH-1005")
        bulletin = _bulletin(region, version=5)

        with (
            patch(_INSTANCE_BUILD, side_effect=RenderModelBuildError("boom")),
            patch(_INSTANCE_RECOMPUTE) as recompute,
        ):
            result = rebuild_bulletin_task.enqueue(bulletin.pk)

        assert result.status == TaskResultStatus.FAILED
        assert "RenderRebuildFailed" in result.errors[0].exception_class_path
        recompute.assert_not_called()
        bulletin.refresh_from_db()
        assert bulletin.render_model_version == 0
        assert bulletin.render_model["error"] == "boom"

    def test_a_failed_recompute_rolls_the_row_back(self) -> None:
        """The row stays a candidate for the next run."""
        region = MicroRegionFactory.create(region_id="CH-1006")
        bulletin = _bulletin(region)

        with patch(_INSTANCE_RECOMPUTE, side_effect=RuntimeError("db gone")):
            result = rebuild_bulletin_task.enqueue(bulletin.pk)

        assert result.status == TaskResultStatus.FAILED
        bulletin.refresh_from_db()
        assert bulletin.render_model_version == 0
        assert not RegionDayRating.objects.exists()

    def test_a_committed_rebuild_drops_the_cached_season_calendar(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        """SNOW-1172: the region's cached calendar goes once the rebuild commits."""
        region = MicroRegionFactory.create(region_id="CH-1011")
        bulletin = _bulletin(region, level="considerable")
        keys = _seed_calendar(region)

        with django_capture_on_commit_callbacks(execute=True):
            rebuild_one_bulletin(bulletin.pk)

        assert all(cache.get(key) is None for key in keys)

    def test_a_rolled_back_rebuild_keeps_the_cached_season_calendar(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        """A failed recompute rolls back, so the still-correct cache stays."""
        region = MicroRegionFactory.create(region_id="CH-1012")
        bulletin = _bulletin(region)
        keys = _seed_calendar(region)

        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            with patch(_INSTANCE_RECOMPUTE, side_effect=RuntimeError("db gone")):
                with pytest.raises(RuntimeError):
                    rebuild_one_bulletin(bulletin.pk)

        assert callbacks == []
        assert all(cache.get(key) is not None for key in keys)

    @pytest.mark.parametrize("morning_first", [True, False])
    def test_siblings_on_one_pair_rate_the_morning_issue(
        self, morning_first: bool
    ) -> None:
        """Whichever task runs first, the morning issue governs the day."""
        region = MicroRegionFactory.create(region_id="CH-1007")
        morning = _bulletin(region, level="high", hour=7)
        evening = _bulletin(region, level="low", hour=17)
        order = [morning, evening] if morning_first else [evening, morning]

        for bulletin in order:
            rebuild_bulletin_task.enqueue(bulletin.pk)

        rating = RegionDayRating.objects.get(region=region, date=DAY)
        assert rating.max_rating == "high"
        assert rating.source_bulletin_id == morning.pk


class TestSourceTask:
    """``rebuild_source_bulletin_task`` over a service ``SourceBulletin``."""

    def test_a_stale_row_is_rebuilt_indexed_and_rated(self) -> None:
        """Model, target day and codes stored; the pairs recomputed."""
        row = _source(["CH-1", "CH-2"], level="considerable")

        with patch(_SOURCE_BUILD, side_effect=_by_caaml):
            result = rebuild_source_bulletin_task.enqueue(row.pk)

        assert result.return_value == REBUILT
        row.refresh_from_db()
        assert row.render_model_version == RENDER_MODEL_VERSION
        assert row.target_date == DAY
        assert row.region_codes == ["CH-1", "CH-2"]
        ratings = SourceDayRating.objects.filter(date=DAY).order_by("region_code")
        assert [(r.region_code, r.max_rating) for r in ratings] == [
            ("CH-1", "considerable"),
            ("CH-2", "considerable"),
        ]

    def test_a_current_indexed_row_is_skipped(self) -> None:
        """A second enqueue writes nothing and leaves updated_at."""
        row = _source(["CH-1"])
        with patch(_SOURCE_BUILD, side_effect=_by_caaml):
            rebuild_one_source_bulletin(row.pk)
        row.refresh_from_db()
        stamp = row.updated_at

        with patch(_SOURCE_BUILD) as build:
            assert rebuild_one_source_bulletin(row.pk) is None
        build.assert_not_called()
        row.refresh_from_db()
        assert row.updated_at == stamp

    def test_a_gone_pk_is_skipped(self) -> None:
        """A bulletin deleted since the enqueue needs nothing."""
        assert rebuild_one_source_bulletin(999_999) is None

    def test_force_walks_a_current_row(self) -> None:
        """Forced and unchanged writes nothing; forced and changed writes."""
        row = _source(["CH-1"])
        with patch(_SOURCE_BUILD, side_effect=_by_caaml):
            rebuild_one_source_bulletin(row.pk)
            assert rebuild_one_source_bulletin(row.pk, force=True) == (
                OUTCOME_UNCHANGED
            )
        with patch(_SOURCE_BUILD, return_value=_model("high")):
            assert rebuild_one_source_bulletin(row.pk, force=True) == REBUILT
        assert SourceDayRating.objects.get(region_code="CH-1").max_rating == "high"

    def test_a_build_error_commits_the_sentinel_and_fails_the_task(self) -> None:
        """No rating is recomputed; the result reads FAILED."""
        row = _source(["CH-1"])

        with patch(_SOURCE_REFRESH) as refresh:
            result = rebuild_source_bulletin_task.enqueue(row.pk)

        assert result.status == TaskResultStatus.FAILED
        refresh.assert_not_called()
        row.refresh_from_db()
        assert row.render_model_version == 0
        assert row.render_model["version"] == 0

    def test_a_failed_recompute_rolls_the_row_back(self) -> None:
        """The row stays stale, so the next run enqueues it again."""
        row = _source(["CH-1"])

        with (
            patch(_SOURCE_BUILD, side_effect=_by_caaml),
            patch(_SOURCE_REFRESH, side_effect=RuntimeError("db gone")),
        ):
            result = rebuild_source_bulletin_task.enqueue(row.pk)

        assert result.status == TaskResultStatus.FAILED
        row.refresh_from_db()
        assert row.render_model == {}
        assert row.target_date is None
        assert not SourceDayRating.objects.exists()

    @pytest.mark.parametrize("morning_first", [True, False])
    def test_siblings_on_one_pair_rate_the_morning_issue(
        self, morning_first: bool
    ) -> None:
        """Whichever task runs first, the morning issue governs the day."""
        morning = _source(["CH-1"], hour=7, level="high")
        evening = _source(["CH-1"], hour=17, level="low")
        order = [morning, evening] if morning_first else [evening, morning]

        with patch(_SOURCE_BUILD, side_effect=_by_caaml):
            for row in order:
                rebuild_source_bulletin_task.enqueue(row.pk)

        rating = SourceDayRating.objects.get(region_code="CH-1", date=DAY)
        assert rating.max_rating == "high"
        assert rating.bulletin_id == morning.bulletin_id


class TestStaleSourceDayRating:
    """``refresh_source_day_rating_task`` over a stale ``SourceDayRating``."""

    def test_a_stale_rating_is_recomputed_once(self) -> None:
        """The first run rewrites it at the current version; the second skips."""
        SourceDayRatingFactory.create(
            region_code="CH-9", date=DAY, version=DAY_RATING_VERSION - 1
        )

        result = refresh_source_day_rating_task.enqueue("CH-9", DAY.isoformat())

        assert result.return_value == 1
        assert SourceDayRating.objects.get().version == DAY_RATING_VERSION
        assert refresh_one_source_day_rating("CH-9", DAY) == 0

    def test_a_missing_rating_is_skipped(self) -> None:
        """A pair with no row is not stale: nothing is created."""
        assert refresh_one_source_day_rating("CH-9", DAY) == 0
        assert not SourceDayRating.objects.exists()


class TestLocks:
    """The rating-row locks each task takes before reading candidates."""

    def test_the_instance_lock_creates_a_missing_row(self) -> None:
        """There is a row to lock even for a pair never rated."""
        region = MicroRegionFactory.create(region_id="CH-1010")
        _lock_region_day_ratings({(region, DAY)})
        assert RegionDayRating.objects.filter(region=region, date=DAY).exists()

    def test_the_instance_lock_retries_a_racing_create(self) -> None:
        """A create lost to another transaction is read on the retry."""
        region = MicroRegionFactory.create(region_id="CH-1011")
        real = RegionDayRating.objects.get_or_create
        calls = {"n": 0}

        def _racing(**kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise IntegrityError("lost the race")
            return real(**kwargs)

        with patch.object(RegionDayRating.objects, "get_or_create", _racing):
            _lock_region_day_ratings({(region, DAY)})
        assert calls["n"] == 2

    def test_the_service_lock_creates_an_unrated_row(self) -> None:
        """The placeholder is the unrated day at the current version."""
        lock_source_day_ratings([("CH-7", DAY)])
        row = SourceDayRating.objects.get(region_code="CH-7", date=DAY)
        assert row.max_rating == "no_rating"
        assert row.version == DAY_RATING_VERSION

    def test_the_service_lock_retries_a_racing_create(self) -> None:
        """A create lost to another transaction is read on the retry."""
        real = SourceDayRating.objects.get_or_create
        calls = {"n": 0}

        def _racing(**kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise IntegrityError("lost the race")
            return real(**kwargs)

        with patch.object(SourceDayRating.objects, "get_or_create", _racing):
            lock_source_day_ratings([("CH-7", DAY)])
        assert calls["n"] == 2

    def test_an_empty_lock_does_nothing(self) -> None:
        """No pairs, no query."""
        _lock_region_day_ratings(set())
        lock_source_day_ratings([])
        assert not RegionDayRating.objects.exists()
        assert not SourceDayRating.objects.exists()


def test_every_task_is_low_priority() -> None:
    """User-facing work on the default queue goes first."""
    assert RENDER_TASK_PRIORITY == -50
    for task in (
        rebuild_bulletin_task,
        rebuild_source_bulletin_task,
        refresh_source_day_rating_task,
    ):
        assert task.priority == RENDER_TASK_PRIORITY


def test_a_failed_source_build_is_reported() -> None:
    """The body returns the failure for the wrapper to raise."""
    row = _source(["CH-1"])
    assert rebuild_one_source_bulletin(row.pk) == REBUILD_FAILED


def _record_locks(events: list[tuple[str, Any]]) -> Any:
    """Patch ``QuerySet.select_for_update`` to record each lock, then lock.

    SQLite emits no ``FOR UPDATE``, so the call itself is the evidence:
    each entry is ``("lock", (model name, no_key))``.
    """
    real = QuerySet.select_for_update

    def _spy(self: QuerySet, *args: Any, **kwargs: Any) -> QuerySet:
        events.append(("lock", (self.model.__name__, kwargs.get("no_key", False))))
        return real(self, *args, **kwargs)

    return patch.object(QuerySet, "select_for_update", _spy)


def _before_first(events: list[tuple[str, Any]], kind: str) -> list[tuple[str, Any]]:
    """Return the events recorded before the first of ``kind``.

    Fails if ``kind`` never happened, so the prefix is never vacuous.
    """
    kinds = [event[0] for event in events]
    return events[: kinds.index(kind)]


def _index(queries: CaptureQueriesContext, *needles: str, after: int = -1) -> int:
    """Return the index of the first SELECT after ``after`` holding ``needles``."""
    return next(
        i
        for i, q in enumerate(queries.captured_queries)
        if i > after
        and q["sql"].startswith("SELECT")
        and all(needle in q["sql"] for needle in needles)
    )


def _order_clause(queries: CaptureQueriesContext, index: int) -> str:
    """Return the ORDER BY clause of the captured query at ``index``."""
    sql = queries.captured_queries[index]["sql"]
    return sql[sql.index("ORDER BY") :]


class TestLockOrder:
    """The pair locks are taken, sorted, before the candidates are read."""

    def test_instance_locks_bulletin_no_key_then_pairs_before_candidates(
        self,
    ) -> None:
        """Bulletin FOR NO KEY UPDATE; ratings FOR UPDATE, sorted, then read."""
        first = MicroRegionFactory.create(region_id="CH-2001")
        second = MicroRegionFactory.create(region_id="CH-2002")
        bulletin = _bulletin(second)
        RegionBulletinFactory.create(bulletin=bulletin, region=first)
        events: list[tuple[str, Any]] = []
        real_recompute = recompute_region_day

        def _recompute(region: MicroRegion, day: date, **kwargs: Any) -> None:
            events.append(("recompute", region.pk))
            real_recompute(region, day, **kwargs)

        with (
            _record_locks(events),
            patch(_INSTANCE_RECOMPUTE, side_effect=_recompute),
            CaptureQueriesContext(connection) as queries,
        ):
            rebuild_one_bulletin(bulletin.pk)

        # update_or_create inside the recompute takes its own row lock, so
        # only the locks before the first recompute are the task's.
        assert _before_first(events, "recompute") == [
            ("lock", ("Bulletin", True)),
            ("lock", ("RegionDayRating", False)),
        ]
        assert [e for e in events if e[0] == "recompute"] == [
            ("recompute", first.pk),
            ("recompute", second.pk),
        ]
        lock_at = _index(queries, 'FROM "bulletins_regiondayrating"', " OR ")
        order = _order_clause(queries, lock_at)
        assert order.index('"region_id"') < order.index('"date"')
        candidates_at = _index(queries, 'FROM "bulletins_bulletin"', '"target_date" =')
        assert lock_at < candidates_at

    def test_source_locks_bulletin_no_key_then_pairs_before_candidates(
        self,
    ) -> None:
        """Bulletin FOR NO KEY UPDATE; ratings FOR UPDATE, sorted, then read."""
        row = _source(["CH-2", "CH-1"])
        events: list[tuple[str, Any]] = []
        real_refresh = refresh_source_day_ratings

        def _refresh(pairs: Any, **kwargs: Any) -> int:
            events.append(("refresh", sorted(pairs)))
            return real_refresh(pairs, **kwargs)

        with (
            patch(_SOURCE_BUILD, side_effect=_by_caaml),
            _record_locks(events),
            patch(_SOURCE_REFRESH, side_effect=_refresh),
            CaptureQueriesContext(connection) as queries,
        ):
            rebuild_one_source_bulletin(row.pk)

        assert _before_first(events, "refresh") == [
            ("lock", ("SourceBulletin", True)),
            ("lock", ("SourceDayRating", False)),
        ]
        assert [e for e in events if e[0] == "refresh"] == [
            ("refresh", [("CH-1", DAY), ("CH-2", DAY)]),
        ]
        lock_at = _index(queries, 'FROM "ingest_sourcedayrating"', " OR ")
        order = _order_clause(queries, lock_at)
        assert order.index('"region_code"') < order.index('"date"')
        candidates_at = _index(
            queries, 'FROM "ingest_sourcebulletin"', '"target_date" ='
        )
        assert lock_at < candidates_at

    @pytest.mark.skipif(
        not connection.features.has_select_for_no_key_update,
        reason="the backend emits no row locks (SQLite)",
    )
    def test_the_bulletin_lock_compiles_to_for_no_key_update(self) -> None:
        """On Postgres the bulletin lock is FOR NO KEY UPDATE, not FOR UPDATE."""
        bulletin = _bulletin(MicroRegionFactory.create(region_id="CH-2003"))
        with CaptureQueriesContext(connection) as queries:
            rebuild_one_bulletin(bulletin.pk, refresh_ratings=False)
        # The django_db transaction makes the task's atomic() open a
        # SAVEPOINT first, so find the bulletin SELECT rather than query 0.
        lock_at = _index(queries, 'FROM "bulletins_bulletin"')
        assert "FOR NO KEY UPDATE" in queries.captured_queries[lock_at]["sql"]


class TestInterleaving:
    """A sibling that commits just before this task locks is read by it."""

    @pytest.mark.parametrize("first", ["morning", "evening"])
    def test_instance_sibling_committing_before_the_lock_is_read(
        self, first: str
    ) -> None:
        """B has written; A runs whole before B locks; B rates correctly."""
        region = MicroRegionFactory.create(region_id="CH-3001")
        rows = {
            "morning": _bulletin(region, level="high", hour=7),
            "evening": _bulletin(region, level="low", hour=17),
        }
        a = rows[first]
        b = rows["evening" if first == "morning" else "morning"]
        real_lock = _lock_region_day_ratings

        def _a_then_lock(pairs: Any) -> None:
            rebuild_one_bulletin(a.pk)
            real_lock(pairs)

        with patch(_INSTANCE_LOCK, side_effect=_a_then_lock):
            rebuild_one_bulletin(b.pk)

        rating = RegionDayRating.objects.get(region=region, date=DAY)
        assert rating.max_rating == "high"
        assert rating.source_bulletin_id == rows["morning"].pk

    @pytest.mark.parametrize("first", ["morning", "evening"])
    def test_source_sibling_committing_before_the_lock_is_read(
        self, first: str
    ) -> None:
        """B has written; A runs whole before B locks; B rates correctly."""
        rows = {
            "morning": _source(["CH-1"], hour=7, level="high"),
            "evening": _source(["CH-1"], hour=17, level="low"),
        }
        a = rows[first]
        b = rows["evening" if first == "morning" else "morning"]
        real_lock = lock_source_day_ratings

        def _a_then_lock(pairs: Any) -> None:
            rebuild_one_source_bulletin(a.pk)
            real_lock(pairs)

        with (
            patch(_SOURCE_BUILD, side_effect=_by_caaml),
            patch(_SOURCE_LOCK, side_effect=_a_then_lock),
        ):
            rebuild_one_source_bulletin(b.pk)

        rating = SourceDayRating.objects.get(region_code="CH-1", date=DAY)
        assert rating.max_rating == "high"
        assert rating.bulletin_id == rows["morning"].bulletin_id
