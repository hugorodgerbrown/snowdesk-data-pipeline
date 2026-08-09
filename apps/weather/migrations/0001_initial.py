"""
apps/weather/migrations/0001_initial.py — State-only creation of the weather models.

SNOW-654 moved WeatherSnapshot, ForecastPoint, ForecastPointWeather and
ForecastPointWeatherHistory out of ``apps.bulletins`` and into
``apps.weather``. The tables themselves did not move: each model pins
``Meta.db_table`` to the ``bulletins_*`` name it already had, so this
migration is wrapped in ``SeparateDatabaseAndState`` with an empty
``database_operations`` list. ``sqlmigrate`` on it emits no DDL.

The matching state-only ``DeleteModel`` operations live in
``bulletins/0018``, which depends on this one (via ``favourites`` and
``regions``, whose foreign keys are repointed in between).
"""

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """Create the four weather models in migration state only."""

    initial = True

    dependencies = [
        ("regions", "0015_uppercase_resort_geocode_source"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="ForecastPoint",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        (
                            "uuid",
                            models.UUIDField(
                                default=uuid.uuid4, editable=False, unique=True
                            ),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        (
                            "lat_cell",
                            models.IntegerField(
                                help_text="floor(latitude / LAT_CELL_SIZE) — see forecast_points.py."
                            ),
                        ),
                        (
                            "lon_cell",
                            models.IntegerField(
                                help_text="floor(longitude / LON_CELL_SIZE) — see forecast_points.py."
                            ),
                        ),
                        (
                            "elevation_band",
                            models.IntegerField(
                                help_text="floor(elevation / ELEVATION_BAND_SIZE) — see forecast_points.py."
                            ),
                        ),
                        (
                            "latitude",
                            models.FloatField(
                                help_text="Representative latitude of the pin that resolved to this point."
                            ),
                        ),
                        (
                            "longitude",
                            models.FloatField(
                                help_text="Representative longitude of the pin that resolved to this point."
                            ),
                        ),
                        (
                            "elevation",
                            models.FloatField(
                                help_text="Representative elevation in metres, from the Open-Meteo lookup."
                            ),
                        ),
                    ],
                    options={
                        "db_table": "bulletins_forecastpoint",
                        "ordering": ["-created_at"],
                        "abstract": False,
                        "unique_together": {("lat_cell", "lon_cell", "elevation_band")},
                    },
                ),
                migrations.CreateModel(
                    name="ForecastPointWeather",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        (
                            "uuid",
                            models.UUIDField(
                                default=uuid.uuid4, editable=False, unique=True
                            ),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        (
                            "fetched_at",
                            models.DateTimeField(
                                default=django.utils.timezone.now,
                                help_text="When this row was last written (updated on every upsert).",
                            ),
                        ),
                        (
                            "valid_for_date",
                            models.DateField(
                                db_index=True,
                                help_text="The calendar date this weather forecast applies to.",
                            ),
                        ),
                        (
                            "weather_code",
                            models.PositiveSmallIntegerField(
                                help_text="WMO weather interpretation code (0–99)."
                            ),
                        ),
                        (
                            "sunrise",
                            models.DateTimeField(
                                help_text="Sunrise time for this point on valid_for_date (tz-aware, local time)."
                            ),
                        ),
                        (
                            "sunset",
                            models.DateTimeField(
                                help_text="Sunset time for this point on valid_for_date (tz-aware, local time)."
                            ),
                        ),
                        (
                            "temperature_2m_max",
                            models.FloatField(
                                blank=True,
                                help_text="Maximum daily air temperature at 2m, in °C.",
                                null=True,
                            ),
                        ),
                        (
                            "temperature_2m_min",
                            models.FloatField(
                                blank=True,
                                help_text="Minimum daily air temperature at 2m, in °C.",
                                null=True,
                            ),
                        ),
                        (
                            "apparent_temperature_max",
                            models.FloatField(
                                blank=True,
                                help_text="Maximum daily apparent (feels-like) temperature, in °C.",
                                null=True,
                            ),
                        ),
                        (
                            "apparent_temperature_min",
                            models.FloatField(
                                blank=True,
                                help_text="Minimum daily apparent (feels-like) temperature, in °C.",
                                null=True,
                            ),
                        ),
                        (
                            "precipitation_sum",
                            models.FloatField(
                                blank=True,
                                help_text="Total daily precipitation (rain + showers + snow), in mm.",
                                null=True,
                            ),
                        ),
                        (
                            "snowfall_sum",
                            models.FloatField(
                                blank=True,
                                help_text="Total daily snowfall, in cm.",
                                null=True,
                            ),
                        ),
                        (
                            "precipitation_probability_max",
                            models.PositiveSmallIntegerField(
                                blank=True,
                                help_text="Maximum daily precipitation probability, as a percentage.",
                                null=True,
                            ),
                        ),
                        (
                            "precipitation_hours",
                            models.FloatField(
                                blank=True,
                                help_text="Number of hours with measurable precipitation, in hours.",
                                null=True,
                            ),
                        ),
                        (
                            "wind_speed_10m_max",
                            models.FloatField(
                                blank=True,
                                help_text="Maximum daily wind speed at 10m, in km/h.",
                                null=True,
                            ),
                        ),
                        (
                            "wind_gusts_10m_max",
                            models.FloatField(
                                blank=True,
                                help_text="Maximum daily wind gust speed at 10m, in km/h.",
                                null=True,
                            ),
                        ),
                        (
                            "wind_direction_10m_dominant",
                            models.PositiveSmallIntegerField(
                                blank=True,
                                help_text="Dominant daily wind direction at 10m, in degrees (0–360).",
                                null=True,
                            ),
                        ),
                        (
                            "uv_index_max",
                            models.FloatField(
                                blank=True,
                                help_text="Maximum daily UV index (dimensionless).",
                                null=True,
                            ),
                        ),
                        (
                            "daylight_duration",
                            models.FloatField(
                                blank=True,
                                help_text="Total daylight duration, in seconds.",
                                null=True,
                            ),
                        ),
                        (
                            "sunshine_duration",
                            models.FloatField(
                                blank=True,
                                help_text="Total sunshine duration, in seconds.",
                                null=True,
                            ),
                        ),
                        (
                            "freezing_level_height",
                            models.FloatField(
                                blank=True,
                                help_text="Daily maximum freezing level height, in metres. Open-Meteo has no daily freezing-level aggregate — this is derived as the maximum of the hourly freezing_level_height values for the day.",
                                null=True,
                            ),
                        ),
                        (
                            "hourly_series",
                            models.JSONField(
                                blank=True,
                                default=None,
                                help_text="Near-term hourly detail for this day, as a list of dicts with keys time, temperature_2m, snowfall, precipitation, wind_speed_10m, wind_gusts_10m, freezing_level_height. Only populated for the first POINT_HOURLY_DAYS rows of a fetch; None beyond, to keep the JSON payload bounded.",
                                null=True,
                            ),
                        ),
                        (
                            "forecast_point",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="weather_snapshots",
                                to="weather.forecastpoint",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "bulletins_forecastpointweather",
                        "ordering": ["-valid_for_date", "forecast_point__id"],
                        "abstract": False,
                        "indexes": [
                            models.Index(
                                fields=["forecast_point", "valid_for_date"],
                                name="bulletins_f_forecas_e18a91_idx",
                            )
                        ],
                        "unique_together": {("forecast_point", "valid_for_date")},
                    },
                ),
                migrations.CreateModel(
                    name="ForecastPointWeatherHistory",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        (
                            "uuid",
                            models.UUIDField(
                                default=uuid.uuid4, editable=False, unique=True
                            ),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        (
                            "fetched_at",
                            models.DateTimeField(
                                default=django.utils.timezone.now,
                                help_text="When this row was last written (updated on every upsert).",
                            ),
                        ),
                        (
                            "valid_for_date",
                            models.DateField(
                                db_index=True,
                                help_text="The calendar date this forecast applies to.",
                            ),
                        ),
                        (
                            "issued_date",
                            models.DateField(
                                help_text="The date this view of valid_for_date was fetched — the anchor date of the run that produced it."
                            ),
                        ),
                        (
                            "lead_days",
                            models.SmallIntegerField(
                                help_text="valid_for_date - issued_date, in days. Denormalised from the two dates so that querying one lead time across many days (e.g. every three-day-out forecast) is indexable. Signed: a response whose first day precedes the run anchor yields a negative value, which is recorded rather than clamped."
                            ),
                        ),
                        (
                            "weather_code",
                            models.PositiveSmallIntegerField(
                                help_text="WMO weather interpretation code (0–99)."
                            ),
                        ),
                        (
                            "temperature_2m_max",
                            models.FloatField(
                                blank=True,
                                help_text="Maximum daily air temperature at 2m, in °C.",
                                null=True,
                            ),
                        ),
                        (
                            "temperature_2m_min",
                            models.FloatField(
                                blank=True,
                                help_text="Minimum daily air temperature at 2m, in °C.",
                                null=True,
                            ),
                        ),
                        (
                            "precipitation_sum",
                            models.FloatField(
                                blank=True,
                                help_text="Total daily precipitation (rain + showers + snow), in mm.",
                                null=True,
                            ),
                        ),
                        (
                            "snowfall_sum",
                            models.FloatField(
                                blank=True,
                                help_text="Total daily snowfall, in cm.",
                                null=True,
                            ),
                        ),
                        (
                            "wind_speed_10m_max",
                            models.FloatField(
                                blank=True,
                                help_text="Maximum daily wind speed at 10m, in km/h.",
                                null=True,
                            ),
                        ),
                        (
                            "freezing_level_height",
                            models.FloatField(
                                blank=True,
                                help_text="Daily maximum freezing level height, in metres.",
                                null=True,
                            ),
                        ),
                        (
                            "forecast_point",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="forecast_history",
                                to="weather.forecastpoint",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name_plural": "forecast point weather history",
                        "db_table": "bulletins_forecastpointweatherhistory",
                        "ordering": ["-valid_for_date", "issued_date"],
                        "abstract": False,
                        "indexes": [
                            models.Index(
                                fields=["forecast_point", "valid_for_date"],
                                name="bulletins_f_forecas_95f2d5_idx",
                            ),
                            models.Index(
                                fields=["valid_for_date", "lead_days"],
                                name="bulletins_f_valid_f_dd24eb_idx",
                            ),
                        ],
                        "unique_together": {
                            ("forecast_point", "valid_for_date", "issued_date")
                        },
                    },
                ),
                migrations.CreateModel(
                    name="WeatherSnapshot",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        (
                            "uuid",
                            models.UUIDField(
                                default=uuid.uuid4, editable=False, unique=True
                            ),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        (
                            "fetched_at",
                            models.DateTimeField(
                                default=django.utils.timezone.now,
                                help_text="When this snapshot was last written (updated on every upsert).",
                            ),
                        ),
                        (
                            "valid_for_date",
                            models.DateField(
                                db_index=True,
                                help_text="The calendar date this weather observation/forecast applies to.",
                            ),
                        ),
                        (
                            "weather_code",
                            models.PositiveSmallIntegerField(
                                help_text="WMO weather interpretation code (0–99)."
                            ),
                        ),
                        (
                            "sunrise",
                            models.DateTimeField(
                                help_text="Sunrise time for this region on valid_for_date (tz-aware, local time)."
                            ),
                        ),
                        (
                            "sunset",
                            models.DateTimeField(
                                help_text="Sunset time for this region on valid_for_date (tz-aware, local time)."
                            ),
                        ),
                        (
                            "temperature_2m_max",
                            models.FloatField(
                                blank=True,
                                help_text="Maximum daily air temperature at 2m, in °C.",
                                null=True,
                            ),
                        ),
                        (
                            "temperature_2m_min",
                            models.FloatField(
                                blank=True,
                                help_text="Minimum daily air temperature at 2m, in °C.",
                                null=True,
                            ),
                        ),
                        (
                            "snowfall_sum",
                            models.FloatField(
                                blank=True,
                                help_text="Total daily snowfall, in cm.",
                                null=True,
                            ),
                        ),
                        (
                            "region",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="weather_snapshots",
                                to="regions.microregion",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "bulletins_weathersnapshot",
                        "ordering": ["-valid_for_date", "region__region_id"],
                        "abstract": False,
                        "indexes": [
                            models.Index(
                                fields=["region", "valid_for_date"],
                                name="bulletins_w_region__26008e_idx",
                            )
                        ],
                        "unique_together": {("region", "valid_for_date")},
                    },
                ),
            ],
            # Empty by design: the tables already exist under their
            # bulletins_* names and are not touched by the app split.
            database_operations=[],
        ),
    ]
