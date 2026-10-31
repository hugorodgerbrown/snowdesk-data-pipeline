"""
tests/data_feed/management/commands/test_pull_reference_data.py — pull_reference_data (SNOW-1272).

As for ``pull_production_data``, the weight is carried end to end. The
estate is built the way production holds it: curated rows mirrored into
the service (``mirror_reference_data``), a bulletin stored and applied,
its day rating computed by the service, and a day of weather applied
from the service's row. Then:

* pointed at this test server (one database), every item must come back
  unchanged — idempotency checked against the live feeds;
* captured, the client's tables emptied, and replayed, every row comes
  back under the service's uuids, a dry run first failing nothing, and a
  second commit changing nothing.

The rest cover the window, tombstones through the command, the refusals
and the exit code, and a window at SLF's 2026/27 per-region volume
(SNOW-998).
"""

from __future__ import annotations

import datetime as dt
import json
import math
import uuid
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test.utils import override_settings
from django.utils import timezone
from oauth2_provider.models import Application

from apps.bulletins.models import Bulletin, RegionBulletin, RegionDayRating
from apps.bulletins.services.apply import apply_bulletin
from apps.core.models import CommandRun
from apps.data_feed import contract
from apps.data_feed.management.commands.pull_reference_data import (
    describe_item,
    place_label,
)
from apps.ingest.public import (
    NormalisedBulletin,
    SourceLocation,
    SourceWeather,
    serialise_source_document,
)
from apps.locations.models import Location, ResortLocation
from apps.regions.models import MicroRegion, Pass, Resort
from apps.regions.services.reference import mirror_reference_data
from apps.routes.models import SkiSegment, SkiTour
from apps.routes.services.ski_network import apply_from_service
from apps.weather.models import Weather
from apps.weather.services.reference import apply_source_weather
from tests.data_feed.management.commands.test_pull_production_data import (
    SECRET,
    _LiveSession,
    _ReplaySession,
)
from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    OAuthApplicationFactory,
    PassFactory,
    PipelineRunFactory,
    ResortFactory,
    ResortLocationFactory,
    SkiSegmentFactory,
    SourceSkiSegmentFactory,
    SourceSkiTourFactory,
    SourceWeatherFactory,
)

FEED = "http://localhost/api/data/v1/"
FEED_V2 = "http://localhost/api/data/v2/"
FEED_SETTINGS = {
    "DATA_FEED_URL": FEED,
    "DATA_FEED_CLIENT_ID": "staging-client",
    "DATA_FEED_CLIENT_SECRET": SECRET,
    "DATA_FEED_V2_TOKEN": "",
    "SITE_BASE_URL": "https://staging.example",
}
FEEDS = (
    "locations",
    "passes",
    "resorts",
    "ski-network",
    "ski-tours",
    "bulletins",
    "day-ratings",
    "weather",
)


@pytest.fixture
def service_client(db: None) -> Application:
    """The service client the command authenticates as."""
    return OAuthApplicationFactory.create(
        client_id="staging-client",
        client_secret=SECRET,
        client_type=Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
        redirect_uris="",
    )


@pytest.fixture
def estate(db: None) -> dict[str, Any]:
    """The service's estate: mirrored places, a pass, a resort, a bulletin, weather."""
    region = MicroRegionFactory.create(region_id="CH-4115")
    resort = ResortFactory.create(name="Verbier", geocoded=True, region=region)
    resort.passes.add(PassFactory.create(slug="magic-pass", name="Magic Pass"))
    link = ResortLocationFactory.create(
        resort=resort, role=ResortLocation.ROLE.BASE, is_primary=True
    )
    centroid = LocationFactory.create(name="", kind="")
    MicroRegion.objects.filter(pk=region.pk).update(centroid_location=centroid)
    mirror_reference_data()

    today = timezone.localdate()
    start = f"{today - dt.timedelta(days=1)}T15:00:00Z"
    apply_bulletin(
        NormalisedBulletin(
            "b-1",
            "SLF",
            {
                "bulletinID": "b-1",
                "publicationTime": start,
                "validTime": {"startTime": start, "endTime": f"{today}T15:00:00Z"},
                "lang": "en",
                "regions": [{"regionID": "CH-4115", "name": "Piz Buin"}],
                "dangerRatings": [],
                "avalancheProblems": [],
                "customData": {"CH": {}},
            },
            lambda: "",
        ),
        PipelineRunFactory.create(),
    )
    link.location.refresh_from_db()
    assert link.location.source_uuid is not None
    place = SourceLocation.objects.get(uuid=link.location.source_uuid)
    apply_source_weather(SourceWeatherFactory.create(location=place))
    # SNOW-1276: a segment and a tour, imported and applied as the
    # service's host does.
    SourceSkiSegmentFactory.create()
    SourceSkiTourFactory.create()
    apply_from_service(commit=True)
    return {"resort": resort, "link": link, "centroid": centroid}


