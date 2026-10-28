"""
tests/data_feed/services/test_apply.py — Tests for apps.data_feed.services.apply (SNOW-1197).

Each feed's apply function across its outcomes — created, updated,
unchanged, and weather's kept — in both modes. The dry run must decide the
same outcome and write nothing; the second application of the same item
must be UNCHANGED, which is the idempotency the command promises.

SNOW-1198: a location a dry run would create counts as present for the
weather and resorts after it. SNOW-1199: a page's reads cost the same
number of queries whatever its size.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.bulletins.models import Bulletin, RegionBulletin
from apps.data_feed import feeds
from apps.data_feed.services.apply import (
    ApplyError,
    Outcome,
    apply_bulletin,
    apply_location,
    apply_resort,
    apply_weather,
    lookups_for,
)
from apps.locations.models import Location, ResortLocation
from apps.regions.models import MicroRegion, Pass, Resort
from apps.weather.models import Weather
from tests.factories import (
    LocationFactory,
    MicroRegionFactory,
    PassFactory,
    PipelineRunFactory,
    ResortFactory,
    ResortLocationFactory,
    WeatherFactory,
)


def _caaml(bulletin_id: str = "b-1", **overrides: Any) -> dict[str, Any]:
    """Return a minimal SLF-shaped CAAML bulletin for region CH-4115."""
    base: dict[str, Any] = {
        "bulletinID": bulletin_id,
        "publicationTime": "2026-10-20T15:00:00Z",
        "validTime": {
            "startTime": "2026-10-20T15:00:00Z",
            "endTime": "2026-10-21T15:00:00Z",
        },
        "lang": "en",
        "unscheduled": False,
        "regions": [{"regionID": "CH-4115", "name": "Piz Buin"}],
        "dangerRatings": [],
        "avalancheProblems": [],
        "customData": {"CH": {}},
    }
    return base | overrides


def _location_item(**fields: Any) -> dict[str, Any]:
    """Return a locations feed item for a new curated location."""
    return {
        "uuid": str(uuid.uuid4()),
        "updated_at": "2026-10-23T06:00:00+00:00",
        "fields": {
            "short_id": "abcdefghijk",
            "name": "Mont Fort",
            "kind": "PEAK",
            "latitude": 46.08,
            "longitude": 7.31,
            "elevation_m": 3329.0,
            "what3words": None,
            "what3words_fetched_at": None,
        }
        | fields,
        "centroid_of": [],
    }


@pytest.mark.django_db
class TestApplyLocation:
    """Curated locations, matched on uuid."""

    def test_dry_run_decides_and_writes_nothing(self) -> None:
        """A new location is CREATED in a dry run too, with no row made."""
        assert apply_location(_location_item(), commit=False) is Outcome.CREATED
        assert not Location.objects.exists()

    def test_create_then_unchanged(self) -> None:
        """The second application of the same item is UNCHANGED."""
        item = _location_item()
        assert apply_location(item, commit=True) is Outcome.CREATED
        location = Location.objects.get(uuid=item["uuid"])
        assert location.name == "Mont Fort"
        assert location.short_id == "abcdefghijk"
        assert apply_location(item, commit=True) is Outcome.UNCHANGED

    def test_a_source_uuid_reads_as_unchanged(self) -> None:
        """Codex on #1260: a wire source_uuid compares as a UUID, not a string."""
        item = _location_item(source_uuid=str(uuid.uuid4()))
        assert apply_location(item, commit=True) is Outcome.CREATED
        assert apply_location(item, commit=True) is Outcome.UNCHANGED

    def test_a_moved_pin_is_updated(self) -> None:
        """A corrected coordinate updates the same row."""
        item = _location_item()
        apply_location(item, commit=True)
        item["fields"]["latitude"] = 46.09
        assert apply_location(item, commit=False) is Outcome.UPDATED
        assert Location.objects.get(uuid=item["uuid"]).latitude == 46.08
        assert apply_location(item, commit=True) is Outcome.UPDATED
        assert Location.objects.get(uuid=item["uuid"]).latitude == 46.09

    def test_centroids_are_linked(self) -> None:
        """A region named in centroid_of points at the location afterwards."""
        region = MicroRegionFactory.create(region_id="CH-9100")
        item = _location_item(name="")
        item["centroid_of"] = ["CH-9100", "CH-NOT-HERE"]
        apply_location(item, commit=True)
        region.refresh_from_db()
        assert region.centroid_location is not None
        assert str(region.centroid_location.uuid) == item["uuid"]

    def test_a_stale_centroid_alone_is_an_update(self) -> None:
        """An unchanged location whose region points elsewhere is re-linked."""
        item = _location_item()
        apply_location(item, commit=True)
        region = MicroRegionFactory.create(
            region_id="CH-9101", centroid_location=LocationFactory.create()
        )
        item["centroid_of"] = ["CH-9101"]
        assert apply_location(item, commit=True) is Outcome.UPDATED
        region.refresh_from_db()
        assert region.centroid_location is not None
        assert str(region.centroid_location.uuid) == item["uuid"]

    def test_a_centroid_dropped_on_production_is_cleared(self) -> None:
        """A region here that production no longer lists is unlinked."""
        item = _location_item()
        item["centroid_of"] = ["CH-9102"]
        region = MicroRegionFactory.create(region_id="CH-9102")
        apply_location(item, commit=True)
        item["centroid_of"] = []
        assert apply_location(item, commit=False) is Outcome.UPDATED
        assert MicroRegion.objects.filter(
            pk=region.pk, centroid_location__isnull=False
        ).exists()
        assert apply_location(item, commit=True) is Outcome.UPDATED
        region.refresh_from_db()
        assert region.centroid_location is None
        assert apply_location(item, commit=True) is Outcome.UNCHANGED


