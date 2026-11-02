"""
tests/ingest/sources/test_geo_admin.py — Reading FOEN's rest zones from geo.admin (SNOW-1263).

One canton per ``find`` request, matched exactly; an empty answer is a
real answer, and a failed request or an answer with no result list is an
error. No request leaves the test.
"""

from __future__ import annotations

from unittest import mock

import pytest
import requests
from django.test import override_settings

from apps.ingest.sources import geo_admin


def _response(body: object) -> mock.Mock:
    """A requests response answering ``body`` as JSON."""
    response = mock.Mock()
    response.json.return_value = body
    return response


@override_settings(GEO_ADMIN_API_URL="https://geo.example/MapServer/")
def test_one_canton_is_asked_for_exactly() -> None:
    """The layer, the canton and an exact match, under the configured host."""
    features = [{"featureId": 1}]
    with mock.patch.object(
        geo_admin.requests, "get", return_value=_response({"results": features})
    ) as get:
        assert geo_admin.fetch_canton("GR") == features
    assert get.call_args.args[0] == "https://geo.example/MapServer/find"
    params = get.call_args.kwargs["params"]
    assert params["layer"] == "ch.bafu.wrz-wildruhezonen_portal"
    assert params["searchText"] == "GR"
    assert params["contains"] == "false"
    assert params["sr"] == "4326"


def test_a_canton_with_no_zones_answers_an_empty_list() -> None:
    """Most cantons hold none; that is not a failure."""
    with mock.patch.object(
        geo_admin.requests, "get", return_value=_response({"results": []})
    ):
        assert geo_admin.fetch_canton("ZH") == []


def test_a_failed_request_is_an_error() -> None:
    """A network failure names the canton."""
    with (
        mock.patch.object(
            geo_admin.requests, "get", side_effect=requests.ConnectionError("down")
        ),
        pytest.raises(geo_admin.GeoAdminError, match="VS"),
    ):
        geo_admin.fetch_canton("VS")


@pytest.mark.parametrize(
    "body", [{"error": "x"}, ["not", "a", "dict"], {"results": {}}]
)
def test_an_answer_with_no_result_list_is_an_error(body: object) -> None:
    """Anything but a list of results is refused, never read as empty."""
    with (
        mock.patch.object(geo_admin.requests, "get", return_value=_response(body)),
        pytest.raises(geo_admin.GeoAdminError),
    ):
        geo_admin.fetch_canton("BE")


def test_every_canton_is_listed_once() -> None:
    """Twenty-six codes, no repeats."""
    assert len(geo_admin.CANTONS) == len(set(geo_admin.CANTONS)) == 26
