"""SNOW-1337: one Weather row per day, created ahead and refined.

Adds ``forecast_on`` — the day of the fetch that last wrote the row — and
drops the ``forecast`` column. Every existing row was written on its own
day, so its ``forecast_on`` is its ``observed_on`` (lead time zero). The
next scheduled fetch creates the rows for the six days ahead.
"""

from django.db import migrations, models
from django.db.models import F


def stamp_forecast_on(apps, schema_editor):  # noqa: ARG001 — migration signature
    """Set every existing row's forecast_on to its own day."""
    apps.get_model("weather", "Weather").objects.update(forecast_on=F("observed_on"))


class Migration(migrations.Migration):
    """Add forecast_on, then drop forecast."""

    dependencies = [
        ("weather", "0008_feed_keyset_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="weather",
            name="forecast_on",
            field=models.DateField(null=True),
        ),
        migrations.RunPython(stamp_forecast_on, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="weather",
            name="forecast_on",
            field=models.DateField(
                help_text=(
                    "The day of the fetch that last wrote this row. The lead time is "
                    "observed_on - forecast_on: six when the day enters the window, "
                    "zero on the day itself."
                ),
            ),
        ),
        migrations.RemoveField(
            model_name="weather",
            name="forecast",
        ),
    ]
