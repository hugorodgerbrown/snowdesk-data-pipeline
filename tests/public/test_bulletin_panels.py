"""
tests/public/test_bulletin_panels.py — The bulletin render layer's public API.

SNOW-1426 moved the render layer out of ``apps/public/views.py`` into
``apps/public/bulletin_panels.py`` (docs/decisions/public-splits-by-surface.md).
The map sheets, favourites and the MCP server import these names, so each
must be importable from the new module, and ``views.py`` must not define a
second copy. Behaviour is covered where it always was, in the bulletin page,
detail, sheet and favourites tests.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from apps.public import bulletin_panels

VIEWS = Path(bulletin_panels.__file__).with_name("views.py")

PUBLIC_API = (
    "BULLETIN_SOURCE_LINKS",
    "COUNTRY_NAMES",
    "DANGER_ORDER",
    "DANGER_PANEL_META",
    "ELEVATION_LOWER",
    "ELEVATION_UPPER",
    "PROBLEM_LABELS",
    "PROVIDER_BY_COUNTRY",
    "ElevationBounds",
    "build_bulletin_body",
    "build_resort_body",
    "enrich_render_model",
    "get_render_model",
    "plain_text",
    "problem_cards_for_bulletin",
    "resolve_region_for_bulletin",
    "resolve_selected_pick",
    "rm_elevation_bounds",
)


def _defined_in_views() -> set[str]:
    """Return every top-level name ``views.py`` defines or assigns.

    Returns:
        Function, class and assigned names; imported names are excluded.

    """
    names: set[str] = set()
    for node in ast.parse(VIEWS.read_text()).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


@pytest.mark.parametrize("name", PUBLIC_API)
def test_name_is_importable(name: str) -> None:
    """Each public name exists on the module."""
    assert hasattr(bulletin_panels, name)


def test_views_defines_none_of_them() -> None:
    """``views.py`` imports the render layer; it keeps no copy of it."""
    assert _defined_in_views().isdisjoint(PUBLIC_API)


def test_the_old_private_names_are_gone() -> None:
    """The underscore spellings were renamed, not aliased."""
    old = {
        "_DANGER_PANEL_META",
        "_PROBLEM_LABELS",
        "_DANGER_ORDER",
        "_get_render_model",
        "_plain_text",
        "_resolve_selected_pick",
        "_resolve_region_for_bulletin",
        "_rm_elevation_bounds",
        "_PROVIDER_BY_COUNTRY",
    }
    assert not old & set(dir(bulletin_panels))
    assert not old & _defined_in_views()
