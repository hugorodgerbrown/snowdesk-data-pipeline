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
    PipelineRun,
    RegionBulletin,
    RegionDayRating,
)
from apps.bulletins.services.day_rating import (
    DAY_RATING_VERSION,
    target_day_for_valid_from,
)
from apps.core.models import RequestLog
from apps.downloads.models import DownloadArea
from apps.favourites.models import Favourite
from apps.locations.models import Location, ResortLocation
from apps.observations.models import FieldObservation
from apps.regions.models import (
    MajorRegion,
    MicroRegion,
    RegionAlias,
    Resort,
    SubRegion,
)
from apps.regions.services.basemap_tiles import MICRO_BAND, build_blob
from apps.routes.models import Route
from apps.weather.models import Weather

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

    The ``geocoded`` trait sets ``latitude``/``longitude`` to representative
    coordinates (46.1, 7.4) plus ``geocode_source="MANUAL"``, so
    ``ResortFactory.create(geocoded=True)`` builds a resort that
    ``Resort.objects.geocoded()`` can pick up.
    """

    class Meta:
        """Factory metadata."""

        model = Resort

    class Params:
        """Traits for common variations."""

        geocoded = factory.Trait(
            latitude=46.1,
            longitude=7.4,
            geocode_source=Resort.GeocodeSource.MANUAL,
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
    tier = Resort.Tier.STANDARD
    num_lifts = None
    num_runs = None
    total_piste_km = None
    base_elevation_m = None
    top_elevation_m = None
    typical_season_open = ""
    typical_season_close = ""


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


class LocationFactory(factory.django.DjangoModelFactory[Location]):
    """
    Factory for Location instances.

    Defaults to a **named, curated** location, because that is the case
    almost every test is about — the anonymous rows SNOW-704 and SNOW-709
    mint are the exception, and the ``anonymous`` trait produces one.

    ``elevation_m`` is left null by default, matching a freshly imported
    row: it is resolved out-of-band via an Open-Meteo elevation call. The
    ``resolved`` trait supplies it for tests that need a location whose
    height is already known.
    """

    class Meta:
        """Factory metadata."""

        model = Location

    class Params:
        """Traits for common variations."""

        # A location minted from a favourite or an observation: no name and
        # no kind, because naming is a curation act.
        anonymous = factory.Trait(name="", kind="")
        # Elevation filled in, as after an out-of-band resolution pass.
        resolved = factory.Trait(elevation_m=1500.0)

    name = factory.Sequence(lambda n: f"Location {n}")
    kind = Location.KIND.PEAK
    latitude = 46.1
    longitude = 7.4


class ResortLocationFactory(factory.django.DjangoModelFactory[ResortLocation]):
    """
    Factory for ResortLocation instances.

    Defaults to a non-primary ``TOP`` link, so a test that wants the resort
    page's hero has to say ``is_primary=True`` rather than getting it by
    accident.
    """

    class Meta:
        """Factory metadata."""

        model = ResortLocation

    resort = factory.SubFactory(ResortFactory)
    location = factory.SubFactory(LocationFactory)
    role = ResortLocation.ROLE.TOP
    is_primary = False


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
    # The anonymous Location this report happened at (SNOW-709), threaded
    # from the same coordinates. Pass ``location=None`` for a pre-SNOW-709
    # row.
    location = factory.SubFactory(
        LocationFactory,
        anonymous=True,
        latitude=factory.LazyAttribute(lambda obj: obj.factory_parent.latitude),
        longitude=factory.LazyAttribute(lambda obj: obj.factory_parent.longitude),
    )
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
    are threaded into the ``location`` SubFactory, so each Favourite and its
    anonymous Location agree on where the pin is.
    """

    class Meta:
        """Factory metadata."""

        model = Favourite

    user = factory.SubFactory(UserFactory)
    name = ""
    latitude = factory.Sequence(lambda n: 46.1 + n * 0.05)
    longitude = factory.Sequence(lambda n: 7.4 + n * 0.05)
    elevation = 1500.0
    # The anonymous Location this pin is (SNOW-704), threaded from the same
    # coordinates — which is what ``create_favourite`` builds. Pass
    # ``location=None`` to get a pre-SNOW-704 row.
    location = factory.SubFactory(
        LocationFactory,
        anonymous=True,
        latitude=factory.LazyAttribute(lambda obj: obj.factory_parent.latitude),
        longitude=factory.LazyAttribute(lambda obj: obj.factory_parent.longitude),
        elevation_m=factory.LazyAttribute(lambda obj: obj.factory_parent.elevation),
    )
    region = None
    resort = None


