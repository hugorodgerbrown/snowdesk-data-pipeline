"""
tests/mcp_server/test_resort_tools.py — Tests for apps.mcp_server.resort_tools.

The ops MCP server's resort tools (SNOW-1401): ``get_resort`` and
``find_resorts`` read; ``update_resort`` and ``create_resort`` write — a
preview that writes nothing, then a commit that is validated by the model,
refused on a stale preview, and audited with its source. Every tool is
called through the JSON-RPC dispatcher, the way the endpoint reaches it.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry

from apps.mcp_server import ops_tools, protocol, resort_tools
from apps.regions.models import Resort
from tests.factories import (
    MajorRegionFactory,
    MicroRegionFactory,
    ResortFactory,
    SubRegionFactory,
    UserFactory,
)

OPS = protocol.ToolServer(name="snowdesk-ops", tools=ops_tools.OPS_TOOLS, resources={})

WRITE_TOOLS = ("update_resort", "create_resort")


def _call(name: str, arguments: dict[str, Any], actor: Any = None) -> dict[str, Any]:
    """Call an ops tool through the dispatcher, with ``actor`` bound.

    Args:
        name: The tool name.
        arguments: The tool's arguments.
        actor: The user bound to the server, as the view binds it.

    Returns:
        The ``CallToolResult``.

    """
    response = protocol.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        OPS._replace(actor=actor),
    )
    assert response is not None
    result: dict[str, Any] = response["result"]
    return result


def _ok(result: dict[str, Any]) -> dict[str, Any]:
    """Return a successful result's structured content."""
    assert result["isError"] is False, result["content"][0]["text"]
    content: dict[str, Any] = result["structuredContent"]
    return content


def _error(result: dict[str, Any]) -> str:
    """Return a failed result's message."""
    assert result["isError"] is True
    text: str = result["content"][0]["text"]
    return text


def _region_with_square_boundary(region_id: str = "CH-4115") -> Any:
    """Create a region whose polygon covers (46.0–46.5 N, 7.0–7.5 E)."""
    return MicroRegionFactory.create(
        region_id=region_id,
        boundary={
            "type": "Polygon",
            "coordinates": [
                [[7.0, 46.0], [7.5, 46.0], [7.5, 46.5], [7.0, 46.5], [7.0, 46.0]],
            ],
        },
    )


@pytest.fixture
def staff() -> Any:
    """Return a staff user, as the ops endpoint requires."""
    return UserFactory.create(is_staff=True)


def test_registered_in_the_ops_catalogue() -> None:
    """The four resort tools are ops tools, and only ops tools."""
    assert set(resort_tools.RESORT_TOOLS) <= set(ops_tools.OPS_TOOLS)
    assert set(resort_tools.RESORT_TOOLS) == {
        "get_resort",
        "find_resorts",
        "update_resort",
        "create_resort",
    }


def test_write_tools_are_annotated_as_writes() -> None:
    """tools/list marks the two writes as writing and the readers as not."""
    response = protocol.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, OPS
    )
    assert response is not None
    listed = {tool["name"]: tool for tool in response["result"]["tools"]}
    for name in WRITE_TOOLS:
        assert listed[name]["annotations"]["readOnlyHint"] is False
    assert "annotations" not in listed["get_resort"]
    assert "annotations" not in listed["find_resorts"]


def test_update_description_carries_the_sentence_rule() -> None:
    """The model's editorial rule for why_it_matters reaches the client."""
    rule = str(Resort._meta.get_field("why_it_matters").help_text)
    assert rule in resort_tools.RESORT_TOOLS["update_resort"].description


@pytest.mark.django_db
@pytest.mark.parametrize("name", WRITE_TOOLS)
def test_writes_are_refused_without_an_actor(name: str) -> None:
    """Nothing unattributed is ever changed."""
    text = _error(_call(name, {"ref": "x", "commit": False}))
    assert "signed-in account" in text


