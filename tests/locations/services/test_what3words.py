"""
tests/locations/services/test_what3words.py — Tests for the what3words service.

Covers:
  - convert_to_3wa: the happy path, the configured host, a ``base_url``
    override, and that the key rides in the ``X-Api-Key`` HEADER and never
    in the query string.
  - convert_to_3wa NEVER RAISES: an empty key, a timeout, a 4xx carrying
    the documented ``{"error": {"code": ...}}`` body, a non-JSON body and
    a body with no ``words`` all return None. This is the property the
    trip page depends on — a what3words outage must not take a page down.
  - fill_what3words: a fresh cache is returned without a call, an expired
    one is re-converted, both columns are written, and a failed conversion
    leaves the row untouched (no negative caching).

All outbound HTTP is mocked with ``unittest.mock.patch``, mirroring
test_elevation.py.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from freezegun import freeze_time
from pytest_django.fixtures import Settings

from apps.locations.services.what3words import convert_to_3wa, fill_what3words
from tests.factories import LocationFactory


@pytest.fixture(autouse=True)
def api_key(settings: Settings) -> None:
    """Configure a key for every test in this module.

    A key has to be present for the service to make any request at all,
    and the branch that fires without one is a single test below, which
    clears it again. Autouse rather than a decorator on each class
    because ``override_settings`` cannot decorate a plain pytest class.

    Args:
        settings: pytest-django's settings wrapper.

    """
    settings.WHAT3WORDS_API_KEY = "test-key"


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


class TestConvertTo3wa:
    """The conversion call itself."""

    def test_returns_the_words_from_the_response(self) -> None:
        """The ``words`` value, unprefixed — the ``///`` is presentation."""
        with patch(
            "apps.locations.services.what3words.requests.get", _mock_get(_GOOD_BODY)
        ):
            assert convert_to_3wa(46.080012, 7.318197) == "filled.count.soap"

    def test_uses_the_configured_host(self, settings: Settings) -> None:
        """The configured base plus ``/convert-to-3wa``."""
        settings.WHAT3WORDS_API_BASE_URL = "https://api.example/v3"
        mock_get = _mock_get(_GOOD_BODY)
        with patch("apps.locations.services.what3words.requests.get", mock_get):
            convert_to_3wa(46.0, 7.0)
        assert mock_get.call_args[0][0] == "https://api.example/v3/convert-to-3wa"

    def test_base_url_override_replaces_the_host(self) -> None:
        """A ``base_url`` override points the call somewhere else."""
        mock_get = _mock_get(_GOOD_BODY)
        with patch("apps.locations.services.what3words.requests.get", mock_get):
            convert_to_3wa(46.0, 7.0, base_url="https://mirror.example/v3")
        assert mock_get.call_args[0][0] == "https://mirror.example/v3/convert-to-3wa"

    def test_sends_the_coordinate_and_english(self) -> None:
        """One ``coordinates`` parameter, lat first, and ``language=en``.

        The language is hardcoded on purpose: two people on one trip must
        not be shown different words for the same square.
        """
        mock_get = _mock_get(_GOOD_BODY)
        with patch("apps.locations.services.what3words.requests.get", mock_get):
            convert_to_3wa(46.080012, 7.318197)
        params = mock_get.call_args.kwargs["params"]
        assert params["coordinates"] == "46.080012,7.318197"
        assert params["language"] == "en"

    def test_the_key_travels_in_the_header_not_the_query(self) -> None:
        """A header cannot land in an access log or a proxy's URL capture."""
        mock_get = _mock_get(_GOOD_BODY)
        with patch("apps.locations.services.what3words.requests.get", mock_get):
            convert_to_3wa(46.0, 7.0)
        assert mock_get.call_args.kwargs["headers"]["X-Api-Key"] == "test-key"
        assert "test-key" not in str(mock_get.call_args.kwargs["params"])


