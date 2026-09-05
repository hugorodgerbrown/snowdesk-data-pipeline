"""
apps/locations/migrations/0005_location_what3words.py.

Add ``Location.what3words`` and ``Location.what3words_fetched_at``
(SNOW-840) — the cached three word address for the square a location sits
in, and the stamp that expires it.

Two plain nullable columns and nothing else. No backfill runs here, and
none is scheduled: the pair is a CACHE the licence caps at 30 days (see
docs/decisions/what3words-cache-expires-at-thirty-days.md), so filling it
ahead of anyone asking would spend paid conversions on squares nobody
reads and start a clock on all of them at once. ``fill_what3words`` fills
one row the first time a trip page renders it, which is also the rule that
keeps the estate inside the licence.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("locations", "0004_location_short_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="location",
            name="what3words",
            field=models.CharField(
                blank=True,
                help_text="Cached three word address, stored WITHOUT the /// prefix — 'filled.count.soap'. Filled lazily by fill_what3words on a read path, and EXPIRES: read it through three_word_address, never directly, because the licence caps the cache at 30 days.",
                max_length=100,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="location",
            name="what3words_fetched_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When what3words above was converted. The expiry clock, not an audit stamp — a row older than 30 days is re-converted.",
                null=True,
            ),
        ),
    ]
