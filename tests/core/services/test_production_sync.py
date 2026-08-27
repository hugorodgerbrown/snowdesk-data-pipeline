"""
tests/core/services/test_production_sync.py — Tests for production_sync.

Covers:
  - Plan integrity: every ``natural_key`` is a real unique constraint, every
    ``remap`` names a real foreign key and a real id map, ``required`` is a
    subset of ``remap``, ``null_out`` names a nullable foreign key, and a
    table that provides an id map appears before the tables that consume it.
  - ``check_safe_to_write``: the three refusals.
  - ``shared_fields`` / ``_update_fields`` / ``_apply_remaps`` /
    ``_coerce_aware``: the pure translation rules.
  - A full self-copy: with the production alias pointed at the test database
    and ``production_columns`` stubbed, every table in the plan copies onto
    itself without duplicating a row or breaking a foreign key.

The self-copy is the closest a SQLite test suite can get to the real
Postgres → Postgres path. It exercises the read SQL, the keyset pagination,
the id maps, the remapping and the natural-key upsert; what it cannot
exercise is ``information_schema`` (SQLite has none) and the column
intersection that depends on it, which is covered by stubbing instead.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from django.apps import apps
from django.db import OperationalError, connections, models
from django.utils import timezone

from apps.bulletins.models import Bulletin, RegionBulletin
from apps.core.services import production_sync
from apps.core.services.production_sync import (
    CONNECTION_ATTEMPTS,
    ID_MAP_SPECS,
    MIXED_TABLES,
    SYNC_PLAN,
    IdMap,
    ProductionSyncError,
    TableResult,
    TableSpec,
    _apply_remaps,
    _coerce_aware,
    _update_fields,
    build_id_map,
    check_safe_to_write,
    fetch_all,
    shared_fields,
    sync_table,
)
from apps.locations.models import Location, ResortLocation
from tests.factories import (
    BulletinFactory,
    BulletinGroupingFactory,
    ForecastCellFactory,
    ForecastCellWeatherFactory,
    ForecastCellWeatherHistoryFactory,
    LocationFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
    ResortFactory,
    ResortLocationFactory,
    WeatherSnapshotFactory,
)

# ---------------------------------------------------------------------------
# Plan integrity — no database required
# ---------------------------------------------------------------------------


def _unique_key_sets(model: type[models.Model]) -> list[frozenset[str]]:
    """Return every unique field-name set declared on a model."""
    keys = [frozenset(names) for names in model._meta.unique_together]
    keys.extend(frozenset([f.name]) for f in model._meta.concrete_fields if f.unique)
    return keys


@pytest.mark.parametrize("spec", SYNC_PLAN, ids=lambda s: s.model_label)
def test_natural_key_is_a_real_unique_constraint(spec: TableSpec) -> None:
    """Each table's conflict target matches a unique constraint.

    ``bulk_create(update_conflicts=True)`` raises at runtime if it does not,
    which on the cron job would mean a red run rather than a caught typo.
    """
    assert frozenset(spec.natural_key) in _unique_key_sets(spec.model)


@pytest.mark.parametrize("spec", SYNC_PLAN, ids=lambda s: s.model_label)
def test_remaps_name_real_foreign_keys_and_real_maps(spec: TableSpec) -> None:
    """Every remap key is a concrete FK attname and names a declared map."""
    fk_attnames = {f.attname for f in spec.model._meta.concrete_fields if f.is_relation}
    map_names = {m.name for m in ID_MAP_SPECS}
    for attname, map_name in spec.remap.items():
        assert attname in fk_attnames, f"{spec.model_label}.{attname}"
        assert map_name in map_names, f"{spec.model_label} -> {map_name}"


@pytest.mark.parametrize("spec", SYNC_PLAN, ids=lambda s: s.model_label)
def test_required_keys_are_remapped_and_non_nullable(spec: TableSpec) -> None:
    """``required`` only lists remapped, genuinely non-nullable keys.

    A required entry that is actually nullable would drop rows the database
    would have accepted; one that is not remapped could never be null in the
    first place.
    """
    by_attname = {f.attname: f for f in spec.model._meta.concrete_fields}
    for attname in spec.required:
        assert attname in spec.remap
        assert not by_attname[attname].null


@pytest.mark.parametrize("spec", SYNC_PLAN, ids=lambda s: s.model_label)
def test_null_out_targets_are_nullable(spec: TableSpec) -> None:
    """A key forced to NULL must be a nullable foreign key."""
    by_attname = {f.attname: f for f in spec.model._meta.concrete_fields}
    for attname in spec.null_out:
        field = by_attname[attname]
        assert field.is_relation
        assert field.null, f"{spec.model_label}.{attname} is NOT NULL"


def test_providers_come_before_their_consumers() -> None:
    """A table is copied before any table that remaps through its id map.

    Otherwise a child's id map is built from rows the parent has not written
    yet, and every child row is skipped on a first load.
    """
    provided_at: dict[str, int] = {}
    for index, spec in enumerate(SYNC_PLAN):
        for map_name in spec.remap.values():
            provider = provided_at.get(map_name)
            # ``region`` is never copied — it is fixture-backed on both sides.
            if map_name != "region":
                assert provider is not None, f"{spec.model_label} -> {map_name}"
                assert provider < index
        if spec.provides is not None:
            provided_at[spec.provides] = index


@pytest.mark.parametrize("spec", SYNC_PLAN, ids=lambda s: s.model_label)
def test_mixed_tables_are_restricted(spec: TableSpec) -> None:
    """A partly-in-scope table is never copied whole.

    ``locations.Location`` holds a resort's village/mid-station/peak
    alongside a row per saved favourite and per field report, both minted
    from user input (``apps/favourites/services.py``,
    ``apps/observations/views.py``). Copying it unrestricted would put
    somebody's positions on staging — the one thing this sync must not do.
    """
    if spec.model_label in MIXED_TABLES:
        assert spec.referenced_by is not None, (
            f"{spec.model_label} is a mixed table and must set referenced_by"
        )


@pytest.mark.parametrize("spec", SYNC_PLAN, ids=lambda s: s.model_label)
def test_restrictions_resolve_to_a_real_table_and_column(spec: TableSpec) -> None:
    """``referenced_by`` names a real model and a real foreign key."""
    if spec.referenced_by is None:
        return
    table, column = spec.restriction or (None, None)
    assert table and column
    referencing = apps.get_model(spec.referenced_by[0])
    assert table == referencing._meta.db_table
    assert column in {str(f.column) for f in referencing._meta.concrete_fields}


def test_plan_copies_no_user_data() -> None:
    """No model in the plan reaches a user, directly or by foreign key.

    This is the invariant the whole design rests on: because nothing
    personal is copied, the sync needs no anonymisation step and is safe to
    run unattended against a staging box that sends email inline.
    """
    forbidden = {
        "auth.User",
        "accounts.Account",
        "accounts.Subscription",
        "accounts.PasskeyCredential",
        "accounts.PushSubscription",
        "core.RequestLog",
        "favourites.Favourite",
        "observations.FieldObservation",
        "routes.Route",
    }
    for spec in SYNC_PLAN:
        assert spec.model_label not in forbidden
        for f in spec.model._meta.concrete_fields:
            if not f.is_relation or f.related_model is None:
                continue
            if f.attname in spec.null_out:
                continue
            assert f.related_model._meta.label not in forbidden, (
                f"{spec.model_label}.{f.attname} reaches user data"
            )


# ---------------------------------------------------------------------------
# check_safe_to_write
# ---------------------------------------------------------------------------


def test_check_safe_to_write_requires_the_production_alias() -> None:
    """With no production alias configured, the sync refuses to start."""
    with pytest.raises(ProductionSyncError, match="No 'production' database"):
        check_safe_to_write(site_environment="staging")


def test_check_safe_to_write_refuses_on_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process that believes it is production never writes."""
    monkeypatch.setitem(
        connections.databases, "production", {"HOST": "prod", "PORT": "", "NAME": "a"}
    )
    with pytest.raises(ProductionSyncError, match="SITE_ENVIRONMENT"):
        check_safe_to_write(site_environment="production")


