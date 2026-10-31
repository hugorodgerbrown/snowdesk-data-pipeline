"""
tests/public/test_main_css_theme_parity.py — the map surface's light tokens.

SNOW-1334: in dark mode the map surface stays light. ``.dark
.map-surface-light`` in src/css/main.css restates, at its light value,
every token ``.dark`` overrides. It is a copy, because once ``.dark`` has
rewritten a token on <html> the light value is gone. These tests catch the
copy drifting: a token added to ``.dark`` and not restated (it would turn
dark on the map), or a light value changed in ``@theme`` and not here (the
map would keep the old one).

Only the names ``.dark`` overrides are compared, not every ``@theme`` token,
because theme-invariant tokens are deliberately absent from ``.dark``.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

MAIN_CSS = Path(settings.BASE_DIR) / "src/css/main.css"

_DECLARATION = re.compile(r"^\s*(--[\w-]+)\s*:\s*([^;]+);", re.M)


def _block(css: str, selector: str) -> str:
    """Return the body of the one rule whose selector is ``selector``."""
    start = css.index(f"\n{selector} {{") + len(selector) + 3
    return css[start : css.index("\n}", start)]


def _declarations(body: str) -> dict[str, str]:
    """Map each custom property declared in ``body`` to its value."""
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return {name: value.strip() for name, value in _DECLARATION.findall(body)}


def _light_values(css: str) -> dict[str, str]:
    """Each custom property's light value: its last declaration before ``.dark``."""
    return _declarations(css[: css.index("\n.dark {")])


def test_every_token_dark_overrides_is_restated_on_the_map_surface() -> None:
    """No token ``.dark`` changes is left dark on the map surface."""
    css = MAIN_CSS.read_text()
    dark = _declarations(_block(css, ".dark"))
    surface = _declarations(_block(css, ".dark .map-surface-light"))

    assert sorted(set(dark) - set(surface)) == []


def test_the_map_surface_restates_the_light_values_exactly() -> None:
    """Each restated token carries its ``@theme`` light value."""
    css = MAIN_CSS.read_text()
    light = _light_values(css)
    surface = _declarations(_block(css, ".dark .map-surface-light"))

    assert {name: light.get(name) for name in surface} == surface


def test_the_map_surface_restates_nothing_dark_leaves_alone() -> None:
    """The block restates only what ``.dark`` overrides, so it stays a mirror."""
    css = MAIN_CSS.read_text()
    dark = _declarations(_block(css, ".dark"))
    surface = _declarations(_block(css, ".dark .map-surface-light"))

    assert sorted(set(surface) - set(dark)) == []