def _resort_item(resort: Resort) -> dict[str, Any]:
    """Return the feed's own item for ``resort``."""
    return feeds.serialise_resort(feeds.FEEDS["resorts"].queryset().get(pk=resort.pk))


@pytest.mark.django_db
class TestApplyResort:
    """Resorts, matched on slug, with their relations."""

    def test_unchanged_when_identical(self) -> None:
        """A resort applied onto itself is UNCHANGED."""
        resort = ResortFactory.create(geocoded=True)
        ResortLocationFactory.create(resort=resort)
        resort.passes.add(PassFactory.create())
        assert apply_resort(_resort_item(resort), commit=True) is Outcome.UNCHANGED

    def test_create_with_every_relation(self) -> None:
        """A resort new here arrives with its region, pin, passes and links."""
        resort = ResortFactory.create(name="Verbier", geocoded=True)
        link = ResortLocationFactory.create(
            resort=resort, role=ResortLocation.ROLE.BASE, is_primary=True
        )
        resort.passes.add(PassFactory.create(slug="magic-pass", name="Magic Pass"))
        item = _resort_item(resort)
        resort.delete()
        Pass.objects.all().delete()

        assert apply_resort(item, commit=False) is Outcome.CREATED
        assert not Resort.objects.exists()
        assert apply_resort(item, commit=True) is Outcome.CREATED
        made = Resort.objects.get(slug="verbier")
        assert made.location is not None
        assert str(made.location.uuid) == item["location"]
        assert list(made.passes.values_list("slug", flat=True)) == ["magic-pass"]
        (made_link,) = made.resort_locations.all()
        assert made_link.location_id == link.location_id
        assert made_link.is_primary
        assert apply_resort(item, commit=True) is Outcome.UNCHANGED

    def test_changes_are_updated(self) -> None:
        """A changed column, a dropped link and a new pass are all applied."""
        resort = ResortFactory.create(name="Nendaz")
        ResortLocationFactory.create(resort=resort)
        item = _resort_item(resort)
        item["fields"]["num_lifts"] = 40
        item["locations"] = []
        item["passes"] = [{"slug": "4v", "name": "4 Vallées", "website": ""}]

        assert apply_resort(item, commit=True) is Outcome.UPDATED
        resort.refresh_from_db()
        assert resort.num_lifts == 40
        assert not resort.resort_locations.exists()
        assert list(resort.passes.values_list("slug", flat=True)) == ["4v"]

    def test_a_renamed_pass_is_updated(self) -> None:
        """A pass whose name or website changed on production is rewritten."""
        resort = ResortFactory.create()
        resort.passes.add(PassFactory.create(slug="magic-pass", name="Magic"))
        item = _resort_item(resort)
        item["passes"] = [
            {"slug": "magic-pass", "name": "Magic Pass", "website": "https://m.ch/"}
        ]
        assert apply_resort(item, commit=True) is Outcome.UPDATED
        renamed = Pass.objects.get(slug="magic-pass")
        assert (renamed.name, renamed.website) == ("Magic Pass", "https://m.ch/")
        assert apply_resort(item, commit=True) is Outcome.UNCHANGED

    def test_unknown_region_fails_even_in_a_dry_run(self) -> None:
        """A region this database lacks is an error, not a create."""
        item = _resort_item(ResortFactory.create())
        item["region"] = "XX-0000"
        with pytest.raises(ApplyError, match="XX-0000"):
            apply_resort(item, commit=False)

    def test_unknown_location_fails(self) -> None:
        """A link to a location not pulled yet says to pull locations first."""
        item = _resort_item(ResortFactory.create())
        item["locations"] = [
            {"location": str(uuid.uuid4()), "role": "TOP", "is_primary": False}
        ]
        with pytest.raises(ApplyError, match="pull locations first"):
            apply_resort(item, commit=True)


