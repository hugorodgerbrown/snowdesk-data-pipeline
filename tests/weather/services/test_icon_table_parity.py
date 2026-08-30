"""
tests/weather/services/test_icon_table_parity.py — Server/client icon parity.

``/api/weather.geojson`` serves the raw WMO weather code, and the map
resolves it to a Meteocons filename in ``static/js/map_weather_core.js``.
That means the code → icon-bucket table exists twice: once in
``apps.weather.services.weather_display`` for every server-rendered surface,
once in JavaScript for the map.

A mirror with no guard is drift waiting to happen — a provider adds a code,
one side learns it, and the map quietly draws an overcast cloud over a
thunderstorm with nothing failing. This test parses the JS table and asserts
the two are the same mapping.

It lives in pytest rather than Vitest because the assertion is about a
PYTHON constant; Vitest cannot see one. The JS side's own behaviour —
``iconForCode``'s fallback and its day-variant rule — is covered in
``tests/js/test_map_weather_core.js``.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

from apps.weather.services.weather_display import _WMO_CODE_TO_ICON_BUCKET

JS_PATH = Path(settings.BASE_DIR) / "static" / "js" / "map_weather_core.js"

# The object literal, then one `code: 'bucket',` pair per line inside it.
_TABLE_RE = re.compile(r"var WMO_ICON_BUCKET = \{(.*?)\n  \};", re.DOTALL)
_ENTRY_RE = re.compile(r"^\s*(\d+):\s*'([a-z_]+)',\s*$", re.MULTILINE)


def _parse_js_table() -> dict[int, str]:
    """Read the WMO → icon-bucket table out of the JavaScript module.

    Returns:
        The parsed mapping.

    """
    body = _TABLE_RE.search(JS_PATH.read_text())
    assert body is not None, (
        "WMO_ICON_BUCKET literal not found in map_weather_core.js — if the "
        "table was reformatted, update this parser rather than deleting it."
    )
    return {int(code): bucket for code, bucket in _ENTRY_RE.findall(body.group(1))}


def test_the_javascript_table_is_not_empty() -> None:
    """The parser reads a real table, so a parse failure cannot pass silently.

    Without this, a regex that stopped matching would yield ``{}`` and the
    equality test below would compare ``{}`` to ``{}`` if the Python table
    were ever emptied too.
    """
    assert len(_parse_js_table()) > 20


def test_python_and_javascript_agree_on_every_code() -> None:
    """The two code → icon-bucket tables are the same mapping."""
    assert _parse_js_table() == _WMO_CODE_TO_ICON_BUCKET
