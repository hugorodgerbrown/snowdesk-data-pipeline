# Generated for SNOW-277 — add sec_purpose field to RequestLog.

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add sec_purpose CharField to RequestLog."""

    dependencies = [
        ("core", "0001_requestlog"),
    ]

    operations = [
        migrations.AddField(
            model_name="requestlog",
            name="sec_purpose",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Sec-Purpose header — non-empty for prefetch/prerender requests.",
                max_length=64,
            ),
        ),
    ]