class TestConvertTo3waFailures:
    """Every failure returns None. None of them raise."""

    def test_no_key_makes_no_request(self, settings: Settings) -> None:
        """An unsubscribed environment is a supported state, not an error."""
        settings.WHAT3WORDS_API_KEY = ""
        mock_get = _mock_get(_GOOD_BODY)
        with patch("apps.locations.services.what3words.requests.get", mock_get):
            assert convert_to_3wa(46.0, 7.0) is None
        mock_get.assert_not_called()

    def test_a_timeout_returns_none(self) -> None:
        """The upstream being slow renders a coordinate, not a 500."""
        with patch(
            "apps.locations.services.what3words.requests.get",
            side_effect=requests.Timeout("too slow"),
        ):
            assert convert_to_3wa(46.0, 7.0) is None

    def test_a_connection_error_returns_none(self) -> None:
        """As does the upstream being unreachable."""
        with patch(
            "apps.locations.services.what3words.requests.get",
            side_effect=requests.ConnectionError("no route"),
        ):
            assert convert_to_3wa(46.0, 7.0) is None

    def test_a_4xx_returns_none_and_logs_the_error_code(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``InvalidKey`` and ``QuotaExceeded`` need different fixes.

        The status alone says "4xx", so the documented ``error.code`` is
        what reaches the log.
        """
        body = {"error": {"code": "QuotaExceeded", "message": "Quota Exceeded"}}
        with patch(
            "apps.locations.services.what3words.requests.get", _mock_get(body, 402)
        ):
            assert convert_to_3wa(46.0, 7.0) is None
        assert "QuotaExceeded" in caplog.text

    def test_a_4xx_with_an_undocumented_body_still_returns_none(self) -> None:
        """The failure handler must not fail inside the failure."""
        with patch(
            "apps.locations.services.what3words.requests.get", _mock_get({}, 500)
        ):
            assert convert_to_3wa(46.0, 7.0) is None

    def test_a_non_json_body_returns_none(self) -> None:
        """An HTML error page from an intermediary is not a conversion."""
        mock_get = _mock_get({})
        mock_get.return_value.json.side_effect = ValueError("not JSON")
        with patch("apps.locations.services.what3words.requests.get", mock_get):
            assert convert_to_3wa(46.0, 7.0) is None

    @pytest.mark.parametrize("words", [None, "", 3])
    def test_a_body_without_usable_words_returns_none(self, words: object) -> None:
        """Missing, blank and not-a-string are all "no address"."""
        with patch(
            "apps.locations.services.what3words.requests.get",
            _mock_get({"words": words}),
        ):
            assert convert_to_3wa(46.0, 7.0) is None


@pytest.mark.django_db
class TestFillWhat3words:
    """The read path's entry point — cache first, convert second."""

    @freeze_time("2026-03-01T12:00:00+00:00")
    def test_a_fresh_cache_is_returned_without_a_call(self) -> None:
        """One paid conversion per square per month, not per page view."""
        location = LocationFactory.create(
            what3words="filled.count.soap",
            what3words_fetched_at=datetime.datetime(
                2026, 2, 20, 12, 0, tzinfo=datetime.UTC
            ),
        )
        mock_get = _mock_get(_GOOD_BODY)
        with patch("apps.locations.services.what3words.requests.get", mock_get):
            assert fill_what3words(location) == "filled.count.soap"
        mock_get.assert_not_called()

    @freeze_time("2026-03-01T12:00:00+00:00")
    def test_an_expired_cache_is_reconverted_and_restamped(self) -> None:
        """Past 30 days the held value is no longer ours to show."""
        location = LocationFactory.create(
            what3words="stale.old.words",
            what3words_fetched_at=datetime.datetime(
                2026, 1, 1, 12, 0, tzinfo=datetime.UTC
            ),
        )
        with patch(
            "apps.locations.services.what3words.requests.get", _mock_get(_GOOD_BODY)
        ):
            assert fill_what3words(location) == "filled.count.soap"

        location.refresh_from_db()
        assert location.what3words == "filled.count.soap"
        assert location.what3words_fetched_at == datetime.datetime(
            2026, 3, 1, 12, 0, tzinfo=datetime.UTC
        )

    def test_an_empty_row_is_filled_and_both_columns_written(self) -> None:
        """The first render of a trip is what fills the cache."""
        location = LocationFactory.create()
        with patch(
            "apps.locations.services.what3words.requests.get", _mock_get(_GOOD_BODY)
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
            "apps.locations.services.what3words.requests.get",
            side_effect=requests.Timeout("too slow"),
        ):
            assert fill_what3words(location) is None

        location.refresh_from_db()
        assert location.what3words is None
        assert location.what3words_fetched_at is None

    def test_filling_twice_is_idempotent(self) -> None:
        """Two concurrent renders both converting is harmless."""
        location = LocationFactory.create()
        with patch(
            "apps.locations.services.what3words.requests.get", _mock_get(_GOOD_BODY)
        ):
            first = fill_what3words(location)
            second = fill_what3words(location)

        assert first == second == "filled.count.soap"
