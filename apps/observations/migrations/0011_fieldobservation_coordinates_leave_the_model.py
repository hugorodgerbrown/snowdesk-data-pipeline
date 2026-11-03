"""
apps/observations/migrations/0011_fieldobservation_coordinates_leave_the_model.py.

SNOW-714, first half: ``FieldObservation.latitude`` and ``longitude`` leave
the model, and ``location`` becomes required. A report reads its coordinate
from its ``Location`` (SNOW-709); ``gps_latitude`` / ``gps_longitude`` are
the raw device fix, provenance rather than a copy, and stay.

**The columns stay in the database for one more release.** ``build.sh`` runs
``migrate`` before the new build takes traffic, so for that window the old
build is still serving and still selects both columns. Dropping them here
would fail each of its report queries until the switchover. Instead they
become nullable, so the new build's inserts, which no longer name them,
succeed. SNOW-1413 drops them, once no running build names them.

``refuse_a_report_with_no_location`` stops the deploy with a plain message
if any report still has no Location; ``SET NOT NULL`` would refuse it anyway,
less legibly. Read-only, and one count.

The two WGS-84 range guards go with the columns they guarded; ``Location``
carries them now (``locations`` 0009).
"""

from typing import Any

from django.db import migrations, models


def refuse_a_report_with_no_location(apps: Any, schema_editor: Any) -> None:
    """Stop the migration if any report has no Location.

    Args:
        apps: The historical app registry.
        schema_editor: The schema editor (unused).

    Raises:
        RuntimeError: When such a row exists.

    """
    FieldObservation = apps.get_model("observations", "FieldObservation")
    count = FieldObservation.objects.filter(location__isnull=True).count()
    if count:
        raise RuntimeError(
            f"{count} field observation(s) have no Location. Give each one "
            "before this migration runs (SNOW-714)."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("observations", "0010_fieldobservation_submission_and_types"),
        ("locations", "0009_location_within_wgs84"),
    ]

    operations = [
        migrations.RunPython(
            refuse_a_report_with_no_location, migrations.RunPython.noop
        ),
        migrations.RemoveConstraint(
            model_name="fieldobservation",
            name="fieldobservation_latitude_within_wgs84",
        ),
        migrations.RemoveConstraint(
            model_name="fieldobservation",
            name="fieldobservation_longitude_within_wgs84",
        ),
        migrations.AlterField(
            model_name="fieldobservation",
            name="location",
            field=models.ForeignKey(
                help_text=(
                    "Where this observation happened: the GPS fix, refined pin "
                    "or manually-placed pin. Shared with any other observation "
                    "at the same point."
                ),
                on_delete=models.deletion.PROTECT,
                related_name="field_observations",
                to="locations.location",
            ),
        ),
        # The model forgets the columns; the database keeps them, nullable,
        # for one more release (see above).
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="fieldobservation", name="latitude"),
                migrations.RemoveField(model_name="fieldobservation", name="longitude"),
            ],
            database_operations=[
                migrations.AlterField(
                    model_name="fieldobservation",
                    name="latitude",
                    field=models.FloatField(null=True),
                ),
                migrations.AlterField(
                    model_name="fieldobservation",
                    name="longitude",
                    field=models.FloatField(null=True),
                ),
            ],
        ),
    ]
