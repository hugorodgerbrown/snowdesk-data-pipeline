"""
tests/data_feed/test_contract.py — Tests for apps.data_feed.contract (SNOW-1197).

The field lists are an allowlist, so the test that matters is the drift
guard: a column added to Weather, Location or Resort must be put in its
list or its ``*_NOT_SENT`` set, never neither. Then the round trip of a
value through ``to_wire`` and ``load_fields``.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import models

from apps.data_feed import contract
from apps.locations.models import Location
from apps.regions.models import Resort
from apps.weather.models import Weather


@pytest.mark.parametrize(
    ("model", "sent", "not_sent"),
    [
        (Weather, contract.WEATHER_FIELDS, contract.WEATHER_NOT_SENT),
        (Location, contract.LOCATION_FIELDS, contract.LOCATION_NOT_SENT),
        (Resort, contract.RESORT_FIELDS, contract.RESORT_NOT_SENT),
    ],
)
def test_every_column_is_classified(
    model: type[models.Model], sent: tuple[str, ...], not_sent: frozenset[str]
) -> None:
    """Every concrete column is either sent or deliberately not sent."""
    columns = {f.name for f in model._meta.get_fields() if f.concrete}
    columns |= {f.name for f in model._meta.many_to_many}
    assert columns - set(sent) - not_sent == set(), "classify the new column"
    assert set(sent) - columns == set(), "a sent field no longer exists"
    assert set(sent) & not_sent == set()


def test_to_wire_renders_dates_as_iso() -> None:
    """Dates and datetimes become ISO strings; everything else is untouched."""
    moment = dt.datetime(2026, 10, 23, 6, 30, tzinfo=dt.UTC)
    assert contract.to_wire(moment) == "2026-10-23T06:30:00+00:00"
    assert contract.to_wire(dt.date(2026, 10, 23)) == "2026-10-23"
    assert contract.to_wire({"a": [1]}) == {"a": [1]}
    assert contract.to_wire(None) is None


def test_load_fields_parses_back_and_ignores_unknown_names() -> None:
    """Datetimes parse back aware; a name not on the list is dropped."""
    loaded = contract.load_fields(
        Weather,
        contract.WEATHER_FIELDS,
        {
            "sunrise": "2026-10-23T06:30:00+00:00",
            "sunset": None,
            "weather_code": 3,
            "a_column_from_a_newer_server": 1,
        },
    )
    assert loaded == {
        "sunrise": dt.datetime(2026, 10, 23, 6, 30, tzinfo=dt.UTC),
        "sunset": None,
        "weather_code": 3,
    }


def test_load_fields_refuses_a_bad_datetime() -> None:
    """A datetime that does not parse names the field."""
    with pytest.raises(ValueError, match="sunrise"):
        contract.load_fields(Weather, contract.WEATHER_FIELDS, {"sunrise": "dawn"})


def test_load_fields_parses_a_date_column() -> None:
    """A DateField (not a DateTimeField) parses with parse_date."""

    class _Stub(models.Model):
        """An unsaved model with a date column, for the date branch."""

        day = models.DateField()

        class Meta:
            """Test-only model."""

            app_label = "data_feed"
            managed = False

    assert contract.load_fields(_Stub, ("day",), {"day": "2026-10-23"}) == {
        "day": dt.date(2026, 10, 23)
    }
    with pytest.raises(ValueError, match="day"):
        contract.load_fields(_Stub, ("day",), {"day": "Thursday"})
