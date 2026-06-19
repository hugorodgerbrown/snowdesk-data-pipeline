"""
0002_seed_field_observations_flag — Create the ``field_observations`` waffle Flag.

Data migration only.  Seeds a ``waffle.Flag`` row idempotently so that on
first deploy the GPS-gated field-report feature (SNOW-324, the "Waze-style
Report" button on /map/) is available to superusers immediately, with no
manual ``/admin/waffle/flag/`` step required.

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

FLAG_NAME = "field_observations"
FLAG_NOTE = (
    "Gates the GPS-gated field-report feature on /map/ (SNOW-324). "
    "Seeded with superusers=True so the project owner has access on "
    "first deploy. Add specific users via the Users field to invite "
    "non-superuser subscribers to beta-test. Set everyone=False to "
    "disable the feature entirely without un-ticking Superusers."
)


def seed_field_observations_flag(apps: Any, schema_editor: Any) -> None:
    """Create the ``field_observations`` Flag row if it doesn't exist."""
    Flag = apps.get_model("waffle", "Flag")  # noqa: N806
    Flag.objects.get_or_create(
        name=FLAG_NAME,
        defaults={
            "superusers": True,
            "note": FLAG_NOTE,
        },
    )


def remove_field_observations_flag(apps: Any, schema_editor: Any) -> None:
    """Reverse: drop the ``field_observations`` Flag row by name."""
    Flag = apps.get_model("waffle", "Flag")  # noqa: N806
    Flag.objects.filter(name=FLAG_NAME).delete()


class Migration(migrations.Migration):
    """Seed the ``field_observations`` Flag for SNOW-324."""

    dependencies = [
        ("observations", "0001_initial"),
        # Pin to the latest waffle schema migration so the Flag model
        # exists at the point this RunPython executes.
        ("waffle", "0004_update_everyone_nullbooleanfield"),
    ]

    operations = [
        migrations.RunPython(
            seed_field_observations_flag,
            reverse_code=remove_field_observations_flag,
        ),
    ]
