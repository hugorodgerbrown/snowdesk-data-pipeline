"""
0003_seed_weather_header_flag — Create the ``weather_header`` django-waffle Flag.

Data migration only. Seeds a ``waffle.Flag`` row idempotently so that on
first deploy the SNOW-98 weather-driven bulletin header is available to
superusers immediately, with no manual ``/admin/waffle/flag/`` step
required. Hides the header (and its in-development debug overlay) from
public traffic until the visual design hand-off lands.

Idempotent: ``get_or_create`` on the unique ``name`` field — re-applying
this migration on a database that already has the row is a no-op, and
operators who change ``superusers``/``users``/``everyone`` via the admin
will not see their edits clobbered by a re-run.

Toggle / extend behaviour at runtime via the admin:

* ``superusers=True``  — the seeded default; gives every superuser the
  preview without naming them individually.
* ``users``            — add specific Django users (handy for inviting a
  designer to preview the header without granting superuser).
* ``everyone=Yes``     — flip on for all traffic when the visual design
  ships. ``everyone=No`` is the kill switch.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

FLAG_NAME = "weather_header"
FLAG_NOTE = (
    "Gates the weather-driven bulletin header on the bulletin detail "
    "page (SNOW-98). Seeded with superusers=True so the project owner "
    "previews the header on first deploy; public traffic still sees the "
    "old layout until the visual design lands. Add users via the Users "
    "field, set everyone=Yes to ship to all traffic, or everyone=No to "
    "kill the feature without un-ticking Superusers."
)


def seed_weather_header_flag(apps: Any, schema_editor: Any) -> None:
    """Create the ``weather_header`` Flag row if it doesn't exist."""
    Flag = apps.get_model("waffle", "Flag")  # noqa: N806
    Flag.objects.get_or_create(
        name=FLAG_NAME,
        defaults={
            "superusers": True,
            "note": FLAG_NOTE,
        },
    )


def remove_weather_header_flag(apps: Any, schema_editor: Any) -> None:
    """Reverse: drop the ``weather_header`` Flag row by name."""
    Flag = apps.get_model("waffle", "Flag")  # noqa: N806
    Flag.objects.filter(name=FLAG_NAME).delete()


class Migration(migrations.Migration):
    """Seed the ``weather_header`` Flag for SNOW-98."""

    dependencies = [
        ("bulletins", "0002_add_weather_snapshot"),
        # Pin to the latest waffle schema migration so the Flag model
        # exists at the point this RunPython executes.
        ("waffle", "0004_update_everyone_nullbooleanfield"),
    ]

    operations = [
        migrations.RunPython(
            seed_weather_header_flag, reverse_code=remove_weather_header_flag
        ),
    ]
