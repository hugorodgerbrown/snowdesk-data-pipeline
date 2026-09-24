"""
tests/routes/test_bank.py — the signed bank angle across a track.

Covers ``apps.routes.services.bank`` (SNOW-1021).

The tracks are the synthetic PLANES ``test_terrain_detail`` builds: a face
of known angle and aspect, crossed at a known heading. On a plane the
decomposition is exact — ``tan(pitch) = tan(slope) × cos(δ)`` and
``tan(roll) = tan(slope) × sin(δ)`` — so the tests assert the figures
rather than properties of them.

**ONE TEST ASSERTS THE DECOMPOSITION**: ``tan²(pitch) + tan²(roll) ==
tan²(slope)`` at every heading. A sin/cos swap survives it, which is why
the traverse and fall-line cases pin which one is which; a radians slip
or a wrong exponent does not.
"""

from __future__ import annotations

import math

import pytest

from apps.routes.services.bank import bank_angle_deg, bank_angles
from tests.routes.test_terrain_detail import _plane_track, _record


def _pitch_deg(angle_deg: float, aspect_deg: float, bearing_deg: float) -> float:
    """Return the closed-form pitch on a plane, in degrees (positive downhill)."""
    return math.degrees(
        math.atan(
            math.tan(math.radians(angle_deg))
            * math.cos(math.radians(aspect_deg - bearing_deg))
        )
    )


class TestDecomposition:
    """Pitch and roll split the slope without losing any of it."""

    @pytest.mark.parametrize("heading", range(0, 360, 15))
    def test_pitch_and_roll_recompose_the_slope(self, heading: int) -> None:
        """``tan²(pitch) + tan²(roll) = tan²(slope)`` at every heading."""
        roll = bank_angle_deg(40.0, 135.0, float(heading))
        assert roll is not None
        pitch = _pitch_deg(40.0, 135.0, float(heading))
        assert math.tan(math.radians(pitch)) ** 2 + math.tan(
            math.radians(roll)
        ) ** 2 == pytest.approx(math.tan(math.radians(40.0)) ** 2, abs=1e-9)

    def test_a_level_traverse_banks_by_the_whole_slope(self) -> None:
        """Across a 40 degree face: roll 40, pitch 0."""
        roll = bank_angle_deg(40.0, 90.0, 0.0)
        assert roll is not None
        assert abs(roll) == pytest.approx(40.0)
        assert _pitch_deg(40.0, 90.0, 0.0) == pytest.approx(0.0, abs=1e-9)

    def test_a_fall_line_descent_does_not_bank(self) -> None:
        """Straight down the face: roll 0."""
        assert bank_angle_deg(40.0, 90.0, 90.0) == pytest.approx(0.0, abs=1e-9)

    @pytest.mark.parametrize("heading", range(0, 360, 10))
    def test_gentle_ground_limits_itself(self, heading: int) -> None:
        """A 20 degree face never banks a track by more than 20 degrees.

        Why there is no gate: ``|sin δ| ≤ 1`` bounds the roll by the
        slope, so gentle ground can only ever draw a gentle tick.
        """
        roll = bank_angle_deg(20.0, 200.0, float(heading))
        assert roll is not None
        assert abs(roll) <= 20.0 + 1e-9


class TestSign:
    """The sign is the feature (SNOW-1021) — never an ``abs()`` away.

    SNOW-1021 decided the bank angle is drawn SIGNED: positive when the
    ground falls away on the skier's right. These tests exist so that a
    tidy-up which takes the absolute value fails loudly rather than
    quietly deleting which shoulder the slope falls from. See
    ``docs/decisions/the-bank-angle-is-drawn-signed.md``.
    """

    def test_ground_falling_to_the_right_is_positive(self) -> None:
        """Heading north across an east-facing slope: downhill is right."""
        roll = bank_angle_deg(35.0, 90.0, 0.0)
        assert roll is not None
        assert roll > 0

    def test_ground_falling_to_the_left_is_negative(self) -> None:
        """Heading north across a west-facing slope: downhill is left."""
        roll = bank_angle_deg(35.0, 270.0, 0.0)
        assert roll is not None
        assert roll < 0

    def test_reversing_the_track_flips_the_sign_and_keeps_the_magnitude(
        self,
    ) -> None:
        """The same line, walked the other way, over the same plane."""
        out_points = _plane_track(20.0, 38.0, 110.0)
        back_points = list(reversed(out_points))
        out = bank_angles(_record(out_points, 38.0, 110.0))
        back = bank_angles(_record(back_points, 38.0, 110.0))
        assert out and back
        assert all(bank is not None and bank > 0 for bank in out)
        assert all(bank is not None and bank < 0 for bank in back)
        assert {abs(bank) for bank in out if bank is not None} == {
            abs(bank) for bank in back if bank is not None
        }


class TestBankAngles:
    """One whole signed degree per segment of a stored record."""

    def test_a_traverse_reads_the_slope_on_every_segment(self) -> None:
        """Due north across a 40 degree east face: +40 throughout."""
        points = _plane_track(0.0, 40.0, 90.0)
        banks = bank_angles(_record(points, 40.0, 90.0))
        assert banks is not None
        assert len(banks) == len(_record(points, 40.0, 90.0)["segments"])
        assert set(banks) == {40}

    def test_values_are_whole_degrees(self) -> None:
        """Ints on the wire, not floats with a tenth attached."""
        points = _plane_track(33.0, 37.0, 101.0)
        banks = bank_angles(_record(points, 37.0, 101.0))
        assert banks is not None
        assert all(isinstance(bank, int) for bank in banks)

    def test_an_unknown_segment_has_no_roll(self) -> None:
        """No angle and no aspect: None, never zero."""
        points = _plane_track(0.0, 40.0, 90.0)
        banks = bank_angles(_record(points, 40.0, 90.0, unknown_at=3))
        assert banks is not None
        assert banks[3] is None
        assert banks[2] == 40

    def test_level_ground_has_no_roll(self) -> None:
        """A known angle with no aspect faces nowhere, so falls to no side."""
        points = _plane_track(0.0, 0.0, 0.0)
        banks = bank_angles(_record(points, 0.0, None))
        assert banks is not None
        assert all(bank is None for bank in banks)

    def test_an_unsampled_route_is_none(self) -> None:
        """No record at all."""
        assert bank_angles(None) is None

    def test_an_unpaired_record_is_none(self) -> None:
        """N + 1 boundaries must bound N segments — the fall-line refusal."""
        points = _plane_track(0.0, 40.0, 90.0)
        record = _record(points, 40.0, 90.0)
        record["segments"].pop()
        assert bank_angles(record) is None
