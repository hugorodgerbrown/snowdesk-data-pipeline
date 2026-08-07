"""
tests/factories.py — FactoryBoy factories for all Snowdesk models.

Each model has a corresponding factory that produces valid instances with
sensible defaults. Use these in tests to avoid brittle fixture data.

Factories are parameterised with their model type
(e.g. ``DjangoModelFactory[MicroRegion]``) so that mypy infers the correct
return type when calling ``MicroRegionFactory.create(...)`` — no casts needed at
call sites.
"""

import datetime
from datetime import UTC

import factory
from django.contrib.auth.models import User
from django.utils import timezone as django_timezone

from apps.accounts.models import (
    Account,
    PasskeyCredential,
    PushSubscription,
    Subscription,
)
from apps.bulletins.models import (
    Bulletin,
    BulletinGrouping,
    BulletinShare,
    BulletinShareClick,
    ForecastPoint,
    ForecastPointWeather,
    ForecastPointWeatherHistory,
    PipelineRun,
    RegionBulletin,
    RegionDayRating,
    WeatherSnapshot,
)
from apps.bulletins.services.day_rating import (
    DAY_RATING_VERSION,
    target_day_for_valid_from,
)
from apps.bulletins.services.forecast_points import (
    quantise_elevation,
    quantise_lat,
    quantise_lon,
)
from apps.core.models import RequestLog
from apps.favourites.models import Favourite
from apps.observations.models import FieldObservation
from apps.regions.models import (
    MajorRegion,
    MicroRegion,
    RegionAlias,
    Resort,
    SubRegion,
)
from apps.regions.services.basemap_tiles import MICRO_BAND, build_blob

# A small representative Alpine bbox (roughly Valais) used as
# MicroRegionFactory's ``basemap_download`` default — SNOW-521's rework
# needs a valid blob on every factory-built MicroRegion so pytest/API
# tests exercise the ``download`` property without each test
# hand-building one. Not derived from the factory's own
# ``bbox``/``boundary`` (those default to None/unset and are set
# explicitly per test where geometry matters) — this is a canned,
# schema-valid placeholder, independent of the actual geometry.
_FACTORY_BASEMAP_BBOX = [7.0, 46.0, 8.0, 47.5]


class PipelineRunFactory(factory.django.DjangoModelFactory[PipelineRun]):
    """Factory for PipelineRun instances."""

    class Meta:
        """Factory metadata."""

        model = PipelineRun

    triggered_by = "test"
    status = PipelineRun.Status.PENDING


class MajorRegionFactory(factory.django.DjangoModelFactory[MajorRegion]):
    """Factory for MajorRegion (L1) instances."""

    class Meta:
        """Factory metadata."""

        model = MajorRegion
        django_get_or_create = ("prefix",)

    prefix = factory.Sequence(lambda n: f"CH-{n % 9 + 1}")
    country = "CH"
    name_native = factory.LazyAttribute(lambda obj: f"Major {obj.prefix}")
    name_en = factory.LazyAttribute(lambda obj: f"Major {obj.prefix}")
    display_on_map = True


class SubRegionFactory(factory.django.DjangoModelFactory[SubRegion]):
    """Factory for SubRegion (L2) instances."""

    class Meta:
        """Factory metadata."""

        model = SubRegion
        django_get_or_create = ("prefix",)

    prefix = factory.Sequence(lambda n: f"CH-{n % 9 + 1}{n % 3 + 1}")
    major = factory.SubFactory(
        MajorRegionFactory,
        prefix=factory.LazyAttribute(lambda obj: obj.factory_parent.prefix[:4]),
    )
    name_native = factory.LazyAttribute(lambda obj: f"Sub {obj.prefix}")
    name_en = factory.LazyAttribute(lambda obj: f"Sub {obj.prefix}")


class MicroRegionFactory(factory.django.DjangoModelFactory[MicroRegion]):
    """Factory for MicroRegion (L4 EAWS micro-region) instances."""

    class Meta:
        """Factory metadata."""

        model = MicroRegion

    region_id = factory.Sequence(lambda n: f"CH-{1000 + n}")
    name = factory.LazyAttribute(lambda obj: f"Region {obj.region_id}")
    slug = factory.LazyAttribute(lambda obj: obj.region_id.lower().replace("-", "-"))
    subregion = factory.SubFactory(
        SubRegionFactory,
        prefix=factory.LazyAttribute(lambda obj: obj.factory_parent.region_id[:5]),
    )
    centre = factory.LazyFunction(lambda: {"lon": 7.5, "lat": 46.8})
    boundary = None
    basemap_download = factory.LazyFunction(
        lambda: build_blob(_FACTORY_BASEMAP_BBOX, *MICRO_BAND)
    )


