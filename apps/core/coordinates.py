"""
apps/core/coordinates.py — Shared WGS-84 coordinate validation.

Guards against non-finite (NaN / ±Inf) and out-of-range latitude/longitude and
accuracy values reaching the database, an external API, or a GeoJSON payload.
``float("nan")`` and ``float("inf")`` both parse cleanly, so a plain
``float()`` conversion — as the observations, favourites, and forecast-point
parsers do — is not enough on its own: these validators must run before any
persist or outbound call (SNOW-464).

Lives in ``core/`` so every consuming app (observations, favourites, bulletins)
can import it without a circular dependency. Coordinate arguments are ordered
``(latitude, longitude)`` — latitude first — matching the project convention
(SNOW-426).
"""

from __future__ import annotations

import math

# WGS-84 bounds (inclusive).
LAT_MIN, LAT_MAX = -90.0, 90.0
LON_MIN, LON_MAX = -180.0, 180.0


class InvalidCoordinatesError(ValueError):
    """Raised when a coordinate or accuracy value is non-finite or out of range.

    Subclasses ``ValueError`` so existing parsers that already catch
    ``ValueError`` (converting bad input to a ``None`` / 400 response) pick up
    a validation failure without a second except clause.
    """


def validate_coordinates(latitude: float, longitude: float) -> None:
    """Validate a WGS-84 ``(latitude, longitude)`` pair.

    Args:
        latitude: Latitude in degrees.
        longitude: Longitude in degrees.

    Raises:
        InvalidCoordinatesError: If either value is non-finite (NaN / ±Inf) or
            outside WGS-84 bounds (latitude −90..90, longitude −180..180).

    """
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise InvalidCoordinatesError(
            f"Coordinates must be finite: lat={latitude!r} lon={longitude!r}."
        )
    if not LAT_MIN <= latitude <= LAT_MAX:
        raise InvalidCoordinatesError(
            f"Latitude {latitude!r} out of range [{LAT_MIN}, {LAT_MAX}]."
        )
    if not LON_MIN <= longitude <= LON_MAX:
        raise InvalidCoordinatesError(
            f"Longitude {longitude!r} out of range [{LON_MIN}, {LON_MAX}]."
        )


def validate_accuracy_radius_km(accuracy_km: float) -> None:
    """Validate a GPS accuracy radius in kilometres.

    Args:
        accuracy_km: Accuracy radius in kilometres.

    Raises:
        InvalidCoordinatesError: If the value is non-finite or negative.

    """
    if not math.isfinite(accuracy_km) or accuracy_km < 0:
        raise InvalidCoordinatesError(
            f"Accuracy radius must be finite and non-negative: {accuracy_km!r} km."
        )
