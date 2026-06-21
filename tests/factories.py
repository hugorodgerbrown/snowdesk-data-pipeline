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

from bulletins.models import (
    Bulletin,
    BulletinGrouping,
    BulletinShare,
    BulletinShareClick,
    PipelineRun,
    RegionBulletin,
    RegionDayRating,
    WeatherSnapshot,
)
from bulletins.services.day_rating import DAY_RATING_VERSION
from core.models import RequestLog
from observations.models import FieldObservation
from regions.models import (
    MajorRegion,
    MicroRegion,
    Resort,
    SubRegion,
)
from subscriptions.models import (
    PasskeyCredential,
    PushSubscription,
    Subscriber,
    Subscription,
)


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


class ResortFactory(factory.django.DjangoModelFactory[Resort]):
    """Factory for Resort instances."""

    class Meta:
        """Factory metadata."""

        model = Resort

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


class BulletinFactory(factory.django.DjangoModelFactory[Bulletin]):
    """Factory for Bulletin instances."""

    class Meta:
        """Factory metadata."""

        model = Bulletin

    bulletin_id = factory.Sequence(lambda n: f"bulletin-{n:04d}")
    raw_data = factory.LazyFunction(dict)
    render_model = factory.LazyFunction(lambda: {"version": 0, "traits": []})
    render_model_version = 0
    issued_at = factory.Faker("date_time_this_year", tzinfo=UTC)
    valid_from = factory.LazyAttribute(lambda obj: obj.issued_at)
    valid_to = factory.LazyAttribute(lambda obj: obj.issued_at)
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


class RequestLogFactory(factory.django.DjangoModelFactory[RequestLog]):
    """Factory for RequestLog instances."""

    class Meta:
        """Factory metadata."""

        model = RequestLog

    subscriber = None  # nullable — anonymous requests are the common case
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
    """Factory for plain Django auth.User instances (non-subscriber staff).

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


class SubscriberFactory(factory.django.DjangoModelFactory[Subscriber]):
    """Factory for Subscriber profile instances.

    Each factory creates a linked User (is_staff=False by default) and a
    Subscriber profile pointing to that User.  ``user__username`` is derived
    from ``user__email`` via LazyAttribute so that overriding ``user__email``
    at call sites also fixes the username — matching the production invariant
    enforced by ``get_or_create_for_email``.
    """

    class Meta:
        """Factory metadata."""

        model = Subscriber

    user = factory.SubFactory(
        UserFactory,
        is_staff=False,
        email=factory.Sequence(lambda n: f"subscriber{n}@example.com"),
    )
    status = Subscriber.Status.ACTIVE
    acquisition_request = None  # nullable — not always set


class SubscriptionFactory(factory.django.DjangoModelFactory[Subscription]):
    """Factory for Subscription instances."""

    class Meta:
        """Factory metadata."""

        model = Subscription

    subscriber = factory.SubFactory(SubscriberFactory)
    region = factory.SubFactory(MicroRegionFactory)
    subscribed_via = None  # nullable — not always set
    geo_match_kind = Subscription.GeoMatchKind.UNKNOWN
    geo_matched_region = None  # nullable — not set for unknown / elsewhere


class PasskeyCredentialFactory(factory.django.DjangoModelFactory[PasskeyCredential]):
    """Factory for PasskeyCredential instances."""

    class Meta:
        """Factory metadata."""

        model = PasskeyCredential

    subscriber = factory.SubFactory(SubscriberFactory)
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

    subscriber = None  # nullable — use SubscriberFactory.create() to set
    endpoint = factory.Sequence(lambda n: f"https://push.example.com/endpoint/{n:04d}")
    p256dh = factory.Sequence(lambda n: f"p256dh-key-{n:04d}")
    auth = factory.Sequence(lambda n: f"auth-secret-{n:04d}")
    user_agent = "Mozilla/5.0 (Test)"
    last_used_at = None


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
    target_date = factory.LazyFunction(lambda: datetime.date(2026, 1, 15))
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

    subscriber = factory.SubFactory(SubscriberFactory)
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
