"""build_france_fixture — build regions/fixtures/france.json from source data.

Combines three source files to produce a Django fixture with 4 L1
``MajorRegion``, 4 L2 ``SubRegion`` (one placeholder per L1), and 35 L4
``MicroRegion`` entries for Metropolitan France:

    sample_data/eaws/FR_micro-regions.geojson  — EAWS L4 IDs + geometry
    sample_data/eaws/fr_names.json             — EAWS canonical names per FR-NN
    sample_data/liste-massifs.geojson          — MF massif → mountain grouping

The EAWS ``FR-NN`` ID and the MF integer ``code`` are 1:1
(``int("FR-68".split("-")[1]) == 68``). EAWS is the canonical source for
IDs, names, and geometry; MF provides the 4-mountain grouping used as L1.

L1/L2 hierarchy:
    FR-1  / FR-1A  — Alpes du Nord   (codes 1–15 approx.)
    FR-2  / FR-2A  — Alpes du Sud    (codes 13–23 approx.)
    FR-3  / FR-3A  — Pyrenees        (codes 64–74)
    FR-4  / FR-4A  — Corse           (codes 40–41)

Convention note: the Swiss-fixture convention ``region_id[:5] == sub.prefix``
and ``region_id[:4] == major.prefix`` **does not hold** for French rows —
FKs (the ``subregion`` natural-key list) are authoritative, not slicing.

Safe-by-default: read-only unless ``--commit`` is passed. A bare invocation
prints a summary and exits 0 without writing anything.

Usage:
    # Preview only (default — no writes).
    poetry run python manage.py build_france_fixture

    # Write regions/fixtures/france.json.
    poetry run python manage.py build_france_fixture --commit
"""

from __future__ import annotations

import json
import logging
from argparse import ArgumentParser
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand
from django.utils.text import slugify

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Source / output paths (module-level so tests can monkeypatch them)
# ---------------------------------------------------------------------------

_BASE_DIR = Path("sample_data")
_EAWS_GEOJSON = _BASE_DIR / "eaws" / "FR_micro-regions.geojson"
_FR_NAMES = _BASE_DIR / "eaws" / "fr_names.json"
_MF_MASSIFS = _BASE_DIR / "liste-massifs.geojson"

_FRANCE_FIXTURE = Path("regions/fixtures/france.json")

# ---------------------------------------------------------------------------
# L1 mountain → (major prefix, sub prefix)
# ---------------------------------------------------------------------------

_MOUNTAIN_PREFIXES: dict[str, tuple[str, str]] = {
    "Alpes du Nord": ("FR-1", "FR-1A"),
    "Alpes du Sud": ("FR-2", "FR-2A"),
    "Pyrenees": ("FR-3", "FR-3A"),
    "Corse": ("FR-4", "FR-4A"),
}

# Canonical mountain order for stable fixture output
_MOUNTAIN_ORDER = ["Alpes du Nord", "Alpes du Sud", "Pyrenees", "Corse"]


class Command(BaseCommand):
    """Build regions/fixtures/france.json from EAWS + MF source files.

    Read-only by default; pass ``--commit`` to write the fixture.
    """

    help = (
        "Build regions/fixtures/france.json from EAWS micro-region geometry "
        "and MeteoFrance massif groupings. Read-only unless --commit is passed."
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

        eaws_features = _load_geojson(_EAWS_GEOJSON)
        fr_names = _load_json(_FR_NAMES)
        mf_code_to_mountain = _load_mf_mountain_map(_MF_MASSIFS)

        entries = _build_entries(eaws_features, fr_names, mf_code_to_mountain)

        l1_count = sum(1 for e in entries if e["model"] == "regions.majorregion")
        l2_count = sum(1 for e in entries if e["model"] == "regions.subregion")
        l4_count = sum(1 for e in entries if e["model"] == "regions.microregion")

        if verbosity >= 1:
            self.stdout.write(f"Built: L1={l1_count} L2={l2_count} L4={l4_count}")

        changes = _diff_against_existing(_FRANCE_FIXTURE, entries)

        if verbosity >= 1:
            self.stdout.write(f"Change(s) vs existing fixture: {changes}")

        if not commit:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING("Dry-run (no --commit) — not writing fixture.")
                )
            return

        _write_fixture(_FRANCE_FIXTURE, entries)

        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(f"Wrote {_FRANCE_FIXTURE} ({len(entries)} entries).")
            )


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_geojson(path: Path) -> list[dict[str, Any]]:
    """Load a GeoJSON FeatureCollection and return its features list."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data["features"]  # type: ignore[no-any-return]


def _load_json(path: Path) -> dict[str, Any]:
    """Load a plain JSON object from *path*."""
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _load_mf_mountain_map(path: Path) -> dict[int, str]:
    """Build ``{code: mountain}`` from the MF massifs GeoJSON."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return {
        f["properties"]["code"]: f["properties"]["mountain"] for f in data["features"]
    }


# ---------------------------------------------------------------------------
# Entry builders
# ---------------------------------------------------------------------------


