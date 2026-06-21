# SNOW-333 — replace subscriber FK with a direct auth.User FK on FieldObservation,
# and update observation_type help_text to say "user" instead of "subscriber".
#
# Pre-launch: zero FieldObservation rows in production, so no RunPython backfill
# is required.  The old subscriber column is dropped and the new non-nullable
# user column is added in a single migration step.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Replace subscriber FK with a direct auth.User FK on FieldObservation.

    Also updates the observation_type help_text to reflect that the reporter
    is now described as a user rather than a subscriber.

    Zero rows in production (pre-launch) so no data backfill is needed.
    """

    dependencies = [
        ("observations", "0004_alter_help_text_latitude_longitude_region"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveField(
            model_name="fieldobservation",
            name="subscriber",
        ),
        migrations.AddField(
            model_name="fieldobservation",
            name="user",
            field=models.ForeignKey(
                help_text="User who submitted this report.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="field_observations",
                to=settings.AUTH_USER_MODEL,
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="fieldobservation",
            name="observation_type",
            field=models.CharField(
                choices=[
                    ("WHUMPFING", "Whumpfing"),
                    ("PINWHEELS", "Pinwheels"),
                    ("WIND_STRIATIONS", "Wind striations"),
                    ("FRACTURES", "Fractures"),
                    ("SHOOTING_CRACKS", "Shooting cracks"),
                ],
                help_text=(
                    "Single OBSERVATION_TYPE value reported by the user "
                    "(e.g. WHUMPFING).  To report two problems, submit two reports."
                ),
                max_length=32,
            ),
        ),
    ]
