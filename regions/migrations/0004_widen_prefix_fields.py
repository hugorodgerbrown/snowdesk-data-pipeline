"""Widen MajorRegion.prefix and SubRegion.prefix to fit canonical EAWS codes.

MajorRegion.prefix: 4 → 12  (e.g. 'AT-02', 'IT-32-BZ')
SubRegion.prefix:   5 → 16  (e.g. 'AT-02-14', 'IT-32-BZ-15')

No data migration is required — existing CH/FR values still fit.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Widen prefix fields to accommodate EAWS codes for AT and IT."""

    dependencies = [
        ("regions", "0003_update_microregion_subregion_help_text"),
    ]

    operations = [
        migrations.AlterField(
            model_name="majorregion",
            name="prefix",
            field=models.CharField(
                db_index=True,
                help_text="EAWS L1 prefix, e.g. 'CH-4' or 'AT-02' or 'IT-32-BZ'.",
                max_length=12,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="subregion",
            name="prefix",
            field=models.CharField(
                db_index=True,
                help_text="EAWS L2 prefix, e.g. 'CH-41' or 'AT-02-14' or 'IT-32-BZ-15'.",
                max_length=16,
                unique=True,
            ),
        ),
    ]