class ResortFactory(factory.django.DjangoModelFactory[Resort]):
    """
    Factory for Resort instances.

    The ``geocoded`` trait sets ``latitude``/``longitude`` to the same
    representative coordinates as ``ForecastPointFactory`` (46.1, 7.4) plus
    ``geocode_source="manual"``, so ``ResortFactory.create(geocoded=True)``
    builds a resort that ``Resort.objects.geocoded()`` — and
    ``link_resort_forecast_points`` — can pick up.
    """

    class Meta:
        """Factory metadata."""

        model = Resort

    class Params:
        """Traits for common variations."""

        geocoded = factory.Trait(
            latitude=46.1,
            longitude=7.4,
            geocode_source="manual",
        )

    name = factory.Sequence(lambda n: f"Resort {n}")
    name_alt = ""
    region = factory.SubFactory(MicroRegionFactory)
    canton = "VS"
    notes = ""
    latitude = None
    longitude = None
    geocode_source = ""
    geocode_confidence = None
    geocoded_at = None
    needs_review = False
    operator_name = ""
    website = ""
    why_it_matters = ""
    num_lifts = None
    num_runs = None
    total_piste_km = None
    base_elevation_m = None
    top_elevation_m = None
    typical_season_open = ""
    typical_season_close = ""
    forecast_point = None


class RegionAliasFactory(factory.django.DjangoModelFactory[RegionAlias]):
    """Factory for RegionAlias instances."""

    class Meta:
        """Factory metadata."""

        model = RegionAlias

    region = factory.SubFactory(MicroRegionFactory)
    alias_text = factory.Sequence(lambda n: f"Alias {n}")


class BulletinFactory(factory.django.DjangoModelFactory[Bulletin]):
    """Factory for Bulletin instances."""

    class Meta:
        """Factory metadata."""

        model = Bulletin

    bulletin_id = factory.Sequence(lambda n: f"bulletin-{n:04d}")
    source = Bulletin.Source.SLF
    raw_data = factory.LazyFunction(dict)
    render_model = factory.LazyFunction(lambda: {"version": 0, "traits": []})
    render_model_version = 0
    issued_at = factory.Faker("date_time_this_year", tzinfo=UTC)
    valid_from = factory.LazyAttribute(lambda obj: obj.issued_at)
    valid_to = factory.LazyAttribute(lambda obj: obj.issued_at)
    target_date = factory.LazyAttribute(
        lambda obj: target_day_for_valid_from(obj.valid_from)
    )
    lang = "en"
    unscheduled = False
    pipeline_run = factory.SubFactory(PipelineRunFactory)


class RegionBulletinFactory(factory.django.DjangoModelFactory[RegionBulletin]):
    """Factory for RegionBulletin instances."""

    class Meta:
        """Factory metadata."""

        model = RegionBulletin

    bulletin = factory.SubFactory(BulletinFactory)
    region = factory.SubFactory(MicroRegionFactory)
    region_name_at_time = factory.LazyAttribute(lambda obj: obj.region.name)


class RegionDayRatingFactory(factory.django.DjangoModelFactory[RegionDayRating]):
    """Factory for RegionDayRating instances.

    Defaults ``min_rating`` to the same value as ``max_rating`` (uniform day)
    so existing tests that only set one field continue to work without change.

    ``source`` defaults to the model's blank default (empty string).
    ``bands`` defaults to the model's null default (None, meaning no
    elevation-band breakdown — the ALBINA-only field).

    ``am_rating`` and ``pm_rating`` default to ``None`` (no time split), mirroring
    the model defaults.
    """

    class Meta:
        """Factory metadata."""

        model = RegionDayRating

    region = factory.SubFactory(MicroRegionFactory)
    date = factory.LazyFunction(lambda: datetime.date.today())
    min_rating = RegionDayRating.Rating.LOW
    min_subdivision = ""
    max_rating = RegionDayRating.Rating.LOW
    max_subdivision = ""
    am_rating = None
    am_subdivision = ""
    pm_rating = None
    pm_subdivision = ""
    source_bulletin = None
    version = DAY_RATING_VERSION
    source = ""
    bands = None


