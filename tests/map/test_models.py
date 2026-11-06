"""tests/map/test_models.py — the map app's models.

Map3dDeviceTest, an anonymous 3D test result (SNOW-1456). SNOW-1450 removed
Map3dShare, the retired /map/3d/'s share token.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import site
from django.test import RequestFactory

from apps.map.admin import Map3dDeviceTestAdmin
from apps.map.models import Map3dDeviceTest
from tests.factories import Map3dDeviceTestFactory

pytestmark = pytest.mark.django_db


def test_a_device_test_keeps_its_overlays_and_passes() -> None:
    """The JSON fields round-trip: the overlays list and each pass's figures."""
    row = Map3dDeviceTestFactory.create(overlays=["terrain"], probe=True)
    row.refresh_from_db()
    assert row.overlays == ["terrain"]
    assert [p["label"] for p in row.passes] == ["Saas-Fee z12.6", "Valais z9"]
    assert row.passes[0]["median_ms"] == 16.7
    assert row.verdict == Map3dDeviceTest.Verdict.SMOOTH


def test_a_device_test_holds_nothing_about_who_sent_it() -> None:
    """No user, session or IP field: the row is anonymous by its shape."""
    names = {field.name for field in Map3dDeviceTest._meta.get_fields()}
    assert not names & {"user", "created_by", "session", "session_key", "ip"}


def test_device_tests_list_newest_first_and_from_readers_drops_probes() -> None:
    """Meta.ordering is -created_at; from_readers() leaves out staff probes."""
    older = Map3dDeviceTestFactory.create()
    probe = Map3dDeviceTestFactory.create(probe=True)
    assert list(Map3dDeviceTest.objects.all()) == [probe, older]
    assert list(Map3dDeviceTest.objects.from_readers()) == [older]


def test_device_test_str_delegates_to_to_string() -> None:
    """__str__ is to_string(), naming the verdict and the GPU."""
    row = Map3dDeviceTestFactory.create(
        verdict=Map3dDeviceTest.Verdict.SLOW, gpu="Adreno 610"
    )
    assert str(row) == row.to_string() == "3D device test Too slow (Adreno 610)"
    assert "unknown GPU" in Map3dDeviceTestFactory.create(gpu="").to_string()


def test_device_test_admin_is_read_only_and_filters_by_verdict_and_probe() -> None:
    """The admin cannot add a row, and filters on verdict and probe."""
    admin = site._registry[Map3dDeviceTest]
    assert isinstance(admin, Map3dDeviceTestAdmin)
    assert admin.list_filter == ["verdict", "probe"]
    assert admin.has_add_permission(RequestFactory().get("/admin/")) is False