def _run(*args: str) -> str:
    """Run the command and return its stdout."""
    out = StringIO()
    call_command("pull_reference_data", *args, stdout=out)
    return out.getvalue()


def _counts() -> dict[str, int]:
    """The counts of the most recent recorded run."""
    run = CommandRun.objects.for_command("pull_reference_data").latest("started_at")
    counts: dict[str, int] = run.counts
    return counts


def _written(counts: dict[str, int]) -> int:
    """Sum every count that means a row was (or would be) written."""
    return sum(
        v
        for k, v in counts.items()
        if k.endswith(("_created", "_updated", "_deleted", "_detached"))
    )


def _capture() -> dict[tuple[str, str | None], dict]:
    """Read every v2 feed whole through the live endpoints, by path."""
    live = _LiveSession()
    token = live.client.post(
        "/oauth/token/",
        {
            "grant_type": "client_credentials",
            "client_id": "staging-client",
            "client_secret": SECRET,
            "scope": "data",
            "resource": FEED_V2,
        },
    ).json()["access_token"]
    capture: dict[tuple[str, str | None], dict] = {}
    for feed in FEEDS:
        path = f"/api/data/v2/{feed}/"
        body = live.client.get(
            path, {"limit": "500"}, headers={"Authorization": f"Bearer {token}"}
        ).json()
        assert body["next_cursor"] is None
        capture[(path, None)] = body
    return capture


@pytest.mark.django_db
@override_settings(**FEED_SETTINGS)
def test_live_pull_against_itself_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    service_client: Application,
    estate: dict[str, Any],
) -> None:
    """The real token endpoint and v2 feeds, one database: every item unchanged."""
    monkeypatch.setattr("apps.data_feed.client.requests.Session", _LiveSession)
    output = _run("--commit")
    counts = _counts()
    assert counts["locations_unchanged"] == 3  # the pin, the link, the centroid
    assert counts["passes_unchanged"] == 1
    assert counts["resorts_unchanged"] == 1
    assert counts["bulletins_unchanged"] == 1
    assert counts["day-ratings_unchanged"] == 1
    assert counts["weather_unchanged"] == 1
    assert counts["ski-network_unchanged"] == counts["ski-tours_unchanged"] == 1
    assert counts["failed"] == 0
    assert _written(counts) == 0
    assert "verbier" in output  # the countdown names each resort
    link = estate["link"].location
    assert link.name
    assert f"at {link.name}" in output  # and each day of weather by its place
    assert str(link.source_uuid) not in output
    assert "centre of CH-4115" in output  # an unnamed centre by its region


