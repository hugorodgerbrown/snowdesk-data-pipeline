"""
tests/ingest/test_conf.py — Tests for apps/ingest/conf.py.

``positive_int`` is the accessor the v2 feeds' page ceilings are read
through: a usable override is taken, and an absent or mistyped one falls
back to the shipped value rather than breaking every feed request.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.ingest import conf
from apps.ingest.api.paging import MAX_LIMIT, parse_limit


@pytest.mark.parametrize(("raw", "expected"), [(7, 7), ("40", 40)])
def test_positive_int_takes_a_usable_value(
    settings: Any, raw: object, expected: int
) -> None:
    """An int, or a string of one as the environment gives it, is read."""
    settings.FEED_MAX_LIMIT = raw
    assert conf.positive_int("FEED_MAX_LIMIT", 100) == expected


@pytest.mark.parametrize("raw", ["many", None, 0, -3])
def test_positive_int_falls_back_on_an_unusable_value(
    settings: Any, raw: object
) -> None:
    """Not a whole number, or below one: the default, not an error."""
    settings.FEED_MAX_LIMIT = raw
    assert conf.positive_int("FEED_MAX_LIMIT", 100) == 100


def test_positive_int_falls_back_when_the_setting_is_absent(settings: Any) -> None:
    """A project that never set it gets the default."""
    del settings.FEED_MAX_LIMIT
    assert conf.positive_int("FEED_MAX_LIMIT", 100) == 100


def test_parse_limit_is_held_to_the_setting(settings: Any) -> None:
    """``?limit=`` is clamped to FEED_MAX_LIMIT, read per request."""
    settings.FEED_MAX_LIMIT = 30
    assert parse_limit(str(MAX_LIMIT)) == 30
