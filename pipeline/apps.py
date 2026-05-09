"""
pipeline/apps.py — AppConfig stub kept for migration history (SNOW-140).

The pipeline app no longer owns any models, services, commands, or
URLs — the reference-data models moved to ``regions`` and the
SLF-specific code moved to ``bulletins`` (see SNOW-140).

The app remains in ``INSTALLED_APPS`` purely so that the existing
migration history (0001–0020) continues to load. Several downstream
migrations declare ``("pipeline", "0019_close_region_boundary_rings")``
or ``("pipeline", "0020_remove_reference_models")`` as a dependency;
removing the app would break the migration graph.

Deletion is deferred indefinitely — leaving the stub costs nothing
at runtime and avoids ContentType / migration-history surgery.
"""

from django.apps import AppConfig


class PipelineConfig(AppConfig):
    """Django application stub for legacy migration history."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pipeline"
    verbose_name = "Pipeline (legacy)"
