"""
apps/bulletins/services/render_rebuild.py — one bulletin's render model, one task.

SNOW-1346, after SNOW-1345's ``apps/routes/services/ski_tour_render.py``.
``rebuild_render_models --commit`` and ``rebuild_source_render_models
--commit`` used to walk every stale bulletin in one process and recompute
the day ratings at the end; now they only enqueue, and each bulletin is
its own task on the background runner (``db_worker`` in production; inline
under ``ImmediateBackend`` in dev, test and staging):

  rebuild_bulletin_task.enqueue(pk, force, refresh_ratings)
      An instance ``Bulletin``: re-read it and skip it if it is gone or
      already current (unless ``force``), rebuild its render model from
      ``raw_data``, write the model, its version and ``danger_levels``,
      then recompute the ``RegionDayRating`` of every (region, day) it
      governs.

  rebuild_source_bulletin_task.enqueue(pk, force, refresh_ratings)
      A service ``SourceBulletin``: the same through
      ``apps.ingest.public.rebuild_render_model``, then
      ``refresh_source_day_ratings`` for the pairs it governed before and
      governs now.

  refresh_source_day_rating_task.enqueue(region_code, day)
      One ``SourceDayRating`` computed under an older ``DAY_RATING_VERSION``
      with no bulletin rebuild to reach it: re-check it is still stale,
      then recompute it.

Each body runs in one ``transaction.atomic()``: the row write and its
day-rating recompute commit together, so a recompute that fails rolls the
row back and it stays a candidate for the next run. Before the recompute
reads its candidates, the task locks every pair's rating row
(``select_for_update``, sorted by region and day, a missing row created
first), so two sibling bulletins' tasks on one pair recompute one after
the other and the second reads the first's committed write, whatever the
order the worker took them in.

The bulletin row itself is locked FOR NO KEY UPDATE, not FOR UPDATE: a
sibling's recompute can point a shared rating's ``source_bulletin`` at
this bulletin, and that foreign-key check takes a KEY SHARE lock on it,
which FOR UPDATE blocks while this task waits on the rating lock the
sibling holds — a deadlock on Postgres. FOR NO KEY UPDATE does not
conflict with KEY SHARE.

A build error commits the version-0 sentinel and recomputes no ratings;
the task wrapper then raises ``RenderRebuildFailed`` after the commit, so
the task result reads FAILED and the command counts it.

The ``@task`` wrappers live here, not in ``apps/ingest``, because ingest
imports no other ``apps.*`` package (tests/ingest/test_boundary.py) and
``django_tasks`` is the host's runner.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from django.db import IntegrityError, models, transaction
from django_tasks import task

from apps.bulletins.models import Bulletin, RegionDayRating
from apps.bulletins.services.day_rating import day_rating_pairs, recompute_region_day
from apps.bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    build_render_model,
    danger_levels_column,
)
from apps.ingest.public import (
    DAY_RATING_VERSION,
    REBUILD_FAILED,
    REBUILT,
    SourceBulletin,
    SourceDayRating,
    lock_source_day_ratings,
    rebuild_render_model,
    refresh_source_day_ratings,
)
from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)

# Below the default (0), so user-facing work on the queue goes first.
RENDER_TASK_PRIORITY = -50

# What an instance task reads off its Bulletin: the CAAML it builds from,
# the version it checks, and what day_rating_pairs and the log line need.
_BULLETIN_FIELDS: tuple[str, ...] = (
    "pk",
    "bulletin_id",
    "source",
    "raw_data",
    "render_model_version",
    "valid_from",
    "target_date",
)

# What rebuild_render_model reads and writes on a SourceBulletin.
_SOURCE_FIELDS: tuple[str, ...] = (
    "pk",
    "bulletin_id",
    "provider",
    "caaml",
    "render_model",
    "render_model_version",
    "valid_from",
    "target_date",
    "region_codes",
)


class RenderRebuildFailed(RuntimeError):
    """A bulletin's render model failed to build; its sentinel is committed."""


