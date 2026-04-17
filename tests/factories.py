"""
tests/factories.py — FactoryBoy factories for all pipeline models.

Each model has a corresponding factory that produces valid instances with
sensible defaults. Use these in tests to avoid brittle fixture data.

Factories are parameterised with their model type
(e.g. ``DjangoModelFactory[Region]``) so that mypy infers the correct
return type when calling ``RegionFactory(...)`` — no casts needed at
call sites.
"""

import datetime
from datetime import UTC

import factory

from pipeline.models import (
    Bulletin,
    PipelineRun,
    Region,
    RegionBulletin,
    RegionDayRating,
    Resort,
)
from pipeline.services.day_rating import DAY_RATING_VERSION
from subscriptions.models import Subscriber, Subscription


class PipelineRunFactory(factory.django.DjangoModelFactory[PipelineRun]):
    """Factory for PipelineRun instances."""

    class Meta:
        """Factory metadata."""

        model = PipelineRun

    triggered_by = "test"
    status = PipelineRun.Status.PENDING


class RegionFactory(factory.django.DjangoModelFactory[Region]):
    """Factory for Region instances."""

    class Meta:
        """Factory metadata."""

        model = Region

    region_id = factory.Sequence(lambda n: f"CH-{1000 + n}")
    name = factory.LazyAttribute(lambda obj: f"Region {obj.region_id}")
    slug = factory.LazyAttribute(lambda obj: obj.region_id.lower().replace("-", "-"))
    centre = factory.LazyFunction(lambda: {"lon": 7.5, "lat": 46.8})
    boundary = None


class ResortFactory(factory.django.DjangoModelFactory[Resort]):
    """Factory for Resort instances."""

    class Meta:
        """Factory metadata."""

        model = Resort

    name = factory.Sequence(lambda n: f"Resort {n}")
    name_alt = ""
    region = factory.SubFactory(RegionFactory)
    canton = "VS"
    notes = ""


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
    region = factory.SubFactory(RegionFactory)
    region_name_at_time = factory.LazyAttribute(lambda obj: obj.region.name)


class RegionDayRatingFactory(factory.django.DjangoModelFactory[RegionDayRating]):
    """Factory for RegionDayRating instances.

    Defaults ``min_rating`` to the same value as ``max_rating`` (uniform day)
    so existing tests that only set one field continue to work without change.
    """

    class Meta:
        """Factory metadata."""

        model = RegionDayRating

    region = factory.SubFactory(RegionFactory)
    date = factory.LazyFunction(lambda: datetime.date.today())
    min_rating = RegionDayRating.Rating.LOW
    min_subdivision = ""
    max_rating = RegionDayRating.Rating.LOW
    max_subdivision = ""
    source_bulletin = None
    version = DAY_RATING_VERSION


class SubscriberFactory(factory.django.DjangoModelFactory[Subscriber]):
    """Factory for Subscriber instances."""

    class Meta:
        """Factory metadata."""

        model = Subscriber

    email = factory.Sequence(lambda n: f"subscriber{n}@example.com")
    is_active = True
    last_authenticated_at = None


class SubscriptionFactory(factory.django.DjangoModelFactory[Subscription]):
    """Factory for Subscription instances."""

    class Meta:
        """Factory metadata."""

        model = Subscription

    subscriber = factory.SubFactory(SubscriberFactory)
    region = factory.SubFactory(RegionFactory)
