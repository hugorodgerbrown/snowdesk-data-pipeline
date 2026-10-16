"""
apps/downloads/migrations/0002_downloadarea_terrain.py.

Adds ``DownloadArea.terrain`` (SNOW-1072): whether the user opted in to
terrain data — the terrain-class tiles — for this area. A plain column with
a default, so every existing row reads False, which is what every existing
download was: none of them pinned class tiles. No data is written here.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("downloads", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="downloadarea",
            name="terrain",
            field=models.BooleanField(
                default=False,
                help_text="Whether the download included terrain data — the terrain-class tiles the terrain filter paints from (SNOW-1072). An opt-in, so False by default. Synced so another device downloading or repairing the same area fetches the same tile families.",
            ),
        ),
    ]
