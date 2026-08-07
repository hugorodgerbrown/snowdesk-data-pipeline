"""
apps/bulletins/services/golden_week.py — The "golden week" realistic bulletin corpus.

Selects seven consecutive real days from the committed provider archives under
``apps/bulletins/local_mirrors/`` and loads them through the production ingest path
(``upsert_bulletin``), so a dev or test database can be populated with a corpus
that behaves like production instead of like a factory (SNOW-528).

Why this exists
---------------
``seed_test_data`` builds a *navigable* dataset from factories: CH-only, one
date for the map, and a synthetic danger cycle. That is the right shape for
most tests, but it cannot answer any question about how bulletins behave
**across** days — whether the region partition shifts between issues, whether a
morning bulletin supersedes the previous evening's, whether a past date is
genuinely immutable. The nine payloads in ``tests/sentinels/`` are real CAAML
but are one graded example per structural case, drawn from dates months apart;
replaying them onto a shared date manufactures region overlaps that cannot
occur in production. That trap produced the false conclusion behind the
cancelled SNOW-527.

Nothing is *captured* here: the full 2025/26 season for all three providers is
already committed and git-tracked under ``apps/bulletins/local_mirrors/``. The
golden week is a **selection** from that data, resolved at seed time, so it
adds no bytes to the repo and needs neither network access nor a running dev
server (unlike the ``fetch_bulletins --local-mirror`` path, which replays the
same archives over HTTP and always ends its window at today).

Target-day resolution
---------------------
The three providers key their records differently, and getting this wrong
shifts a whole provider by a day:

* **SLF** — no explicit day field. A morning issue (~07:00 UTC) is valid
  ``D 07:00 → D 16:00`` and forecasts day ``D``; an evening issue (~16:00 UTC)
  is valid ``D 16:00 → D+1 16:00`` and forecasts day ``D+1``. Resolved with
  ``day_rating.target_day_for_valid_from`` — the same rule the day-rating
  service applies to the persisted row, so selection and rating agree by
  construction rather than by coincidence.
* **ALBINA** — carries the target day explicitly as
  ``customData.ALBINA.mainDate``. Its ``validTime.startTime`` is 16:00 on the
  *previous* day, so filtering on that would silently shift the provider back
  by one.
* **Météo-France** — carries ``customData.MF.date``, and its records are
  GeoJSON Feature envelopes rather than bare CAAML. It publishes one bulletin
  per massif with no ``bulletinID`` and no ``publicationTime``.

The chosen week
---------------
Mon 2026-02-09 → Sun 2026-02-15, picked by scoring every seven-day window from
December to mid-April on provider completeness, danger spread, and issue-time
pairing. It has all three providers on all seven days, all five danger levels,
a genuine mid-week swing (considerable → high → very_high on the Thursday and
Friday → back down), and SLF morning *and* evening issues on every day — so the
supersession invariant has something to bite on daily, not just once.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.conf import settings

from apps.bulletins.models import Bulletin
from apps.bulletins.services.day_rating import target_day_for_valid_from
from apps.bulletins.services.meteofrance_archive_loader import fixup_envelope
from apps.bulletins.services.slf_archive import read_archive

if TYPE_CHECKING:
    from apps.bulletins.models import PipelineRun

logger = logging.getLogger(__name__)

# The golden week: seven consecutive days, Monday to Sunday. See the module
# docstring for how it was chosen. Changing these changes what every caller of
# ``seed_test_week`` gets, so treat them as part of the command's contract.
GOLDEN_WEEK_START = datetime.date(2026, 2, 9)
GOLDEN_WEEK_LENGTH = 7

# Region fixtures the week's bulletins reference. The week spans Switzerland
# (SLF), Austria and South Tyrol (ALBINA) and the French massifs (Météo-France),
# where the factory dataset is CH-only.
REQUIRED_REGION_FIXTURES = ("eaws_CH", "eaws_AT", "eaws_IT", "eaws_FR")

# Provider keys. These name the archives and are surfaced in command output;
# SNOW-582: reference Bulletin.Source directly rather than redeclaring the
# same upper-case strings.
SOURCE_SLF = Bulletin.Source.SLF
SOURCE_ALBINA = Bulletin.Source.ALBINA
SOURCE_METEOFRANCE = Bulletin.Source.METEOFRANCE

SOURCES = (SOURCE_SLF, SOURCE_ALBINA, SOURCE_METEOFRANCE)


def golden_week_dates() -> list[datetime.date]:
    """
    Return the seven consecutive dates the golden week covers.

    Returns:
        The week's dates in ascending order, Monday first.

    """
    return [
        GOLDEN_WEEK_START + datetime.timedelta(days=offset)
        for offset in range(GOLDEN_WEEK_LENGTH)
    ]


def archive_path(source: str) -> Path:
    """
    Return the on-disk archive path for one provider.

    Args:
        source: One of the ``SOURCE_*`` constants.

    Returns:
        The configured NDJSON archive path for that provider.

    Raises:
        ValueError: If ``source`` is not a known provider key.

    """
    paths: dict[str, Path] = {
        SOURCE_SLF: settings.SLF_ARCHIVE_PATH,
        SOURCE_ALBINA: settings.ALBINA_ARCHIVE_PATH,
        SOURCE_METEOFRANCE: settings.METEOFRANCE_ARCHIVE_PATH,
    }
    try:
        return paths[source]
    except KeyError as exc:
        raise ValueError(f"Unknown bulletin source: {source!r}") from exc


def _parse_iso_datetime(value: str) -> datetime.datetime:
    """
    Parse a CAAML ISO-8601 timestamp into a timezone-aware datetime.

    The archives mix the ``Z`` suffix (SLF, ALBINA) with an explicit
    ``+00:00`` offset (Météo-France); both are normalised to UTC here. A
    timestamp without any offset is assumed UTC, matching how the fetchers
    treat provider payloads.

    Args:
        value: An ISO-8601 timestamp string.

    Returns:
        A timezone-aware datetime in UTC.

    """
    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC)


def _slf_target_day(raw: dict[str, Any]) -> datetime.date | None:
    """
    Return the day an SLF record forecasts, or ``None`` if undeterminable.

    SLF carries no explicit target-day field, so the day is derived from
    ``validTime.startTime`` using the production rule (morning issues forecast
    their own date, evening issues the next).

    Args:
        raw: A bare CAAML record from the SLF archive.

    Returns:
        The forecast date, or ``None`` when ``validTime.startTime`` is absent
        or unparseable.

    """
    start = (raw.get("validTime") or {}).get("startTime")
    if not start:
        return None
    try:
        return target_day_for_valid_from(_parse_iso_datetime(start))
    except ValueError:
        logger.warning("Golden week: unparseable SLF validTime.startTime %r", start)
        return None


def _custom_data_date(
    raw: dict[str, Any], namespace: str, key: str
) -> datetime.date | None:
    """
    Return a date read from a ``customData`` sub-key, or ``None`` if absent.

    Args:
        raw: A CAAML record (bare, or the ``properties`` of an envelope).
        namespace: The ``customData`` namespace, e.g. ``"ALBINA"``.
        key: The date field within that namespace, e.g. ``"mainDate"``.

    Returns:
        The parsed date, or ``None`` when the key is missing or malformed.

    """
    value = ((raw.get("customData") or {}).get(namespace) or {}).get(key)
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except TypeError, ValueError:
        logger.warning(
            "Golden week: unparseable customData.%s.%s %r", namespace, key, value
        )
        return None


def _iter_source_records(source: str) -> Iterator[dict[str, Any]]:
    """
    Yield the CAAML payloads for one provider's archive.

    Météo-France stores GeoJSON Feature envelopes, so its records are unwrapped
    to their ``properties`` and passed through ``fixup_envelope`` — which
    translates ``FR-{SLUG}`` region IDs to the canonical ``FR-{NN}`` form and
    synthesises the ``bulletinID`` that ``upsert_bulletin`` requires. Records
    whose massif slug is unknown are skipped, matching the archive loader.

    Args:
        source: One of the ``SOURCE_*`` constants.

    Yields:
        CAAML payload dicts ready for ``upsert_bulletin``.

    """
    for record in read_archive(archive_path(source)):
        if source != SOURCE_METEOFRANCE:
            yield record
            continue
        properties = record.get("properties")
        if not isinstance(properties, dict):
            logger.warning("Golden week: Météo-France record missing 'properties'")
            continue
        if fixup_envelope(properties) is None:
            # fixup_envelope has already logged the specific reason.
            continue
        yield properties


def target_day(source: str, raw: dict[str, Any]) -> datetime.date | None:
    """
    Return the day a record forecasts, using that provider's own day key.

    Args:
        source: One of the ``SOURCE_*`` constants.
        raw: The provider's CAAML payload (Météo-France already unwrapped).

    Returns:
        The forecast date, or ``None`` when it cannot be determined.

    Raises:
        ValueError: If ``source`` is not a known provider key.

    """
    if source == SOURCE_SLF:
        return _slf_target_day(raw)
    if source == SOURCE_ALBINA:
        return _custom_data_date(raw, "ALBINA", "mainDate")
    if source == SOURCE_METEOFRANCE:
        return _custom_data_date(raw, "MF", "date")
    raise ValueError(f"Unknown bulletin source: {source!r}")


def _meteofrance_publication_date(raw: dict[str, Any]) -> str:
    """
    Return the publication date encoded in a Météo-France record's source file.

    ``customData.MF.source_file`` looks like ``BRA.ARAVIS.2026-02-12.pdf``,
    where the date is the day the BRA was *published* (not the day it covers).
    Returned as the raw ``YYYY-MM-DD`` substring so it sorts lexicographically,
    with ``""`` for anything unparseable — which sorts first and so loses to any
    dated sibling.

    Args:
        raw: A Météo-France CAAML payload.

    Returns:
        The publication date as ``YYYY-MM-DD``, or ``""`` when unavailable.

    """
    source_file = ((raw.get("customData") or {}).get("MF") or {}).get("source_file")
    if not isinstance(source_file, str):
        return ""
    parts = source_file.split(".")
    for part in parts:
        try:
            datetime.date.fromisoformat(part)
        except ValueError:
            continue
        return part
    return ""


def _dedupe_meteofrance(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Collapse Météo-France records that share a synthesised ``bulletinID``.

    Météo-France publishes two BRAs for a given massif and day — a
    previous-evening issue and a morning one — but ``fixup_envelope``
    synthesises the ``bulletinID`` as ``FR-{NN}-{date}`` from the *covered*
    day alone, so both issues collide on one ID. Left alone, ``upsert_bulletin``
    would write one then overwrite it with the other, and which one survived
    would depend on the order of lines in the archive file.

    That is unacceptable for a fixture whose purpose is testing behaviour
    across issues, so the choice is made explicit here: keep the record with
    the latest publication date, i.e. the morning issue. This matches the
    morning-wins rule ``day_rating.target_day_for_valid_from`` applies to every
    other provider, so the golden week is consistent about which issue
    represents a day.

    The underlying ID-synthesis defect is tracked as SNOW-559 and not fixed
    here — narrowing the ID would change the identity of Météo-France bulletins
    already ingested by ``load_meteofrance_archive``, which is a migration, not
    a fixture change. Remove this function when SNOW-559 lands.

    Args:
        records: Météo-France payloads already filtered to the golden week.

    Returns:
        One record per ``bulletinID``, in first-seen order.

    """
    chosen: dict[str, dict[str, Any]] = {}
    dropped = 0
    for raw in records:
        bulletin_id = raw.get("bulletinID", "")
        incumbent = chosen.get(bulletin_id)
        if incumbent is None:
            chosen[bulletin_id] = raw
            continue
        dropped += 1
        if _meteofrance_publication_date(raw) > _meteofrance_publication_date(
            incumbent
        ):
            chosen[bulletin_id] = raw

    if dropped:
        logger.info(
            "Golden week: collapsed %d superseded Météo-France issue(s) sharing a "
            "synthesised bulletinID; kept the latest-published issue for each.",
            dropped,
        )
    return list(chosen.values())