class WeatherSnapshotFactory(factory.django.DjangoModelFactory[WeatherSnapshot]):
    """Factory for WeatherSnapshot instances."""

    class Meta:
        """Factory metadata."""

        model = WeatherSnapshot

    region = factory.SubFactory(MicroRegionFactory)
    valid_for_date = factory.LazyFunction(django_timezone.localdate)
    weather_code = 0  # clear sky
    sunrise = factory.LazyFunction(
        lambda: datetime.datetime(2026, 5, 1, 5, 30, tzinfo=UTC)
    )
    sunset = factory.LazyFunction(
        lambda: datetime.datetime(2026, 5, 1, 20, 45, tzinfo=UTC)
    )


class ForecastPointFactory(factory.django.DjangoModelFactory[ForecastPoint]):
    """
    Factory for ForecastPoint instances.

    ``lat_cell``/``lon_cell``/``elevation_band`` are derived from the
    representative ``latitude``/``longitude``/``elevation`` via
    ``LazyAttribute`` so the quantised keys always stay consistent with the
    coordinates, matching what ``resolve_forecast_point`` would compute.
    """

    class Meta:
        """Factory metadata."""

        model = ForecastPoint

    latitude = 46.1
    longitude = 7.4
    elevation = 1500.0
    lat_cell = factory.LazyAttribute(lambda obj: quantise_lat(obj.latitude))
    lon_cell = factory.LazyAttribute(lambda obj: quantise_lon(obj.longitude))
    elevation_band = factory.LazyAttribute(
        lambda obj: quantise_elevation(obj.elevation)
    )


class ForecastPointWeatherFactory(
    factory.django.DjangoModelFactory[ForecastPointWeather]
):
    """
    Factory for ForecastPointWeather instances.

    Extended fields default to non-null values (rather than ``None``) so
    factory-built rows exercise the "full daily payload" path by default;
    tests covering the null-tolerance case construct rows or defaults dicts
    explicitly instead of relying on this factory.
    """

    class Meta:
        """Factory metadata."""

        model = ForecastPointWeather

    forecast_point = factory.SubFactory(ForecastPointFactory)
    valid_for_date = factory.LazyFunction(django_timezone.localdate)
    weather_code = 0  # clear sky
    sunrise = factory.LazyFunction(
        lambda: datetime.datetime(2026, 5, 1, 5, 30, tzinfo=UTC)
    )
    sunset = factory.LazyFunction(
        lambda: datetime.datetime(2026, 5, 1, 20, 45, tzinfo=UTC)
    )
    temperature_2m_max = 4.0
    temperature_2m_min = -3.0
    apparent_temperature_max = 2.0
    apparent_temperature_min = -6.0
    precipitation_sum = 0.0
    snowfall_sum = 0.0
    precipitation_probability_max = 10
    precipitation_hours = 0.0
    wind_speed_10m_max = 12.0
    wind_gusts_10m_max = 25.0
    wind_direction_10m_dominant = 270
    uv_index_max = 3.5
    daylight_duration = 46800.0
    sunshine_duration = 30000.0
    freezing_level_height = 1800.0
    hourly_series = factory.LazyFunction(
        lambda: [
            {
                "time": "2026-05-01T06:00",
                "temperature_2m": -2.0,
                "snowfall": 0.5,
                "precipitation": 0.5,
                "wind_speed_10m": 10.0,
                "wind_gusts_10m": 20.0,
                "freezing_level_height": 1700.0,
            },
            {
                "time": "2026-05-01T12:00",
                "temperature_2m": 1.0,
                "snowfall": 0.0,
                "precipitation": 0.0,
                "wind_speed_10m": 14.0,
                "wind_gusts_10m": 28.0,
                "freezing_level_height": 1800.0,
            },
        ]
    )


