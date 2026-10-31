"""
apps/ingest/sources/meteofrance.py — Météo-France's DPBRA API and BRA archive.

The HTTP half of the Météo-France pipeline, moved out of
``apps/bulletins/services/meteofrance_fetcher.py`` (SNOW-1249):

  resolve_base_url            an explicit override, else
                              ``METEOFRANCE_API_BASE_URL``.
  fetch_meteofrance_bulletin  one massif's DPBRA XML, behind the APIM key —
                              or, for a ``file://`` base URL, read from a
                              local mirror so a keyless session can run it.
  fetch_mf_index              the BRA archive's daily index.
  meteofrance_pdf_url         the archive PDF one bulletin was published as,
                              failing open to ``""``.
  pdf_session                 a session for the archive, with its User-Agent.

Translating the XML is ``apps/ingest/normalise/meteofrance/translator.py``;
the walk across massifs lives in ``apps/ingest/runs.py``.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests

from apps.ingest import conf

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30  # seconds

MF_ARCHIVE_BASE = "https://donneespubliques.meteofrance.fr/donnees_libres/Pdf/BRA"
MF_USER_AGENT = "snowdesk/1.0 (contact@snowdesk.app)"


def pdf_session() -> requests.Session:
    """Return a session for the BRA archive, identifying Snowdesk.

    One session is shared across a run's index lookups for connection reuse.

    Returns:
        A ``requests.Session`` carrying ``MF_USER_AGENT``.

    """
    session = requests.Session()
    session.headers["User-Agent"] = MF_USER_AGENT
    return session


def resolve_base_url(base_url: str | None) -> str:
    """
    Resolve the effective API base URL for this pipeline run.

    An explicit ``base_url`` wins (used by ``fetch_bulletins --local-mirror``
    to forward ``settings.METEOFRANCE_API_LOCAL_MIRROR_URL``); otherwise the
    live ``settings.METEOFRANCE_API_BASE_URL`` is used. The mirror setting
    is *never* picked up implicitly — matching the SLF and ALBINA fetchers
    so a stale ``.env`` value cannot silently redirect a live run.

    Args:
        base_url: Explicit override from the caller, or ``None``.

    Returns:
        The resolved base URL string.

    """
    if base_url is not None:
        return base_url

    return conf.text("METEOFRANCE_API_BASE_URL")


def fetch_mf_index(
    valid_date: date,
    session: requests.Session,
) -> list[dict[str, Any]] | None:
    """
    Fetch the Météo-France BRA daily index for ``valid_date``.

    Returns the parsed JSON list on success, ``None`` on any error (network,
    non-200/404, parse failure).  A 404 response is treated as "no bulletins
    that day" and is not logged at warning level.

    Args:
        valid_date: The bulletin date — used to build ``bra.YYYYMMDD.json``.
        session: A shared ``requests.Session`` for connection reuse.

    Returns:
        A list of ``{"massif": …, "heures": […]}`` dicts, or ``None`` on any
        error.

    """
    index_url = f"{MF_ARCHIVE_BASE}/bra.{valid_date:%Y%m%d}.json"
    try:
        resp = session.get(index_url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.info("fetch_mf_index: network error fetching %s: %s", index_url, exc)
        return None

    if resp.status_code == 404:
        return None  # off-season gap; caller treats None as "no URL"

    if not resp.ok:
        logger.info(
            "fetch_mf_index: unexpected status %d for %s", resp.status_code, index_url
        )
        return None

    try:
        result: list[dict[str, Any]] = resp.json()
        return result
    except Exception as exc:
        logger.info("fetch_mf_index: could not parse index JSON: %s", exc)
        return None


def meteofrance_pdf_url(
    massif_name: str,
    valid_date: date,
    session: requests.Session,
) -> str:
    """
    Resolve the Météo-France BRA archive PDF URL for a given massif and date.

    Fails open: any network error, non-200 response (other than 404), missing
    massif, or empty ``heures`` list returns ``""`` and logs at INFO level —
    the upstream portal is being decommissioned, so failures must not break
    ingest.

    Args:
        massif_name: Upper-case massif slug as stored in the raw bulletin's
            ``customData["MF"]["massif"]`` field (e.g. ``"CHABLAIS"``).
            Do *not* pass ``regions[0]["name"]`` — that field holds the
            title-case display name (e.g. ``"Chablais"``) which does not
            match the index.
        valid_date: The bulletin's validity date — used to build the
            ``bra.YYYYMMDD.json`` index URL.
        session: A ``requests.Session`` shared across the pipeline run for
            connection reuse.

    Returns:
        The direct PDF URL, or ``""`` when unavailable.

    """
    if not massif_name:
        logger.info("meteofrance_pdf_url: empty massif_name — returning empty")
        return ""

    index = fetch_mf_index(valid_date, session)
    if index is None:
        return ""

    for entry in index:
        if entry.get("massif") != massif_name:
            continue
        heures: list[str] = entry.get("heures") or []
        if not heures:
            logger.info(
                "meteofrance_pdf_url: massif %s found but heures is empty", massif_name
            )
            return ""
        latest = sorted(heures)[-1]
        return f"{MF_ARCHIVE_BASE}/BRA.{massif_name}.{latest}.pdf"

    logger.info(
        "meteofrance_pdf_url: massif %s not found in index for %s",
        massif_name,
        valid_date,
    )
    return ""


# ---------------------------------------------------------------------------
# HTTP / local-mirror fetcher
# ---------------------------------------------------------------------------


def fetch_meteofrance_bulletin(
    massif_id: int,
    base_url: str,
    api_key: str,
) -> bytes | None:
    """
    Fetch the DPBRA XML for one massif from the MeteoFrance APIM.

    If ``base_url`` starts with ``file://``, the function reads the XML from
    the local filesystem at ``<directory>/massif-{massif_id:03d}.xml`` so
    integration tests can run without a live API key.

    Args:
        massif_id: The MeteoFrance integer massif ID (1..74).
        base_url: The API base URL, or a ``file://`` directory URI for local
            mirror use.
        api_key: The MeteoFrance APIM key sent as the ``apikey`` header.
            Unused for ``file://`` base URLs.

    Returns:
        The raw XML bytes, or ``None`` when the API returns 404 (no
        bulletin for this massif today).

    Raises:
        RuntimeError: ``base_url`` is a live HTTP(S) URL and ``api_key``
            is empty.
        requests.HTTPError: The API returns a non-2xx, non-404 status code.

    """
    if base_url.startswith("file://"):
        return _read_local_mirror(massif_id, base_url)

    if not api_key:
        raise RuntimeError(
            "METEOFRANCE_API_KEY must be set when base_url is not a file:// mirror."
        )

    url = f"{base_url.rstrip('/')}/massif/{massif_id}/BRA"
    logger.debug(
        "Fetching MeteoFrance bulletin: massif=%d url=%s",
        massif_id,
        url,
    )

    response = requests.get(
        url,
        headers={"apikey": api_key},
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code == 404:
        logger.debug("No bulletin for massif %d today (404)", massif_id)
        return None

    response.raise_for_status()
    return response.content


def _read_local_mirror(massif_id: int, base_url: str) -> bytes | None:
    """
    Read a DPBRA XML from the local mirror directory.

    Constructs the path ``<mirror_dir>/massif-{massif_id:03d}.xml`` and
    returns the file contents. Returns ``None`` when the file does not exist,
    mirroring the HTTP-404 semantics of the live path.

    Args:
        massif_id: The MeteoFrance integer massif ID.
        base_url: A ``file://`` URI pointing at the mirror directory.

    Returns:
        The raw XML bytes, or ``None`` when the file is absent.

    """
    parsed = urlparse(base_url)
    # Percent-decoded: ``Path.as_uri()`` (the development default) escapes a
    # space or non-ASCII character in the checkout path, and a raw
    # ``%20`` names a directory that does not exist.
    mirror_dir = Path(parsed.netloc + unquote(parsed.path))
    xml_path = mirror_dir / f"massif-{massif_id:03d}.xml"
    if not xml_path.exists():
        logger.debug("Local mirror: no file for massif %d at %s", massif_id, xml_path)
        return None
    return xml_path.read_bytes()


def bulletin_pdf_url(raw: dict[str, Any], session: requests.Session) -> str:
    """Return the archive PDF for one translated bulletin, or ``""``.

    Reads the canonical upstream slug from ``customData.MF.massif`` (e.g.
    ``"VANOISE"``), not ``regions[0]["name"]``, which carries the display
    name (e.g. ``"Vanoise"``): the archive index keys on the upper-case slug.
    Fails open, like ``meteofrance_pdf_url``: the portal is being
    decommissioned, so no failure here may stop a bulletin being stored.

    Args:
        raw: A CAAML v6 bulletin translated from DPBRA XML.
        session: The run's archive session (see ``pdf_session``).

    Returns:
        The PDF URL, or ``""`` when it cannot be resolved.

    """
    massif_name: str = raw.get("customData", {}).get("MF", {}).get("massif", "") or ""
    start_time_str: str = (raw.get("validTime") or {}).get("startTime", "")
    if not (massif_name and start_time_str):
        return ""
    try:
        issued = start_time_str.replace("Z", "+00:00")
        valid_date = datetime.fromisoformat(issued).date()
        return meteofrance_pdf_url(massif_name, valid_date, session)
    except Exception as exc:
        logger.info(
            "bulletin_pdf_url: could not resolve pdf_url for %s: %s",
            raw.get("bulletinID", ""),
            exc,
        )
        return ""