@pytest.mark.django_db
class TestApplyBulletin:
    """Bulletins, replayed through upsert_bulletin."""

    @pytest.fixture(autouse=True)
    def _region(self) -> MicroRegion:
        """The region the CAAML names."""
        return MicroRegionFactory.create(region_id="CH-4115")

    def _item(self, **overrides: Any) -> dict[str, Any]:
        """Return a bulletins feed item."""
        return {
            "bulletin_id": "b-1",
            "updated_at": "2026-10-20T15:05:00+00:00",
            "pdf_url": "https://example.org/b-1.pdf",
            "caaml": _caaml(**overrides),
        }

    def test_create_rebuilds_the_derived_rows_here(self) -> None:
        """The bulletin's region link is made by this database's own code."""
        run = PipelineRunFactory.create()
        assert apply_bulletin(self._item(), commit=False, run=None) is Outcome.CREATED
        assert not Bulletin.objects.exists()
        assert apply_bulletin(self._item(), commit=True, run=run) is Outcome.CREATED
        bulletin = Bulletin.objects.get(bulletin_id="b-1")
        assert bulletin.pdf_url == "https://example.org/b-1.pdf"
        assert bulletin.pipeline_run == run
        assert RegionBulletin.objects.filter(bulletin=bulletin).count() == 1

    def test_identical_is_unchanged_and_not_rewritten(self) -> None:
        """A re-pull of the same bulletin does not touch the row."""
        run = PipelineRunFactory.create()
        apply_bulletin(self._item(), commit=True, run=run)
        before = Bulletin.objects.get(bulletin_id="b-1").updated_at
        assert apply_bulletin(self._item(), commit=True, run=run) is Outcome.UNCHANGED
        assert Bulletin.objects.get(bulletin_id="b-1").updated_at == before

    def test_a_correction_is_an_update(self) -> None:
        """Changed CAAML is replayed as an update."""
        run = PipelineRunFactory.create()
        apply_bulletin(self._item(), commit=True, run=run)
        changed = self._item(unscheduled=True)
        assert apply_bulletin(changed, commit=True, run=run) is Outcome.UPDATED
        assert Bulletin.objects.get(bulletin_id="b-1").unscheduled is True

    def test_no_caaml_is_an_error(self) -> None:
        """An item with nothing to replay fails."""
        with pytest.raises(ApplyError):
            apply_bulletin({"bulletin_id": "b-2", "caaml": None}, commit=True, run=None)

    def test_commit_needs_a_run(self) -> None:
        """A write is always attributed to a PipelineRun."""
        with pytest.raises(ValueError, match="PipelineRun"):
            apply_bulletin(self._item(), commit=True, run=None)