def test_check_safe_to_write_refuses_when_both_urls_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading and writing the same database is refused, not attempted."""
    same = dict(connections.databases["default"])
    monkeypatch.setitem(connections.databases, "production", same)
    with pytest.raises(ProductionSyncError, match="same database"):
        check_safe_to_write(site_environment="staging")


def test_check_safe_to_write_passes_for_a_distinct_staging_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A distinct, non-production target is allowed."""
    monkeypatch.setitem(
        connections.databases,
        "production",
        {"HOST": "prod.example", "PORT": "5432", "NAME": "snowdesk"},
    )
    check_safe_to_write(site_environment="staging")


# ---------------------------------------------------------------------------
# Translation rules
# ---------------------------------------------------------------------------


def test_shared_fields_drops_the_pk_and_unknown_columns() -> None:
    """Only columns present in both databases are read, never the pk."""
    available = {"id", "bulletin_id", "source", "issued_at"}
    names = {f.name for f in shared_fields(Bulletin, available)}
    assert names == {"bulletin_id", "source", "issued_at"}


def test_update_fields_excludes_the_conflict_target_and_created_at() -> None:
    """Postgres rejects a conflict-target column in DO UPDATE SET."""
    spec = SYNC_PLAN[0]
    available = {str(f.column) for f in Bulletin._meta.concrete_fields}
    fields = shared_fields(Bulletin, available)
    updated = _update_fields(Bulletin, fields, spec)
    assert "bulletin_id" not in updated
    assert "created_at" not in updated
    assert "raw_data" in updated
    assert "updated_at" in updated


