"""
tests/public/test_weather_points.py — The point-forecast context's public API.

SNOW-1426 moved ``weather_point_context`` and its helpers out of
``apps/public/views.py`` into ``apps/public/weather_points.py``
(docs/decisions/public-splits-by-surface.md). The location weather page and
the map's weather-detail endpoint both import it from there, and
``views.py`` keeps no copy. Behaviour is covered by the weather page and
weather-detail tests.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from apps.map import api
from apps.public import views, weather_points

MOVED = (
    "weather_point_context",
    "_location_forecast_context",
    "_weather_point_places",
    "_weather_detail_heading",
)


@pytest.mark.parametrize("name", MOVED)
def test_name_lives_in_weather_points(name: str) -> None:
    """Each moved function is defined in the new module."""
    assert getattr(weather_points, name).__module__ == "apps.public.weather_points"


def test_views_defines_none_of_them() -> None:
    """``views.py`` imports the context builder; it keeps no copy of it."""
    tree = ast.parse(Path(views.__file__).read_text())
    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert defined.isdisjoint(MOVED)


def test_both_callers_share_one_builder() -> None:
    """The weather page and the map's ``weather_detail`` use one function."""
    assert views.weather_point_context is weather_points.weather_point_context
    assert api.weather_point_context is weather_points.weather_point_context