def select_records(source: str) -> list[dict[str, Any]]:
    """
    Return one provider's archive records that fall inside the golden week.

    Args:
        source: One of the ``SOURCE_*`` constants.

    Returns:
        The matching CAAML payloads, in archive order. Météo-France records are
        additionally deduplicated by ``bulletinID`` (see ``_dedupe_meteofrance``).

    """
    week = set(golden_week_dates())
    selected = [
        raw for raw in _iter_source_records(source) if target_day(source, raw) in week
    ]
    if source == SOURCE_METEOFRANCE:
        selected = _dedupe_meteofrance(selected)
    logger.debug("Golden week: selected %d %s record(s)", len(selected), source)
    return selected


@dataclass
class GoldenWeekResult:
    """Summary counters from a golden-week load."""

    #: ``source -> number of archive records selected for the week``.
    selected: dict[str, int] = field(default_factory=dict)
    created: int = 0
    updated: int = 0
    failed: int = 0

    @property
    def total_selected(self) -> int:
        """Return the number of records selected across all providers."""
        return sum(self.selected.values())

    def as_summary(self) -> str:
        """
        Return a one-line human-readable summary for logs and command output.

        Returns:
            A short string reporting per-provider selection and load counters.

        """
        per_source = ", ".join(
            f"{source}={self.selected.get(source, 0)}" for source in SOURCES
        )
        return (
            f"{self.total_selected} record(s) selected ({per_source}); "
            f"{self.created} created, {self.updated} updated, {self.failed} failed."
        )