@pytest.mark.django_db
@override_settings(**FEED_SETTINGS)
def test_replay_into_an_empty_instance_restores_everything(
    monkeypatch: pytest.MonkeyPatch,
    service_client: Application,
    estate: dict[str, Any],
) -> None:
    """Capture the feeds, empty the client's tables, pull: every row is back."""
    capture = _capture()
    centroid_source = estate["centroid"].uuid
    Weather.objects.all().delete()
    RegionDayRating.objects.all().delete()
    RegionBulletin.objects.all().delete()
    Bulletin.objects.all().delete()
    Resort.objects.all().delete()
    ResortLocation.objects.all().delete()
    MicroRegion.objects.update(centroid_location=None)
    Location.objects.all().delete()
    Pass.objects.all().delete()
    SkiSegment.objects.all().delete()
    SkiTour.objects.all().delete()
    monkeypatch.setattr(
        "apps.data_feed.client.requests.Session", lambda: _ReplaySession(capture)
    )

    # A dry run: the places are only planned, yet the resort and the
    # weather that hang off them must not fail.
    assert "[READ-ONLY]" in _run()
    counts = _counts()
    assert counts["failed"] == 0
    assert counts["locations_created"] == 3
    assert counts["weather_created"] == 1
    assert counts["ski-network_created"] == counts["ski-tours_created"] == 1
    assert not Location.objects.exists()
    assert not SkiSegment.objects.exists()

    _run("--commit")
    counts = _counts()
    assert counts["failed"] == 0
    assert (counts["locations_created"], counts["passes_created"]) == (3, 1)
    assert (counts["resorts_created"], counts["bulletins_created"]) == (1, 1)
    assert counts["weather_created"] == 1
    resort = Resort.objects.get(slug=estate["resort"].slug)
    assert resort.location is not None
    assert resort.resort_locations.get().is_primary
    assert list(resort.passes.values_list("slug", flat=True)) == ["magic-pass"]
    centroid = MicroRegion.objects.get(region_id="CH-4115").centroid_location
    assert centroid is not None
    assert centroid.source_uuid == centroid_source == centroid.uuid
    assert RegionBulletin.objects.filter(bulletin__bulletin_id="b-1").count() == 1
    assert RegionDayRating.objects.filter(region__region_id="CH-4115").exists()
    assert Weather.objects.count() == 1
    assert SkiSegment.objects.count() == SkiTour.objects.count() == 1

    _run("--commit")
    assert _written(_counts()) == 0


@pytest.mark.django_db
@override_settings(**FEED_SETTINGS)
def test_a_resort_recreated_under_its_slug_replaces_the_old_one(
    monkeypatch: pytest.MonkeyPatch,
    service_client: Application,
    estate: dict[str, Any],
) -> None:
    """Codex on #1268: the old row is buried, then the new uuid takes its slug.

    The live replacement wants a slug the old row still holds, so it is
    deferred until the old tombstone is applied, not adopted onto the old
    row (which the tombstone would then delete) and not failed.
    """
    capture = _capture()
    body = capture[("/api/data/v2/resorts/", None)]
    (old,) = body["results"]
    replacement = old | {"uuid": str(uuid.uuid4())}
    body["results"] = [replacement, old | {"deleted_at": timezone.now().isoformat()}]
    monkeypatch.setattr(
        "apps.data_feed.client.requests.Session", lambda: _ReplaySession(capture)
    )
    _run("--commit")
    counts = _counts()
    assert counts["failed"] == 0
    assert (counts["resorts_created"], counts["resorts_deleted"]) == (1, 1)
    resort = Resort.objects.get(slug=estate["resort"].slug)
    assert str(resort.source_uuid) == replacement["uuid"]
    assert resort.resort_locations.get().is_primary


@pytest.mark.django_db
@override_settings(**FEED_SETTINGS)
def test_a_tombstone_detaches_a_favourited_place(
    monkeypatch: pytest.MonkeyPatch,
    service_client: Application,
    estate: dict[str, Any],
) -> None:
    """A removed place a user saved stays theirs; it leaves the estate.

    Its days, still served, are skipped rather than failing the run
    (Codex on #1268).
    """
    location = estate["link"].location
    FavouriteFactory.create(location=location)
    SourceLocation.objects.filter(uuid=location.source_uuid).update(
        deleted_at=timezone.now()
    )
    assert SourceWeather.objects.exists()
    monkeypatch.setattr("apps.data_feed.client.requests.Session", _LiveSession)
    _run("--commit")
    counts = _counts()
    assert counts["locations_detached"] == 1
    assert counts["weather_skipped"] == SourceWeather.objects.count()
    assert counts["failed"] == 0
    location.refresh_from_db()
    assert location.source_uuid is None
    assert not ResortLocation.objects.filter(location=location).exists()


