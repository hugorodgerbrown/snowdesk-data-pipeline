"""
tests/pipeline/management/commands/test_dump_resorts_fixture.py — SNOW-74.

Covers ``dump_resorts_fixture``: read-only by default, ``--commit``
writes the fixture file, output uses natural foreign keys, and the
written fixture round-trips through ``loaddata``.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from pipeline.models import Resort
from tests.factories import RegionFactory, ResortFactory

FIXTURE_PATH = Path("pipeline/fixtures/resorts.json")


@pytest.mark.django_db
class TestDumpResortsFixture:
    """Tests for the ``dump_resorts_fixture`` command."""

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        """A bare invocation reports the diff and exits without writing."""
        # Snapshot the fixture, run dry-run, assert the file is byte-identical.
        original = FIXTURE_PATH.read_text(encoding="utf-8")

        ResortFactory.create(
            name="DryRunTest",
            latitude=46.0,
            longitude=7.0,
        )
        out = StringIO()
        call_command("dump_resorts_fixture", stdout=out)

        assert "Dry-run" in out.getvalue() or "No changes" in out.getvalue()
        assert FIXTURE_PATH.read_text(encoding="utf-8") == original

    def test_commit_writes_fixture(self, tmp_path: Path) -> None:
        """``--commit`` writes the fixture file with natural-key references."""
        # Capture the original so we can restore it after the test — we do
        # not want to leave per-test data in a tracked fixture.
        original = FIXTURE_PATH.read_text(encoding="utf-8")
        try:
            region = RegionFactory.create(region_id="CH-9999")
            ResortFactory.create(
                name="CommitTest",
                region=region,
                latitude=46.0,
                longitude=7.0,
                geocode_source="manual",
                geocode_confidence=1.0,
            )

            out = StringIO()
            call_command("dump_resorts_fixture", "--commit", stdout=out)

            assert "Wrote" in out.getvalue()
            written = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
            commit_test = next(
                (e for e in written if e["fields"]["name"] == "CommitTest"),
                None,
            )
            assert commit_test is not None
            # Natural foreign key — region as ``[region_id]``, not numeric pk.
            assert commit_test["fields"]["region"] == ["CH-9999"]
            assert commit_test["fields"]["latitude"] == 46.0
            assert commit_test["fields"]["longitude"] == 7.0
            assert commit_test["fields"]["geocode_source"] == "manual"
        finally:
            # Restore the fixture so the test is hermetic.
            FIXTURE_PATH.write_text(original, encoding="utf-8")

    def test_round_trip_dump_then_loaddata(self, tmp_path: Path) -> None:
        """Dump → loaddata produces the same Resort set."""
        original = FIXTURE_PATH.read_text(encoding="utf-8")
        try:
            region = RegionFactory.create(region_id="CH-9999")
            ResortFactory.create(
                name="RoundTrip",
                region=region,
                latitude=46.0,
                longitude=7.0,
            )
            call_command("dump_resorts_fixture", "--commit", verbosity=0)

            # Wipe and reload.
            Resort.objects.all().delete()
            assert Resort.objects.count() == 0
            call_command("loaddata", "resorts", verbosity=0)

            # The reloaded set must contain our row with its coords intact.
            roundtrip = Resort.objects.get(name="RoundTrip")
            assert roundtrip.latitude == 46.0
            assert roundtrip.longitude == 7.0
            assert roundtrip.region.region_id == "CH-9999"
        finally:
            FIXTURE_PATH.write_text(original, encoding="utf-8")
