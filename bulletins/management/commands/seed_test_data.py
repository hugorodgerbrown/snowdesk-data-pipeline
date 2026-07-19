"""
bulletins/management/commands/seed_test_data.py — Factory-based DB seeder.

Populates a database with the same navigable dataset that ``build_test_data``
bakes into ``bulletins/fixtures/test_data.json``, but by calling the FactoryBoy
factories in ``tests/factories.py`` to create real objects rather than loading a
static fixture. Running the factories against a live database exercises them as a
side benefit.

Unlike ``build_test_data`` (which writes a JSON fixture and rolls the DB back),
this command writes rows into the database named by ``DJANGO_SETTINGS_MODULE``.
It is developer tooling for local/CI dev databases: like ``seed_dev_users`` it
refuses to run when ``DEBUG`` is ``False`` so it cannot touch a production DB.

Coverage (identical to ``build_test_data`` — the two share the dataset helpers):
  - one Bulletin + RegionBulletin + RegionDayRating per CH MicroRegion on the map
    reference date (2026-04-08),
  - CH-4115 (Martigny-Verbier) additionally gets a bulletin per day across April
    2026 (2026-04-08 excluded, as the map layer already covers it),
  - a WeatherSnapshot per (region, date) pair,
  - render models built at ``RENDER_MODEL_VERSION`` and day ratings applied via the
    production services, so the seeded DB renders with no rebuild step.

Beyond the ``build_test_data`` layer it also seeds a small standalone set of
ForecastPoints, a ForecastPointWeather per point per April date, and one Favourite
per point (all owned by a single seeded user) — none of which appear in the JSON
fixture.

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
from datetime import date
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from bulletins.management.commands.build_test_data import (
    _EAWS_CH_FIXTURE,
    APRIL_DATES,
    DETAIL_REGIONS,
    MAP_DATE,
    _danger_for_day,
    _make_bulletin_params,
    _make_weather_snapshot_params,
)
from bulletins.services.day_rating import apply_bulletin_day_ratings
from bulletins.services.render_model import RENDER_MODEL_VERSION, build_render_model

if TYPE_CHECKING:
    from bulletins.models import Bulletin, ForecastPoint
    from regions.models import MicroRegion

logger = logging.getLogger(__name__)


class SeedModel(enum.StrEnum):
    """Models ``seed_test_data`` can create, named by ``--include``/``--exclude``.

    Values are the lowercase model names surfaced in ``--help``. Membership order
    is irrelevant; seeding order is fixed by ``_BULLETIN_FAMILY``/``_POINT_FAMILY``.
    """

    BULLETIN = "bulletin"
    REGIONBULLETIN = "regionbulletin"
    REGIONDAYRATING = "regiondayrating"
    WEATHERSNAPSHOT = "weathersnapshot"
    FORECASTPOINT = "forecastpoint"
    FORECASTPOINTWEATHER = "forecastpointweather"
    FAVOURITE = "favourite"


# FK prerequisites — selecting a model pulls in these even when unnamed, because
# the row cannot exist without them (a RegionBulletin needs a Bulletin, etc.).
_DEPENDENCIES: dict[SeedModel, tuple[SeedModel, ...]] = {
    SeedModel.BULLETIN: (),
    SeedModel.REGIONBULLETIN: (SeedModel.BULLETIN,),
    # RegionDayRating needs RegionBulletin too: apply_bulletin_day_ratings reads
    # bulletin.regions.all() (the RegionBulletin M2M), so without those links it
    # silently produces zero ratings.
    SeedModel.REGIONDAYRATING: (SeedModel.BULLETIN, SeedModel.REGIONBULLETIN),
    SeedModel.WEATHERSNAPSHOT: (),
    SeedModel.FORECASTPOINT: (),
    SeedModel.FORECASTPOINTWEATHER: (SeedModel.FORECASTPOINT,),
    SeedModel.FAVOURITE: (SeedModel.FORECASTPOINT,),
}

# Dependency-safe seeding order, split into the two independent families the run
# is organised around (the bulletin layer shares created Bulletins; the point layer
# shares created ForecastPoints). The families have no cross-dependencies.
_BULLETIN_FAMILY: tuple[SeedModel, ...] = (
    SeedModel.BULLETIN,
    SeedModel.REGIONBULLETIN,
    SeedModel.REGIONDAYRATING,
    SeedModel.WEATHERSNAPSHOT,
)
_POINT_FAMILY: tuple[SeedModel, ...] = (
    SeedModel.FORECASTPOINT,
    SeedModel.FORECASTPOINTWEATHER,
    SeedModel.FAVOURITE,
)

_CHOICES: list[str] = [m.value for m in SeedModel]

# Coordinates for the seeded ForecastPoints — spaced (0.1° / 300 m apart) so each
# resolves to a distinct (lat_cell, lon_cell, elevation_band) triple, around the
# Verbier detail region.
_FORECAST_POINT_COORDS: tuple[tuple[float, float, float], ...] = (
    (46.10, 7.40, 1500.0),
    (46.20, 7.50, 1800.0),
    (46.30, 7.60, 2100.0),
    (46.40, 7.70, 2400.0),
    (46.50, 7.80, 2700.0),
)


class _Rollback(Exception):
    """Raised inside ``transaction.atomic()`` to force a dry-run rollback."""


class Command(BaseCommand):
    """Seed a database with the ``build_test_data`` dataset via factories.

    Read-only by default; ``--commit`` persists. Exactly one of ``--all``,
    ``--include``, or ``--exclude`` selects which models to seed.
    """

    help = (
        "Seed the DB with the build_test_data dataset using tests/factories.py "
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
    ) -> "tuple[list[tuple[str, str, date, str]], list[tuple[str, date]]]":
        """Build the bulletin and weather coverage plan.

        The region set and per-day danger logic are read from the same eaws_CH
        fixture and helpers as ``build_test_data``, so the two commands produce an
        identical dataset.

        Returns:
            A ``(bulletin_specs, weather_pairs)`` tuple. ``bulletin_specs`` is a
            list of ``(region_id, region_name, target_date, danger_value)``;
            ``weather_pairs`` is a list of ``(region_id, target_date)``.

        """
        region_data = json.loads(_EAWS_CH_FIXTURE.read_text())
        region_name_map: dict[str, str] = {
            d["fields"]["region_id"]: d["fields"]["name"]
            for d in region_data
            if d["model"] == "regions.microregion"
        }

        specs: list[tuple[str, str, date, str]] = []
        for region_id, region_name in region_name_map.items():
            specs.append((region_id, region_name, MAP_DATE, "moderate"))
        for region_id, region_name in DETAIL_REGIONS.items():
            for target_date in APRIL_DATES:
                if target_date == MAP_DATE:
                    continue
                specs.append(
                    (region_id, region_name, target_date, _danger_for_day(target_date))
                )

        weather_pairs = [
            (region_id, target_date) for region_id, _, target_date, _ in specs
        ]
        return specs, weather_pairs

    def _load_micro_regions(
        self, specs: "list[tuple[str, str, date, str]]"
    ) -> "dict[str, MicroRegion]":
        """Load the MicroRegion rows referenced by the coverage plan.

        Args:
            specs: The bulletin coverage plan from ``_coverage``.

        Returns:
            A ``region_id -> MicroRegion`` lookup for the regions that exist in the
            DB. Missing regions are silently omitted; seeders skip them with a log
            warning, matching ``build_test_data``.

        """
        from regions.models import MicroRegion

        region_ids = {region_id for region_id, _, _, _ in specs}
        return MicroRegion.objects.filter(region_id__in=region_ids).in_bulk(
            field_name="region_id"
        )

    # ------------------------------------------------------------------
    # Seeder dispatch
    # ------------------------------------------------------------------

    def _run_seeders(
        self,
        resolved: "set[SeedModel]",
        specs: "list[tuple[str, str, date, str]]",
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
        counts.update(
            self._seed_bulletin_family(
                resolved, specs, weather_pairs, micro_map, verbosity
            )
        )
        counts.update(self._seed_point_family(resolved, verbosity))
        return counts

    def _seed_bulletin_family(
        self,
        resolved: "set[SeedModel]",
        specs: "list[tuple[str, str, date, str]]",
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
            elif model is SeedModel.WEATHERSNAPSHOT:
                counts[model.value] = self._seed_weather_snapshots(
                    weather_pairs, micro_map, verbosity
                )
        return counts

    def _seed_point_family(
        self, resolved: "set[SeedModel]", verbosity: int
    ) -> dict[str, int]:
        """Seed the point-layer models (ForecastPoint/ForecastPointWeather/Favourite).

        Args:
            resolved: Models to seed (already expanded with prerequisites).
            verbosity: Verbosity level.

        Returns:
            A dict of model value -> row count for the models in this family.

        """
        counts: dict[str, int] = {}
        forecast_points: list[ForecastPoint] = []
        for model in _POINT_FAMILY:
            if model not in resolved:
                continue
            if model is SeedModel.FORECASTPOINT:
                forecast_points = self._seed_forecast_points(verbosity)
                counts[model.value] = len(forecast_points)
            elif model is SeedModel.FORECASTPOINTWEATHER:
                counts[model.value] = self._seed_forecast_point_weather(
                    forecast_points, verbosity
                )
            elif model is SeedModel.FAVOURITE:
                counts[model.value] = self._seed_favourites(forecast_points, verbosity)
        return counts

    def _seed_bulletins(
        self, specs: "list[tuple[str, str, date, str]]", verbosity: int
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
        for region_id, region_name, target_date, danger in specs:
            params = _make_bulletin_params(region_id, region_name, target_date, danger)
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
        except Exception as exc:  # noqa: BLE001 — mirror build_test_data: never abort the seed
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
        from bulletins.models import RegionDayRating

        for bulletin in bulletins:
            try:
                apply_bulletin_day_ratings(bulletin)
            except Exception as exc:  # noqa: BLE001 — mirror build_test_data
                logger.warning(
                    "seed_test_data: day rating failed for %s: %s",
                    bulletin.bulletin_id,
                    exc,
                )

        count = RegionDayRating.objects.filter(source_bulletin__in=bulletins).count()
        if verbosity >= 2:
            self.stdout.write(f"  Created {count} RegionDayRating rows")
        return count

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

    def _seed_forecast_points(self, verbosity: int) -> "list[ForecastPoint]":
        """Create the fixed set of ForecastPoints from ``_FORECAST_POINT_COORDS``.

        Args:
            verbosity: Verbosity level.

        Returns:
            The created ForecastPoint instances.

        """
        from tests.factories import ForecastPointFactory

        points = [
            ForecastPointFactory.create(
                latitude=latitude, longitude=longitude, elevation=elevation
            )
            for latitude, longitude, elevation in _FORECAST_POINT_COORDS
        ]

        if verbosity >= 2:
            self.stdout.write(f"  Created {len(points)} ForecastPoint rows")
        return points

    def _seed_forecast_point_weather(
        self, forecast_points: "list[ForecastPoint]", verbosity: int
    ) -> int:
        """Create a ForecastPointWeather per point across every April date.

        Sunrise/sunset/weather-code come from the same helper as the region
        snapshots; the extended daily fields use the factory defaults.

        Args:
            forecast_points: The seeded ForecastPoint instances.
            verbosity: Verbosity level.

        Returns:
            The number of ForecastPointWeather rows created.

        """
        from tests.factories import ForecastPointWeatherFactory

        created = 0
        for point in forecast_points:
            for target_date in APRIL_DATES:
                # region_id is unused by the helper (it derives sunrise/sunset from
                # the date alone); "" keeps the call honest for point weather.
                params = _make_weather_snapshot_params("", target_date)
                ForecastPointWeatherFactory.create(
                    forecast_point=point,
                    valid_for_date=params["valid_for_date"],
                    weather_code=params["weather_code"],
                    sunrise=params["sunrise"],
                    sunset=params["sunset"],
                    fetched_at=params["fetched_at"],
                )
                created += 1

        if verbosity >= 2:
            self.stdout.write(f"  Created {created} ForecastPointWeather rows")
        return created

    def _seed_favourites(
        self, forecast_points: "list[ForecastPoint]", verbosity: int
    ) -> int:
        """Create one Favourite per ForecastPoint, all owned by one seeded user.

        Each Favourite references a seeded ForecastPoint (so its coordinates line
        up with real point weather) rather than letting the factory synthesise a
        fresh point.

        Args:
            forecast_points: The seeded ForecastPoint instances.
            verbosity: Verbosity level.

        Returns:
            The number of Favourite rows created.

        """
        from tests.factories import FavouriteFactory, UserFactory

        user = UserFactory.create()
        created = 0
        for point in forecast_points:
            FavouriteFactory.create(
                user=user,
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
