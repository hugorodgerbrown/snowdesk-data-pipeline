"""
apps/ingest/runs.py — Walk a provider and yield what it published (SNOW-1249).

The fetch half of fetch → normalise → apply. Each provider has its own walk:
SLF pages backwards through a list API, ALBINA visits every (date, region)
slot on a CDN, and Météo-France fetches one XML document per massif. Each
walk is a generator, and ``fetch_bulletins`` picks one by provider. A walk
yields two kinds of item:

  NormalisedBulletin   a CAAML v6 bulletin in the window, ready to apply.
  FetchOutcome         something that is not a bulletin to apply — a slot
                       that failed, a bulletin outside the window, a
                       duplicate, a massif delegated to another body — so
                       the caller can count it the way it always has.

The walk never touches a database. Whether a bulletin already exists,
dry runs and the write itself are the caller's: ``run_slf_pipeline`` and its
two siblings in ``apps/bulletins/services/``. ``on_item`` sees each raw
bulletin at the point the provider's pipeline always exposed it to
``--stash``, and ``FetchStats`` counts the requests made, for the run's log
line.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests

from apps.ingest import conf
from apps.ingest.normalise.albina import albina_pdf_url
from apps.ingest.normalise.fetcher_common import OUTCOME_FAILED, OUTCOME_SKIPPED
from apps.ingest.normalise.meteofrance.translator import (
    MeteoFranceDelegatedRegionError,
    MeteoFranceTranslationError,
    parse_dpbra_xml,
)
from apps.ingest.normalise.slf import resolve_issued_at, slf_pdf_url
from apps.ingest.sources import (
    albina as albina_source,
    meteofrance as meteofrance_source,
    slf as slf_source,
)

logger = logging.getLogger(__name__)

PROVIDER_SLF = "SLF"
PROVIDER_ALBINA = "ALBINA"
PROVIDER_METEOFRANCE = "METEOFRANCE"

# Outcomes only some providers produce. The shared ones (skipped, failed)
# live in ``normalise/fetcher_common.py``.
OUTCOME_SKIPPED_NEWER = "skipped_newer"
OUTCOME_DUPLICATE = "duplicate"
OUTCOME_DELEGATED = "delegated"

_ONE_DAY = timedelta(days=1)

OnItem = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class NormalisedBulletin:
    """One provider bulletin, normalised to CAAML v6 and ready to apply.

    Attributes:
        bulletin_id: The provider's ``bulletinID``.
        provider: ``"SLF"``, ``"ALBINA"`` or ``"METEOFRANCE"``.
        caaml: The CAAML v6 bulletin dict.
        resolve_pdf_url: Returns the PDF the bulletin was published as. A
            callable rather than a value because Météo-France's costs a
            request to its archive, which a bulletin that is never written
            should not spend.

    """

    bulletin_id: str
    provider: str
    caaml: dict[str, Any]
    resolve_pdf_url: Callable[[], str]

    def pdf_url(self) -> str:
        """Return the bulletin's PDF URL, resolving it now."""
        return self.resolve_pdf_url()

    def to_string(self) -> str:
        """Return a short label for logs."""
        return f"{self.provider} {self.bulletin_id}"

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


@dataclass(frozen=True)
class FetchOutcome:
    """Something a walk met that is not a bulletin to apply.

    Attributes:
        kind: ``OUTCOME_FAILED`` (the caller counts it as a failed record),
            ``OUTCOME_SKIPPED``, ``OUTCOME_SKIPPED_NEWER``,
            ``OUTCOME_DUPLICATE`` or ``OUTCOME_DELEGATED``.

    """

    kind: str


@dataclass
class FetchStats:
    """What a walk spent, for the caller's log line.

    Attributes:
        requests: Requests made to the provider (pages, slots or massifs).

    """

    requests: int = 0


Item = NormalisedBulletin | FetchOutcome


