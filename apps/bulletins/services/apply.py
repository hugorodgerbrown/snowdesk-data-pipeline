"""
apps/bulletins/services/apply.py — Write one provider bulletin into Snowdesk's tables.

The apply half of the fetch → normalise → apply split (SNOW-1249). Ingest
fetches and normalises a provider's bulletins (``apps.ingest.public``); this
module turns one CAAML v6 bulletin into a ``Bulletin`` row, its
``RegionBulletin`` links, its render model, its day ratings and its grouping
outline. Every path that writes a bulletin comes through here: the three
provider pipelines, the Météo-France archive loader, the golden week and
``pull_production_data``.

  upsert_bulletin(raw, run, pdf_url)   one raw CAAML dict → one row.
  apply_feed_item(item, run, …)        one ``bulletins`` feed item → one
                                       row, unless it is already here.
  apply_bulletin(record, run, force)   store an ingest ``NormalisedBulletin``
                                       in ``SourceBulletin``, then apply the
                                       stored row as a feed item (SNOW-1250).

Moved out of ``slf_fetcher.py`` unchanged, where it had lived since before
the other two providers shared it.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from django.db import transaction

from apps.bulletins.models import (
    Bulletin,
    BulletinGrouping,
    PipelineRun,
    RegionBulletin,
)
from apps.bulletins.services.day_rating import (
    apply_bulletin_day_ratings,
    apply_day_rating_item,
    day_rating_pairs,
    target_day_for_valid_from,
)
from apps.bulletins.services.grouping import compute_bulletin_grouping_boundary
from apps.bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    build_render_model,
    danger_levels_column,
    detect_source,
)
from apps.ingest.public import (
    OUTCOME_CREATED,
    OUTCOME_FAILED,
    OUTCOME_UNCHANGED,
    OUTCOME_UPDATED,
    SLF_DEFAULT_LANG,
    FetchOutcome,
    NormalisedBulletin,
    SourceBulletin,
    SourceDayRating,
    day_pairs,
    parse_dt,
    resolve_issued_at,
    serialise_source_bulletin,
    serialise_source_day_rating,
    store_bulletin,
)
from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)


class UnknownRegionError(LookupError):
    """Raised when an ingested bulletin references an unseeded region_id.

    Regions are fixture-backed reference data, not auto-created. If a
    CAAML bulletin arrives with a ``region_id`` that isn't in the
    ``pipeline_region`` table, we want that to fail loudly so a human
    can investigate (new EAWS region published? typo in the feed?) and
    update the fixture deliberately.
    """


class NoResolvableRegionsError(LookupError):
    """Raised when *every* region on a bulletin is unresolvable (SNOW-547).

    A bulletin that lists regions but resolves none of them would
    otherwise be ingested as a success while erasing all of its coverage:
    the update path deletes the existing ``RegionBulletin`` rows, creates
    no replacements, and reports created/updated with ``records_failed``
    untouched. That is indistinguishable from a legitimately
    empty-``regions`` bulletin, which is benign and must keep succeeding.

    The partial case — at least one region resolves — deliberately stays
    a WARNING and a success. Failing on partial resolution would turn
    every scheduled run red on routine fixture drift from a single stale
    region ID; only total coverage loss is unambiguous enough to fail on.
    """


def _get_region(region_id: str) -> MicroRegion:
    """
    Look up the MicroRegion for an ingested bulletin entry.

    Regions are fixture-backed; unseen identifiers raise
    ``UnknownRegionError`` rather than being silently auto-created.

    Args:
        region_id: SLF region identifier, e.g. "CH-4115".

    Returns:
        The matching MicroRegion instance.

    Raises:
        UnknownRegionError: The region_id does not correspond to any
            seeded MicroRegion row.

    """
    try:
        return MicroRegion.objects.get(region_id=region_id)
    except MicroRegion.DoesNotExist as exc:
        raise UnknownRegionError(
            f"Bulletin references unknown region_id={region_id!r} — "
            "add it to apps/regions/fixtures/eaws_CH.json (and rerun "
            "refresh_eaws_fixtures if the EAWS source has changed) before "
            "re-ingesting."
        ) from exc


def _resolve_bulletin_regions(
    raw_regions: list[dict[str, str]],
) -> tuple[list[tuple[MicroRegion, str]], list[str]]:
    """Resolve raw region entries to ``(region, name)`` pairs before any write.

    Returns the resolvable ``(MicroRegion, region_name_at_time)`` pairs plus
    the region_ids skipped because they are not in the fixtures. Raises
    ``KeyError`` if a region entry is missing ``regionID`` or ``name`` — this
    surfaces malformed provider data *before* the caller mutates the DB, so a
    bad entry can never leave a bulletin with a half-written link set
    (SNOW-460).

    Args:
        raw_regions: The ``regions`` list from a raw bulletin dict.

    Returns:
        ``(resolved, skipped)`` where ``resolved`` is a list of
        ``(MicroRegion, region_name_at_time)`` pairs and ``skipped`` is the
        list of unresolved (unknown) region_ids.

    """
    resolved: list[tuple[MicroRegion, str]] = []
    skipped: list[str] = []
    for raw_region in raw_regions:
        region_id = raw_region["regionID"]
        try:
            region = _get_region(region_id)
        except UnknownRegionError:
            skipped.append(region_id)
            continue
        resolved.append((region, raw_region["name"]))
    return resolved, skipped


def _assert_any_region_resolved(
    bulletin_id: str,
    raw_regions: list[dict[str, str]],
    resolved_regions: list[tuple[MicroRegion, str]],
    skipped_regions: list[str],
) -> None:
    """Raise if a bulletin listed regions but resolved none of them (SNOW-547).

    Called *before* the write transaction, so a total resolution failure
    leaves the bulletin's existing region links untouched instead of
    deleting them and creating no replacements — which reported success
    while silently erasing the bulletin's coverage from the map.

    A bulletin with no ``regions`` at all is a different, benign case and
    passes; so does partial resolution, which stays a WARNING (see
    ``NoResolvableRegionsError``).

    Args:
        bulletin_id: The bulletin being ingested, for the error message.
        raw_regions: The ``regions`` list from the raw bulletin dict.
        resolved_regions: Pairs returned by ``_resolve_bulletin_regions``.
        skipped_regions: Unresolved region_ids from the same call.

    Raises:
        NoResolvableRegionsError: ``raw_regions`` is non-empty and
            ``resolved_regions`` is empty.

    """
    if not raw_regions or resolved_regions:
        return
    raise NoResolvableRegionsError(
        f"Bulletin {bulletin_id}: none of the {len(raw_regions)} region(s) "
        f"resolved ({', '.join(sorted(skipped_regions))}). Ingesting would "
        "delete this bulletin's existing region links and replace them with "
        "nothing. Check apps/regions/fixtures/ against the upstream EAWS region "
        "scheme before re-ingesting."
    )


def _resolve_source(raw: dict[str, Any], bulletin_id: str) -> str:
    """
    Detect the provider a raw bulletin came from, degrading to blank.

    Provenance is detected from the raw payload rather than read back out of
    ``render_model["source"]``: it is an ingest fact, so it must survive a
    render-model build failure and must not be rewritten by
    ``rebuild_render_models``.

    Args:
        raw: The raw CAAML properties dict.
        bulletin_id: The bulletin identifier, for the warning log line.

    Returns:
        A ``Bulletin.Source`` value, or ``""`` when the payload carries no
        known source marker.

    """
    try:
        return detect_source(raw)
    except RenderModelBuildError:
        logger.warning(
            "No known source marker in customData for bulletin %s — "
            "leaving source blank",
            bulletin_id,
        )
        return ""


def _refresh_grouping(bulletin: Bulletin, run: PipelineRun) -> None:
    """
    Recompute ``bulletin``'s grouping outline without ever aborting ingest.

    The grouping is a denormalisation; the authoritative data lives in the
    RegionBulletin rows, so a geometry error is logged and ingest continues.

    SNOW-1228: a failed recompute must not leave the PREVIOUS outline live.
    A correction that moved a region out of the bulletin would otherwise
    keep drawing the old aggregation, which asserts the old region is still
    covered by it. So the stale row is deleted — no outline is honest, an
    old one is not — and the failure counts against the run, as a failed
    day rating does, so cron/CI see it. The delete has its own guard: a
    failing delete is logged but never aborts ingest either.

    Args:
        bulletin: The bulletin just upserted, its region links committed.
        run: The pipeline run whose ``records_failed`` a failure increments.

    """
    try:
        compute_bulletin_grouping_boundary(bulletin)
    except Exception:
        logger.exception(
            "compute_bulletin_grouping_boundary failed for bulletin %s"
            " — ingest continues",
            bulletin.bulletin_id,
        )
        try:
            BulletinGrouping.objects.filter(bulletin=bulletin).delete()
        except Exception:
            logger.exception(
                "could not delete the stale BulletinGrouping for bulletin %s",
                bulletin.bulletin_id,
            )
        run.records_failed += 1
        run.save(update_fields=["records_failed"])


def _render_model(
    raw: dict[str, Any],
    served: dict[str, Any] | None,
    run: PipelineRun,
) -> tuple[dict[str, Any], int]:
    """Return the render model to store: the service's, or one built here.

    The service builds every bulletin's render model and serves it with the
    CAAML (SNOW-1270). It is copied when it was built at this code's
    ``RENDER_MODEL_VERSION``; otherwise — an older service, a failed build
    there, a caller with no served model — it is built here, so an instance
    running a newer builder is never held to the service's output.

    Args:
        raw: The CAAML properties.
        served: The service's render model, if the caller has one.
        run: The run whose ``records_failed`` a failed local build bumps.

    Returns:
        ``(render_model, version)``.

    """
    if served is not None and served.get("version") == RENDER_MODEL_VERSION:
        return served, RENDER_MODEL_VERSION
    try:
        return build_render_model(raw), RENDER_MODEL_VERSION
    except RenderModelBuildError as exc:
        logger.exception(
            "Failed to build render model for bulletin %s: %s",
            raw.get("bulletinID"),
            exc,
        )
        run.records_failed += 1
        run.save(update_fields=["records_failed"])
        return {
            "version": 0,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        }, 0


def served_render_model_differs(item: dict[str, Any], existing: Bulletin) -> bool:
    """Say whether a feed item carries a current render model ``existing`` lacks.

    Args:
        item: A ``bulletins`` feed item.
        existing: The stored row, with ``render_model`` loaded.

    Returns:
        True when the item's model is at this code's version and differs
        from the stored one — a service rebuild after a builder change.

    """
    served = item.get("render_model")
    return (
        isinstance(served, dict)
        and served.get("version") == RENDER_MODEL_VERSION
        and served != existing.render_model
    )


def _fed_regions(
    raw_regions: list[dict[str, str]], region_codes: list[str] | None
) -> list[dict[str, str]]:
    """Return the CAAML region entries the bulletin is linked to.

    The service indexes every bulletin's region codes and serves them
    (SNOW-1341), so with ``region_codes`` the links are the codes it
    sent, not a re-reading of the CAAML. The name each link records
    (``region_name_at_time``) is only in the CAAML, so the entries keep
    the CAAML's order and names; without ``region_codes`` every entry is
    kept, as before.

    Args:
        raw_regions: The CAAML ``regions`` list.
        region_codes: The feed item's ``region_codes``, or None.

    Every CAAML entry is validated first, exactly as
    ``_resolve_bulletin_regions`` validates it, so a malformed entry raises
    before any write whether or not the item carries codes: the service's
    index silently skips an entry with no ``regionID``, and filtering on its
    codes would otherwise drop the malformed entry before the check saw it
    (Codex on #1361).

    Args:
        raw_regions: The CAAML ``regions`` list.
        region_codes: The feed item's ``region_codes``, or None.

    Returns:
        The entries to resolve, in CAAML order.

    Raises:
        KeyError: An entry lacks ``regionID`` or ``name``.

    """
    if region_codes is None:
        return raw_regions
    for raw_region in raw_regions:
        raw_region["regionID"]  # noqa: B018 — the same KeyError the resolver raises
        raw_region["name"]  # noqa: B018 — the same KeyError the resolver raises
    codes = set(region_codes)
    return [r for r in raw_regions if r["regionID"] in codes]


def upsert_bulletin(
    raw: dict[str, Any],
    run: PipelineRun,
    pdf_url: str = "",
    render_model: dict[str, Any] | None = None,
    *,
    valid_from: datetime | None = None,
    target_date: date | None = None,
    region_codes: list[str] | None = None,
) -> bool:
    """
    Create or update a single Bulletin from a raw API dict.

    Wraps the raw bulletin in a GeoJSON Feature envelope (matching the
    format expected by downstream consumers) before storing. Creates or
    looks up MicroRegion records and links them via RegionBulletin.

    Args:
        raw: A single bulletin dict from the SLF CAAML API.
        run: The PipelineRun to associate with this bulletin.
        pdf_url: Optional URL of the source bulletin PDF. Defaults to ``""``
            (empty) when not supplied — callers compute this via the
            per-source ``_*_pdf_url`` helpers and pass it through.
        render_model: The service's render model for this bulletin, copied
            when it is at this code's version (``_render_model``).
        valid_from: The service's indexed ``validTime.startTime``; read
            from the CAAML when None.
        target_date: The service's indexed forecast day; derived from
            ``valid_from`` when None.
        region_codes: The service's indexed region codes; every CAAML
            region is linked when None.

    Returns:
        True if a new row was created, False if an existing row was updated.

    """
    bulletin_id: str = raw["bulletinID"]
    raw_data: dict[str, Any] = {
        "type": "Feature",
        "geometry": None,
        "properties": raw,
    }

    next_update_raw: str | None = raw.get("nextUpdate")
    # The service indexed the regions, the start and the day (SNOW-1341):
    # a fed value is used as sent, and only an absent one is read here.
    caaml_regions: list[dict[str, str]] = raw.get("regions", [])
    raw_regions = _fed_regions(caaml_regions, region_codes)

    computed_render_model, computed_render_model_version = _render_model(
        raw, render_model, run
    )

    if valid_from is None:
        valid_from = parse_dt(raw["validTime"]["startTime"])
    if target_date is None:
        target_date = target_day_for_valid_from(valid_from)

    defaults: dict[str, Any] = {
        "raw_data": raw_data,
        "source": _resolve_source(raw, bulletin_id),
        "render_model": computed_render_model,
        "render_model_version": computed_render_model_version,
        "danger_levels": danger_levels_column(computed_render_model),
        "issued_at": resolve_issued_at(raw),
        "valid_from": valid_from,
        "valid_to": parse_dt(raw["validTime"]["endTime"]),
        "target_date": target_date,
        "next_update": parse_dt(next_update_raw) if next_update_raw else None,
        "lang": raw.get("lang", SLF_DEFAULT_LANG),
        "unscheduled": raw.get("unscheduled", False),
        "pipeline_run": run,
        "pdf_url": pdf_url,
    }

    # Resolve every region *before* touching the DB (SNOW-460): a malformed
    # entry raises here, before the delete-and-recreate below, so it can never
    # leave a bulletin with a truncated link set. Unknown-but-well-formed
    # regions are skipped as before (absent from the fixtures, not an error).
    resolved_regions, skipped_regions = _resolve_bulletin_regions(raw_regions)

    # Total resolution failure is a coverage-erasing event, not a skip
    # (SNOW-547) — raised here, before the transaction below deletes the
    # existing links, so prior state survives untouched. Judged against the
    # CAAML's own list: fed codes that keep none of its entries resolve
    # nothing, and must not pass as a bulletin with no regions.
    _assert_any_region_resolved(
        bulletin_id, caaml_regions, resolved_regions, skipped_regions
    )

    # Replace the bulletin and its region links in one transaction so a
    # failure can never commit a bulletin with a half-written link set — the
    # bulletin and its associations move between consistent versions atomically.
    with transaction.atomic():
        # Capture the (region, day) pairs the stored row covers *before* the
        # update rewrites its target day and the delete below drops its links:
        # afterwards they are unrecoverable, and a correction that narrows the
        # coverage would leave the dropped regions' ratings sourced from a
        # bulletin that no longer covers them (SNOW-1168). Slim read — only
        # what day_rating_pairs needs.
        existing = (
            Bulletin.objects.filter(bulletin_id=bulletin_id)
            .only("pk", "target_date", "valid_from")
            .first()
        )
        prior_pairs = day_rating_pairs([existing]) if existing else set()

        bulletin, created = Bulletin.objects.update_or_create(
            bulletin_id=bulletin_id,
            defaults=defaults,
        )

        # Clear existing links on update to stay in sync.
        if not created:
            RegionBulletin.objects.filter(bulletin=bulletin).delete()

        for region, region_name_at_time in resolved_regions:
            RegionBulletin.objects.create(
                bulletin=bulletin,
                region=region,
                region_name_at_time=region_name_at_time,
            )

    linked_count = len(resolved_regions)

    if skipped_regions:
        logger.warning(
            "Bulletin %s: %d/%d region(s) skipped — not in fixtures: %s",
            bulletin_id,
            len(skipped_regions),
            len(raw_regions),
            ", ".join(sorted(skipped_regions)),
        )

    action = "Created" if created else "Updated"
    logger.debug(
        "%s bulletin %s (issued %s, %d/%d regions linked)",
        action,
        bulletin_id,
        defaults["issued_at"],
        linked_count,
        len(raw_regions),
    )

    # Refresh day ratings. Day ratings are a denormalisation — the
    # authoritative data lives in Bulletin/RegionBulletin — so a rating failure
    # must not abort ingest. But a failed recompute would otherwise leave a
    # stale, possibly wrong (e.g. low-when-now-high) rating live, so
    # apply_bulletin_day_ratings invalidates the affected rows and reports how
    # many pairs failed; we add those to records_failed so the pipeline run
    # is marked failed and cron/CI surface it (SNOW-461). The prior pairs make
    # it reconcile what a correction dropped as well (SNOW-1168). The broad
    # except is a backstop for an unexpected wholesale failure (e.g. the
    # region query) — count it as one failure so the run still fails.
    try:
        rating_failures = apply_bulletin_day_ratings(bulletin, prior_pairs=prior_pairs)
    except Exception:
        logger.exception(
            "apply_bulletin_day_ratings failed for bulletin %s — ingest continues",
            bulletin_id,
        )
        rating_failures = 1
    if rating_failures:
        run.records_failed += rating_failures
        run.save(update_fields=["records_failed"])

    # Compute the dissolved grouping boundary — wrapped identically so that
    # a geometry error never aborts ingest.
    _refresh_grouping(bulletin, run)

    return created


def apply_feed_item(
    item: dict[str, Any],
    run: PipelineRun | None,
    *,
    existing: Bulletin | None,
    commit: bool = True,
    force: bool = False,
) -> str:
    """Write one ``bulletins`` feed item here, unless it is already here.

    The one apply step both ways a bulletin reaches this database: a
    production fetch applying what ingest just stored, and
    ``pull_production_data`` applying what production's feed sent (via
    ``apps.data_feed.services.apply.apply_bulletin``). Both hand it the same
    wire item, built by ``serialise_source_bulletin``.

    Unchanged when the stored CAAML and PDF link are identical, which is
    what makes a re-fetch or a re-pull of the same window a no-op:
    ``upsert_bulletin`` would otherwise rewrite the row and recompute its
    derived tables — unless the item's render model is current and differs
    from the stored one, which is the service having rebuilt it (SNOW-1270).
    The item's render model is handed to ``upsert_bulletin`` to copy, and
    its indexed ``valid_from``, ``target_date`` and ``region_codes`` to use
    as sent (SNOW-1341) — an item from a service that predates them carries
    none, and ``upsert_bulletin`` reads them from the CAAML instead.

    Args:
        item: A feed item, its ``caaml`` a dict.
        run: The ``PipelineRun`` to attribute a write to; required when
            ``commit``.
        existing: The ``Bulletin`` already stored under the item's id (with
            ``raw_data``, ``pdf_url`` and ``render_model`` loaded), or None.
        commit: Write; otherwise only decide.
        force: Write even when nothing has changed.

    Returns:
        ``OUTCOME_CREATED``, ``OUTCOME_UPDATED`` or ``OUTCOME_UNCHANGED``.

    """
    caaml = item["caaml"]
    pdf_url = item.get("pdf_url") or ""
    if existing is not None and not force:
        stored = (existing.raw_data or {}).get("properties")
        if (
            stored == caaml
            and existing.pdf_url == pdf_url
            and not served_render_model_differs(item, existing)
        ):
            return OUTCOME_UNCHANGED
    if commit:
        if run is None:
            raise ValueError("apply_feed_item needs a PipelineRun to commit")
        served = item.get("render_model")
        fed_valid_from = item.get("valid_from")
        fed_target_date = item.get("target_date")
        fed_codes = item.get("region_codes")
        upsert_bulletin(
            caaml,
            run,
            pdf_url=pdf_url,
            render_model=served if isinstance(served, dict) else None,
            valid_from=parse_dt(fed_valid_from) if fed_valid_from else None,
            target_date=date.fromisoformat(fed_target_date)
            if fed_target_date
            else None,
            region_codes=list(fed_codes) if isinstance(fed_codes, list) else None,
        )
    return OUTCOME_CREATED if existing is None else OUTCOME_UPDATED


def apply_bulletin(
    record: NormalisedBulletin, run: PipelineRun, *, force: bool = False
) -> str:
    """Store a fetched bulletin in ingest, then apply it here (SNOW-1250).

    Two steps, in this order. Ingest keeps what the provider published
    (``store_bulletin``, written only when its content hash changes); then
    the stored row, as the feed would serialise it, is applied to
    ``Bulletin`` by ``apply_feed_item`` — the same item, and the same apply
    step, that a staging pull uses. Production's ``Bulletin`` is therefore
    always derived from ingest's ``SourceBulletin``, never written around it.

    The apply step compares with what ``Bulletin`` holds rather than trusting
    the store's outcome, so a bulletin whose apply failed on one run (an
    unresolvable region, say) is retried on the next even though ingest
    already has it.

    Then the service's day ratings for every (region, day) the bulletin
    governed before or governs now are copied in
    (``copy_service_day_ratings``, SNOW-1270).

    Args:
        record: A bulletin from ``apps.ingest.public.fetch_bulletins``.
        run: The PipelineRun to associate with it.
        force: Re-resolve the PDF link and rewrite the bulletin even when
            nothing has changed.

    Returns:
        ``OUTCOME_CREATED``, ``OUTCOME_UPDATED`` or ``OUTCOME_UNCHANGED``.

    """
    prior = (
        SourceBulletin.objects.filter(bulletin_id=record.bulletin_id)
        .only("pk", "target_date", "region_codes")
        .first()
    )
    pairs = day_pairs(prior) if prior is not None else set()
    row, _ = store_bulletin(record, force=force)
    existing = (
        Bulletin.objects.filter(bulletin_id=row.bulletin_id)
        .only("pk", "bulletin_id", "raw_data", "pdf_url", "render_model")
        .first()
    )
    outcome = apply_feed_item(
        serialise_source_bulletin(row), run, existing=existing, force=force
    )
    copy_service_day_ratings(pairs | day_pairs(row))
    return outcome


def copy_service_day_ratings(pairs: set[tuple[str, date]]) -> None:
    """Copy the service's day ratings for ``pairs`` into ``RegionDayRating``.

    The production half of SNOW-1270: the service rated these pairs when it
    stored the bulletin, and its rating is the one every instance shows.
    ``upsert_bulletin`` has already rated them from this instance's own
    bulletins, by the same rules, so a copy normally changes nothing; when
    the two differ, the service's stands. A failure is logged and never
    aborts ingest, as a failed local rating does not.

    Args:
        pairs: (region code, day) pairs.

    """
    for rating in SourceDayRating.objects.filter(
        region_code__in={code for code, _ in pairs},
        date__in={day for _, day in pairs},
    ):
        if (rating.region_code, rating.date) not in pairs:
            continue
        try:
            apply_day_rating_item(serialise_source_day_rating(rating))
        except Exception:
            logger.exception("Could not copy the service's day rating %s", rating)


def count_outcome(
    outcome: FetchOutcome, run: PipelineRun, counts: dict[str, int]
) -> None:
    """Count something an ingest walk met that is not a bulletin to apply.

    A failed slot or massif is also a failed record on the run, so
    ``fetch_bulletins`` exits non-zero for it.

    Args:
        outcome: The walk's outcome.
        run: The active PipelineRun; its ``records_failed`` is saved.
        counts: The pipeline's outcome counters; updated.

    """
    counts[outcome.kind] = counts.get(outcome.kind, 0) + 1
    if outcome.kind == OUTCOME_FAILED:
        run.records_failed += 1
        run.save(update_fields=["records_failed"])