class _Recording(_ReplaySession):
    """A replay that records the query parameters of every feed request."""

    seen: dict[str, dict]

    def get(self, url: str, *, params: dict, **kwargs: Any) -> Any:
        """Record ``params`` by feed, then answer an empty page."""
        self.seen[url.rstrip("/").rsplit("/", 1)[-1]] = dict(params)
        body: dict[str, Any] = {"results": [], "next_cursor": None, "count": 0}
        return type("R", (), {"status_code": 200, "text": "", "json": lambda s: body})()


@pytest.mark.django_db
@override_settings(**FEED_SETTINGS)
@pytest.mark.parametrize(
    ("args", "day_from", "day_to"),
    [
        ((), "recent:14", None),
        (("--recent", "3"), "recent:3", None),
        (("--golden-week",), "2026-02-09", "2026-02-15"),
    ],
)
def test_the_window_reaches_the_series_and_not_the_estate(
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
    day_from: str,
    day_to: str | None,
) -> None:
    """--recent and --golden-week window the time series; the estate is whole."""
    session = _Recording({})
    session.seen = {}
    monkeypatch.setattr("apps.data_feed.client.requests.Session", lambda: session)
    output = _run(*args)
    if day_from.startswith("recent:"):
        days = int(day_from.split(":")[1])
        day_from = (timezone.localdate() - dt.timedelta(days=days)).isoformat()
    for feed in ("bulletins", "day-ratings", "weather"):
        assert session.seen[feed].get("from") == day_from
        assert session.seen[feed].get("to") == day_to
    for feed in ("locations", "passes", "resorts", "ski-network", "ski-tours"):
        assert "from" not in session.seen[feed]
        assert "since" not in session.seen[feed]
    assert "/api/data/v2/" in output


@pytest.mark.django_db
@override_settings(**FEED_SETTINGS)
def test_the_ski_lines_are_pulled_since_the_newest_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SNOW-1276: incremental, from the newest line this instance holds."""
    held = SkiSegmentFactory.create()
    session = _Recording({})
    session.seen = {}
    monkeypatch.setattr("apps.data_feed.client.requests.Session", lambda: session)
    _run()
    assert session.seen["ski-network"]["since"] == held.source_updated_at.isoformat()
    assert "since" not in session.seen["ski-tours"]


@pytest.mark.django_db
@override_settings(**{**FEED_SETTINGS, "DATA_FEED_V2_TOKEN": "minted"})
def test_a_minted_v2_token_needs_no_client_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DATA_FEED_V2_TOKEN is sent as the bearer and no token is requested."""
    seen: list[str] = []

    class _Session(_Recording):
        """Records the bearer, and refuses any token request."""

        def post(self, url: str, **_: Any) -> Any:
            """Fail: a minted token must not be exchanged."""
            raise AssertionError("token requested")

        def get(self, url: str, *, params: dict, **kwargs: Any) -> Any:
            """Record the bearer."""
            seen.append(kwargs["headers"]["Authorization"])
            return super().get(url, params=params)

    session = _Session({})
    session.seen = {}
    monkeypatch.setattr("apps.data_feed.client.requests.Session", lambda: session)
    with override_settings(DATA_FEED_CLIENT_ID="", DATA_FEED_CLIENT_SECRET=""):
        _run()
    assert set(seen) == {"Bearer minted"}


@pytest.mark.django_db
def test_refusals() -> None:
    """No settings, its own host, a URL naming no version, a negative window."""
    with override_settings(DATA_FEED_URL="", DATA_FEED_V2_TOKEN=""):
        with pytest.raises(CommandError, match="DATA_FEED_URL"):
            _run()
    with override_settings(
        **{**FEED_SETTINGS, "DATA_FEED_URL": "https://staging.example/api/data/v1/"}
    ):
        with pytest.raises(CommandError, match="its own feed"):
            _run()
    with override_settings(
        **{**FEED_SETTINGS, "DATA_FEED_URL": "https://prod.example/api/"}
    ):
        with pytest.raises(CommandError, match="does not end in"):
            _run()
    with pytest.raises(CommandError, match="--recent"):
        _run("--recent", "-1")


