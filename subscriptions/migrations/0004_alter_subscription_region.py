"""SNOW-140: state-only repoint of Subscription.region from pipeline to regions.

Updates Django's migration state so that ``Subscription.region`` points
at ``regions.region`` instead of ``pipeline.region``. The underlying
foreign-key constraint is unchanged because both targets map to the
same physical table (``pipeline_region``); ``database_operations`` is
empty.

Pairs with ``regions.0001_initial`` and
``pipeline.0020_remove_reference_models``.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """State-only re-target of Subscription.region."""

    dependencies = [
        ("regions", "0001_initial"),
        ("subscriptions", "0003_create_cache_table"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="subscription",
                    name="region",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subscriptions",
                        to="regions.region",
                    ),
                ),
            ],
        ),
    ]
