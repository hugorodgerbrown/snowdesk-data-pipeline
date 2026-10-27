"""
tests/bulletins/migrations/test_0022_copy_bulletins_to_source_bulletins.py — the copy.

Migrates to just before bulletins 0022, creates bulletins through the
historical models, migrates forward and checks every one reached
``SourceBulletin`` with its own timestamps and a hash ``content_hash``
agrees with; then back and forward again, to show it is idempotent. Ends
on the latest migration so later tests see the full schema.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.ingest.models import content_hash

BEFORE = [("bulletins", "0021_pipelinerun_partial_status"), ("ingest", "0001_initial")]
AFTER = [("bulletins", "0022_copy_bulletins_to_source_bulletins")]
STAMP = dt.datetime(2026, 1, 15, 8, tzinfo=dt.UTC)


def _migrate(targets: list[tuple[str, str]]) -> MigrationExecutor:
    """Migrate to ``targets`` and return a fresh executor for its state."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return MigrationExecutor(connection)


@pytest.mark.django_db(transaction=True)
def test_every_bulletin_is_copied_once() -> None:
    """Sourced, unsourced and unrecognisable rows; then a second pass adds none."""
    old = _migrate(BEFORE).loader.project_state(BEFORE).apps
    Bulletin = old.get_model("bulletins", "Bulletin")
    common = {"issued_at": STAMP, "valid_from": STAMP, "valid_to": STAMP}
    caaml = {"bulletinID": "slf-1", "customData": {"CH": {}}}
    Bulletin.objects.create(
        bulletin_id="slf-1",
        source="SLF",
        raw_data={"type": "Feature", "properties": caaml},
        pdf_url="https://x/1.pdf",
        **common,
    )
    Bulletin.objects.create(
        bulletin_id="mf-1",
        source="",
        raw_data={"properties": {"bulletinID": "mf-1", "customData": {"MF": {}}}},
        **common,
    )
    Bulletin.objects.create(
        bulletin_id="lwd-1",
        source="",
        raw_data={"properties": {"customData": {"LWD_Tirol": {}}}},
        **common,
    )
    Bulletin.objects.create(bulletin_id="none-1", source="", raw_data={}, **common)
    Bulletin.objects.filter(bulletin_id="slf-1").update(updated_at=STAMP)

    new = _migrate(AFTER).loader.project_state(AFTER).apps
    SourceBulletin = new.get_model("ingest", "SourceBulletin")
    rows = {row.bulletin_id: row for row in SourceBulletin.objects.all()}
    assert set(rows) == {"slf-1", "mf-1", "lwd-1"}
    slf = rows["slf-1"]
    assert (slf.provider, slf.caaml, slf.pdf_url) == ("SLF", caaml, "https://x/1.pdf")
    assert slf.content_hash == content_hash(caaml)
    assert slf.updated_at == STAMP
    assert rows["mf-1"].provider == "METEOFRANCE"
    assert rows["lwd-1"].provider == "ALBINA"

    _migrate([("bulletins", "0021_pipelinerun_partial_status")])
    _migrate(AFTER)
    assert SourceBulletin.objects.count() == 3

    _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