@pytest.mark.django_db
class TestGetResort:
    """get_resort: one resort in full, by uuid, slug or name."""

    def test_by_slug_with_every_field(self) -> None:
        """A slug finds the row; every editable field is returned."""
        resort = ResortFactory.create(name="Verbier", num_lifts=33, geocoded=True)
        result = _ok(_call("get_resort", {"ref": resort.slug}))
        assert result["uuid"] == str(resort.uuid)
        assert result["fields"]["num_lifts"] == 33
        assert set(result["fields"]) == set(resort_tools.RESORT_EDIT_FIELDS)
        assert result["latitude"] == pytest.approx(46.1)
        assert "why_it_matters" in result["empty_fields"]
        assert result["updated_at"] == resort.updated_at.isoformat()

    @pytest.mark.parametrize("attribute", ["uuid", "source_uuid"])
    def test_by_uuid(self, attribute: str) -> None:
        """The row's own uuid and its service uuid both find it."""
        resort = ResortFactory.create(
            source_uuid="6f1c1e64-7c3e-4b44-9b0c-1c4d3d6c0a11"
        )
        result = _ok(_call("get_resort", {"ref": str(getattr(resort, attribute))}))
        assert result["slug"] == resort.slug

    def test_unknown_uuid(self) -> None:
        """A uuid nothing carries is an error, not a name search."""
        text = _error(
            _call("get_resort", {"ref": "6f1c1e64-7c3e-4b44-9b0c-000000000000"})
        )
        assert "No resort has uuid" in text

    def test_by_name_and_name_alt_ignoring_case(self) -> None:
        """An exact name or alternative name, in any case, finds the row."""
        resort = ResortFactory.create(
            name="Crans Montana", name_alt="Crans Montana Aminona"
        )
        assert _ok(_call("get_resort", {"ref": "crans montana"}))["slug"] == resort.slug
        found = _ok(_call("get_resort", {"ref": "CRANS MONTANA AMINONA"}))
        assert found["slug"] == resort.slug

    def test_by_fuzzy_name(self) -> None:
        """A misspelled name falls through to the fuzzy search."""
        resort = ResortFactory.create(name="Zermatt")
        assert _ok(_call("get_resort", {"ref": "Zermat"}))["slug"] == resort.slug

    def test_ambiguous_name_lists_candidates(self) -> None:
        """Two resorts of one name are listed, never guessed between."""
        first = ResortFactory.create(name="Saas Grund")
        second = ResortFactory.create(name="Saas Grund")
        text = _error(_call("get_resort", {"ref": "Saas Grund"}))
        assert str(first.slug) in text
        assert str(second.slug) in text

    def test_fuzzy_name_ambiguous_within_one_region(self) -> None:
        """Two similar names in one region are listed, never collapsed into one."""
        region = MicroRegionFactory.create()
        north = ResortFactory.create(name="Alpha North", region=region)
        south = ResortFactory.create(name="Alpha South", region=region)
        text = _error(_call("get_resort", {"ref": "Alpha"}))
        assert str(north.slug) in text
        assert str(south.slug) in text

    def test_no_match(self) -> None:
        """A name matching nothing says so."""
        assert "No resort matches" in _error(_call("get_resort", {"ref": "Qqqqq"}))

    def test_history_shows_the_latest_changes(self, staff: Any) -> None:
        """A committed edit appears in the resort's history."""
        resort = ResortFactory.create(name="Saas-Fee")
        resort_tools.update_resort(
            str(resort.slug),
            {"num_lifts": 22},
            "Atlas p.4",
            True,
            resort.updated_at,
            staff,
        )
        history = _ok(_call("get_resort", {"ref": resort.slug}))["history"]
        assert len(history) == 1
        assert "Atlas p.4" in history[0]["change"]
        assert history[0]["by"] == staff.get_username()


