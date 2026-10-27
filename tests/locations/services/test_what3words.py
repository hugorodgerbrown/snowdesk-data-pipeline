"""
tests/locations/services/test_what3words.py — Tests for fill_what3words.

A fresh cache is returned without a call, an old address is never
re-converted, both columns are written, and a failed conversion leaves the
row untouched (no negative caching). The conversion itself lives in
apps.ingest (SNOW-1249) and is tested in tests/ingest/sources/; here it is
reached through ``requests.get``, mocked as there.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.utils import timezone
from freezegun import freeze_time
from pytest_django.fixtures import Settings

from apps.locations.services.what3words import fill_what3words
from tests.factories import LocationFactory


@pytest.fixture(autouse=True)
def api_key(settings: Settings) -> None:
    """Pin the service's configuration for every test in this module.

    A key has to be present for the service to make any request at all,
    and the branch that fires without one is a single test below, which
    clears it again. Autouse rather than a decorator on each class
    because ``override_settings`` cannot decorate a plain pytest class.

    ``WHAT3WORDS_FAKE`` is pinned OFF here, and that pin is load-bearing
    rather than tidiness. The suite runs under
    ``config.settings.development``, which reads the developer's own
    ``.env`` — so a machine with the local fake switched on for UX work
    took the invented-address branch instead of the mocked HTTP one, and
    every test in the two classes below failed comparing
    ``hazel.kettle.willow`` against ``filled.count.soap``. A test of the
    real request path must not depend on a file that is not in the repo.

    Args:
        settings: pytest-django's settings wrapper.

    """
    settings.WHAT3WORDS_API_KEY = "test-key"
    settings.WHAT3WORDS_FAKE = False


_GOOD_BODY: dict[str, Any] = {
    "country": "CH",
    "square": {},
    "nearestPlace": "Verbier",
    "coordinates": {"lat": 46.080012, "lng": 7.318197},
    "words": "filled.count.soap",
    "language": "en",
    "locale": "en",
    "map": "https://w3w.co/filled.count.soap",
}


def _mock_get(body: dict[str, Any], status_code: int = 200) -> MagicMock:
    """Return a mock for requests.get yielding a JSON response.

    Args:
        body: The decoded response body.
        status_code: The HTTP status. Anything outside 2xx sets ``ok``
            False, which is what the service branches on.

    Returns:
        A MagicMock standing in for ``requests.get``.

    """
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = body
    return MagicMock(return_value=response)


@pytest.mark.django_db
class TestFillWhat3words:
    """The read path's entry point — cache first, convert second."""

    def test_a_stored_address_is_returned_without_a_call(self) -> None:
        """One conversion per square, ever — not one per page view."""
        location = LocationFactory.create(
            what3words="filled.count.soap",
            what3words_fetched_at=timezone.now(),
        )
        mock_get = _mock_get(_GOOD_BODY)
        with patch("apps.ingest.sources.what3words.requests.get", mock_get):
            assert fill_what3words(location) == "filled.count.soap"
        mock_get.assert_not_called()

    @freeze_time("2026-03-01T12:00:00+00:00")
    def test_an_old_address_is_never_reconverted(self) -> None:
        """Fourteen months old and still no call (SNOW-861).

        Under SNOW-840 this re-converted every 30 days. The square has not
        moved, so there is nothing to re-ask; a pin that DOES move clears
        the column at the point of the move instead.
        """
        location = LocationFactory.create(
            what3words="filled.count.soap",
            what3words_fetched_at=datetime.datetime(
                2025, 1, 1, 12, 0, tzinfo=datetime.UTC
            ),
        )
        mock_get = _mock_get(_GOOD_BODY)
        with patch("apps.ingest.sources.what3words.requests.get", mock_get):
            assert fill_what3words(location) == "filled.count.soap"
        mock_get.assert_not_called()

        location.refresh_from_db()
        assert location.what3words_fetched_at == datetime.datetime(
            2025, 1, 1, 12, 0, tzinfo=datetime.UTC
        )

    def test_an_empty_row_is_filled_and_both_columns_written(self) -> None:
        """An unresolved row is what a fill is for."""
        location = LocationFactory.create()
        with patch(
            "apps.ingest.sources.what3words.requests.get", _mock_get(_GOOD_BODY)
        ):
            assert fill_what3words(location) == "filled.count.soap"

        location.refresh_from_db()
        assert location.what3words == "filled.count.soap"
        assert location.what3words_fetched_at is not None

    def test_a_failed_conversion_writes_nothing(self) -> None:
        """No negative caching — a failure is the upstream, not the square.

        Stamping "we tried" would suppress the retry that fixes itself as
        soon as the outage ends.
        """
        location = LocationFactory.create()
        with patch(
            "apps.ingest.sources.what3words.requests.get",
            side_effect=requests.Timeout("too slow"),
        ):
            assert fill_what3words(location) is None

        location.refresh_from_db()
        assert location.what3words is None
        assert location.what3words_fetched_at is None

    def test_filling_twice_is_idempotent(self) -> None:
        """Two concurrent callers both converting is harmless."""
        location = LocationFactory.create()
        with patch(
            "apps.ingest.sources.what3words.requests.get", _mock_get(_GOOD_BODY)
        ):
            first = fill_what3words(location)
            second = fill_what3words(location)

        assert first == second == "filled.count.soap"
