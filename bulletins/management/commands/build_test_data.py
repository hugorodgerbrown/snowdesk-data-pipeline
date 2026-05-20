"""
bulletins/management/commands/build_test_data.py — Generate test_data fixture.

Produces a single self-contained fixture that brings a freshly migrated DB
to a fully navigable state via one ``loaddata test_data`` invocation.

Coverage:
  - Every CH MicroRegion has a Bulletin + RegionBulletin + RegionDayRating on
    2026-04-08 (the "map reference date").
  - CH-4115 and CH-4222 additionally have bulletins for every day of April 2026
    (1st–30th).
  - WeatherSnapshot rows exist for every (region, date) pair covered by the
    bulletins.
  - ``Bulletin.render_model_version`` equals ``RENDER_MODEL_VERSION`` (4) so
    no rebuild step is needed after load.

The command follows the Option A convention from CLAUDE.md: read-only by
default; ``--commit`` is the only way to write the fixture file.

Implementation strategy:
  - Open a ``transaction.atomic`` block and write all bulletin/rating/weather
    rows into the live DB.
  - Serialise everything via Django's serialiser framework in FK-safe order.
  - Write the fixture file to ``bulletins/fixtures/test_data.json``.
  - Forcibly roll back the transaction so the live DB is untouched.
  - In dry-run mode (no ``--commit``) only print the counts, never write.
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
import zoneinfo
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from django.core import serializers
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone as django_timezone

from bulletins.services.day_rating import apply_bulletin_day_ratings
from bulletins.services.render_model import RENDER_MODEL_VERSION, build_render_model

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EAWS_CH_FIXTURE = _REPO_ROOT / "regions" / "fixtures" / "eaws_CH.json"
_RESORTS_FIXTURE = _REPO_ROOT / "regions" / "fixtures" / "resorts.json"
_OUTPUT_FIXTURE = _REPO_ROOT / "bulletins" / "fixtures" / "test_data.json"

# ---------------------------------------------------------------------------
# Map-reference date and detail-region constants
# ---------------------------------------------------------------------------

MAP_DATE = date(2026, 4, 8)
APRIL_DATES = [date(2026, 4, d) for d in range(1, 31)]
DETAIL_REGIONS = {"CH-4115": "Martigny-Verbier", "CH-4222": "Zermatt"}

# Danger ratings to cycle across April so the calendar shows a colour gradient.
# Indices into APRIL_DATES are mapped to these levels (wrapping).
_CYCLING_DANGER = [
    "low",
    "low",
    "moderate",
    "moderate",
    "moderate",
    "considerable",
    "considerable",
    "moderate",
    "moderate",
    "low",
]

# ---------------------------------------------------------------------------
# CAAML raw_data template  (templated from a known-valid CAAML payload shape;
# all fields are fully parameterised)
# ---------------------------------------------------------------------------


def _make_raw_data(
    bulletin_id: str,
    region_id: str,
    region_name: str,
    valid_from_iso: str,
    valid_to_iso: str,
    issued_at_iso: str,
    danger_value: str = "moderate",
) -> dict[str, Any]:
    """
    Build a minimal but valid CAAML GeoJSON Feature envelope for one bulletin.

    The payload includes enough fields (aggregation, dangerRatings,
    avalancheProblems, snowpackStructure, weatherForecast) for the render model
    builder to produce a non-error render_model at version 4.

    Args:
        bulletin_id: The unique bulletin identifier (UUID-style string).
        region_id: SLF region ID, e.g. ``"CH-4115"``.
        region_name: Human-readable region name, e.g. ``"Martigny-Verbier"``.
        valid_from_iso: ISO 8601 string for validTime.startTime.
        valid_to_iso: ISO 8601 string for validTime.endTime.
        issued_at_iso: ISO 8601 string for publicationTime.
        danger_value: CAAML danger level key, e.g. ``"moderate"``.

    Returns:
        A GeoJSON Feature dict suitable for storage in ``Bulletin.raw_data``.

    """
    return {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "lang": "en",
            "bulletinID": bulletin_id,
            "regions": [{"name": region_name, "regionID": region_id}],
            "validTime": {
                "startTime": valid_from_iso,
                "endTime": valid_to_iso,
            },
            "publicationTime": issued_at_iso,
            "nextUpdate": valid_to_iso,
            "unscheduled": False,
            "customData": {
                "CH": {
                    "aggregation": [
                        {
                            "category": "dry",
                            "problemTypes": ["persistent_weak_layers"],
                            "validTimePeriod": "all_day",
                        }
                    ]
                }
            },
            "dangerRatings": [
                {
                    "mainValue": danger_value,
                    "customData": {"CH": {"subdivision": ""}},
                    "validTimePeriod": "all_day",
                }
            ],
            "avalancheProblems": [
                {
                    "aspects": ["N", "NE", "E", "SE", "SW", "W", "NW"],
                    "comment": (
                        "Weak layers in the old snowpack represent the main danger."
                    ),
                    "elevation": {"lowerBound": "2200"},
                    "customData": {
                        "CH": {
                            "subdivision": "",
                            "coreZoneText": None,
                        }
                    },
                    "problemType": "persistent_weak_layers",
                    "validTimePeriod": "all_day",
                    "dangerRatingValue": danger_value,
                }
            ],
            "snowpackStructure": {
                "comment": (
                    "<p>Snowpack conditions are typical for the season. "
                    "Weak layers exist on shady slopes at high elevation.</p>"
                )
            },
            "weatherForecast": {
                "comment": (
                    "<p>Conditions will remain settled with light winds "
                    "and seasonal temperatures.</p>"
                )
            },
            "tendency": [
                {
                    "comment": (
                        "<p>Avalanche risk is expected to remain similar "
                        "over the coming days.</p>"
                    )
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# Bulletin row construction helpers
# ---------------------------------------------------------------------------


def _bulletin_id_for(region_id: str, target_date: date) -> str:
    """
    Generate a deterministic bulletin_id for a (region, date) pair.

    Uses a UUID-5 constructed from a fixed namespace so the same
    (region, date) always produces the same ID, making the fixture
    fully reproducible on repeated runs.

    Args:
        region_id: SLF region ID string.
        target_date: The calendar date for the bulletin.

    Returns:
        A UUID-style string unique to (region_id, target_date).

    """
    ns = uuid.UUID("a4020000-0000-0000-0000-000000000000")
    return str(uuid.uuid5(ns, f"{region_id}:{target_date.isoformat()}"))


def _danger_for_day(target_date: date) -> str:
    """
    Return a cycling danger level key for the given April date.

    Cycles through ``_CYCLING_DANGER`` using the day-of-month index (mod 10)
    so the calendar shows a colour gradient across the month.

    Args:
        target_date: The bulletin's target calendar date.

    Returns:
        A danger level key string, e.g. ``"moderate"``.

    """
    idx = (target_date.day - 1) % len(_CYCLING_DANGER)
    return _CYCLING_DANGER[idx]


def _make_bulletin_params(
    region_id: str,
    region_name: str,
    target_date: date,
    danger_value: str,
) -> dict[str, Any]:
    """
    Return kwargs for creating a Bulletin instance for one (region, date) pair.

    Morning issue: valid_from at 07:00 UTC on ``target_date``.

    Args:
        region_id: SLF region ID.
        region_name: Human-readable region name.
        target_date: The calendar date that this bulletin covers.
        danger_value: Danger level key to embed in the CAAML payload.

    Returns:
        A dict of Bulletin model field values.

    """
    issued_dt = datetime(
        target_date.year, target_date.month, target_date.day, 6, 50, tzinfo=UTC
    )
    valid_from_dt = datetime(
        target_date.year, target_date.month, target_date.day, 7, 0, tzinfo=UTC
    )
    valid_to_dt = datetime(
        target_date.year, target_date.month, target_date.day, 16, 0, tzinfo=UTC
    )
    bid = _bulletin_id_for(region_id, target_date)
    raw = _make_raw_data(
        bulletin_id=bid,
        region_id=region_id,
        region_name=region_name,
        valid_from_iso=valid_from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        valid_to_iso=valid_to_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        issued_at_iso=issued_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        danger_value=danger_value,
    )
    return {
        "bulletin_id": bid,
        "raw_data": raw,
        "render_model": {},
        "render_model_version": 0,
        "issued_at": issued_dt,
        "valid_from": valid_from_dt,
        "valid_to": valid_to_dt,
        "next_update": valid_to_dt,
        "lang": "en",
        "unscheduled": False,
        "pipeline_run": None,
    }


# ---------------------------------------------------------------------------
# WeatherSnapshot synthesis
# ---------------------------------------------------------------------------


def _make_weather_snapshot_params(
    region_id: str,
    target_date: date,
) -> dict[str, Any]:
    """
    Build kwargs for a deterministic WeatherSnapshot row.

    Uses WMO code 1 (mainly clear), sunrise 06:30 Europe/Zurich,
    sunset 18:30 Europe/Zurich for the given date.

    Args:
        region_id: SLF region ID (used to look up the MicroRegion FK).
        target_date: The calendar date the snapshot applies to.

    Returns:
        A dict of WeatherSnapshot model field values (excluding the region FK,
        which is resolved later from ``region_id``).

    """
    zurich = zoneinfo.ZoneInfo("Europe/Zurich")
    sunrise = datetime(
        target_date.year, target_date.month, target_date.day, 6, 30, tzinfo=zurich
    )
    sunset = datetime(
        target_date.year, target_date.month, target_date.day, 18, 30, tzinfo=zurich
    )
    return {
        "valid_for_date": target_date,
        "weather_code": 1,
        "sunrise": sunrise,
        "sunset": sunset,
        "fetched_at": django_timezone.now(),
    }


# ---------------------------------------------------------------------------
# Serialisation order
# ---------------------------------------------------------------------------

# FK-safe order for the final fixture.  Region and resort rows come verbatim
# from the existing eaws_CH / resorts fixtures (not serialised here — they are
# prepended from those fixture files at write time).  This list is kept as a
# reader aid so the full dependency chain is visible in one place.
_SERIALISE_MODELS: list[str] = [
    "regions.majorregion",
    "regions.subregion",
    "regions.microregion",
    # regions.microregionneighbour is omitted — this command never populates it.
    "regions.resort",
    "bulletins.bulletin",
    "bulletins.regionbulletin",
    "bulletins.regiondayrating",
    "bulletins.weathersnapshot",
]


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


class Command(BaseCommand):
    """
    Generate ``bulletins/fixtures/test_data.json``.

    Builds region/resort rows verbatim from the existing eaws_CH and resorts
    fixtures, then synthesises Bulletin + RegionBulletin + RegionDayRating +
    WeatherSnapshot rows for the map-coverage and detail layers. Calls the
    production render-model builder and day-rating service so the fixture is
    fully ready without any post-load rebuild step.

    Default mode: dry-run — prints counts, no file written.
    Pass ``--commit`` to write ``bulletins/fixtures/test_data.json``.
    """

    help = (
        "Generate bulletins/fixtures/test_data.json. "
        "Read-only by default; pass --commit to write the file."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Write the fixture file. Without this flag the command is read-only.",
        )
        parser.add_argument(
            "--output",
            default=None,
            help=(
                "Override the output path. Defaults to "
                "bulletins/fixtures/test_data.json."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        output_path = Path(options["output"]) if options["output"] else _OUTPUT_FIXTURE

        if not commit:
            self.stdout.write(
                self.style.WARNING(
                    "[READ-ONLY] Dry-run mode — no file will be written. "
                    "Pass --commit to persist."
                )
            )

        # Load region/resort fixture data verbatim (these go into the output
        # unchanged — we don't create DB rows for them here because the eaws_CH
        # and resorts fixtures already exist and may be loaded in the target DB).
        region_data = json.loads(_EAWS_CH_FIXTURE.read_text())
        resort_data = json.loads(_RESORTS_FIXTURE.read_text())

        # Build a lookup: region_id → region name.  Used when synthesising bulletins
        # so we don't have to query the DB (the regions aren't loaded into the test
        # DB in this code path).
        region_name_map: dict[str, str] = {
            d["fields"]["region_id"]: d["fields"]["name"]
            for d in region_data
            if d["model"] == "regions.microregion"
        }
        all_region_ids = list(region_name_map.keys())

        # ------------------------------------------------------------------
        # Synthesise bulletin rows inside a transaction that we roll back.
        # Writing to the DB is the only way to use the production render-model
        # builder (it expects saved Bulletin instances) and day-rating service.
        # The transaction.atomic + savepoint trick lets us roll back all DB writes
        # while the fixture data collected inside survives on the heap.
        # ------------------------------------------------------------------
        counts: dict[str, int] = {}
        fixture_json: str = ""

        try:
            with transaction.atomic():
                counts, fixture_json = self._build_inside_transaction(
                    region_name_map=region_name_map,
                    all_region_ids=all_region_ids,
                    verbosity=verbosity,
                )
                # Unconditionally roll back — the fixture JSON was captured above.
                raise _RollbackSentinel()
        except _RollbackSentinel:
            pass

        # Print summary.
        self._print_counts(counts, verbosity)

        if not commit:
            self.stdout.write(
                self.style.SUCCESS(
                    "Dry-run complete. Pass --commit to write the fixture."
                )
            )
            return

        # Assemble the final fixture: region data + resort data + bulletin data.
        # Region/resort rows come from the verbatim fixtures; bulletin rows come
        # from the serialised DB snapshot captured inside the rolled-back txn.
        bulletin_records = json.loads(fixture_json)
        all_records: list[Any] = region_data + resort_data + bulletin_records

        output_path.write_text(
            json.dumps(all_records, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self.stdout.write(
            self.style.SUCCESS(f"Wrote {len(all_records)} records to {output_path}")
        )
        logger.info(
            "build_test_data: wrote %d records to %s",
            len(all_records),
            output_path,
        )

    def _build_inside_transaction(  # noqa: C901 — linear steps; no meaningful simplification
        self,
        region_name_map: dict[str, str],
        all_region_ids: list[str],
        verbosity: int,
    ) -> tuple[dict[str, int], str]:
        """
        Create all bulletin/rating/weather rows and serialise them to JSON.

        This method runs inside a ``transaction.atomic()`` block that is
        unconditionally rolled back by the caller.  All DB writes are
        temporary; the only persistent artefact is the returned JSON string.

        Args:
            region_name_map: Mapping of region_id → name for all CH regions.
            all_region_ids: Ordered list of all CH micro-region IDs.
            verbosity: Verbosity level from command options.

        Returns:
            A ``(counts, fixture_json)`` tuple where ``counts`` is a dict of
            model label → record count and ``fixture_json`` is the serialised
            JSON string for the bulletin-side records.

        """
        from bulletins.models import (
            Bulletin,
            RegionBulletin,
            RegionDayRating,
            WeatherSnapshot,
        )
        from regions.models import MicroRegion

        # Load all MicroRegion rows from the DB (they must already be loaded
        # via their own fixtures before this command can run).  We need PKs for
        # FK wiring.
        micro_qs: dict[str, MicroRegion] = MicroRegion.objects.filter(
            region_id__in=all_region_ids
        ).in_bulk(field_name="region_id")

        all_bulletins, weather_pairs = self._create_bulletins(
            region_name_map, all_region_ids, verbosity
        )
        self._build_render_models(all_bulletins, verbosity)
        region_bulletins = self._create_region_bulletins(
            all_bulletins, micro_qs, verbosity
        )
        new_rdr_pks, rating_count = self._create_day_ratings(all_bulletins, verbosity)
        weather_snapshots = self._create_weather_snapshots(
            weather_pairs, micro_qs, verbosity
        )

        # Serialise only the records we just created (not pre-existing dev DB rows).
        new_bulletin_pks = [b.pk for b in all_bulletins]
        new_rb_pks = [rb.pk for rb in region_bulletins]
        new_ws_pks = [ws.pk for ws in weather_snapshots]
        objects_to_serialise = (
            list(Bulletin.objects.filter(pk__in=new_bulletin_pks))
            + list(RegionBulletin.objects.filter(pk__in=new_rb_pks))
            + list(RegionDayRating.objects.filter(pk__in=new_rdr_pks))
            + list(WeatherSnapshot.objects.filter(pk__in=new_ws_pks))
        )
        fixture_json = serializers.serialize(
            "json",
            objects_to_serialise,
            indent=2,
            use_natural_foreign_keys=True,
            use_natural_primary_keys=False,
        )

        counts = {
            "bulletins": len(all_bulletins),
            "region_bulletins": len(region_bulletins),
            "day_ratings": rating_count,
            "weather_snapshots": len(weather_snapshots),
        }
        return counts, fixture_json

    def _create_bulletins(
        self,
        region_name_map: dict[str, str],
        all_region_ids: list[str],
        verbosity: int,
    ) -> "tuple[list[Any], list[tuple[str, date]]]":
        """
        Create map-coverage and detail-layer Bulletin rows.

        Map-coverage: one per MicroRegion on MAP_DATE.
        Detail layer: one per day × {CH-4115, CH-4222} for full April 2026
        (MAP_DATE is skipped since it is already covered by map-coverage).

        Args:
            region_name_map: region_id → name lookup.
            all_region_ids: All CH micro-region IDs.
            verbosity: Verbosity level.

        Returns:
            A ``(all_bulletins, weather_pairs)`` tuple.

        """
        from bulletins.models import Bulletin

        weather_pairs: list[tuple[str, date]] = []

        map_bulletins: list[Bulletin] = []
        for region_id in all_region_ids:
            region_name = region_name_map[region_id]
            params = _make_bulletin_params(region_id, region_name, MAP_DATE, "moderate")
            b = Bulletin(**params)
            b.save()
            map_bulletins.append(b)
            weather_pairs.append((region_id, MAP_DATE))

        if verbosity >= 2:
            self.stdout.write(
                f"  Created {len(map_bulletins)} map-coverage bulletins for {MAP_DATE}"
            )

        detail_bulletins: list[Bulletin] = []
        for region_id, region_name in DETAIL_REGIONS.items():
            for target_date in APRIL_DATES:
                if target_date == MAP_DATE:
                    continue
                b = Bulletin(
                    **_make_bulletin_params(
                        region_id,
                        region_name,
                        target_date,
                        _danger_for_day(target_date),
                    )
                )
                b.save()
                detail_bulletins.append(b)
                weather_pairs.append((region_id, target_date))

        if verbosity >= 2:
            self.stdout.write(
                f"  Created {len(detail_bulletins)} detail bulletins for April 2026"
            )

        return map_bulletins + detail_bulletins, weather_pairs

    def _build_render_models(self, all_bulletins: "list[Any]", verbosity: int) -> None:
        """
        Build and save the render model for every bulletin in-place.

        Args:
            all_bulletins: List of saved Bulletin instances.
            verbosity: Verbosity level.

        """
        failed = 0
        for b in all_bulletins:
            properties = (b.raw_data or {}).get("properties", {})
            try:
                rm = build_render_model(properties)
                b.render_model = rm
                b.render_model_version = RENDER_MODEL_VERSION
            except Exception as exc:
                logger.warning(
                    "build_test_data: render model failed for %s: %s",
                    b.bulletin_id,
                    exc,
                )
                b.render_model = {"version": 0, "error": str(exc)}
                b.render_model_version = 0
                failed += 1
            b.save(update_fields=["render_model", "render_model_version"])

        if verbosity >= 2:
            self.stdout.write(
                f"  Built render models ({failed} failures out of {len(all_bulletins)})"
            )

    def _create_region_bulletins(
        self,
        all_bulletins: "list[Any]",
        micro_qs: "dict[str, Any]",
        verbosity: int,
    ) -> "list[Any]":
        """
        Create RegionBulletin through-table rows for every bulletin.

        Args:
            all_bulletins: List of saved Bulletin instances.
            micro_qs: region_id → MicroRegion lookup dict.
            verbosity: Verbosity level.

        Returns:
            List of saved RegionBulletin instances.

        """
        from bulletins.models import RegionBulletin

        region_bulletins: list[Any] = []
        for b in all_bulletins:
            properties = (b.raw_data or {}).get("properties", {})
            for region_entry in properties.get("regions", []):
                region_id = region_entry["regionID"]
                micro = micro_qs.get(region_id)
                if micro is None:
                    logger.warning(
                        "build_test_data: no MicroRegion for %s — skipping",
                        region_id,
                    )
                    continue
                rb = RegionBulletin(
                    bulletin=b,
                    region=micro,
                    region_name_at_time=region_entry.get("name", micro.name),
                )
                rb.save()
                region_bulletins.append(rb)

        if verbosity >= 2:
            self.stdout.write(f"  Created {len(region_bulletins)} RegionBulletin links")
        return region_bulletins

    def _create_day_ratings(
        self, all_bulletins: "list[Any]", verbosity: int
    ) -> "tuple[set[int], int]":
        """
        Generate RegionDayRating rows via the production day-rating service.

        Args:
            all_bulletins: List of saved Bulletin instances.
            verbosity: Verbosity level.

        Returns:
            A ``(new_rdr_pks, rating_count)`` tuple.

        """
        from bulletins.models import RegionDayRating

        for b in all_bulletins:
            try:
                apply_bulletin_day_ratings(b)
            except Exception as exc:
                logger.warning(
                    "build_test_data: day rating failed for %s: %s",
                    b.bulletin_id,
                    exc,
                )

        new_bulletin_pks_set: set[int] = {b.pk for b in all_bulletins}
        new_rdr_pks: set[int] = set(
            RegionDayRating.objects.filter(
                source_bulletin_id__in=new_bulletin_pks_set
            ).values_list("pk", flat=True)
        )
        rating_count = len(new_rdr_pks)
        if verbosity >= 2:
            self.stdout.write(f"  Created {rating_count} RegionDayRating rows")
        return new_rdr_pks, rating_count

    def _create_weather_snapshots(
        self,
        weather_pairs: "list[tuple[str, date]]",
        micro_qs: "dict[str, Any]",
        verbosity: int,
    ) -> "list[Any]":
        """
        Generate WeatherSnapshot rows for the (region, date) pairs.

        Deduplicates pairs so each (region, date) combination is only created once.

        Args:
            weather_pairs: List of (region_id, date) pairs to cover.
            micro_qs: region_id → MicroRegion lookup dict.
            verbosity: Verbosity level.

        Returns:
            List of saved WeatherSnapshot instances.

        """
        from bulletins.models import WeatherSnapshot

        seen_weather: set[tuple[str, date]] = set()
        weather_snapshots: list[Any] = []
        for region_id, target_date in weather_pairs:
            key = (region_id, target_date)
            if key in seen_weather:
                continue
            seen_weather.add(key)
            micro = micro_qs.get(region_id)
            if micro is None:
                continue
            ws_params = _make_weather_snapshot_params(region_id, target_date)
            ws = WeatherSnapshot(
                region=micro,
                valid_for_date=ws_params["valid_for_date"],
                weather_code=ws_params["weather_code"],
                sunrise=ws_params["sunrise"],
                sunset=ws_params["sunset"],
                fetched_at=ws_params["fetched_at"],
            )
            ws.save()
            weather_snapshots.append(ws)

        if verbosity >= 2:
            self.stdout.write(
                f"  Created {len(weather_snapshots)} WeatherSnapshot rows"
            )
        return weather_snapshots

    def _print_counts(self, counts: dict[str, int], verbosity: int) -> None:
        """
        Print a summary of records that would be / were written.

        Args:
            counts: Model label → record count mapping.
            verbosity: Verbosity level from command options.

        """
        if verbosity < 1:
            return
        self.stdout.write("Record counts:")
        for label, count in counts.items():
            self.stdout.write(f"  {label}: {count}")


# ---------------------------------------------------------------------------
# Sentinel exception for transaction rollback
# ---------------------------------------------------------------------------


class _RollbackSentinel(Exception):
    """Raised inside transaction.atomic() to trigger an unconditional rollback."""