class RouteFactory(factory.django.DjangoModelFactory[Route]):
    """Factory for Route instances (SNOW-685).

    Produces a short three-point track climbing 100 m, with the derived
    fields consistent with ``points`` so a test that reads ``bounds`` or
    ``point_count`` off a factory-built row sees the same relationship
    ``apps.routes.services.gpx.parse_gpx`` would have produced.
    ``distance_m`` is a round stand-in rather than the true great-circle
    length of those coordinates — tests that care about the real maths
    exercise the parser directly.

    The default track only climbs, so ``descent_m`` is a true 0.0 rather
    than a null: null means "the source file carried no elevation data",
    and these points plainly carry some. A test that needs the unknown
    case passes ``ascent_m=None, descent_m=None`` with elevation-free
    ``points`` to match.

    ``started_at`` / ``finished_at`` default to a two-hour span, the shape
    a recorded upload has (SNOW-750). A test that needs an untimed route —
    a planned ``<rte>``, or a row predating migration ``0003`` — passes
    ``started_at=None, finished_at=None``; the pair is always set or unset
    together, never one of the two.
    """

    class Meta:
        """Factory metadata."""

        model = Route

    user = factory.SubFactory(UserFactory)
    name = factory.Sequence(lambda n: f"Route {n}")
    source_filename = "track.gpx"
    # [lon, lat, ele] — GeoJSON axis order, as stored.
    points = factory.LazyFunction(
        lambda: [
            [7.4, 46.1, 1500.0],
            [7.41, 46.11, 1550.0],
            [7.42, 46.12, 1600.0],
        ]
    )
    distance_m = 2500.0
    ascent_m = 100.0
    descent_m = 0.0
    started_at = datetime.datetime(2026, 3, 13, 9, 0, tzinfo=UTC)
    finished_at = datetime.datetime(2026, 3, 13, 11, 0, tzinfo=UTC)
    point_count = 3
    bounds = factory.LazyFunction(lambda: [7.4, 46.1, 7.42, 46.12])


class DownloadAreaFactory(factory.django.DjangoModelFactory[DownloadArea]):
    """Factory for DownloadArea instances (SNOW-749).

    Defaults to a REGION area, which is the common case and the one with
    the simpler shape: ``area_id`` and ``region_id`` agree, and ``bbox``
    stays null because a region's tiles are computed from its own
    boundary rather than a box.

    A custom area needs both halves overridden together — the ``custom-``
    prefix is what ``apps.downloads.views`` and
    ``basemap_download_core.js``'s ``isCustomAreaId`` each read to decide
    what an id is, so an ``area_id`` and a ``kind`` that disagree would
    describe a row neither side could act on:

        DownloadAreaFactory.create(
            area_id="custom-abc",
            kind=DownloadArea.KIND.CUSTOM,
            region_id="",
            bbox=[7.0, 45.9, 7.3, 46.1],
        )
    """

    class Meta:
        """Factory metadata."""

        model = DownloadArea

    user = factory.SubFactory(UserFactory)
    region_id = factory.Sequence(lambda n: f"ch-{4000 + n}")
    area_id = factory.LazyAttribute(lambda o: f"region-{o.region_id}")
    kind = DownloadArea.KIND.REGION
    bbox = None
    basemap_key = "outdoor"
    name = ""


class WeatherFactory(factory.django.DjangoModelFactory[Weather]):
    """Factory for Weather instances.

    Defaults to **today**, because today's row is the writable one and a
    test that wants the immutable case should have to say
    ``observed_on=<a past date>`` rather than getting it by accident of
    when the suite runs.

    ``forecast`` defaults to an empty list rather than null: a row written
    by the fetcher always has the key, even when the model horizon left
    nothing forward to record, and a test asserting on it should not have
    to distinguish "no forward days" from "never fetched".
    """

    class Meta:
        """Factory metadata."""

        model = Weather

    location = factory.SubFactory(LocationFactory)
    observed_on = factory.LazyFunction(django_timezone.localdate)
    fetched_at = factory.LazyFunction(django_timezone.now)
    weather_code = 3
    sunrise = factory.LazyFunction(
        lambda: django_timezone.now().replace(hour=6, minute=30)
    )
    sunset = factory.LazyFunction(
        lambda: django_timezone.now().replace(hour=18, minute=45)
    )
    temperature_2m_max = 4.5
    temperature_2m_min = -2.0
    snowfall_sum = 1.2
    hourly = None
    # LazyFunction, not a bare [] — a mutable class attribute would be the
    # same list object on every row the factory builds.
    forecast = factory.LazyFunction(list)
