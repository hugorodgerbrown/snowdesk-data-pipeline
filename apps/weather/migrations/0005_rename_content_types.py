"""
apps/weather/migrations/0005_rename_content_types.py — carry the ContentType rows.

``0003`` renamed three models in state only, without touching their tables.
Their ``django_content_type`` rows, however, key on ``(app_label, model)``
and still hold the old ``model`` values. Left alone, ``post_migrate`` mints
fresh rows under the new names and every ``auth_permission`` and admin
``LogEntry`` stays pointed at the old ones — which then look orphaned, and
are the first thing a later cleanup would delete.

Sibling of ``0002_move_content_types``, which did the same for the SNOW-654
app-label move. The only difference is the column being rewritten: that one
moved ``app_label``, this one moves ``model``.

Touches at most three rows and takes no meaningful lock, so it cannot stall
a Render deploy.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

# Old -> new, as the lowercased ``model`` column in ``django_content_type``.
RENAMED_MODELS = (
    ("forecastpoint", "forecastcell"),
    ("forecastpointweather", "forecastcellweather"),
    ("forecastpointweatherhistory", "forecastcellweatherhistory"),
)

APP_LABEL = "weather"


def _rename(apps: Apps, pairs: tuple[tuple[str, str], ...]) -> None:
    """Rewrite the ``model`` column of the renamed models' ContentType rows.

    Idempotent in both directions, and converges on exactly one row per
    model whichever order things happened in.

    When both names somehow hold a row for the same model, the **old** one
    survives: it is the pre-rename row, so it is the one every
    ``auth_permission`` and admin ``LogEntry`` points at via
    ``content_type_id``. The new-name row in that case was minted by
    ``post_migrate`` and carries no history, so it is the one that can be
    dropped without losing anything. Deleting the wrong one cascades
    exactly the rows this migration exists to preserve.

    Args:
        apps: The historical app registry supplied by ``RunPython``.
        pairs: ``(from_model, to_model)`` pairs to rewrite.

    """
    ContentType = apps.get_model("contenttypes", "ContentType")

    for old_model, new_model in pairs:
        source = ContentType.objects.filter(
            app_label=APP_LABEL, model=old_model
        ).first()
        if source is None:
            # Nothing to rename — a fresh database, or already migrated.
            continue

        target = ContentType.objects.filter(
            app_label=APP_LABEL, model=new_model
        ).first()
        if target is not None:
            # Drop the history-less duplicate first: (app_label, model) is
            # unique, so the rename below would collide with it.
            target.delete()

        source.model = new_model
        source.save(update_fields=["model"])

    # The real ContentType manager caches lookups per (app_label, model);
    # the historical model above has no such cache, so clear it on the
    # concrete class or the rest of this migrate run reads stale rows.
    from django.contrib.contenttypes.models import (  # noqa: PLC0415 — deferred so the module stays importable without app loading
        ContentType as ConcreteContentType,
    )

    ConcreteContentType.objects.clear_cache()


def forwards(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Rename the three ContentType rows to the ``forecastcell*`` names."""
    _rename(apps, RENAMED_MODELS)


def backwards(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Rename them back to the ``forecastpoint*`` names."""
    _rename(apps, tuple((new, old) for old, new in RENAMED_MODELS))


class Migration(migrations.Migration):
    """Carry the renamed models' ContentType rows onto their new names."""

    dependencies = [
        ("weather", "0004_alter_forecastcellweather_options_and_more"),
        ("contenttypes", "__first__"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards, elidable=False),
    ]
