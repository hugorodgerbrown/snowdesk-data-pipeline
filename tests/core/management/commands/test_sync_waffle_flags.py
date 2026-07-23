"""
tests/core/management/commands/test_sync_waffle_flags.py — Tests for the
sync_waffle_flags management command.

Covers:
  - Dry-run (no ``--commit``) writes nothing.
  - ``--commit`` creates manifest flags absent from the DB, with the
    manifest's declared create-defaults.
  - ``--commit`` deletes DB flags absent from the manifest.
  - Re-running ``--commit`` against a matching DB is a no-op.
  - Targeting preservation: a flag present in both the manifest and the DB
    is never edited in place, even when its live targeting has drifted
    from the manifest's create-defaults.
  - A missing, malformed, or duplicate-name manifest raises ``CommandError``.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from waffle.models import Flag


def _write_manifest(path: Path, flags: list[dict[str, object]]) -> Path:
    """Write a ``{"flags": [...]}`` manifest to ``path`` and return it."""
    path.write_text(json.dumps({"flags": flags}))
    return path


@pytest.mark.django_db
def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    """Without --commit, no Flag rows are created or deleted."""
    Flag.objects.create(name="stale_flag", note="Not in manifest")
    manifest = _write_manifest(
        tmp_path / "flags.json", [{"name": "new_flag", "note": "New"}]
    )

    call_command("sync_waffle_flags", f"--manifest={manifest}")

    assert not Flag.objects.filter(name="new_flag").exists()
    assert Flag.objects.filter(name="stale_flag").exists()


@pytest.mark.django_db
def test_commit_creates_missing_flags(tmp_path: Path) -> None:
    """--commit creates manifest flags absent from the DB with declared defaults."""
    manifest = _write_manifest(
        tmp_path / "flags.json",
        [
            {
                "name": "new_flag",
                "note": "A new flag",
                "superusers": False,
                "staff": True,
                "percent": 25.5,
            }
        ],
    )

    call_command("sync_waffle_flags", "--commit", f"--manifest={manifest}")

    flag = Flag.objects.get(name="new_flag")
    assert flag.note == "A new flag"
    assert flag.superusers is False
    assert flag.staff is True
    assert flag.percent == Decimal("25.5")


@pytest.mark.django_db
def test_commit_deletes_flags_absent_from_manifest(tmp_path: Path) -> None:
    """--commit deletes DB flags that are no longer in the manifest."""
    Flag.objects.create(name="stale_flag", note="Should be removed")
    manifest = _write_manifest(tmp_path / "flags.json", [])

    call_command("sync_waffle_flags", "--commit", f"--manifest={manifest}")

    assert not Flag.objects.filter(name="stale_flag").exists()


@pytest.mark.django_db
def test_commit_is_a_no_op_when_db_matches_manifest(tmp_path: Path) -> None:
    """Re-running --commit against an already-synced DB creates/deletes nothing."""
    Flag.objects.create(name="steady_flag", note="Already synced", superusers=True)
    manifest = _write_manifest(
        tmp_path / "flags.json",
        [{"name": "steady_flag", "note": "Already synced", "superusers": True}],
    )

    call_command("sync_waffle_flags", "--commit", f"--manifest={manifest}")

    assert Flag.objects.count() == 1
    assert Flag.objects.get(name="steady_flag").superusers is True


@pytest.mark.django_db
def test_commit_preserves_targeting_on_existing_flag(tmp_path: Path) -> None:
    """A flag present in both sets is never edited in place.

    Even when the live row's targeting has drifted from what the manifest
    would create it with, --commit must leave it untouched — the command
    only creates and deletes, it never updates.
    """
    flag = Flag.objects.create(
        name="tuned_flag",
        note="Original note",
        superusers=True,
        everyone=None,
        percent=None,
    )
    # Simulate an operator's live admin edit that would be clobbered by an
    # edit-in-place sync.
    Flag.objects.filter(pk=flag.pk).update(everyone=False, percent=Decimal("42.0"))

    manifest = _write_manifest(
        tmp_path / "flags.json",
        [{"name": "tuned_flag", "note": "Original note", "superusers": True}],
    )

    call_command("sync_waffle_flags", "--commit", f"--manifest={manifest}")

    flag.refresh_from_db()
    assert flag.everyone is False
    assert flag.percent == Decimal("42.0")


@pytest.mark.django_db
def test_missing_manifest_raises_command_error(tmp_path: Path) -> None:
    """A missing manifest file raises CommandError."""
    with pytest.raises(CommandError):
        call_command("sync_waffle_flags", f"--manifest={tmp_path / 'missing.json'}")


@pytest.mark.django_db
def test_malformed_manifest_raises_command_error(tmp_path: Path) -> None:
    """Malformed JSON in the manifest raises CommandError."""
    manifest = tmp_path / "flags.json"
    manifest.write_text("{not valid json")
    with pytest.raises(CommandError):
        call_command("sync_waffle_flags", f"--manifest={manifest}")


@pytest.mark.django_db
def test_duplicate_name_manifest_raises_command_error(tmp_path: Path) -> None:
    """A manifest with a duplicate flag name raises CommandError."""
    manifest = _write_manifest(
        tmp_path / "flags.json",
        [
            {"name": "dup_flag", "note": "First"},
            {"name": "dup_flag", "note": "Second"},
        ],
    )
    with pytest.raises(CommandError):
        call_command("sync_waffle_flags", f"--manifest={manifest}")