@pytest.mark.django_db
class TestFindResorts:
    """find_resorts: a worklist, filtered, prominent resorts first."""

    def test_orders_core_first_then_by_name(self) -> None:
        """CORE leads, then STANDARD, then MINOR, each by name."""
        ResortFactory.create(name="Alpha", tier=Resort.Tier.MINOR)
        ResortFactory.create(name="Bravo", tier=Resort.Tier.STANDARD)
        ResortFactory.create(name="Charlie", tier=Resort.Tier.CORE)
        ResortFactory.create(name="Able", tier=Resort.Tier.STANDARD)
        names = [row["name"] for row in _ok(_call("find_resorts", {}))["resorts"]]
        assert names == ["Charlie", "Able", "Bravo", "Alpha"]

    def test_missing_text_field(self) -> None:
        """'missing' finds the blank sentences and returns the field."""
        ResortFactory.create(name="Filled", why_it_matters="Steep and high.")
        ResortFactory.create(name="Blank")
        result = _ok(_call("find_resorts", {"missing": "why_it_matters"}))
        assert [row["name"] for row in result["resorts"]] == ["Blank"]
        assert result["resorts"][0]["why_it_matters"] == ""

    def test_missing_number_field(self) -> None:
        """'missing' finds a null count."""
        ResortFactory.create(name="Counted", num_runs=40)
        ResortFactory.create(name="Uncounted")
        result = _ok(_call("find_resorts", {"missing": "num_runs"}))
        assert [row["name"] for row in result["resorts"]] == ["Uncounted"]

    def test_region_country_kind_and_review_filters(self) -> None:
        """Each filter narrows the list, and they combine."""
        major = MajorRegionFactory.create(prefix="FR-01", country="FR")
        french = MicroRegionFactory.create(
            region_id="FR-01",
            subregion=SubRegionFactory.create(prefix="FR-01", major=major),
        )
        ResortFactory.create(name="Chamonix", region=french, needs_review=True)
        ResortFactory.create(
            name="Grimsel", kind=Resort.Kind.TOURING_TERRAIN, needs_review=True
        )
        ResortFactory.create(name="Arosa")
        assert _ok(_call("find_resorts", {"region_id": "FR-01"}))["total"] == 1
        assert _ok(_call("find_resorts", {"country": "fr"}))["total"] == 1
        touring = _ok(_call("find_resorts", {"kind": "TOURING_TERRAIN"}))
        assert [row["name"] for row in touring["resorts"]] == ["Grimsel"]
        both = _ok(_call("find_resorts", {"needs_review": True, "country": "CH"}))
        assert [row["name"] for row in both["resorts"]] == ["Grimsel"]

    def test_limit_keeps_the_total(self) -> None:
        """The limit caps the rows, not the count."""
        for index in range(3):
            ResortFactory.create(name=f"Resort {index}")
        result = _ok(_call("find_resorts", {"limit": 2}))
        assert result["total"] == 3
        assert len(result["resorts"]) == 2

    @pytest.mark.parametrize(
        "arguments",
        [
            {"region_id": "CH-9999"},
            {"kind": "VOLCANO"},
            {"missing": "slug"},
            {"missing": "needs_review"},
            {"needs_review": "yes"},
            {"limit": 0},
        ],
    )
    def test_bad_filters_are_errors(self, arguments: dict[str, Any]) -> None:
        """A filter the estate has no value for is refused."""
        _error(_call("find_resorts", arguments))


