"""
0006_seed_community_reports_flag — Create the ``community_reports`` waffle Flag.

Data migration only.  Seeds a ``waffle.Flag`` row idempotently so that on
first deploy the "Community reports" read overlay on ``/map/`` (SNOW-419 —
anonymised, clustered pins from the last 48 hours of ``FieldObservation``
rows) is available to superusers immediately, with no manual
``/admin/waffle/flag/`` step required.

Deliberately a separate flag from ``field_observations`` (seeded by
``0002_seed_field_observations_flag.py``) so the read overlay can be
enabled independently of the GPS-gated submission feature.

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

FLAG_NAME = "community_reports"
FLAG_NOTE = (
    "Gates the community-reports read overlay on /map/ (SNOW-419) — "
    "anonymised, clustered pins of the last 48 hours of FieldObservation "
    "rows. Seeded with superusers=True so the project owner has access on "
    "first deploy. Add specific users via the Users field to invite "
    "non-superuser subscribers to beta-test. Set everyone=False to "
    "disable the overlay entirely without un-ticking Superusers."
)


def seed_community_reports_flag(apps: Any, schema_editor: Any) -> None:
    """Create the ``community_reports`` Flag row if it doesn't exist."""
    Flag = apps.get_model("waffle", "Flag")  # noqa: N806
    Flag.objects.get_or_create(
        name=FLAG_NAME,
        defaults={
            "superusers": True,
            "note": FLAG_NOTE,
        },
    )


def remove_community_reports_flag(apps: Any, schema_editor: Any) -> None:
    """Reverse: drop the ``community_reports`` Flag row by name."""
    Flag = apps.get_model("waffle", "Flag")  # noqa: N806
    Flag.objects.filter(name=FLAG_NAME).delete()


class Migration(migrations.Migration):
    """Seed the ``community_reports`` Flag for SNOW-419."""

    dependencies = [
        ("observations", "0005_fieldobservation_user"),
        # Pin to the latest waffle schema migration so the Flag model
        # exists at the point this RunPython executes.
        ("waffle", "0004_update_everyone_nullbooleanfield"),
    ]

    operations = [
        migrations.RunPython(
            seed_community_reports_flag,
            reverse_code=remove_community_reports_flag,
        ),
    ]