@pytest.mark.django_db
class TestApplyWeather:
    """Weather, through upsert_weather and its immutability rule."""

    def _item(self, location: Location, day: dt.date, **fields: Any) -> dict:
        """Return a weather feed item made from a factory row's values."""
        row = WeatherFactory.build(location=location, observed_on=day, **fields)
        return feeds.serialise_weather(row)

    def test_create_then_unchanged(self) -> None:
        """A new location-day is created; the same item again is unchanged."""
        location = LocationFactory.create()
        item = self._item(location, dt.date(2026, 10, 1))
        assert apply_weather(item, commit=False) is Outcome.CREATED
        assert not Weather.objects.exists()
        assert apply_weather(item, commit=True) is Outcome.CREATED
        assert apply_weather(item, commit=True) is Outcome.UNCHANGED

    def test_a_past_day_is_kept(self) -> None:
        """A recorded past day that differs is KEPT, never rewritten."""
        location = LocationFactory.create()
        day = timezone.localdate() - dt.timedelta(days=2)
        WeatherFactory.create(location=location, observed_on=day, weather_code=1)
        item = self._item(location, day, weather_code=71)
        assert apply_weather(item, commit=True) is Outcome.KEPT
        assert Weather.objects.get(location=location).weather_code == 1

    def test_today_is_refined(self) -> None:
        """Today's row is updated in place."""
        location = LocationFactory.create()
        today = timezone.localdate()
        WeatherFactory.create(location=location, observed_on=today, weather_code=1)
        item = self._item(location, today, weather_code=71)
        assert apply_weather(item, commit=True) is Outcome.UPDATED
        assert Weather.objects.get(location=location).weather_code == 71

    def test_unknown_location_fails(self) -> None:
        """Weather for a location not pulled yet says to pull locations first."""
        item = self._item(LocationFactory.build(), dt.date(2026, 10, 1))
        with pytest.raises(ApplyError, match="pull locations first"):
            apply_weather(item, commit=True)

    def test_bad_day_fails(self) -> None:
        """An unparseable observed_on is an error."""
        location = LocationFactory.create()
        item = self._item(location, dt.date(2026, 10, 1))
        item["observed_on"] = "yesterday"
        with pytest.raises(ApplyError, match="yesterday"):
            apply_weather(item, commit=True)


@pytest.mark.django_db
class TestPlannedLocations:
    """SNOW-1198: a dry run treats a location it would create as present."""

    def test_weather_for_a_planned_location_would_be_created(self) -> None:
        """The location is new, so its day is too."""
        item = _location_item()
        planned: set[str] = set()
        assert (
            apply_location(
                item,
                commit=False,
                lookups=lookups_for("locations", [item], planned=planned),
            )
            is Outcome.CREATED
        )
        assert planned == {item["uuid"]}
        day = {
            "location": item["uuid"],
            "observed_on": "2026-10-01",
            "fields": {"weather_code": 3},
        }
        lookups = lookups_for("weather", [day], planned=planned)
        assert apply_weather(day, commit=False, lookups=lookups) is Outcome.CREATED
        assert not Location.objects.exists()

    def test_a_resort_linking_a_planned_location_would_be_created(self) -> None:
        """A resort whose links are all planned resolves instead of failing."""
        resort = ResortFactory.create(geocoded=True)
        ResortLocationFactory.create(resort=resort)
        item = _resort_item(resort)
        planned = {item["location"]} | {link["location"] for link in item["locations"]}
        resort.delete()
        Location.objects.all().delete()
        lookups = lookups_for("resorts", [item], planned=planned)
        assert apply_resort(item, commit=False, lookups=lookups) is Outcome.CREATED

    def test_an_unplanned_location_still_fails(self) -> None:
        """A location neither here nor planned fails, as a commit would."""
        day = {
            "location": str(uuid.uuid4()),
            "observed_on": "2026-10-01",
            "fields": {},
        }
        lookups = lookups_for("weather", [day], planned={str(uuid.uuid4())})
        with pytest.raises(ApplyError, match="pull locations first"):
            apply_weather(day, commit=False, lookups=lookups)


