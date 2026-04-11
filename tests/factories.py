"""
tests/factories.py — FactoryBoy factories for all pipeline models.

Each model has a corresponding factory that produces valid instances with
sensible defaults. Use these in tests to avoid brittle fixture data.

Note on typing: factory-boy uses a metaclass to intercept ``Factory(...)``
calls and return a model instance rather than a Factory instance. Mypy
reads the metaclass's ``__call__`` signature, which is untyped, and
therefore sees ``Factory(...)`` as returning the Factory subclass itself.
Neither ``__new__`` overrides nor ``TYPE_CHECKING`` stubs defeat this —
call sites that pass a factory instance to a typed function must
``cast`` or use ``typing.cast`` to tell mypy the real type.
"""

from datetime import UTC

import factory

from pipeline.models import Bulletin, PipelineRun, Region, RegionBulletin


class PipelineRunFactory(factory.django.DjangoModelFactory):
    """Factory for PipelineRun instances."""

    class Meta:
        """Factory metadata."""

        model = PipelineRun

    triggered_by = "test"
    status = PipelineRun.Status.PENDING


class RegionFactory(factory.django.DjangoModelFactory):
    """Factory for Region instances."""

    class Meta:
        """Factory metadata."""

        model = Region

    region_id = factory.Sequence(lambda n: f"CH-{1000 + n}")
    name = factory.LazyAttribute(lambda obj: f"Region {obj.region_id}")
    slug = factory.LazyAttribute(lambda obj: obj.region_id.lower().replace("-", "-"))


class BulletinFactory(factory.django.DjangoModelFactory):
    """Factory for Bulletin instances."""

    class Meta:
        """Factory metadata."""

        model = Bulletin

    bulletin_id = factory.Sequence(lambda n: f"bulletin-{n:04d}")
    raw_data = factory.LazyFunction(dict)
    issued_at = factory.Faker("date_time_this_year", tzinfo=UTC)
    valid_from = factory.LazyAttribute(lambda obj: obj.issued_at)
    valid_to = factory.LazyAttribute(lambda obj: obj.issued_at)
    lang = "en"
    unscheduled = False
    pipeline_run = factory.SubFactory(PipelineRunFactory)


class RegionBulletinFactory(factory.django.DjangoModelFactory):
    """Factory for RegionBulletin instances."""

    class Meta:
        """Factory metadata."""

        model = RegionBulletin

    bulletin = factory.SubFactory(BulletinFactory)
    region = factory.SubFactory(RegionFactory)
    region_name_at_time = factory.LazyAttribute(lambda obj: obj.region.name)
