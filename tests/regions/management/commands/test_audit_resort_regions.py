"""
tests/regions/management/commands/test_audit_resort_regions.py

Covers ``audit_resort_regions``:
  - Dry-run: reports bucket-(b) mismatches and bucket-(c) outsiders,
    exits non-zero when bucket-(b) is non-empty, leaves FK unchanged.
  - ``--commit``: re-FKs bucket-(b) resorts, leaves bucket-(c) untouched,
    calls _write_resorts_fixture.
  - When all FKs are correct, exits zero and reports success.

Synthetic geometry:
  Four non-overlapping unit squares are used as MicroRegion polygons:
    Region A: (0,0)→(1,1)  — Resort "Matching" (lat=0.5, lon=0.5, FK=A) → correct
    Region B: (2,0)→(3,1)  — Resort "Wrong"    (lat=2.5, lon=2.5, FK=A) → should be B
    Region C: (4,0)→(5,1)  — (no resort initially)
    Region D: (6,0)→(7,1)  — (no resort initially)
  Resort "Outside": lat=50, lon=50 → falls outside every polygon (bucket c)
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command

from tests.factories import MicroRegionFactory, ResortFactory, SubRegionFactory


def _make_square_polygon(x0: float, y0: float, x1: float, y1: float) -> dict[str, Any]:
    """Return a GeoJSON Polygon for the rectangle (x0,y0)→(x1,y1)."""
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [x0, y0],
                [x1, y0],
                [x1, y1],
                [x0, y1],
                [x0, y0],
            ]
        ],
    }


def _make_region(region_id: str, x0: float, y0: float, x1: float, y1: float) -> Any:
    """Create a MicroRegion with a square boundary polygon."""
    sub = SubRegionFactory.create(prefix=region_id[:5])
    return MicroRegionFactory.create(
        region_id=region_id,
        subregion=sub,
        boundary=_make_square_polygon(x0, y0, x1, y1),
    )


@pytest.mark.django_db
class TestAuditResortRegionsDryRun:
    """Dry-run (no --commit) behaviour."""

    def test_bucket_b_reported_and_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FK mismatch is reported; command exits non-zero."""
        region_a = _make_region("CH-T100", 0, 0, 1, 1)
        region_b = _make_region("CH-T200", 2, 0, 3, 1)

        # Resort inside region_b polygon but FK points to region_a.
        wrong_resort = ResortFactory.create(
            name="Wrong Resort",
            region=region_a,
            latitude=0.5,
            longitude=2.5,
        )

        tmp_fixture = tmp_path / "resorts.json"
        tmp_fixture.write_text("[]", encoding="utf-8")

        from regions.management.commands import audit_resort_regions as mod

        monkeypatch.setattr(mod, "_FIXTURE_PATH", tmp_fixture)

        out = StringIO()
        with pytest.raises(SystemExit) as exc_info:
            call_command("audit_resort_regions", stdout=out)

        assert exc_info.value.code != 0
        output = out.getvalue()
        assert wrong_resort.name in output
        assert region_a.region_id in output
        assert region_b.region_id in output
        assert "Dry-run" in output

    def test_dry_run_does_not_change_fk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FK is not updated when --commit is absent."""
        region_a = _make_region("CH-T110", 0, 0, 1, 1)
        _make_region("CH-T210", 2, 0, 3, 1)  # target polygon for point (2.5, 0.5)

        wrong_resort = ResortFactory.create(
            name="No Change",
            region=region_a,
            latitude=0.5,
            longitude=2.5,
        )

        tmp_fixture = tmp_path / "resorts.json"
        tmp_fixture.write_text("[]", encoding="utf-8")

        from regions.management.commands import audit_resort_regions as mod

        monkeypatch.setattr(mod, "_FIXTURE_PATH", tmp_fixture)

        with pytest.raises(SystemExit):
            call_command("audit_resort_regions", stdout=StringIO())

        wrong_resort.refresh_from_db()
        assert wrong_resort.region.region_id == region_a.region_id

    def test_bucket_c_outside_reported_as_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Resort outside every polygon is reported as WARNING, not a mismatch."""
        region_a = _make_region("CH-T120", 0, 0, 1, 1)
        outside_resort = ResortFactory.create(
            name="Faraway Resort",
            region=region_a,
            latitude=50.0,
            longitude=50.0,
        )

        tmp_fixture = tmp_path / "resorts.json"
        tmp_fixture.write_text("[]", encoding="utf-8")

        from regions.management.commands import audit_resort_regions as mod

        monkeypatch.setattr(mod, "_FIXTURE_PATH", tmp_fixture)

        out = StringIO()
        # No bucket-(b) → exits zero despite bucket-(c) existing.
        call_command("audit_resort_regions", stdout=out)

        output = out.getvalue()
        assert outside_resort.name in output
        assert "WARNING" in output or "outside" in output.lower()

    def test_all_correct_exits_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When every FK is correct, exits zero and reports success."""
        region_a = _make_region("CH-T130", 0, 0, 1, 1)
        ResortFactory.create(
            name="Good Resort",
            region=region_a,
            latitude=0.5,
            longitude=0.5,
        )

        tmp_fixture = tmp_path / "resorts.json"
        tmp_fixture.write_text("[]", encoding="utf-8")

        from regions.management.commands import audit_resort_regions as mod

        monkeypatch.setattr(mod, "_FIXTURE_PATH", tmp_fixture)

        out = StringIO()
        call_command("audit_resort_regions", stdout=out)

        output = out.getvalue()
        assert "consistent" in output.lower() or "correct" in output.lower()


@pytest.mark.django_db
class TestAuditResortRegionsCommit:
    """``--commit`` behaviour."""

    def test_commit_refks_bucket_b(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--commit updates the region FK for bucket-(b) resorts."""
        region_a = _make_region("CH-T300", 0, 0, 1, 1)
        region_b = _make_region("CH-T400", 2, 0, 3, 1)

        wrong_resort = ResortFactory.create(
            name="To Fix",
            region=region_a,
            latitude=0.5,
            longitude=2.5,
        )

        tmp_fixture = tmp_path / "resorts.json"
        tmp_fixture.write_text("[]", encoding="utf-8")

        # Stub _write_resorts_fixture so we don't write to the real fixture.
        write_calls: list[Path] = []

        from regions.management.commands import audit_resort_regions as mod

        monkeypatch.setattr(mod, "_FIXTURE_PATH", tmp_fixture)

        # Patch the imported _write_resorts_fixture inside audit_resort_regions.
        import regions.management.commands.dump_resorts_fixture as dump_mod

        original_write = dump_mod._write_resorts_fixture

        def fake_write(fixture_path: Path, verbosity: int = 0) -> None:
            write_calls.append(fixture_path)

        monkeypatch.setattr(dump_mod, "_write_resorts_fixture", fake_write)

        call_command("audit_resort_regions", "--commit", stdout=StringIO())

        wrong_resort.refresh_from_db()
        assert wrong_resort.region.region_id == region_b.region_id
        assert len(write_calls) == 1

        # Restore for safety (monkeypatch does it automatically but being explicit).
        monkeypatch.setattr(dump_mod, "_write_resorts_fixture", original_write)

    def test_commit_leaves_bucket_c_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--commit does not change the FK of resorts outside every polygon."""
        region_a = _make_region("CH-T310", 0, 0, 1, 1)
        _make_region("CH-T410", 2, 0, 3, 1)  # target polygon for bucket-(b) resort

        # Resort outside every polygon.
        outside_resort = ResortFactory.create(
            name="Outside Resort",
            region=region_a,
            latitude=50.0,
            longitude=50.0,
        )
        # Also add a bucket-(b) resort so the commit path runs.
        ResortFactory.create(
            name="Fix Me",
            region=region_a,
            latitude=0.5,
            longitude=2.5,
        )

        tmp_fixture = tmp_path / "resorts.json"
        tmp_fixture.write_text("[]", encoding="utf-8")

        from regions.management.commands import audit_resort_regions as mod

        monkeypatch.setattr(mod, "_FIXTURE_PATH", tmp_fixture)

        import regions.management.commands.dump_resorts_fixture as dump_mod

        monkeypatch.setattr(dump_mod, "_write_resorts_fixture", lambda *a, **kw: None)

        call_command("audit_resort_regions", "--commit", stdout=StringIO())

        outside_resort.refresh_from_db()
        # FK is unchanged — still region_a.
        assert outside_resort.region.region_id == region_a.region_id

    def test_commit_exits_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--commit does not sys.exit with non-zero code."""
        region_a = _make_region("CH-T320", 0, 0, 1, 1)
        _make_region("CH-T420", 2, 0, 3, 1)  # target polygon for resort below

        ResortFactory.create(
            name="Fix Me Too",
            region=region_a,
            latitude=0.5,
            longitude=2.5,
        )

        tmp_fixture = tmp_path / "resorts.json"
        tmp_fixture.write_text("[]", encoding="utf-8")

        from regions.management.commands import audit_resort_regions as mod

        monkeypatch.setattr(mod, "_FIXTURE_PATH", tmp_fixture)

        import regions.management.commands.dump_resorts_fixture as dump_mod

        monkeypatch.setattr(dump_mod, "_write_resorts_fixture", lambda *a, **kw: None)

        # Should not raise SystemExit.
        call_command("audit_resort_regions", "--commit", stdout=StringIO())
