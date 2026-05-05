"""tests/public/test_offline_api.py — Tests for the service-worker endpoint.

Originally covered the SNOW-9 ``/api/offline-manifest/map/`` precache
endpoint as well, but that endpoint was retired in SNOW-79 (PWA shell
rewrite). The remaining surface is ``/sw.js`` itself — served by
``public.views.serve_sw`` and consumed by ``static/js/sw_register.js``.
SNOW-118 added a pre-cached offline fallback page and asserts that the
SW source references it so the network-first navigation strategy can
return it on a both-fail miss.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import Client


def test_serve_sw_returns_200_with_correct_headers() -> None:
    """``/sw.js`` returns 200 with the required service-worker headers."""
    client = Client()
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/javascript")
    assert response["Service-Worker-Allowed"] == "/"
    assert response["Cache-Control"] == "no-cache"


def test_serve_sw_contains_service_worker_code() -> None:
    """The SW script body contains ``addEventListener`` (proves it is not empty)."""
    client = Client()
    response = client.get("/sw.js")
    assert b"addEventListener" in response.content


def test_serve_sw_references_offline_fallback() -> None:
    """The SW source references the offline fallback URL (SNOW-118).

    Asserts the contract that ``_networkFirst`` falls through to a
    cached offline page when both network and per-page cache miss. A
    runtime test isn't possible in pytest — the SW only executes inside
    a browser context — so we verify the static contract instead.
    """
    client = Client()
    response = client.get("/sw.js")
    assert b"/static/offline.html" in response.content
    assert b"OFFLINE_FALLBACK" in response.content


def test_offline_fallback_page_exists_on_disk() -> None:
    """``static/offline.html`` ships and contains the expected heading (SNOW-118)."""
    path = Path(settings.BASE_DIR) / "static" / "offline.html"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "You're offline" in content
    # The page must NOT pull external CSS/JS — its job is to render with
    # zero network access. Strip HTML comments before matching so the
    # rationale comment in the file (which spells out "no
    # <link rel=stylesheet>") doesn't trip the assertion.
    stripped = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    assert '<link rel="stylesheet"' not in stripped
    assert "<script src=" not in stripped
