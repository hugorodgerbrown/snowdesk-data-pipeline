# SNOW-334: Repoint PasskeyCredential FK from Subscriber to auth.User.
#
# There are no live passkey rows (confirmed before this migration was written),
# so a single-step drop-add is safe — no RunPython data migration needed.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Swap PasskeyCredential.subscriber (→ Subscriber) for .user (→ auth.User)."""

    dependencies = [
        ("accounts", "0003_geo_match_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveField(
            model_name="passkeycredential",
            name="subscriber",
        ),
        migrations.AddField(
            model_name="passkeycredential",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="passkeys",
                to=settings.AUTH_USER_MODEL,
            ),
            preserve_default=False,
        ),
    ]
