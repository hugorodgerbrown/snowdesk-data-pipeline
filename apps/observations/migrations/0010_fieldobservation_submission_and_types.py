"""
0010 — group a report's rows by submission; the SNOW-1289 types.

A report can now carry several types, each its own row, tied together by
``submission``. Every existing row was its own report, so each gets its own
uuid. The field goes in nullable, is filled row by row (a callable default
on AddField is evaluated ONCE, which would have made every existing row one
giant report), and is then made non-null.
"""

import uuid
from typing import Any

from django.db import migrations, models


def give_each_row_its_own_submission(apps: Any, schema_editor: Any) -> None:
    """Stamp every existing FieldObservation with a fresh submission uuid."""
    FieldObservation = apps.get_model("observations", "FieldObservation")
    for pk in FieldObservation.objects.values_list("pk", flat=True).iterator():
        FieldObservation.objects.filter(pk=pk).update(submission=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [
        ("observations", "0009_fieldobservation_location_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="fieldobservation",
            name="submission",
            field=models.UUIDField(null=True),
        ),
        migrations.RunPython(
            give_each_row_its_own_submission, reverse_code=migrations.RunPython.noop
        ),
        migrations.AlterField(
            model_name="fieldobservation",
            name="submission",
            field=models.UUIDField(
                db_index=True,
                default=uuid.uuid4,
                help_text="Groups the rows of one report: a report of three types is three rows with the same submission.",
            ),
        ),
        migrations.AlterField(
            model_name="fieldobservation",
            name="observation_type",
            field=models.CharField(
                choices=[
                    ("WHUMPFING", "Whumpfing"),
                    ("SHOOTING_CRACKS", "Shooting cracks"),
                    ("RECENT_AVALANCHE", "Recent avalanche"),
                    ("WIND_SLAB", "Fresh wind slab"),
                    ("PINWHEELS", "Pinwheels"),
                    ("WIND_STRIATIONS", "Wind striations"),
                    ("FRACTURES", "Fractures"),
                    ("POWDER", "Powder"),
                    ("WIND_PACKED", "Wind-packed"),
                    ("CRUST", "Crust"),
                    ("SPRING_SNOW", "Spring snow"),
                    ("ICY", "Icy"),
                    ("HEAVY_WET", "Heavy / wet"),
                    ("BUSY", "Busy"),
                    ("QUIET", "Quiet"),
                    ("SKIN_TRACK", "Skin track in"),
                    ("BOOTPACK", "Bootpack"),
                    ("TRACK_ICY", "Track icy"),
                    ("CLOSED", "Lift or road shut"),
                ],
                help_text="Single OBSERVATION_TYPE value reported by the user (e.g. WHUMPFING). A report of several types is several rows sharing one submission.",
                max_length=32,
            ),
        ),
    ]
