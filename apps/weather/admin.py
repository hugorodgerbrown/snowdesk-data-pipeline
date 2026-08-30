"""
apps/weather/admin.py — Django admin registration for the weather app.

One registration, ``WeatherAdmin``, and the only thing about it that is not
boilerplate is that **a past row renders read-only**.

The immutability rule lives in ``apps.weather.services.upsert`` and is
backstopped by ``Weather.save()``, which raises. Raising is right for a
shell session or a script — it is a bug being reported — but wrong for a
form: a curator who opens a row from last week and presses Save would get a
500 rather than a page that told them, before they typed anything, that
there was nothing to edit. Making the fields read-only turns the guard into
something the interface says rather than something it does.

Today's and forward rows stay editable, because a live row is legitimately
rewritten four times a day and a manual correction to one is a normal thing
to want. Deletion is off everywhere: a row is the record that we said
something, and removing it is not a correction.
"""

import logging
from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from .models import Weather

logger = logging.getLogger(__name__)

# Columns describing the day itself, in the order the API returns them.
_DAILY_FIELDS = [
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
]

# Never editable, on any row: identity and the two audit timestamps.
_ALWAYS_READONLY = ["id", "uuid", "created_at", "updated_at"]


@admin.register(Weather)
class WeatherAdmin(admin.ModelAdmin):
    """Admin view for Weather. Past rows are read-only; see the module docstring."""

    list_display = [
        "location",
        "observed_on",
        "weather_code",
        "temperature_2m_max",
        "temperature_2m_min",
        "snowfall_sum",
        "fetched_at",
    ]
    list_filter = ["observed_on"]
    date_hierarchy = "observed_on"
    search_fields = ["location__name"]
    autocomplete_fields = ["location"]
    list_select_related = ["location"]
    ordering = ["-observed_on"]

    fieldsets = (
        (None, {"fields": ("location", "observed_on", "fetched_at")}),
        ("Conditions", {"fields": tuple(_DAILY_FIELDS)}),
        ("Series", {"fields": ("hourly", "forecast")}),
        ("Metadata", {"fields": tuple(_ALWAYS_READONLY)}),
    )

    def get_readonly_fields(
        self, request: HttpRequest, obj: Weather | None = None
    ) -> list[str]:
        """Return every editable field as read-only when the row's day has passed.

        Args:
            request: The admin request.
            obj: The row being viewed, or None on the add form.

        Returns:
            The read-only field names for this row.

        """
        if obj is not None and obj.is_immutable:
            return _ALWAYS_READONLY + [
                "location",
                "observed_on",
                "fetched_at",
                "hourly",
                "forecast",
                *_DAILY_FIELDS,
            ]
        return list(_ALWAYS_READONLY)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Refuse deletion — a row is the record that we said something.

        Args:
            request: The admin request.
            obj: The row, or None for the changelist's bulk action.

        Returns:
            Always False.

        """
        return False
