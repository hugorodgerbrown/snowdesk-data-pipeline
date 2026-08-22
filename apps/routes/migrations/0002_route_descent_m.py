"""
apps/routes/migrations/0002_route_descent_m.py — add Route.descent_m.

Adds the field, then backfills every row that already exists.

WHY THE BACKFILL IS MEASURED ON A COARSER TRACK THAN ``ascent_m`` IS.
``distance_m`` and ``ascent_m`` are derived at ingest from the
full-resolution track, before Douglas-Peucker thins it — see
``apps.routes.services.gpx.parse_gpx``. A row created before this
migration cannot be given a figure on those terms: the uploaded ``.gpx``
is parsed and discarded and never written to disk
(``docs/decisions/gpx-uploads-are-parsed-not-stored.md``), so the only
geometry still in existence for an old row is the simplified ``points``
in its own column.

Three options, and the reason for the one taken:

  * **leave old rows null** — rejected. Null already means "the source
    file carried no elevation data", which the display layer reads as
    "unknown" and honours by omitting the figure entirely. Reusing it for
    "not computed yet" would overload one null with two facts, and a row
    showing an ascent but no descent would read as a data bug on a track
    that plainly has both;
  * **recompute ascent from ``points`` too**, so both figures come from
    one geometry — rejected. That throws away a better number to make a
    worse one consistent with it;
  * **backfill descent from ``points``** — taken. The error is small and
    bounded in a knowable direction: Douglas-Peucker runs over the
    (lon, lat) plane only and returns a subsequence of its input, so it
    drops horizontally near-collinear vertices. What that removes from
    the elevation series is mostly small non-monotonic wiggle, which is
    GPS/barometric noise as often as it is terrain, so the backfilled
    descent tends to sit slightly UNDER a full-resolution one rather than
    over it. Under-reporting a drop is the safe direction to be wrong in,
    and it only applies to rows already in the table — every row created
    from here on gets the full-resolution figure.

Only rows whose stored ``points`` actually carry elevation are given a
number; a track of ``[lon, lat, null]`` triples keeps its null, which for
that row is the true and permanent answer.

Reverse is a no-op beyond dropping the column: there is nothing to
restore.
"""

from django.db import migrations, models


def _backfill_descent(apps, schema_editor):
    """Set ``descent_m`` on every existing row from its stored ``points``.

    Mirrors ``apps.routes.services.gpx._total_ascent_descent_m``'s descent
    half — deliberately inlined rather than imported, because a migration
    must keep behaving the way it did the day it was written even after
    the service it was modelled on has moved on.

    Args:
        apps: The historical app registry.
        schema_editor: The schema editor (unused; the writes go through
            the ORM).

    """
    Route = apps.get_model("routes", "Route")

    updated = []
    # Iterate rather than materialise a list of model instances plus a
    # second list of updates: the table is small, but the shape should not
    # depend on that being true.
    for route in Route.objects.all().iterator():
        elevations = [
            point[2] if len(point) > 2 else None for point in (route.points or [])
        ]
        if all(elevation is None for elevation in elevations):
            # No elevation in the stored geometry — the null stands.
            continue

        descent = 0.0
        for previous, current in zip(elevations, elevations[1:]):
            if previous is None or current is None:
                continue
            delta = current - previous
            if delta < 0:
                descent -= delta

        route.descent_m = descent
        updated.append(route)

    if updated:
        Route.objects.bulk_update(updated, ["descent_m"], batch_size=500)


def _noop_reverse(apps, schema_editor):
    """Reverse the backfill — nothing to do.

    The column itself is dropped by the reversed ``AddField``, which takes
    every value with it.

    Args:
        apps: The historical app registry (unused).
        schema_editor: The schema editor (unused).

    """


class Migration(migrations.Migration):
    """Add ``Route.descent_m`` and backfill it from the stored geometry."""

    dependencies = [
        ("routes", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="route",
            name="descent_m",
            field=models.FloatField(
                blank=True,
                help_text=(
                    "Total elevation loss in metres over the full-resolution "
                    "track, as a positive magnitude. Null on the same "
                    "condition as ascent_m — the two are read off one "
                    "elevation series and are always known or unknown "
                    "together."
                ),
                null=True,
            ),
        ),
        migrations.RunPython(_backfill_descent, _noop_reverse),
    ]
