"""
apps/data_feed/contract.py — The data feed's wire contract, shared by both sides.

The server (``feeds.py``) writes rows with these field lists and the client
(``services/apply.py``) reads them back with the same lists, so the two
cannot drift apart inside one codebase. A client on an older build simply
ignores a field it does not know.

**Every list is an allowlist.** A column is on the wire only because it is
named here; a new column on ``Weather``, ``Location`` or ``Resort`` is not
published until someone adds it. ``tests/data_feed/test_contract.py`` fails
when a model gains a column that is in neither the list nor the
``*_NOT_SENT`` set beside it, so the choice is always made rather than
defaulted.

Keys travel as natural keys, never primary keys: a bulletin by its
``bulletin_id``, a resort by its ``slug``, a location by its ``uuid``, a
region by its EAWS ``region_id``, a pass by its ``slug``. Two databases
seeded independently agree on all of those and on none of the integer ids.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import models
from django.utils.dateparse import parse_date, parse_datetime

from apps.ingest.public import FEED_DEFAULT_LIMIT, FEED_MAX_LIMIT, FEED_VERSION

# Bumped on a breaking change to any shape below; it is also the URL's /v1/.
# The version and the page sizes are the paging rule's, which ingest owns
# (apps/ingest/api/paging.py, SNOW-1250).
VERSION = FEED_VERSION

# The OAuth scope a service client's token must carry (config/settings/base.py).
SCOPE = "data"

# The feeds, in the order a full pull applies them. Locations come before
# the resorts that link to them and the weather that hangs off them.
FEEDS = ("locations", "resorts", "bulletins", "weather")

# Page size: the default, and the most a client may ask for.
DEFAULT_LIMIT = FEED_DEFAULT_LIMIT
MAX_LIMIT = FEED_MAX_LIMIT

WEATHER_FIELDS: tuple[str, ...] = (
    "fetched_at",
    "weather_code",
    "sunrise",
    "sunset",
    "temperature_2m_max",
    "temperature_2m_min",
    "apparent_temperature_max",
    "apparent_temperature_min",
    "precipitation_sum",
    "snowfall_sum",
    "precipitation_probability_max",
    "precipitation_hours",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "uv_index_max",
    "daylight_duration",
    "sunshine_duration",
    "freezing_level_height",
    "hourly",
    "forecast",
)
# The row's identity travels as keys, and the bookkeeping is each side's own.
WEATHER_NOT_SENT = frozenset(
    {"id", "uuid", "created_at", "updated_at", "location", "observed_on"}
)

LOCATION_FIELDS: tuple[str, ...] = (
    "short_id",
    "name",
    "kind",
    "latitude",
    "longitude",
    "elevation_m",
    "what3words",
    "what3words_fetched_at",
)
LOCATION_NOT_SENT = frozenset({"id", "uuid", "created_at", "updated_at"})

RESORT_FIELDS: tuple[str, ...] = (
    "name",
    "kind",
    "tier",
    "name_alt",
    "canton",
    "notes",
    "latitude",
    "longitude",
    "geocode_source",
    "geocode_confidence",
    "geocoded_at",
    "needs_review",
    "operator_name",
    "website",
    "why_it_matters",
    "num_lifts",
    "num_runs",
    "total_piste_km",
    "base_elevation_m",
    "top_elevation_m",
    "typical_season_open",
    "typical_season_close",
)
# region, location and passes are relations: they travel as natural keys
# in their own members of the item (see feeds.serialise_resort).
RESORT_NOT_SENT = frozenset(
    {
        "id",
        "uuid",
        "created_at",
        "updated_at",
        "slug",
        "region",
        "location",
        "passes",
    }
)


def to_wire(value: Any) -> Any:
    """Return a column value as JSON: dates and datetimes as ISO 8601.

    Args:
        value: A model field's value.

    Returns:
        The value, with ``date`` and ``datetime`` rendered as ISO strings.

    """
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    return value


def dump_fields(instance: models.Model, names: tuple[str, ...]) -> dict[str, Any]:
    """Return ``names`` read off ``instance``, ready for the wire.

    Args:
        instance: The row.
        names: The allowlisted column names to send.

    Returns:
        ``{name: wire value}`` for every name.

    """
    return {name: to_wire(getattr(instance, name)) for name in names}


def load_fields(
    model: type[models.Model], names: tuple[str, ...], data: dict[str, Any]
) -> dict[str, Any]:
    """Return the allowlisted columns of ``data``, parsed back into Python.

    Only names in ``names`` are read, so a field a newer server sends is
    ignored rather than passed to the ORM. Date and datetime columns are
    parsed from their ISO strings; everything else is JSON already.

    Args:
        model: The model the columns belong to.
        names: The allowlisted column names.
        data: The item's ``fields`` member.

    Returns:
        ``{name: python value}`` for every allowlisted name present.

    Raises:
        ValueError: A date or datetime string does not parse.

    """
    loaded: dict[str, Any] = {}
    for name in names:
        if name not in data:
            continue
        value = data[name]
        field = model._meta.get_field(name)
        if value is not None and isinstance(field, models.DateTimeField):
            value = _parsed(parse_datetime(value), name, value)
        elif value is not None and isinstance(field, models.DateField):
            value = _parsed(parse_date(value), name, value)
        loaded[name] = value
    return loaded


def _parsed(parsed: Any, name: str, raw: Any) -> Any:
    """Return ``parsed``, or raise naming the field when it did not parse.

    Args:
        parsed: The result of ``parse_date`` / ``parse_datetime``.
        name: The field's name, for the message.
        raw: The string that was parsed, for the message.

    Returns:
        ``parsed``.

    Raises:
        ValueError: ``parsed`` is None.

    """
    if parsed is None:
        raise ValueError(f"{name}: {raw!r} is not an ISO 8601 date or datetime")
    return parsed