@pytest.mark.django_db
@override_settings(**FEED_SETTINGS)
def test_a_failed_item_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """One bad item is counted, the rest applied, and the exit is non-zero."""
    empty: dict[str, Any] = {"results": [], "next_cursor": None, "count": 0}
    capture: dict[tuple[str, str | None], dict] = {
        (f"/api/data/v2/{feed}/", None): empty for feed in FEEDS
    }
    capture[("/api/data/v2/passes/", None)] = {
        "results": [{"uuid": "not-a-uuid", "slug": "x", "document": {}}],
        "next_cursor": None,
        "count": 1,
    }
    monkeypatch.setattr(
        "apps.data_feed.client.requests.Session", lambda: _ReplaySession(capture)
    )
    with pytest.raises(CommandError, match="1 failed item"):
        _run("--commit")
    counts = _counts()
    assert counts["passes_failed"] == counts["failed"] == 1
    assert counts["weather_unchanged"] == 0  # zeros are recorded too


@pytest.mark.django_db
@override_settings(**FEED_SETTINGS)
def test_a_failed_ski_page_stops_the_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing of a failed page is written and the next page is not asked for.

    The next pull starts from the newest line held, so a line written past
    a failure would carry it beyond the failed one for good.
    """
    empty: dict[str, Any] = {"results": [], "next_cursor": None, "count": 0}
    capture: dict[tuple[str, str | None], dict] = {
        (f"/api/data/v2/{feed}/", None): empty for feed in FEEDS
    }
    good = serialise_source_document(SourceSkiSegmentFactory.create())
    capture[("/api/data/v2/ski-network/", None)] = {
        "results": [good, {**good, "uuid": "not-a-uuid"}],
        "next_cursor": "page-2",  # not captured: asking for it would fail
        "count": 3,
    }
    monkeypatch.setattr(
        "apps.data_feed.client.requests.Session", lambda: _ReplaySession(capture)
    )
    with pytest.raises(CommandError, match="1 failed item"):
        _run("--commit")
    assert not SkiSegment.objects.exists()
    assert _counts()["ski-network_failed"] == 1


@pytest.mark.parametrize(
    ("item", "label"),
    [
        ({"uuid": "u-1", "fields": {"name": "Mont Fort"}}, "Mont Fort"),
        (
            {
                "uuid": "u-2",
                "fields": {"name": ""},
                "document": {"centroid_of": ["CH-2", "CH-1"]},
            },
            "centre of CH-1, CH-2",
        ),
        ({"uuid": "u-3", "fields": {"name": None}, "document": {}}, "u-3"),
        ({"uuid": "u-4", "deleted_at": "2026-10-28T00:00:00+00:00"}, "u-4"),
    ],
)
def test_place_label(item: dict[str, Any], label: str) -> None:
    """A place reads by its name, then its regions, then its uuid."""
    assert place_label(item) == label


def test_a_day_of_weather_is_described_by_its_place() -> None:
    """The weather label reads the place's label; an unknown place its uuid."""
    describe = describe_item("weather", {"u-1": "Mont Fort"})
    assert describe({"observed_on": "2026-10-28", "location": "u-1"}) == (
        "2026-10-28 at Mont Fort"
    )
    assert describe({"observed_on": "2026-10-28", "location": "u-9"}) == (
        "2026-10-28 at u-9"
    )


