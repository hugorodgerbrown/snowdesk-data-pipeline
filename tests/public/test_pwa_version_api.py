"""tests/public/test_pwa_version_api.py — /api/version and /api/sw-config.

Covers the PWA-shell contract endpoints (SNOW-369 + SNOW-372, spec §5.2 /
§5.10). Both are settings-driven read-only views, so most of the tests are
shape checks — the fixed JSON keys, the Cache-Control headers, and that the
values flow through from ``config/settings/base.py``.

The exception is ``update_required`` (SNOW-609), which is a real decision
rather than a passthrough: it is the server's whole forced-update verdict,
computed from the request's ``X-Client-Version`` against
``settings.APP_BLOCKED_VERSIONS``. The membership matrix below — including
the fail-open case for a client that sends no version — is the test of
record for it, since the client now performs no version comparison at all.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client, override_settings

from config.settings.base import _comma_separated_frozenset


@pytest.mark.django_db
@override_settings(
    APP_VERSION="2026.07.15.testabc",
    APP_BLOCKED_VERSIONS=frozenset(),
    APP_RELEASED_AT="2026-07-15T09:00:00+00:00",
    SW_KILL=False,
)
def test_version_endpoint_returns_expected_shape() -> None:
    """``/api/version`` returns ``{current, update_required, released_at, kill}``."""
    response = Client().get("/api/version")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body == {
        "current": "2026.07.15.testabc",
        "update_required": False,
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
def test_version_endpoint_varies_on_client_version() -> None:
    """The verdict depends on a request header, so the response must Vary on it.

    ``update_required`` is per-client; without ``Vary: X-Client-Version`` a
    shared cache could replay one blocked client's ``true`` to every other
    client for the full 60-second window.
    """
    response = Client().get("/api/version")
    assert "X-Client-Version" in response["Vary"]


# ---------------------------------------------------------------------------
# update_required — the blocked-build membership matrix (SNOW-609)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(APP_BLOCKED_VERSIONS=frozenset({"badbuild", "worsebuild"}))
def test_update_required_true_for_a_blocked_client() -> None:
    """A client whose build is in the blocked set is told to force-update."""
    response = Client().get("/api/version", headers={"x-client-version": "badbuild"})
    assert json.loads(response.content)["update_required"] is True


@pytest.mark.django_db
@override_settings(APP_BLOCKED_VERSIONS=frozenset({"badbuild"}))
def test_update_required_false_for_an_unblocked_client() -> None:
    """A client outside the blocked set is left alone."""
    response = Client().get("/api/version", headers={"x-client-version": "goodbuild"})
    assert json.loads(response.content)["update_required"] is False


@pytest.mark.django_db
@override_settings(APP_BLOCKED_VERSIONS=frozenset())
def test_update_required_false_when_no_build_is_blocked() -> None:
    """An empty blocked set — the default — blocks nobody."""
    response = Client().get("/api/version", headers={"x-client-version": "anybuild"})
    assert json.loads(response.content)["update_required"] is False


@pytest.mark.django_db
@override_settings(APP_BLOCKED_VERSIONS=frozenset({"badbuild"}))
def test_update_required_fails_open_without_a_client_version_header() -> None:
    """An unidentified client is never blocked.

    A blocking modal on a client whose build we cannot read has no recovery
    path — it would sit there through every reload. Absent header means
    absent verdict.
    """
    response = Client().get("/api/version")
    assert json.loads(response.content)["update_required"] is False


@pytest.mark.django_db
@override_settings(APP_BLOCKED_VERSIONS=frozenset({""}))
def test_update_required_fails_open_against_an_empty_blocked_entry() -> None:
    """An empty string in the blocked set cannot match a header-less client.

    ``_comma_separated_frozenset`` already drops empty entries, so this
    state is unreachable from env config; the guard is asserted here
    directly because it is what makes the fail-open promise unconditional.
    """
    response = Client().get("/api/version")
    assert json.loads(response.content)["update_required"] is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", frozenset()),
        ("abc123", frozenset({"abc123"})),
        ("abc123,def456", frozenset({"abc123", "def456"})),
        (" abc123 , def456 ", frozenset({"abc123", "def456"})),
        ("abc123,,", frozenset({"abc123"})),
        (",", frozenset()),
        ("abc123,abc123", frozenset({"abc123"})),
    ],
)
def test_blocked_versions_env_parsing(raw: str, expected: frozenset[str]) -> None:
    """``APP_BLOCKED_VERSIONS`` parses to a trimmed, empty-free frozenset.

    The empty-entry cases matter most: a ``""`` member would match the
    empty string a header-less request resolves to, silently blocking every
    unidentified client.
    """
    assert _comma_separated_frozenset(raw) == expected


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
