"""
tests/locations/management/commands/test_fill_what3words.py

Covers ``fill_what3words`` (SNOW-881) — the out-of-band walk that gives
every ``Location`` its three word address.

Three of these tests exist because running the command by hand found the
bugs they pin. The no-key case exited NON-ZERO with every row counted as a
failure, which would have alarmed a scheduled job in any environment
without a subscription; and the fix for that very nearly disabled the local
fake, which is the one environment where somebody without a subscription
actually wants the command to do something.

The rest are the command contract from CLAUDE.md: nothing is written
without ``--commit``, the walk is idempotent, and a partial batch exits
non-zero rather than reporting success.
"""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, _patch, patch

import pytest
import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from pytest_django.fixtures import Settings

from apps.locations.models import Location
from tests.factories import LocationFactory

COMMAND = "fill_what3words"

_GOOD_BODY = {"words": "filled.count.soap"}


def _mock_get(payload: dict[str, str]) -> "_patch[MagicMock]":
    """Return a ``requests.get`` double answering 200 with ``payload``."""
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(payload).encode()
    return patch(
        "apps.locations.services.what3words.requests.get", return_value=response
    )


@pytest.fixture
def keyed(settings: Settings) -> None:
    """Configure a key so the command does not short-circuit."""
    settings.WHAT3WORDS_API_KEY = "test-key"
    settings.WHAT3WORDS_FAKE = False


@pytest.mark.django_db
class TestFillWhat3Words:
    """The walk, its candidate set, and its exit code."""

    def test_writes_nothing_without_commit(self, keyed: None) -> None:
        """Read-only by default — the command contract in CLAUDE.md."""
        location = LocationFactory.create(what3words=None)
        with _mock_get(_GOOD_BODY):
            call_command(COMMAND, delay=0, stdout=StringIO())

        location.refresh_from_db()
        assert location.what3words is None

    def test_commit_fills_every_unaddressed_location(self, keyed: None) -> None:
        """The whole candidate set, not just the trip meeting points."""
        first = LocationFactory.create(what3words=None)
        second = LocationFactory.create(what3words=None)
        with _mock_get(_GOOD_BODY):
            call_command(COMMAND, commit=True, delay=0, stdout=StringIO())

        for location in (first, second):
            location.refresh_from_db()
            assert location.what3words == "filled.count.soap"
            assert location.what3words_fetched_at is not None

    def test_an_addressed_location_is_not_reconverted(self, keyed: None) -> None:
        """A stored address encodes a fixed square — there is nothing to re-ask."""
        LocationFactory.create(what3words="already.got.words")
        with _mock_get(_GOOD_BODY) as mock_get:
            call_command(COMMAND, commit=True, delay=0, stdout=StringIO())
        mock_get.assert_not_called()

    def test_the_empty_string_counts_as_unaddressed(self, keyed: None) -> None:
        """The column is nullable AND blankable, so both states are candidates.

        A candidate set that saw only NULL would leave a ``""`` row
        unfillable forever with nothing looking wrong.
        """
        location = LocationFactory.create(what3words="")
        with _mock_get(_GOOD_BODY):
            call_command(COMMAND, commit=True, delay=0, stdout=StringIO())

        location.refresh_from_db()
        assert location.what3words == "filled.count.soap"

    def test_a_second_run_selects_nothing(self, keyed: None) -> None:
        """Idempotent by construction, so a re-run costs nothing."""
        LocationFactory.create(what3words=None)
        with _mock_get(_GOOD_BODY):
            call_command(COMMAND, commit=True, delay=0, stdout=StringIO())
        assert Location.objects.unaddressed().count() == 0

        with _mock_get(_GOOD_BODY) as mock_get:
            call_command(COMMAND, commit=True, delay=0, stdout=StringIO())
        mock_get.assert_not_called()

    def test_one_failure_does_not_abort_the_batch(self, keyed: None) -> None:
        """A row that cannot convert keeps its null; the walk carries on.

        The exit is non-zero so a partial run is never mistaken for a
        clean one — but only after every other row has been filled.
        """
        LocationFactory.create(what3words=None)
        LocationFactory.create(what3words=None)
        with (
            patch(
                "apps.locations.services.what3words.requests.get",
                side_effect=requests.Timeout("too slow"),
            ),
            pytest.raises(CommandError, match="2 of 2"),
        ):
            call_command(COMMAND, commit=True, delay=0, stdout=StringIO())

        assert Location.objects.unaddressed().count() == 2


@pytest.mark.django_db
class TestFillWhat3WordsWithoutASubscription:
    """No key is a supported state and must not look like a failure."""

    def test_no_key_exits_zero_and_converts_nothing(self, settings: Settings) -> None:
        """THE REGRESSION. This walked the estate and exited non-zero.

        ``convert_to_3wa`` returns None both for "no key" and for "the
        call failed", which is right for a page render and wrong here: a
        scheduled run in an environment that has simply not subscribed
        would have counted every location as a failure and alarmed on
        every pass.
        """
        settings.WHAT3WORDS_API_KEY = ""
        settings.WHAT3WORDS_FAKE = False
        LocationFactory.create(what3words=None)
        out = StringIO()

        with patch("apps.locations.services.what3words.requests.get") as mock_get:
            call_command(COMMAND, commit=True, delay=0, stdout=out)

        mock_get.assert_not_called()
        assert "WHAT3WORDS_API_KEY is not set" in out.getvalue()
        assert Location.objects.unaddressed().count() == 1

    def test_the_local_fake_still_fills_without_a_key(self, settings: Settings) -> None:
        """The no-key skip must not disable the fake it sits in front of.

        ``convert_to_3wa`` tests the fake BEFORE the key, so an empty key
        with the fake on is a working configuration — and it is the exact
        one somebody without a subscription uses to see the feature.
        """
        settings.WHAT3WORDS_API_KEY = ""
        settings.WHAT3WORDS_FAKE = True
        settings.DEBUG = True
        location = LocationFactory.create(what3words=None)

        call_command(COMMAND, commit=True, delay=0, stdout=StringIO())

        location.refresh_from_db()
        assert location.what3words
