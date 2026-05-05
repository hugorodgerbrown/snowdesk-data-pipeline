"""
0004_remove_weather_header_flag — Delete the ``weather_header`` django-waffle Flag.

Data migration that removes the ``weather_header`` Flag row seeded by
``0003_seed_weather_header_flag``. The flag is no longer needed: the
weather-driven bulletin header (SNOW-98) is now unconditional — the
flag was set to ``everyone=Yes`` in production and the template branch
has been removed (SNOW-121).

Reversible: the reverse function recreates the row with the same defaults
as the original seeding migration so a rollback lands in a state consistent
with what ``0003`` produced.
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


def remove_weather_header_flag(apps: Any, schema_editor: Any) -> None:
    """Drop the ``weather_header`` Flag row by name (SNOW-121 retirement)."""
    Flag = apps.get_model("waffle", "Flag")  # noqa: N806
    Flag.objects.filter(name=FLAG_NAME).delete()


def seed_weather_header_flag(apps: Any, schema_editor: Any) -> None:
    """Reverse: recreate the ``weather_header`` Flag row if it was removed."""
    Flag = apps.get_model("waffle", "Flag")  # noqa: N806
    Flag.objects.get_or_create(
        name=FLAG_NAME,
        defaults={
            "superusers": True,
            "note": FLAG_NOTE,
        },
    )


class Migration(migrations.Migration):
    """Remove the ``weather_header`` Flag for SNOW-121."""

    dependencies = [
        ("bulletins", "0003_seed_weather_header_flag"),
    ]

    operations = [
        migrations.RunPython(
            remove_weather_header_flag, reverse_code=seed_weather_header_flag
        ),
    ]
