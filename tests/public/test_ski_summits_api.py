"""
tests/public/test_ski_summits_api.py — /api/ski-summits.json (SNOW-1294).

Every SAC summit and hut as a search row: its shape, that it is read from
the summit cache keyed on the tours' stamp, and that it is anonymous and
read-only.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from tests.factories import SkiTourFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """No cached summits between tests."""
    cache.clear()


def _get(client: Client, **headers: str) -> Any:
    """GET the endpoint."""
    return client.get(reverse("api:ski_summits_json"), headers=headers)


def test_every_summit_and_hut_is_a_row(client: Client) -> None:
    """One row per target, its tours counted up and down, a hut named as one."""
    SkiTourFactory.create()
    SkiTourFactory.create(ascent_m=None, descent_m=1100)
    SkiTourFactory.create(target_name="Chamonna Tuoi CAS", target_altitude_m=2250)
    response = _get(client)
    assert response.status_code == 200
    rows = {row["key"]: row for row in response.json()["summits"]}
    assert set(rows) == {"piz-buin-3312", "chamonna-tuoi-cas-2250"}
    peak = rows["piz-buin-3312"]
    assert set(peak) == {"key", "name", "altitude_m", "kind", "lon", "lat", "tours"}
    assert peak["name"] == "Piz Buin"
    assert peak["altitude_m"] == 3312
    assert peak["kind"] == "summit"
    assert peak["tours"] == 2
    assert isinstance(peak["lon"], float)
    assert isinstance(peak["lat"], float)
    assert rows["chamonna-tuoi-cas-2250"]["kind"] == "hut"


def test_no_tours_is_an_empty_list_with_no_stale_window(client: Client) -> None:
    """An empty answer is not pinned: an import may be still to come."""
    response = _get(client)
    assert response.status_code == 200
    assert response.json() == {"summits": []}
    assert "stale-while-revalidate" not in response["Cache-Control"]


def test_it_is_etagged_and_revalidates(client: Client) -> None:
    """A client holding the list is told it is unchanged."""
    SkiTourFactory.create()
    first = _get(client)
    assert "stale-while-revalidate" in first["Cache-Control"]
    again = _get(client, if_none_match=first["ETag"])
    assert again.status_code == 304


def test_an_import_changes_the_answer(client: Client) -> None:
    """The summits are cached on the tours' stamp, so a new tour reads afresh."""
    SkiTourFactory.create()
    assert len(_get(client).json()["summits"]) == 1
    SkiTourFactory.create(target_name="Signalhorn (GR)", target_altitude_m=3207)
    assert len(_get(client).json()["summits"]) == 2


def test_it_is_anonymous_and_read_only(client: Client) -> None:
    """No sign-in is needed to read it, and nothing can be written to it."""
    SkiTourFactory.create()
    assert _get(client).status_code == 200
    assert client.post(reverse("api:ski_summits_json")).status_code == 405
