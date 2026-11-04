"""
apps/favourites/migrations/0012_drop_favourite_coordinate_columns.py.

SNOW-1413, the second half of SNOW-714: drop ``latitude``, ``longitude`` and
``elevation`` from ``favourites_favourite``. SNOW-714 (``0011``) took them
off the model a release earlier, so no running build names them any more and
the drop cannot break the build still serving while this one deploys.

Database-only: the model state already lacks them, so ``state_operations``
is empty and ``makemigrations`` sees nothing. Reversing re-adds each column
as an empty, nullable float.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("favourites", "0011_favourite_coordinates_leave_the_model"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[],
            database_operations=[
                migrations.RunSQL(
                    sql="ALTER TABLE favourites_favourite DROP COLUMN latitude",
                    reverse_sql="ALTER TABLE favourites_favourite ADD COLUMN latitude double precision NULL",
                ),
                migrations.RunSQL(
                    sql="ALTER TABLE favourites_favourite DROP COLUMN longitude",
                    reverse_sql="ALTER TABLE favourites_favourite ADD COLUMN longitude double precision NULL",
                ),
                migrations.RunSQL(
                    sql="ALTER TABLE favourites_favourite DROP COLUMN elevation",
                    reverse_sql="ALTER TABLE favourites_favourite ADD COLUMN elevation double precision NULL",
                ),
            ],
        ),
    ]
