"""Tests for the Content Security Policy wiring (django-csp-plus).

Dev/test settings (``config.settings.development``) enable CSP in
report-only mode, so every HTML response should carry
``Content-Security-Policy-Report-Only`` with our baseline directives.
``/admin/`` is explicitly exempted; JSON responses do not receive the
header (built-in content-type filter).

``CSP_ENABLED`` and ``CSP_REPORT_ONLY`` are read at middleware import
time by django-csp-plus, so these tests deliberately do not use
``override_settings`` to toggle them — they assert the dev baseline
instead.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

REPORT_ONLY_HEADER = "Content-Security-Policy-Report-Only"
ENFORCING_HEADER = "Content-Security-Policy"


def _csp(response: Any) -> str:
    """Return the CSP-Report-Only header as a string, or empty if absent."""
    return str(response.headers.get(REPORT_ONLY_HEADER, ""))


@pytest.mark.django_db
def test_csp_header_present_on_home_page() -> None:
    """The home page carries a report-only CSP header."""
    response = Client().get("/")
    assert response.status_code == 200
    assert REPORT_ONLY_HEADER in response.headers
    # And not the enforcing variant — report-only is the initial posture.
    assert ENFORCING_HEADER not in response.headers


@pytest.mark.django_db
def test_csp_header_present_on_map_page() -> None:
    """The map page carries a report-only CSP header."""
    response = Client().get("/map/")
    assert response.status_code == 200
    assert REPORT_ONLY_HEADER in response.headers


@pytest.mark.django_db
def test_csp_allows_maplibre_tile_origin() -> None:
    """The baseline policy allowlists the MapLibre tile origin in connect-src."""
    response = Client().get("/map/")
    policy = _csp(response)
    assert "connect-src" in policy
    assert "https://tiles.openfreemap.org" in policy


@pytest.mark.django_db
def test_csp_contains_script_nonce() -> None:
    """script-src includes a concrete nonce (placeholder replaced at request time)."""
    response = Client().get("/")
    policy = _csp(response)
    assert "script-src" in policy
    # "'nonce-{nonce}'" in CSP_DEFAULTS is replaced with a real base64 value;
    # the literal placeholder must not leak into the emitted header.
    assert "{nonce}" not in policy
    assert "'nonce-" in policy


@pytest.mark.django_db
def test_csp_worker_src_allows_blob_and_self() -> None:
    """worker-src covers blob: (MapLibre) and 'self' (our /sw.js)."""
    response = Client().get("/map/")
    policy = _csp(response)
    assert "worker-src" in policy
    assert "blob:" in policy


@pytest.mark.django_db
def test_csp_defaults_are_locked_down() -> None:
    """default-src is 'none' and frame-ancestors 'none' (clickjacking)."""
    response = Client().get("/")
    policy = _csp(response)
    assert "default-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy


@pytest.mark.django_db
def test_csp_header_absent_on_admin() -> None:
    """CSP is intentionally skipped on /admin/ via CSP_FILTER_REQUEST_FUNC."""
    User = get_user_model()
    User.objects.create_superuser(
        username="admin", email="admin@example.com", password="p"
    )
    client = Client()
    client.login(username="admin", password="p")  # noqa: S106
    response = client.get("/admin/")
    assert response.status_code == 200
    assert REPORT_ONLY_HEADER not in response.headers
    assert ENFORCING_HEADER not in response.headers


@pytest.mark.django_db
def test_csp_header_absent_on_json_response() -> None:
    """The built-in response filter limits CSP to text/html responses."""
    response = Client().get("/api/regions.geojson")
    assert response.status_code == 200
    assert REPORT_ONLY_HEADER not in response.headers
    assert ENFORCING_HEADER not in response.headers


@pytest.mark.django_db
def test_home_template_renders_nonce_on_inline_script() -> None:
    """templates/includes/theme_head.html injects request.csp_nonce."""
    response = Client().get("/")
    assert response.status_code == 200
    body = response.content.decode()
    # The dark-mode init script appears on every public page; its nonce
    # attribute must be populated with the same value that appears in
    # the CSP header.
    assert '<script nonce="' in body
    # Extract the script nonce and confirm it appears in the policy.
    script_nonce = body.split('<script nonce="', 1)[1].split('"', 1)[0]
    assert script_nonce
    assert f"'nonce-{script_nonce}'" in _csp(response)
