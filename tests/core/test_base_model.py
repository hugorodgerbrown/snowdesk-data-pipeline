"""Smoke tests for core.models.BaseModel (SNOW-91)."""

from core.models import BaseModel


def test_base_model_is_abstract() -> None:
    assert BaseModel._meta.abstract is True


def test_base_model_has_expected_fields() -> None:
    field_names = {f.name for f in BaseModel._meta.get_fields()}
    assert {"id", "uuid", "created_at", "updated_at"}.issubset(field_names)


def test_base_model_default_ordering() -> None:
    assert BaseModel._meta.ordering == ["-created_at"]


def test_subscriber_inherits_base_model() -> None:
    """Subscriber is now a profile model extending BaseModel, not AbstractBaseUser."""
    from subscriptions.models import Subscriber

    assert issubclass(Subscriber, BaseModel)


def test_pipeline_models_inherit_base_model() -> None:
    from bulletins.models import Bulletin, PipelineRun
    from regions.models import MicroRegion

    for model in (Bulletin, PipelineRun, MicroRegion):
        assert issubclass(model, BaseModel)
