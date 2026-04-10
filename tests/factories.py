"""
tests/factories.py — FactoryBoy factories for all pipeline models.

Each model has a corresponding factory that produces valid instances with
sensible defaults. Use these in tests to avoid brittle fixture data.
"""

from typing import Any

import factory

from pipeline.models import Bulletin, PipelineRun, Region, RegionBulletin


class PipelineRunFactory(factory.django.DjangoModelFactory):
    """Factory for PipelineRun instances."""

    class Meta:
        """Factory metadata."""

        model = PipelineRun

    triggered_by = "test"
    status = PipelineRun.Status.PENDING

    @classmethod
    def create(cls, **kwargs: Any) -> PipelineRun:
        """Create and return a PipelineRun instance."""
        return super().create(**kwargs)


class RegionFactory(factory.django.DjangoModelFactory):
    """Factory for Region instances."""

    class Meta:
        """Factory metadata."""

        model = Region

    region_id = factory.Sequence(lambda n: f"CH-{1000 + n}")
    name = factory.LazyAttribute(lambda obj: f"Region {obj.region_id}")
    slug = factory.LazyAttribute(lambda obj: obj.region_id.lower().replace("-", "-"))

    @classmethod
    def create(cls, **kwargs: Any) -> Region:
        """Create and return a Region instance."""
        return super().create(**kwargs)


class BulletinFactory(factory.django.DjangoModelFactory):
    """Factory for Bulletin instances."""

    class Meta:
        """Factory metadata."""

        model = Bulletin

    bulletin_id = factory.Sequence(lambda n: f"bulletin-{n:04d}")
    raw_data = factory.LazyFunction(dict)
    issued_at = factory.Faker("date_time_this_year", tzinfo=None)
    valid_from = factory.LazyAttribute(lambda obj: obj.issued_at)
    valid_to = factory.LazyAttribute(lambda obj: obj.issued_at)
    lang = "en"
    unscheduled = False
    pipeline_run = factory.SubFactory(PipelineRunFactory)

    @classmethod
    def create(cls, **kwargs: Any) -> Bulletin:
        """Create and return a Bulletin instance."""
        return super().create(**kwargs)


class RegionBulletinFactory(factory.django.DjangoModelFactory):
    """Factory for RegionBulletin instances."""

    class Meta:
        """Factory metadata."""

        model = RegionBulletin

    bulletin = factory.SubFactory(BulletinFactory)
    region = factory.SubFactory(RegionFactory)
    region_name_at_time = factory.LazyAttribute(lambda obj: obj.region.name)

    @classmethod
    def create(cls, **kwargs: Any) -> RegionBulletin:
        """Create and return a RegionBulletin instance."""
        return super().create(**kwargs)