@pytest.mark.django_db
class TestUpdateResort:
    """update_resort: preview, then a commit against the preview."""

    @pytest.fixture
    def resort(self) -> Resort:
        """Return a resort with some fields filled."""
        return ResortFactory.create(
            name="Verbier", num_lifts=30, base_elevation_m=820, top_elevation_m=3300
        )

    def _commit(
        self, resort: Resort, changes: dict[str, Any], actor: Any
    ) -> dict[str, Any]:
        """Preview ``changes`` and commit them against that preview."""
        preview = _ok(
            _call(
                "update_resort",
                {
                    "ref": resort.slug,
                    "changes": changes,
                    "source": "Atlas p.112",
                    "commit": False,
                },
                actor,
            )
        )
        return _call(
            "update_resort",
            {
                "ref": resort.slug,
                "changes": changes,
                "source": "Atlas p.112",
                "commit": True,
                "expected_updated_at": preview["updated_at"],
            },
            actor,
        )

    def test_preview_writes_nothing(self, resort: Resort, staff: Any) -> None:
        """A preview returns the diff and changes no row and no log."""
        result = _ok(
            _call(
                "update_resort",
                {
                    "ref": resort.slug,
                    "changes": {"num_lifts": 33, "why_it_matters": "Big, steep, high."},
                    "source": "Atlas p.112",
                    "commit": False,
                },
                staff,
            )
        )
        assert result["committed"] is False
        assert result["diff"]["num_lifts"] == {"from": 30, "to": 33}
        assert result["why_it_matters_check"] == {
            "length": len("Big, steep, high."),
            "max_length": resort_tools.WHY_IT_MATTERS_MAX,
        }
        resort.refresh_from_db()
        assert resort.num_lifts == 30
        assert not LogEntry.objects.exists()

    def test_commit_writes_and_audits(self, resort: Resort, staff: Any) -> None:
        """A commit writes the fields and one CHANGE entry naming each and the source."""
        result = _ok(
            self._commit(
                resort, {"num_lifts": 33, "operator_name": "Téléverbier"}, staff
            )
        )
        assert result["committed"] is True
        resort.refresh_from_db()
        assert resort.num_lifts == 33
        assert resort.operator_name == "Téléverbier"
        entry = LogEntry.objects.get()
        assert entry.action_flag == CHANGE
        assert entry.user == staff
        assert entry.object_id == str(resort.pk)
        message = entry.get_change_message()
        assert "num_lifts: 30 → 33" in message
        assert "operator_name" in message
        assert "Source: Atlas p.112" in message

    def test_a_change_since_the_preview_is_refused(
        self, resort: Resort, staff: Any
    ) -> None:
        """A row saved after the preview is not overwritten."""
        preview = _ok(
            _call(
                "update_resort",
                {
                    "ref": resort.slug,
                    "changes": {"num_lifts": 33},
                    "source": "x",
                    "commit": False,
                },
                staff,
            )
        )
        resort.notes = "edited in the admin"
        resort.save()
        text = _error(
            _call(
                "update_resort",
                {
                    "ref": resort.slug,
                    "changes": {"num_lifts": 33},
                    "source": "x",
                    "commit": True,
                    "expected_updated_at": preview["updated_at"],
                },
                staff,
            )
        )
        assert "Preview again" in text
        resort.refresh_from_db()
        assert resort.num_lifts == 30

    def test_no_change_writes_nothing(self, resort: Resort, staff: Any) -> None:
        """Committing the values already held writes no row and no log."""
        before = resort.updated_at
        result = _ok(self._commit(resort, {"num_lifts": 30}, staff))
        assert result["committed"] is False
        resort.refresh_from_db()
        assert resort.updated_at == before
        assert not LogEntry.objects.exists()

    def test_null_clears_a_field(self, resort: Resort, staff: Any) -> None:
        """A null value clears the field."""
        _ok(self._commit(resort, {"num_lifts": None}, staff))
        resort.refresh_from_db()
        assert resort.num_lifts is None

    @pytest.mark.parametrize(
        ("changes", "named"),
        [
            ({"top_elevation_m": 500}, "top_elevation_m"),
            ({"typical_season_open": "December"}, "typical_season_open"),
            ({"website": "not a url"}, "website"),
            ({"num_lifts": -1}, "num_lifts"),
            ({"total_piste_km": -3.0}, "total_piste_km"),
            ({"why_it_matters": "x" * 256}, "why_it_matters"),
        ],
    )
    def test_invalid_values_are_refused(
        self, resort: Resort, staff: Any, changes: dict[str, Any], named: str
    ) -> None:
        """The model's own constraints refuse a bad value, naming the field."""
        text = _error(
            _call(
                "update_resort",
                {
                    "ref": resort.slug,
                    "changes": changes,
                    "source": "x",
                    "commit": False,
                },
                staff,
            )
        )
        assert named in text

    @pytest.mark.parametrize(
        "field", ["slug", "region", "canton", "latitude", "kind", "passes"]
    )
    def test_fields_outside_the_list_are_refused(
        self, resort: Resort, staff: Any, field: str
    ) -> None:
        """Only the editorial fields can be written here."""
        text = _error(
            _call(
                "update_resort",
                {
                    "ref": resort.slug,
                    "changes": {field: "x"},
                    "source": "x",
                    "commit": False,
                },
                staff,
            )
        )
        assert "Not editable here" in text

    @pytest.mark.parametrize(
        "arguments",
        [
            {"changes": {"num_lifts": 1}, "source": "x"},
            {"changes": {"num_lifts": 1}, "commit": False},
            {"changes": {}, "source": "x", "commit": False},
            {"changes": {"num_lifts": 1}, "source": "x", "commit": True},
            {
                "changes": {"num_lifts": 1},
                "source": "x",
                "commit": True,
                "expected_updated_at": "2026-11-03T10:00:00",
            },
            {"changes": {"num_lifts": 1}, "source": "x" * 501, "commit": False},
        ],
    )
    def test_required_arguments(
        self, resort: Resort, staff: Any, arguments: dict[str, Any]
    ) -> None:
        """commit, source, a non-empty change and an aware updated_at are required."""
        _error(_call("update_resort", {"ref": resort.slug, **arguments}, staff))
        resort.refresh_from_db()
        assert resort.num_lifts == 30


