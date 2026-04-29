"""tests/public/test_offline_api.py — Tests for the service-worker endpoint.

Originally covered the SNOW-9 ``/api/offline-manifest/map/`` precache
endpoint as well, but that endpoint was retired in SNOW-79 (PWA shell
rewrite). The remaining surface is ``/sw.js`` itself — served by
``public.views.serve_sw`` and consumed by ``static/js/sw_register.js``.
"""

from __future__ import annotations

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
