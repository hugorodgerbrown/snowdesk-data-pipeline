"""
tests/core/services/test_waffle_sync.py — Tests for apps.core.services.waffle_sync.

Covers:
  - ``compute_flag_diff``: create-only, delete-only, mixed, and no-op cases.
  - ``load_manifest``: a valid manifest parses into the expected FlagSpecs;
    a missing file, malformed JSON, wrong top-level shape, a missing
    ``name``/``note``, a duplicate ``name``, and an unknown key each raise
    ``ValueError``.
  - ``FlagSpec.create_defaults``: only explicitly-set fields are included.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from apps.core.services.waffle_sync import FlagSpec, compute_flag_diff, load_manifest

# ---------------------------------------------------------------------------
# compute_flag_diff
# ---------------------------------------------------------------------------


def test_compute_flag_diff_create_only() -> None:
    """Manifest names absent from the DB are reported as to_create."""
    to_create, to_delete = compute_flag_diff({"a", "b"}, set())
    assert to_create == {"a", "b"}
    assert to_delete == set()


def test_compute_flag_diff_delete_only() -> None:
    """DB names absent from the manifest are reported as to_delete."""
    to_create, to_delete = compute_flag_diff(set(), {"a", "b"})
    assert to_create == set()
    assert to_delete == {"a", "b"}


def test_compute_flag_diff_mixed() -> None:
    """Names in only the manifest or only the DB are split correctly."""
    to_create, to_delete = compute_flag_diff({"a", "b", "c"}, {"b", "c", "d"})
    assert to_create == {"a"}
    assert to_delete == {"d"}


def test_compute_flag_diff_no_op() -> None:
    """Identical manifest and DB name sets produce no diff either way."""
    to_create, to_delete = compute_flag_diff({"a", "b"}, {"a", "b"})
    assert to_create == set()
    assert to_delete == set()


# ---------------------------------------------------------------------------
# load_manifest — happy path
# ---------------------------------------------------------------------------


def _write_manifest(path: Path, payload: object) -> Path:
    """Write ``payload`` as JSON to ``path`` and return the path."""
    path.write_text(json.dumps(payload))
    return path


def test_load_manifest_valid(tmp_path: Path) -> None:
    """A valid manifest parses into one FlagSpec per entry, in file order."""
    manifest = _write_manifest(
        tmp_path / "flags.json",
        {
            "flags": [
                {"name": "flag_a", "note": "Note A", "superusers": True},
                {
                    "name": "flag_b",
                    "note": "Note B",
                    "staff": True,
                    "everyone": False,
                    "percent": 12.5,
                },
            ]
        },
    )

    specs = load_manifest(manifest)

    assert [spec.name for spec in specs] == ["flag_a", "flag_b"]
    assert specs[0] == FlagSpec(name="flag_a", note="Note A", superusers=True)
    assert specs[1] == FlagSpec(
        name="flag_b",
        note="Note B",
        staff=True,
        everyone=False,
        percent=Decimal("12.5"),
    )


def test_load_manifest_empty_flags_list(tmp_path: Path) -> None:
    """An empty ``flags`` list is valid and parses to an empty result."""
    manifest = _write_manifest(tmp_path / "flags.json", {"flags": []})
    assert load_manifest(manifest) == []


# ---------------------------------------------------------------------------
# load_manifest — error cases
# ---------------------------------------------------------------------------


def test_load_manifest_missing_file(tmp_path: Path) -> None:
    """A missing manifest file raises ValueError."""
    with pytest.raises(ValueError, match="not found"):
        load_manifest(tmp_path / "does-not-exist.json")


def test_load_manifest_malformed_json(tmp_path: Path) -> None:
    """Malformed JSON raises ValueError."""
    path = tmp_path / "flags.json"
    path.write_text("{not valid json")
    with pytest.raises(ValueError, match="Malformed JSON"):
        load_manifest(path)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"flags": "not-a-list"},
        {"wrong_key": []},
    ],
)
def test_load_manifest_wrong_shape(tmp_path: Path, payload: object) -> None:
    """A manifest not shaped {"flags": [...]} raises ValueError."""
    manifest = _write_manifest(tmp_path / "flags.json", payload)
    with pytest.raises(ValueError, match="flags"):
        load_manifest(manifest)


def test_load_manifest_missing_name(tmp_path: Path) -> None:
    """An entry missing ``name`` raises ValueError."""
    manifest = _write_manifest(
        tmp_path / "flags.json", {"flags": [{"note": "Note only"}]}
    )
    with pytest.raises(ValueError, match="missing required key"):
        load_manifest(manifest)


def test_load_manifest_missing_note(tmp_path: Path) -> None:
    """An entry missing ``note`` raises ValueError."""
    manifest = _write_manifest(tmp_path / "flags.json", {"flags": [{"name": "flag_a"}]})
    with pytest.raises(ValueError, match="missing required key"):
        load_manifest(manifest)


def test_load_manifest_duplicate_name(tmp_path: Path) -> None:
    """A repeated ``name`` across entries raises ValueError."""
    manifest = _write_manifest(
        tmp_path / "flags.json",
        {
            "flags": [
                {"name": "flag_a", "note": "First"},
                {"name": "flag_a", "note": "Second"},
            ]
        },
    )
    with pytest.raises(ValueError, match="duplicate flag name"):
        load_manifest(manifest)


def test_load_manifest_unknown_key(tmp_path: Path) -> None:
    """An entry with a key outside the recognised FlagSpec fields raises ValueError.

    Regression guard: a typo like "superuser" (missing the trailing 's') must
    be caught rather than silently ignored.
    """
    manifest = _write_manifest(
        tmp_path / "flags.json",
        {"flags": [{"name": "flag_a", "note": "Note", "superuser": True}]},
    )
    with pytest.raises(ValueError, match="unknown"):
        load_manifest(manifest)


# ---------------------------------------------------------------------------
# FlagSpec.create_defaults
# ---------------------------------------------------------------------------


def test_flag_spec_create_defaults_only_includes_explicit_fields() -> None:
    """create_defaults() includes note plus only the explicitly-set fields."""
    spec = FlagSpec(name="flag_a", note="Note A", superusers=True)
    assert spec.create_defaults() == {"note": "Note A", "superusers": True}


def test_flag_spec_create_defaults_with_no_optional_fields() -> None:
    """A spec with no optional fields set produces only {"note": ...}."""
    spec = FlagSpec(name="flag_a", note="Note A")
    assert spec.create_defaults() == {"note": "Note A"}
