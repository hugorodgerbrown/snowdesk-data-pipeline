"""
apps/ingest/conf.py — The one place ingest reads Django settings.

Ingest imports no other ``apps.*`` package, but it still runs inside a Django
project, so its provider URLs and keys come from ``django.conf.settings``
under the names the project already uses. Reading them here, and nowhere else
in the package, means a rehosted ingest service sets exactly the names listed
below (SNOW-1249):

  SLF_API_BASE_URL, SLF_API_LEGACY_URL
  ALBINA_API_BASE_URL, ALBINA_REGIONS
  METEOFRANCE_API_BASE_URL, METEOFRANCE_API_KEY, METEOFRANCE_MASSIF_IDS
  OPEN_METEO_API_BASE_URL, OPEN_METEO_HISTORY_BASE_URL, OPEN_METEO_API_KEY
  WEATHER_BACKFILL_FLOOR (the earliest day the backfill asks for)
  WHAT3WORDS_API_BASE_URL, WHAT3WORDS_API_KEY, WHAT3WORDS_FAKE
  SWISSTOPO_STAC_URL (the ski-touring GeoPackage's STAC host)
  TERRAIN_TILE_BASE_URL (the terrain model a tour is walked against)
  DEBUG (only to gate the what3words fake)

Each accessor reads the setting at call time, not import time, so a test's
``settings`` override applies.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings


def get(name: str, default: Any = None) -> Any:
    """Return one ingest setting, or ``default`` when the project omits it.

    Args:
        name: The setting's name, e.g. ``"SLF_API_BASE_URL"``.
        default: What to return when the setting is absent.

    Returns:
        The setting's value.

    """
    return getattr(settings, name, default)


def text(name: str) -> str:
    """Return a string setting, with an absent or ``None`` value as ``""``.

    Args:
        name: The setting's name.

    Returns:
        The value as a string; empty when unset.

    """
    return str(get(name, "") or "")
