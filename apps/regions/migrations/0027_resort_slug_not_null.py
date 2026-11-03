# SNOW-1400: Resort.slug is never null. save() has minted it from the name
# on first save since SNOW-796, and the backfill command that filled older
# rows was retired by SNOW-1396. Any row still null (none was expected) is
# given one first, by the same rule as unique_resort_slug, so the ALTER
# cannot fail on an environment nobody checked.

from django.db import migrations, models
from django.utils.text import slugify


def _fill_slugs(apps, schema_editor) -> None:
    """Mint a slug for any resort without one: slugify(name), then -2, -3."""
    Resort = apps.get_model("regions", "Resort")
    for pk, name in (
        Resort.all_objects.filter(slug__isnull=True)
        .values_list("pk", "name")
        .iterator()
    ):
        base = slugify(name) or "resort"
        taken = set(
            Resort.all_objects.filter(slug__startswith=base).values_list(
                "slug", flat=True
            )
        )
        candidate, suffix = base, 2
        while candidate in taken:
            candidate, suffix = f"{base}-{suffix}", suffix + 1
        Resort.all_objects.filter(pk=pk).update(slug=candidate)


class Migration(migrations.Migration):
    """Fill any null slug, then make the column NOT NULL."""

    dependencies = [
        ("regions", "0026_resort_retirement"),
    ]

    operations = [
        migrations.RunPython(_fill_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="resort",
            name="slug",
            field=models.SlugField(
                blank=True,
                help_text=(
                    "URL identifier — /resorts/<slug>/ — and the id resorts.geojson "
                    "emits (SNOW-796). Minted from the name the first time the row is "
                    "saved and NEVER regenerated on rename: the resort page is an "
                    "indexed landing page, so a changed slug is a broken URL."
                ),
                max_length=255,
                unique=True,
            ),
        ),
    ]
