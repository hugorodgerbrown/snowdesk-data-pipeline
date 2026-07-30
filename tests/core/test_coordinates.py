"""
tests/core/test_coordinates.py — Tests for apps.core.coordinates (SNOW-464).

Covers validate_coordinates and validate_accuracy_radius_km: finiteness,
WGS-84 bounds, inclusive boundaries, and non-negative accuracy.
"""

from __future__ import annotations

import math

import pytest

from apps.core.coordinates import (
    InvalidCoordinatesError,
    validate_accuracy_radius_km,
    validate_coordinates,
)


class TestValidateCoordinates:
    """Tests for validate_coordinates."""

    @pytest.mark.parametrize(
        ("latitude", "longitude"),
        [
            (46.1, 7.1),
            (0.0, 0.0),
            (90.0, 180.0),  # inclusive upper boundary
            (-90.0, -180.0),  # inclusive lower boundary
        ],
    )
    def test_valid_values_pass(self, latitude: float, longitude: float) -> None:
        """Finite, in-range coordinates do not raise."""
        validate_coordinates(latitude, longitude)

    @pytest.mark.parametrize(
        ("latitude", "longitude"),
        [
            (math.nan, 7.1),
            (46.1, math.nan),
            (math.inf, 7.1),
            (46.1, math.inf),
            (-math.inf, 7.1),
            (46.1, -math.inf),
        ],
    )
    def test_non_finite_raises(self, latitude: float, longitude: float) -> None:
        """NaN / ±Inf coordinates raise InvalidCoordinatesError."""
        with pytest.raises(InvalidCoordinatesError):
            validate_coordinates(latitude, longitude)

    @pytest.mark.parametrize(
        ("latitude", "longitude"),
        [
            (90.1, 7.1),
            (-90.1, 7.1),
            (46.1, 180.1),
            (46.1, -180.1),
        ],
    )
    def test_out_of_range_raises(self, latitude: float, longitude: float) -> None:
        """Out-of-WGS-84-range coordinates raise InvalidCoordinatesError."""
        with pytest.raises(InvalidCoordinatesError):
            validate_coordinates(latitude, longitude)

    def test_error_is_a_value_error(self) -> None:
        """InvalidCoordinatesError subclasses ValueError so existing except
        ValueError parsers catch it.
        """
        with pytest.raises(ValueError):
            validate_coordinates(math.nan, 0.0)


class TestValidateAccuracyRadiusKm:
    """Tests for validate_accuracy_radius_km."""

    @pytest.mark.parametrize("accuracy_km", [0.0, 0.05, 1.0, 12000.0])
    def test_valid_values_pass(self, accuracy_km: float) -> None:
        """Finite, non-negative accuracy does not raise."""
        validate_accuracy_radius_km(accuracy_km)

    @pytest.mark.parametrize(
        "accuracy_km", [-0.001, -5.0, math.nan, math.inf, -math.inf]
    )
    def test_invalid_values_raise(self, accuracy_km: float) -> None:
        """Negative or non-finite accuracy raises InvalidCoordinatesError."""
        with pytest.raises(InvalidCoordinatesError):
            validate_accuracy_radius_km(accuracy_km)
