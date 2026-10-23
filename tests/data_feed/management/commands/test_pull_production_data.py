"""
tests/data_feed/management/commands/test_pull_production_data.py — pull_production_data (SNOW-1197).

The two tests that carry the weight run the real thing end to end. The
first points the command at this test server: the real token endpoint
issues a client-credentials token, the real feed answers, and — because
client and server share one database — every item must come back
UNCHANGED, which is the idempotency promise checked against the live path.
The second captures the feed, empties the tables, and replays the capture
into the empty database: every row comes back, and a second run changes
nothing. A dry run of that replay must fail nothing either (SNOW-1198).
The rest cover --type, the watermark, the refusals and the exit code.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from io import StringIO
from typing import Any
from urllib.parse import urlsplit

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.test.utils import override_settings
from django.utils import timezone
from oauth2_provider.models import Application

from apps.bulletins.models import Bulletin, RegionBulletin
from apps.bulletins.services.slf_fetcher import upsert_bulletin
from apps.core.models import CommandRun
from apps.data_feed.management.commands import pull_production_data as command
from apps.locations.models import Location, ResortLocation
from apps.regions.models import MicroRegion, Pass, Resort
from apps.weather.models import Weather
from tests.factories import (
    CommandRunFactory,
    LocationFactory,
    MicroRegionFactory,
    OAuthApplicationFactory,
    PassFactory,
    PipelineRunFactory,
    ResortFactory,
    ResortLocationFactory,
    WeatherFactory,
)

FEED = "http://localhost/api/data/v1/"
SECRET = "service-secret-for-tests"
FEED_SETTINGS = {
    "DATA_FEED_URL": FEED,
    "DATA_FEED_CLIENT_ID": "staging-client",
    "DATA_FEED_CLIENT_SECRET": SECRET,
    "SITE_BASE_URL": "https://staging.example",
}


class _DjangoResponse:
    """A test-client response with the ``requests`` surface the client reads."""

    def __init__(self, response: Any) -> None:
        """Wrap ``response``."""
        self.status_code = response.status_code
        self.text = response.content.decode()

    def json(self) -> Any:
        """Decode the body."""
        return json.loads(self.text)


class _LiveSession:
    """A ``requests.Session`` stand-in that sends every call to this server."""

    def __init__(self) -> None:
        """Use a fresh test client."""
        self.client = Client(HTTP_HOST="localhost")

    def post(self, url: str, *, data: dict, auth: tuple[str, str], **_: Any) -> Any:
        """Send the token request with HTTP Basic client authentication."""
        basic = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        return _DjangoResponse(
            self.client.post(
                urlsplit(url).path, data, headers={"Authorization": f"Basic {basic}"}
            )
        )

    def get(self, url: str, *, params: dict, headers: dict, **_: Any) -> Any:
        """Send a feed request."""
        return _DjangoResponse(
            self.client.get(urlsplit(url).path, params, headers=headers)
        )


class _ReplaySession:
    """Answers feed requests from a capture taken earlier, by path and cursor."""

    def __init__(self, capture: dict[tuple[str, str | None], dict]) -> None:
        """Hold the captured pages."""
        self.capture = capture

    def post(self, url: str, **_: Any) -> Any:
        """Issue a token without asking anyone."""
        return type(
            "R", (), {"status_code": 200, "json": lambda s: {"access_token": "t"}}
        )()

    def get(self, url: str, *, params: dict, **_: Any) -> Any:
        """Serve the captured page for this path and cursor."""
        body = self.capture[(urlsplit(url).path, params.get("cursor"))]
        return type("R", (), {"status_code": 200, "text": "", "json": lambda s: body})()


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
    """Production-shaped data: a resort with links and a pass, a centroid, a bulletin, weather."""
    region = MicroRegionFactory.create(region_id="CH-4115")
    resort = ResortFactory.create(name="Verbier", geocoded=True, region=region)
    resort.passes.add(PassFactory.create(slug="magic-pass", name="Magic Pass"))
    link = ResortLocationFactory.create(
        resort=resort, role=ResortLocation.ROLE.BASE, is_primary=True
    )
    centroid = LocationFactory.create(name="", kind="")
    MicroRegion.objects.filter(pk=region.pk).update(centroid_location=centroid)
    upsert_bulletin(
        {
            "bulletinID": "b-1",
            "publicationTime": "2026-10-20T15:00:00Z",
            "validTime": {
                "startTime": "2026-10-20T15:00:00Z",
                "endTime": "2026-10-21T15:00:00Z",
            },
            "lang": "en",
            "regions": [{"regionID": "CH-4115", "name": "Piz Buin"}],
            "dangerRatings": [],
            "avalancheProblems": [],
            "customData": {"CH": {}},
        },
        PipelineRunFactory.create(),
    )
    WeatherFactory.create(location=link.location)
    WeatherFactory.create(
        location=centroid, observed_on=timezone.localdate() - dt.timedelta(days=1)
    )
    return {"resort": resort, "link": link, "centroid": centroid}


def _run(*args: str) -> str:
    """Run the command and return its stdout."""
    out = StringIO()
    call_command("pull_production_data", *args, stdout=out)
    return out.getvalue()


def _counts() -> dict[str, int]:
    """The counts of the most recent recorded run."""
    run = CommandRun.objects.for_command("pull_production_data").latest("started_at")
    counts: dict[str, int] = run.counts
    return counts


@pytest.mark.django_db
@override_settings(**FEED_SETTINGS)
def test_live_pull_against_itself_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    service_client: Application,
    estate: dict[str, Any],
) -> None:
    """The real token endpoint and feed, one database: every item UNCHANGED."""
    monkeypatch.setattr("apps.data_feed.client.requests.Session", _LiveSession)
    output = _run("--commit")
    counts = _counts()
    assert counts["locations_unchanged"] == 3  # the pin, the link, the centroid
    assert counts["resorts_unchanged"] == 1
    assert counts["bulletins_unchanged"] == 1
    assert counts["weather_unchanged"] == 2
    assert counts["failed"] == 0
    assert (
        sum(v for k, v in counts.items() if k.endswith(("_created", "_updated"))) == 0
    )
    assert "Verbier".lower() in output  # the countdown names each resort


@pytest.mark.django_db
@override_settings(**FEED_SETTINGS)
def test_replay_into_an_empty_database_restores_everything(
    monkeypatch: pytest.MonkeyPatch,
    service_client: Application,
    estate: dict[str, Any],
) -> None:
    """Capture the feed, empty the tables, pull: every row is back, then idempotent."""
    capture: dict[tuple[str, str | None], dict] = {}
    live = _LiveSession()
    token = live.client.post(
        "/oauth/token/",
        {
            "grant_type": "client_credentials",
            "client_id": "staging-client",
            "client_secret": SECRET,
            "scope": "data",
            "resource": FEED,
        },
    ).json()["access_token"]
    for feed in ("locations", "resorts", "bulletins", "weather"):
        path = f"/api/data/v1/{feed}/"
        body = live.client.get(
            path, {"limit": "500"}, headers={"Authorization": f"Bearer {token}"}
        ).json()
        assert body["next_cursor"] is None
        capture[(path, None)] = body

    resort_slug = estate["resort"].slug
    centroid_uuid = estate["centroid"].uuid
    Weather.objects.all().delete()
    RegionBulletin.objects.all().delete()
    Bulletin.objects.all().delete()
    Resort.objects.all().delete()
    ResortLocation.objects.all().delete()
    MicroRegion.objects.update(centroid_location=None)
    Location.objects.all().delete()
    Pass.objects.all().delete()

    monkeypatch.setattr(
        "apps.data_feed.client.requests.Session", lambda: _ReplaySession(capture)
    )
    # SNOW-1198: the dry run first. Its locations are only planned, yet the
    # weather and the resort that hang off them must not fail.
    _run("--since", "2026-01-01")
    counts = _counts()
    assert counts["failed"] == 0
    assert counts["locations_created"] == 3
    assert counts["resorts_created"] == 1
    assert counts["weather_created"] == 2
    assert not Location.objects.exists()

    _run("--commit", "--since", "2026-01-01")
    counts = _counts()
    assert counts["locations_created"] == 3  # the pin, the link, the centroid
    assert counts["resorts_created"] == 1
    assert counts["bulletins_created"] == 1
    assert counts["weather_created"] == 2

    resort = Resort.objects.get(slug=resort_slug)
    assert resort.location is not None
    assert resort.resort_locations.get().is_primary
    assert list(resort.passes.values_list("slug", flat=True)) == ["magic-pass"]
    centroid = MicroRegion.objects.get(region_id="CH-4115").centroid_location
    assert centroid is not None
    assert centroid.uuid == centroid_uuid
    assert RegionBulletin.objects.filter(bulletin__bulletin_id="b-1").count() == 1
    assert Weather.objects.count() == 2

    _run("--commit", "--since", "2026-01-01")
    counts = _counts()
    assert sum(v for k, v in counts.items() if k.endswith("_unchanged")) == 7
    assert (
        sum(v for k, v in counts.items() if k.endswith(("_created", "_updated"))) == 0
    )


@pytest.mark.django_db
@override_settings(**FEED_SETTINGS)
def test_type_picks_the_feeds_and_a_dry_run_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--type weather reads only the weather feed; no --commit, no rows."""
    capture: dict[tuple[str, str | None], dict] = {
        ("/api/data/v1/weather/", None): {
            "results": [
                {
                    "location": str(LocationFactory.create().uuid),
                    "observed_on": "2026-10-01",
                    "updated_at": "2026-10-01T06:00:00+00:00",
                    "fields": {"weather_code": 3},
                }
            ],
            "next_cursor": None,
            "count": 1,
        }
    }
    monkeypatch.setattr(
        "apps.data_feed.client.requests.Session", lambda: _ReplaySession(capture)
    )
    output = _run("--type", "weather")
    assert "[READ-ONLY]" in output
    assert "1 weather item(s) remaining 2026-10-01 at" in output
    assert _counts()["weather_created"] == 1
    assert "bulletins_created" not in _counts()
    assert not Weather.objects.exists()


