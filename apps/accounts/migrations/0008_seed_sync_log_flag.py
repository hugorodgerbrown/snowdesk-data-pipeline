"""
0008_seed_sync_log_flag — Create the ``sync_log`` waffle Flag.

Data migration only. Seeds a ``waffle.Flag`` row idempotently so that on
first deploy the SNOW-482 sync-log panel — the manage-page read-out of
recent real (un-cached) server round-trips, backed by the client-side
``log:sync`` IndexedDB store — and its matching ``/help/`` section are
available to superusers immediately, with no manual
``/admin/waffle/flag/`` step required.

Idempotent: ``get_or_create`` on the unique ``name`` field — re-applying
this migration on a database that already has the row is a no-op, and
operators who change ``superusers``/``users``/``everyone`` via the admin
will not see their edits clobbered by a re-run.

Toggle / extend behaviour at runtime via the admin:

* ``superusers=True``  — the seeded default; gives every superuser
  access without listing them by name.
* ``users``            — add specific Django users (handy if you want
  to invite a non-superuser subscriber to beta-test the feature).
* ``everyone=False``   — kill switch; turns the feature off for
  everybody including superusers, without un-ticking ``superusers``.

Reverse migration deletes the row by name.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

FLAG_NAME = "sync_log"
FLAG_NOTE = (
    "Gates the SNOW-482 sync-log panel (manage page) and its matching "
    "/help/ section — a read-out of the client-side log:sync IndexedDB "
    "store of recent real server round-trips. Seeded with superusers=True "
    "so the project owner has access on first deploy. Add specific users "
    "via the Users field to invite non-superuser subscribers to "
    "beta-test. Set everyone=False to disable both surfaces entirely "
    "without un-ticking Superusers."
)


def seed_sync_log_flag(apps: Any, schema_editor: Any) -> None:
    """Create the ``sync_log`` Flag row if it doesn't exist."""
    Flag = apps.get_model("waffle", "Flag")  # noqa: N806
    Flag.objects.get_or_create(
        name=FLAG_NAME,
        defaults={
            "superusers": True,
            "note": FLAG_NOTE,
        },
    )


def remove_sync_log_flag(apps: Any, schema_editor: Any) -> None:
    """Reverse: drop the ``sync_log`` Flag row by name."""
    Flag = apps.get_model("waffle", "Flag")  # noqa: N806
    Flag.objects.filter(name=FLAG_NAME).delete()


class Migration(migrations.Migration):
    """Seed the ``sync_log`` Flag for SNOW-482."""

    dependencies = [
        ("accounts", "0007_account_pending_email_and_more"),
        # Pin to the latest waffle schema migration so the Flag model
        # exists at the point this RunPython executes.
        ("waffle", "0004_update_everyone_nullbooleanfield"),
    ]

    operations = [
        migrations.RunPython(
            seed_sync_log_flag,
            reverse_code=remove_sync_log_flag,
        ),
    ]
