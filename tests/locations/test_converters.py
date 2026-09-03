"""
tests/locations/test_converters.py — Tests for the ``short_id`` path converter.

Covers ``ShortIdConverter`` (SNOW-797): it accepts exactly the eleven
URL-safe characters ``secrets.token_urlsafe(8)`` produces, and nothing
else — so ``/weather/<short_id>/`` and the legacy ``/weather/<int>/``
redirect can coexist without competing for a segment.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from apps.locations.converters import ShortIdConverter
from apps.locations.models import generate_short_id

_PATTERN = re.compile(f"^{ShortIdConverter.regex}$")


class TestShortIdConverter:
    """The regex is the whole contract."""

    def test_accepts_what_the_generator_produces(self) -> None:
        """Every token the generator mints matches, so no row is unreachable."""
        for _ in range(50):
            assert _PATTERN.match(generate_short_id())

    @pytest.mark.parametrize(
        "value",
        ["AAAAAAAAAA", "AAAAAAAAAAAA", "AAAAA.AAAAA", "42", "00000000042", ""],
    )
    def test_rejects_the_wrong_shape(self, value: str) -> None:
        """Ten or twelve characters, a dot, any run of digits — none is a short id.

        The eleven-digit case is the load-bearing one: it is what keeps
        this converter and ``<int:location_id>`` from ever both matching.
        """
        assert not _PATTERN.match(value)

    def test_generator_never_mints_an_all_digit_token(self) -> None:
        """A digits-only draw is re-drawn, so every minted id matches here."""
        with patch(
            "apps.locations.models.secrets.token_urlsafe",
            side_effect=["00000000000", "Ab3dE_fGh1J"],
        ):
            assert generate_short_id() == "Ab3dE_fGh1J"

    def test_to_python_and_to_url_are_identity(self) -> None:
        """The segment reaches the view unchanged and reverses unchanged."""
        converter = ShortIdConverter()
        assert converter.to_python("Ab3dE_fGh1J") == "Ab3dE_fGh1J"
        assert converter.to_url("Ab3dE_fGh1J") == "Ab3dE_fGh1J"
