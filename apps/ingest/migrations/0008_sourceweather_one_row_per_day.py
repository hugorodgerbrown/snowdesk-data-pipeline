"""SNOW-1337: one SourceWeather row per day, created ahead and refined.

Adds ``forecast_on`` — the day of the fetch that last wrote the row — and
drops the ``forecast`` column. Every existing row was written on its own
day, so its ``forecast_on`` is its ``observed_on`` (lead time zero). The
next scheduled fetch creates the rows for the six days ahead.
"""

from django.db import migrations, models
from django.db.models import F


def stamp_forecast_on(apps, schema_editor):  # noqa: ARG001 — migration signature
    """Set every existing row's forecast_on to its own day."""
    apps.get_model("ingest", "SourceWeather").objects.update(
        forecast_on=F("observed_on")
    )


class Migration(migrations.Migration):
    """Add forecast_on, then drop forecast."""

    dependencies = [
        ("ingest", "0007_source_ski_tour_render"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourceweather",
            name="forecast_on",
            field=models.DateField(null=True),
        ),
        migrations.RunPython(stamp_forecast_on, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="sourceweather",
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
            model_name="sourceweather",
            name="forecast",
        ),
    ]
