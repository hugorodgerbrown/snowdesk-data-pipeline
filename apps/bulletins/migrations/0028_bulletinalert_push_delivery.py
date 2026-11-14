# SNOW-1495: BulletinAlert stores the push delivery its message went with
# (urgency and expiry), so resend_bulletin_alert sends by the same rules
# and refuses once the bulletin has ended. Both are empty on older rows.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bulletins", "0027_bulletin_target_date_not_null"),
    ]

    operations = [
        migrations.AddField(
            model_name="bulletinalert",
            name="push_expires_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the message's bulletins stop being valid, which its push TTL runs to (SNOW-1495). A resend after it is refused.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="bulletinalert",
            name="push_urgency",
            field=models.CharField(
                blank=True,
                default="",
                help_text="RFC 8030 Urgency the message was pushed with (low, normal, high); blank on rows from before SNOW-1495 and rows that never sent. Every row of one message carries the same value.",
                max_length=8,
            ),
        ),
    ]
