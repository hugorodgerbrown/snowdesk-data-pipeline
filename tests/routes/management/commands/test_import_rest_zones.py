"""
tests/routes/management/commands/test_import_rest_zones.py — import_rest_zones (SNOW-1263).

The command end to end against a fake geo.admin: a preview that writes
nothing, a commit that fills the service and this instance, a second
commit that changes nothing, and a failing canton that writes nothing and
exits non-zero, with the zones already held intact; the same for a zone
with an empty ring, and for a zone that fails to apply.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from io import StringIO
from typing import Any
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.models import CommandRun
from apps.ingest.public import SourceRestZone
from apps.ingest.sources.geo_admin import GeoAdminError
from apps.routes.models import RestZone
from tests.ingest.normalise.test_rest_zones import feature

pytestmark = pytest.mark.django_db

ZONES: dict[str, list[dict[str, Any]]] = {"GR": [feature(1), feature(2)]}


def _fetch(canton: str) -> list[dict[str, Any]]:
    """geo.admin, faked: two zones in GR, none anywhere else."""
    return ZONES.get(canton, [])


def _failing(canton: str) -> list[dict[str, Any]]:
    """geo.admin with VS down."""
    if canton == "VS":
        raise GeoAdminError("VS is down")
    return [feature(3)] if canton == "GR" else []


@pytest.fixture
def geo_admin() -> Iterator[mock.MagicMock]:
    """Route the import's canton reads through ``_fetch``."""
    with mock.patch(
        "apps.ingest.rest_zones.fetch_canton", side_effect=_fetch
    ) as patched:
        yield patched


def _run(*args: str) -> str:
    """Run the command and return its stdout."""
    out = StringIO()
    call_command("import_rest_zones", *args, stdout=out)
    return out.getvalue()


def _counts() -> dict[str, int]:
    """The counts of the most recent recorded run."""
    run = CommandRun.objects.for_command("import_rest_zones").latest("started_at")
    counts: dict[str, int] = run.counts
    return counts


def test_a_preview_writes_nothing(geo_admin: mock.MagicMock) -> None:
    """The counts are a commit's; both tables stay empty."""
    output = _run()
    assert "[READ-ONLY]" in output
    counts = _counts()
    assert counts["service_rest-zones_created"] == 2
    assert counts["rest-zones_created"] == 2
    assert not SourceRestZone.objects.exists()
    assert not RestZone.objects.exists()


def test_a_commit_fills_both_and_a_second_run_changes_nothing(
    geo_admin: mock.MagicMock,
) -> None:
    """The service and this instance hold the zones; the rerun is quiet."""
    _run("--commit")
    assert SourceRestZone.objects.count() == RestZone.objects.count() == 2
    assert _counts()["failed"] == 0
    _run("--commit")
    counts = _counts()
    assert counts["service_rest-zones_unchanged"] == 2
    assert counts["service_rest-zones_created"] == 0
    assert counts["rest-zones_created"] == counts["rest-zones_updated"] == 0


def test_a_failing_canton_writes_nothing_and_exits_non_zero(
    geo_admin: mock.MagicMock,
) -> None:
    """VS down: the error names it, and the zones already held survive."""
    _run("--commit")
    geo_admin.side_effect = _failing
    with pytest.raises(CommandError, match="canton VS failed.*Nothing was written"):
        _run("--commit")
    assert SourceRestZone.objects.live().count() == 2
    assert not SourceRestZone.objects.tombstoned().exists()
    assert RestZone.objects.count() == 2
    assert not RestZone.objects.filter(feature_id=3).exists()


def test_an_empty_ring_writes_nothing_and_exits_non_zero(
    geo_admin: mock.MagicMock,
) -> None:
    """A zone with an empty ring is refused before the sync tombstones anything."""
    _run("--commit")
    broken = feature(3)
    broken["geometry"] = {"type": "Polygon", "coordinates": [[]]}
    geo_admin.side_effect = lambda canton: [broken] if canton == "GR" else []
    with pytest.raises(CommandError, match="no polygon.*Nothing was written"):
        _run("--commit")
    assert SourceRestZone.objects.live().count() == 2
    assert not SourceRestZone.objects.tombstoned().exists()
    assert not SourceRestZone.objects.filter(document__feature_id=3).exists()


def test_a_zone_that_fails_to_apply_rolls_the_sync_back(
    geo_admin: mock.MagicMock,
) -> None:
    """A failed apply undoes the service's sync as well, then exits non-zero."""
    failed = {"rest-zones": Counter({"failed": 1})}
    with (
        mock.patch(
            "apps.routes.management.commands.import_rest_zones.apply_from_service",
            return_value=failed,
        ),
        pytest.raises(CommandError, match="1 zone.*Nothing was written"),
    ):
        _run("--commit")
    assert not SourceRestZone.objects.exists()


def test_the_run_is_registered() -> None:
    """It inherits RegisteredCommand, so every run lands in the register."""
    from apps.core.command_register import RegisteredCommand  # noqa: PLC0415 — the assertion's subject
    from apps.routes.management.commands.import_rest_zones import (  # noqa: PLC0415 — the assertion's subject
        Command,
    )

    assert issubclass(Command, RegisteredCommand)
