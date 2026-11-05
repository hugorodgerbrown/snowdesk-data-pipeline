"""
tests/map/test_your_location.py — the ground under a locate-me fix
(``apps.map.your_location``, SNOW-1395).

Covers the terrain reading (height to whole metres, aspect to its octant,
slope to its 5° band and the slope layer's class), the device altitude as
the fallback off the terrain model, and nothing at all with neither. The
terrain module is patched at the seam: its own tests cover the tiles.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from apps.map.your_location import (
    GROUND_DEVICE,
    GROUND_TERRAIN,
    Ground,
    read_ground,
)


def _known(height: float) -> SimpleNamespace:
    """A TerrainHeight that knows the height."""
    return SimpleNamespace(is_known=True, height_m=height, unknown=None)


def _unknown() -> SimpleNamespace:
    """A TerrainHeight outside the model's coverage."""
    return SimpleNamespace(is_known=False, height_m=None, unknown="outside_coverage")


def _read(
    height: SimpleNamespace,
    angle: float | None = 31.2,
    aspect: float | None = 44.0,
    device: float | None = None,
) -> Ground:
    """Read the ground with the terrain module patched to answer as given."""
    with (
        mock.patch("apps.map.your_location.load_grid", return_value=object()),
        mock.patch("apps.map.your_location.sample_height", return_value=height),
        mock.patch(
            "apps.map.your_location.sample_slope",
            return_value=SimpleNamespace(angle_deg=angle, aspect_deg=aspect),
        ),
    ):
        return read_ground(46.09, 7.27, device)


class TestReadGround:
    """read_ground: the terrain model first, the device second."""

    def test_the_terrain_model_answers(self) -> None:
        ground = _read(_known(2410.4))
        assert ground == Ground(
            height_m=2410, octant="NE", slope_band=6, source=GROUND_TERRAIN
        )

    def test_the_terrain_model_beats_the_device(self) -> None:
        assert _read(_known(2410.4), device=2385.0).height_m == 2410

    def test_level_ground_has_no_aspect(self) -> None:
        ground = _read(_known(1500.0), angle=2.0, aspect=None)
        assert ground.octant is None
        assert ground.slope_band == 0

    def test_off_the_model_the_device_height_alone(self) -> None:
        ground = _read(_unknown(), device=980.6)
        assert ground == Ground(
            height_m=981, octant=None, slope_band=None, source=GROUND_DEVICE
        )

    def test_nothing_at_all(self) -> None:
        ground = _read(_unknown())
        assert ground == Ground(
            height_m=None, octant=None, slope_band=None, source=None
        )

    def test_a_non_finite_device_height_is_ignored(self) -> None:
        assert _read(_unknown(), device=float("nan")).height_m is None


class TestGround:
    """The derived readings the sheet shows."""

    @pytest.mark.parametrize(
        ("band", "label", "klass"),
        [
            (2, "10–15°", None),
            (5, "25–30°", None),
            (6, "30–35°", 0),
            (8, "40–45°", 2),
            (12, "60–65°", 4),
        ],
    )
    def test_band_label_and_class(
        self, band: int, label: str, klass: int | None
    ) -> None:
        ground = Ground(
            height_m=2000, octant="N", slope_band=band, source=GROUND_TERRAIN
        )
        assert ground.slope_label == label
        assert ground.slope_class == klass

    def test_no_band(self) -> None:
        ground = Ground(
            height_m=2000, octant=None, slope_band=None, source=GROUND_DEVICE
        )
        assert ground.slope_label == ""
        assert ground.slope_class is None
        assert ground.octant_index is None

    def test_octant_index(self) -> None:
        ground = Ground(height_m=2000, octant="NW", slope_band=6, source=GROUND_TERRAIN)
        assert ground.octant_index == 7