@pytest.mark.django_db
class TestPageLookups:
    """SNOW-1199: one page's reads are a fixed number of queries."""

    @staticmethod
    def _page_queries(feed: str, items: list[dict[str, Any]]) -> int:
        """Count the queries a dry run of one page costs, lookups included."""
        apply = {
            "locations": apply_location,
            "resorts": apply_resort,
            "weather": apply_weather,
        }[feed]
        with CaptureQueriesContext(connection) as ctx:
            lookups = lookups_for(feed, items)
            for item in items:
                apply(item, commit=False, lookups=lookups)
        return len(ctx.captured_queries)

    def test_locations(self) -> None:
        """Existing locations with centroids: as cheap for 12 as for 2."""
        items = []
        for _ in range(12):
            location = LocationFactory.create()
            MicroRegionFactory.create(centroid_location=location)
            items.append(feeds.serialise_location(location))
        assert self._page_queries("locations", items[:2]) == self._page_queries(
            "locations", items
        )

    def test_resorts(self) -> None:
        """Existing resorts with links and passes: as cheap for 12 as for 2."""
        items = []
        for _ in range(12):
            resort = ResortFactory.create(geocoded=True)
            ResortLocationFactory.create(resort=resort)
            resort.passes.add(PassFactory.create())
            items.append(_resort_item(resort))
        assert self._page_queries("resorts", items[:2]) == self._page_queries(
            "resorts", items
        )

    def test_weather(self) -> None:
        """Existing days at many locations: as cheap for 12 as for 2."""
        rows = [WeatherFactory.create() for _ in range(12)]
        items = [feeds.serialise_weather(row) for row in rows]
        assert self._page_queries("weather", items[:2]) == self._page_queries(
            "weather", items
        )

    def test_a_region_moved_between_two_items_on_one_page(self) -> None:
        """B claims A's region before A is read: A must not then drop it."""
        a, b = LocationFactory.create(), LocationFactory.create()
        region = MicroRegionFactory.create(centroid_location=a)
        item_a = feeds.serialise_location(a) | {"centroid_of": []}
        item_b = feeds.serialise_location(b) | {"centroid_of": [region.region_id]}
        lookups = lookups_for("locations", [item_b, item_a])
        assert apply_location(item_b, commit=True, lookups=lookups) is Outcome.UPDATED
        assert apply_location(item_a, commit=True, lookups=lookups) is Outcome.UNCHANGED
        region.refresh_from_db()
        assert region.centroid_location == b


@pytest.mark.django_db
class TestPageLookupsAreBounded:
    """Codex review on SNOW-1199: what a page's lookups may and may not load."""

    def test_weather_loads_only_the_pages_own_pairs(self) -> None:
        """Two locations by two days: a page asking for two pairs gets two rows."""
        a, b = LocationFactory.create(), LocationFactory.create()
        one, two = dt.date(2026, 10, 1), dt.date(2026, 10, 2)
        rows = {
            (loc, day): WeatherFactory.create(location=loc, observed_on=day)
            for loc in (a, b)
            for day in (one, two)
        }
        page = [
            feeds.serialise_weather(rows[(a, one)]),
            feeds.serialise_weather(rows[(b, two)]),
        ]
        assert set(lookups_for("weather", page).weather) == {
            (a.pk, one),
            (b.pk, two),
        }

    def test_a_malformed_uuid_fails_only_its_own_item(self) -> None:
        """Reading the page does not raise; the bad item fails when applied."""
        good = feeds.serialise_weather(WeatherFactory.create())
        bad = good | {"location": "not-a-uuid"}
        lookups = lookups_for("weather", [bad, good])
        assert apply_weather(good, commit=False, lookups=lookups) is Outcome.UNCHANGED
        with pytest.raises(ApplyError, match="pull locations first"):
            apply_weather(bad, commit=False, lookups=lookups)


@pytest.mark.django_db
def test_an_invalid_location_uuid_is_refused_before_it_is_planned() -> None:
    """A dry run fails it as a commit would, and plans nothing (Codex review)."""
    item = _location_item() | {"uuid": "not-a-uuid"}
    planned: set[str] = set()
    with pytest.raises(ApplyError, match="not a uuid"):
        apply_location(
            item,
            commit=False,
            lookups=lookups_for("locations", [item], planned=planned),
        )
    assert planned == set()
