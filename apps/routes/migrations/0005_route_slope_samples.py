"""
apps/routes/migrations/0005_route_slope_samples.py — add terrain steepness.

Adds ``slope_samples``, the record of how steep the ground each stretch of
a track crosses is (SNOW-910).

COLUMNS ONLY. NO BACKFILL HERE, AND THAT IS NOT BECAUSE ONE IS IMPOSSIBLE.
Unlike migration 0003's timing, every existing row CAN be sampled — the
stored ``points`` are all the walk needs. But sampling one route is
roughly one HTTP request per terrain tile it crosses, against an origin
outside this process, and CLAUDE.md forbids bulk dataset updates in a
migration for exactly that reason: a deploy's ``migrate`` step would sit
on the table making network calls. ``backfill_route_slope_samples`` is the
``--commit``-gated command that does it afterwards, on an operator's
schedule rather than a deploy's.

Every existing row therefore starts null, which is the honest reading of
it: null means NEVER SAMPLED, not "no steep ground here". See
``Route.slope_samples``' help_text for why that distinction is load-bearing
and why it is not the same fact as a sampled segment with no answer.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("routes", "0004_routeshare"),
    ]

    operations = [
        migrations.AddField(
            model_name="route",
            name="slope_samples",
            field=models.JSONField(
                blank=True,
                help_text="Steepness of the GROUND the track crosses, sampled from the terrain grid at a fixed stride — never derived from the track's own elevation. Null means NEVER SAMPLED, which is not the same fact as a segment whose unknown reason is set (that is ground the survey does not cover); the two must never render alike.",
                null=True,
            ),
        ),
    ]
