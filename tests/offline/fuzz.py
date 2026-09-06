"""
tests/offline/fuzz.py — seeded selection of what each weekly run exercises.

A test that downloads CH-4115 every week proves that CH-4115 works. It
proves nothing about the other 148 regions, and the failures this suite
exists to catch are shaped exactly like that: a region whose boundary
clips to an empty tile row, a basemap whose sprite lives at a path the
others don't use, a zoom at the edge of ``MICRO_BAND`` where the overzoom
rule changes. Those are found by walking the space, not by re-proving one
point in it.

So every run draws its subject from a seed, and prints the seed. A red run
is reproduced with::

    SNOWDESK_OFFLINE_SEED=<seed> uv run tox -e offline

The default seed is the ISO week — ``2026-W37`` — so a scheduled Monday run
picks a new subject each week while every re-run *within* that week (a
retry, a bisect, someone reproducing locally on the Tuesday) is
byte-identical. A random default would make a red run unreproducible, which
in a weekly suite means unfixable: by the time anyone looks, the evidence
is a seed nobody recorded.

What is deliberately NOT fuzzed: anything whose failure would be a flake
rather than a finding. The tile band is fixed at z10–14 because that is
what the product stores; the download size ceiling is fixed because the
suite is not a load test.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import random
from dataclasses import dataclass

# Regions above this tile count are excluded from the draw. The pool still
# holds roughly half the Swiss micro-regions, and the cap keeps one weekly
# run to a few hundred real tile fetches rather than the ~2,800 the largest
# region would pull from a live origin every Monday. It is a courtesy to
# the tile origin, not a correctness bound.
MAX_FUZZ_TILE_COUNT = 220

# Which countries each basemap actually draws. Not a nicety: IGN Plan
# covers France and basemap.at covers Austria, and OUTSIDE their country
# they render blank by design (see BASEMAP_STYLES in config/settings/base.py
# — "coverage is national, so they are a comparison aid, not a
# replacement"). A fuzzer that drew basemap.at for a Swiss region would
# download a few hundred legitimately empty tiles and then report the
# product broken because nothing drew.
#
# ``None`` means global. A key absent from this map is treated as global
# too, so adding a new global basemap needs no edit here; adding a national
# one does, and the failure if you forget is loud rather than subtle.
BASEMAP_COUNTRIES: dict[str, frozenset[str] | None] = {
    "openfreemap_liberty": None,
    "swisstopo_winter": frozenset({"CH"}),
    "swisstopo_light": frozenset({"CH"}),
    "ign_plan": frozenset({"FR"}),
    "basemap_at": frozenset({"AT"}),
}

# The band the product actually stores (basemap_tiles.MICRO_BAND). Probe
# zooms are drawn from inside it; the two out-of-band probes below sit
# either side, where the rendering rule changes.
BAND_MIN_Z = 10
BAND_MAX_Z = 14


def default_seed() -> str:
    """Return the ISO-week seed, e.g. ``2026-W37``.

    Overridden by ``SNOWDESK_OFFLINE_SEED`` so a red run is reproducible
    exactly. See the module docstring for why the default is weekly rather
    than random.
    """
    override = os.environ.get("SNOWDESK_OFFLINE_SEED")
    if override:
        return override
    today = dt.date.today()
    iso = today.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


@dataclass(frozen=True)
class OfflineSubject:
    """One week's draw — everything a run varies, decided in one place.

    Args:
        seed: The seed this subject was drawn from, for the failure message.
        region_id: EAWS id of the region to download, e.g. ``"CH-4115"``.
        region_name: Its display name, for readable assertion output.
        tile_count: Tiles the region's precomputed blob will fetch.
        centre: ``(latitude, longitude)`` of the region, the point the
            "inside coverage" probes aim at.
        outside: ``(latitude, longitude)`` well clear of the region, the
            point the "outside coverage" probes aim at. Drawn on a fuzzed
            bearing so the suite does not always leave coverage in the same
            direction — a boundary is not equally close on every side.
        basemap_key: Which basemap the download is made under.
        inside_zoom: A zoom inside the stored band, for the "it renders"
            probe.
        above_band_zoom: A zoom past ``BAND_MAX_Z``, where stored tiles
            overzoom and must still draw.
        below_band_zoom: A zoom under ``BAND_MIN_Z``, where nothing was
            stored and the basemap must go blank without erroring.

    """

    seed: str
    region_id: str
    region_name: str
    tile_count: int
    centre: tuple[float, float]
    outside: tuple[float, float]
    basemap_key: str
    inside_zoom: int
    above_band_zoom: int
    below_band_zoom: int

    def to_string(self) -> str:
        """Return the one-line banner every run prints before it starts."""
        return (
            f"seed={self.seed} region={self.region_id} ({self.region_name}) "
            f"tiles={self.tile_count} basemap={self.basemap_key} "
            f"zooms={self.below_band_zoom}/{self.inside_zoom}/{self.above_band_zoom}"
        )


def draw_subject(
    candidates: list[tuple[str, str, int, float, float]],
    basemap_keys: list[str],
    seed: str | None = None,
) -> OfflineSubject:
    """Draw one week's subject from the candidate regions.

    Args:
        candidates: ``(region_id, name, tile_count, latitude, longitude)``
            for every region with a precomputed ``basemap_download`` blob
            and a usable centre point.
        basemap_keys: Basemap keys offered by the running settings. Ones
            that do not draw the chosen region's country are dropped before
            the draw — see ``BASEMAP_COUNTRIES``.
        seed: Override the seed; defaults to ``default_seed()``.

    Returns:
        The subject for this run.

    Raises:
        ValueError: If no candidate region is small enough to draw, which
            means the fixture lost its precomputed blobs rather than that
            the cap is wrong.

    """
    seed = seed or default_seed()
    # S311 is suppressed below: reproducibility is the whole requirement
    # here and there is no security property to protect — a seeded
    # Mersenne Twister is exactly the right tool, and `secrets` cannot be
    # seeded at all.
    rng = random.Random(seed)  # noqa: S311

    pool = [row for row in candidates if 0 < row[2] <= MAX_FUZZ_TILE_COUNT]
    if not pool:
        raise ValueError(
            "no region has a precomputed basemap_download blob under "
            f"{MAX_FUZZ_TILE_COUNT} tiles — has the eaws fixture lost its "
            "`basemap_download` values? Re-run compute_basemap_download."
        )
    # Sorted before the draw: the caller's queryset order is not guaranteed
    # stable across databases, and an unstable pool order would make the
    # seed reproduce a different region on a different machine — which
    # defeats the entire point of seeding it.
    pool.sort()
    region_id, region_name, tile_count, latitude, longitude = rng.choice(pool)

    country = region_id.split("-", 1)[0].upper()
    drawable = sorted(
        key
        for key in basemap_keys
        if BASEMAP_COUNTRIES.get(key) is None
        or country in (BASEMAP_COUNTRIES.get(key) or frozenset())
    )
    if not drawable:
        raise ValueError(
            f"no configured basemap draws {country}: every key in "
            f"{sorted(basemap_keys)} is national and none covers it. "
            "Add the new basemap to BASEMAP_COUNTRIES, or widen the "
            "seeded dataset."
        )

    # A point far enough outside the region that no margin tile could
    # reach it. Region downloads clip to the boundary plus about one z14
    # tile (~1.7 km); a Swiss micro-region is at most ~30 km across, so a
    # degree of latitude (~111 km) from the centre is outside any of them
    # by a wide margin, on any bearing.
    bearing = rng.uniform(0.0, 360.0)
    outside = (
        latitude + math.cos(math.radians(bearing)),
        longitude + math.sin(math.radians(bearing)) * 1.5,
    )

    return OfflineSubject(
        seed=seed,
        region_id=region_id,
        region_name=region_name,
        tile_count=tile_count,
        centre=(latitude, longitude),
        outside=outside,
        basemap_key=rng.choice(drawable),
        inside_zoom=rng.randint(BAND_MIN_Z + 1, BAND_MAX_Z),
        above_band_zoom=rng.randint(BAND_MAX_Z + 1, BAND_MAX_Z + 2),
        below_band_zoom=rng.randint(BAND_MIN_Z - 3, BAND_MIN_Z - 1),
    )
