"""
apps/core/services/waffle_sync.py — Declarative waffle.Flag manifest diffing.

Pure, ORM-free helpers behind the ``sync_waffle_flags`` management command
(``apps/core/management/commands/sync_waffle_flags.py``). The command reconciles
the DB's ``waffle.Flag`` rows to a JSON manifest by create + delete only —
never edit-in-place, so an operator's admin-tuned targeting (``superusers``,
``everyone``, ``percent``, …) on an existing flag is never clobbered by a
re-run.

``load_manifest`` parses and validates the manifest file into a list of
``FlagSpec``; ``compute_flag_diff`` is the pure set-difference the command
acts on. Neither function imports the ORM, so both are unit-testable without
a database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

# Keys every manifest entry may carry. ``name``/``note`` are required; the
# rest are optional scalar Flag create-defaults. Anything outside this set
# is rejected as an unknown key (catches typos such as "superuser").
_REQUIRED_KEYS = frozenset({"name", "note"})
_OPTIONAL_KEYS = frozenset(
    {
        "superusers",
        "staff",
        "authenticated",
        "everyone",
        "percent",
        "testing",
        "rollout",
    }
)
_ALLOWED_KEYS = _REQUIRED_KEYS | _OPTIONAL_KEYS


@dataclass(frozen=True, slots=True)
class FlagSpec:
    """One manifest entry — a flag name plus its create-time defaults.

    ``superusers``/``staff``/``authenticated``/``testing``/``rollout`` are
    ``bool | None``; ``everyone`` is ``bool | None`` (waffle's ``Flag.everyone``
    is a nullable ``BooleanField`` — ``None`` means "fall through to the other
    targeting rules"); ``percent`` is ``Decimal | None``. In every case
    ``None`` means "not specified in the manifest" — the created ``Flag`` row
    falls back to the model field's own default rather than an explicit value.
    """

    name: str
    note: str
    superusers: bool | None = None
    staff: bool | None = None
    authenticated: bool | None = None
    everyone: bool | None = None
    percent: Decimal | None = None
    testing: bool | None = None
    rollout: bool | None = None

    def create_defaults(self) -> dict[str, Any]:
        """Return the kwargs for ``Flag.objects.create(name=..., **kwargs)``.

        Only fields explicitly present in the manifest entry are included
        (plus ``note``, always required) — fields left unset here are
        omitted entirely so the ``Flag`` model's own field default applies.

        Returns:
            A dict of ``Flag`` field name -> value, suitable as ``create()``
            keyword arguments alongside ``name``.

        """
        defaults: dict[str, Any] = {"note": self.note}
        for field_name in _OPTIONAL_KEYS:
            value = getattr(self, field_name)
            if value is not None:
                defaults[field_name] = value
        return defaults


def load_manifest(path: Path) -> list[FlagSpec]:
    """Parse and validate the waffle flag manifest at ``path``.

    Args:
        path: Path to the manifest JSON file, shaped ``{"flags": [...]}``.

    Returns:
        One ``FlagSpec`` per manifest entry, in file order.

    Raises:
        ValueError: If the file is missing, is not valid JSON, is not
            shaped ``{"flags": [...]}``, or any entry is missing a
            required key (``name``/``note``), repeats a ``name`` already
            seen earlier in the file, or carries a key outside the
            recognised ``FlagSpec`` fields.

    """
    if not path.exists():
        raise ValueError(f"Waffle flag manifest not found: {path}")

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Malformed JSON in waffle flag manifest {path}: {exc}"
        ) from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("flags"), list):
        raise ValueError(
            f"Waffle flag manifest {path} must be a JSON object shaped "
            '{"flags": [...]}.'
        )

    specs: list[FlagSpec] = []
    seen_names: set[str] = set()
    for index, entry in enumerate(raw["flags"]):
        _validate_entry(path, index, entry, seen_names)
        specs.append(_spec_from_entry(entry))
    return specs


def _validate_entry(path: Path, index: int, entry: Any, seen_names: set[str]) -> None:
    """Validate one manifest entry, raising ``ValueError`` on any problem.

    Mutates ``seen_names`` in place (adds the entry's ``name``) so the
    caller can detect duplicates across the whole file.

    Args:
        path: The manifest file path, for error messages.
        index: The entry's position in the ``flags`` list, for error messages.
        entry: The raw parsed JSON value for this entry.
        seen_names: Flag names already seen earlier in the file.

    Raises:
        ValueError: If ``entry`` is not an object, carries an unknown key,
            is missing a required key, or repeats a ``name`` already in
            ``seen_names``.

    """
    if not isinstance(entry, dict):
        raise ValueError(
            f"Waffle flag manifest {path}: entry {index} is not a JSON object."
        )

    unknown = set(entry) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(
            f"Waffle flag manifest {path}: entry {index} has unknown "
            f"key(s): {', '.join(sorted(unknown))}."
        )

    missing = _REQUIRED_KEYS - set(entry)
    if missing:
        raise ValueError(
            f"Waffle flag manifest {path}: entry {index} is missing "
            f"required key(s): {', '.join(sorted(missing))}."
        )

    name = entry["name"]
    if name in seen_names:
        raise ValueError(f"Waffle flag manifest {path}: duplicate flag name {name!r}.")
    seen_names.add(name)


def _spec_from_entry(entry: dict[str, Any]) -> FlagSpec:
    """Build a ``FlagSpec`` from one already-validated manifest entry.

    Args:
        entry: A manifest entry that has already passed ``_validate_entry``.

    Returns:
        The corresponding ``FlagSpec``.

    """
    percent = entry.get("percent")
    return FlagSpec(
        name=entry["name"],
        note=entry["note"],
        superusers=entry.get("superusers"),
        staff=entry.get("staff"),
        authenticated=entry.get("authenticated"),
        everyone=entry.get("everyone"),
        percent=Decimal(str(percent)) if percent is not None else None,
        testing=entry.get("testing"),
        rollout=entry.get("rollout"),
    )


def compute_flag_diff(
    manifest_names: set[str], db_names: set[str]
) -> tuple[set[str], set[str]]:
    """Compute the create/delete diff between the manifest and the DB.

    Pure set logic — no ORM import — so it is unit-testable without a
    database. Flags present in both sets are left untouched by design: the
    caller never edits an existing ``Flag`` row in place.

    Args:
        manifest_names: Flag names declared in the manifest.
        db_names: Flag names currently present in the ``waffle.Flag`` table.

    Returns:
        A ``(to_create, to_delete)`` tuple: names in the manifest but not the
        DB, and names in the DB but not the manifest, respectively.

    """
    to_create = manifest_names - db_names
    to_delete = db_names - manifest_names
    return to_create, to_delete
