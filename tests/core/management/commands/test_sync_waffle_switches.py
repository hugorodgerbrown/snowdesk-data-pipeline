"""
tests/core/management/commands/test_sync_waffle_switches.py — sync_waffle_switches (SNOW-1201).

The command reconciles waffle switches to waffle_switches.json: it creates
a missing switch in the manifest's state, deletes a switch the manifest no
longer names, never changes an existing one, and writes nothing without
--commit.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from waffle.models import Switch

from apps.core.services.waffle_sync import load_switch_manifest


def _manifest(tmp_path: Path, *switches: dict) -> str:
    """Write a manifest holding ``switches`` and return its path."""
    path = tmp_path / "waffle_switches.json"
    path.write_text(json.dumps({"switches": list(switches)}))
    return str(path)


def _run(*args: str) -> str:
    """Run the command and return its stdout."""
    out = StringIO()
    call_command("sync_waffle_switches", *args, stdout=out)
    return out.getvalue()


ON = {"name": "job_on", "note": "On by default.", "active": True}
OFF = {"name": "job_off", "note": "Off by default.", "active": False}


@pytest.mark.django_db
class TestSyncWaffleSwitches:
    """Reconciling switches to the manifest."""

    def test_creates_each_switch_in_its_manifest_state(self, tmp_path: Path) -> None:
        """A missing switch is created as the manifest says, note included."""
        _run("--commit", "--manifest", _manifest(tmp_path, ON, OFF))
        assert Switch.objects.get(name="job_on").active
        assert not Switch.objects.get(name="job_off").active
        assert Switch.objects.get(name="job_on").note == "On by default."

    def test_a_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        """Without --commit, nothing is created."""
        output = _run("--manifest", _manifest(tmp_path, ON))
        assert "job_on" in output
        assert "[READ-ONLY]" in output
        assert not Switch.objects.exists()

    def test_an_operators_change_survives(self, tmp_path: Path) -> None:
        """An existing switch is never changed, so an operator's off stays off."""
        Switch.objects.create(name="job_on", active=False)
        _run("--commit", "--manifest", _manifest(tmp_path, ON))
        assert not Switch.objects.get(name="job_on").active

    def test_a_switch_the_manifest_drops_is_deleted(self, tmp_path: Path) -> None:
        """Only what the manifest names stays."""
        Switch.objects.create(name="retired", active=True)
        _run("--commit", "--manifest", _manifest(tmp_path, ON))
        assert set(Switch.objects.values_list("name", flat=True)) == {"job_on"}

    def test_idempotent(self, tmp_path: Path) -> None:
        """A second run has nothing to do."""
        path = _manifest(tmp_path, ON)
        _run("--commit", "--manifest", path)
        output = _run("--commit", "--manifest", path)
        assert "Switches to create: (none)" in output
        assert "Switches to delete: (none)" in output

    def test_a_bad_manifest_is_a_command_error(self, tmp_path: Path) -> None:
        """A malformed entry stops the command before anything is written."""
        bad = {"name": "x", "note": "", "active": "yes"}
        with pytest.raises(CommandError, match="true or false"):
            _run("--commit", "--manifest", _manifest(tmp_path, bad))

    def test_the_real_manifest_loads(self) -> None:
        """The checked-in manifest parses."""
        path = (
            Path(__file__).resolve().parents[4]
            / "apps/core/fixtures/waffle_switches.json"
        )
        assert load_switch_manifest(path)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"name": "a", "note": ""}, "exactly the keys"),
        (
            {"name": "a", "note": "", "active": True, "everyone": True},
            "exactly the keys",
        ),
    ],
)
def test_manifest_entries_are_validated(
    tmp_path: Path, entry: dict, message: str
) -> None:
    """A missing or unknown key is refused."""
    with pytest.raises(ValueError, match=message):
        load_switch_manifest(Path(_manifest(tmp_path, entry)))


def test_a_duplicate_name_is_refused(tmp_path: Path) -> None:
    """A switch named twice is refused."""
    with pytest.raises(ValueError, match="duplicate"):
        load_switch_manifest(Path(_manifest(tmp_path, ON, ON)))
