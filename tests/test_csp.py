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

The basemap-origin tests do the opposite (SNOW-626): they name the
origin they exercise via ``_basemap_style_url`` rather than asserting
the packaged default, because ``OPENFREEMAP_STYLE_URL`` is env-derived
and any developer whose ``.env`` carries the self-hosted production
value would otherwise see them fail for reasons unrelated to their work.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import pytest
from csp.policy import clear_cache
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, override_settings

from config.settings.base import basemap_origin, csp_defaults

REPORT_ONLY_HEADER = "Content-Security-Policy-Report-Only"
ENFORCING_HEADER = "Content-Security-Policy"


def _csp(response: Any) -> str:
    """Return the CSP-Report-Only header as a string, or empty if absent."""
    return str(response.headers.get(REPORT_ONLY_HEADER, ""))


@contextmanager
def _basemap_style_url(style_url: str) -> Generator[str, None, None]:
    """Point the basemap settings — and the derived CSP — at ``style_url``.

    ``OPENFREEMAP_ORIGIN`` and ``CSP_DEFAULTS`` are both computed once at
    ``config.settings.base`` import time, so overriding
    ``OPENFREEMAP_STYLE_URL`` on its own recomputes neither. All three are
    overridden together here, with the derived pair built by the same two
    callables the settings module itself calls — so the override cannot
    encode a different derivation from production's.

    django-csp-plus caches the assembled policy (``csp::rules``), which
    outlives a single request, so the cache is cleared on both entry and
    exit; the surrounding tests must not see this policy either.
    """
    origin = basemap_origin(style_url)
    with override_settings(
        OPENFREEMAP_STYLE_URL=style_url,
        OPENFREEMAP_ORIGIN=origin,
        CSP_DEFAULTS=csp_defaults(origin),
    ):
        clear_cache()
        try:
            yield origin
        finally:
            clear_cache()


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
    with _basemap_style_url("https://tiles.example.test/styles/liberty") as origin:
        response = Client().get("/")
        policy = _csp(response)
    assert "connect-src" in policy
    assert origin in policy


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("style_url", "expected_origin"),
    [
        # The packaged default (the public OpenFreeMap volunteer tier)...
        (
            "https://tiles.openfreemap.org/styles/liberty",
            "https://tiles.openfreemap.org",
        ),
        # ...the self-hosted production origin (SNOW-485)...
        (
            "https://tiles.snowdesk-data.info/styles/liberty",
            "https://tiles.snowdesk-data.info",
        ),
        # ...and a port-bearing URL, which is a valid origin too.
        ("http://localhost:8080/styles/liberty", "http://localhost:8080"),
    ],
)
def test_csp_connect_src_derived_from_openfreemap_style_url(
    style_url: str, expected_origin: str
) -> None:
    """connect-src allowlists OPENFREEMAP_ORIGIN, derived from OPENFREEMAP_STYLE_URL.

    SNOW-242: the two settings are derived from a single env-configurable
    value so they never drift. SNOW-626: driving several values through
    the derivation pins that contract without depending on which one the
    ambient environment happens to carry.
    """
    with _basemap_style_url(style_url) as origin:
        assert origin == expected_origin
        assert settings.OPENFREEMAP_ORIGIN == expected_origin
        assert settings.OPENFREEMAP_STYLE_URL.startswith(settings.OPENFREEMAP_ORIGIN)

        response = Client().get("/")
        policy = _csp(response)

    assert expected_origin in policy


def test_openfreemap_style_url_validation_failure_mode() -> None:
    """A scheme-less OPENFREEMAP_STYLE_URL trips base.py's startup guard.

    SNOW-242: an absolute URL is required because the CSP origin is
    derived from it — a bare host would yield ``://`` in connect-src and
    break tile loading at runtime instead of at startup. SNOW-626: the
    guard lives in ``basemap_origin()``, so this drives it directly
    rather than re-deriving ``urlsplit``'s behaviour by hand.
    """
    with pytest.raises(ImproperlyConfigured):
        basemap_origin("tiles.openfreemap.org/styles/liberty")

    # A port-bearing URL is still a valid origin — scheme + host:port.
    assert (
        basemap_origin("https://host.example:8080/styles/liberty")
        == "https://host.example:8080"
    )

    # And the value the running environment supplied is itself absolute:
    # settings imported at all means the guard passed.
    assert settings.OPENFREEMAP_ORIGIN == basemap_origin(settings.OPENFREEMAP_STYLE_URL)


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
