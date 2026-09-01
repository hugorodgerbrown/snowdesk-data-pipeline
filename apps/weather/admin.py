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

**The two JSON series are read-only on every row, and pretty-printed.**
``hourly`` is 24 objects of seven variables and ``forecast`` is up to six
days of the same shape again; in a ``Textarea`` they render as one
unbroken line thousands of characters long, which is unreadable and — since
the only way to edit it is to hand-write valid JSON into that line — not
usefully editable either. They are provider output, written by
``services/fetch.py`` and ``services/backfill.py``; correcting one by hand
is not a workflow the surface should offer. So they are shown as indented
JSON instead, on every row rather than only past ones.
"""

import json
import logging
from typing import Any

from django.contrib import admin
from django.http import HttpRequest
from django.utils.html import format_html
from django.utils.safestring import SafeString

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

# Never editable, on any row: identity, the two audit timestamps, and the
# two JSON series (see the module docstring — they are rendered by the
# display methods below rather than by a Textarea).
_ALWAYS_READONLY = [
    "id",
    "uuid",
    "created_at",
    "updated_at",
    "hourly_json",
    "forecast_json",
]

# Bounded height with its own scrollbar: a season's ``forecast`` runs to
# hundreds of lines, and letting it push the Metadata fieldset off the
# bottom of the page would trade one readability problem for another.
_JSON_STYLE = (
    "max-height:24rem;overflow:auto;white-space:pre;"
    "font-family:ui-monospace,SFMono-Regular,Menlo,monospace;"
    "font-size:0.75rem;line-height:1.5;margin:0;"
)


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
        (
            "Series",
            {
                "fields": ("hourly_json", "forecast_json"),
                "description": (
                    "Provider output, shown as formatted JSON. Not editable "
                    "here on any row — see apps/weather/services/fetch.py and "
                    "services/backfill.py, which write them."
                ),
            },
        ),
        (
            "Metadata",
            {"fields": ("id", "uuid", "created_at", "updated_at")},
        ),
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
                *_DAILY_FIELDS,
            ]
        return list(_ALWAYS_READONLY)

    @admin.display(description="Hourly")
    def hourly_json(self, obj: Weather) -> SafeString | str:
        """Render this day's hourly series as indented JSON.

        Args:
            obj: The row being viewed.

        Returns:
            A scrollable ``<pre>`` block, or an em dash when the column is
            null — which is what a response carrying no hourly block leaves.

        """
        return self._as_json(obj.hourly)

    @admin.display(description="Forecast")
    def forecast_json(self, obj: Weather) -> SafeString | str:
        """Render the forward days as indented JSON.

        Args:
            obj: The row being viewed.

        Returns:
            A scrollable ``<pre>`` block, or an em dash when the column is
            null — which is what every backfilled row carries, deliberately
            (see docs/decisions/weather-backfill-is-an-admin-action.md).

        """
        return self._as_json(obj.forecast)

    @staticmethod
    def _as_json(value: Any) -> SafeString | str:
        """Return ``value`` as an indented, scrollable ``<pre>`` block.

        ``format_html`` escapes the payload, so provider strings cannot
        reach the page as markup.

        Args:
            value: The decoded JSON column, or None.

        Returns:
            The rendered block, or an em dash for a null or empty column.

        """
        if not value:
            return "—"
        return format_html(
            '<pre style="{}">{}</pre>',
            _JSON_STYLE,
            json.dumps(value, indent=2, sort_keys=False),
        )

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Refuse deletion — a row is the record that we said something.

        Args:
            request: The admin request.
            obj: The row, or None for the changelist's bulk action.

        Returns:
            Always False.

        """
        return False