class _Refusing(_ReplaySession):
    """Answers like a service on an older release: one feed is not served."""

    def __init__(
        self, capture: dict[tuple[str, str | None], dict], missing: str
    ) -> None:
        """Hold the captured pages and the path that answers 404."""
        super().__init__(capture)
        self.missing = missing

    def get(self, url: str, *, params: dict, **kwargs: Any) -> Any:
        """Answer 404 for the missing feed, the capture for the rest."""
        if urlsplit(url).path == self.missing:
            return type(
                "R",
                (),
                {"status_code": 404, "text": '{"error": "not_found"}', "json": None},
            )()
        return super().get(url, params=params, **kwargs)


@pytest.mark.django_db
@override_settings(**FEED_SETTINGS)
def test_an_unread_feed_costs_only_itself(
    service_client: Application, estate: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A service that does not serve ski-network yet still sends its weather.

    Staging on ``main`` pulled from a production on an older release: the
    ski-network 404 aborted the run before the weather, so staging's days
    stopped. The feed is named and the run fails, but the rest arrive.
    """
    capture = _capture()
    Weather.objects.all().delete()
    monkeypatch.setattr(
        "apps.data_feed.client.requests.Session",
        lambda: _Refusing(capture, "/api/data/v2/ski-network/"),
    )
    with pytest.raises(CommandError, match=r"could not read ski-network \(.*404"):
        _run("--commit")
    assert Weather.objects.exists()
    counts = _counts()
    assert counts["feeds_unread"] == 1
    assert counts["weather_created"] == Weather.objects.count()
    assert counts["ski-tours_unchanged"] == 1


class _Counting(_ReplaySession):
    """A replay that counts the pages asked of each feed."""

    def __init__(self, capture: dict[tuple[str, str | None], dict]) -> None:
        """Hold the captured pages and an empty tally."""
        super().__init__(capture)
        self.requests: dict[str, int] = {}

    def get(self, url: str, *, params: dict, **kwargs: Any) -> Any:
        """Count the request against its path, then serve the capture."""
        path = urlsplit(url).path
        self.requests[path] = self.requests.get(path, 0) + 1
        return super().get(url, params=params, **kwargs)


@pytest.mark.django_db
@override_settings(**FEED_SETTINGS)
def test_a_de_aggregated_window_is_consumed_within_its_page_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--recent 14 at ~149 bulletins an issue, two a day, reads cleanly.

    4,172 real per-region items (the sentinel slice, re-identified) in
    pages of the client's MAX_LIMIT: every item is reached, none fails,
    and the walk asks for no more pages than the item count needs. A dry
    run, because the write per item is the one the fetch_bulletins slice
    test already drives; what this proves is the client at the volume.
    """
    slice_path = (
        Path(__file__).resolve().parents[3]
        / "sentinels"
        / "slf"
        / "per-region-slice.ndjson"
    )
    sample = [json.loads(line) for line in slice_path.read_text().splitlines()]
    total = 14 * 149 * 2
    items = [
        {
            "bulletin_id": f"per-region-{n:05d}",
            "updated_at": "2026-10-31T09:00:00+00:00",
            "pdf_url": "",
            "caaml": {**sample[n % len(sample)], "bulletinID": f"per-region-{n:05d}"},
            "render_model": None,
        }
        for n in range(total)
    ]
    path = "/api/data/v2/bulletins/"
    capture: dict[tuple[str, str | None], dict] = {
        (f"/api/data/v2/{feed}/", None): {
            "results": [],
            "next_cursor": None,
            "count": 0,
        }
        for feed in FEEDS
    }
    budget = math.ceil(total / contract.MAX_LIMIT)
    for page in range(budget):
        chunk = items[page * contract.MAX_LIMIT : (page + 1) * contract.MAX_LIMIT]
        body: dict[str, Any] = {
            "results": chunk,
            "next_cursor": f"p{page + 1}" if page + 1 < budget else None,
        }
        if page == 0:
            body["count"] = total
        capture[(path, f"p{page}" if page else None)] = body
    session = _Counting(capture)
    monkeypatch.setattr("apps.data_feed.client.requests.Session", lambda: session)

    _run("--recent", "14")

    counts = _counts()
    assert counts["bulletins_created"] == total
    assert counts["bulletins_failed"] == 0
    assert session.requests[path] == budget == 9
