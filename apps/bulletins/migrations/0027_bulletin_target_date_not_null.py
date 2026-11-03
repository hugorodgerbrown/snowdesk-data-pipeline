# SNOW-1400: Bulletin.target_date is never null. upsert_bulletin has set it
# at ingest since SNOW-660 and the backfill command that filled older rows
# was retired by SNOW-1396. Any row still null (none was expected) is set
# from its valid_from by the same rule first, so the ALTER cannot fail on
# an environment nobody checked.

from django.db import migrations, models


def _fill_target_dates(apps, schema_editor) -> None:
    """Set a null target_date from valid_from, the rule ingest applies."""
    from apps.ingest.public import target_day_for_valid_from

    Bulletin = apps.get_model("bulletins", "Bulletin")
    for pk, valid_from in (
        Bulletin.objects.filter(target_date__isnull=True)
        .values_list("pk", "valid_from")
        .iterator()
    ):
        Bulletin.objects.filter(pk=pk).update(
            target_date=target_day_for_valid_from(valid_from)
        )


class Migration(migrations.Migration):
    """Fill any null target_date, then make the column NOT NULL."""

    dependencies = [
        ("bulletins", "0026_bulletin_keeps_no_caaml"),
    ]

    operations = [
        migrations.RunPython(_fill_target_dates, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="bulletin",
            name="target_date",
            field=models.DateField(
                db_index=True,
                help_text=(
                    "Calendar date this bulletin is forecasting, as determined by "
                    "target_day_for_valid_from() from valid_from. Set by "
                    "upsert_bulletin at ingest time."
                ),
            ),
        ),
    ]