def _window(start: date, end: date) -> tuple[datetime, datetime]:
    """Return the UTC instants bounding ``[start, end]``, end exclusive."""
    range_start = datetime(start.year, start.month, start.day, tzinfo=UTC)
    range_end = datetime(end.year, end.month, end.day, tzinfo=UTC) + _ONE_DAY
    return range_start, range_end


def _log_unparseable(raw: dict[str, Any]) -> None:
    """Log a bulletin that could not be read, in full, before re-raising."""
    logger.exception("Error parsing bulletin data:\n%s", json.dumps(raw, indent=4))


def _slf_item(
    raw: dict[str, Any], *, range_start: datetime, range_end: datetime
) -> Item | None:
    """Decide what one SLF bulletin from a page is.

    Args:
        raw: The raw bulletin.
        range_start: Window start, inclusive.
        range_end: Window end, exclusive.

    Returns:
        The bulletin, a skipped-newer outcome, or ``None`` once the walk has
        passed the start of the window and should stop.

    Raises:
        KeyError: The bulletin has no usable timestamp or id; its JSON is
            logged first.

    """
    try:
        issued_at = resolve_issued_at(raw)
        if issued_at >= range_end:
            return FetchOutcome(OUTCOME_SKIPPED_NEWER)
        if issued_at < range_start:
            logger.info("Passed start boundary at %s, stopping", issued_at.isoformat())
            return None
        bulletin_id: str = raw["bulletinID"]
    except KeyError:
        _log_unparseable(raw)
        raise
    return NormalisedBulletin(bulletin_id, PROVIDER_SLF, raw, lambda: slf_pdf_url(raw))


def iter_slf(
    start: date,
    end: date,
    *,
    base_url: str | None = None,
    delay: float = 0.0,
    on_item: OnItem | None = None,
    stats: FetchStats | None = None,
) -> Iterator[Item]:
    """Page backwards through SLF's list API, yielding the window's bulletins.

    The API is reverse-chronological and cannot filter by date, so the walk
    skips bulletins issued after ``end`` and stops at the first one issued
    before ``start``, or at a short page.

    Args:
        start: First date to include.
        end: Last date to include.
        base_url: Override for the API base URL (see
            ``sources.slf.resolve_base_url``).
        delay: Seconds to sleep between pages.
        on_item: Called with every raw bulletin on every page, before the
            window is applied.
        stats: Counts the pages fetched.

    Yields:
        ``NormalisedBulletin`` for each bulletin in the window, and
        ``FetchOutcome(OUTCOME_SKIPPED_NEWER)`` for each one after it.

    Raises:
        KeyError: A bulletin carries no usable timestamp; its JSON is
            logged first.

    """
    range_start, range_end = _window(start, end)
    stats = stats if stats is not None else FetchStats()
    offset = 0
    while True:
        page = slf_source.fetch_bulletin_page(
            slf_source.LANG, slf_source.PAGE_SIZE, offset, base_url=base_url
        )
        stats.requests += 1
        if not page:
            return
        for raw in page:
            if on_item is not None:
                on_item(raw)
            item = _slf_item(raw, range_start=range_start, range_end=range_end)
            if item is None:
                return
            yield item
        if len(page) < slf_source.PAGE_SIZE:
            return
        offset += slf_source.PAGE_SIZE
        if delay > 0:
            time.sleep(delay)


