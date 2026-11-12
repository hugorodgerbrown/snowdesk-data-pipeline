"""
tests/observations/test_family_colour_parity.py — the family colours agree.

A field report's family colour is written twice (SNOW-1290): as the
``--color-obs-*`` tokens in ``src/css/main.css``, which every template
surface reads, and as ``FAMILY_COLOURS`` in
``static/js/community_reports_core.js``, because MapLibre paint cannot read
a CSS custom property. A drift would draw a pin one colour and its legend
swatch and region-panel row another, so this reads both files and compares.

The reader's own reports are drawn in ``MINE_COLOUR`` (SNOW-1480), the JS
copy of ``--color-accent`` — the same parity, for the same reason.
"""

import re
from pathlib import Path

from django.conf import settings

from apps.observations.models import FieldObservation

CSS = Path(settings.BASE_DIR) / "src" / "css" / "main.css"
JS = Path(settings.BASE_DIR) / "static" / "js" / "community_reports_core.js"


def _css_colours() -> dict[str, str]:
    """Return the light-theme ``--color-obs-*`` values, keyed by family."""
    text = CSS.read_text()
    colours: dict[str, str] = {}
    for name, value in re.findall(r"--color-obs-(\w+):\s*(#[0-9a-fA-F]{6})", text):
        # The first declaration is the light theme's @theme value; the dark
        # block's override comes later and is not what the map draws on.
        colours.setdefault(name.upper(), value.lower())
    return colours


def _js_colours() -> dict[str, str]:
    """Return ``FAMILY_COLOURS`` from the core module."""
    block = re.search(
        r"FAMILY_COLOURS = Object\.freeze\(\{(.*?)\}\)", JS.read_text(), re.S
    )
    assert block, "FAMILY_COLOURS not found in community_reports_core.js"
    return {
        key: value.lower()
        for key, value in re.findall(r"(\w+):\s*'(#[0-9a-fA-F]{6})'", block.group(1))
    }


def test_every_family_has_a_token() -> None:
    """Each OBSERVATION_FAMILY value has a --color-obs-* token."""
    assert set(_css_colours()) == set(FieldObservation.OBSERVATION_FAMILY.values)


def test_map_colours_match_the_tokens() -> None:
    """The pins are drawn in exactly the token colours."""
    assert _js_colours() == _css_colours()


def test_mine_colour_matches_the_accent_token() -> None:
    """The reader's own pins are drawn in the light theme's --color-accent."""
    accent = re.search(r"--color-accent:\s*(#[0-9a-fA-F]{6})", CSS.read_text())
    assert accent, "--color-accent not found in main.css"
    mine = re.search(r"MINE_COLOUR = '(#[0-9a-fA-F]{6})'", JS.read_text())
    assert mine, "MINE_COLOUR not found in community_reports_core.js"
    assert mine.group(1).lower() == accent.group(1).lower()