def _lock_region_day_ratings(pairs: set[tuple[MicroRegion, date]]) -> None:
    """Lock each (region, day)'s ``RegionDayRating`` row until commit.

    A missing row is created first (``recompute_region_day`` overwrites
    every column of it), so there is a row to lock; a concurrent create of
    the same row is retried once as a read. The rows are then locked in one
    ``SELECT … FOR UPDATE`` ordered by (region, day), so two tasks locking
    overlapping pairs take them in the same order and cannot deadlock.

    Args:
        pairs: The (region, day) pairs about to be recomputed.

    """
    keys = sorted({(region.pk, day) for region, day in pairs})
    if not keys:
        return
    match = models.Q()
    for region_pk, day in keys:
        try:
            RegionDayRating.objects.get_or_create(region_id=region_pk, date=day)
        except IntegrityError:
            # Another transaction created it between our read and insert.
            RegionDayRating.objects.get_or_create(region_id=region_pk, date=day)
        match |= models.Q(region_id=region_pk, date=day)
    list(
        RegionDayRating.objects.select_for_update()
        .filter(match)
        .order_by("region_id", "date")
        .values_list("pk", flat=True)
    )


def rebuild_one_bulletin(
    pk: int, *, force: bool = False, refresh_ratings: bool = True
) -> str | None:
    """Rebuild one instance bulletin's render model; the task's body.

    Args:
        pk: The ``Bulletin``'s primary key.
        force: Rebuild even when the stored model is current (``--all``,
            ``--bulletin-id``).
        refresh_ratings: Recompute the ``RegionDayRating`` of every
            (region, day) the bulletin governs.

    Returns:
        ``REBUILT``, ``REBUILD_FAILED`` (the sentinel was committed), or
        None when the bulletin is gone or already current.

    """
    with transaction.atomic():
        bulletin = (
            Bulletin.objects.select_for_update(no_key=True)
            .only(*_BULLETIN_FIELDS)
            .filter(pk=pk)
            .first()
        )
        if bulletin is None:
            logger.info("render rebuild: bulletin pk=%s is gone, skipping", pk)
            return None
        if not force and bulletin.render_model_version >= RENDER_MODEL_VERSION:
            return None

        props = (bulletin.raw_data or {}).get("properties", {})
        render_model: dict[str, Any]
        try:
            render_model = build_render_model(props)
            version = RENDER_MODEL_VERSION
        except RenderModelBuildError as exc:
            logger.exception(
                "Failed to build render model for bulletin %s: %s",
                bulletin.bulletin_id,
                exc,
            )
            render_model = {
                "version": 0,
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            }
            version = 0
        # danger_levels is read off the model it was built with, so the
        # column moves with every rebuild (SNOW-1341).
        Bulletin.objects.filter(pk=pk).update(
            render_model=render_model,
            render_model_version=version,
            danger_levels=danger_levels_column(render_model),
        )
        if version == 0:
            return REBUILD_FAILED
        if refresh_ratings:
            # A rebuild moves neither the bulletin's regions nor its target
            # day, so the pairs it governed before are the pairs it governs.
            pairs = day_rating_pairs([bulletin])
            _lock_region_day_ratings(pairs)
            for region, day in sorted(pairs, key=lambda p: (p[0].pk, p[1])):
                # Not refresh_day_ratings, which swallows a failure: one
                # here must roll the rebuild back with it.
                recompute_region_day(region, day, commit=True)
    return REBUILT


