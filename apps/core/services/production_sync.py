"""
apps/core/services/production_sync.py — Copy provider data production → staging.

Staging runs one web dyno with no scheduler and no task worker (see
``render.yaml``), so its database never ingests a bulletin or a weather
forecast of its own. Until now ``docs/deployment.md`` answered that with
"seed it with a manual ``fetch_bulletins`` run when test data is needed",
which means staging is either empty or months stale on any given day.

This module copies the **provider-derived** tables from the production
database into the local one. It deliberately copies no user data: no
``auth_user``, no ``Account``, no ``PasskeyCredential``, no
``PushSubscription``, no ``Route``, no ``FieldObservation``, no
``RequestLog``, no ``BulletinShare``. Production personal data therefore
never reaches staging at all, which is the only version of this that needs
no anonymisation step to be safe — and that matters here because staging
runs ``config.settings.staging``, whose ``ImmediateBackend`` sends email
inline on the request rather than queueing it to a worker.

Design notes, in the order they bite:

**Staging's schema is normally ahead of production's.** Staging deploys from
``main`` and production from ``release``, so between releases ``main`` may
carry migrations ``release`` has not applied. Reading production through the
ORM would ``SELECT`` every column the *local* model declares, including ones
production does not have yet. Every read here is therefore raw SQL over the
**intersection** of the local model's columns and the columns production
actually reports in ``information_schema``. Columns that exist only locally
fall back to their Django field default, which is why rows are written via
``bulk_create`` (Python-level defaults apply) rather than a raw ``INSERT``.

**Primary keys are not portable.** Every table here has a ``BigAutoField``
pk assigned by insertion order, and the region fixtures are loaded by
natural key with no explicit ``pk`` (see ``bin/build.sh``), so a given
``MicroRegion`` may hold different ids in the two databases. Nothing is
copied by id: every table is upserted on its own natural key, and foreign
keys are translated through id maps built by matching natural keys across
the two databases.

**``created_at`` / ``updated_at`` are not preserved.** Both are
``auto_now_add`` / ``auto_now`` on :class:`~apps.core.models.BaseModel`, and
``bulk_create`` runs ``pre_save`` on them, so a synced row's timestamps are
the time it reached staging. That is the truthful value for a copy, and
every domain timestamp that actually carries meaning (``issued_at``,
``valid_from``, ``target_date``, ``fetched_at``, ``valid_for_date``) is an
ordinary field and copies verbatim.

**Reads are keyset-paginated, never materialised.** ``Bulletin.raw_data``
and ``ForecastCellWeather.hourly_series`` are large JSON columns and
production holds thousands of both, so a plain client-side cursor over
either table is an out-of-memory failure on a starter dyno.

The plan is declared in :data:`SYNC_PLAN`; the command at
``apps/core/management/commands/sync_from_production.py`` is a thin wrapper
around :func:`sync_table`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, TypeVar

from django.apps import apps
from django.db import OperationalError, connections, models

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# The alias ``config/settings/staging.py`` registers for the production
# database. That overlay defines it and nothing else does, so the sync
# cannot be run from production's own settings module: the connection it
# would read from is not configured there.
PRODUCTION_ALIAS = "production"

# Rows fetched from production per round trip. Sized for the widest table in
# the plan (ForecastCellWeather carries an hourly-series JSON blob), not the
# narrowest.
READ_BATCH_SIZE = 500

# Rows handed to a single ``bulk_create``.
WRITE_BATCH_SIZE = 500

# Sentinel start for keyset pagination — above any BigAutoField value.
_MAX_BIGINT = 2**63 - 1

# Waits between reconnection attempts, in seconds — one per retry.
#
# Deliberately long. The first version of this retried three times inside
# 0.7 seconds against a server reporting "the database system is in recovery
# mode", which is Postgres replaying WAL after an unclean shutdown and takes
# tens of seconds. Three instant retries were three guaranteed failures
# (SNOW-734).
#
# Losing a connection mid-copy is an ordinary operating condition for this
# job — the run is minutes long and each side sits idle while the other
# works — so the sync rides it out rather than abandoning a 60,000-row
# table. What it must not do is hammer a database that is trying to recover.
RETRY_BACKOFF_SECONDS: tuple[float, ...] = (5.0, 20.0, 60.0)

# One initial try plus one per backoff interval.
CONNECTION_ATTEMPTS = len(RETRY_BACKOFF_SECONDS) + 1


class ProductionSyncError(Exception):
    """Raised when the sync cannot run safely, or a table cannot be copied."""


def _concrete_field(model: type[models.Model], name: str) -> models.Field:
    """Return a model's concrete field by name.

    ``Options.get_field`` also returns reverse relations, which have no
    ``column`` or ``attname``; this narrows to the real ones so a typo in
    the plan fails here rather than as an ``AttributeError`` mid-copy.

    Args:
        model: The model to look the field up on.
        name: The field's name.

    Returns:
        The concrete field.

    Raises:
        ProductionSyncError: if the name resolves to a reverse relation.

    """
    found = model._meta.get_field(name)
    if not isinstance(found, models.Field):
        raise ProductionSyncError(
            f"{model._meta.label}.{name} is not a concrete field."
        )
    return found


def _column_of(model: type[models.Model], name: str) -> str:
    """Return the physical column backing a model field.

    Args:
        model: The model to look the field up on.
        name: The field's name.

    Returns:
        The column name. Note this differs from the field name wherever
        ``db_column`` is pinned — every ``apps.weather`` model pins its
        table and its ``forecast_point_id`` column to the legacy
        ``bulletins_*`` names.

    """
    return str(_concrete_field(model, name).column)


# ---------------------------------------------------------------------------
# Id maps
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdMap:
    """A production-pk → local-pk translation for one foreign-key target.

    Built by reading the natural key of every row on both sides and joining
    on it, so it is correct whether or not the two databases happened to
    assign matching auto-increment ids.

    Attributes:
        name: The map's name, as referenced by :attr:`TableSpec.remap`.
        model_label: Dotted ``app_label.ModelName`` of the target model.
        natural_key: Field names whose tuple identifies a row across
            databases.
        mapping: The resolved ``{production_pk: local_pk}`` pairs.

    """

    name: str
    model_label: str
    natural_key: tuple[str, ...]
    mapping: dict[int, int] = field(default_factory=dict)

    def resolve(self, production_pk: int | None) -> int | None:
        """Translate a production pk, returning ``None`` when unmapped.

        Args:
            production_pk: The pk as it appears in the production row, or
                ``None`` for a null foreign key.

        Returns:
            The equivalent local pk, or ``None`` when the foreign key was
            already null or the target row does not exist locally.

        """
        if production_pk is None:
            return None
        return self.mapping.get(production_pk)


# ---------------------------------------------------------------------------
# Table plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableSpec:
    """One table's copy rules.

    Attributes:
        model_label: Dotted ``app_label.ModelName`` of the model to copy.
        natural_key: Field names forming the ``ON CONFLICT`` target. Must
            correspond to a real unique constraint or ``bulk_create``
            raises.
        remap: ``{foreign-key attname: id-map name}``. Every entry is
            translated through the named map before the row is written.
        required: Foreign-key attnames that cannot be null. A row whose
            remap yields ``None`` for one of these is skipped rather than
            written with a dangling reference.
        null_out: Foreign-key attnames forced to ``None``, for targets
            deliberately outside the plan.
        provides: Name of the id map this table populates once copied, if
            any. Built after the table is written, so child tables see rows
            this run inserted.
        referenced_by: ``(model_label, field_name)`` naming a foreign key
            that must point at a row for it to be copied — the row filter
            for a table whose contents are only *partly* in scope. The
            subquery runs against production, so it sees the full picture
            regardless of what this database holds.

    """

    model_label: str
    natural_key: tuple[str, ...]
    remap: dict[str, str] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    null_out: tuple[str, ...] = ()
    provides: str | None = None
    referenced_by: tuple[str, str] | None = None

    @property
    def model(self) -> type[models.Model]:
        """Return the model class this spec copies."""
        return apps.get_model(self.model_label)

    @property
    def restriction(self) -> tuple[str, str] | None:
        """Resolve :attr:`referenced_by` to a ``(table, column)`` pair.

        Returns:
            The referencing table and its foreign-key column, or ``None``
            when the whole table is in scope.

        """
        if self.referenced_by is None:
            return None
        label, field_name = self.referenced_by
        model = apps.get_model(label)
        return model._meta.db_table, _column_of(model, field_name)


# Copy order matters: a table's parents appear before it, so the id map a
# child remaps through is populated by the time the child is written.
#
# PipelineRun is deliberately absent. It is telemetry about *production's*
# ingest runs, it has no natural key to upsert on, and
# ``Bulletin.pipeline_run`` is ``null=True, on_delete=SET_NULL`` — so
# nulling the reference on staging loses nothing staging can act on.
#
# BulletinShare and BulletinShareClick are absent for the opposite reason:
# they are user data (a click row foreign-keys RequestLog, which carries IP
# and geo), and no user data crosses this boundary.
SYNC_PLAN: tuple[TableSpec, ...] = (
    TableSpec(
        model_label="bulletins.Bulletin",
        natural_key=("bulletin_id",),
        null_out=("pipeline_run_id",),
        provides="bulletin",
    ),
    TableSpec(
        model_label="weather.ForecastCell",
        natural_key=("lat_cell", "lon_cell", "elevation_band"),
        provides="forecast_cell",
    ),
    TableSpec(
        model_label="bulletins.RegionBulletin",
        natural_key=("bulletin", "region"),
        remap={"bulletin_id": "bulletin", "region_id": "region"},
        required=("bulletin_id", "region_id"),
    ),
    TableSpec(
        model_label="bulletins.RegionDayRating",
        natural_key=("region", "date"),
        # source_bulletin is null=True, so an unmapped one becomes NULL
        # rather than dropping the rating itself.
        remap={"region_id": "region", "source_bulletin_id": "bulletin"},
        required=("region_id",),
    ),
    TableSpec(
        model_label="bulletins.BulletinGrouping",
        natural_key=("bulletin",),
        remap={"bulletin_id": "bulletin"},
        required=("bulletin_id",),
    ),
    TableSpec(
        model_label="weather.WeatherSnapshot",
        natural_key=("region", "valid_for_date"),
        remap={"region_id": "region"},
        required=("region_id",),
    ),
    TableSpec(
        model_label="weather.ForecastCellWeather",
        natural_key=("forecast_cell", "valid_for_date"),
        remap={"forecast_cell_id": "forecast_cell"},
        required=("forecast_cell_id",),
    ),
    TableSpec(
        model_label="weather.ForecastCellWeatherHistory",
        natural_key=("forecast_cell", "valid_for_date", "issued_date"),
        remap={"forecast_cell_id": "forecast_cell"},
        required=("forecast_cell_id",),
    ),
    # --- Curated resort estate -------------------------------------------
    # Resorts are editable data owned by each environment's database, not a
    # fixture reloaded on deploy (docs/decisions/resorts-are-editable-data.md).
    # That ADR forbids re-loading resorts.json over production's edits; this
    # runs the other way, carrying production's curated set *to* staging,
    # which is the only route those edits have ever had — St. Moritz's
    # coordinate, for one, was placed by hand in the production map editor
    # and reaches staging by nothing else.
    #
    # Keyed on ``uuid``: Resort, Location and ResortLocation declare no
    # domain-unique field, and ``BaseModel.uuid`` is exactly the stable,
    # non-enumerable identity to fall back on. Nothing in the codebase ever
    # reassigns it, so a row copied today updates the same row tomorrow.
    TableSpec(
        model_label="regions.Resort",
        natural_key=("uuid",),
        remap={"region_id": "region", "forecast_point_id": "forecast_cell"},
        required=("region_id",),
        provides="resort",
    ),
    # locations_location is a MIXED table and the one genuine hazard in this
    # plan. A Location is a resort's village/mid-station/peak — curated — but
    # apps/favourites/services.py and apps/observations/views.py also mint one
    # per saved pin and per field report, from user input. Those are somebody's
    # positions, and copying them would break the property the whole sync rests
    # on. ``referenced_by`` restricts the read to rows a ResortLocation points
    # at, which is a structural test of "is this curated", not a heuristic on
    # a text field.
    TableSpec(
        model_label="locations.Location",
        natural_key=("uuid",),
        remap={"forecast_cell_id": "forecast_cell"},
        provides="location",
        referenced_by=("locations.ResortLocation", "location"),
    ),
    TableSpec(
        model_label="locations.ResortLocation",
        natural_key=("uuid",),
        remap={"resort_id": "resort", "location_id": "location"},
        required=("resort_id", "location_id"),
    ),
)

# Tables whose contents are only partly in scope: every plan entry naming one
# MUST carry a ``referenced_by`` restriction, or the copy sweeps up user data.
# Enforced by tests/core/services/test_production_sync.py.
MIXED_TABLES: frozenset[str] = frozenset({"locations.Location"})

# Id maps the plan remaps through. ``bulletin`` and ``forecast_cell`` are
# rebuilt after their own tables are copied (``TableSpec.provides``);
# ``region`` is never copied — MicroRegion rows are fixture-backed and
# ``build.sh`` loads the same fixtures into both databases — so its map is
# built up front by matching ``region_id`` across the two.
ID_MAP_SPECS: tuple[IdMap, ...] = (
    IdMap(name="region", model_label="regions.MicroRegion", natural_key=("region_id",)),
    IdMap(
        name="bulletin",
        model_label="bulletins.Bulletin",
        natural_key=("bulletin_id",),
    ),
    IdMap(
        name="forecast_cell",
        model_label="weather.ForecastCell",
        natural_key=("lat_cell", "lon_cell", "elevation_band"),
    ),
    IdMap(name="resort", model_label="regions.Resort", natural_key=("uuid",)),
    IdMap(name="location", model_label="locations.Location", natural_key=("uuid",)),
)


@dataclass
class TableResult:
    """Per-table outcome, for the command's summary.

    Attributes:
        label: The model label this result describes.
        read: Rows read from production.
        written: Rows written locally. Postgres does not distinguish an
            insert from an update in an upsert, so this is the combined
            count.
        skipped: Rows dropped because a required foreign key did not
            resolve to a local row.

    """

    label: str
    read: int = 0
    written: int = 0
    skipped: int = 0


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


def check_safe_to_write(*, site_environment: str) -> None:
    """Refuse to run anywhere the write target might be production.

    Three independent conditions have to hold. The structural one is that
    the ``production`` alias is only ever registered by
    ``config/settings/staging.py``, so production's own settings module
    cannot reach this code path at all.

    Args:
        site_environment: ``settings.SITE_ENVIRONMENT`` for this process.

    Raises:
        ProductionSyncError: if the production alias is unconfigured, if it
            resolves to the same database as ``default``, or if the process
            believes it is production.

    """
    if PRODUCTION_ALIAS not in connections.databases:
        raise ProductionSyncError(
            f"No {PRODUCTION_ALIAS!r} database configured. Set "
            "PRODUCTION_DATABASE_URL and run under "
            "DJANGO_SETTINGS_MODULE=config.settings.staging."
        )

    if site_environment == "production":
        raise ProductionSyncError(
            "SITE_ENVIRONMENT is 'production'. This command only ever writes "
            "to a non-production database."
        )

    if _dsn_identity("default") == _dsn_identity(PRODUCTION_ALIAS):
        raise ProductionSyncError(
            "DATABASE_URL and PRODUCTION_DATABASE_URL point at the same "
            "database. The sync would read and write the same rows."
        )


def _dsn_identity(alias: str) -> tuple[str, str, str]:
    """Return the (host, port, name) triple identifying a configured database.

    Compares connection targets without going near the password.

    Args:
        alias: The ``DATABASES`` alias to describe.

    Returns:
        The host, port and database name, all as strings.

    """
    settings_dict = connections.databases[alias]
    return (
        str(settings_dict.get("HOST") or ""),
        str(settings_dict.get("PORT") or ""),
        str(settings_dict.get("NAME") or ""),
    )


# ---------------------------------------------------------------------------
# Schema intersection
# ---------------------------------------------------------------------------


def with_reconnect(alias: str, what: str, operation: Callable[[], _T]) -> _T:
    """Run ``operation``, reconnecting and retrying if the connection dies.

    Backs off between attempts per :data:`RETRY_BACKOFF_SECONDS`. The waits
    are the point: a database in recovery is unreachable for tens of
    seconds, so retrying immediately just spends the attempts.

    Callers must be idempotent, because a retry re-runs the whole
    operation. Both callers here are: reads are self-contained selects under
    keyset pagination, and writes are natural-key upserts.

    Args:
        alias: The database alias to close before retrying.
        what: Short label for the log line ("read" / "write").
        operation: The work to run. Called again on each retry.

    Returns:
        Whatever ``operation`` returns.

    Raises:
        ProductionSyncError: once the attempts are exhausted.

    """
    last: OperationalError | None = None
    for attempt in range(1, CONNECTION_ATTEMPTS + 1):
        try:
            return operation()
        except OperationalError as exc:
            last = exc
            if attempt == CONNECTION_ATTEMPTS:
                break
            wait = RETRY_BACKOFF_SECONDS[attempt - 1]
            logger.warning(
                "production_sync: %r %s connection lost on attempt %d/%d (%s); "
                "reconnecting in %.0fs",
                alias,
                what,
                attempt,
                CONNECTION_ATTEMPTS,
                exc,
                wait,
            )
            # Discards the dead socket; the next use dials a fresh one.
            connections[alias].close()
            time.sleep(wait)

    raise ProductionSyncError(
        f"Lost the {alias!r} {what} connection {CONNECTION_ATTEMPTS} times "
        f"in a row: {last}"
    )


def fetch_all(
    alias: str, sql: str, params: list[Any] | None = None
) -> list[tuple[Any, ...]]:
    """Run a read query, reconnecting if the connection died under it.

    Every read in this module goes through here, and each one opens its own
    short-lived cursor. That is deliberate: holding a cursor open across the
    writes between batches is what left the production connection idle long
    enough to be reaped in the first place.

    Reconnecting is safe because every caller is a self-contained ``SELECT``
    outside any transaction — keyset pagination means a re-run of the same
    query returns the same rows, so a retry cannot skip or duplicate work.

    Args:
        alias: The database alias to read from.
        sql: The query, with ``%s`` placeholders.
        params: Query parameters, if any.

    Returns:
        Every row the query returned.

    Raises:
        ProductionSyncError: if the connection could not be re-established
            within :data:`CONNECTION_ATTEMPTS` tries.

    """

    def run() -> list[tuple[Any, ...]]:
        with connections[alias].cursor() as cursor:
            cursor.execute(sql, params or [])
            rows: list[tuple[Any, ...]] = cursor.fetchall()
            return rows

    return with_reconnect(alias, "read", run)


def production_columns(table: str) -> set[str]:
    """Return the column names production actually has for ``table``.

    Staging deploys from ``main`` and production from ``release``, so the
    local model may declare columns production has not migrated yet.
    Selecting those would fail; this is how they are excluded.

    Args:
        table: The physical table name (``Model._meta.db_table``).

    Returns:
        The column names present in production's ``public`` schema.

    Raises:
        ProductionSyncError: if the table does not exist in production.

    """
    rows = fetch_all(
        PRODUCTION_ALIAS,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        [table],
    )
    columns = {row[0] for row in rows}

    if not columns:
        raise ProductionSyncError(
            f"Table {table!r} does not exist in the production database."
        )
    return columns


def shared_fields(model: type[models.Model], available: set[str]) -> list[models.Field]:
    """Return the concrete fields whose column exists in both databases.

    The primary key is excluded: pks are not portable between the two
    databases, and every table is upserted on its natural key instead.

    Args:
        model: The local model being copied.
        available: Column names production reports for the model's table.

    Returns:
        The fields to read, in model declaration order.

    """
    return [
        f
        for f in model._meta.concrete_fields
        if str(f.column) in available and not f.primary_key
    ]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def read_rows(
    *,
    table: str,
    columns: list[str],
    since: datetime | None,
    restrict: tuple[str, str] | None = None,
    batch_size: int = READ_BATCH_SIZE,
) -> Iterator[list[tuple[Any, ...]]]:
    """Stream production rows newest-id-first, one batch at a time.

    Uses keyset pagination on ``id`` rather than a server-side cursor, so
    memory stays bounded regardless of table size and regardless of the
    driver's cursor behaviour. Descending order means rows created while the
    sync runs sort ahead of the cursor and are never re-visited — the same
    reasoning as :func:`apps.core.command_iteration.iterate_rows`.

    Args:
        table: Physical table name in production.
        columns: Column names to select. ``id`` is prepended automatically.
        since: Only read rows whose ``updated_at`` is at or after this
            instant. ``None`` reads the whole table.
        restrict: ``(table, column)`` limiting the read to rows whose ``id``
            appears in that column — the filter for a partly-in-scope table.
            Evaluated inside production, so no id list crosses the wire.
        batch_size: Rows per round trip.

    Yields:
        Batches of rows as tuples, each row prefixed with its production
        ``id``.

    """
    selected = ", ".join(f'"{name}"' for name in ["id", *columns])
    clauses = ["id < %s"]
    if since is not None:
        clauses.append("updated_at >= %s")
    if restrict is not None:
        via_table, via_column = restrict
        # S608: both identifiers are resolved from model metadata by
        # ``TableSpec.restriction``, never from input.
        subquery = f'SELECT "{via_column}" FROM "{via_table}"'  # noqa: S608
        clauses.append(f"id IN ({subquery})")
    predicate = " AND ".join(clauses)
    # S608: every identifier interpolated here comes from model metadata
    # (``_meta.db_table`` / ``Field.column``) and ``batch_size`` is coerced to
    # int — no user input reaches the string. Values stay parameterised.
    sql = (
        f'SELECT {selected} FROM "{table}" WHERE {predicate} '  # noqa: S608
        f"ORDER BY id DESC LIMIT {int(batch_size)}"
    )

    # One cursor per batch, deliberately. This used to hold a single cursor
    # open for the whole table, and because this is a generator the `yield`
    # suspended *inside* it — so the production connection sat idle through
    # every staging write in between. Over a 60,000-row table that is minutes
    # of idle time, and a managed Postgres reaps an idle connection: the next
    # execute() then died with "SSL error: unexpected eof while reading"
    # (SNOW-734). Keyset pagination makes each batch an independent query, so
    # per-batch cursors cost nothing and there is no long-lived state to lose.
    cursor_id = _MAX_BIGINT
    while True:
        params: list[Any] = [cursor_id]
        if since is not None:
            params.append(since)
        rows = fetch_all(PRODUCTION_ALIAS, sql, params)
        if not rows:
            return
        yield rows
        cursor_id = rows[-1][0]


# ---------------------------------------------------------------------------
# Id-map construction
# ---------------------------------------------------------------------------


def build_id_map(spec: IdMap) -> IdMap:
    """Return a copy of ``spec`` with its mapping resolved.

    Reads the natural key of every row from both databases and joins on it.
    Every target is small — 149 Swiss micro-regions plus the other three
    countries, a few dozen forecast cells, and one narrow row per bulletin —
    so both sides are read in full rather than paginated.

    Args:
        spec: The map to populate.

    Returns:
        A new :class:`IdMap` with :attr:`IdMap.mapping` filled in. The input
        is left untouched, so the module-level :data:`ID_MAP_SPECS` stay
        clean between runs.

    """
    model = apps.get_model(spec.model_label)
    columns = [_column_of(model, name) for name in spec.natural_key]
    selected = ", ".join(f'"{name}"' for name in ["id", *columns])
    # S608: identifiers come from model metadata, not user input.
    sql = f'SELECT {selected} FROM "{model._meta.db_table}"'  # noqa: S608

    local: dict[tuple[Any, ...], int] = {}
    for row in fetch_all("default", sql):
        local[tuple(row[1:])] = row[0]

    mapping: dict[int, int] = {}
    production_rows = 0
    for row in fetch_all(PRODUCTION_ALIAS, sql):
        production_rows += 1
        local_pk = local.get(tuple(row[1:]))
        if local_pk is not None:
            mapping[row[0]] = local_pk

    logger.info(
        "production_sync: id map %r resolved %d of %d production row(s)",
        spec.name,
        len(mapping),
        production_rows,
    )
    return replace(spec, mapping=mapping)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def sync_table(
    spec: TableSpec,
    *,
    id_maps: dict[str, IdMap],
    since: datetime | None,
    commit: bool,
) -> TableResult:
    """Copy one table from production into the local database.

    **Each batch commits on its own; there is no transaction around the
    table.** There used to be, so that a failed table rolled back cleanly.
    That put 59,085 rows into a single transaction on the first real run and
    the staging server came back reporting ``FATAL: the database system is
    in recovery mode`` (SNOW-734) — a transaction that large is a resource
    problem on a small instance, and rolling back was never worth it anyway:
    every table upserts on its natural key, so partial progress is
    re-applied harmlessly by the next run rather than being something to
    undo.

    Args:
        spec: The table's copy rules.
        id_maps: The available foreign-key translations, by name.
        since: Only copy rows whose production ``updated_at`` is at or after
            this instant; ``None`` copies the whole table.
        commit: When ``False``, everything is read and translated but
            nothing is written — the returned counts are what *would* be
            written.

    Returns:
        The per-table counts.

    Raises:
        ProductionSyncError: if the table is missing from production, or a
            required id map was not built.

    """
    model = spec.model
    available = production_columns(model._meta.db_table)
    fields = shared_fields(model, available)
    columns = [str(f.column) for f in fields]
    attnames = [f.attname for f in fields]

    for map_name in spec.remap.values():
        if map_name not in id_maps:
            raise ProductionSyncError(
                f"{spec.model_label}: id map {map_name!r} was not built."
            )

    update_fields = _update_fields(model, fields, spec)
    result = TableResult(label=spec.model_label)
    pending: list[models.Model] = []

    objects = _translate_rows(
        spec,
        attnames=attnames,
        columns=columns,
        since=since,
        id_maps=id_maps,
        result=result,
    )
    for obj in objects:
        pending.append(obj)
        if len(pending) >= WRITE_BATCH_SIZE:
            result.written += _write(
                model, pending, spec=spec, update_fields=update_fields, commit=commit
            )
            pending = []

    if pending:
        result.written += _write(
            model, pending, spec=spec, update_fields=update_fields, commit=commit
        )

    logger.info(
        "production_sync: %s read=%d written=%d skipped=%d",
        spec.model_label,
        result.read,
        result.written,
        result.skipped,
    )
    return result


def _translate_rows(
    spec: TableSpec,
    *,
    attnames: list[str],
    columns: list[str],
    since: datetime | None,
    id_maps: dict[str, IdMap],
    result: TableResult,
) -> Iterator[models.Model]:
    """Stream production rows as unsaved local instances.

    Counts every row read and every row skipped onto ``result`` as it goes,
    so the caller only has to count what it writes.

    Args:
        spec: The table's copy rules.
        attnames: Model attribute names matching ``columns``, in order.
        columns: Production column names to select.
        since: Update-window start, or ``None`` for the whole table.
        id_maps: The available foreign-key translations, by name.
        result: The running counts, mutated in place.

    Yields:
        Unsaved instances of the spec's model, foreign keys already
        translated to local ids.

    """
    model = spec.model
    for batch in read_rows(
        table=model._meta.db_table,
        columns=columns,
        since=since,
        restrict=spec.restriction,
    ):
        for row in batch:
            result.read += 1
            values = {
                name: _coerce_aware(value)
                for name, value in zip(attnames, row[1:], strict=True)
            }
            if not _apply_remaps(values, spec=spec, id_maps=id_maps):
                result.skipped += 1
                continue
            for attname in spec.null_out:
                values[attname] = None
            yield model(**values)


def _coerce_aware(value: Any) -> Any:
    """Stamp UTC onto a naive datetime, passing everything else through.

    Raw cursors return whatever the driver hands back. ``psycopg`` returns
    aware datetimes for the ``timestamptz`` columns Django creates under
    ``USE_TZ``, so on the real production → staging path this never fires.
    SQLite's driver does not, and the test suite runs on SQLite — without
    this, every copied row raises Django's naive-datetime ``RuntimeWarning``
    and the project's "all datetimes carry tzinfo" rule holds only by
    accident of which database backend is underneath.

    Args:
        value: A value read from a production row.

    Returns:
        The value, with UTC attached if it was a naive datetime.

    """
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _apply_remaps(
    values: dict[str, Any], *, spec: TableSpec, id_maps: dict[str, IdMap]
) -> bool:
    """Translate a row's foreign keys in place.

    Args:
        values: The row's ``{attname: value}`` pairs, mutated in place.
        spec: The table's copy rules.
        id_maps: The available translations, by name.

    Returns:
        ``True`` if the row is writable; ``False`` if a required foreign key
        did not resolve to a local row and the row must be skipped.

    """
    for attname, map_name in spec.remap.items():
        if attname not in values:
            continue
        resolved = id_maps[map_name].resolve(values[attname])
        if resolved is None and attname in spec.required:
            return False
        values[attname] = resolved
    return True


def _update_fields(
    model: type[models.Model], fields: list[models.Field], spec: TableSpec
) -> list[str]:
    """Return the field names an upsert should overwrite on conflict.

    The natural key is excluded — it is the conflict target, and Postgres
    rejects a conflict-target column in the ``DO UPDATE SET`` clause — as is
    ``created_at``, which records when the row first reached *this*
    database.

    Args:
        model: The model being copied.
        fields: The fields shared with production.
        spec: The table's copy rules, for its natural key.

    Returns:
        Field names to update, in declaration order.

    """
    conflict_attnames = {
        _concrete_field(model, name).attname for name in spec.natural_key
    }
    return [
        f.name
        for f in fields
        if f.attname not in conflict_attnames and f.name != "created_at"
    ]


def _write(
    model: type[models.Model],
    objects: list[models.Model],
    *,
    spec: TableSpec,
    update_fields: list[str],
    commit: bool,
) -> int:
    """Upsert one batch on the table's natural key.

    Args:
        model: The model being copied.
        objects: Unsaved instances to write.
        spec: The table's copy rules, for its natural key.
        update_fields: Field names to overwrite when a row already exists.
        commit: When ``False``, returns the count without writing.

    Returns:
        The number of rows written, or that would have been written.

    """
    if not objects:
        return 0
    if not commit:
        return len(objects)

    def run() -> int:
        # ``_default_manager`` rather than ``objects``: the model is resolved
        # from the plan at runtime, so it is only known as ``type[Model]``,
        # which declares no manager attribute.
        model._default_manager.bulk_create(
            objects,
            update_conflicts=True,
            unique_fields=list(spec.natural_key),
            update_fields=update_fields,
            batch_size=WRITE_BATCH_SIZE,
        )
        return len(objects)

    # Each batch is its own committed unit — see sync_table's docstring for
    # why there is no transaction around the table.
    return with_reconnect("default", "write", run)
