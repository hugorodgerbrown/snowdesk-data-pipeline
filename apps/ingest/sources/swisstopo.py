"""
apps/ingest/sources/swisstopo.py — Downloading swisstopo's ski-touring GeoPackage.

The HTTP half of the ski-touring import (SNOW-1276); the reading is
``apps/ingest/normalise/swisstopo_ski.py``. swisstopo publishes the data
through its STAC API:

  ski_archive_url()
      Ask the STAC item ``skitouren`` of the collection
      ``ch.swisstopo-karto.skitouren`` for its GeoPackage asset, and return
      the asset's URL. Looked up each run rather than hard-coded, so a new
      release under a new file name is still found.
  download_ski_archive(destination)
      Stream that asset (a zip of about 17 MB holding one GeoPackage per
      layer) to ``destination``.

The STAC host is ``SWISSTOPO_STAC_URL``, read through ``apps.ingest.conf``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import requests

from apps.ingest import conf

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60  # seconds
DOWNLOAD_CHUNK = 1 << 16  # bytes

# The STAC collection and item the ski-touring map is published under.
COLLECTION = "ch.swisstopo-karto.skitouren"
ITEM = "skitouren"
# The media type of the asset that holds the GeoPackages.
GEOPACKAGE_ZIP = "application/x.geopackage+zip"


class SwisstopoError(RuntimeError):
    """The STAC item has no GeoPackage asset, or a request failed."""


def ski_archive_url() -> str:
    """Return the URL of the ski-touring GeoPackage zip, from the STAC item.

    Returns:
        The asset's ``href``.

    Raises:
        SwisstopoError: The item cannot be read or has no GeoPackage asset.

    """
    base = conf.text("SWISSTOPO_STAC_URL").rstrip("/")
    url = f"{base}/collections/{COLLECTION}/items/{ITEM}"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        assets = response.json().get("assets") or {}
    except (requests.RequestException, ValueError) as exc:
        raise SwisstopoError(f"cannot read the STAC item {url}: {exc}") from exc
    for asset in assets.values():
        if asset.get("type") == GEOPACKAGE_ZIP and asset.get("href"):
            return str(asset["href"])
    raise SwisstopoError(f"the STAC item {url} has no GeoPackage asset")


def download_ski_archive(destination: Path) -> Path:
    """Download the ski-touring GeoPackage zip to ``destination``.

    Args:
        destination: The file to write.

    Returns:
        ``destination``.

    Raises:
        SwisstopoError: The download failed.

    """
    url = ski_archive_url()
    logger.info("swisstopo: downloading %s", url)
    try:
        with requests.get(url, timeout=REQUEST_TIMEOUT, stream=True) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_content(DOWNLOAD_CHUNK):
                    handle.write(chunk)
    except requests.RequestException as exc:
        raise SwisstopoError(f"cannot download {url}: {exc}") from exc
    return destination
