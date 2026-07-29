# Generated for SNOW-277 — migrate BulletinShareClick onto RequestLog FK.
#
# WARNING: The RunPython step unconditionally deletes all existing
# BulletinShareClick rows before the schema change.  This is safe because
# the site is pre-launch and there is no production data to preserve.
# The reverse migration re-adds the dropped columns as nullable so a backwards
# migration is at least theoretically possible; but it cannot restore the
# deleted rows.

import django.db.models.deletion
from django.db import migrations, models


def _delete_all_share_clicks(apps, schema_editor):  # type: ignore[no-untyped-def]
    """Delete every BulletinShareClick row before the schema change.

    The site is pre-launch — no data to preserve.  The non-null ``request``
    FK cannot be added without either back-filling a RequestLog for every
    existing row or deleting them first.  Deletion is simpler and correct
    for a pre-launch environment.
    """
    BulletinShareClick = apps.get_model("bulletins", "BulletinShareClick")
    BulletinShareClick.objects.all().delete()


def _restore_inline_columns(apps, schema_editor):  # type: ignore[no-untyped-def]
    """No-op reverse for the delete step — deleted rows cannot be restored."""


class Migration(migrations.Migration):
    """Swap BulletinShareClick inline request columns for a RequestLog FK."""

    dependencies = [
        ("bulletins", "0002_bulletin_share_models"),
        ("core", "0001_requestlog"),
    ]

    operations = [
        # Step 1: purge existing rows so the non-null FK addition succeeds.
        migrations.RunPython(
            _delete_all_share_clicks,
            reverse_code=_restore_inline_columns,
        ),
        # Step 2: add the non-null FK to RequestLog.
        migrations.AddField(
            model_name="bulletinshareclick",
            name="request",
            field=models.ForeignKey(
                to="core.RequestLog",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="bulletin_share_clicks",
                help_text="Request context captured when this link was followed.",
                # Temporarily nullable so the migration can add it before
                # removing the old columns; the follow-up AlterField makes it
                # non-null.  Using a default is not appropriate for a FK.
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="bulletinshareclick",
            name="request",
            field=models.ForeignKey(
                to="core.RequestLog",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="bulletin_share_clicks",
                help_text="Request context captured when this link was followed.",
                null=False,
            ),
        ),
        # Step 3: drop the now-redundant inline request columns.
        migrations.RemoveField(
            model_name="bulletinshareclick",
            name="ip_address",
        ),
        migrations.RemoveField(
            model_name="bulletinshareclick",
            name="user_agent",
        ),
        migrations.RemoveField(
            model_name="bulletinshareclick",
            name="session_id",
        ),
        migrations.RemoveField(
            model_name="bulletinshareclick",
            name="referer",
        ),
        migrations.RemoveField(
            model_name="bulletinshareclick",
            name="sec_purpose",
        ),
        migrations.RemoveField(
            model_name="bulletinshareclick",
            name="country_code",
        ),
    ]