def rebuild_one_source_bulletin(
    pk: int, *, force: bool = False, refresh_ratings: bool = True
) -> str | None:
    """Rebuild one service bulletin's render model; the task's body.

    Args:
        pk: The ``SourceBulletin``'s primary key.
        force: Rebuild even when the stored model is current and indexed
            (``--all``).
        refresh_ratings: Recompute the ``SourceDayRating`` of every
            (region code, day) the bulletin governed before or governs now.

    Returns:
        ``REBUILT``, ``REBUILD_FAILED`` (the sentinel was committed if it
        changed), ``OUTCOME_UNCHANGED`` for a forced rebuild that came out
        the same, or None when the bulletin is gone or already current.

    """
    with transaction.atomic():
        row = (
            SourceBulletin.objects.select_for_update(no_key=True)
            .only(*_SOURCE_FIELDS)
            .filter(pk=pk)
            .first()
        )
        if row is None:
            logger.info("render rebuild: source bulletin pk=%s is gone, skipping", pk)
            return None
        if (
            not force
            and row.render_model_version >= RENDER_MODEL_VERSION
            and row.target_date is not None
        ):
            return None
        touched: set[tuple[str, date]] = set()
        outcome = rebuild_render_model(row, commit=True, touched=touched)
        if outcome == REBUILD_FAILED:
            logger.error("render rebuild: render model failed for %s", row)
            return outcome
        if refresh_ratings and touched:
            lock_source_day_ratings(touched)
            refresh_source_day_ratings(touched)
    return outcome


def refresh_one_source_day_rating(region_code: str, day: date) -> int:
    """Recompute one stale ``SourceDayRating``; the task's body.

    Args:
        region_code: The region's EAWS code.
        day: The rated day.

    Returns:
        How many rows were written: 0 when the row is gone or no longer
        stale (a bulletin task reached it first), else 0 or 1.

    """
    with transaction.atomic():
        stale = SourceDayRating.objects.stale(DAY_RATING_VERSION).filter(
            region_code=region_code, date=day
        )
        if not stale.exists():
            return 0
        lock_source_day_ratings([(region_code, day)])
        return refresh_source_day_ratings([(region_code, day)])


@task(priority=RENDER_TASK_PRIORITY)
def rebuild_bulletin_task(
    pk: int, force: bool = False, refresh_ratings: bool = True
) -> str | None:
    """Background worker: rebuild one instance bulletin (SNOW-1346).

    Takes a primary key, not the row, so the task is JSON-serialisable into
    the database and replayable.

    Args:
        pk: The ``Bulletin``'s primary key.
        force: Rebuild even a current model.
        refresh_ratings: Recompute its day ratings.

    Returns:
        The outcome, kept on the task result, or None when skipped.

    Raises:
        RenderRebuildFailed: The build failed; its sentinel is committed.

    """
    outcome = rebuild_one_bulletin(pk, force=force, refresh_ratings=refresh_ratings)
    if outcome == REBUILD_FAILED:
        raise RenderRebuildFailed(f"render model failed for bulletin pk={pk}")
    return outcome


@task(priority=RENDER_TASK_PRIORITY)
def rebuild_source_bulletin_task(
    pk: int, force: bool = False, refresh_ratings: bool = True
) -> str | None:
    """Background worker: rebuild one service bulletin (SNOW-1346).

    Args:
        pk: The ``SourceBulletin``'s primary key.
        force: Rebuild even a current, indexed model.
        refresh_ratings: Recompute its day ratings.

    Returns:
        The outcome, kept on the task result, or None when skipped.

    Raises:
        RenderRebuildFailed: The build failed; its sentinel is committed.

    """
    outcome = rebuild_one_source_bulletin(
        pk, force=force, refresh_ratings=refresh_ratings
    )
    if outcome == REBUILD_FAILED:
        raise RenderRebuildFailed(f"render model failed for source bulletin pk={pk}")
    return outcome


@task(priority=RENDER_TASK_PRIORITY)
def refresh_source_day_rating_task(region_code: str, day: str) -> int:
    """Background worker: recompute one stale service day rating (SNOW-1346).

    Args:
        region_code: The region's EAWS code.
        day: The rated day, ISO format (a task's arguments are JSON).

    Returns:
        How many rows were written.

    """
    return refresh_one_source_day_rating(region_code, date.fromisoformat(day))
