"""tests/public/test_offline_api.py — Tests for the service-worker endpoint.

Originally covered the SNOW-9 ``/api/offline-manifest/map/`` precache
endpoint as well, but that endpoint was retired in SNOW-79 (PWA shell
rewrite). The remaining surface is ``/sw.js`` itself — served by
``apps.public.views.serve_sw`` and consumed by ``static/js/sw_register.js``.
SNOW-118 added a pre-cached offline fallback page and asserts that the
SW source references it so the network-first navigation strategy can
return it on a both-fail miss.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import Client, override_settings


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


def test_serve_sw_contains_push_event_handler() -> None:
    """The SW source contains a 'push' event listener (SNOW-228 Web Push spike).

    Asserts the static contract that ``addEventListener('push', ...)`` is
    present in the SW source — a runtime test isn't possible in pytest since
    the SW only executes inside a browser context.
    """
    client = Client()
    response = client.get("/sw.js")
    assert b"addEventListener('push'" in response.content


def test_serve_sw_contains_notificationclick_handler() -> None:
    """The SW source contains a 'notificationclick' event listener (SNOW-228).

    Asserts that the handler that focuses or opens the target URL on
    notification click is present in the SW source.
    """
    client = Client()
    response = client.get("/sw.js")
    assert b"addEventListener('notificationclick'" in response.content


def test_offline_fallback_page_exists_on_disk() -> None:
    """``static/offline.html`` ships and contains the expected heading (SNOW-118)."""
    path = Path(settings.BASE_DIR) / "static" / "offline.html"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "This page isn't available offline" in content
    # The page must NOT pull external CSS/JS — its job is to render with
    # zero network access. Strip HTML comments before matching so the
    # rationale comment in the file (which spells out "no
    # <link rel=stylesheet>") doesn't trip the assertion.
    stripped = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    assert '<link rel="stylesheet"' not in stripped
    assert "<script src=" not in stripped


# ---------------------------------------------------------------------------
# Kill-switch SW (SNOW-373, spec §6.3 Mechanism B)
# ---------------------------------------------------------------------------


def test_serve_sw_kill_returns_200_with_correct_headers() -> None:
    """``/sw-kill.js`` returns 200 with the same headers as the real SW.

    Root scope + no-cache: once a client is on the kill-switch, we still
    need every subsequent visit to re-fetch so a config flip back to
    ``/sw.js`` picks up promptly (spec §6.4).
    """
    client = Client()
    response = client.get("/sw-kill.js")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/javascript")
    assert response["Service-Worker-Allowed"] == "/"
    assert response["Cache-Control"] == "no-cache"


def test_sw_kill_wipes_caches_and_indexeddb() -> None:
    """The kill-switch SW source contains the wipe + unregister contract.

    A runtime test isn't possible in pytest (SW only runs in a browser).
    Instead we verify the static contract that spec §6.3 requires: the
    activate handler references ``caches.delete``, ``indexedDB.deleteDatabase``,
    and ``registration.unregister``. Missing any of the three would let a
    poisoned cache or DB survive a kill-switch activation.
    """
    client = Client()
    response = client.get("/sw-kill.js")
    body = response.content
    assert b"activate" in body
    assert b"caches.delete" in body
    assert b"deleteDatabase" in body
    assert b"registration.unregister" in body


def test_sw_kill_has_no_fetch_handler() -> None:
    """The kill-switch SW deliberately has no ``fetch`` listener.

    Absence of a fetch handler makes the browser bypass the SW for all
    network requests — so the moment this SW activates, the user's page
    traffic goes straight to the network. Adding a fetch listener would
    defeat the point of the kill switch.
    """
    client = Client()
    response = client.get("/sw-kill.js")
    body = response.content.decode("utf-8")
    assert "addEventListener('fetch'" not in body
    assert 'addEventListener("fetch"' not in body


def test_sw_kill_file_exists_on_disk() -> None:
    """``static/js/sw-kill.js`` ships in the repo (not templated at request-time)."""
    path = Path(settings.BASE_DIR) / "static" / "js" / "sw-kill.js"
    assert path.exists()


# ---------------------------------------------------------------------------
# Dev shell-cache bypass (SNOW-585)
# ---------------------------------------------------------------------------


@override_settings(SW_DEV_SHELL_BYPASS=True)
def test_serve_sw_flips_dev_shell_bypass_to_true_when_setting_is_on() -> None:
    """``/sw.js`` carries ``DEV_SHELL_BYPASS = true`` when the setting is on."""
    client = Client()
    response = client.get("/sw.js")
    assert b"const DEV_SHELL_BYPASS = true;" in response.content
    assert b"const DEV_SHELL_BYPASS = false;" not in response.content


@override_settings(SW_DEV_SHELL_BYPASS=False)
def test_serve_sw_keeps_dev_shell_bypass_false_when_setting_is_off() -> None:
    """``/sw.js`` carries ``DEV_SHELL_BYPASS = false`` when the setting is off."""
    client = Client()
    response = client.get("/sw.js")
    assert b"const DEV_SHELL_BYPASS = false;" in response.content
    assert b"const DEV_SHELL_BYPASS = true;" not in response.content


@override_settings(SW_DEV_SHELL_BYPASS=True)
def test_serve_sw_kill_is_never_rewritten() -> None:
    """``/sw-kill.js`` is never substituted, even when the setting is on.

    ``_serve_sw_file``'s ``replacements`` parameter is only ever passed by
    ``serve_sw`` — ``serve_sw_kill`` must keep serving the file byte-for-byte.
    """
    client = Client()
    response = client.get("/sw-kill.js")
    assert b"DEV_SHELL_BYPASS" not in response.content


def test_sw_js_on_disk_still_carries_the_false_placeholder() -> None:
    """The on-disk ``sw.js`` literal stays ``false`` so a failed substitution fails safe.

    ``apps.public.views.serve_sw`` looks for the exact literal
    ``const DEV_SHELL_BYPASS = false;`` and replaces it — if this literal
    ever drifts (e.g. reformatted to different spacing), the substitution
    silently no-ops rather than raising, which is the intended fail-safe
    behaviour, but only as long as the on-disk default really is ``false``.
    """
    path = Path(settings.BASE_DIR) / "static" / "js" / "sw.js"
    content = path.read_text(encoding="utf-8")
    assert "const DEV_SHELL_BYPASS = false;" in content