class ForecastPointWeatherHistoryFactory(
    factory.django.DjangoModelFactory[ForecastPointWeatherHistory]
):
    """
    Factory for ForecastPointWeatherHistory instances.

    Defaults to a three-day-out view of a day, since a lead of zero is the
    degenerate case (the day-of forecast, which is what the accompanying
    ForecastPointWeather row already holds). ``lead_days`` is set
    explicitly rather than derived, so a test can construct a deliberately
    inconsistent row when that is the thing under test.
    """

    class Meta:
        """Factory metadata."""

        model = ForecastPointWeatherHistory

    forecast_point = factory.SubFactory(ForecastPointFactory)
    valid_for_date = factory.LazyFunction(django_timezone.localdate)
    issued_date = factory.LazyFunction(
        lambda: django_timezone.localdate() - datetime.timedelta(days=3)
    )
    lead_days = 3
    weather_code = 0  # clear sky
    temperature_2m_max = 4.0
    temperature_2m_min = -3.0
    precipitation_sum = 0.0
    snowfall_sum = 0.0
    wind_speed_10m_max = 12.0
    freezing_level_height = 1800.0


class RequestLogFactory(factory.django.DjangoModelFactory[RequestLog]):
    """Factory for RequestLog instances."""

    class Meta:
        """Factory metadata."""

        model = RequestLog

    account = None  # nullable — anonymous requests are the common case
    session_key = factory.Sequence(lambda n: f"session-{n:04d}")
    method = "POST"
    path = "/"
    referer = ""
    user_agent = "Mozilla/5.0 (Test)"
    ip_address = factory.Sequence(lambda n: f"203.0.113.{n % 255 + 1}")
    country_code = ""
    subdivision_code = ""
    city = ""
    latitude = None
    longitude = None
    accuracy_radius_km = None
    accept_language = ""
    language = ""


class UserFactory(factory.django.DjangoModelFactory[User]):
    """Factory for plain Django auth.User instances (non-account staff).

    ``username`` is derived from ``email`` via LazyAttribute so that the
    production invariant (username == email) is upheld by default.  Override
    either field individually or together — LazyAttribute always resolves from
    whatever ``email`` was set to on the same instance.
    """

    class Meta:
        """Factory metadata."""

        model = User

    email = factory.Sequence(lambda n: f"staff{n}@example.com")
    username = factory.LazyAttribute(lambda obj: obj.email)
    password = factory.django.Password("pass")
    is_staff = True


class AccountFactory(factory.django.DjangoModelFactory[Account]):
    """Factory for Account identity-profile instances (SNOW-430).

    Creates a linked non-staff User (``user__username`` derived from
    ``user__email`` to uphold the username == email invariant) and defaults
    to a **verified** account — the common state for tests that need a
    logged-in user able to submit field reports.  Pass ``is_verified=False``
    for the pre-verification case; ``verified_at`` then defaults to None.
    """

    class Meta:
        """Factory metadata."""

        model = Account

    user = factory.SubFactory(
        UserFactory,
        is_staff=False,
        email=factory.Sequence(lambda n: f"account{n}@example.com"),
    )
    is_verified = True
    verified_at = factory.LazyAttribute(
        lambda obj: django_timezone.now() if obj.is_verified else None
    )
    display_name = ""


class SubscriptionFactory(factory.django.DjangoModelFactory[Subscription]):
    """Factory for Subscription instances."""

    class Meta:
        """Factory metadata."""

        model = Subscription

    account = factory.SubFactory(AccountFactory)
    region = factory.SubFactory(MicroRegionFactory)
    subscribed_via = None  # nullable — not always set
    geo_match_kind = Subscription.GeoMatchKind.UNKNOWN
    geo_matched_region = None  # nullable — not set for unknown / elsewhere


class PasskeyCredentialFactory(factory.django.DjangoModelFactory[PasskeyCredential]):
    """Factory for PasskeyCredential instances.

    Passkeys are keyed to auth.User (not to Account), so the factory uses
    UserFactory as its subfactory.  If you need a passkey for an account,
    pass ``user=account.user`` at the call site.
    """

    class Meta:
        """Factory metadata."""

        model = PasskeyCredential

    user = factory.SubFactory(UserFactory)
    credential_id = factory.Sequence(lambda n: f"cred-id-{n:04d}")
    public_key = b"\x00" * 77
    sign_count = 0
    aaguid = None
    name = factory.Sequence(lambda n: f"Device passkey — {n}")
    device_type = "platform"
    backed_up = False
    last_used_at = None