def load_golden_week(*, run: "PipelineRun | None", commit: bool) -> GoldenWeekResult:
    """
    Load the golden week from the committed archives into the database.

    Every record goes through the production ``upsert_bulletin``, so the loaded
    corpus gets its RegionBulletin links, RegionDayRating rows and
    BulletinGrouping boundaries from the same services the live ingest path
    uses — there is no parallel seeding logic to drift out of step.

    A record that fails to ingest is logged and counted, never fatal: one bad
    payload must not cost the other six days. Callers surface a non-zero
    ``failed`` count as a command failure.

    Args:
        run: The PipelineRun to attribute the loaded bulletins to. May be
            ``None`` only when ``commit`` is ``False``, since a dry run writes
            nothing at all — including no PipelineRun row.
        commit: When ``False``, records are selected and counted but nothing is
            written — the read-only default the command contract requires.

    Returns:
        A ``GoldenWeekResult`` with per-provider selection and load counters.

    Raises:
        ValueError: If ``commit`` is ``True`` without a ``run`` to attribute
            the loaded bulletins to.

    """
    from apps.bulletins.services.slf_fetcher import upsert_bulletin

    if commit and run is None:
        raise ValueError("load_golden_week(commit=True) requires a PipelineRun.")

    result = GoldenWeekResult()

    for source in SOURCES:
        records = select_records(source)
        result.selected[source] = len(records)

        if not commit or run is None:
            continue

        for raw in records:
            try:
                created = upsert_bulletin(raw, run)
            except Exception:
                logger.exception(
                    "Golden week: failed to ingest %s bulletin %s",
                    source,
                    raw.get("bulletinID"),
                )
                result.failed += 1
                continue
            if created:
                result.created += 1
            else:
                result.updated += 1

    # ``upsert_bulletin`` does not raise when a render model fails to build or a
    # day rating cannot be recomputed — it stores an error sentinel and bumps
    # ``run.records_failed`` instead. Those records never reach the except
    # branch above, so without folding the counter in here the command would
    # report success on a batch that is partially broken, which contract rule 4
    # exists to prevent. ``run`` is the same instance ``upsert_bulletin``
    # mutates, so the count is already current.
    if run is not None:
        result.failed += run.records_failed

    logger.info("Golden week load: %s", result.as_summary())
    return result
