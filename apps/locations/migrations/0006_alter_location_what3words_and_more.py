"""
apps/locations/migrations/0006_alter_location_what3words_and_more.py.

Re-document ``Location.what3words`` and ``Location.what3words_fetched_at``
(SNOW-861). HELP TEXT ONLY — no schema change, no data change, and nothing
to run out of hours.

The columns were introduced as a cache the licence capped at 30 days. That
cap governs ``convert-to-coordinates``; Snowdesk derives an address from
its own pin via ``convert-to-3wa``, which is unmetered and storable
indefinitely (what3words, in writing, September 2026). The expiry is gone
from the model, so the two ``help_text`` strings that described it — one
calling the column a cache that EXPIRES, one calling the stamp an expiry
clock — would otherwise keep saying so in the admin.

See docs/decisions/what3words-addresses-are-stored-indefinitely.md.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("locations", "0005_location_what3words"),
    ]

    operations = [
        migrations.AlterField(
            model_name="location",
            name="what3words",
            field=models.CharField(
                blank=True,
                help_text="Three word address, stored WITHOUT the /// prefix — 'filled.count.soap'. Derived from the coordinate and resolved out of band by fill_what3words; null until that has run. Read it through three_word_address rather than directly.",
                max_length=100,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="location",
            name="what3words_fetched_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When what3words above was converted. Provenance, not an expiry clock — the address does not go stale, because the square it names cannot move.",
                null=True,
            ),
        ),
    ]
