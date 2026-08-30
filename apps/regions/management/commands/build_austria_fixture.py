"""build_austria_fixture — build apps/regions/fixtures/eaws_AT.json from EAWS data.

Reads the vendored EAWS micro-region GeoJSON files for Austria's seven
avalanche-service states and produces a Django fixture with one L1
``MajorRegion`` and multiple L2 ``SubRegion`` / L4 ``MicroRegion``
entries per state file.

Source files (vendored under reference_data/eaws/micro-regions/):
    AT-02_micro-regions.geojson.json  — Niederösterreich / Steiermark
    AT-03_micro-regions.geojson.json  — Oberösterreich
    AT-04_micro-regions.geojson.json  — Salzburg (city region)
    AT-05_micro-regions.geojson.json  — Salzburg (alpine)
    AT-06_micro-regions.geojson.json  — Kärnten
    AT-07_micro-regions.geojson.json  — Tirol
    AT-08_micro-regions.geojson.json  — Vorarlberg

L1 / L2 / L4 hierarchy derived from EAWS feature IDs:
    L1: one MajorRegion per source file, prefix = the state code
        (e.g. 'AT-02'). country='AT'.
    L2: derived by stripping the trailing segment from the feature id:
        - 3-part ID (e.g. 'AT-02-14') → L2 = the ID itself (1:1 synthetic;
          L2 boundary = L4 boundary). Happens when strip-last equals L1.
        - 4-part ID (e.g. 'AT-02-06-01') → L2 = 'AT-02-06'.
        - 5-part ID (e.g. 'AT-02-03-01-01') → L2 = 'AT-02-03-01'.
    L4: one MicroRegion per feature; region_id = feature id.
    names: L4 name from EAWS de.json; L1/L2 name_native from de.json,
        name_en from en.json. Falls back to region_id if EAWS has no entry.
    neighbours: [] (not used for intent rendering).

Note: some features serve as both L4 and the parent for deeper features
(e.g. 'AT-02-01' is a leaf L4 and also the L2 parent for 'AT-02-01-01').
This is structurally fine — one SubRegion row covers both roles.

Safe-by-default: read-only unless ``--commit`` is passed. A bare
invocation prints a summary and exits 0 without writing anything.

Usage:
    # Preview only (default — no writes).
    uv run python manage.py build_austria_fixture

    # Write apps/regions/fixtures/eaws_AT.json.
    uv run python manage.py build_austria_fixture --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.regions.fixture_utils import (
    build_entries_from_eaws_dir,
    carry_forward_preserved_fields,
    diff_against_existing,
    write_fixture,
)
from apps.regions.names import lookup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Source / output paths (module-level so tests can monkeypatch them)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_EAWS_DIR = _REPO_ROOT / "reference_data" / "eaws" / "micro-regions"

_AT_STATE_CODES = ["AT-02", "AT-03", "AT-04", "AT-05", "AT-06", "AT-07", "AT-08"]

_AUSTRIA_FIXTURE = _REPO_ROOT / "apps" / "regions" / "fixtures" / "eaws_AT.json"

_FIXTURE_TIMESTAMP = "2026-05-14T00:00:00Z"


class Command(BaseCommand):
    """Build apps/regions/fixtures/eaws_AT.json from EAWS micro-region source files.

    Read-only by default; pass ``--commit`` to write the fixture.

    SNOW-602 exempt: operates on parsed GeoJSON in memory, not a queryset.
    """

    help = (
        "Build apps/regions/fixtures/eaws_AT.json from the vendored EAWS Austria "
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

        entries = _build_entries(_EAWS_DIR, _AT_STATE_CODES)

        l1_count = sum(1 for e in entries if e["model"] == "regions.majorregion")
        l2_count = sum(1 for e in entries if e["model"] == "regions.subregion")
        l4_count = sum(1 for e in entries if e["model"] == "regions.microregion")

        if verbosity >= 1:
            self.stdout.write(f"Built: L1={l1_count} L2={l2_count} L4={l4_count}")

        # SNOW-771: the upstream EAWS source carries no centroid
        # elevation, so a rebuild would drop all of them. Carry the
        # committed values across before diffing or writing.
        entries = carry_forward_preserved_fields(_AUSTRIA_FIXTURE, entries)
        changes = diff_against_existing(_AUSTRIA_FIXTURE, entries)

        if verbosity >= 1:
            self.stdout.write(f"Change(s) vs existing fixture: {changes}")

        if not commit:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING("Dry-run (no --commit) — not writing fixture.")
                )
            return

        write_fixture(_AUSTRIA_FIXTURE, entries)

        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Wrote {_AUSTRIA_FIXTURE} ({len(entries)} entries)."
                )
            )


# ---------------------------------------------------------------------------
# Entry builders
# ---------------------------------------------------------------------------


def _build_entries(eaws_dir: Path, state_codes: list[str]) -> list[dict[str, Any]]:
    """Build the full fixture entry list (L1 + L2 + L4) for all AT state files.

    Delegates to the shared ``build_entries_from_eaws_dir`` helper in
    ``apps.regions.fixture_utils``, supplying ``_build_state_entries`` as the
    per-code builder and ``"build_austria_fixture"`` as the log label.

    Args:
        eaws_dir: Directory containing the vendored EAWS source GeoJSON files.
        state_codes: List of EAWS state codes to process (e.g. ['AT-02', …]).

    Returns:
        Ordered list of Django fixture entry dicts (L1s first, then L2s, then L4s).

    """
    return build_entries_from_eaws_dir(
        eaws_dir,
        state_codes,
        _build_state_entries,
        "build_austria_fixture",
    )


def _build_state_entries(
    l1_code: str,
    features: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Build L1, L2, and L4 entries for one Austrian state.

    Derives the L2 prefix by stripping the trailing hyphen-separated segment
    from each feature ID. If the result equals the L1 code (i.e. the feature
    is a direct 3-part child), the feature becomes its own 1:1 L2 parent.

    Args:
        l1_code: The EAWS state code, e.g. 'AT-02'.
        features: List of GeoJSON features from the state's source file.

    Returns:
        Tuple of (l1_entry, l2_entries_by_prefix, l4_entries).

    """
    from apps.regions.fixture_utils import (
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
        l4_name = lookup(region_id, "de") or region_id

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
                "name_native": lookup(l2_prefix, "de") or l2_prefix,
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
            "country": "AT",
            "name_native": lookup(l1_code, "de") or l1_code,
            "name_en": lookup(l1_code, "en") or l1_code,
            "centre": l1_centre,
            "bbox": l1_bbox,
            "boundary": l1_boundary,
            "display_on_map": True,
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

    If the result equals the L1 code (i.e. the feature is a direct child of
    L1 with no intermediate group), return the feature ID itself — a 1:1
    synthetic L2 where L2 boundary equals L4 boundary.

    Args:
        region_id: The EAWS feature ID (e.g. 'AT-02-14' or 'AT-02-06-01').
        l1_code: The L1 state code (e.g. 'AT-02').

    Returns:
        The L2 prefix string.

    """
    parts = region_id.split("-")
    candidate = "-".join(parts[:-1])
    return region_id if candidate == l1_code else candidate
