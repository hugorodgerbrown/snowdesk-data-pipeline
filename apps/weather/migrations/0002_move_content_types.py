"""
apps/weather/migrations/0002_move_content_types.py — Carry the ContentType rows over.

SNOW-654 moved four models from ``bulletins`` to ``weather`` without
touching their tables. Their ``django_content_type`` rows, however, are
keyed on ``(app_label, model)``, so they still read ``bulletins``. Left
alone, the ``contenttypes`` ``post_migrate`` hook mints four *new* rows
under ``weather`` and the old ones are stranded — taking every
``auth_permission`` row and every admin ``LogEntry`` that points at them
with it (permissions and log entries hold a ``content_type_id``, not a
label).

Migrations run *before* ``post_migrate``, so rewriting the label here
wins: by the time the hook runs it finds the rows already present and
creates nothing.

This rewrites four rows. That is not a bulk data update, so it does not
breach the project's "no large dataset updates in migrations" rule (see
``docs/decisions/`` and CLAUDE.md) — a four-row ``UPDATE`` takes no
meaningful lock and cannot stall a Render deploy.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from apps.core.contenttype_migrations import rewrite_content_type_rows

# The four models that moved. Values are the lowercased ``model`` column
# in ``django_content_type``.
MOVED_MODELS = (
    "weathersnapshot",
    "forecastpoint",
    "forecastpointweather",
    "forecastpointweatherhistory",
)

OLD_LABEL = "bulletins"
NEW_LABEL = "weather"


def _relabel(apps: Apps, from_label: str, to_label: str) -> None:
    """Move the four moved models' ContentType rows between app labels.

    Routes through
    ``apps.core.contenttype_migrations.rewrite_content_type_rows`` for the
    row-rewrite itself (tie-break-on-collision behaviour documented
    there). Permission codenames don't embed the app label, so — unlike
    the model rename in the sibling ``0005_rename_content_types`` — the
    permissions that follow each row across are already correctly named
    and ``post_migrate`` re-creates nothing; no extra step is needed here.

    Args:
        apps: The historical app registry supplied by ``RunPython``.
        from_label: The ``app_label`` the rows currently carry.
        to_label: The ``app_label`` they should carry.

    """
    rewrite_content_type_rows(
        apps,
        (
            (
                {"app_label": from_label, "model": model},
                {"app_label": to_label, "model": model},
                {"app_label": to_label},
            )
            for model in MOVED_MODELS
        ),
    )


def forwards(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Relabel the four ContentType rows from ``bulletins`` to ``weather``."""
    _relabel(apps, OLD_LABEL, NEW_LABEL)


def backwards(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Relabel the four ContentType rows back from ``weather`` to ``bulletins``."""
    _relabel(apps, NEW_LABEL, OLD_LABEL)


class Migration(migrations.Migration):
    """Carry the moved models' ContentType rows across to the new app label."""

    dependencies = [
        ("weather", "0001_initial"),
        ("contenttypes", "__first__"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards, elidable=False),
    ]
