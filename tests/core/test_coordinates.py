"""
tests/core/test_coordinates.py — Tests for apps.core.coordinates (SNOW-464).

Covers validate_coordinates and validate_accuracy_radius_km: finiteness,
WGS-84 bounds, inclusive boundaries, and non-negative accuracy; and
parse_coordinates, the shared request-string parser (SNOW-452).
"""

from __future__ import annotations

import math

import pytest

from apps.core.coordinates import (
    InvalidCoordinatesError,
    parse_coordinates,
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


class TestParseCoordinates:
    """Tests for parse_coordinates (SNOW-452)."""

    def test_valid_pair_parses(self) -> None:
        """A finite, in-range pair comes back as floats, latitude first."""
        assert parse_coordinates("46.1", "7.1") == (46.1, 7.1)

    @pytest.mark.parametrize(
        ("lat_str", "lon_str"),
        [
            ("90", "180"),
            ("-90", "-180"),
        ],
    )
    def test_boundaries_are_accepted(self, lat_str: str, lon_str: str) -> None:
        """The WGS-84 bounds are inclusive."""
        assert parse_coordinates(lat_str, lon_str) == (float(lat_str), float(lon_str))

    @pytest.mark.parametrize(
        ("lat_str", "lon_str"),
        [
            (None, "7.1"),
            ("46.1", None),
            (None, None),
            ("", "7.1"),
            ("46.1", ""),
        ],
    )
    def test_missing_or_empty_is_none(
        self, lat_str: str | None, lon_str: str | None
    ) -> None:
        """A missing or empty value on either side gives None."""
        assert parse_coordinates(lat_str, lon_str) is None

    @pytest.mark.parametrize(
        ("lat_str", "lon_str"),
        [
            ("abc", "7.1"),
            ("46.1", "east"),
        ],
    )
    def test_non_numeric_is_none(self, lat_str: str, lon_str: str) -> None:
        """An unparseable value gives None."""
        assert parse_coordinates(lat_str, lon_str) is None

    @pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
    def test_non_finite_is_none(self, bad: str) -> None:
        """NaN and ±Inf parse as floats but are refused on either side."""
        assert parse_coordinates(bad, "7.1") is None
        assert parse_coordinates("46.1", bad) is None

    @pytest.mark.parametrize(
        ("lat_str", "lon_str"),
        [
            ("90.0001", "7.1"),
            ("-90.0001", "7.1"),
            ("46.1", "180.0001"),
            ("46.1", "-180.0001"),
        ],
    )
    def test_out_of_range_is_none(self, lat_str: str, lon_str: str) -> None:
        """A latitude or longitude outside WGS-84 gives None."""
        assert parse_coordinates(lat_str, lon_str) is None
