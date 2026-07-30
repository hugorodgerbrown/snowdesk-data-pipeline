"""
apps/favourites/apps.py — AppConfig for the favourites application.

The favourites app owns the ``Favourite`` model: a saved map pin (with a
resolved ``ForecastPoint`` and best-effort ``MicroRegion``) that an
authenticated user creates, renames, or deletes from the map page.
"""

from django.apps import AppConfig


class FavouritesConfig(AppConfig):
    """AppConfig for the favourites application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.favourites"
    verbose_name = "Favourites"
