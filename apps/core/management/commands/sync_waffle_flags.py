"""
apps/core/management/commands/sync_waffle_flags.py — Reconcile Flags to a manifest.

Replaces the old "one data migration per flag" pattern (see
docs/feature-flags.md) with a single manifest at
``apps/core/fixtures/waffle_flags.json``. The command diffs the manifest against
the DB and reconciles by **create + delete only** — an existing flag's
admin-tuned targeting (``superusers``, ``everyone``, ``percent``, …) is
never edited in place, so an operator's live changes survive every deploy.

Read-only by default (prints the create/delete diff); pass ``--commit`` to
persist. Run on every deploy via ``build.sh``, after ``migrate``/``loaddata``.

SNOW-812: a manifest entry may carry ``groups`` (group names). Those are
applied with ``.set()`` after the row is created — ``Flag.groups`` is a
ManyToMany — and any named group that does not exist yet is created empty,
because a flag scoped to a missing group silently matches nobody.

Every flag is also scoped to two groups the manifest never names: its own
``GRP_FLAG_<NAME>`` (so turning a flag on for someone is adding them to a
group) and ``GRP_ALPHA`` (the alpha testers, who hold every flag). Both are
created with the flag. They are also ADDED to a flag that already exists
without them — the one write the command makes to an existing flag, and
add-only, so an operator's own targeting is still never undone. When a flag
leaves the manifest its ``GRP_FLAG_*`` group is deleted with it; so is any
``GRP_FLAG_*`` group no manifest flag owns. ``GRP_ALPHA`` and the manifest's
named groups are never deleted.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, cast

from django.contrib.auth.models import Group
from django.core.management.base import CommandError
from django.db import transaction
from waffle.models import Flag

from apps.core.command_register import RegisteredCommand
from apps.core.services.waffle_sync import (
    FLAG_GROUP_PREFIX,
    FlagSpec,
    compute_flag_diff,
    default_groups,
    flag_group_name,
    load_manifest,
)

logger = logging.getLogger(__name__)

# The command lives at apps/core/management/commands/, so parents[2] is the ``core``
# app root; the manifest sits alongside the app's other fixtures.
DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "waffle_flags.json"
)


class Command(RegisteredCommand):
    """Reconcile waffle.Flag rows to apps/core/fixtures/waffle_flags.json.

    Read-only by default; ``--commit`` persists the create/delete diff.
    Flags present in both the manifest and the DB are left untouched but
    for one add-only step — each is given its ``GRP_FLAG_*`` group and
    ``GRP_ALPHA`` if it lacks them — so a manifest ``groups`` change never
    re-scopes an existing flag, exactly as an ``everyone`` change never
    re-targets one. Re-scoping is an admin action, or a delete-and-recreate.

    SNOW-602 exempt: no per-row queryset loop — a handful of named flags,
    reconciled as name sets via bulk create/delete.
    """

    help = (
        "Reconcile waffle.Flag rows to the declarative manifest at "
        "apps/core/fixtures/waffle_flags.json by create + delete only (never "
        "edit-in-place, so admin-tuned targeting on an existing flag is "
        "preserved). Read-only by default; pass --commit to persist."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the create/delete diff. Without this flag the "
                "command is read-only."
            ),
        )
        parser.add_argument(
            "--manifest",
            type=Path,
            default=DEFAULT_MANIFEST_PATH,
            help=(
                f"Path to the flag manifest JSON. Defaults to {DEFAULT_MANIFEST_PATH}."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        manifest_path: Path = options["manifest"]

        try:
            specs = load_manifest(manifest_path)
        except (ValueError, FileNotFoundError) as exc:
            raise CommandError(str(exc)) from exc

        manifest_by_name = {spec.name: spec for spec in specs}
        db_names = set(Flag.objects.values_list("name", flat=True))
        to_create, to_delete = compute_flag_diff(set(manifest_by_name), db_names)
        missing_defaults = self._missing_default_groups(
            set(manifest_by_name) & db_names
        )
        groups_to_create, groups_to_delete = self._group_diff(
            manifest_by_name, to_create
        )
        attach_count = sum(len(names) for names in missing_defaults.values())
        # Counts for the command run register (SNOW-1190).
        self.tally("created", len(to_create))
        self.tally("deleted", len(to_delete))
        self.tally("groups_created", len(groups_to_create))
        self.tally("groups_deleted", len(groups_to_delete))
        self.tally("groups_attached", attach_count)

        if verbosity >= 1:
            self._report_plan(
                {
                    "Flags to create": to_create,
                    "Flags to delete": to_delete,
                    "Groups to create": groups_to_create,
                    "Groups to delete": groups_to_delete,
                },
                missing_defaults,
            )

        if not commit:
            self.stdout.write(
                self.style.WARNING(
                    "[READ-ONLY] dry-run — no rows written. Pass --commit to persist."
                )
            )
            return

        with transaction.atomic():
            for group_name in groups_to_create:
                Group.objects.get_or_create(name=group_name)
            for name in sorted(to_create):
                self._create_flag(manifest_by_name[name], verbosity)
            for name, group_names in missing_defaults.items():
                existing = cast(Flag, Flag.objects.get(name=name))
                existing.groups.add(*Group.objects.filter(name__in=group_names))
            # Flags first: deleting a GRP_FLAG_* group also drops its
            # memberships, and the flag it held is gone by then.
            Flag.objects.filter(name__in=to_delete).delete()
            Group.objects.filter(name__in=groups_to_delete).delete()

        logger.info(
            "sync_waffle_flags: created %d flag(s), deleted %d flag(s), "
            "created %d group(s), deleted %d group(s), attached %d group(s)",
            len(to_create),
            len(to_delete),
            len(groups_to_create),
            len(groups_to_delete),
            attach_count,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {len(to_create)} flag(s), deleted {len(to_delete)} "
                f"flag(s); created {len(groups_to_create)} group(s), deleted "
                f"{len(groups_to_delete)} group(s)."
            )
        )

    def _create_flag(self, spec: FlagSpec, verbosity: int) -> None:
        """Create one manifest flag and scope it to its groups.

        Args:
            spec: The manifest entry to create.
            verbosity: The command's ``--verbosity``.

        """
        name = spec.name
        # cast: ``Flag.objects`` is typed against waffle's
        # ``AbstractBaseFlag``, which declares no ``groups`` — that
        # field arrives on ``AbstractUserFlag``, which the concrete
        # ``Flag`` this project uses inherits from. The row really is
        # a ``Flag``; only the manager's annotation is broader.
        flag = cast(Flag, Flag.objects.create(name=name, **spec.create_defaults()))
        # SNOW-812: ``groups`` is a ManyToMany, so it is applied here
        # rather than as a create-default. Every group it names was
        # created above if missing: a flag scoped to a group that
        # does not exist matches nobody and reports no error, which
        # is the exact class of silent failure this manifest exists
        # to remove. Creating the empty group instead leaves an
        # operator one visible step — add people to it in the admin.
        group_names = {*spec.groups, *default_groups(name)}
        flag.groups.set(Group.objects.filter(name__in=group_names))
        if verbosity >= 1:
            self.stdout.write(
                f"  {name}: scoped to group(s) {', '.join(sorted(group_names))}"
            )

    def _report_plan(
        self, sections: dict[str, set[str]], missing_defaults: dict[str, set[str]]
    ) -> None:
        """Print what a run would change (or, with ``--commit``, will).

        Args:
            sections: Heading -> the flag or group names under it.
            missing_defaults: Existing flag name -> default groups to add.

        """
        for heading, names in sections.items():
            self.stdout.write(f"{heading}: {', '.join(sorted(names)) or '(none)'}")
        for name in sorted(missing_defaults):
            self.stdout.write(
                f"  {name}: add group(s) {', '.join(sorted(missing_defaults[name]))}"
            )

    def _missing_default_groups(self, kept: set[str]) -> dict[str, set[str]]:
        """Return the default groups each existing flag is not yet scoped to.

        Args:
            kept: Names of flags in both the manifest and the DB.

        Returns:
            Flag name -> the names from ``default_groups`` it lacks; a flag
            lacking none is absent.

        """
        # cast: as in ``handle`` — the manager is annotated against
        # ``AbstractBaseFlag``, which has no ``groups`` relation.
        through = cast(Any, Flag).groups.through
        held: dict[str, set[str]] = {name: set() for name in kept}
        for flag_name, group_name in through.objects.filter(
            flag__name__in=kept
        ).values_list("flag__name", "group__name"):
            held[flag_name].add(group_name)
        missing = {
            name: set(default_groups(name)) - held[name] for name in sorted(kept)
        }
        return {name: groups for name, groups in missing.items() if groups}

    def _group_diff(
        self, manifest_by_name: dict[str, FlagSpec], to_create: set[str]
    ) -> tuple[set[str], set[str]]:
        """Work out which auth groups to create and which to delete.

        Args:
            manifest_by_name: The manifest's ``FlagSpec`` per flag name.
            to_create: Names of flags about to be created.

        Returns:
            ``(to_create, to_delete)`` group names. Created: every default
            group a manifest flag needs, plus the named ``groups`` of a flag
            being created, that does not exist yet. Deleted: every
            ``GRP_FLAG_*`` group no manifest flag owns.

        """
        needed = {group for name in manifest_by_name for group in default_groups(name)}
        for name in to_create:
            needed.update(manifest_by_name[name].groups)
        existing = set(Group.objects.values_list("name", flat=True))
        owned = {flag_group_name(name) for name in manifest_by_name}
        # Exact prefix match in Python: SQLite's LIKE (``__startswith``) is
        # case-insensitive, and only the command's own spelling is its own.
        orphaned = {
            group
            for group in existing
            if group.startswith(FLAG_GROUP_PREFIX) and group not in owned
        }
        return needed - existing, orphaned
