# SNOW-1400: Location.short_id is never null. Its default has minted one on
# every create since SNOW-797, and the backfill command that filled older
# rows was retired by SNOW-1396. Any row still null (none was expected) is
# given one first, so the ALTER cannot fail on an environment nobody
# checked.

from django.db import migrations, models

from apps.locations.models import generate_short_id


def _fill_short_ids(apps, schema_editor) -> None:
    """Mint a short id no row holds for any location without one."""
    Location = apps.get_model("locations", "Location")
    for pk in (
        Location.objects.filter(short_id__isnull=True)
        .values_list("pk", flat=True)
        .iterator()
    ):
        candidate = generate_short_id()
        while Location.objects.filter(short_id=candidate).exists():
            candidate = generate_short_id()
        Location.objects.filter(pk=pk).update(short_id=candidate)


class Migration(migrations.Migration):
    """Fill any null short_id, then make the column NOT NULL."""

    dependencies = [
        ("locations", "0007_location_source_uuid"),
    ]

    operations = [
        migrations.RunPython(_fill_short_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="location",
            name="short_id",
            field=models.CharField(
                default=generate_short_id,
                editable=False,
                help_text=(
                    "Eleven-character opaque URL identifier — /weather/<short_id>/ and "
                    "the id weather.geojson emits (SNOW-797). Opaque rather than a slug "
                    "because most public locations are unnamed region centroids."
                ),
                max_length=16,
                unique=True,
            ),
        ),
    ]
