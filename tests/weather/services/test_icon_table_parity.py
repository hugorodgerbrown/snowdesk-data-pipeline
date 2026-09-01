"""
tests/weather/services/test_icon_table_parity.py — Server/client icon parity.

``/api/weather.geojson`` serves the raw WMO weather code, and the map
resolves it to an SVG filename in ``static/js/map_weather_core.js``. Two
tables therefore exist twice — once in
``apps.weather.services.weather_display`` for every server-rendered surface,
once in JavaScript for the map:

1. the **code → icon-bucket** map (``_WMO_CODE_TO_ICON_BUCKET`` /
   ``WMO_ICON_BUCKET``);
2. the **buckets carrying a day/night pair**
   (``WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT`` / ``DAY_NIGHT_BUCKETS``), which
   was one string on each side until SNOW-791 swapped the icon set and made
   it a set of two.

A mirror with no guard is drift waiting to happen — a provider adds a code,
one side learns it, and the map quietly draws an overcast cloud over a
thunderstorm with nothing failing; or a bucket gains a day/night pair on one
side only and the map requests a filename that is not on disk. This test
parses both JS tables and asserts each matches its Python counterpart.

It lives in pytest rather than Vitest because the assertion is about a
PYTHON constant; Vitest cannot see one. The JS side's own behaviour —
``iconForCode``'s fallback and its day-variant rule — is covered in
``tests/js/test_map_weather_core.js``.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

from apps.weather.services.weather_display import (
    _WMO_CODE_TO_ICON_BUCKET,
    WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT,
)

JS_PATH = Path(settings.BASE_DIR) / "static" / "js" / "map_weather_core.js"

# The object literal, then one `code: 'bucket',` pair per line inside it.
_TABLE_RE = re.compile(r"var WMO_ICON_BUCKET = \{(.*?)\n  \};", re.DOTALL)
_ENTRY_RE = re.compile(r"^\s*(\d+):\s*'([a-z_]+)',\s*$", re.MULTILINE)

# The day/night array, then one quoted bucket per line inside it.
_DAY_NIGHT_RE = re.compile(r"var DAY_NIGHT_BUCKETS = \[(.*?)\n  \];", re.DOTALL)
_BUCKET_RE = re.compile(r"^\s*'([a-z_]+)',\s*$", re.MULTILINE)


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


def _parse_js_day_night_buckets() -> frozenset[str]:
    """Read the day/night bucket list out of the JavaScript module.

    Returns:
        The parsed bucket names.

    """
    body = _DAY_NIGHT_RE.search(JS_PATH.read_text())
    assert body is not None, (
        "DAY_NIGHT_BUCKETS literal not found in map_weather_core.js — if the "
        "array was reformatted, update this parser rather than deleting it."
    )
    return frozenset(_BUCKET_RE.findall(body.group(1)))


def test_the_javascript_day_night_list_is_not_empty() -> None:
    """The parser reads a real array, so a parse failure cannot pass silently.

    The same trap as :func:`test_the_javascript_table_is_not_empty`, and a
    sharper one here: an empty parse is a *plausible* value for this
    constant, since a set with no day/night buckets at all is a shape the
    code handles.
    """
    assert len(_parse_js_day_night_buckets()) > 0


def test_python_and_javascript_agree_on_the_day_night_buckets() -> None:
    """Both sides suffix ``-day``/``-night`` for exactly the same buckets."""
    assert _parse_js_day_night_buckets() == WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT
