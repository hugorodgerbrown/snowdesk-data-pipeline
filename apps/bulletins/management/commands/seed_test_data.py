"""
apps/bulletins/management/commands/seed_test_data.py — Factory-based DB seeder.

Builds a navigable test dataset by calling the FactoryBoy factories in
``tests/factories.py`` to create real objects in the database — the default way
to populate a fresh dev/CI database (it replaced the old ``build_test_data``
JSON-fixture path). Running the factories against a live database exercises them
as a side benefit.

The command writes rows into the database named by ``DJANGO_SETTINGS_MODULE``.
It is developer tooling for local/CI dev databases: it refuses to run when
``DEBUG`` is ``False`` so it cannot touch a production DB.

Bulletin-layer coverage:
  - on the map reference date (2026-04-08), one Bulletin per *contiguous group*
    of CH MicroRegions (SNOW-534) — 10 groups across the 149 CH regions, the
    order of magnitude SLF issues for a real day. Every region still gets its
    own RegionBulletin + RegionDayRating; only the bulletin count drops, so the
    L3 bulletin-boundary layer has real groupings to dissolve rather than one
    outline per region,
  - a BulletinGrouping per bulletin, computed by the same service the ingest
    path uses, so the boundary layer draws in a freshly-seeded DB,
  - CH-4115 (Martigny-Verbier) additionally gets a single-region bulletin per
    day across April 2026 (2026-04-08 excluded, as the map layer already
    covers it),
  - a WeatherSnapshot per (region, date) pair,
  - render models built at ``RENDER_MODEL_VERSION`` and day ratings applied via the
    production services, so the seeded DB renders with no rebuild step.

It also seeds two named dev accounts (a superuser and an active, CH-4115-subscribed
normal user — folded in from the former ``seed_dev_users`` command) plus a small
standalone set of ForecastCells, a ForecastCellWeather per point per April date,
and one Favourite per point (all owned by the seeded normal dev user).

Region/resort reference data (MajorRegion/SubRegion/MicroRegion/Resort) is a
*prerequisite*: it must already be loaded (e.g. ``loaddata eaws_CH resorts``). It
is not seeded here and is not addressable by ``--include``/``--exclude``.

Selection: exactly one of ``--all``, ``--include``, or ``--exclude`` is required.
``--include``/``--exclude`` take one or more model names (case-insensitive,
validated against the ``SeedModel`` enumeration, so ``--help`` lists the exact
set). Because some models are FK-dependent on others (a RegionBulletin needs a
Bulletin), a selection is expanded to pull in any prerequisite models even if they
were not named — those extra models are reported before seeding.

Read-only by default (prints intended counts); pass ``--commit`` to persist. The
command expects an empty/migrated database: it creates deterministic bulletin IDs
and one WeatherSnapshot per (region, date), so re-seeding a populated DB raises a
``CommandError`` rather than a raw ``IntegrityError``.
"""

from __future__ import annotations

import argparse
import enum
import json
import logging
import uuid
import zoneinfo
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone as django_timezone

from apps.bulletins.services.day_rating import apply_bulletin_day_ratings
from apps.bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    build_render_model,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from apps.bulletins.models import Bulletin
    from apps.regions.models import MicroRegion
    from apps.weather.models import ForecastCell

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataset shape — map-reference date, detail region, and the CAAML payload
# template. These define the navigable test dataset (10 grouped CH map-coverage
# bulletins spanning all 149 micro-regions on MAP_DATE, plus a full-April
# single-region detail month for CH-4115).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[4]
_EAWS_CH_FIXTURE = _REPO_ROOT / "apps" / "regions" / "fixtures" / "eaws_CH.json"

MAP_DATE = date(2026, 4, 8)
APRIL_DATES = [date(2026, 4, d) for d in range(1, 31)]
DETAIL_REGIONS = {"CH-4115": "Martigny-Verbier"}

# Micro-regions per map-date bulletin. A real bulletin covers a set of adjacent
# regions, and the L3 boundary layer exists to draw where one bulletin's regions
# end — with one bulletin per region (the pre-SNOW-534 shape) every boundary
# traced a single micro-region ring and the layer showed nothing the L4 tier
# didn't already.
#
# 18 partitions the 149 CH micro-regions into exactly 10 groups, which is the
# order of magnitude SLF actually issues for Switzerland on a given day. The
# relationship isn't 149/18 — the greedy walk leaves small remainders where it
# runs out of unassigned neighbours — so this constant is tuned to the group
# COUNT it produces, not derived from it. Changing it changes
# ``_EXPECTED_BULLETINS`` in the seeder tests.
_GROUP_TARGET_SIZE = 18

# One bulletin's slot in the coverage plan: the ``(region_id, region_name)``
# pairs it covers, the date it targets, and its CAAML danger key. The regions
# are a tuple, not a single pair, because a map-date bulletin covers a whole
# contiguous group (see ``_contiguous_groups``).
BulletinSpec = tuple[tuple[tuple[str, str], ...], date, str]

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
# Dev/test user accounts (folded in from the former ``seed_dev_users`` command).
# Seeded by ``SeedModel.USER``. The two named accounts let a freshly-seeded DB be
# used for manual testing immediately: a superuser for ``/admin/`` and an active,
# region-subscribed normal user for the full subscription journey. The normal
# user also owns the seeded Favourites. The shared password is documented in
# docs/worktrees.md.
# ---------------------------------------------------------------------------

