"""
tests/bulletins/migrations/test_0026_bulletin_keeps_no_caaml.py — the last copy.

Migrates to just before bulletins 0026, creates one bulletin ingest already
holds and one it does not (as the Météo-France archive loader wrote them
before SNOW-1337), migrates forward and checks the unheld one's CAAML
reached ``SourceBulletin`` while the held one was left alone — so dropping
``raw_data`` loses no provider payload. Ends on the latest migration so
later tests see the full schema.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.ingest.models import content_hash

BEFORE = [
    ("bulletins", "0025_bulletin_danger_levels"),
    ("ingest", "0008_sourceweather_one_row_per_day"),
]
AFTER = [("bulletins", "0026_bulletin_keeps_no_caaml")]
STAMP = dt.datetime(2026, 1, 15, 8, tzinfo=dt.UTC)


def _migrate(targets: list[tuple[str, str]]) -> MigrationExecutor:
    """Migrate to ``targets`` and return a fresh executor for its state."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return MigrationExecutor(connection)


@pytest.mark.django_db(transaction=True)
def test_an_unheld_bulletins_caaml_is_copied_before_the_column_goes() -> None:
    """The unheld row is copied; the held row and an empty one are not."""
    old = _migrate(BEFORE).loader.project_state(BEFORE).apps
    Bulletin = old.get_model("bulletins", "Bulletin")
    OldSource = old.get_model("ingest", "SourceBulletin")
    common = {"issued_at": STAMP, "valid_from": STAMP, "valid_to": STAMP}
    archived = {
        "bulletinID": "mf-archive",
        "regions": [{"regionID": "FR-02"}, {"regionID": "FR-01"}],
        "customData": {"MF": {}},
    }
    Bulletin.objects.create(
        bulletin_id="mf-archive",
        source="METEOFRANCE",
        raw_data={"type": "Feature", "geometry": None, "properties": archived},
        render_model={"version": 9, "traits": []},
        render_model_version=9,
        target_date=dt.date(2026, 1, 15),
        **common,
    )
    Bulletin.objects.create(
        bulletin_id="slf-held",
        source="SLF",
        raw_data={"properties": {"bulletinID": "slf-held", "stale": True}},
        **common,
    )
    OldSource.objects.create(
        bulletin_id="slf-held",
        provider="SLF",
        caaml={"bulletinID": "slf-held"},
        content_hash=content_hash({"bulletinID": "slf-held"}),
        fetched_at=STAMP,
    )
    Bulletin.objects.create(bulletin_id="empty", source="", raw_data={}, **common)
    Bulletin.objects.filter(bulletin_id="mf-archive").update(updated_at=STAMP)

    new = _migrate(AFTER).loader.project_state(AFTER).apps
    SourceBulletin = new.get_model("ingest", "SourceBulletin")
    rows = {row.bulletin_id: row for row in SourceBulletin.objects.all()}

    assert set(rows) == {"mf-archive", "slf-held"}
    copied = rows["mf-archive"]
    assert (copied.provider, copied.caaml) == ("METEOFRANCE", archived)
    assert copied.content_hash == content_hash(archived)
    assert copied.fetched_at == STAMP
    # Served whole: the feed files a row by its model and index alone.
    assert copied.render_model == {"version": 9, "traits": []}
    assert copied.render_model_version == 9
    assert (copied.valid_from, copied.target_date) == (STAMP, dt.date(2026, 1, 15))
    assert copied.region_codes == ["FR-01", "FR-02"]
    assert rows["slf-held"].caaml == {"bulletinID": "slf-held"}

    _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
