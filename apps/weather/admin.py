"""
apps/weather/admin.py — Django admin registrations for the weather models.

Provides list views and detail views for WeatherSnapshot, ForecastCell,
ForecastCellWeather, and ForecastCellWeatherHistory so that operators
can inspect Open-Meteo data without needing direct database access.

``WeatherSnapshotAdmin`` carries a one-click "Fetch today's weather"
button that calls ``fetch_all_regions()`` directly from the changelist
page. ``ForecastCellWeatherAdmin`` (SNOW-416) is its point analogue
without the button — the point pass runs from ``fetch_weather`` only, not
the admin UI — and ``ForecastCellWeatherHistoryAdmin`` (SNOW-575) beside
it is the only read surface on the forecast-convergence series.

Split out of ``apps.bulletins.admin`` by SNOW-654; the admin URL names
moved with the app label, from ``admin:bulletins_*`` to
``admin:weather_*``.
"""

import logging

from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import URLPattern, path, reverse
from django.utils import timezone

from apps.weather.models import (
    ForecastCell,
    ForecastCellWeather,
    ForecastCellWeatherHistory,
    WeatherSnapshot,
)
from apps.weather.services.weather_fetcher import fetch_all_regions

logger = logging.getLogger(__name__)


@admin.register(WeatherSnapshot)
class WeatherSnapshotAdmin(admin.ModelAdmin):
    """Admin view for WeatherSnapshot."""

    change_list_template = "admin/weather/weathersnapshot/change_list.html"

    list_display = [
        "id",
        "region",
        "valid_for_date",
        "weather_code",
        "temperature_2m_max",
        "temperature_2m_min",
        "snowfall_sum",
        "fetched_at",
    ]
    list_filter = ["valid_for_date"]
    search_fields = ["region__region_id", "region__name"]
    list_select_related = ("region",)
    raw_id_fields = ("region",)
    readonly_fields = ("uuid", "created_at", "updated_at", "fetched_at")
    ordering = ["-valid_for_date", "region__region_id"]

    def get_urls(self) -> list[URLPattern]:
        """Add a custom URL for the one-click weather fetch button."""
        custom_urls = [
            path(
                "fetch-today/",
                self.admin_site.admin_view(self.fetch_today_view),
                name="weather_weathersnapshot_fetch_today",
            ),
        ]
        return custom_urls + super().get_urls()

    def fetch_today_view(self, request: HttpRequest) -> HttpResponseRedirect:
        """
        Handle the "Fetch today's weather" button POST.

        Calls fetch_all_regions() for today's date and redirects back to the
        changelist with a success, warning, or error message so the operator
        can see the outcome without inspecting logs.

        A warning-level message is used (rather than success) when any regions
        failed, so the operator notices the partial failure immediately.
        """
        changelist_url = reverse("admin:weather_weathersnapshot_changelist")

        if request.method != "POST":
            return HttpResponseRedirect(changelist_url)

        today = timezone.localdate()
        logger.info("Admin weather fetch triggered for %s", today)

        try:
            counts = fetch_all_regions(today, commit=True)
        except Exception:
            logger.exception("Admin weather fetch failed")
            self.message_user(
                request,
                "Weather fetch failed — check the server logs.",
                messages.ERROR,
            )
            return HttpResponseRedirect(changelist_url)

        created = counts["created"]
        updated = counts["updated"]
        skipped = counts["skipped"]
        failed = counts["failed"]

        summary = (
            f"Fetched today's weather: {created} created, {updated} updated, "
            f"{skipped} skipped, {failed} failed."
        )
        level = messages.WARNING if failed > 0 else messages.SUCCESS
        self.message_user(request, summary, level)

        return HttpResponseRedirect(changelist_url)


@admin.register(ForecastCell)
class ForecastCellAdmin(admin.ModelAdmin):
    """Admin view for ForecastCell."""

    list_display = (
        "id",
        "latitude",
        "longitude",
        "elevation",
        "lat_cell",
        "lon_cell",
        "elevation_band",
    )
    list_filter = ("elevation_band",)
    readonly_fields = ("uuid", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(ForecastCellWeather)
class ForecastCellWeatherAdmin(admin.ModelAdmin):
    """Admin view for ForecastCellWeather."""

    list_display = [
        "id",
        "forecast_cell",
        "valid_for_date",
        "weather_code",
        "temperature_2m_max",
        "snowfall_sum",
        "wind_speed_10m_max",
        "fetched_at",
    ]
    list_filter = ["valid_for_date"]
    list_select_related = ("forecast_cell",)
    raw_id_fields = ("forecast_cell",)
    readonly_fields = ("uuid", "created_at", "updated_at", "fetched_at")
    ordering = ["-valid_for_date", "forecast_cell__id"]


@admin.register(ForecastCellWeatherHistory)
class ForecastCellWeatherHistoryAdmin(admin.ModelAdmin):
    """Admin view for ForecastCellWeatherHistory.

    Ordered so that one forecast day's rows read oldest-issue-first —
    the direction a convergence series is read in.
    """

    list_display = [
        "id",
        "forecast_cell",
        "valid_for_date",
        "issued_date",
        "lead_days",
        "weather_code",
        "temperature_2m_max",
        "snowfall_sum",
        "freezing_level_height",
    ]
    list_filter = ["valid_for_date", "lead_days"]
    list_select_related = ("forecast_cell",)
    raw_id_fields = ("forecast_cell",)
    readonly_fields = ("uuid", "created_at", "updated_at", "fetched_at")
    ordering = ["-valid_for_date", "issued_date"]