SUPERUSER_EMAIL = "admin@snowdesk.dev"
NORMAL_USER_EMAIL = "dev@snowdesk.dev"
# Intentional dev-only constant; the command is DEBUG-gated in handle() so it can
# never touch a production DB. Documented in docs/worktrees.md.
DEV_USER_PASSWORD = "snowdesk"  # noqa: S105 — dev-only constant, DEBUG-gated (see handle)

# CH-4115 (Martigny-Verbier) is the canonical detail region; the normal dev user
# is subscribed to it. It must already be loaded (loaddata eaws_CH resorts).
SUBSCRIBED_REGION_ID = "CH-4115"


def _make_raw_data(
    bulletin_id: str,
    regions: "list[tuple[str, str]]",
    valid_from_iso: str,
    valid_to_iso: str,
    issued_at_iso: str,
    danger_value: str = "moderate",
) -> dict[str, Any]:
    """
    Build a minimal but valid CAAML GeoJSON Feature envelope for one bulletin.

    The payload includes enough fields (aggregation, dangerRatings,
    avalancheProblems, snowpackStructure, weatherForecast) for the render model
    builder to produce a non-error render_model at the current version.

    Args:
        bulletin_id: The unique bulletin identifier (UUID-style string).
        regions: The ``(region_id, region_name)`` pairs this bulletin covers,
            in the order they should appear in ``properties.regions``. A real
            bulletin routinely covers several adjacent micro-regions, which is
            what gives the L3 boundary layer something to dissolve — see
            ``_contiguous_groups``.
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
            "regions": [
                {"name": region_name, "regionID": region_id}
                for region_id, region_name in regions
            ],
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


def _bulletin_id_for(region_id: str, target_date: date) -> str:
    """
    Generate a deterministic bulletin_id for a (region, date) pair.

    Uses a UUID-5 constructed from a fixed namespace so the same
    (region, date) always produces the same ID, making the seed
    fully reproducible on repeated runs.

    For a multi-region bulletin the caller passes the group's FIRST region,
    which ``_contiguous_groups`` fixes deterministically — so a group's ID is
    as stable across runs as a single-region one.

    Args:
        region_id: SLF region ID string.
        target_date: The calendar date for the bulletin.

    Returns:
        A UUID-style string unique to (region_id, target_date).

    """
    ns = uuid.UUID("a4020000-0000-0000-0000-000000000000")
    return str(uuid.uuid5(ns, f"{region_id}:{target_date.isoformat()}"))


def _contiguous_groups(
    region_ids: "list[str]",
    adjacency: "dict[str, set[str]]",
    target_size: int = _GROUP_TARGET_SIZE,
) -> "list[list[str]]":
    """
    Partition ``region_ids`` into contiguous groups of up to ``target_size``.

    Models how a real bulletin covers several *adjacent* micro-regions rather
    than exactly one. Contiguity matters: ``compute_bulletin_grouping_boundary``
    dissolves a bulletin's regions with ``unary_union``, so a group of
    non-touching regions yields a MultiPolygon of separate outlines — which
    looks identical to no grouping at all and defeats the point of the layer.

    Walks ``region_ids`` in sorted order and grows each group breadth-first
    from the unassigned neighbours of the regions already in it. Every step
    iterates sorted candidates, so the partition is deterministic: re-seeding
    reproduces the same bulletin set and therefore the same bulletin IDs.

    A region whose neighbours are all already assigned forms a group of one.
    That is not a defect to design out — single-region bulletins exist in the
    real feed too, and the mix exercises both shapes.

    Args:
        region_ids: The region IDs to partition.
        adjacency: ``region_id -> set of neighbouring region_ids``. Missing
            keys are treated as "no neighbours" (an isolated region).
        target_size: Maximum regions per group.

    Returns:
        A list of groups, each a list of region IDs with the group's
        lowest-sorting member first.

    """
    remaining = sorted(region_ids)
    unassigned = set(remaining)
    groups: list[list[str]] = []

    for seed_region in remaining:
        if seed_region not in unassigned:
            continue
        group = [seed_region]
        unassigned.discard(seed_region)
        # Breadth-first over the group's own frontier, so every added region
        # touches one already in the group and the union stays connected.
        frontier = [seed_region]
        while frontier and len(group) < target_size:
            current = frontier.pop(0)
            for neighbour in sorted(adjacency.get(current, ())):
                if len(group) >= target_size:
                    break
                if neighbour not in unassigned:
                    continue
                group.append(neighbour)
                unassigned.discard(neighbour)
                frontier.append(neighbour)
        groups.append(group)

    return groups


def _adjacency_from_fixture(
    region_data: "list[dict[str, Any]]",
) -> "dict[str, set[str]]":
    """
    Build the micro-region adjacency map from the eaws fixture's ``neighbours``.

    Reads the fixture rather than the DB so the coverage plan stays computable
    before any query runs (and so ``--dry-run`` needs no region rows). Fixture
    neighbour entries are natural keys — one-element lists like
    ``[["CH-1112"], ["CH-1113"]]`` — which are flattened to bare region IDs
    here. The map is symmetrised: the fixture lists each edge from both sides
    already, but relying on that would make the partition depend on fixture
    hygiene rather than on the graph.

    Args:
        region_data: The parsed eaws fixture rows (all models).

    Returns:
        ``region_id -> set of neighbouring region_ids``.

    """
    adjacency: dict[str, set[str]] = {}
    for row in region_data:
        if row["model"] != "regions.microregion":
            continue
        region_id = row["fields"]["region_id"]
        neighbours = {
            entry[0] if isinstance(entry, list) else entry
            for entry in row["fields"].get("neighbours", [])
        }
        adjacency.setdefault(region_id, set()).update(neighbours)
        for neighbour in neighbours:
            adjacency.setdefault(neighbour, set()).add(region_id)
    return adjacency


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
    regions: "list[tuple[str, str]]",
    target_date: date,
    danger_value: str,
) -> dict[str, Any]:
    """
    Return kwargs for creating a Bulletin instance for one (regions, date) group.

    Morning issue: valid_from at 07:00 UTC on ``target_date``.

    Args:
        regions: The ``(region_id, region_name)`` pairs this bulletin covers.
            The first entry keys the deterministic bulletin ID, so callers
            must pass the group in a stable order.
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
    bid = _bulletin_id_for(regions[0][0], target_date)
    raw = _make_raw_data(
        bulletin_id=bid,
        regions=regions,
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


def _make_weather_snapshot_params(
    region_id: str,
    target_date: date,
) -> dict[str, Any]:
    """
    Build kwargs for a deterministic WeatherSnapshot / ForecastCellWeather row.

    Uses WMO code 1 (mainly clear), sunrise 06:30 Europe/Zurich,
    sunset 18:30 Europe/Zurich for the given date.

    Args:
        region_id: Accepted for call-site symmetry; not used (sunrise/sunset are
            derived from ``target_date`` alone).
        target_date: The calendar date the snapshot applies to.

    Returns:
        A dict of shared weather field values (region/forecast_cell FK excluded).

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


class SeedModel(enum.StrEnum):
    """Models ``seed_test_data`` can create, named by ``--include``/``--exclude``.

    Values are the lowercase model names surfaced in ``--help``. Membership order
    is irrelevant; seeding order is fixed by ``_BULLETIN_FAMILY``/``_POINT_FAMILY``.
    """

    BULLETIN = "bulletin"
    REGIONBULLETIN = "regionbulletin"
    REGIONDAYRATING = "regiondayrating"
    BULLETINGROUPING = "bulletingrouping"
    WEATHERSNAPSHOT = "weathersnapshot"
    FORECASTCELL = "forecastcell"
    FORECASTCELLWEATHER = "forecastcellweather"
    FAVOURITE = "favourite"
    USER = "user"


# FK prerequisites — selecting a model pulls in these even when unnamed, because
# the row cannot exist without them (a RegionBulletin needs a Bulletin, etc.).
_DEPENDENCIES: dict[SeedModel, tuple[SeedModel, ...]] = {
    SeedModel.BULLETIN: (),
    SeedModel.REGIONBULLETIN: (SeedModel.BULLETIN,),
    # RegionDayRating needs RegionBulletin too: apply_bulletin_day_ratings reads
    # bulletin.regions.all() (the RegionBulletin M2M), so without those links it
    # silently produces zero ratings.
    SeedModel.REGIONDAYRATING: (SeedModel.BULLETIN, SeedModel.REGIONBULLETIN),
    # Same reason as RegionDayRating: compute_bulletin_grouping_boundary
    # dissolves the regions reached through the RegionBulletin M2M, so without
    # those links every bulletin looks region-less and no grouping is written.
    SeedModel.BULLETINGROUPING: (SeedModel.BULLETIN, SeedModel.REGIONBULLETIN),
    SeedModel.WEATHERSNAPSHOT: (),
    SeedModel.FORECASTCELL: (),
    SeedModel.FORECASTCELLWEATHER: (SeedModel.FORECASTCELL,),
    # Favourites are owned by the seeded normal dev user, so USER is a prerequisite.
    SeedModel.FAVOURITE: (SeedModel.FORECASTCELL, SeedModel.USER),
    SeedModel.USER: (),
}

# Dependency-safe seeding order, split into the two independent families the run
# is organised around (the bulletin layer shares created Bulletins; the point layer
# shares created ForecastCells). The families have no cross-dependencies.
_BULLETIN_FAMILY: tuple[SeedModel, ...] = (
    SeedModel.BULLETIN,
    SeedModel.REGIONBULLETIN,
    SeedModel.REGIONDAYRATING,
    SeedModel.BULLETINGROUPING,
    SeedModel.WEATHERSNAPSHOT,
)
_POINT_FAMILY: tuple[SeedModel, ...] = (
    SeedModel.FORECASTCELL,
    SeedModel.FORECASTCELLWEATHER,
    SeedModel.FAVOURITE,
)
# The account layer has no cross-dependencies on the other families and is seeded
# first, because Favourites (point family) are owned by the seeded normal user.
_ACCOUNT_FAMILY: tuple[SeedModel, ...] = (SeedModel.USER,)

_CHOICES: list[str] = [m.value for m in SeedModel]

# Coordinates for the seeded ForecastCells — spaced (0.1° / 300 m apart) so each
# resolves to a distinct (lat_cell, lon_cell, elevation_band) triple, around the
# Verbier detail region.
_FORECAST_CELL_COORDS: tuple[tuple[float, float, float], ...] = (
    (46.10, 7.40, 1500.0),
    (46.20, 7.50, 1800.0),
    (46.30, 7.60, 2100.0),
    (46.40, 7.70, 2400.0),
    (46.50, 7.80, 2700.0),
)


class _Rollback(Exception):
    """Raised inside ``transaction.atomic()`` to force a dry-run rollback."""


class Command(BaseCommand):
    """Seed a database with the navigable test dataset via factories.

    Read-only by default; ``--commit`` persists. Exactly one of ``--all``,
    ``--include``, or ``--exclude`` selects which models to seed.

    SNOW-602 exempt: a fixed-size seed spec driven by module-level helpers,
    not a queryset over a growable table.
    """

    help = (
        "Seed the DB with the navigable test dataset using tests/factories.py "
        "factories. Read-only by default; pass --commit to persist. Requires one "
        "of --all / --include / --exclude."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Persist rows to the DB. Without this flag the command is read-only.",
        )
        # nargs="*" (not "+") and no argparse-level ``choices``: an empty or unknown
        # value is validated in ``_resolve_selection`` so both cases print the full
        # available-model list, rather than argparse's terse "expected at least one
        # argument".
        selection = parser.add_mutually_exclusive_group(required=True)
        selection.add_argument(
            "--all",
            action="store_true",
            help="Seed every model (include everything / exclude nothing).",
        )
        selection.add_argument(
            "--include",
            nargs="*",
            metavar="MODEL",
            help=(
                "Seed only the named model(s), case-insensitive. FK prerequisites "
                f"are pulled in automatically. Models: {', '.join(_CHOICES)}."
            ),
        )
        selection.add_argument(
            "--exclude",
            nargs="*",
            metavar="MODEL",
            help=(
                "Seed every model except the named one(s), case-insensitive. A "
                "prerequisite of a seeded model is still created even if excluded. "
                f"Models: {', '.join(_CHOICES)}."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        if not settings.DEBUG:
            raise CommandError("This command is only available when DEBUG=True.")

        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        resolved, pulled_in = self._resolve_selection(options)

        if pulled_in and verbosity >= 1:
            names = ", ".join(sorted(m.value for m in pulled_in))
            self.stdout.write(
                self.style.WARNING(
                    f"Also seeding FK prerequisites (not named but required): {names}"
                )
            )

        if not commit:
            self.stdout.write(
                self.style.WARNING(
                    "[READ-ONLY] Dry-run — no rows written. Pass --commit to persist."
                )
            )

        specs, weather_pairs = self._coverage()
        micro_map = self._load_micro_regions(specs)

        counts: dict[str, int] = {}
        try:
            with transaction.atomic():
                counts = self._run_seeders(
                    resolved, specs, weather_pairs, micro_map, verbosity
                )
                if not commit:
                    raise _Rollback()
        except _Rollback:
            pass
        except IntegrityError as exc:
            raise CommandError(
                "Seeding hit a uniqueness constraint. seed_test_data expects an "
                "empty database — it creates deterministic bulletin IDs and one "
                "WeatherSnapshot per (region, date), which collide with existing "
                "rows. Start from a fresh/migrated DB (or flush it) and retry. "
                f"({exc})"
            ) from exc

        self._print_counts(counts, verbosity)

        if not commit:
            self.stdout.write(
                self.style.SUCCESS("Dry-run complete. Pass --commit to persist.")
            )
            return
        self.stdout.write(self.style.SUCCESS(f"Seeded {sum(counts.values())} rows."))
        logger.info("seed_test_data: seeded %d rows", sum(counts.values()))

    # ------------------------------------------------------------------
    # Selection resolution
    # ------------------------------------------------------------------

    def _resolve_selection(
        self, options: dict[str, Any]
    ) -> "tuple[set[SeedModel], set[SeedModel]]":
        """Resolve the flag selection into a seeding set plus pulled-in prerequisites.

        argparse guarantees exactly one of ``--all``/``--include``/``--exclude`` is
        present (the group is ``required=True``); the model names themselves are
        validated here so an empty or unknown value lists the available models.

        Args:
            options: Parsed command options.

        Returns:
            A ``(resolved, pulled_in)`` tuple where ``resolved`` is the full set of
            models to seed (including prerequisites) and ``pulled_in`` is the subset
            added purely to satisfy FK dependencies.

        Raises:
            CommandError: If ``--include``/``--exclude`` is given with no model, or
                with a name outside the ``SeedModel`` enumeration.

        """
        if options.get("all"):
            base = set(SeedModel)
        elif options.get("include") is not None:
            base = self._parse_model_names("--include", options["include"])
        elif options.get("exclude") is not None:
            base = set(SeedModel) - self._parse_model_names(
                "--exclude", options["exclude"]
            )
        else:  # pragma: no cover — argparse's required group prevents this
            raise CommandError(
                f"Select one of --all / --include / --exclude. {self._available()}"
            )

        resolved = set(base)
        for model in base:
            resolved.update(_DEPENDENCIES[model])
        return resolved, resolved - base

    @staticmethod
    def _available() -> str:
        """Return a one-line list of the seedable model names for error messages."""
        return "Available models: " + ", ".join(m.value for m in SeedModel) + "."

    def _parse_model_names(self, flag: str, names: list[str]) -> "set[SeedModel]":
        """Validate raw ``--include``/``--exclude`` values into a set of SeedModels.

        Names are matched case-insensitively. An empty list or any unknown name
        raises a ``CommandError`` whose message lists the available models.

        Args:
            flag: The flag being validated (for the error message), e.g. ``--include``.
            names: The raw model names supplied on the command line.

        Returns:
            The validated set of ``SeedModel`` members.

        Raises:
            CommandError: If ``names`` is empty or contains an unknown model name.

        """
        if not names:
            raise CommandError(
                f"{flag} needs at least one model name. {self._available()}"
            )
        valid = {m.value: m for m in SeedModel}
        selected: set[SeedModel] = set()
        unknown: list[str] = []
        for name in names:
            model = valid.get(name.lower())
            if model is None:
                unknown.append(name)
            else:
                selected.add(model)
        if unknown:
            raise CommandError(
                f"{flag}: unknown model name(s): {', '.join(unknown)}. "
                f"{self._available()}"
            )
        return selected

    # ------------------------------------------------------------------
    # Coverage plan and region lookup
    # ------------------------------------------------------------------

    def _coverage(
        self,
    ) -> "tuple[list[BulletinSpec], list[tuple[str, date]]]":
        """Build the bulletin and weather coverage plan.

        The region set and per-day danger logic are read from the same eaws_CH
        eaws_CH fixture (for the region set) and the module-level dataset helpers.

        Returns:
            A ``(bulletin_specs, weather_pairs)`` tuple. ``bulletin_specs`` is a
            list of ``(regions, target_date, danger_value)``, where ``regions``
            is a tuple of ``(region_id, region_name)`` pairs — one bulletin may
            cover several adjacent micro-regions (SNOW-534). ``weather_pairs``
            is a list of ``(region_id, target_date)``, still one per region.

        """
        region_data = json.loads(_EAWS_CH_FIXTURE.read_text())
        region_name_map: dict[str, str] = {
            d["fields"]["region_id"]: d["fields"]["name"]
            for d in region_data
            if d["model"] == "regions.microregion"
        }

        specs: list[BulletinSpec] = []
        # Map date: one bulletin per contiguous group of micro-regions, so the
        # L3 boundary layer has real groupings to dissolve (SNOW-534). Every
        # region is still covered exactly once, so the per-region rating and
        # snapshot counts are unchanged — only the bulletin count drops.
        adjacency = _adjacency_from_fixture(region_data)
        for group in _contiguous_groups(list(region_name_map), adjacency):
            regions = tuple((rid, region_name_map[rid]) for rid in group)
            specs.append((regions, MAP_DATE, "moderate"))
        # Detail region: single-region bulletins, one per April day. Kept
        # ungrouped both because the region detail pages read this region's own
        # bulletin per day, and because the real feed mixes both shapes.
        for region_id, region_name in DETAIL_REGIONS.items():
            for target_date in APRIL_DATES:
                if target_date == MAP_DATE:
                    continue
                specs.append(
                    (
                        ((region_id, region_name),),
                        target_date,
                        _danger_for_day(target_date),
                    )
                )

        weather_pairs = [
            (region_id, target_date)
            for regions, target_date, _ in specs
            for region_id, _ in regions
        ]
        return specs, weather_pairs

    def _load_micro_regions(
        self, specs: "list[BulletinSpec]"
    ) -> "dict[str, MicroRegion]":
        """Load the MicroRegion rows referenced by the coverage plan.

        Args:
            specs: The bulletin coverage plan from ``_coverage``.

        Returns:
            A ``region_id -> MicroRegion`` lookup for the regions that exist in the
            DB. Missing regions are silently omitted; seeders skip them with a log
            warning.

        """
        from apps.regions.models import MicroRegion

        region_ids = {region_id for regions, _, _ in specs for region_id, _ in regions}
        return MicroRegion.objects.filter(region_id__in=region_ids).in_bulk(
            field_name="region_id"
        )

    # ------------------------------------------------------------------
    # Seeder dispatch
    # ------------------------------------------------------------------

    def _run_seeders(
        self,
        resolved: "set[SeedModel]",
        specs: "list[BulletinSpec]",
        weather_pairs: "list[tuple[str, date]]",
        micro_map: "dict[str, MicroRegion]",
        verbosity: int,
    ) -> dict[str, int]:
        """Run the selected seeders in dependency-safe order and collect counts.

        Args:
            resolved: Models to seed (already expanded with prerequisites).
            specs: Bulletin coverage plan.
            weather_pairs: (region_id, date) pairs for weather snapshots.
            micro_map: region_id -> MicroRegion lookup.
            verbosity: Verbosity level.

        Returns:
            A dict of model value -> number of rows created.

        """
        counts: dict[str, int] = {}
        account_counts, dev_user = self._seed_account_family(resolved, verbosity)
        counts.update(account_counts)
        counts.update(
            self._seed_bulletin_family(
                resolved, specs, weather_pairs, micro_map, verbosity
            )
        )
        counts.update(self._seed_point_family(resolved, dev_user, verbosity))
        return counts

    def _seed_account_family(
        self, resolved: "set[SeedModel]", verbosity: int
    ) -> "tuple[dict[str, int], User | None]":
        """Seed the account-layer models (the two named dev users).

        Args:
            resolved: Models to seed (already expanded with prerequisites).
            verbosity: Verbosity level.

        Returns:
            A ``(counts, normal_user)`` tuple. ``normal_user`` is the subscribed
            dev user that owns the seeded Favourites, or ``None`` when USER is not
            in the selection.

        """
        counts: dict[str, int] = {}
        dev_user: User | None = None
        for model in _ACCOUNT_FAMILY:
            if model not in resolved:
                continue
            if model is SeedModel.USER:
                counts[model.value], dev_user = self._seed_users(verbosity)
        return counts, dev_user

    def _seed_users(self, verbosity: int) -> "tuple[int, User]":
        """Create the two named dev accounts and return the subscribed normal user.

        Folded in from the former ``seed_dev_users`` command: a superuser for
        ``/admin/`` and an active, CH-4115-subscribed normal user. Idempotent
        (``update_or_create`` / ``get_or_create``), so ``--include user`` can be
        re-run safely even though the rest of the command expects an empty DB.

        Args:
            verbosity: Verbosity level.

        Returns:
            A ``(count, normal_user)`` tuple: the number of accounts ensured (2)
            and the subscribed normal dev user (owns the seeded Favourites).

        Raises:
            CommandError: If the CH-4115 MicroRegion is not loaded.

        """
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Account, Subscription
        from apps.regions.models import MicroRegion

        user_model = get_user_model()

        # Superuser for /admin/.
        email_super = SUPERUSER_EMAIL.lower()
        user_super, _ = user_model.objects.update_or_create(
            username=email_super,
            defaults={
                "email": email_super,
                "is_staff": True,
                "is_superuser": True,
            },
        )
        user_super.set_password(DEV_USER_PASSWORD)
        user_super.save()

        # Verified, region-subscribed normal user.
        email_dev = NORMAL_USER_EMAIL.lower()
        now = django_timezone.now()
        account, _ = Account.objects.get_or_create_for_email(
            email_dev,
            defaults={"is_verified": True, "verified_at": now},
        )
        # get_or_create_for_email leaves an unusable password; set it explicitly
        # so the account is immediately usable for manual testing.
        account.user.set_password(DEV_USER_PASSWORD)
        account.user.save()
        if not account.is_verified:
            account.mark_verified(now)
            account.save(update_fields=["is_verified", "verified_at", "updated_at"])

        try:
            region = MicroRegion.objects.get(region_id=SUBSCRIBED_REGION_ID)
        except MicroRegion.DoesNotExist as exc:
            raise CommandError(
                f"MicroRegion {SUBSCRIBED_REGION_ID!r} does not exist. Load the "
                "region fixtures first: "
                "uv run python manage.py loaddata eaws_CH resorts"
            ) from exc
        Subscription.objects.get_or_create(
            account=account,
            region=region,
            defaults={"geo_match_kind": Subscription.GeoMatchKind.IN_REGION},
        )

        if verbosity >= 2:
            self.stdout.write(f"  Created dev accounts: {email_super}, {email_dev}")
        return 2, account.user

    def _seed_bulletin_family(
        self,
        resolved: "set[SeedModel]",
        specs: "list[BulletinSpec]",
        weather_pairs: "list[tuple[str, date]]",
        micro_map: "dict[str, MicroRegion]",
        verbosity: int,
    ) -> dict[str, int]:
        """Seed the bulletin-layer models (Bulletin/RegionBulletin/rating/snapshot).

        Args:
            resolved: Models to seed (already expanded with prerequisites).
            specs: Bulletin coverage plan.
            weather_pairs: (region_id, date) pairs for weather snapshots.
            micro_map: region_id -> MicroRegion lookup.
            verbosity: Verbosity level.

        Returns:
            A dict of model value -> row count for the models in this family.

        """
        counts: dict[str, int] = {}
        bulletins: list[Bulletin] = []
        for model in _BULLETIN_FAMILY:
            if model not in resolved:
                continue
            if model is SeedModel.BULLETIN:
                bulletins = self._seed_bulletins(specs, verbosity)
                counts[model.value] = len(bulletins)
            elif model is SeedModel.REGIONBULLETIN:
                counts[model.value] = self._seed_region_bulletins(
                    bulletins, micro_map, verbosity
                )
            elif model is SeedModel.REGIONDAYRATING:
                counts[model.value] = self._seed_day_ratings(bulletins, verbosity)
            elif model is SeedModel.BULLETINGROUPING:
                counts[model.value] = self._seed_bulletin_groupings(
                    bulletins, verbosity
                )
            elif model is SeedModel.WEATHERSNAPSHOT:
                counts[model.value] = self._seed_weather_snapshots(
                    weather_pairs, micro_map, verbosity
                )
        return counts

    def _seed_point_family(
        self, resolved: "set[SeedModel]", dev_user: "User | None", verbosity: int
    ) -> dict[str, int]:
        """Seed the point-layer models (ForecastCell/ForecastCellWeather/Favourite).

        Args:
            resolved: Models to seed (already expanded with prerequisites).
            dev_user: The seeded normal user that owns Favourites. Guaranteed
                non-``None`` whenever FAVOURITE is seeded, because FAVOURITE
                depends on USER (see ``_DEPENDENCIES``).
            verbosity: Verbosity level.

        Returns:
            A dict of model value -> row count for the models in this family.

        """
        counts: dict[str, int] = {}
        forecast_cells: list[ForecastCell] = []
        for model in _POINT_FAMILY:
            if model not in resolved:
                continue
            if model is SeedModel.FORECASTCELL:
                forecast_cells = self._seed_forecast_cells(verbosity)
                counts[model.value] = len(forecast_cells)
            elif model is SeedModel.FORECASTCELLWEATHER:
                counts[model.value] = self._seed_forecast_cell_weather(
                    forecast_cells, verbosity
                )
            elif model is SeedModel.FAVOURITE:
                if dev_user is None:  # pragma: no cover — FAVOURITE pulls in USER
                    raise CommandError(
                        "Favourite seeding requires the USER model, which should "
                        "have been pulled in as a prerequisite."
                    )
                counts[model.value] = self._seed_favourites(
                    forecast_cells, dev_user, verbosity
                )
        return counts

    def _seed_bulletins(
        self, specs: "list[BulletinSpec]", verbosity: int
    ) -> "list[Bulletin]":
        """Create Bulletin rows with real CAAML payloads and built render models.

        Args:
            specs: Bulletin coverage plan.
            verbosity: Verbosity level.

        Returns:
            The created Bulletin instances (render model built, saved).

        """
        from tests.factories import BulletinFactory

        bulletins: list[Bulletin] = []
        for regions, target_date, danger in specs:
            params = _make_bulletin_params(list(regions), target_date, danger)
            bulletin = BulletinFactory.create(**params)
            self._build_render_model(bulletin)
            bulletins.append(bulletin)

        if verbosity >= 2:
            self.stdout.write(f"  Created {len(bulletins)} Bulletin rows")
        return bulletins

    def _build_render_model(self, bulletin: "Bulletin") -> None:
        """Build and persist the render model for one bulletin in-place.

        Args:
            bulletin: A saved Bulletin instance.

        """
        properties = (bulletin.raw_data or {}).get("properties", {})
        try:
            bulletin.render_model = build_render_model(properties)
            bulletin.render_model_version = RENDER_MODEL_VERSION
        except Exception as exc:  # noqa: BLE001 — never abort the seed on one bad render
            logger.warning(
                "seed_test_data: render model failed for %s: %s",
                bulletin.bulletin_id,
                exc,
            )
            bulletin.render_model = {"version": 0, "error": str(exc)}
            bulletin.render_model_version = 0
        bulletin.save(update_fields=["render_model", "render_model_version"])

    def _seed_region_bulletins(
        self,
        bulletins: "list[Bulletin]",
        micro_map: "dict[str, MicroRegion]",
        verbosity: int,
    ) -> int:
        """Create RegionBulletin links for every bulletin's regions.

        Args:
            bulletins: The seeded Bulletin instances.
            micro_map: region_id -> MicroRegion lookup.
            verbosity: Verbosity level.

        Returns:
            The number of RegionBulletin rows created.

        """
        from tests.factories import RegionBulletinFactory

        created = 0
        for bulletin in bulletins:
            properties = (bulletin.raw_data or {}).get("properties", {})
            for region_entry in properties.get("regions", []):
                micro = micro_map.get(region_entry["regionID"])
                if micro is None:
                    logger.warning(
                        "seed_test_data: no MicroRegion for %s — skipping",
                        region_entry["regionID"],
                    )
                    continue
                RegionBulletinFactory.create(
                    bulletin=bulletin,
                    region=micro,
                    region_name_at_time=region_entry.get("name", micro.name),
                )
                created += 1

        if verbosity >= 2:
            self.stdout.write(f"  Created {created} RegionBulletin rows")
        return created

    def _seed_day_ratings(self, bulletins: "list[Bulletin]", verbosity: int) -> int:
        """Generate RegionDayRating rows via the production day-rating service.

        Args:
            bulletins: The seeded Bulletin instances.
            verbosity: Verbosity level.

        Returns:
            The number of RegionDayRating rows created from these bulletins.

        """
        from apps.bulletins.models import RegionDayRating

        for bulletin in bulletins:
            try:
                apply_bulletin_day_ratings(bulletin)
            except Exception as exc:  # noqa: BLE001 — one bad rating never aborts the seed
                logger.warning(
                    "seed_test_data: day rating failed for %s: %s",
                    bulletin.bulletin_id,
                    exc,
                )

        count = RegionDayRating.objects.filter(source_bulletin__in=bulletins).count()
        if verbosity >= 2:
            self.stdout.write(f"  Created {count} RegionDayRating rows")
        return count

    def _seed_bulletin_groupings(
        self, bulletins: "list[Bulletin]", verbosity: int
    ) -> int:
        """Dissolve each bulletin's regions into a BulletinGrouping row.

        Calls the same ``compute_bulletin_grouping_boundary`` service the real
        ingest path runs from ``upsert_bulletin``. The factory-based seeder
        bypasses ``upsert_bulletin`` entirely, so without this step a seeded DB
        has no groupings at all and ``/api/bulletin-groupings.geojson`` returns
        an empty FeatureCollection for every date — the L3 boundary layer draws
        nothing until someone runs ``backfill_bulletin_groupings`` by hand
        (SNOW-534).

        A bulletin whose regions carry no boundary geometry yields ``None`` and
        is counted as skipped, matching the service's own contract.

        Args:
            bulletins: The seeded Bulletin instances.
            verbosity: Verbosity level.

        Returns:
            The number of BulletinGrouping rows created.

        """
        from apps.bulletins.services.grouping import compute_bulletin_grouping_boundary

        created = 0
        for bulletin in bulletins:
            try:
                if compute_bulletin_grouping_boundary(bulletin) is not None:
                    created += 1
            except Exception as exc:  # noqa: BLE001 — one bad dissolve never aborts the seed
                logger.warning(
                    "seed_test_data: grouping failed for %s: %s",
                    bulletin.bulletin_id,
                    exc,
                )

        if verbosity >= 2:
            self.stdout.write(f"  Created {created} BulletinGrouping rows")
        return created

    def _seed_weather_snapshots(
        self,
        weather_pairs: "list[tuple[str, date]]",
        micro_map: "dict[str, MicroRegion]",
        verbosity: int,
    ) -> int:
        """Create one WeatherSnapshot per unique (region, date) pair.

        Args:
            weather_pairs: (region_id, date) pairs to cover.
            micro_map: region_id -> MicroRegion lookup.
            verbosity: Verbosity level.

        Returns:
            The number of WeatherSnapshot rows created.

        """
        from tests.factories import WeatherSnapshotFactory

        seen: set[tuple[str, date]] = set()
        created = 0
        for region_id, target_date in weather_pairs:
            if (region_id, target_date) in seen:
                continue
            seen.add((region_id, target_date))
            micro = micro_map.get(region_id)
            if micro is None:
                continue
            params = _make_weather_snapshot_params(region_id, target_date)
            WeatherSnapshotFactory.create(
                region=micro,
                valid_for_date=params["valid_for_date"],
                weather_code=params["weather_code"],
                sunrise=params["sunrise"],
                sunset=params["sunset"],
                fetched_at=params["fetched_at"],
            )
            created += 1

        if verbosity >= 2:
            self.stdout.write(f"  Created {created} WeatherSnapshot rows")
        return created

    def _seed_forecast_cells(self, verbosity: int) -> "list[ForecastCell]":
        """Create the fixed set of ForecastCells from ``_FORECAST_CELL_COORDS``.

        Args:
            verbosity: Verbosity level.

        Returns:
            The created ForecastCell instances.

        """
        from tests.factories import ForecastCellFactory

        points = [
            ForecastCellFactory.create(
                latitude=latitude, longitude=longitude, elevation=elevation
            )
            for latitude, longitude, elevation in _FORECAST_CELL_COORDS
        ]

        if verbosity >= 2:
            self.stdout.write(f"  Created {len(points)} ForecastCell rows")
        return points

    def _seed_forecast_cell_weather(
        self, forecast_cells: "list[ForecastCell]", verbosity: int
    ) -> int:
        """Create a ForecastCellWeather per point across every April date.

        Sunrise/sunset/weather-code come from the same helper as the region
        snapshots; the extended daily fields use the factory defaults.

        Args:
            forecast_cells: The seeded ForecastCell instances.
            verbosity: Verbosity level.

        Returns:
            The number of ForecastCellWeather rows created.

        """
        from tests.factories import ForecastCellWeatherFactory

        created = 0
        for point in forecast_cells:
            for target_date in APRIL_DATES:
                # region_id is unused by the helper (it derives sunrise/sunset from
                # the date alone); "" keeps the call honest for point weather.
                params = _make_weather_snapshot_params("", target_date)
                ForecastCellWeatherFactory.create(
                    forecast_cell=point,
                    valid_for_date=params["valid_for_date"],
                    weather_code=params["weather_code"],
                    sunrise=params["sunrise"],
                    sunset=params["sunset"],
                    fetched_at=params["fetched_at"],
                )
                created += 1

        if verbosity >= 2:
            self.stdout.write(f"  Created {created} ForecastCellWeather rows")
        return created

    def _seed_favourites(
        self,
        forecast_cells: "list[ForecastCell]",
        owner: "User",
        verbosity: int,
    ) -> int:
        """Create one Favourite per ForecastCell, all owned by the dev user.

        Each Favourite references a seeded ForecastCell (so its coordinates line
        up with real point weather) rather than letting the factory synthesise a
        fresh point. Ownership is the seeded normal dev user (``owner``) so the
        Favourites appear on that account during manual testing.

        Args:
            forecast_cells: The seeded ForecastCell instances.
            owner: The seeded normal dev user that owns the Favourites.
            verbosity: Verbosity level.

        Returns:
            The number of Favourite rows created.

        """
        from tests.factories import FavouriteFactory

        created = 0
        for point in forecast_cells:
            FavouriteFactory.create(
                user=owner,
                forecast_point=point,
                latitude=point.latitude,
                longitude=point.longitude,
                elevation=point.elevation,
                region=None,
            )
            created += 1

        if verbosity >= 2:
            self.stdout.write(f"  Created {created} Favourite rows")
        return created

    def _print_counts(self, counts: dict[str, int], verbosity: int) -> None:
        """Print a summary of rows created (or that would be created in dry-run).

        Args:
            counts: model value -> row count mapping.
            verbosity: Verbosity level.

        """
        if verbosity < 1:
            return
        self.stdout.write("Record counts:")
        for label, count in counts.items():
            self.stdout.write(f"  {label}: {count}")
