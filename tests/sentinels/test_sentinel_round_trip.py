"""
tests/sentinels/test_sentinel_round_trip.py — Smoke tests for the sentinel bulletins.

Two parametrised test suites:

1. ``test_source_json_passes_render_model`` — for every ``source.json`` under
   ``tests/sentinels/``, load the bulletin dict, assert that ``bulletinID`` is a
   non-empty string and ``dangerRatings`` is a non-empty list, then call
   ``build_render_model()`` and assert it does not raise.

2. ``test_mf_xml_round_trips_to_source_json`` — for every ``source.xml`` under
   ``tests/sentinels/meteofrance/``, load the raw bytes, call
   ``parse_dpbra_xml(xml_bytes)`` and assert the result equals the committed
   ``source.json`` sitting next to it.

Neither test writes to the database, makes network calls, or requires factories.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apps.bulletins.services.meteofrance_translator import parse_dpbra_xml
from apps.bulletins.services.render_model import build_render_model

SENTINELS_DIR = Path(__file__).resolve().parent


def _all_source_json_paths() -> list[Path]:
    """Return all ``source.json`` files under the sentinels directory."""
    return sorted(SENTINELS_DIR.glob("*/*/source.json"))


def _all_mf_xml_paths() -> list[Path]:
    """Return all ``source.xml`` files under the ``meteofrance/`` sentinel subtree."""
    return sorted((SENTINELS_DIR / "meteofrance").glob("*/source.xml"))


def _sentinel_id(path: Path) -> str:
    """Return a short human-readable test ID from a sentinel file path.

    Args:
        path: Absolute path to a sentinel ``source.*`` file.

    Returns:
        A string like ``"slf/A-single-level"`` suitable for use as a pytest ID.

    """
    # Relative path from sentinels root: <source>/<variant>/source.*
    rel = path.relative_to(SENTINELS_DIR)
    return str(rel.parent)


@pytest.mark.parametrize(
    "source_path",
    _all_source_json_paths(),
    ids=_sentinel_id,
)
def test_source_json_passes_render_model(source_path: Path) -> None:
    """Smoke-test a sentinel ``source.json`` through ``build_render_model``.

    Asserts:
    - ``bulletinID`` is a non-empty string.
    - ``dangerRatings`` is a non-empty list.
    - ``build_render_model(properties)`` does not raise.

    Args:
        source_path: Path to a committed ``source.json`` sentinel file.

    """
    raw: dict[str, Any] = json.loads(source_path.read_text(encoding="utf-8"))

    bulletin_id: Any = raw.get("bulletinID")
    assert isinstance(bulletin_id, str) and bulletin_id, (
        f"{source_path}: expected non-empty string bulletinID, got {bulletin_id!r}"
    )

    danger_ratings: Any = raw.get("dangerRatings")
    assert isinstance(danger_ratings, list) and len(danger_ratings) > 0, (
        f"{source_path}: expected non-empty dangerRatings list, got {danger_ratings!r}"
    )

    # Must not raise — any render-model error surfaces as a test failure here.
    build_render_model(raw)


@pytest.mark.parametrize(
    "xml_path",
    _all_mf_xml_paths(),
    ids=_sentinel_id,
)
def test_mf_xml_round_trips_to_source_json(xml_path: Path) -> None:
    """Verify that a committed MF ``source.xml`` round-trips through the translator.

    Loads the raw XML bytes, calls ``parse_dpbra_xml()``, and asserts the result
    equals the committed ``source.json`` that lives in the same directory.

    This proves two things:
    - The committed ``source.json`` was genuinely produced by the live translator.
    - The translator has not drifted since the sentinel was committed.

    Args:
        xml_path: Path to a committed ``source.xml`` MF sentinel file.

    """
    xml_bytes = xml_path.read_bytes()
    json_path = xml_path.with_name("source.json")

    expected: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))
    actual: dict[str, Any] = parse_dpbra_xml(xml_bytes)

    assert actual == expected, (
        f"{xml_path}: parse_dpbra_xml() output does not match committed source.json.\n"
        "Re-generate source.json by running parse_dpbra_xml() against the XML and\n"
        "committing the result if the translator has legitimately changed."
    )
