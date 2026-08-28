"""
apps/routes/migrations/0003_route_started_at_finished_at.py — add timing.

Adds ``started_at`` and ``finished_at``, the two ends of the recording
(SNOW-750).

COLUMNS ONLY. NO BACKFILL, AND NONE IS POSSIBLE. Migration 0002 could
recover ``descent_m`` for existing rows because a coarser copy of the
elevation series survives in ``points``. Timing leaves no such trace: the
uploaded ``.gpx`` is parsed and discarded
(``docs/decisions/gpx-uploads-are-parsed-not-stored.md``), and no column
holds a timestamp per point. A row created before this migration has no
recoverable start or finish, and re-uploading the file is the only way to
give it one.

That is not a gap to paper over. Null here means exactly what null means
for ``ascent_m`` — "the source carried no usable timing" — and the
display layer already omits an unknown figure rather than rendering a
zero for it. Old rows simply read as untimed, which is true of them.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("routes", "0002_route_descent_m"),
    ]

    operations = [
        migrations.AddField(
            model_name="route",
            name="finished_at",
            field=models.DateTimeField(
                blank=True,
                help_text="The last track point's own <time>. Null on the same condition as started_at — the pair is read and validated together.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="route",
            name="started_at",
            field=models.DateTimeField(
                blank=True,
                help_text="The first track point's own <time>. Null when the source file carried no usable timing.",
                null=True,
            ),
        ),
    ]
