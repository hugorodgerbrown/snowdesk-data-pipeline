"""
tests/regions/management/commands/test_dump_resorts_fixture.py — SNOW-74.

Covers ``dump_resorts_fixture``: read-only by default, ``--commit``
writes the fixture file, output uses natural foreign keys, and the
written fixture round-trips through ``loaddata``.

SNOW-659: every test here redirects the command's module-level
``_FIXTURE_PATH`` at a ``tmp_path`` file instead of letting it read and
write the real, tracked ``apps/regions/fixtures/resorts.json``. Operating
on the tracked file raced under xdist — one worker's ``--commit`` write
landing between another's two reads failed the dry-run test
intermittently, and a worker interrupted mid-write left the tracked
fixture truncated in the working tree, which then failed four unrelated
``tests/regions/`` tests and looked like a real regression.

The redirect is the idiom the sibling command tests already use — see
``test_audit_resort_regions.py`` and ``test_refresh_eaws_fixtures.py``,
both of which monkeypatch their own command's fixture constant the same
way. Nothing in this module may touch a git-tracked path.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.regions.management.commands import dump_resorts_fixture as dump_module
from apps.regions.models import Resort
from tests.factories import MicroRegionFactory, ResortFactory


def _redirect_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contents: str = "[]\n",
) -> Path:
    """Point the command's fixture path at a temp file and return it.

    ``handle`` resolves ``_FIXTURE_PATH`` as a module global at call time,
    so patching the attribute redirects both the command's read of the
    current fixture and its write.

    Args:
        tmp_path: pytest's per-test temporary directory.
        monkeypatch: pytest's monkeypatch fixture.
        contents: initial contents of the temp fixture. The default is an
            empty JSON array, so the command always sees a difference
            against a DB holding factory rows.

    Returns:
        The temp fixture path the command will read and write.

    """
    fixture = tmp_path / "resorts.json"
    fixture.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(dump_module, "_FIXTURE_PATH", fixture)
    return fixture


@pytest.mark.django_db
class TestDumpResortsFixture:
    """Tests for the ``dump_resorts_fixture`` command."""

    def test_dry_run_does_not_write(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A bare invocation reports the diff and exits without writing."""
        fixture = _redirect_fixture(tmp_path, monkeypatch)
        original = fixture.read_text(encoding="utf-8")

        ResortFactory.create(
            name="DryRunTest",
            latitude=46.0,
            longitude=7.0,
        )
        out = StringIO()
        call_command("dump_resorts_fixture", stdout=out)

        assert "Dry-run" in out.getvalue() or "No changes" in out.getvalue()
        assert fixture.read_text(encoding="utf-8") == original

    def test_commit_writes_fixture(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``--commit`` writes the fixture file with natural-key references."""
        fixture = _redirect_fixture(tmp_path, monkeypatch)

        region = MicroRegionFactory.create(region_id="CH-9999")
        ResortFactory.create(
            name="CommitTest",
            region=region,
            latitude=46.0,
            longitude=7.0,
            geocode_source=Resort.GeocodeSource.MANUAL,
            geocode_confidence=1.0,
        )

        out = StringIO()
        call_command("dump_resorts_fixture", "--commit", stdout=out)

        assert "Wrote" in out.getvalue()
        written = json.loads(fixture.read_text(encoding="utf-8"))
        commit_test = next(
            (e for e in written if e["fields"]["name"] == "CommitTest"),
            None,
        )
        assert commit_test is not None
        # Natural foreign key — region as ``[region_id]``, not numeric pk.
        assert commit_test["fields"]["region"] == ["CH-9999"]
        assert commit_test["fields"]["latitude"] == 46.0
        assert commit_test["fields"]["longitude"] == 7.0
        assert commit_test["fields"]["geocode_source"] == Resort.GeocodeSource.MANUAL

    def test_round_trip_dump_then_loaddata(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Dump → loaddata produces the same Resort set."""
        fixture = _redirect_fixture(tmp_path, monkeypatch)

        region = MicroRegionFactory.create(region_id="CH-9999")
        ResortFactory.create(
            name="RoundTrip",
            region=region,
            latitude=46.0,
            longitude=7.0,
            operator_name="Téléverbier",
        )
        call_command("dump_resorts_fixture", "--commit", verbosity=0)

        # Wipe and reload. ``loaddata`` takes the temp path explicitly — by
        # bare name ("resorts") Django's fixture finders would resolve the
        # TRACKED fixture instead, reloading rows this test never dumped and
        # asserting nothing about the file just written.
        Resort.objects.all().delete()
        assert Resort.objects.count() == 0
        call_command("loaddata", str(fixture), verbosity=0)

        # The reloaded set must contain our row with its coords intact.
        roundtrip = Resort.objects.get(name="RoundTrip")
        assert roundtrip.latitude == 46.0
        assert roundtrip.longitude == 7.0
        assert roundtrip.region.region_id == "CH-9999"
        # Metadata fields (SNOW-500) survive the dump/loaddata round trip.
        assert roundtrip.operator_name == "Téléverbier"

    def test_commit_reports_a_path_outside_the_cwd(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The success line survives a fixture path the cwd doesn't contain.

        SNOW-659: the message used to render the destination as
        ``_FIXTURE_PATH.relative_to(Path.cwd())`` unguarded, which raises
        ``ValueError`` for any path outside the working directory — after
        the write had already succeeded, so the operation completed but
        reported a traceback instead of its confirmation.
        """
        fixture = _redirect_fixture(tmp_path, monkeypatch)
        ResortFactory.create(name="OutsideCwd", latitude=46.0, longitude=7.0)

        out = StringIO()
        call_command("dump_resorts_fixture", "--commit", stdout=out)

        assert "Wrote" in out.getvalue()
        assert str(fixture) in out.getvalue()
        assert fixture.read_text(encoding="utf-8").strip() != "[]"
