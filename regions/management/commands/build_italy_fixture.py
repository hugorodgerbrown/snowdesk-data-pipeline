"""build_italy_fixture — build regions/fixtures/eaws_IT.json from EAWS source data.

Reads the vendored EAWS micro-region GeoJSON files for Italy's seven
alpine avalanche-service regions and produces a Django fixture with one L1
``MajorRegion`` and multiple L2 ``SubRegion`` / L4 ``MicroRegion``
entries per region file.

Source files (vendored under reference_data/eaws/micro-regions/):
    IT-21_micro-regions.geojson.json  — Piemonte
    IT-23_micro-regions.geojson.json  — Valle d'Aosta
    IT-25_micro-regions.geojson.json  — Lombardia
    IT-32-BZ_micro-regions.geojson.json — South Tyrol / Südtirol (EUREGIO)
    IT-32-TN_micro-regions.geojson.json — Trentino (EUREGIO)
    IT-34_micro-regions.geojson.json  — Veneto
    IT-36_micro-regions.geojson.json  — Friuli-Venezia Giulia

IT-32-BZ and IT-32-TN are intentionally kept as separate L1 MajorRegion
rows, matching the EAWS file split and the EUREGIO conceptual boundary.
No parent ``IT-32`` row is created — that would be an invention without
EAWS backing.

L1 / L2 / L4 hierarchy derived from EAWS feature IDs:
    L1: one MajorRegion per source file, prefix = the region code
        (e.g. 'IT-21', 'IT-32-BZ'). country='IT'.
    L2: derived by stripping the trailing segment from the feature id:
        - For IT-21: 'IT-21-TO-05' → L2 = 'IT-21-TO' (province grouping).
        - For IT-32-BZ: 'IT-32-BZ-17' → L2 = 'IT-32-BZ-17' (1:1, direct
          child of L1). 'IT-32-BZ-15-02' → L2 = 'IT-32-BZ-15'.
    L4: one MicroRegion per feature; region_id = feature id.
    names: L4 name from EAWS it.json; L1/L2 name_native from it.json,
        name_en from en.json. Falls back to region_id if EAWS has no entry.
    neighbours: [] (not used for intent rendering).

Safe-by-default: read-only unless ``--commit`` is passed. A bare
invocation prints a summary and exits 0 without writing anything.

Usage:
    # Preview only (default — no writes).
    poetry run python manage.py build_italy_fixture

    # Write regions/fixtures/eaws_IT.json.
    poetry run python manage.py build_italy_fixture --commit
"""

from __future__ import annotations

import json
import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from regions.names import lookup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Source / output paths (module-level so tests can monkeypatch them)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_EAWS_DIR = _REPO_ROOT / "reference_data" / "eaws" / "micro-regions"

_IT_REGION_CODES = [
    "IT-21",
    "IT-23",
    "IT-25",
    "IT-32-BZ",
    "IT-32-TN",
    "IT-34",
    "IT-36",
]

_ITALY_FIXTURE = _REPO_ROOT / "regions" / "fixtures" / "eaws_IT.json"

_FIXTURE_TIMESTAMP = "2026-05-14T00:00:00Z"


