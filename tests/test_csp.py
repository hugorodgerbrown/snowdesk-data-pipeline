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
from urllib.parse import urlsplit

import pytest
from django.conf import settings
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
    """The canonical map page (/) carries a report-only CSP header.

    SNOW-344: /map/ is now a 301 redirect; the live map page is /.
    """
    response = Client().get("/")
    assert response.status_code == 200
    assert REPORT_ONLY_HEADER in response.headers


@pytest.mark.django_db
def test_csp_allows_maplibre_tile_origin() -> None:
    """The baseline policy allowlists the MapLibre tile origin in connect-src."""
    response = Client().get("/")
    policy = _csp(response)
    assert "connect-src" in policy
    assert "https://tiles.openfreemap.org" in policy


@pytest.mark.django_db
def test_csp_connect_src_derived_from_openfreemap_style_url() -> None:
    """connect-src allowlists OPENFREEMAP_ORIGIN, derived from OPENFREEMAP_STYLE_URL.

    SNOW-242: the two settings are derived from a single env-configurable
    value so they never drift.
    """
    assert settings.OPENFREEMAP_ORIGIN == "https://tiles.openfreemap.org"
    assert settings.OPENFREEMAP_STYLE_URL.startswith(settings.OPENFREEMAP_ORIGIN)

    response = Client().get("/")
    policy = _csp(response)
    assert settings.OPENFREEMAP_ORIGIN in policy


def test_openfreemap_style_url_validation_failure_mode() -> None:
    """A scheme-less OPENFREEMAP_STYLE_URL would fail base.py's startup guard.

    SNOW-242: settings are read at import time, so this test cannot reload
    ``config.settings.base`` with a bad env value without side effects on
    the rest of the suite. Instead it pins the failure mode the inline
    ``ImproperlyConfigured`` guard relies on (empty ``scheme``/``netloc``
    from ``urlsplit``) and confirms the deployed default does not trip it.
    """
    bad_parts = urlsplit("tiles.openfreemap.org/styles/liberty")
    assert not bad_parts.scheme
    assert not bad_parts.netloc

    # A port-bearing URL is still a valid origin — scheme + host:port.
    port_parts = urlsplit("https://host.example:8080/styles/liberty")
    assert port_parts.scheme and port_parts.netloc
    assert f"{port_parts.scheme}://{port_parts.netloc}" == "https://host.example:8080"

    assert settings.OPENFREEMAP_ORIGIN == "https://tiles.openfreemap.org"


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
    response = Client().get("/")
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
    from tests.factories import UserFactory

    UserFactory.create(email="admin@example.com", is_superuser=True, is_staff=True)
    client = Client()
    client.login(username="admin@example.com", password="pass")  # noqa: S106
    response = client.get("/admin/")
    assert response.status_code == 200
    assert REPORT_ONLY_HEADER not in response.headers
    assert ENFORCING_HEADER not in response.headers


@pytest.mark.django_db
def test_csp_header_absent_on_json_response() -> None:
    """The built-in response filter limits CSP to text/html responses."""
    response = Client().get("/api/regions.geojson?country=ch")
    assert response.status_code == 200
    assert REPORT_ONLY_HEADER not in response.headers
    assert ENFORCING_HEADER not in response.headers


@pytest.mark.django_db
def test_csp_nonce_token_not_double_wrapped() -> None:
    """script-src nonce must be well-formed and not double-wrapped.

    CSP_DEFAULTS used to carry ``'nonce-{nonce}'`` (with surrounding single
    quotes), which caused django-csp-plus to emit ``'nonce-'nonce-<b64>''``
    — a double-wrapped value that browsers reject.  After the fix the
    placeholder is bare ``{nonce}`` and django-csp-plus emits a correctly
    quoted ``'nonce-<b64>'``.
    """
    response = Client().get("/")
    policy = _csp(response)
    # Well-formed nonce present.
    assert "'nonce-" in policy
    # Malformed double-wrap must not appear.
    assert "'nonce-'nonce-" not in policy


@pytest.mark.django_db
def test_csp_no_unpkg_origin() -> None:
    """unpkg.com must not appear in CSP headers now that assets are self-hosted.

    htmx and MapLibre GL are vendored into static/ (SNOW-169), so there is no
    longer any reason to allowlist the unpkg CDN in script-src or style-src.
    """
    # SNOW-344: /map/ is now a 301 redirect; only test the live page at /.
    response = Client().get("/")
    policy = _csp(response)
    assert "unpkg.com" not in policy, (
        f"unpkg.com unexpectedly present in CSP header for /: {policy}"
    )


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
