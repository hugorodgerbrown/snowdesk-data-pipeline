"""
apps/ingest/normalise/albina.py — Pure helpers for ALBINA (EUREGIO) bulletins.

Moved out of ``apps/bulletins/services/albina_fetcher.py`` (SNOW-1248), which
keeps the HTTP and the database writes:

  albina_pdf_url   the on-demand PDF a bulletin can be rendered as, built
                   from fields already in its CAAML payload.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

_ALBINA_PDF_API = "https://api.avalanche.report/albina/api/bulletins/pdf"


def albina_pdf_url(raw: dict[str, Any], region: str) -> str:
    """
    Derive the ALBINA on-demand PDF URL from a raw bulletin dict.

    The URL is constructed from fields already present in the raw CAAML
    payload — no additional network call is needed.  The ``region``
    parameter comes from the per-slot fetch loop in
    ``run_albina_pipeline``.

    Args:
        raw: A single raw bulletin dict from the ALBINA CDN.  Must contain
            ``validTime.startTime``.
        region: The ALBINA region code used for this bulletin's CDN slot
            (e.g. ``"AT-07"``, ``"IT-32-BZ"``, ``"IT-32-TN"``).  Passed
            through to the PDF endpoint as the ``region`` query parameter.

    Returns:
        The full ALBINA PDF API URL, e.g.
        ``https://api.avalanche.report/albina/api/bulletins/pdf
        ?date=2025-11-30T16%3A00%3A00Z&region=AT-07&lang=en&grayscale=false``.

    """
    start_time: str = (raw.get("validTime") or {}).get("startTime", "")
    lang: str = raw.get("lang", "en")
    params = {
        "date": start_time,
        "region": region,
        "lang": lang,
        "grayscale": "false",
    }
    return f"{_ALBINA_PDF_API}?{urlencode(params)}"
