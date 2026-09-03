"""
apps/regions/migrations/0021_resort_slug.py.

Add ``Resort.slug`` (SNOW-796) — nullable, so the column lands on a
populated table without a value this migration would have to invent.
``backfill_resort_slugs --commit`` fills it, and a later migration tightens
the column to ``null=False`` once every environment has run that command.
No data is written here (CLAUDE.md: no bulk data updates in migrations).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("regions", "0020_microregion_centroid_elevation_m_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="resort",
            name="slug",
            field=models.SlugField(
                blank=True,
                help_text="URL identifier — /resorts/<slug>/ — and the id resorts.geojson emits (SNOW-796). Minted from the name the first time the row is saved and NEVER regenerated on rename: the resort page is an indexed landing page, so a changed slug is a broken URL. Null only on a row backfill_resort_slugs has not reached yet.",
                max_length=255,
                null=True,
                unique=True,
            ),
        ),
    ]
