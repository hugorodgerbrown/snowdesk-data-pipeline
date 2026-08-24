"""
apps/weather/migrations/0005_rename_content_types.py — carry the ContentType rows.

``0003`` renamed three models in state only, without touching their tables.
Their ``django_content_type`` rows, however, key on ``(app_label, model)``
and still hold the old ``model`` values. Left alone, ``post_migrate`` mints
fresh rows under the new names and every ``auth_permission`` and admin
``LogEntry`` stays pointed at the old ones — which then look orphaned, and
are the first thing a later cleanup would delete.

Sibling of ``0002_move_content_types``, which did the same for the SNOW-654
app-label move — both route through
``apps.core.contenttype_migrations.rewrite_content_type_rows`` for the
row-rewrite itself. Where this migration differs is the column: that one
rewrote ``app_label``, this one rewrites ``model`` — and unlike
``app_label``, ``model`` is embedded in every affected ``auth_permission``
row's ``codename`` (``add_forecastpoint``, ``view_forecastpoint``, …),
which Django derives once at creation time and never revisits. Left alone,
a previously-granted permission's codename would keep saying the old model
name forever, silently mismatching the codename Django's own permission
checks build from the live (renamed) model — so this migration also
rewrites that codename suffix, which ``0002`` never needed to.

Touches at most three ContentType rows and their permissions, and takes no
meaningful lock, so it cannot stall a Render deploy.
"""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from apps.core.contenttype_migrations import rewrite_content_type_rows

# Old -> new, as the lowercased ``model`` column in ``django_content_type``.
RENAMED_MODELS = (
    ("forecastpoint", "forecastcell"),
    ("forecastpointweather", "forecastcellweather"),
    ("forecastpointweatherhistory", "forecastcellweatherhistory"),
)

APP_LABEL = "weather"


def _rename_permission_codenames(
    apps: Apps, pairs: tuple[tuple[str, str], ...]
) -> None:
    """Rewrite the codename suffix of every Permission tied to a renamed model.

    ``ContentType.model`` is rewritten by ``rewrite_content_type_rows``
    above, but ``Permission.codename`` is a separate column Django derives
    once at creation time (``add_<model>``, ``change_<model>``, …) and
    never revisits. Left alone, a codename like ``view_forecastpoint``
    keeps pointing at the renamed content type while Django's own
    permission checks (which build the string from the *live* model name)
    look for ``view_forecastcell`` — so a previously-granted, non-superuser
    permission silently stops matching, and ``post_migrate`` mints a fresh,
    zero-grant duplicate under the new codename.

    Idempotent, and safe on a database where ``post_migrate`` already
    created the new-codename permissions: the history-less duplicate is
    dropped first, exactly as ``rewrite_content_type_rows`` does for
    ContentType rows themselves, so a permission's grants and its
    ``content_type_id`` never move — only the codename text changes.

    Args:
        apps: The historical app registry supplied by ``RunPython``.
        pairs: ``(from_model, to_model)`` pairs, as passed to ``_rename``.

    """
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")

    for old_model, new_model in pairs:
        content_type = ContentType.objects.filter(
            app_label=APP_LABEL, model=new_model
        ).first()
        if content_type is None:
            # Nothing rewritten above (fresh database) — nothing to fix here.
            continue

        old_suffix = f"_{old_model}"
        new_suffix = f"_{new_model}"
        for permission in Permission.objects.filter(
            content_type=content_type, codename__endswith=old_suffix
        ):
            new_codename = permission.codename[: -len(old_suffix)] + new_suffix

            duplicate = (
                Permission.objects.filter(
                    content_type=content_type, codename=new_codename
                )
                .exclude(pk=permission.pk)
                .first()
            )
            if duplicate is not None:
                # Drop the history-less duplicate first: (content_type,
                # codename) is unique, so the rewrite below would collide.
                duplicate.delete()

            permission.codename = new_codename
            permission.save(update_fields=["codename"])


def _rename(apps: Apps, pairs: tuple[tuple[str, str], ...]) -> None:
    """Rewrite the renamed models' ContentType rows and permission codenames.

    Args:
        apps: The historical app registry supplied by ``RunPython``.
        pairs: ``(from_model, to_model)`` pairs to rewrite.

    """
    rewrite_content_type_rows(
        apps,
        (
            (
                {"app_label": APP_LABEL, "model": old_model},
                {"app_label": APP_LABEL, "model": new_model},
                {"model": new_model},
            )
            for old_model, new_model in pairs
        ),
    )
    _rename_permission_codenames(apps, pairs)


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
        ("auth", "__first__"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards, elidable=False),
    ]