class BulletinShareFactory(factory.django.DjangoModelFactory[BulletinShare]):
    """Factory for BulletinShare instances."""

    class Meta:
        """Factory metadata."""

        model = BulletinShare

    token = factory.Sequence(lambda n: f"tok{n:04d}")
    bulletin = factory.SubFactory(BulletinFactory)
    region = factory.SubFactory(MicroRegionFactory)
    target_date = factory.LazyFunction(lambda: datetime.date.today())


class BulletinShareClickFactory(factory.django.DjangoModelFactory[BulletinShareClick]):
    """Factory for BulletinShareClick instances."""

    class Meta:
        """Factory metadata."""

        model = BulletinShareClick

    share = factory.SubFactory(BulletinShareFactory)
    request = factory.SubFactory(RequestLogFactory)
    visitor_hash = "abcdef1234567890"


class PushSubscriptionFactory(factory.django.DjangoModelFactory[PushSubscription]):
    """Factory for PushSubscription instances."""

    class Meta:
        """Factory metadata."""

        model = PushSubscription

    account = None  # nullable — use AccountFactory.create() to set
    endpoint = factory.Sequence(lambda n: f"https://push.example.com/endpoint/{n:04d}")
    p256dh = factory.Sequence(lambda n: f"p256dh-key-{n:04d}")
    auth = factory.Sequence(lambda n: f"auth-secret-{n:04d}")
    user_agent = "Mozilla/5.0 (Test)"
    last_used_at = None
    mechanism = PushSubscription.Mechanism.SW
    inactive_at = None


class BulletinGroupingFactory(factory.django.DjangoModelFactory[BulletinGrouping]):
    """Factory for BulletinGrouping instances.

    Produces a BulletinGrouping with a minimal valid GeoJSON Polygon boundary
    and a single CH country entry.  Override ``boundary`` and/or ``countries``
    as needed in individual tests.
    """

    class Meta:
        """Factory metadata."""

        model = BulletinGrouping

    bulletin = factory.SubFactory(BulletinFactory)
    target_date = factory.LazyAttribute(
        lambda obj: target_day_for_valid_from(obj.bulletin.valid_from)
    )
    boundary = factory.LazyFunction(
        lambda: {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
            ],
        }
    )
    countries = factory.LazyFunction(lambda: ["CH"])


class FieldObservationFactory(factory.django.DjangoModelFactory[FieldObservation]):
    """Factory for FieldObservation instances.

    Defaults to a CH-alpine GPS fix (Martigny area) with observation_type
    WHUMPFING and location_source GPS.  Override any field as needed.
    """

    class Meta:
        """Factory metadata."""

        model = FieldObservation

    user = factory.SubFactory(UserFactory)
    region = factory.SubFactory(MicroRegionFactory)
    # WGS-84 coordinates inside Martigny / CH-4115 territory.
    latitude = 46.10
    longitude = 7.10
    accuracy_radius_km = None
    # Raw GPS fix — defaults to None (gps_lat/gps_lon not set separately).
    gps_latitude = None
    gps_longitude = None
    location_source = FieldObservation.LOCATION_SOURCE.GPS
    observed_at = factory.LazyFunction(django_timezone.now)
    observation_type = FieldObservation.OBSERVATION_TYPE.WHUMPFING


class FavouriteFactory(factory.django.DjangoModelFactory[Favourite]):
    """Factory for Favourite instances.

    ``latitude``/``longitude`` vary per instance (``factory.Sequence``) and
    are threaded into the ``forecast_point`` SubFactory so each build lands
    in a distinct (lat_cell, lon_cell, elevation_band) triple — reusing
    ``ForecastPointFactory``'s fixed defaults for every Favourite would trip
    its ``unique_together`` constraint on the second build.
    """

    class Meta:
        """Factory metadata."""

        model = Favourite

    user = factory.SubFactory(UserFactory)
    name = ""
    latitude = factory.Sequence(lambda n: 46.1 + n * 0.05)
    longitude = factory.Sequence(lambda n: 7.4 + n * 0.05)
    elevation = 1500.0
    forecast_point = factory.SubFactory(
        ForecastPointFactory,
        latitude=factory.LazyAttribute(lambda obj: obj.factory_parent.latitude),
        longitude=factory.LazyAttribute(lambda obj: obj.factory_parent.longitude),
        elevation=factory.LazyAttribute(lambda obj: obj.factory_parent.elevation),
    )
    region = None
    resort = None