class Command(BaseCommand):
    """Build regions/fixtures/eaws_IT.json from EAWS micro-region source files.

    Read-only by default; pass ``--commit`` to write the fixture.
    """

    help = (
        "Build regions/fixtures/eaws_IT.json from the vendored EAWS Italy "
        "micro-region GeoJSON files. Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Write the generated fixture to disk. Without this flag the "
                "command only reports what would be written and exits 0."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the fixture build."""
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        entries = _build_entries(_EAWS_DIR, _IT_REGION_CODES)

        l1_count = sum(1 for e in entries if e["model"] == "regions.majorregion")
        l2_count = sum(1 for e in entries if e["model"] == "regions.subregion")
        l4_count = sum(1 for e in entries if e["model"] == "regions.microregion")

        if verbosity >= 1:
            self.stdout.write(f"Built: L1={l1_count} L2={l2_count} L4={l4_count}")

        changes = _diff_against_existing(_ITALY_FIXTURE, entries)

        if verbosity >= 1:
            self.stdout.write(f"Change(s) vs existing fixture: {changes}")

        if not commit:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING("Dry-run (no --commit) — not writing fixture.")
                )
            return

        _write_fixture(_ITALY_FIXTURE, entries)

        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(f"Wrote {_ITALY_FIXTURE} ({len(entries)} entries).")
            )


# ---------------------------------------------------------------------------
# Entry builders
# ---------------------------------------------------------------------------


def _build_entries(eaws_dir: Path, region_codes: list[str]) -> list[dict[str, Any]]:
    """Build the full fixture entry list (L1 + L2 + L4) for all IT region files.

    Args:
        eaws_dir: Directory containing the vendored EAWS source GeoJSON files.
        region_codes: List of EAWS region codes to process.

    Returns:
        Ordered list of Django fixture entry dicts (L1s first, then L2s, then L4s).

    """
    l1_entries: list[dict[str, Any]] = []
    l2_entries: list[dict[str, Any]] = []
    l4_entries: list[dict[str, Any]] = []

    for code in region_codes:
        source_file = eaws_dir / f"{code}_micro-regions.geojson.json"
        if not source_file.exists():
            logger.warning(
                "build_italy_fixture: missing source file %s — skipping", source_file
            )
            continue

        data: dict[str, Any] = json.loads(source_file.read_text(encoding="utf-8"))
        features: list[dict[str, Any]] = data["features"]

        l1, l2s, l4s = _build_region_entries(code, features)
        l1_entries.append(l1)
        l2_entries.extend(l2s.values())
        l4_entries.extend(l4s)

    l4_entries.sort(key=lambda e: e["fields"]["region_id"])

    return l1_entries + l2_entries + l4_entries


def _build_region_entries(
    l1_code: str,
    features: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Build L1, L2, and L4 entries for one Italian region.

    Derives the L2 prefix by stripping the trailing hyphen-separated segment
    from each feature ID. If the result equals the L1 code, the feature
    becomes its own 1:1 L2 parent.

    Args:
        l1_code: The EAWS region code, e.g. 'IT-21' or 'IT-32-BZ'.
        features: List of GeoJSON features from the region's source file.

    Returns:
        Tuple of (l1_entry, l2_entries_by_prefix, l4_entries).

    """
    from scripts.build_regions_fixture import (
        bbox_from_children,
        boundary_from_children,
        centre_from_bbox,
        centre_from_children,
    )

    # --- L4 micro-regions ---------------------------------------------------
    l4_fields_by_region_id: dict[str, dict[str, Any]] = {}
    l2_children: dict[str, list[dict[str, Any]]] = {}

    for feature in features:
        region_id: str = feature["properties"]["id"]
        geometry: dict[str, Any] = feature["geometry"]
        centre = centre_from_bbox(geometry)
        l4_name = lookup(region_id, "it") or region_id

        l4_field: dict[str, Any] = {
            "region_id": region_id,
            "name": l4_name,
            "slug": slugify(region_id),
            "centre": centre,
            "boundary": geometry,
            "neighbours": [],
            "created_at": _FIXTURE_TIMESTAMP,
            "updated_at": _FIXTURE_TIMESTAMP,
        }
        l4_fields_by_region_id[region_id] = l4_field

        l2_prefix = _derive_l2_prefix(region_id, l1_code)
        l4_field["subregion"] = [l2_prefix]
        l2_children.setdefault(l2_prefix, []).append(l4_field)

    # --- L2 sub-regions -----------------------------------------------------
    l2_entries: dict[str, dict[str, Any]] = {}
    for l2_prefix, children in sorted(l2_children.items()):
        centre = centre_from_children(children)
        bbox = bbox_from_children(children)
        boundary = boundary_from_children(children)
        l2_entries[l2_prefix] = {
            "model": "regions.subregion",
            "fields": {
                "prefix": l2_prefix,
                "major": [l1_code],
                "name_native": lookup(l2_prefix, "it") or l2_prefix,
                "name_en": lookup(l2_prefix, "en") or l2_prefix,
                "centre": centre,
                "bbox": bbox,
                "boundary": boundary,
                "created_at": _FIXTURE_TIMESTAMP,
                "updated_at": _FIXTURE_TIMESTAMP,
            },
        }

    # --- L1 major region ----------------------------------------------------
    all_l4_fields = list(l4_fields_by_region_id.values())
    l1_centre = centre_from_children(all_l4_fields)
    l1_bbox = bbox_from_children(all_l4_fields)
    l1_boundary = boundary_from_children(all_l4_fields)

    l1_entry: dict[str, Any] = {
        "model": "regions.majorregion",
        "fields": {
            "prefix": l1_code,
            "country": "IT",
            "name_native": lookup(l1_code, "it") or l1_code,
            "name_en": lookup(l1_code, "en") or l1_code,
            "centre": l1_centre,
            "bbox": l1_bbox,
            "boundary": l1_boundary,
            "created_at": _FIXTURE_TIMESTAMP,
            "updated_at": _FIXTURE_TIMESTAMP,
        },
    }

    # Build L4 entries list (with subregion FK populated above)
    l4_entries = [
        {"model": "regions.microregion", "fields": fields}
        for fields in l4_fields_by_region_id.values()
    ]

    return l1_entry, l2_entries, l4_entries


def _derive_l2_prefix(region_id: str, l1_code: str) -> str:
    """Derive the L2 prefix for a feature by stripping its last segment.

    If stripping the last segment results in the L1 code (i.e. the feature
    is a direct child with no sub-group), return the feature ID itself — a
    1:1 synthetic L2 where L2 boundary equals L4 boundary.

    Args:
        region_id: The EAWS feature ID (e.g. 'IT-21-TO-05' or 'IT-32-BZ-17').
        l1_code: The L1 region code (e.g. 'IT-21' or 'IT-32-BZ').

    Returns:
        The L2 prefix string.

    """
    parts = region_id.split("-")
    candidate = "-".join(parts[:-1])
    return region_id if candidate == l1_code else candidate


# ---------------------------------------------------------------------------
# Fixture I/O
# ---------------------------------------------------------------------------


def _load_fixture(path: Path) -> list[dict[str, Any]]:
    """Read an existing Django fixture file; return an empty list if absent."""
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _write_fixture(path: Path, data: list[dict[str, Any]]) -> None:
    """Write a Django fixture file in the project's canonical format."""
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote %s (%d entries)", path, len(data))


def _diff_against_existing(path: Path, new_data: list[dict[str, Any]]) -> int:
    """Return the number of entries that differ from the on-disk fixture.

    Args:
        path: Path to the existing fixture file.
        new_data: Newly generated fixture entry list.

    Returns:
        Count of changed / added / removed entries.

    """
    existing = _load_fixture(path)
    new_str = json.dumps(new_data, indent=2, ensure_ascii=False)
    old_str = json.dumps(existing, indent=2, ensure_ascii=False)
    if new_str == old_str:
        return 0
    new_by_key = {_entry_key(e): json.dumps(e, sort_keys=True) for e in new_data}
    old_by_key = {_entry_key(e): json.dumps(e, sort_keys=True) for e in existing}
    all_keys = set(new_by_key) | set(old_by_key)
    return sum(1 for k in all_keys if new_by_key.get(k) != old_by_key.get(k))


def _entry_key(entry: dict[str, Any]) -> str:
    """Return a stable string key for a fixture entry (model + natural PK field)."""
    model: str = entry["model"]
    fields: dict[str, Any] = entry["fields"]
    if model == "regions.majorregion":
        return f"{model}:{fields['prefix']}"
    if model == "regions.subregion":
        return f"{model}:{fields['prefix']}"
    if model == "regions.microregion":
        return f"{model}:{fields['region_id']}"
    return f"{model}:{json.dumps(fields, sort_keys=True)}"