def _build_entries(
    eaws_features: list[dict[str, Any]],
    fr_names: dict[str, str],
    mf_code_to_mountain: dict[int, str],
) -> list[dict[str, Any]]:
    """Build the full fixture entry list (L1 + L2 + L4)."""
    # --- L4 micro-regions ---------------------------------------------------
    l4_by_mountain: dict[str, list[dict[str, Any]]] = {m: [] for m in _MOUNTAIN_ORDER}

    l4_entries: list[dict[str, Any]] = []
    for feature in eaws_features:
        region_id: str = feature["properties"]["id"]
        code = int(region_id.split("-")[1])
        mountain = mf_code_to_mountain[code]
        name = fr_names[region_id]
        _, sub_prefix = _MOUNTAIN_PREFIXES[mountain]
        geometry: dict[str, Any] = feature["geometry"]
        centre = _centre_from_bbox(geometry)
        micro_fields: dict[str, Any] = {
            "region_id": region_id,
            "name": name,
            "slug": slugify(region_id),
            "subregion": [sub_prefix],
            "centre": centre,
            "boundary": geometry,
            "neighbours": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        l4_entries.append({"model": "regions.microregion", "fields": micro_fields})
        l4_by_mountain[mountain].append(micro_fields)

    # Sort L4 by region_id for stable, deterministic fixture output
    l4_entries.sort(key=lambda e: e["fields"]["region_id"])

    # --- L1 majors + L2 subs ------------------------------------------------
    l1_entries: list[dict[str, Any]] = []
    l2_entries: list[dict[str, Any]] = []

    for mountain in _MOUNTAIN_ORDER:
        major_prefix, sub_prefix = _MOUNTAIN_PREFIXES[mountain]
        children = l4_by_mountain[mountain]

        if not children:
            logger.warning(
                "build_france_fixture: no L4 children for mountain %r — skipping L1/L2",
                mountain,
            )
            continue

        centre = _centre_from_children(children)
        bbox = _bbox_from_children(children)
        boundary = _boundary_from_children(children)

        l1_entries.append(
            {
                "model": "regions.majorregion",
                "fields": {
                    "prefix": major_prefix,
                    "country": "FR",
                    "name_native": mountain,
                    "name_en": mountain,
                    "centre": centre,
                    "bbox": bbox,
                    "boundary": boundary,
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
            }
        )

        l2_entries.append(
            {
                "model": "regions.subregion",
                "fields": {
                    "prefix": sub_prefix,
                    "major": [major_prefix],
                    "name_native": mountain,
                    "name_en": mountain,
                    "centre": centre,
                    "bbox": bbox,
                    "boundary": boundary,
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
            }
        )

    return l1_entries + l2_entries + l4_entries


# ---------------------------------------------------------------------------
# Geometry helpers (re-implemented locally — do not import from refresh_eaws_fixtures)
# ---------------------------------------------------------------------------


def _centre_from_bbox(geometry: dict[str, Any]) -> dict[str, float]:
    """Return the bbox midpoint of a GeoJSON geometry as ``{lon, lat}``."""
    bbox = _bbox_from_geometry(geometry)
    return {
        "lon": (bbox[0] + bbox[2]) / 2,
        "lat": (bbox[1] + bbox[3]) / 2,
    }


def _bbox_from_geometry(geometry: dict[str, Any]) -> list[float]:
    """Return ``[min_lon, min_lat, max_lon, max_lat]`` for a GeoJSON geometry."""
    coords = list(_iter_coords_from_geometry(geometry))
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return [min(lons), min(lats), max(lons), max(lats)]


def _iter_coords_from_geometry(
    geometry: dict[str, Any],
) -> Iterator[tuple[float, float]]:
    """Yield every ``(lon, lat)`` pair from a GeoJSON geometry."""
    geo_type: str = geometry["type"]
    if geo_type == "Polygon":
        for ring in geometry["coordinates"]:
            yield from ring
    elif geo_type == "MultiPolygon":
        for polygon in geometry["coordinates"]:
            for ring in polygon:
                yield from ring
    else:
        raise ValueError(f"Unsupported geometry type: {geo_type}")


def _centre_from_children(children: list[dict[str, Any]]) -> dict[str, float]:
    """Return the arithmetic mean of the children's ``centre`` values."""
    lons = [child["centre"]["lon"] for child in children if child.get("centre")]
    lats = [child["centre"]["lat"] for child in children if child.get("centre")]
    return {"lon": sum(lons) / len(lons), "lat": sum(lats) / len(lats)}


def _iter_coords(
    children: list[dict[str, Any]],
) -> Iterator[tuple[float, float]]:
    """Yield every ``(lon, lat)`` from the children's boundary geometries."""
    for child in children:
        boundary = child.get("boundary")
        if not boundary:
            continue
        yield from _iter_coords_from_geometry(boundary)


def _bbox_from_children(children: list[dict[str, Any]]) -> list[float]:
    """Return ``[min_lon, min_lat, max_lon, max_lat]`` over all child boundaries."""
    coords = list(_iter_coords(children))
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return [min(lons), min(lats), max(lons), max(lats)]


def _boundary_from_children(children: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge child boundaries into a single GeoJSON Polygon/MultiPolygon.

    Imports ``shapely`` lazily so the runtime never needs the package.
    If shapely is missing, raises a ``RuntimeError`` with install instructions.

    Returns a plain ``dict`` (GeoJSON shape) ready for the ``boundary`` field.
    """
    try:
        from shapely.geometry import mapping, shape
        from shapely.ops import unary_union
    except ImportError as exc:  # pragma: no cover — dev-only dependency
        raise RuntimeError(
            "build_france_fixture requires the dev-only `shapely` dependency. "
            "Install it with `poetry install --with dev`."
        ) from exc

    polys = [shape(child["boundary"]) for child in children if child.get("boundary")]
    union = unary_union(polys)
    # Round-trip through json to normalise shapely's tuple coordinates to lists,
    # so the idempotence diff check compares like-for-like.
    return json.loads(json.dumps(mapping(union)))  # type: ignore[no-any-return]


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

    Compares the serialised JSON string of each entry after round-tripping
    both sides through ``json.dumps`` so normalisation is identical.
    """
    existing = _load_fixture(path)
    new_str = json.dumps(new_data, indent=2, ensure_ascii=False)
    old_str = json.dumps(existing, indent=2, ensure_ascii=False)
    if new_str == old_str:
        return 0
    # Count changed / added / removed entries
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
