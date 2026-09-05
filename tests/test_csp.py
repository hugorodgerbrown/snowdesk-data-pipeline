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


@pytest.mark.django_db
def test_csp_allows_the_origin_of_every_basemap_in_the_catalogue() -> None:
    """SNOW-833: every BASEMAP_STYLES entry's own origin reaches connect-src.

    A basemap the user can pick from the ``#basemap-menu`` popover but whose
    origin the policy omits renders blank under enforcement. Adding a
    candidate is a two-line change — one in the catalogue, one in the policy
    — and nothing but this test connects them.

    Origins only. The tiles a style *points at* can live on other hosts
    entirely (swisstopo's five shards are exactly that case) and are not
    discoverable without fetching the style, which a unit test must not do;
    ``test_csp_allows_every_swisstopo_tile_shard`` pins those separately.
    """
    policy = _csp(Client().get("/"))

    for key, style_url in settings.BASEMAP_STYLES.items():
        origin = basemap_origin(style_url)
        assert origin in policy, (
            f"BASEMAP_STYLES[{key!r}] is served from {origin}, which is not in "
            f"the CSP connect-src list — the basemap will render blank once "
            f"CSP_REPORT_ONLY is False."
        )


@pytest.mark.django_db
def test_csp_allows_every_swisstopo_tile_shard() -> None:
    """SNOW-833: connect-src names all five sharded swisstopo tile hosts.

    The swisstopo style JSON names only ``vectortiles.geo.admin.ch``, but
    the ``tiles`` array in each source's TileJSON fans the tiles themselves
    out across ``vectortiles0…4.geo.admin.ch``. Allowlisting the host in the
    style URL is therefore not enough, and CSP has no wildcard that matches
    a subdomain prefix — every shard has to be named.

    The failure this pins is silent while the policy is report-only, which
    is how it survived: the shards were missing for the whole life of the
    swisstopo basemaps and nothing broke, because nothing was enforcing.
    """
    policy = _csp(Client().get("/"))

    assert "https://vectortiles.geo.admin.ch" in policy
    for shard in range(5):
        assert f"https://vectortiles{shard}.geo.admin.ch" in policy


@pytest.mark.django_db
def test_csp_allows_slope_tile_origin_in_connect_and_img_src() -> None:
    """SNOW-691: the slope raster's origin reaches connect-src AND img-src.

    MapLibre fetches a raster tile with ``fetch()`` and then decodes it as
    an image, so a policy naming the origin in only one of the two blocks
    the overlay. ``img-src`` is otherwise ``'self' data:`` only, which makes
    this the directive that would actually have bitten.
    """
    origin = "https://wmts.example.test"
    tile_origin = basemap_origin(settings.OPENFREEMAP_STYLE_URL)
    with override_settings(
        SLOPE_TILE_URL=f"{origin}/1.0.0/slope/{{z}}/{{x}}/{{y}}.png",
        SLOPE_TILE_ORIGIN=origin,
        CSP_DEFAULTS=csp_defaults(tile_origin, slope_origin=origin),
    ):
        clear_cache()
        try:
            policy = _csp(Client().get("/"))
        finally:
            clear_cache()

    connect_src = policy.split("connect-src")[1].split(";")[0]
    img_src = policy.split("img-src")[1].split(";")[0]
    assert origin in connect_src
    assert origin in img_src


def test_csp_defaults_omit_the_slope_origin_when_unset() -> None:
    """No slope origin configured means no extra source in either directive.

    The keyword is defaulted so the direct callers above keep working
    unchanged; this pins that the default is an OMISSION rather than an
    empty string, which would emit a stray token into the policy.
    """
    policy = csp_defaults("https://tiles.example.test")
    assert policy["img-src"] == ["'self'", "data:"]
    assert "" not in policy["connect-src"]
    assert policy["connect-src"].count("'self'") == 1


def test_slope_tile_origin_is_derived_from_the_configured_template() -> None:
    """SLOPE_TILE_ORIGIN is ``basemap_origin(SLOPE_TILE_URL)``, not a literal.

    The same anti-drift contract SNOW-242 set for the basemap: one
    env-configurable value, one derivation, so the CSP can never allowlist
    an origin the map does not actually fetch from.
    """
    assert settings.SLOPE_TILE_ORIGIN == basemap_origin(settings.SLOPE_TILE_URL)
    assert settings.SLOPE_TILE_URL.startswith(f"{settings.SLOPE_TILE_ORIGIN}/")


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