def test_apply_remaps_translates_a_known_id() -> None:
    """A resolvable foreign key is rewritten to its local pk."""
    id_maps = {
        "bulletin": IdMap("bulletin", "bulletins.Bulletin", ("bulletin_id",), {7: 3})
    }
    spec = TableSpec(
        model_label="bulletins.BulletinGrouping",
        natural_key=("bulletin",),
        remap={"bulletin_id": "bulletin"},
        required=("bulletin_id",),
    )
    values = {"bulletin_id": 7}
    assert _apply_remaps(values, spec=spec, id_maps=id_maps) is True
    assert values["bulletin_id"] == 3


def test_apply_remaps_skips_a_row_with_an_unresolvable_required_key() -> None:
    """A missing parent drops the row rather than writing a dangling FK."""
    id_maps = {
        "bulletin": IdMap("bulletin", "bulletins.Bulletin", ("bulletin_id",), {})
    }
    spec = TableSpec(
        model_label="bulletins.BulletinGrouping",
        natural_key=("bulletin",),
        remap={"bulletin_id": "bulletin"},
        required=("bulletin_id",),
    )
    assert _apply_remaps({"bulletin_id": 7}, spec=spec, id_maps=id_maps) is False


def test_apply_remaps_nulls_an_unresolvable_optional_key() -> None:
    """An unmapped nullable parent becomes NULL, keeping the child row.

    RegionDayRating.source_bulletin is the real case: losing the link to the
    source bulletin is acceptable, losing the rating is not.
    """
    id_maps = {
        "region": IdMap("region", "regions.MicroRegion", ("region_id",), {4: 9}),
        "bulletin": IdMap("bulletin", "bulletins.Bulletin", ("bulletin_id",), {}),
    }
    spec = next(s for s in SYNC_PLAN if s.model_label == "bulletins.RegionDayRating")
    values = {"region_id": 4, "source_bulletin_id": 99}
    assert _apply_remaps(values, spec=spec, id_maps=id_maps) is True
    assert values == {"region_id": 9, "source_bulletin_id": None}


def test_coerce_aware_stamps_utc_on_a_naive_datetime() -> None:
    """A naive datetime from a raw cursor gains UTC; nothing else changes."""
    assert _coerce_aware(datetime(2026, 4, 8, 7)) == datetime(2026, 4, 8, 7, tzinfo=UTC)
    already = datetime(2026, 4, 8, 7, tzinfo=UTC)
    assert _coerce_aware(already) is already
    assert _coerce_aware(date(2026, 4, 8)) == date(2026, 4, 8)
    assert _coerce_aware("CH-4115") == "CH-4115"
    assert _coerce_aware(None) is None