def iter_albina(
    start: date,
    end: date,
    *,
    regions: tuple[str, ...] | None = None,
    base_url: str | None = None,
    delay: float = 0.0,
    on_item: OnItem | None = None,
    stats: FetchStats | None = None,
) -> Iterator[Item]:
    """Visit every ALBINA (date, region) slot, yielding each bulletin once.

    A bulletin covering several regions is published in each region's
    file; it is yielded the first time and counted as a duplicate after.

    Args:
        start: First date to visit.
        end: Last date to visit.
        regions: ALBINA region codes; ``ALBINA_REGIONS`` when ``None``.
        base_url: Override for the CDN base URL.
        delay: Seconds to sleep between requests.
        on_item: Called once per unique bulletin, before the window is
            applied.
        stats: Counts the slots requested.

    Yields:
        ``NormalisedBulletin`` for each unique bulletin issued in the
        window; ``FetchOutcome`` for a failed slot, a bulletin with no id
        or outside the window (skipped), and a repeat (duplicate).

    """
    resolved_regions: tuple[str, ...] = (
        regions if regions is not None else conf.get("ALBINA_REGIONS", ())
    )
    range_start, range_end = _window(start, end)
    stats = stats if stats is not None else FetchStats()
    seen_ids: set[str] = set()
    current = start
    while current <= end:
        for region in resolved_regions:
            if delay > 0 and stats.requests > 0:
                time.sleep(delay)
            try:
                bulletins = albina_source.fetch_albina_for_date(
                    current, region, base_url=base_url
                )
            except requests.HTTPError as exc:
                logger.exception(
                    "HTTP error fetching %s / %s: %s — skipping slot",
                    current.isoformat(),
                    region,
                    exc,
                )
                stats.requests += 1
                yield FetchOutcome(OUTCOME_FAILED)
                continue
            stats.requests += 1
            for raw in bulletins:
                yield _albina_item(
                    raw,
                    region,
                    seen_ids,
                    range_start=range_start,
                    range_end=range_end,
                    on_item=on_item,
                )
        current += _ONE_DAY


def _albina_item(
    raw: dict[str, Any],
    region: str,
    seen_ids: set[str],
    *,
    range_start: datetime,
    range_end: datetime,
    on_item: OnItem | None,
) -> Item:
    """Decide what one ALBINA bulletin from one slot is.

    Args:
        raw: The raw bulletin.
        region: The slot's region code, which the PDF URL needs.
        seen_ids: Bulletin ids already yielded this walk; updated.
        range_start: Window start, inclusive.
        range_end: Window end, exclusive.
        on_item: The walk's per-bulletin callback.

    Returns:
        The bulletin, or the outcome that stands in for it.

    """
    bulletin_id: str = raw.get("bulletinID", "")
    if not bulletin_id:
        logger.warning("ALBINA bulletin with no bulletinID — skipping")
        return FetchOutcome(OUTCOME_SKIPPED)
    if bulletin_id in seen_ids:
        return FetchOutcome(OUTCOME_DUPLICATE)
    seen_ids.add(bulletin_id)
    if on_item is not None:
        on_item(raw)
    issued_at = albina_source.parse_issued_at(raw, fallback=range_start)
    if not (range_start <= issued_at < range_end):
        return FetchOutcome(OUTCOME_SKIPPED)
    return NormalisedBulletin(
        bulletin_id,
        PROVIDER_ALBINA,
        raw,
        lambda: albina_pdf_url(raw, region),
    )


def iter_meteofrance(
    *,
    massif_ids: tuple[int, ...] | None = None,
    base_url: str | None = None,
    delay: float = 0.0,
    on_item: OnItem | None = None,
    stats: FetchStats | None = None,
) -> Iterator[Item]:
    """Fetch and translate today's DPBRA bulletin for each massif.

    DPBRA is one issue per massif per day with no date-range API, so there
    is no window: the walk takes whatever each massif publishes now.

    Args:
        massif_ids: Massifs to fetch; ``METEOFRANCE_MASSIF_IDS`` when
            ``None``.
        base_url: Override for the API base URL; a ``file://`` URL reads a
            local mirror.
        delay: Seconds to sleep between massifs.
        on_item: Called with each translated bulletin.
        stats: Counts the massifs requested.

    Yields:
        ``NormalisedBulletin`` per translated bulletin; ``FetchOutcome``
        for a failed fetch or translation, a 404 (skipped) and a delegated
        massif.

    """
    resolved_ids: tuple[int, ...] = (
        massif_ids if massif_ids is not None else conf.get("METEOFRANCE_MASSIF_IDS", ())
    )
    resolved_base_url = meteofrance_source.resolve_base_url(base_url)
    api_key = conf.text("METEOFRANCE_API_KEY")
    stats = stats if stats is not None else FetchStats()
    # One shared session for PDF index lookups — connection reuse across massifs.
    session = meteofrance_source.pdf_session()
    for i, massif_id in enumerate(resolved_ids):
        if delay > 0 and i > 0:
            time.sleep(delay)
        yield _meteofrance_item(
            massif_id, resolved_base_url, api_key, session, on_item=on_item
        )
        stats.requests += 1


