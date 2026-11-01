"""
apps/bulletins/services/day_rating.py — Per-(region, date) danger rating aggregation.

Maintains the RegionDayRating denormalisation table. Each row stores both the
minimum and maximum danger ratings (within one chosen bulletin) for a single
(region, calendar day) pair.

Aggregation policy (v8 — elevation-band split + afternoon-elevated split + AM/PM):
  - For day X, pick the single bulletin that was most recently published by
    ~10am on day X:
    - Morning-of-X (valid_from.date() == X, hour < 12) takes priority.
    - Prior-evening-of-(X-1) (valid_from.date() == X-1, hour >= 12) is the
      fallback when no morning-of-X bulletin exists.
    - Evening-of-X (valid_from.date() == X, hour >= 12) is excluded — its
      target day is X+1.
  - Formally: keep candidates where ``target_date == X`` (a stored column,
    populated at ingest by ``target_day_for_valid_from``); pick the one
    with the latest ``valid_from``.  Because morning-of-X has a later
    ``valid_from`` than prior-evening-of-(X-1), this naturally implements
    the morning-wins / prior-evening-fallback convention.
  - Min/max split logic — checked in this precedence order:

    1. **Elevation-band split (SNOW-293)**: if
       ``render_model["danger"]["ratings"]`` contains two or more entries
       with ``period="all_day"`` and at least two distinct ``key`` values,
       the day carries an elevation-band split (Météo-France style).
       ``min_rating`` = key of the lowest-ranked band (sorted by
       ``_DANGER_KEY_RANK``); ``max_rating`` = key of the highest-ranked
       band. Subdivisions mirror the headline.

    2. **Time-period split (afternoon-elevated)**: during the trait loop,
       collect ``morning_levels`` from traits with
       ``time_period in ("all_day", "earlier")`` and ``afternoon_levels``
       from traits with ``time_period == "later"``. If both lists are
       non-empty and ``max(afternoon_levels) > max(morning_levels)``,
       produce a split: ``min_rating = key of max(morning_levels)``,
       ``max_rating = key of max(afternoon_levels)``.

    3. **Fallback (headline-only)**: both ``min_rating`` and ``max_rating``
       are set to the bulletin's headline ``render_model["danger"]["key"]``
       (the CAAML aggregate). This keeps the heatmap tile in sync with the
       Day Risk Profile panel, which also shows the headline rating (SNOW-138).

  - AM/PM split fields (SNOW-291):
    - ``am_rating`` / ``pm_rating`` are populated whenever both
      ``morning_levels`` AND ``afternoon_levels`` are non-empty, regardless of
      whether the afternoon level is higher than the morning level.  They
      encode the problem-mix split for flat-but-split days (same level, two
      distinct problem categories) in addition to the escalating case.
    - ``am_rating = key of max(morning_levels)``,
      ``pm_rating = key of max(afternoon_levels)``.
    - Both fields stay ``None`` (uniform day) when the bulletin has no
      ``later`` traits, preserving the single-colour calendar tile for those
      rows.
    - Subdivision for both AM and PM mirrors the headline bulletin subdivision
      (same simplification as ``min/max_subdivision``).
    - The AM/PM time split is independent of the elevation-band split — the
      two encode orthogonal structure (time of day vs band) and either, both,
      or neither may apply to a single bulletin.
  - Bulletins with an empty traits list (quiet day) still benefit from the
    elevation-band split check (step 1) because it reads ``danger.ratings``,
    not ``traits``. AM/PM stays ``None`` on quiet days because the time split
    is purely trait-driven.
  - Bulletins with a completely malformed render_model (empty dict or missing
    both ``danger`` and ``traits``) → write ``no_rating``.
  - Traits with missing or non-integer ``danger_level`` are skipped (debug log).
    If all traits are invalid the row is written as ``no_rating``.
  - Qualifying means ``render_model_version >= RENDER_MODEL_VERSION``
    (version=0 error sentinels are excluded).
  - ``source_bulletin`` is always the chosen bulletin (or None when no
    candidate exists).
  - ``max_subdivision`` / ``min_subdivision``: sourced from the chosen
    bulletin's aggregate ``render_model["danger"]["subdivision"]``.

Since SNOW-1270 the reading of the chosen bulletin — the min/max, AM/PM,
bands and subdivision rules above — and ``target_day_for_valid_from`` are
the service's (``apps.ingest.normalise.day_rating``, through
``apps.ingest.public``); this module selects each (region, day)'s bulletin,
writes the row and keeps the season calendars' cache in step. Both names
are re-exported here for existing callers.

This module intentionally does NOT use post_save signals.
Call ``apply_bulletin_day_ratings`` from ``upsert_bulletin`` inline.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.core.cache.utils import make_template_fragment_key
from django.db.models import Prefetch
from django.utils import timezone, translation

from apps.bulletins.models import Bulletin
from apps.ingest.public import (
    DAY_RATING_FIELDS,
    DAY_RATING_VERSION,
    OUTCOME_CREATED,
    OUTCOME_UNCHANGED,
    OUTCOME_UPDATED,
    RENDER_MODEL_VERSION,
    rate_render_model,
    target_day_for_valid_from,
    unrated_day,
)
from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)

# The only MicroRegion fields recompute_region_day reads. day_rating_pairs
# and slim_regions_prefetch load these and defer the rest (SNOW-1054).
_SLIM_REGION_FIELDS: tuple[str, ...] = ("pk", "region_id")


def recompute_region_day(
    region: "MicroRegion",
    day: date,
    *,
    commit: bool = True,
) -> None:
    """
    Recompute and (optionally) persist the RegionDayRating for one (region, day).

    Selects the single bulletin that was most recently published by ~10am on
    ``day`` (the morning-of-day if available; otherwise the prior-evening).
    Aggregates min/max ratings across that bulletin's traits only.

    Candidates are selected with a single ``target_date=day`` equality
    filter — ``target_date`` is populated at ingest time by
    ``target_day_for_valid_from``, so this already excludes any
    evening-of-day bulletin (whose target is day+1) without a Python
    post-filter.

    Sets ``min_rating`` / ``max_rating`` using the v8 policy: elevation-band
    split from ``danger.ratings`` (precedence 1), afternoon-elevated trait
    split (precedence 2), or headline-only fallback (precedence 3).
    Quiet-day bulletins (empty traits) still check for an elevation-band
    split before falling back to the headline.

    Writes ``no_rating`` when no qualifying bulletin exists or when the
    chosen bulletin's render_model is entirely malformed.

    Args:
        region: The MicroRegion to aggregate for.
        day: The calendar date to aggregate.
        commit: When True (default), upsert the RegionDayRating row.
                When False, log what would be written without touching the DB.

    """
    from apps.bulletins.models import RegionDayRating

    # target_date is populated at ingest time by target_day_for_valid_from,
    # so a single equality filter already selects exactly the candidates for
    # day X (morning-of-X and prior-evening-of-(X-1)) and excludes the
    # evening-of-X bulletin (whose target is X+1) — no Python post-filter
    # needed.
    candidates = list(
        Bulletin.objects.for_target_date(day).filter(
            regions=region,
            render_model_version__gte=RENDER_MODEL_VERSION,
        )
    )

    # Single-bulletin policy: pick the candidate with the latest valid_from.
    # When both morning-of-X and prior-evening-of-(X-1) exist, morning-of-X
    # has the later valid_from and wins. Reading the chosen bulletin is the
    # service's rule (SNOW-1270).
    chosen = max(candidates, key=lambda b: b.valid_from, default=None)
    rating = (
        rate_render_model(chosen.render_model, chosen.bulletin_id)
        if chosen is not None
        else unrated_day()
    )
    source_bulletin = chosen if rating["rated"] else None
    min_key, max_key = rating["min_rating"], rating["max_rating"]

    if not commit:
        logger.info(
            "[read-only] Would write RegionDayRating: region=%s date=%s min=%s max=%s",
            region.region_id,
            day,
            min_key,
            max_key,
        )
        return

    RegionDayRating.objects.update_or_create(
        region=region,
        date=day,
        defaults={
            "min_rating": rating["min_rating"],
            "min_subdivision": rating["min_subdivision"],
            "max_rating": rating["max_rating"],
            "max_subdivision": rating["max_subdivision"],
            "am_rating": rating["am_rating"],
            "am_subdivision": rating["am_subdivision"],
            "pm_rating": rating["pm_rating"],
            "pm_subdivision": rating["pm_subdivision"],
            "source_bulletin": source_bulletin,
            "version": DAY_RATING_VERSION,
            "source": rating["source"],
            "bands": rating["bands"],
        },
    )
    logger.debug(
        "RegionDayRating upserted: region=%s date=%s min=%s max=%s "
        "am=%s pm=%s source=%s",
        region.region_id,
        day,
        min_key,
        max_key,
        rating["am_rating"],
        rating["pm_rating"],
        rating["source"],
    )


def day_rating_pairs(
    bulletins: Iterable["Bulletin"],
) -> set[tuple["MicroRegion", date]]:
    """
    Return the distinct (region, target day) pairs touched by ``bulletins``.

    Each bulletin's target day is read from ``bulletin.target_date``, falling
    back to :func:`target_day_for_valid_from` for any un-backfilled row
    (SNOW-560). Crossed with every region the bulletin is linked to via
    ``RegionBulletin``, so a bulletin covering several regions contributes one
    pair per region.

    Shared by every management command that needs to refresh RegionDayRating
    after a batch mutation (re-keying, reformatting, purging) — collect the
    pairs before the mutation touches the bulletins' region links, then pass
    them to :func:`refresh_day_ratings`.

    Each region carries ``pk`` and ``region_id`` only — all that
    :func:`recompute_region_day` reads. Callers that stream bulletins
    prefetch the regions with :func:`slim_regions_prefetch`, and this
    reads that cache; without a prefetch it loads the two fields itself.
    The callers accumulate these pairs across a whole run, and every
    bulletin loads its regions afresh, so the
    set holds one instance per (region, day): a full-season rebuild touches
    tens of thousands. A full ``MicroRegion`` carries its boundary polygon
    (~5 KB parsed for a Swiss region, ~30 KB for an Austrian or Italian one),
    which put a full rebuild on course for ~2 GB of held memory (SNOW-1054).
    With two fields a pair costs ~0.5 KB.

    Args:
        bulletins: Bulletins whose (region, day) pairs should be collected.

    Returns:
        A set of ``(region, day)`` tuples, deduplicated across bulletins.
        Each region has every field but ``pk`` and ``region_id`` deferred.

    """
    pairs: set[tuple["MicroRegion", date]] = set()
    for bulletin in bulletins:
        day = bulletin.target_date or target_day_for_valid_from(bulletin.valid_from)
        # A caller that streams bulletins prefetches their regions with
        # slim_regions_prefetch(); read that cache. Chaining .only() onto a
        # prefetched manager would bypass the cache and query per bulletin.
        prefetched = getattr(bulletin, "_prefetched_objects_cache", {})
        regions = (
            bulletin.regions.all()
            if "regions" in prefetched
            else bulletin.regions.only(*_SLIM_REGION_FIELDS)
        )
        for region in regions:
            pairs.add((region, day))
    return pairs


def slim_regions_prefetch() -> Prefetch:
    """Return a ``regions`` prefetch loading only what the day ratings read.

    For any command that streams bulletins and hands them to
    :func:`day_rating_pairs`: pass it to ``prefetch_related`` in place of
    the bare ``"regions"``, so each chunk's single regions query loads two
    columns rather than every boundary polygon, and ``day_rating_pairs``
    reads the cache instead of querying per bulletin (SNOW-1054).

    Returns:
        A ``Prefetch`` over ``MicroRegion`` restricted to ``pk`` and
        ``region_id``.

    """
    return Prefetch("regions", queryset=MicroRegion.objects.only(*_SLIM_REGION_FIELDS))


def refresh_day_ratings(pairs: Iterable[tuple["MicroRegion", date]]) -> int:
    """
    Recompute RegionDayRating for every (region, day) pair supplied.

    Then deletes the cached season calendar of every distinct region in
    ``pairs`` (SNOW-1172), so a batch command never leaves the calendar
    serving markup built from the old ratings. A region whose recompute
    failed is invalidated too: its rows may have changed before the
    failure, and a spare re-render is cheap. Cache errors are logged by
    :func:`_invalidate_season_calendars` and never change the return value.

    Args:
        pairs: (region, day) tuples, typically produced by
            :func:`day_rating_pairs`. Any iterable, including a one-shot
            generator: it is read once.

    Returns:
        The number of pairs that failed to recompute.

    """
    pairs = list(pairs)
    failures = 0
    for region, day in pairs:
        try:
            recompute_region_day(region, day, commit=True)
        except Exception:
            failures += 1
            logger.exception(
                "Failed to refresh day rating for region=%s day=%s",
                region.region_id,
                day,
            )
    regions = {region.pk: region for region, _day in pairs}
    _invalidate_season_calendars(regions.values())
    return failures


# Bumped whenever the season calendar's markup changes shape, so a deploy
# never serves a body cached by the previous release against the new CSS
# for the rest of its 25-hour life (SNOW-1163 removed the counted key and
# its .season-cal-key-full rules).
SEASON_CALENDAR_MARKUP_VERSION = "2"


def season_calendar_cache_key(
    canonical_region_id: str, day_iso: str, language: str
) -> str:
    """Return the cache key for one region's rendered season calendar.

    Shared by ``season_calendar_partial`` (which stores the body) and
    :func:`apply_bulletin_day_ratings` (which deletes it after ingest). The
    body is rendered text — month names and level labels in the request's
    language — so the language is part of the key (SNOW-1128), and so is
    :data:`SEASON_CALENDAR_MARKUP_VERSION` (SNOW-1163). ``language``
    is normalised to its ``settings.LANGUAGES`` code, so the active
    ``"en-gb"`` and the listed ``"en"`` name the same key.

    Args:
        canonical_region_id: The region's slugified id, e.g. ``"ch-4115"``.
        day_iso: The calendar day the body was rendered on, ISO format.
        language: A language code, e.g. ``translation.get_language()``.

    Returns:
        The cache key string.

    """
    code = translation.get_supported_language_variant(language)
    return make_template_fragment_key(
        "season_calendar",
        [SEASON_CALENDAR_MARKUP_VERSION, canonical_region_id, day_iso, code],
    )


def _invalidate_season_calendars(regions: Iterable["MicroRegion"]) -> None:
    """
    Delete every language's cached season calendar for each of ``regions``.

    Reads only ``canonical_region_id`` (derived from ``region_id``), so the
    slim regions :func:`day_rating_pairs` returns are sufficient. Cache
    failures are logged and never abort ingest.

    Args:
        regions: The regions whose season-calendar bodies are now stale.

    """
    # Invalidate the season-calendar response cache for each affected region so
    # the next HTMX open re-queries with the freshly written RegionDayRating rows.
    # Keyed to today (the date the response was cached on), not the bulletin's
    # target day — the cache is per-calendar-day, not per-bulletin.
    # Uses make_template_fragment_key to produce the same key that
    # season_calendar_partial stores on a cache miss.
    # SNOW-1128: one cached body per language, so every language's key goes.
    today_iso = timezone.localdate().isoformat()
    for region in regions:
        cache_keys = [
            season_calendar_cache_key(region.canonical_region_id, today_iso, code)
            for code, _name in settings.LANGUAGES
        ]
        try:
            cache.delete_many(cache_keys)
        except Exception:
            logger.exception(
                "Failed to invalidate season_calendar cache key for region=%s;"
                " ingest continues.",
                region.canonical_region_id,
            )


def apply_bulletin_day_ratings(
    bulletin: "Bulletin",
    *,
    prior_pairs: Iterable[tuple["MicroRegion", date]] = (),
) -> int:
    """
    Recompute RegionDayRating for the (region, target_day) pairs of a bulletin.

    A bulletin targets exactly one calendar day — read from ``bulletin.target_date``
    (populated at ingest time), falling back to ``target_day_for_valid_from`` for
    any un-backfilled row: morning issues (valid_from.hour < 12) target their
    own date; evening issues (valid_from.hour >= 12) target the following date.

    For each region linked to the bulletin, calls ``recompute_region_day`` for
    that single target day.  The recompute also pulls in the complementary
    candidate (morning + prior-evening pair) so the chosen bulletin for the day
    is always up to date.

    ``prior_pairs`` are the (region, day) pairs the bulletin covered *before*
    an update rewrote its region links or moved its target day — captured by
    the caller with :func:`day_rating_pairs` ahead of the mutation. They are
    recomputed alongside the current pairs, so a correction that drops a
    region, or moves off a day, reconciles the rating it leaves behind rather
    than leaving it sourced from a bulletin that no longer covers it
    (SNOW-1168). Pairs are deduplicated on ``(region.pk, day)``. Defaults to
    none, which is the first-ingest case.

    Each pair is recomputed independently: a failure for one pair does not
    stop the others. When a recompute fails, the stale RegionDayRating row for
    that ``(region, day)`` pair is **deleted** so the public API can
    never keep serving an out-of-date rating — a bulletin that flipped from
    low to high danger must not leave the old low rating live. A missing row is
    rendered as ``no_rating`` ("no current data") downstream, which is the
    correct, safe fallback (SNOW-461).

    After recomputing, invalidates the season-calendar fragment cache key for
    each affected region (current and prior) so the next HTMX open re-queries
    rather than serving stale markup. Cache failures are logged and never
    abort ingest.

    Designed to be called inline from ``upsert_bulletin`` after the
    RegionBulletin links are created. Day-rating failures never abort ingest
    (the authoritative data lives in Bulletin/RegionBulletin), but the caller
    should treat a non-zero return as a ``records_failed`` increment so the
    pipeline run is marked failed and cron/CI surface it.

    Args:
        bulletin: The Bulletin whose linked (region, target_day) pairs to refresh.
        prior_pairs: (region, day) pairs the bulletin covered before an
            update, to reconcile alongside the current ones.

    Returns:
        The number of pairs whose recompute failed (0 on full success).

    """
    from apps.bulletins.models import RegionDayRating

    target = bulletin.target_date or target_day_for_valid_from(bulletin.valid_from)

    # The current pairs first, then any prior pair not already among them —
    # keyed on (pk, day) so instances from different querysets collapse.
    pairs: dict[tuple[int, date], tuple["MicroRegion", date]] = {}
    for region in bulletin.regions.all():
        pairs[(region.pk, target)] = (region, target)
    for region, day in prior_pairs:
        pairs.setdefault((region.pk, day), (region, day))

    failed_count = 0
    for region, day in pairs.values():
        try:
            recompute_region_day(region, day, commit=True)
        except Exception:
            failed_count += 1
            logger.exception(
                "recompute_region_day failed for bulletin=%s region=%s day=%s"
                " — deleting the stale rating so the public API cannot serve it.",
                bulletin.bulletin_id,
                region.canonical_region_id,
                day,
            )
            # Invalidate the now-untrustworthy row for this (region, day) so a
            # stale (possibly lower) rating is never served. A guarded delete —
            # a failure here must not abort the remaining pairs.
            try:
                RegionDayRating.objects.filter(region=region, date=day).delete()
            except Exception:
                logger.exception(
                    "Failed to invalidate stale RegionDayRating for bulletin=%s"
                    " region=%s day=%s — a stale rating may remain public.",
                    bulletin.bulletin_id,
                    region.canonical_region_id,
                    day,
                )

    regions = {region.pk: region for region, _day in pairs.values()}
    _invalidate_season_calendars(regions.values())

    logger.debug(
        "apply_bulletin_day_ratings: bulletin=%s target_day=%s pairs=%d failed=%d",
        bulletin.bulletin_id,
        target,
        len(pairs),
        failed_count,
    )
    return failed_count


# The outcomes apply_day_rating_item reports beyond created/updated/unchanged.
DAY_RATING_RECOMPUTED = "recomputed"
DAY_RATING_SKIPPED = "skipped"


def apply_day_rating_item(item: dict[str, Any], *, commit: bool = True) -> str:
    """Copy one of the service's day ratings into ``RegionDayRating``.

    The client half of the service's day ratings (SNOW-1270). The item is
    copied when it was computed under this code's ``DAY_RATING_VERSION`` and
    its governing bulletin is held here; otherwise the pair is recomputed
    from this instance's own bulletins — a branch testing a rule change, or
    a rating that arrived before its bulletin did. A region this instance's
    fixtures do not hold is skipped.

    Args:
        item: A ``day-ratings`` feed item (``serialise_source_day_rating``).
        commit: Write; otherwise only decide.

    Returns:
        ``created``, ``updated``, ``unchanged``, ``recomputed`` or ``skipped``.

    """
    from apps.bulletins.models import RegionDayRating

    region = (
        MicroRegion.objects.filter(region_id=item["region_code"])
        .only(*_SLIM_REGION_FIELDS)
        .first()
    )
    if region is None:
        logger.debug("Day rating for unknown region %s skipped", item["region_code"])
        return DAY_RATING_SKIPPED
    day = date.fromisoformat(item["date"])

    bulletin: Bulletin | None = None
    if item["bulletin_id"]:
        bulletin = (
            Bulletin.objects.filter(bulletin_id=item["bulletin_id"]).only("pk").first()
        )
    if item["version"] != DAY_RATING_VERSION or (item["bulletin_id"] and not bulletin):
        if commit:
            recompute_region_day(region, day)
            _invalidate_season_calendars([region])
        return DAY_RATING_RECOMPUTED

    values: dict[str, Any] = {
        name: item[name]
        for name in DAY_RATING_FIELDS
        if name not in ("bulletin_id", "version")
    }
    values["version"] = DAY_RATING_VERSION
    values["source_bulletin_id"] = bulletin.pk if bulletin else None
    existing = RegionDayRating.objects.filter(region=region, date=day).first()
    if existing is not None and all(
        getattr(existing, name) == value for name, value in values.items()
    ):
        return OUTCOME_UNCHANGED
    if commit:
        RegionDayRating.objects.update_or_create(
            region=region, date=day, defaults=values
        )
        _invalidate_season_calendars([region])
    return OUTCOME_CREATED if existing is None else OUTCOME_UPDATED
