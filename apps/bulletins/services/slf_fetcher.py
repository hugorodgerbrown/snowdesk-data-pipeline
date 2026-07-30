"""
apps/bulletins/services/slf_fetcher.py — Fetching and persisting SLF bulletins.

Contains pure-ish functions that:
  1. Fetch a page of bulletins from the SLF CAAML list API (fetch_bulletin_page).
  2. Persist a single bulletin into the database (upsert_bulletin).
  3. Orchestrate a full pipeline run across a date range (run_slf_pipeline).

Also defines the ``BulletinSource`` registry used by the unified
``fetch_bulletins`` management command. The registry maps provider names
(``"SLF"``, ``"ALBINA"``, ``"METEOFRANCE"``) to their pipeline function,
latest-date function, settings keys, and archive-writer adapter so the
command can iterate over requested sources without owning any
provider-specific logic.

Note: ``upsert_bulletin``, ``UnknownRegionError``, and ``_get_region`` are
shared helpers also imported by ``albina_fetcher`` and ``meteofrance_fetcher``.
Extracting them into a dedicated ``persistence.py`` module is a separate
cleanup ticket (not in scope here).

The SLF API returns bulletins in reverse chronological order and is
paginated by offset/limit — it does not support filtering by date. The
pipeline pages through results, skipping bulletins newer than the end date
and stopping once it passes the start date boundary.

Keeping these as functions rather than a class makes them easy to test and
compose. The management commands call run_slf_pipeline(); unit tests can call
fetch_bulletin_page() and upsert_bulletin() independently.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from django.conf import settings
from django.db import transaction

from apps.bulletins.models import Bulletin, PipelineRun, RegionBulletin
from apps.bulletins.services.day_rating import (
    apply_bulletin_day_ratings,
    target_day_for_valid_from,
)
from apps.bulletins.services.fetcher_common import (
    OUTCOME_CREATED,
    OUTCOME_FAILED,
    OUTCOME_UPDATED,
    normalise_bulletin_response,
)
from apps.bulletins.services.grouping import compute_bulletin_grouping_boundary
from apps.bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    build_render_model,
)
from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)

LANG = "en"
PAGE_SIZE = 50
REQUEST_TIMEOUT = 30  # seconds
_ONE_DAY = timedelta(days=1)

_SLF_PDF_BASE = "https://www.slf.ch/fileadmin/avalanche_bulletin/pdf"


def _slf_pdf_url(raw: dict[str, Any]) -> str:
    """
    Derive the SLF archive PDF URL from a raw bulletin dict.

    The PDF URL encodes the issue date, issue time (``08-00`` for morning
    updates, ``17-00`` for afternoon issues), and language.  The hour
    boundary is based on UTC: SLF publishes the morning update at
    approximately 07:00 UTC (08:00 CET) and the afternoon issue at
    approximately 16:00 UTC (17:00 CET), so UTC hour < 12 → ``08-00``,
    UTC hour ≥ 12 → ``17-00``.

    Args:
        raw: A single bulletin dict from the SLF CAAML API.  Must contain
            a parseable ``publicationTime`` (or fall back via
            ``_resolve_issued_at``).

    Returns:
        The full public URL to the PDF, e.g.
        ``https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/2026/03/Bulletin_2026-03-15_17-00_en.pdf``.

    """
    issued_at = _resolve_issued_at(raw)
    issue_time = "08-00" if issued_at.hour < 12 else "17-00"
    lang = raw.get("lang", LANG)
    issue_date = issued_at.date()
    return (
        f"{_SLF_PDF_BASE}/{issue_date:%Y}/{issue_date:%m}/"
        f"Bulletin_{issue_date:%Y-%m-%d}_{issue_time}_{lang}.pdf"
    )


def fetch_bulletin_page(
    lang: str,
    limit: int,
    offset: int,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch a single page of bulletins from the SLF CAAML list API.

    Args:
        lang: Language code ("en", "de", "fr", "it").
        limit: Maximum number of bulletins to return.
        offset: Number of bulletins to skip (for pagination).
        base_url: Override for the API base URL. Falls back to
            ``settings.SLF_API_BASE_URL`` when ``None`` so the
            ``fetch_bulletins`` command can flip between the live API
            and a local mirror without environment-variable gymnastics.

    Returns:
        A list of raw bulletin dicts as returned by the API.

    Raises:
        requests.HTTPError: If the API returns a non-2xx status.
        ValueError: If the response body cannot be parsed as JSON.

    """
    resolved_base = base_url if base_url is not None else settings.SLF_API_BASE_URL
    url = f"{resolved_base}/{lang}/json"
    logger.debug(
        "Fetching SLF bulletins: lang=%s limit=%d offset=%d base=%s",
        lang,
        limit,
        offset,
        resolved_base,
    )

    response = requests.get(
        url,
        params={"limit": limit, "offset": offset},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    data: Any = response.json()
    return _normalise_response(data)


def _normalise_response(data: Any) -> list[dict[str, Any]]:
    """
    Normalise the SLF API response into a flat list of bulletin dicts.

    The API may return:
      - A flat list of bulletins.
      - A single collection object with a "bulletins" key.
      - A list of collection objects, each with a "bulletins" key.

    Delegates to ``normalise_bulletin_response`` from ``fetcher_common``
    with the ``"SLF"`` source label.

    Args:
        data: The parsed JSON response from the SLF API.

    Returns:
        A flat list of bulletin dicts.

    """
    return normalise_bulletin_response(data, "SLF")


def _parse_dt(value: str) -> datetime:
    """
    Parse an ISO-8601 datetime string into a UTC-aware datetime.

    Aware inputs (Z-suffixed or with an explicit offset) are converted to
    UTC. Naive inputs are assumed to be UTC, since the CAAML schema
    requires timestamps to be expressed in UTC or with timezone info.

    Args:
        value: An ISO-8601 formatted datetime string.

    Returns:
        A UTC-aware datetime object.

    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _resolve_issued_at(raw: dict[str, Any]) -> datetime:
    """
    Resolve a bulletin's publication timestamp from the raw payload.

    Pre-2024 SLF bulletins omit the top-level ``publicationTime`` field
    that modern bulletins carry. When it is absent, fall back to
    ``validTime.startTime`` — for SLF the two values are typically
    identical (or differ by at most a couple of hours), so this is a
    safe proxy for both the pagination boundary check and the
    ``Bulletin.issued_at`` column.

    Args:
        raw: A single bulletin dict from the SLF CAAML API.

    Returns:
        A UTC-aware datetime suitable for use as ``issued_at``.

    """
    pub_time = raw.get("publicationTime")
    if pub_time:
        return _parse_dt(pub_time)
    return _parse_dt(raw["validTime"]["startTime"])


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


def upsert_bulletin(raw: dict[str, Any], run: PipelineRun, pdf_url: str = "") -> bool:
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
    raw_regions: list[dict[str, str]] = raw.get("regions", [])

    # Build render model from the raw properties.
    try:
        computed_render_model = build_render_model(raw)
        computed_render_model_version = RENDER_MODEL_VERSION
    except RenderModelBuildError as exc:
        logger.exception(
            "Failed to build render model for bulletin %s: %s",
            bulletin_id,
            exc,
        )
        computed_render_model = {
            "version": 0,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        }
        computed_render_model_version = 0
        run.records_failed += 1
        run.save(update_fields=["records_failed"])

    valid_from = _parse_dt(raw["validTime"]["startTime"])

    defaults: dict[str, Any] = {
        "raw_data": raw_data,
        "render_model": computed_render_model,
        "render_model_version": computed_render_model_version,
        "issued_at": _resolve_issued_at(raw),
        "valid_from": valid_from,
        "valid_to": _parse_dt(raw["validTime"]["endTime"]),
        "target_date": target_day_for_valid_from(valid_from),
        "next_update": _parse_dt(next_update_raw) if next_update_raw else None,
        "lang": raw.get("lang", LANG),
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
    # existing links, so prior state survives untouched.
    _assert_any_region_resolved(
        bulletin_id, raw_regions, resolved_regions, skipped_regions
    )

    # Replace the bulletin and its region links in one transaction so a
    # failure can never commit a bulletin with a half-written link set — the
    # bulletin and its associations move between consistent versions atomically.
    with transaction.atomic():
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
    # many regions failed; we add those to records_failed so the pipeline run
    # is marked failed and cron/CI surface it (SNOW-461). The broad except is a
    # backstop for an unexpected wholesale failure (e.g. the region query) —
    # count it as one failure so the run still fails.
    try:
        rating_failures = apply_bulletin_day_ratings(bulletin)
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
    # a geometry error never aborts ingest.  The grouping is a denormalisation;
    # the authoritative data lives in the RegionBulletin rows.
    try:
        compute_bulletin_grouping_boundary(bulletin)
    except Exception:
        logger.exception(
            "compute_bulletin_grouping_boundary failed for bulletin %s"
            " — ingest continues",
            bulletin_id,
        )

    return created


# Per-bulletin processing outcomes returned by ``_process_bulletin``.
# The generic OUTCOME_* constants are imported from fetcher_common; the
# SLF pipeline also uses internal pagination-control variants that have
# no equivalent in other providers.
_OUTCOME_SKIPPED_EXISTS = "skipped_exists"
_OUTCOME_SKIPPED_NEWER = "skipped_newer"
_OUTCOME_OUT_OF_RANGE = "out_of_range"


def _process_bulletin(
    raw: dict[str, Any],
    run: PipelineRun,
    *,
    range_start: datetime,
    range_end: datetime,
    dry_run: bool,
    force: bool,
) -> str:
    """
    Decide how to handle a single bulletin within the paging loop.

    Returns one of the ``_OUTCOME_*`` constants so the caller can update
    counters or terminate pagination without owning the decision logic.
    """
    issued_at = _resolve_issued_at(raw)

    if issued_at >= range_end:
        return _OUTCOME_SKIPPED_NEWER
    if issued_at < range_start:
        logger.info("Passed start boundary at %s, stopping", issued_at.isoformat())
        return _OUTCOME_OUT_OF_RANGE

    if dry_run:
        logger.info("[dry-run] Would store %s", raw["bulletinID"])
        return OUTCOME_CREATED

    if not force and Bulletin.objects.filter(bulletin_id=raw["bulletinID"]).exists():
        return _OUTCOME_SKIPPED_EXISTS

    # A bulletin whose regions are all unresolvable fails that bulletin, not
    # the batch (SNOW-547) — the same posture RenderModelBuildError takes
    # inside upsert_bulletin. records_failed makes fetch_bulletins exit
    # non-zero per the management-command contract, so cron/CI surface it.
    try:
        created = upsert_bulletin(raw, run, pdf_url=_slf_pdf_url(raw))
    except NoResolvableRegionsError:
        logger.exception("Skipping bulletin %s", raw["bulletinID"])
        run.records_failed += 1
        run.save(update_fields=["records_failed"])
        return OUTCOME_FAILED

    return OUTCOME_CREATED if created else OUTCOME_UPDATED


def _process_page(
    page: list[dict[str, Any]],
    run: PipelineRun,
    counts: dict[str, int],
    *,
    range_start: datetime,
    range_end: datetime,
    dry_run: bool,
    force: bool,
    on_fetched: "Callable[[dict[str, Any]], None] | None",
) -> bool:
    """
    Walk a page of bulletins, mutating counts; return True to stop paging.

    Pulled out of ``run_slf_pipeline`` so the orchestration loop stays
    under the cyclomatic-complexity limit. The return value collapses
    the two pagination-termination signals into one: an out-of-range
    bulletin (``_OUTCOME_OUT_OF_RANGE``) tells the caller to stop.
    """
    for raw in page:
        if on_fetched is not None:
            on_fetched(raw)
        try:
            outcome = _process_bulletin(
                raw,
                run,
                range_start=range_start,
                range_end=range_end,
                dry_run=dry_run,
                force=force,
            )
        except KeyError:
            logger.exception(
                "Error parsing bulletin data:\n%s", json.dumps(raw, indent=4)
            )
            raise
        if outcome == _OUTCOME_OUT_OF_RANGE:
            return True
        counts[outcome] += 1
    return False


def run_slf_pipeline(
    start: date,
    end: date,
    triggered_by: str = "unknown",
    dry_run: bool = False,
    force: bool = False,
    base_url: str | None = None,
    on_fetched: Callable[[dict[str, Any]], None] | None = None,
    delay: float = 0.0,
) -> PipelineRun:
    """
    Orchestrate a full SLF pipeline run over a date range.

    Pages through the SLF CAAML API in reverse chronological order.
    Bulletins newer than ``end`` are skipped; once a bulletin older than
    ``start`` is encountered, pagination stops.

    Args:
        start: First date to include (inclusive).
        end: Last date to include (inclusive).
        triggered_by: Human-readable label for who/what triggered the run.
        dry_run: If True, fetch data but do not write to the database.
        force: If True, upsert existing bulletins instead of skipping them.
        base_url: Override for the SLF API base URL. ``None`` defers to
            ``settings.SLF_API_BASE_URL``. ``fetch_bulletins --source
            local-mirror`` passes the development mirror URL here.
        on_fetched: Optional per-record callback invoked once for every
            raw bulletin returned by the fetcher, *before* in-range /
            dry-run / dedup decisions are made. The ``--stash`` flag
            wires this to a list collector so the on-disk archive
            captures everything the fetcher saw — independent of the
            date window or whether the bulletin was already in the DB.
        delay: Seconds to sleep between successive page fetches. ``0.0``
            (default) is a no-op; positive values pace the API to avoid
            hammering the SLF server during multi-year backfills. The
            sleep happens only between pages, never before the first
            request or after the last.

    Returns:
        The completed (or failed) PipelineRun instance.

    """
    run = PipelineRun.objects.create(triggered_by=triggered_by)
    run.mark_running()

    # Convert date boundaries to aware datetimes for comparison.
    range_start = datetime(start.year, start.month, start.day, tzinfo=UTC)
    range_end = datetime(end.year, end.month, end.day, tzinfo=UTC) + _ONE_DAY

    counts: dict[str, int] = {
        OUTCOME_CREATED: 0,
        OUTCOME_UPDATED: 0,
        _OUTCOME_SKIPPED_EXISTS: 0,
        _OUTCOME_SKIPPED_NEWER: 0,
        OUTCOME_FAILED: 0,
    }
    offset = 0
    pages_fetched = 0

    try:
        logger.info(
            "Pipeline run %s: range %s–%s force=%s dry_run=%s delay=%s",
            run.pk,
            start,
            end,
            force,
            dry_run,
            delay,
        )

        while True:
            page = fetch_bulletin_page(LANG, PAGE_SIZE, offset, base_url=base_url)
            pages_fetched += 1

            if not page:
                break

            stop = _process_page(
                page,
                run,
                counts,
                range_start=range_start,
                range_end=range_end,
                dry_run=dry_run,
                force=force,
                on_fetched=on_fetched,
            )

            # Stop on either an out-of-range bulletin (``stop`` is True)
            # or the upstream's "fewer than ``limit``" last-page signal.
            if stop or len(page) < PAGE_SIZE:
                break

            offset += PAGE_SIZE

            if delay > 0:
                time.sleep(delay)

    except Exception as exc:
        run.mark_failed(exc)
        return run

    logger.info(
        "Pipeline run %s finished: %d pages, %d created, %d updated, "
        "%d skipped, %d failed",
        run.pk,
        pages_fetched,
        counts[OUTCOME_CREATED],
        counts[OUTCOME_UPDATED],
        counts[_OUTCOME_SKIPPED_EXISTS],
        counts[OUTCOME_FAILED],
    )

    if dry_run:
        run.mark_success(0, 0)
    else:
        run.mark_success(counts[OUTCOME_CREATED], counts[OUTCOME_UPDATED])

    return run


# ---------------------------------------------------------------------------
# Source registry — used by the unified fetch_bulletins management command.
# ---------------------------------------------------------------------------

SOURCE_SLF = "SLF"
SOURCE_ALBINA = "ALBINA"
SOURCE_METEOFRANCE = "METEOFRANCE"
SOURCE_CHOICES = (SOURCE_SLF, SOURCE_ALBINA, SOURCE_METEOFRANCE)


def latest_slf_date() -> date | None:
    """
    Return the most recent ``valid_from`` date of any SLF bulletin in the DB.

    Used by the management command to derive the default ``--start-date``
    (resume from where the last run left off, with a one-day same-day
    overlap so morning updates / prior-evening re-issues are re-fetched).
    Returns ``None`` when no bulletin has been stored yet, which causes the
    command to fall back to ``settings.SEASON_START_DATE``.

    Returns:
        The latest ``valid_from.date()`` across all Bulletin rows, or
        ``None`` when the table is empty.

    """
    return Bulletin.objects.latest_valid_from_date()


def slf_stash_writer(records: list[dict[str, Any]], path: Path) -> int:
    """
    Merge ``records`` into the SLF on-disk archive and return the new size.

    Wraps ``apps.bulletins.services.slf_archive.{merge, read_archive, write_archive}``
    to match the uniform ``(records, path) -> int`` stash-writer signature
    required by ``BulletinSource``.

    The merge is atomic (write to a ``.tmp`` sibling then ``os.replace``).

    Args:
        records: Raw bulletin dicts collected by the ``--stash`` callback
            during a pipeline run.
        path: Filesystem path to the SLF archive NDJSON file.

    Returns:
        The total number of records in the archive after the merge.

    """
    from apps.bulletins.services.slf_archive import merge, read_archive, write_archive

    existing = list(read_archive(path))
    merged = merge(existing, records)
    write_archive(path, merged)
    return len(merged)


@dataclass(frozen=True)
class BulletinSource:
    """
    Registry entry describing a single bulletin provider.

    Each field tells the unified ``fetch_bulletins`` command how to
    interact with a specific provider without encoding any provider logic
    in the command itself.

    Attributes:
        name: Short provider name used in ``--source`` choices and log
            output (e.g. ``"SLF"``, ``"ALBINA"``, ``"METEOFRANCE"``).
        pipeline_fn: Callable with the signature
            ``(start, end, triggered_by, dry_run, force, base_url,
            on_fetched, delay) -> PipelineRun`` that runs the full ingest.
        latest_date_fn: Zero-argument callable that returns the most
            recent ``valid_from`` date stored in the DB for this provider,
            or ``None`` when the DB is empty. Used to derive the default
            ``--start-date``.
        live_url_setting: Attribute name on ``django.conf.settings`` that
            holds the provider's live API base URL (e.g.
            ``"SLF_API_BASE_URL"``).
        mirror_url_setting: Attribute name on ``django.conf.settings``
            that holds the dev-mirror URL (e.g.
            ``"SLF_API_LOCAL_MIRROR_URL"``). Expected to be absent or
            falsy in production.
        archive_path_setting: Attribute name on ``django.conf.settings``
            that holds the ``Path`` to the on-disk NDJSON archive (e.g.
            ``"SLF_ARCHIVE_PATH"``). Used when ``--stash`` is passed.
        stash_writer: Callable with signature
            ``(records: list[dict], path: Path) -> int`` that merges
            ``records`` into the on-disk archive and returns the new
            total record count.

    """

    name: str
    pipeline_fn: Callable[..., PipelineRun]
    latest_date_fn: Callable[[], date | None]
    live_url_setting: str
    mirror_url_setting: str
    archive_path_setting: str
    stash_writer: Callable[[list[dict[str, Any]], Path], int]


def get_sources() -> dict[str, BulletinSource]:
    """
    Return the bulletin-provider registry.

    Built on each call so the ALBINA imports (which themselves import
    from this module) are not executed at module load time — avoiding a
    circular import. Not cached on the module: tests patch
    ``run_slf_pipeline`` / ``run_albina_pipeline`` at the module level, and
    caching the resolved references would freeze the unpatched originals
    inside the registry. The rebuild cost is negligible — the command
    runs once per cron invocation.

    Returns:
        A dict mapping ``SOURCE_SLF`` / ``SOURCE_ALBINA`` /
        ``SOURCE_METEOFRANCE`` to their ``BulletinSource`` entries.

    """
    from apps.bulletins.services.albina_fetcher import (
        albina_stash_writer,
        latest_albina_date,
        run_albina_pipeline,
    )
    from apps.bulletins.services.meteofrance_fetcher import (
        latest_meteofrance_date,
        meteofrance_stash_writer,
        run_meteofrance_pipeline,
    )

    return {
        SOURCE_SLF: BulletinSource(
            name=SOURCE_SLF,
            pipeline_fn=run_slf_pipeline,
            latest_date_fn=latest_slf_date,
            live_url_setting="SLF_API_BASE_URL",
            mirror_url_setting="SLF_API_LOCAL_MIRROR_URL",
            archive_path_setting="SLF_ARCHIVE_PATH",
            stash_writer=slf_stash_writer,
        ),
        SOURCE_ALBINA: BulletinSource(
            name=SOURCE_ALBINA,
            pipeline_fn=run_albina_pipeline,
            latest_date_fn=latest_albina_date,
            live_url_setting="ALBINA_API_BASE_URL",
            mirror_url_setting="ALBINA_API_LOCAL_MIRROR_URL",
            archive_path_setting="ALBINA_ARCHIVE_PATH",
            stash_writer=albina_stash_writer,
        ),
        SOURCE_METEOFRANCE: BulletinSource(
            name=SOURCE_METEOFRANCE,
            pipeline_fn=run_meteofrance_pipeline,
            latest_date_fn=latest_meteofrance_date,
            live_url_setting="METEOFRANCE_API_BASE_URL",
            mirror_url_setting="METEOFRANCE_API_LOCAL_MIRROR_URL",
            archive_path_setting="METEOFRANCE_ARCHIVE_PATH",
            stash_writer=meteofrance_stash_writer,
        ),
    }