@pytest.mark.django_db
class TestCreateResort:
    """create_resort: a resort at a coordinate, region from the point."""

    def _arguments(self, **overrides: Any) -> dict[str, Any]:
        """Return a create's arguments inside the square region."""
        return {
            "name": "Ovronnaz",
            "latitude": 46.2,
            "longitude": 7.2,
            "source": "Guide p.9",
            "commit": False,
            **overrides,
        }

    def test_preview_writes_nothing(self, staff: Any) -> None:
        """A preview names the derived region and creates nothing."""
        region = _region_with_square_boundary()
        result = _ok(
            _call(
                "create_resort",
                self._arguments(canton="VS", fields={"num_lifts": 9}),
                staff,
            )
        )
        assert result["committed"] is False
        assert result["region_id"] == region.region_id
        assert result["fields"]["num_lifts"] == 9
        assert not Resort.objects.exists()
        assert not LogEntry.objects.exists()

    def test_commit_creates_pins_and_audits(self, staff: Any) -> None:
        """A commit creates the row, writes its pin, stamps it and logs the source."""
        region = _region_with_square_boundary()
        result = _ok(
            _call(
                "create_resort",
                self._arguments(canton="vs", commit=True, fields={"num_lifts": 9}),
                staff,
            )
        )
        resort = Resort.objects.get()
        assert result["slug"] == resort.slug == "ovronnaz"
        assert resort.region == region
        assert resort.canton == "VS"
        assert resort.num_lifts == 9
        assert resort.geocode_source == Resort.GeocodeSource.IMPORT
        assert resort.needs_review is True
        assert resort.location is not None
        assert resort.pin_coordinates == (46.2, 7.2)
        entry = LogEntry.objects.get()
        assert entry.action_flag == ADDITION
        assert "Source: Guide p.9" in entry.get_change_message()

    def test_canton_is_inherited_from_the_region(self, staff: Any) -> None:
        """An omitted canton comes from the region's resorts."""
        region = _region_with_square_boundary()
        ResortFactory.create(name="Leukerbad", region=region, canton="VS")
        result = _ok(_call("create_resort", self._arguments(), staff))
        assert result["canton"] == "VS"

    def test_canton_required_in_an_empty_region(self, staff: Any) -> None:
        """A region with no resorts to vote asks for the canton."""
        _region_with_square_boundary()
        assert "pass 'canton'" in _error(
            _call("create_resort", self._arguments(), staff)
        )

    def test_point_outside_every_region(self, staff: Any) -> None:
        """A coordinate no region contains is refused."""
        _region_with_square_boundary()
        text = _error(
            _call("create_resort", self._arguments(latitude=47.9, canton="VS"), staff)
        )
        assert "No avalanche region contains" in text

    def test_duplicates_by_distance_and_name(self, staff: Any) -> None:
        """A nearby pin and a same-named resort elsewhere are both listed."""
        region = _region_with_square_boundary()
        other = MicroRegionFactory.create(region_id="CH-2222")
        ResortFactory.create(
            name="Neighbour", region=region, latitude=46.205, longitude=7.2
        )
        ResortFactory.create(name="Ovronnaz", region=other)
        ResortFactory.create(name="Far", region=region, latitude=46.45, longitude=7.45)
        result = _ok(_call("create_resort", self._arguments(canton="VS"), staff))
        why = {row["name"]: row["why"] for row in result["duplicates"]}
        assert why == {"Neighbour": "nearby", "Ovronnaz": "similar name"}
        nearby = next(row for row in result["duplicates"] if row["why"] == "nearby")
        assert nearby["distance_km"] < 1

    def test_same_name_in_the_same_region_is_refused(self, staff: Any) -> None:
        """The region already holding that name is a refusal, as in the map editor."""
        region = _region_with_square_boundary()
        ResortFactory.create(name="Ovronnaz", region=region)
        text = _error(
            _call("create_resort", self._arguments(canton="VS", commit=True), staff)
        )
        assert "update that one instead" in text
        assert Resort.objects.count() == 1

    @pytest.mark.parametrize(
        "overrides",
        [
            {"latitude": "46.2"},
            {"longitude": 181},
            {"fields": {"name": "Other"}},
            {"fields": {"needs_review": False}},
            {"fields": {"slug": "x"}},
            {"fields": {"top_elevation_m": 100, "base_elevation_m": 900}},
            {"commit": None},
        ],
    )
    def test_bad_arguments(self, staff: Any, overrides: dict[str, Any]) -> None:
        """A malformed coordinate, a reserved field or a bad value creates nothing."""
        _region_with_square_boundary()
        _error(_call("create_resort", self._arguments(canton="VS", **overrides), staff))
        assert not Resort.objects.exists()


@pytest.mark.django_db
def test_update_compares_updated_at_exactly(staff: Any) -> None:
    """An updated_at a microsecond off is refused; the exact one commits."""
    resort = ResortFactory.create(name="Arolla")
    off = resort.updated_at + dt.timedelta(microseconds=1)
    with pytest.raises(resort_tools.ToolError):
        resort_tools.update_resort(
            str(resort.slug), {"num_lifts": 5}, "x", True, off, staff
        )
    result = resort_tools.update_resort(
        str(resort.slug), {"num_lifts": 5}, "x", True, resort.updated_at, staff
    )
    assert result["committed"] is True
