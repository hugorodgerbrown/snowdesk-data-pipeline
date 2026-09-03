"""
apps/locations/converters.py — URL path converter for ``Location.short_id``.

``ShortIdConverter`` matches exactly the eleven URL-safe characters
``secrets.token_urlsafe(8)`` produces (SNOW-797), and — like
``generate_short_id`` — refuses an all-digit segment, so ``/weather/<short_id>/``
and the legacy ``/weather/<int:location_id>/`` redirect never compete for a
segment whatever order they are registered in.

Registered as ``short_id`` from ``LocationsConfig.ready()``, the way
``regions`` registers ``region_id``.
"""


class ShortIdConverter:
    """Path converter for an eleven-character ``token_urlsafe(8)`` id."""

    # The lookahead demands a non-digit somewhere in the segment. It cannot
    # skip past a ``/`` because it only steps over digits.
    regex = r"(?=[0-9]*[A-Za-z_-])[A-Za-z0-9_-]{11}"

    def to_python(self, value: str) -> str:
        """Return the matched path segment unchanged."""
        return value

    def to_url(self, value: str) -> str:
        """Return the value unchanged for URL reversal."""
        return value
