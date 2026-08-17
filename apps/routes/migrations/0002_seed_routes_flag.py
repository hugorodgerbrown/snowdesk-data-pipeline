"""
0002_seed_routes_flag — Create the ``routes`` waffle Flag.

Data migration only.  Seeds a ``waffle.Flag`` row idempotently so that on
first deploy the GPX route feature (SNOW-685, and the panel/map layer in
SNOW-686/SNOW-687 that build on it) is available to superusers immediately,
with no manual ``/admin/waffle/flag/`` step required.

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

FLAG_NAME = "routes"
FLAG_NOTE = (
    "Gates the GPX route feature (SNOW-685). Seeded with superusers=True so "
    "the project owner has access on first deploy. Add specific users via the "
    "Users field to invite non-superuser subscribers to beta-test. Set "
    "everyone=False to disable the feature entirely without un-ticking "
    "Superusers."
)


def seed_routes_flag(apps: Any, schema_editor: Any) -> None:
    """Create the ``routes`` Flag row if it doesn't exist."""
    Flag = apps.get_model("waffle", "Flag")  # noqa: N806
    Flag.objects.get_or_create(
        name=FLAG_NAME,
        defaults={
            "superusers": True,
            "note": FLAG_NOTE,
        },
    )


def remove_routes_flag(apps: Any, schema_editor: Any) -> None:
    """Reverse: drop the ``routes`` Flag row by name."""
    Flag = apps.get_model("waffle", "Flag")  # noqa: N806
    Flag.objects.filter(name=FLAG_NAME).delete()


class Migration(migrations.Migration):
    """Seed the ``routes`` Flag for SNOW-685."""

    dependencies = [
        ("routes", "0001_initial"),
        # Pin to the latest waffle schema migration so the Flag model
        # exists at the point this RunPython executes.
        ("waffle", "0004_update_everyone_nullbooleanfield"),
    ]

    operations = [
        migrations.RunPython(
            seed_routes_flag,
            reverse_code=remove_routes_flag,
        ),
    ]