# ---------------------------------------------------------------------------
# Connection resilience
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_fetch_all_reconnects_after_a_dropped_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection that dies mid-run is re-dialled, not surfaced.

    This is the failure SNOW-734 exists for: a managed Postgres reaps a
    connection that has sat idle while the other database was being written
    to, and the next query dies with "SSL error: unexpected eof". The run
    must ride that out rather than abandon a 60,000-row table.
    """
    calls: list[str] = []
    real_cursor = connections["default"].cursor
    closed: list[bool] = []

    def flaky_cursor(*args: object, **kwargs: object) -> Any:
        calls.append("cursor")
        if len(calls) == 1:
            raise OperationalError("consuming input failed: SSL error: unexpected eof")
        return real_cursor(*args, **kwargs)

    monkeypatch.setattr(connections["default"], "cursor", flaky_cursor)
    monkeypatch.setattr(connections["default"], "close", lambda: closed.append(True))

    rows = fetch_all("default", "SELECT 1")

    assert rows == [(1,)]
    assert len(calls) == 2, "did not retry after the connection dropped"
    assert closed, "did not discard the dead connection before retrying"


@pytest.mark.django_db
def test_fetch_all_gives_up_after_the_attempt_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuinely unreachable database fails loudly instead of looping.

    The retry is for a reaped connection, not for a database that is down —
    an unbounded loop there would hang the nightly cron job silently.
    """
    attempts: list[int] = []

    def always_dead(*args: object, **kwargs: object) -> Any:
        attempts.append(1)
        raise OperationalError("connection refused")

    monkeypatch.setattr(connections["default"], "cursor", always_dead)
    monkeypatch.setattr(connections["default"], "close", lambda: None)

    with pytest.raises(ProductionSyncError, match="Lost the 'default' connection"):
        fetch_all("default", "SELECT 1")

    assert len(attempts) == CONNECTION_ATTEMPTS


