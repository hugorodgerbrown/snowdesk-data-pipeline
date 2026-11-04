"""
apps/observations/migrations/0012_drop_fieldobservation_coordinate_columns.py.

SNOW-1413, the second half of SNOW-714: drop ``latitude`` and ``longitude``
from ``observations_fieldobservation``. SNOW-714 (``0011``) took them off the
model a release earlier, so no running build names them any more and the
drop cannot break the build still serving while this one deploys.
``gps_latitude`` / ``gps_longitude`` are the raw device fix and stay.

Database-only: the model state already lacks them, so ``state_operations``
is empty and ``makemigrations`` sees nothing. Reversing re-adds each column
as an empty, nullable float.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("observations", "0011_fieldobservation_coordinates_leave_the_model"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[],
            database_operations=[
                migrations.RunSQL(
                    sql="ALTER TABLE observations_fieldobservation DROP COLUMN latitude",
                    reverse_sql="ALTER TABLE observations_fieldobservation ADD COLUMN latitude double precision NULL",
                ),
                migrations.RunSQL(
                    sql="ALTER TABLE observations_fieldobservation DROP COLUMN longitude",
                    reverse_sql="ALTER TABLE observations_fieldobservation ADD COLUMN longitude double precision NULL",
                ),
            ],
        ),
    ]
