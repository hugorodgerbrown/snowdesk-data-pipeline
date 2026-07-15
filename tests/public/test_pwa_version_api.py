"""tests/public/test_pwa_version_api.py — /api/version and /api/sw-config.

Covers the PWA-shell contract endpoints (SNOW-369 + SNOW-372, spec §5.2 /
§5.10). Both are settings-driven read-only views, so the tests are shape
checks — the fixed JSON keys, the Cache-Control headers, and that the
values flow through from ``config/settings/base.py``.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client, override_settings


@pytest.mark.django_db
@override_settings(
    APP_VERSION="2026.07.15.testabc",
    APP_MIN_VERSION="2026.07.01.baseln",
    APP_RELEASED_AT="2026-07-15T09:00:00+00:00",
    SW_KILL=False,
)
def test_version_endpoint_returns_expected_shape() -> None:
    """``/api/version`` returns ``{current, min_supported, released_at, kill}``."""
    response = Client().get("/api/version")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body == {
        "current": "2026.07.15.testabc",
        "min_supported": "2026.07.01.baseln",
        "released_at": "2026-07-15T09:00:00+00:00",
        "kill": False,
    }


@pytest.mark.django_db
def test_version_endpoint_cacheable_for_60_seconds() -> None:
    """Response carries ``Cache-Control: public, max-age=60`` per spec §5.2."""
    response = Client().get("/api/version")
    assert response.status_code == 200
    assert response["Cache-Control"] == "public, max-age=60"


@pytest.mark.django_db
@override_settings(SW_KILL=True)
def test_version_endpoint_reports_kill_true() -> None:
    """Flipping ``SW_KILL`` surfaces via ``kill: true`` in ``/api/version``."""
    response = Client().get("/api/version")
    assert response.status_code == 200
    assert json.loads(response.content)["kill"] is True


@pytest.mark.django_db
def test_version_endpoint_rejects_post() -> None:
    """``/api/version`` is GET-only — POST returns 405."""
    response = Client().post("/api/version")
    assert response.status_code == 405


# ---------------------------------------------------------------------------
# /api/sw-config (SNOW-372, spec §5.10 / §6.2)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(SW_URL="/sw.js", SW_KILL=False)
def test_sw_config_default_shape() -> None:
    """Default: ``{sw_url: '/sw.js', kill: false}``."""
    response = Client().get("/api/sw-config")
    assert response.status_code == 200
    assert json.loads(response.content) == {"sw_url": "/sw.js", "kill": False}


@pytest.mark.django_db
def test_sw_config_uncached() -> None:
    """Response must not be cacheable — spec §5.10 mandates ``no-cache``.

    We use ``no-store`` rather than ``no-cache`` because ops needs the
    live value, not a revalidation dance — but either satisfies the spec's
    "uncached" intent, so the assertion allows both.
    """
    response = Client().get("/api/sw-config")
    cache_control = response["Cache-Control"]
    assert "no-store" in cache_control or "no-cache" in cache_control


@pytest.mark.django_db
@override_settings(SW_URL="/sw-kill.js", SW_KILL=False)
def test_sw_config_can_swap_sw_url() -> None:
    """Flipping ``SW_URL`` in env swaps the client onto the kill-switch SW.

    Mechanism-A escalation: point every installed client at ``/sw-kill.js``
    without touching the code deploy pipeline (spec §6.4).
    """
    response = Client().get("/api/sw-config")
    body = json.loads(response.content)
    assert body["sw_url"] == "/sw-kill.js"
    assert body["kill"] is False


@pytest.mark.django_db
@override_settings(SW_KILL=True)
def test_sw_config_kill_true_evicts_client() -> None:
    """``SW_KILL=true`` returns ``kill: true`` — the client unregisters its SW."""
    response = Client().get("/api/sw-config")
    assert json.loads(response.content)["kill"] is True


@pytest.mark.django_db
def test_sw_config_rejects_post() -> None:
    """``/api/sw-config`` is GET-only — POST returns 405."""
    response = Client().post("/api/sw-config")
    assert response.status_code == 405
