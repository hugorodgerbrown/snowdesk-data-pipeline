"""SNOW-142: state-only repoint of Subscription.region from regions.region to regions.microregion.

After regions.0002 renames the Region model to MicroRegion, Django's
migration state still records Subscription.region as pointing at
regions.region.  This migration updates that reference to regions.microregion
so the state graph is consistent.

No DDL runs — the physical FK constraint is unchanged.
``database_operations`` is empty.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """State-only repoint of Subscription.region FK to MicroRegion."""

    dependencies = [
        ("regions", "0002_rename_models"),
        ("subscriptions", "0004_alter_subscription_region"),
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
                        to="regions.microregion",
                    ),
                ),
            ],
        ),
    ]