def _meteofrance_item(
    massif_id: int,
    base_url: str,
    api_key: str,
    session: requests.Session,
    *,
    on_item: OnItem | None,
) -> Item:
    """Fetch and translate one massif's bulletin.

    Args:
        massif_id: The Météo-France massif id.
        base_url: The resolved API or mirror base URL.
        api_key: The APIM key.
        session: The run's archive session, for the PDF lookup.
        on_item: The walk's per-bulletin callback.

    Returns:
        The bulletin, or the outcome that stands in for it.

    """
    try:
        xml_bytes = meteofrance_source.fetch_meteofrance_bulletin(
            massif_id, base_url, api_key
        )
    except (requests.HTTPError, RuntimeError) as exc:
        logger.exception("Error fetching MeteoFrance massif %d: %s", massif_id, exc)
        return FetchOutcome(OUTCOME_FAILED)
    if xml_bytes is None:
        logger.info("No bulletin for MeteoFrance massif %d today (404)", massif_id)
        return FetchOutcome(OUTCOME_SKIPPED)
    try:
        raw = parse_dpbra_xml(xml_bytes)
    except MeteoFranceDelegatedRegionError as exc:
        logger.info("MeteoFrance massif %d is delegated — skipping: %s", massif_id, exc)
        return FetchOutcome(OUTCOME_DELEGATED)
    except MeteoFranceTranslationError as exc:
        logger.exception(
            "Translation error for MeteoFrance massif %d: %s", massif_id, exc
        )
        return FetchOutcome(OUTCOME_FAILED)
    if on_item is not None:
        on_item(raw)
    return NormalisedBulletin(
        raw.get("bulletinID", ""),
        PROVIDER_METEOFRANCE,
        raw,
        lambda: meteofrance_source.bulletin_pdf_url(raw, session),
    )


def fetch_bulletins(
    provider: str,
    start: date,
    end: date,
    *,
    base_url: str | None = None,
    delay: float = 0.0,
    on_item: OnItem | None = None,
    stats: FetchStats | None = None,
    regions: tuple[str, ...] | None = None,
    massif_ids: tuple[int, ...] | None = None,
) -> Iterator[Item]:
    """Walk one provider over ``[start, end]``.

    Args:
        provider: ``"SLF"``, ``"ALBINA"`` or ``"METEOFRANCE"``.
        start: First date to include. Météo-France ignores the window.
        end: Last date to include.
        base_url: Override for the provider's base URL.
        delay: Seconds to sleep between requests.
        on_item: Called with each raw bulletin; see the provider's walk for
            exactly when.
        stats: Counts the requests made.
        regions: ALBINA only — region codes to visit.
        massif_ids: Météo-France only — massifs to fetch.

    Returns:
        The provider's walk, as a generator of bulletins and outcomes.

    Raises:
        ValueError: ``provider`` is not one of the three.

    """
    common: dict[str, Any] = {
        "base_url": base_url,
        "delay": delay,
        "on_item": on_item,
        "stats": stats,
    }
    if provider == PROVIDER_SLF:
        return iter_slf(start, end, **common)
    if provider == PROVIDER_ALBINA:
        return iter_albina(start, end, regions=regions, **common)
    if provider == PROVIDER_METEOFRANCE:
        return iter_meteofrance(massif_ids=massif_ids, **common)
    raise ValueError(f"Unknown provider {provider!r}")
