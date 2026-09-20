"""
Add ``Trip.duration`` (SNOW-995) and fill it for the trips that can have it.

Unlike most late-added snapshot fields, this one CAN be backfilled: the
figure comes from the source route's ``started_at``/``finished_at``, which
are columns on a row that is still there, rather than from the uploaded
``.gpx`` that ingest discarded.

Two guards, and the second is the one that matters. The trip must still
point at a route (``route`` is ``SET_NULL``, so the organiser may have
deleted it), and that route's ``points`` must still equal the trip's
snapshot. The same pairing guard ``backfill_trip_slope_samples``'s
``_inherited_record`` uses, for the same reason: a route whose geometry no
longer matches the snapshot is a different track, and its recording time
does not describe the day this trip is for.

Everything else stays null, which is the honest answer and the one the
template already knows how to draw — the cell is omitted, not dashed.
"""

from django.db import migrations, models


def fill_duration_from_source_route(apps, schema_editor) -> None:
    """Copy each trip's source-route elapsed time onto the trip.

    Args:
        apps: The migration-state app registry.
        schema_editor: Unused; required by ``RunPython``.

    """
    Trip = apps.get_model("trips", "Trip")
    updated = []
    candidates = Trip.objects.filter(
        route__isnull=False,
        route__started_at__isnull=False,
        route__finished_at__isnull=False,
    ).select_related("route")
    for trip in candidates.iterator():
        route = trip.route
        if route.points != trip.points:
            continue
        elapsed = route.finished_at - route.started_at
        if elapsed.total_seconds() <= 0:
            continue
        trip.duration = elapsed
        updated.append(trip)
    if updated:
        Trip.objects.bulk_update(updated, ["duration"])


def clear_duration(apps, schema_editor) -> None:
    """Null the column again so the field can be dropped cleanly.

    Args:
        apps: The migration-state app registry.
        schema_editor: Unused; required by ``RunPython``.

    """
    apps.get_model("trips", "Trip").objects.update(duration=None)


class Migration(migrations.Migration):
    dependencies = [
        ("trips", "0003_trip_slope_samples"),
        ("routes", "0007_route_source_point_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="trip",
            name="duration",
            field=models.DurationField(
                blank=True,
                help_text="Snapshot of how long the source route's recording took — the length of the day, not a time of day. A LENGTH rather than a pair of timestamps, which is why a trip may carry it while started_at/finished_at stay off the snapshot: the route was recorded on some other day, but a six-hour track describes a six-hour day whenever it is skied. Elapsed, so it counts every stop the recording sat through. Null — not zero — when the source route was untimed, which is the common case: only an activity or workout export carries per-point times, and a route or course export carries none.",
                null=True,
            ),
        ),
        migrations.RunPython(
            fill_duration_from_source_route,
            clear_duration,
        ),
    ]
