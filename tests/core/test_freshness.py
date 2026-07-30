"""tests/core/test_freshness.py — apply_freshness_headers helper (SNOW-370).

Covers the spec §5.4 contract: the three headers appear with the correct
values, ``X-Data-Unsafe-After`` is suppressible for non-safety data, and
naive datetimes are rejected as a bug rather than emitted with an
ambiguous timezone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from django.http import HttpResponse

from apps.core.freshness import (
    DEFAULT_MAX_AGE_SECONDS,
    DEFAULT_UNSAFE_AFTER_SECONDS,
    apply_freshness_headers,
)


def test_stamps_three_headers_with_defaults() -> None:
    """All three headers land on the response with default max-age / unsafe-after."""
    generated = datetime(2026, 7, 15, 9, 0, 0, tzinfo=UTC)
    response = apply_freshness_headers(HttpResponse(), generated_at=generated)

    assert response["X-Data-Generated-At"] == "2026-07-15T09:00:00+00:00"
    assert response["X-Data-Max-Age"] == str(DEFAULT_MAX_AGE_SECONDS)
    assert response["X-Data-Unsafe-After"] == str(DEFAULT_UNSAFE_AFTER_SECONDS)


def test_unsafe_after_omitted_for_non_safety_data() -> None:
    """Passing ``unsafe_after=None`` drops the header — spec §5.4 non-safety case."""
    generated = datetime(2026, 7, 15, 9, 0, 0, tzinfo=UTC)
    response = apply_freshness_headers(
        HttpResponse(),
        generated_at=generated,
        unsafe_after=None,
    )

    assert "X-Data-Generated-At" in response
    assert "X-Data-Max-Age" in response
    assert "X-Data-Unsafe-After" not in response


def test_custom_max_age_and_unsafe_after() -> None:
    """Explicit values override the defaults."""
    generated = datetime(2026, 7, 15, 9, 0, 0, tzinfo=UTC)
    response = apply_freshness_headers(
        HttpResponse(),
        generated_at=generated,
        max_age=3600,
        unsafe_after=7200,
    )

    assert response["X-Data-Max-Age"] == "3600"
    assert response["X-Data-Unsafe-After"] == "7200"


def test_non_utc_timezone_preserved_in_header() -> None:
    """A tz-aware non-UTC datetime is emitted with its offset intact."""
    plus_two = timezone(timedelta(hours=2))
    generated = datetime(2026, 7, 15, 11, 30, 0, tzinfo=plus_two)
    response = apply_freshness_headers(HttpResponse(), generated_at=generated)

    assert response["X-Data-Generated-At"] == "2026-07-15T11:30:00+02:00"


def test_naive_datetime_raises() -> None:
    """A naive datetime is a caller bug — never silently emit an ambiguous time."""
    with pytest.raises(ValueError):
        apply_freshness_headers(
            HttpResponse(),
            generated_at=datetime(2026, 7, 15, 9, 0, 0),
        )


def test_returns_same_response_for_chaining() -> None:
    """Returns the same response object so callers can chain."""
    response = HttpResponse()
    returned = apply_freshness_headers(
        response,
        generated_at=datetime(2026, 7, 15, 9, 0, 0, tzinfo=UTC),
    )
    assert returned is response
