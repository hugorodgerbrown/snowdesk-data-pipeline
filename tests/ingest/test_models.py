"""
tests/ingest/test_models.py — Tests for ingest's models and content_hash.

SourceBulletin (SNOW-1250); SourceLocation and SourceWeather (SNOW-1253);
SourcePass and SourceResort (SNOW-1271).

The model kit CLAUDE.md asks of every model — declared rather than inherited
here, because ingest may not import ``apps.core`` — plus the canonical hash.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.utils import timezone

from apps.ingest.models import (
    WEATHER_COLUMNS,
    ImmutableSourceWeatherError,
    SourceBulletin,
    SourceLocation,
    SourcePass,
    SourceResort,
    SourceWeather,
    content_hash,
)
from tests.factories import (
    SourceBulletinFactory,
    SourceLocationFactory,
    SourcePassFactory,
    SourceResortFactory,
    SourceWeatherFactory,
)


def test_the_hash_ignores_key_order() -> None:
    """Two dicts that compare equal hash equal."""
    assert content_hash({"a": 1, "b": [1, 2]}) == content_hash({"b": [1, 2], "a": 1})


def test_the_hash_sees_any_change() -> None:
    """A changed value changes the hash; non-ASCII text hashes stably."""
    assert content_hash({"a": 1}) != content_hash({"a": 2})
    assert content_hash({"t": "Météo"}) == content_hash({"t": "Météo"})
    assert len(content_hash({})) == 64


@pytest.mark.django_db
class TestSourceBulletin:
    """The model kit."""

    def test_to_string_names_the_provider_and_id(self) -> None:
        """``__str__`` delegates to ``to_string``."""
        row = SourceBulletinFactory.create(bulletin_id="abc", provider="ALBINA")
        assert row.to_string() == "ALBINA abc"
        assert str(row) == "ALBINA abc"

    def test_declares_what_base_model_would_give(self) -> None:
        """An id, a uuid and both timestamps, set on create."""
        row = SourceBulletinFactory.create()
        assert row.pk is not None
        assert row.uuid is not None
        assert row.created_at is not None
        assert row.updated_at is not None

    def test_newest_first(self) -> None:
        """Ordered by -created_at, as every model is by default."""
        older = SourceBulletinFactory.create()
        newer = SourceBulletinFactory.create()
        assert list(SourceBulletin.objects.all()) == [newer, older]

    def test_queryset_filters_by_provider(self) -> None:
        """``for_provider`` keeps one provider's rows."""
        slf = SourceBulletinFactory.create(provider="SLF")
        SourceBulletinFactory.create(provider="METEOFRANCE")
        assert list(SourceBulletin.objects.for_provider("SLF")) == [slf]

    def test_the_factory_hashes_its_caaml(self) -> None:
        """A factory row compares as a stored one would."""
        row = SourceBulletinFactory.create()
        assert row.content_hash == content_hash(row.caaml)


@pytest.mark.django_db
class TestSourceLocation:
    """The model kit for the weather service's reference places (SNOW-1253)."""

    def test_to_string_names_the_place_its_coordinate_and_height(self) -> None:
        """A name, the exact coordinate and the height; a tombstone says so."""
        row = SourceLocationFactory.create(
            name="Mont Fort", latitude=46.10361, longitude=7.29889, elevation_m=3328
        )
        assert str(row) == "Mont Fort 46.10361,7.29889 @3328m"
        row.name = ""
        row.elevation_m = None
        row.deleted_at = timezone.now()
        assert row.to_string() == "Unnamed 46.10361,7.29889 (deleted)"

    def test_live_and_tombstoned_split_the_table(self) -> None:
        """``live()`` is what the fetch walks; a tombstone is kept, not deleted."""
        live = SourceLocationFactory.create()
        gone = SourceLocationFactory.create(deleted_at=timezone.now())
        assert list(SourceLocation.objects.live()) == [live]
        assert list(SourceLocation.objects.tombstoned()) == [gone]

    def test_newest_first(self) -> None:
        """``Meta.ordering`` is ``-created_at``."""
        assert SourceLocation._meta.ordering == ["-created_at"]


@pytest.mark.django_db
class TestSourceWeather:
    """The model kit and the past-day backstop."""

    def test_to_string_names_the_place_day_and_code(self) -> None:
        """``__str__`` delegates to ``to_string``."""
        row = SourceWeatherFactory.create(weather_code=71)
        assert str(row) == f"{row.location} {row.observed_on} code=71"

    def test_on_date_filters_by_day(self) -> None:
        """The queryset filters to one calendar day."""
        today = SourceWeatherFactory.create()
        SourceWeatherFactory.create(
            observed_on=timezone.localdate() - dt.timedelta(days=1)
        )
        assert list(SourceWeather.objects.on_date(timezone.localdate())) == [today]

    def test_a_past_row_cannot_be_saved_again(self) -> None:
        """``save()`` refuses to rewrite a stored past day."""
        row = SourceWeatherFactory.create(
            observed_on=timezone.localdate() - dt.timedelta(days=1)
        )
        row.weather_code = 0
        with pytest.raises(ImmutableSourceWeatherError):
            row.save()

    def test_creating_a_past_row_and_refining_today_are_allowed(self) -> None:
        """Only rewriting a past day is refused."""
        SourceWeatherFactory.create(
            observed_on=timezone.localdate() - dt.timedelta(days=3)
        )
        today = SourceWeatherFactory.create()
        today.weather_code = 0
        today.save()
        today.refresh_from_db()
        assert today.weather_code == 0

    def test_the_columns_match_the_apps_weather(self) -> None:
        """An apply is a copy, so the two tables must carry the same columns."""
        from apps.weather.models import Weather

        weather = {f.name for f in Weather._meta.concrete_fields}
        assert set(WEATHER_COLUMNS) <= weather
        source = {f.name for f in SourceWeather._meta.concrete_fields}
        key = {"id", "uuid", "created_at", "updated_at", "location", "observed_on"}
        assert source - key == set(WEATHER_COLUMNS)
        assert weather - key == set(WEATHER_COLUMNS)


@pytest.mark.django_db
class TestSourceDocument:
    """SourcePass and SourceResort share the document shape (SNOW-1271)."""

    @pytest.mark.parametrize("factory_class", [SourcePassFactory, SourceResortFactory])
    def test_to_string_is_the_slug_marked_when_tombstoned(
        self, factory_class: Any
    ) -> None:
        """A tombstone says so, so the admin list reads at a glance."""
        row = factory_class.create(slug="verbier")
        assert str(row) == "verbier"
        row.deleted_at = timezone.now()
        assert row.to_string() == "verbier (deleted)"

    @pytest.mark.parametrize("model", [SourcePass, SourceResort])
    def test_live_and_tombstoned_split_the_table(self, model: Any) -> None:
        """``live()`` is what a host still curates; ``tombstoned()`` the rest."""
        factory_class = (
            SourcePassFactory if model is SourcePass else SourceResortFactory
        )
        live = factory_class.create()
        gone = factory_class.create(deleted_at=timezone.now())
        assert list(model.objects.live()) == [live]
        assert list(model.objects.tombstoned()) == [gone]