@pytest.mark.django_db
def test_read_rows_holds_no_cursor_between_batches(
    self_copy: None, seeded_bulletins: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each batch opens its own cursor, so none is held across the writes.

    The original code opened one cursor per *table* and, being a generator,
    suspended inside it at every yield — leaving the production connection
    idle through each staging write. Counting cursors is the only way to
    assert the fix from the outside: the row results are identical either
    way, which is precisely why the bug reached production.
    """
    cursors: list[int] = []
    real = production_sync.fetch_all

    def counting(
        alias: str, sql: str, params: list | None = None
    ) -> list[tuple[Any, ...]]:
        cursors.append(1)
        return real(alias, sql, params)

    monkeypatch.setattr(production_sync, "fetch_all", counting)

    batches = list(
        production_sync.read_rows(
            table="bulletins_bulletin",
            columns=["bulletin_id"],
            since=None,
            batch_size=1,
        )
    )

    # batch_size=1 over N rows means N batches plus the empty terminator, and
    # one fetch_all call each — never one cursor spanning them all.
    assert len(batches) >= 2
    assert len(cursors) == len(batches) + 1


# ---------------------------------------------------------------------------
# Self-copy — the whole engine, against the test database
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_bulletins(db: None) -> None:
    """Build one row in every table the plan copies, with its parents.

    Small on purpose: the assertions are about the copy machinery, not about
    volume. What matters is that each table has rows, that the foreign keys
    span more than one parent, and that both weather tables hang off cells
    with distinct natural keys.
    """
    regions = [
        MicroRegionFactory.create(region_id=f"CH-41{n:02d}", slug=f"region-{n}")
        for n in range(3)
    ]
    cells = [
        ForecastCellFactory.create(latitude=46.0 + n, longitude=7.0 + n)
        for n in range(3)
    ]
    today = timezone.localdate()

    for index, region in enumerate(regions):
        bulletin = BulletinFactory.create(bulletin_id=f"CH-bulletin-{index}")
        RegionBulletinFactory.create(bulletin=bulletin, region=region)
        BulletinGroupingFactory.create(bulletin=bulletin)
        RegionDayRatingFactory.create(
            region=region,
            date=today - timedelta(days=index),
            source_bulletin=bulletin,
        )
        WeatherSnapshotFactory.create(
            region=region, valid_for_date=today - timedelta(days=index)
        )
        ForecastCellWeatherFactory.create(
            forecast_cell=cells[index], valid_for_date=today
        )
        ForecastCellWeatherHistoryFactory.create(
            forecast_cell=cells[index],
            valid_for_date=today,
            issued_date=today - timedelta(days=3),
        )

    # A second RegionBulletin on an existing bulletin, so the bulletin id map
    # is exercised for more than one child per parent.
    RegionBulletinFactory.create(
        bulletin=Bulletin.objects.get(bulletin_id="CH-bulletin-0"),
        region=regions[1],
    )

    # The curated resort estate: a resort with a village and a peak, so the
    # resort and location id maps both translate a real link.
    resort = ResortFactory.create(region=regions[0], forecast_point=cells[0])
    for role, name in (
        (ResortLocation.ROLE.BASE, "Verbier village"),
        (ResortLocation.ROLE.TOP, "Mont Fort"),
    ):
        ResortLocationFactory.create(
            resort=resort,
            location=LocationFactory.create(name=name, forecast_cell=cells[0]),
            role=role,
        )


@pytest.fixture
def self_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the production alias at the test database and stub its schema.

    SQLite has no ``information_schema``, so ``production_columns`` is
    replaced with a ``PRAGMA table_info`` lookup returning the same answer.
    """
    monkeypatch.setattr(production_sync, "PRODUCTION_ALIAS", "default")

    def columns(table: str) -> set[str]:
        with connections["default"].cursor() as cursor:
            rows = cursor.execute(f'PRAGMA table_info("{table}")').fetchall()
        return {row[1] for row in rows}

    monkeypatch.setattr(production_sync, "production_columns", columns)


def _run_plan(commit: bool) -> list[TableResult]:
    """Run the whole plan, rebuilding each provider's id map as it goes."""
    id_maps = {spec.name: build_id_map(spec) for spec in ID_MAP_SPECS}
    results = []
    for spec in SYNC_PLAN:
        results.append(sync_table(spec, id_maps=id_maps, since=None, commit=commit))
        if spec.provides is not None:
            current = id_maps[spec.provides]
            id_maps[spec.provides] = build_id_map(
                IdMap(current.name, current.model_label, current.natural_key)
            )
    return results


@pytest.mark.django_db
def test_self_copy_is_idempotent(self_copy: None, seeded_bulletins: None) -> None:
    """Copying every table onto itself changes no row count.

    Every table upserts on its natural key, so a row that is already present
    is updated in place. A regression here — a wrong conflict target, a
    missing id map — shows up as duplicated rows.
    """
    before = {
        spec.model_label: spec.model._default_manager.count() for spec in SYNC_PLAN
    }
    assert before["bulletins.Bulletin"] > 0, "fixture seeded no bulletins"

    results = _run_plan(commit=True)

    after = {
        spec.model_label: spec.model._default_manager.count() for spec in SYNC_PLAN
    }
    assert after == before
    assert all(r.skipped == 0 for r in results), [
        (r.label, r.skipped) for r in results if r.skipped
    ]
    assert all(r.read == r.written for r in results)


@pytest.mark.django_db
def test_self_copy_preserves_foreign_keys(
    self_copy: None, seeded_bulletins: None
) -> None:
    """Remapped foreign keys still resolve after a copy."""
    _run_plan(commit=True)
    assert not RegionBulletin.objects.filter(bulletin__isnull=True).exists()
    for link in RegionBulletin.objects.select_related("bulletin", "region")[:20]:
        assert link.bulletin.bulletin_id
        assert link.region.region_id


@pytest.mark.django_db
def test_a_ugc_location_is_never_read(self_copy: None, seeded_bulletins: None) -> None:
    """A Location no ResortLocation points at stays out of the copy entirely.

    This is the plan's one mixed table, and the assertion is deliberately
    about ``read``, not ``written``: the restriction is a subquery inside
    production, so a user's saved position is never fetched at all rather
    than fetched and then discarded.
    """
    # What apps/observations/views.py mints for a field report: lat/lon, no name.
    orphan = LocationFactory.create(name="", latitude=46.9, longitude=7.9)
    assert not orphan.resort_locations.exists()

    curated = Location.objects.filter(resort_locations__isnull=False).distinct()
    results = _run_plan(commit=True)

    locations = next(r for r in results if r.label == "locations.Location")
    assert locations.read == curated.count()
    assert locations.read < Location.objects.count()

    # Still present, still untouched — the sync neither copied nor deleted it.
    orphan.refresh_from_db()
    assert orphan.name == ""


@pytest.mark.django_db
def test_dry_run_writes_nothing(self_copy: None, seeded_bulletins: None) -> None:
    """Without ``commit`` the counts are reported but no row is written."""
    Bulletin.objects.all().delete()
    results = _run_plan(commit=False)
    assert Bulletin.objects.count() == 0
    bulletins = next(r for r in results if r.label == "bulletins.Bulletin")
    assert bulletins.read == 0  # deleted above; nothing left to read
    assert bulletins.written == 0
