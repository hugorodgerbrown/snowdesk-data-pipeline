"""
tests/ingest/sources/test_swisstopo.py — The ski-touring archive download (SNOW-1276).

The STAC lookup picks the GeoPackage asset, whatever it is named, and
refuses an item without one; the download streams it to disk. No
request leaves the test.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
import requests
from django.test import override_settings

from apps.ingest.sources import swisstopo

ITEM = {
    "assets": {
        "additional-files_skitouren_2056.zip": {
            "href": "https://data.example/additional.zip",
            "type": "application/x.pdf+zip",
        },
        "skitouren_2056.gpkg.zip": {
            "href": "https://data.example/skitouren_2056.gpkg.zip",
            "type": "application/x.geopackage+zip",
        },
    }
}


def _response(body: object = None, chunks: list[bytes] | None = None) -> mock.Mock:
    """A requests response that answers ``body`` or streams ``chunks``."""
    response = mock.MagicMock()
    response.json.return_value = body
    response.iter_content.return_value = chunks or []
    response.__enter__.return_value = response
    return response


@override_settings(SWISSTOPO_STAC_URL="https://stac.example/api/stac/v1/")
def test_the_archive_url_is_the_items_geopackage_asset() -> None:
    """The item is read under the configured host; the gpkg asset wins."""
    with mock.patch.object(
        swisstopo.requests, "get", return_value=_response(ITEM)
    ) as get:
        assert (
            swisstopo.ski_archive_url()
            == ITEM["assets"]["skitouren_2056.gpkg.zip"]["href"]
        )
    assert get.call_args.args[0] == (
        "https://stac.example/api/stac/v1/collections/"
        "ch.swisstopo-karto.skitouren/items/skitouren"
    )


def test_an_item_without_a_geopackage_is_refused() -> None:
    """No asset of the GeoPackage type is an error, not a guess."""
    with mock.patch.object(
        swisstopo.requests, "get", return_value=_response({"assets": {}})
    ):
        with pytest.raises(swisstopo.SwisstopoError, match="no GeoPackage"):
            swisstopo.ski_archive_url()


def test_an_unreachable_item_is_refused() -> None:
    """A request failure is a SwisstopoError."""
    with mock.patch.object(
        swisstopo.requests, "get", side_effect=requests.ConnectionError("down")
    ):
        with pytest.raises(swisstopo.SwisstopoError, match="cannot read"):
            swisstopo.ski_archive_url()


def test_the_download_streams_to_the_destination(tmp_path: Path) -> None:
    """Every chunk is written, in order."""
    responses = [_response(ITEM), _response(chunks=[b"PK", b"\x03\x04"])]
    with mock.patch.object(swisstopo.requests, "get", side_effect=responses):
        path = swisstopo.download_ski_archive(tmp_path / "s.zip")
    assert path.read_bytes() == b"PK\x03\x04"


def test_a_failed_download_is_refused(tmp_path: Path) -> None:
    """The asset request failing is a SwisstopoError."""
    failing = _response()
    failing.raise_for_status.side_effect = requests.HTTPError("404")
    with mock.patch.object(
        swisstopo.requests, "get", side_effect=[_response(ITEM), failing]
    ):
        with pytest.raises(swisstopo.SwisstopoError, match="cannot download"):
            swisstopo.download_ski_archive(tmp_path / "s.zip")
