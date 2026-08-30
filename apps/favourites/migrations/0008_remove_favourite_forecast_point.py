"""
apps/favourites/migrations/0008_remove_favourite_forecast_point.py.

Drop ``Favourite.forecast_point`` (SNOW-762). A favourite's elevation now
comes straight from ``fetch_elevation`` at creation time and is stored on
its ``Location``; nothing reads the quantised cell any more.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("favourites", "0007_alter_favourite_forecast_point"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="favourite",
            name="forecast_point",
        ),
    ]
