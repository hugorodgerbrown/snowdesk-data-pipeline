"""
apps/favourites/migrations/0011_favourite_coordinates_leave_the_model.py.

SNOW-714, first half: ``Favourite.latitude``, ``longitude`` and ``elevation``
leave the model. A placed pin reads its coordinate and height from its
``Location`` (SNOW-704); a region pin (SNOW-802) has neither.

**The columns stay in the database for one more release.** ``build.sh`` runs
``migrate`` before the new build takes traffic, so for that window the old
build is still serving and still selects every column the model it was built
with names. Dropping them here would fail each of its Favourite queries until
the switchover. They are already nullable (0009), so the new build's inserts,
which no longer name them, succeed against them. SNOW-1413 drops
them, once no running build names them.

Before anything changes, ``refuse_an_unplaced_pin`` checks that every row
with no Location is a region pin. A placed pin still waiting for its
Location would lose its only coordinate once the columns go, so the migration
stops the deploy instead. Read-only, and one count.

The region-pin predicate loses ``latitude__isnull``: ``location`` null with a
region set is the whole of it now. The two WGS-84 range guards go with the
columns they guarded; ``Location`` carries them now
(``locations`` 0009).
"""

from typing import Any

from django.db import migrations, models
from django.db.models import Q


def refuse_an_unplaced_pin(apps: Any, schema_editor: Any) -> None:
    """Stop the migration if a pin with no Location is not a region pin.

    Args:
        apps: The historical app registry.
        schema_editor: The schema editor (unused).

    Raises:
        RuntimeError: When such a row exists.

    """
    Favourite = apps.get_model("favourites", "Favourite")
    stranded = Favourite.objects.filter(location__isnull=True).filter(
        Q(latitude__isnull=False) | Q(region__isnull=True)
    )
    count = stranded.count()
    if count:
        raise RuntimeError(
            f"{count} favourite(s) have no Location but are not region pins. "
            "Give each a Location before this migration runs (SNOW-714)."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("favourites", "0010_favourite_alerts"),
        ("locations", "0009_location_within_wgs84"),
    ]

    operations = [
        migrations.RunPython(refuse_an_unplaced_pin, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="favourite",
            name="favourite_latitude_within_wgs84",
        ),
        migrations.RemoveConstraint(
            model_name="favourite",
            name="favourite_longitude_within_wgs84",
        ),
        migrations.RemoveConstraint(
            model_name="favourite",
            name="favourite_unique_user_region_pin",
        ),
        migrations.AddConstraint(
            model_name="favourite",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("location__isnull", True), ("region__isnull", False)
                ),
                fields=("user", "region"),
                name="favourite_unique_user_region_pin",
            ),
        ),
        migrations.AlterField(
            model_name="favourite",
            name="location",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "The Location this pin is. Reaches coordinates, elevation "
                    "and weather. Null only on a region pin (SNOW-802)."
                ),
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="favourites",
                to="locations.location",
            ),
        ),
        # State only: the columns are dropped a release later (see above).
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="favourite", name="elevation"),
                migrations.RemoveField(model_name="favourite", name="latitude"),
                migrations.RemoveField(model_name="favourite", name="longitude"),
            ],
            database_operations=[],
        ),
    ]
