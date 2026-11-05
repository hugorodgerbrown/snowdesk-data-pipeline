"""
apps/map — The interactive map (SNOW-1428).

The map page and its context builders, the map's JSON endpoints, the
in-map editor, and the region and place sheets. It imports apps/public only
through ``bulletin_panels``, ``weather_points``, ``decorators``, ``errors``,
``season_calendar`` and ``map_shared``; only the URL modules import it
(docs/decisions/public-splits-by-surface.md, tests/map/test_boundary.py).
"""
