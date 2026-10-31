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

SNOW-1201 added the switch manifest, ``waffle_switches.json``, read by
``load_switch_manifest`` into ``SwitchSpec`` for ``sync_waffle_switches``.
Same rules: create + delete only, an existing switch never edited, so an
operator's off survives every deploy. ``compute_flag_diff`` is plain set
difference and serves both.

SNOW-812 added ``groups`` to the manifest vocabulary — group NAMES a flag is
scoped to. It is the one manifest key that is not a scalar create-default:
``Flag.groups`` is a ManyToMany, so the command applies it after the row
exists. Everything here still stops at parsing it.

Every flag also carries two groups the manifest never names: its own
``GRP_FLAG_<NAME>`` group, so turning a flag on for one person is adding
them to a group, and ``GRP_ALPHA``, the alpha testers, who hold every flag.
``flag_group_name`` and ``default_groups`` derive both from the flag name;
the command creates them with the flag and removes the flag's own group
with it.
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
# SNOW-812: ``groups`` is allowed in a manifest entry but is NOT one of the
# scalar create-defaults above — ``Flag.groups`` is a ManyToMany, so it
# cannot be passed to ``Flag.objects.create()`` and is applied by the
# command with ``.set()`` after the row exists. Kept out of
# ``_OPTIONAL_KEYS`` precisely so ``create_defaults()`` cannot pick it up.
_GROUPS_KEY = "groups"
_ALLOWED_KEYS = _REQUIRED_KEYS | _OPTIONAL_KEYS | {_GROUPS_KEY}

#: The alpha-testing group. Every flag is scoped to it, so a member has
#: every flagged feature turned on.
ALPHA_GROUP = "GRP_ALPHA"
#: Prefix of the per-flag group: ``debug_log`` is held by
#: ``GRP_FLAG_DEBUG_LOG``. A group with this prefix belongs to the command —
#: it is created with its flag and deleted with it.
FLAG_GROUP_PREFIX = "GRP_FLAG_"


def flag_group_name(flag_name: str) -> str:
    """Return the name of the group that exists to hold one flag.

    Args:
        flag_name: The waffle flag's name, e.g. ``"debug_log"``.

    Returns:
        ``GRP_FLAG_`` plus the flag name upper-cased, e.g.
        ``"GRP_FLAG_DEBUG_LOG"``.

    """
    return f"{FLAG_GROUP_PREFIX}{flag_name.upper()}"


def default_groups(flag_name: str) -> tuple[str, str]:
    """Return the groups every flag is scoped to, whatever its manifest says.

    Args:
        flag_name: The waffle flag's name.

    Returns:
        The flag's own group and the alpha group, in that order.

    """
    return (flag_group_name(flag_name), ALPHA_GROUP)


@dataclass(frozen=True, slots=True)
class FlagSpec:
    """One manifest entry — a flag name plus its create-time defaults.

    ``superusers``/``staff``/``authenticated``/``testing``/``rollout`` are
    ``bool | None``; ``everyone`` is ``bool | None`` (waffle's ``Flag.everyone``
    is a nullable ``BooleanField`` — ``None`` means "fall through to the other
    targeting rules"); ``percent`` is ``Decimal | None``. In every case
    ``None`` means "not specified in the manifest" — the created ``Flag`` row
    falls back to the model field's own default rather than an explicit value.

    ``groups`` (SNOW-812) is the exception to all of that: a tuple of group
    NAMES, empty when unspecified, applied after create rather than as a
    create-default because it is a ManyToMany.
    """

    name: str
    note: str
    #: SNOW-812: group names this flag is scoped to. Applied via ``.set()``
    #: after create, never as a create-default — see ``_GROUPS_KEY``.
    groups: tuple[str, ...] = ()
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

        ``groups`` is deliberately absent: it is a ManyToMany and cannot be
        set until the row exists.

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
            seen earlier in the file, carries a key outside the
            recognised ``FlagSpec`` fields, or gives ``groups`` anything
            but a list of non-empty strings, or names a ``GRP_FLAG_*``
            group in it.

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
            is missing a required key, gives a malformed ``groups`` or one
            naming a ``GRP_FLAG_*`` group, or repeats a ``name`` already in
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

    groups = entry.get(_GROUPS_KEY)
    if groups is not None and (
        not isinstance(groups, list)
        or not all(isinstance(g, str) and g.strip() for g in groups)
    ):
        raise ValueError(
            f"Waffle flag manifest {path}: entry {index} key "
            f"{_GROUPS_KEY!r} must be a list of non-empty group names."
        )
    if groups and any(g.startswith(FLAG_GROUP_PREFIX) for g in groups):
        # A GRP_FLAG_* group is the command's own: it is derived from a flag
        # name and deleted when no flag owns it, so naming one here would
        # scope this flag to a group the next run removes.
        raise ValueError(
            f"Waffle flag manifest {path}: entry {index} key "
            f"{_GROUPS_KEY!r} may not name a {FLAG_GROUP_PREFIX}* group — "
            "every flag gets its own automatically."
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
        groups=tuple(entry.get(_GROUPS_KEY) or ()),
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


@dataclass(frozen=True, slots=True)
class SwitchSpec:
    """One switch manifest entry: a switch name, its note, and its first state.

    ``active`` is the state the switch is created in. It is never applied
    to a switch that already exists.
    """

    name: str
    note: str
    active: bool


def load_switch_manifest(path: Path) -> list[SwitchSpec]:
    """Parse and validate the waffle switch manifest at ``path`` (SNOW-1201).

    Args:
        path: Path to the manifest JSON file, shaped ``{"switches": [...]}``,
            each entry ``{"name": str, "note": str, "active": bool}``.

    Returns:
        One ``SwitchSpec`` per entry, in file order.

    Raises:
        ValueError: If the file is missing or malformed, an entry carries
            a key other than ``name``/``note``/``active`` or lacks one,
            ``active`` is not a boolean, or a ``name`` repeats.

    """
    if not path.exists():
        raise ValueError(f"Waffle switch manifest not found: {path}")
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Malformed JSON in waffle switch manifest {path}: {exc}"
        ) from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("switches"), list):
        raise ValueError(
            f"Waffle switch manifest {path} must be a JSON object shaped "
            '{"switches": [...]}.'
        )

    keys = {"name", "note", "active"}
    specs: list[SwitchSpec] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw["switches"]):
        if not isinstance(entry, dict) or set(entry) != keys:
            raise ValueError(
                f"Waffle switch manifest {path}: entry {index} must have "
                "exactly the keys active, name, note."
            )
        if not isinstance(entry["active"], bool):
            raise ValueError(
                f"Waffle switch manifest {path}: entry {index} active must be "
                "true or false."
            )
        if entry["name"] in seen:
            raise ValueError(
                f"Waffle switch manifest {path}: duplicate switch name "
                f"{entry['name']!r}."
            )
        seen.add(entry["name"])
        specs.append(SwitchSpec(entry["name"], entry["note"], entry["active"]))
    return specs