@pytest.mark.django_db
@override_settings(**FEED_SETTINGS)
def test_a_failed_item_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """One bad item is counted, the rest applied, and the exit is non-zero."""
    capture: dict[tuple[str, str | None], dict] = {
        ("/api/data/v1/weather/", None): {
            "results": [
                {
                    "location": "00000000-0000-0000-0000-000000000000",
                    "observed_on": "2026-10-01",
                    "fields": {},
                }
            ],
            "next_cursor": None,
            "count": 1,
        }
    }
    monkeypatch.setattr(
        "apps.data_feed.client.requests.Session", lambda: _ReplaySession(capture)
    )
    with pytest.raises(CommandError, match="1 failed item"):
        _run("--type", "weather", "--commit")
    run = CommandRun.objects.for_command("pull_production_data").get()
    assert run.status == CommandRun.STATUS.PARTIAL
    assert run.counts["weather_failed"] == 1


@pytest.mark.django_db
@override_settings(**FEED_SETTINGS)
def test_an_unreadable_feed_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused token is a CommandError naming the feed."""

    class _Refused(_ReplaySession):
        def post(self, url: str, **_: Any) -> Any:
            return type(
                "R",
                (),
                {"status_code": 401, "text": "invalid_client", "json": lambda s: {}},
            )()

    monkeypatch.setattr("apps.data_feed.client.requests.Session", lambda: _Refused({}))
    with pytest.raises(CommandError, match="bulletins: token request refused"):
        _run("--type", "bulletins", "--commit")


@pytest.mark.django_db
class TestRefusals:
    """Misconfiguration stops the command before any request."""

    @override_settings(
        DATA_FEED_URL="", DATA_FEED_CLIENT_ID="", DATA_FEED_CLIENT_SECRET=""
    )
    def test_unset_settings(self) -> None:
        """All three settings are named when missing."""
        with pytest.raises(CommandError, match="DATA_FEED_URL, DATA_FEED_CLIENT_ID"):
            _run()

    @override_settings(
        **FEED_SETTINGS | {"DATA_FEED_URL": "http://prod.example/api/data/v1/"}
    )
    def test_plain_http(self) -> None:
        """A remote plain-HTTP URL is refused."""
        with pytest.raises(CommandError, match="not HTTPS"):
            _run()

    @override_settings(
        **FEED_SETTINGS | {"DATA_FEED_URL": "https://staging.example/api/data/v1/"}
    )
    def test_own_feed(self) -> None:
        """A site cannot pull from itself."""
        with pytest.raises(CommandError, match="points at this site"):
            _run()


@pytest.mark.django_db
class TestWatermark:
    """Where a pull resumes when --since is not given."""

    NOW = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.UTC)

    def test_first_pull_goes_back_seven_days(self) -> None:
        """No earlier run: the last seven days."""
        since, reason = command.watermark("bulletins", now=self.NOW)
        assert since == self.NOW - dt.timedelta(days=7)
        assert "first pull" in reason

    def test_resumes_from_the_last_successful_commit_of_that_type(self) -> None:
        """The last committed, succeeded run that pulled the type, less an hour."""
        started = dt.datetime(2026, 10, 22, 7, 0, tzinfo=dt.UTC)
        CommandRunFactory.create(
            command="pull_production_data",
            arguments={"types": ["bulletins"], "commit": True},
            started_at=started,
        )
        CommandRunFactory.create(  # later, but another type
            command="pull_production_data",
            arguments={"types": ["weather"], "commit": True},
            started_at=started + dt.timedelta(hours=3),
        )
        CommandRunFactory.create(  # later, but failed
            command="pull_production_data",
            arguments={"types": None, "commit": True},
            status=CommandRun.STATUS.PARTIAL,
            started_at=started + dt.timedelta(hours=4),
        )
        since, _ = command.watermark("bulletins", now=self.NOW)
        assert since == started - dt.timedelta(hours=1)

    def test_a_run_naming_no_type_pulled_them_all(self) -> None:
        """types=None means all three."""
        started = dt.datetime(2026, 10, 22, 7, 0, tzinfo=dt.UTC)
        CommandRunFactory.create(
            command="pull_production_data",
            arguments={"types": None, "commit": True},
            started_at=started,
        )
        assert command.watermark("weather", now=self.NOW)[0] == started - dt.timedelta(
            hours=1
        )


class TestSinceArg:
    """--since parsing."""

    def test_a_date_is_midnight_utc(self) -> None:
        """A bare date starts at 00:00 UTC."""
        assert command.since_arg("2026-10-01") == dt.datetime(
            2026, 10, 1, tzinfo=dt.UTC
        )

    def test_an_aware_datetime_is_kept(self) -> None:
        """An offset datetime is used as given."""
        assert command.since_arg("2026-10-01T06:00:00+02:00").hour == 6

    @pytest.mark.parametrize("raw", ["last week", "2026-10-01T06:00:00"])
    def test_anything_else_is_refused(self, raw: str) -> None:
        """Not a date, or a datetime with no offset."""
        with pytest.raises(Exception, match=raw):
            command.since_arg(raw)
