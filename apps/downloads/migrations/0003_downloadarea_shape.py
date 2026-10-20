"""
apps/downloads/migrations/0003_downloadarea_shape.py.

Adds ``DownloadArea.shape`` (SNOW-1136): whether a custom area is a framed
box or a drop zone (a circle inside its bbox). A plain column with a
default, so every existing row reads ``box`` — the shape every one of them
came back as on another device until now. No data is written here.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add DownloadArea.shape."""

    dependencies = [
        ("downloads", "0002_downloadarea_terrain"),
    ]

    operations = [
        migrations.AddField(
            model_name="downloadarea",
            name="shape",
            field=models.CharField(
                choices=[("box", "Box"), ("circle", "Circle (drop zone)")],
                default="box",
                help_text="Whether a custom area is a framed box or a drop zone (a circle inside its bbox) — SNOW-1136. Synced so another device restores a drop zone as a drop zone. Always 'box' for a region.",
                max_length=16,
            ),
        ),
    ]
