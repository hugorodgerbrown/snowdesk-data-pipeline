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

import pytest
from django.conf import settings
from django.test import Client, override_settings

from apps.core import sw_shell


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


def _offline_page_source() -> str:
    """Return ``static/offline.html`` with its HTML comments stripped.

    The file's own rationale comment quotes the markup it forbids (``no
    <link rel=stylesheet>``), so every content assertion below matches
    against the comment-free source rather than the raw bytes.
    """
    path = Path(settings.BASE_DIR) / "static" / "offline.html"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)


def test_offline_fallback_page_exists_on_disk() -> None:
    """``static/offline.html`` ships and contains the expected heading (SNOW-118)."""
    stripped = _offline_page_source()
    assert "This page isn't available offline" in stripped
    # The page must not pull external CSS — its job is to render with zero
    # network access.
    assert '<link rel="stylesheet"' not in stripped


def test_offline_fallback_page_loads_only_the_reset_script() -> None:
    """The one permitted subresource is ``pwa_reset.js`` (SNOW-378 / SNOW-607).

    The page originally carried no external asset at all. It now carries
    exactly one, because §12.7's escape hatch has to reach a user whose
    only reachable page is this one, and the escape hatch is
    ``pwa_reset.js`` — restating the six-step wipe inline would be a
    second implementation to keep in step with the first.

    The list is asserted exhaustively so a future edit can't quietly add a
    second subresource: anything beyond the reset script is an asset the
    page has no way to fetch when it is doing its job.
    """
    stripped = _offline_page_source()
    assert re.findall(r'<script[^>]+src="([^"]+)"', stripped) == [
        "/static/js/pwa_reset.js"
    ]


def test_offline_fallback_page_carries_the_reset_trigger() -> None:
    """The escape hatch ships on the offline page, hidden until it is wired.

    ``data-pwa-reset-trigger`` is ``pwa_reset.js``'s binding contract — the
    same one the manage page uses (which since SNOW-607 is ``@never_cache``
    and so never loads offline). The panel ships ``hidden``: the reset
    itself needs no network, but the script that runs it is a subresource,
    and a trigger bound to nothing is worse than no trigger on a recovery
    page. See the header comment in ``static/offline.html``.
    """
    stripped = _offline_page_source()
    assert "data-pwa-reset-trigger" in stripped
    assert re.search(r'<div[^>]+id="pwa-reset-panel"[^>]+hidden', stripped)
    # The wipe belongs to pwa_reset.js alone.
    assert "deleteDatabase" not in stripped


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

    ``serve_sw`` performs the ``DEV_SHELL_BYPASS`` substitution on its own
    response, after calling the shared ``_serve_sw_file`` helper —
    ``serve_sw_kill`` calls that same helper but never runs the
    substitution, so ``/sw-kill.js`` must keep serving the file
    byte-for-byte.
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


# ---------------------------------------------------------------------------
# Derived CACHE_VERSION substitution (SNOW-590)
# ---------------------------------------------------------------------------


def test_serve_sw_substitutes_the_derived_cache_version() -> None:
    """``/sw.js`` ships the hash-derived cache name, never the placeholder.

    The committed literal is inert; ``serve_sw`` rewrites it per response.
    If this regressed, every client would share one frozen cache name and
    stop picking up shell changes — the SNOW-457 regression.
    """
    response = Client().get("/sw.js")
    body = response.content.decode("utf-8")

    assert f"const CACHE_VERSION = '{sw_shell.cache_version()}';" in body
    assert "UNSUBSTITUTED" not in body


@override_settings(DEBUG=True)
def test_serve_sw_recomputes_the_cache_version_under_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under DEBUG the version is recomputed per request.

    The autoreloader only restarts on ``.py`` edits, so a cached value
    would keep serving a stale cache name after every ``.js`` / ``.css`` /
    template change — exactly the edits that matter here.
    """
    monkeypatch.setattr(sw_shell, "compute_shell_hash", lambda: "0" * 64)
    first = Client().get("/sw.js").content.decode("utf-8")

    monkeypatch.setattr(sw_shell, "compute_shell_hash", lambda: "1" * 64)
    second = Client().get("/sw.js").content.decode("utf-8")

    assert "const CACHE_VERSION = 'snowdesk-shell-000000000000';" in first
    assert "const CACHE_VERSION = 'snowdesk-shell-111111111111';" in second


@override_settings(DEBUG=False)
def test_serve_sw_caches_the_cache_version_when_not_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With DEBUG off the version is computed once per process.

    ``/sw.js`` is served ``Cache-Control: no-cache``, so browsers
    revalidate it on every page load; re-hashing ~15 files per request
    would be pure waste. The tree is immutable between deploys, so the
    cached value cannot go stale in production.

    Note the test settings run with ``DEBUG=True``, so this branch is only
    reachable under an explicit override — without this test the
    production path would never be exercised at all.
    """
    sw_shell.cached_cache_version.cache_clear()
    monkeypatch.setattr(sw_shell, "compute_shell_hash", lambda: "0" * 64)
    first = Client().get("/sw.js").content.decode("utf-8")

    # A changed tree is deliberately NOT picked up until the process restarts.
    monkeypatch.setattr(sw_shell, "compute_shell_hash", lambda: "1" * 64)
    second = Client().get("/sw.js").content.decode("utf-8")

    assert "const CACHE_VERSION = 'snowdesk-shell-000000000000';" in first
    assert "const CACHE_VERSION = 'snowdesk-shell-000000000000';" in second

    sw_shell.cached_cache_version.cache_clear()


def test_serve_sw_kill_is_not_version_substituted() -> None:
    """``/sw-kill.js`` never runs the substitution — it has no shell cache.

    The kill switch must stay structurally immune to shell concerns, not
    merely immune by convention.
    """
    response = Client().get("/sw-kill.js")

    assert response.status_code == 200
    assert "snowdesk-shell-" not in response.content.decode("utf-8")


# ---------------------------------------------------------------------------
# Conditional requests (SNOW-622)
# ---------------------------------------------------------------------------
#
# ``Cache-Control: no-cache`` makes the browser revalidate on every page
# load, which is the contract these workers need. Without a validator there
# was nothing to revalidate against, so every load re-downloaded the whole
# script — ~2,000 lines for ``sw.js``.


def test_serve_sw_stamps_an_etag() -> None:
    """``/sw.js`` carries an ``ETag`` to revalidate against."""
    response = Client().get("/sw.js")

    assert response["ETag"]
    # Quoted, per RFC 9110 — an unquoted tag is not a valid entity-tag and
    # browsers ignore it, which would silently restore the old behaviour.
    assert response["ETag"].startswith('"')
    assert response["ETag"].endswith('"')


def test_serve_sw_answers_a_matching_if_none_match_with_304() -> None:
    """A client holding the current body gets a bodyless 304."""
    client = Client()
    first = client.get("/sw.js")

    second = client.get("/sw.js", headers={"if-none-match": first["ETag"]})

    assert second.status_code == 304
    assert second.content == b""


def test_the_304_repeats_the_headers_that_govern_the_cached_copy() -> None:
    """A 304 still carries the scope and revalidation rules.

    A worker re-served from the browser's cache needs its root scope and
    its revalidate-every-time rule as much as a freshly-downloaded one.
    """
    client = Client()
    first = client.get("/sw.js")

    second = client.get("/sw.js", headers={"if-none-match": first["ETag"]})

    assert second["Service-Worker-Allowed"] == "/"
    assert second["Cache-Control"] == "no-cache"
    assert second["ETag"] == first["ETag"]


def test_a_stale_if_none_match_gets_the_full_body() -> None:
    """A client holding an older body is served the new one."""
    response = Client().get("/sw.js", headers={"if-none-match": '"not-the-tag"'})

    assert response.status_code == 200
    assert b"addEventListener" in response.content


def test_the_etag_covers_the_substituted_body_not_the_file_on_disk() -> None:
    """The tag changes when the cache version does.

    ``serve_sw`` rewrites ``CACHE_VERSION`` into the body per response, and
    that substitution is exactly what makes a browser treat the worker as
    updated. An ``ETag`` computed over the on-disk file would be stable
    across a shell change and would serve a 304 for a worker the client
    ought to be replacing — the opposite of what SNOW-590 arranged.
    """
    client = Client()

    with override_settings(SW_DEV_SHELL_BYPASS=False):
        plain = client.get("/sw.js")
    with override_settings(SW_DEV_SHELL_BYPASS=True):
        bypassed = client.get("/sw.js")

    assert plain.content != bypassed.content, "fixture failed — bodies are identical"
    assert plain["ETag"] != bypassed["ETag"]


def test_serve_sw_kill_also_supports_conditional_requests() -> None:
    """The kill-switch worker gets the same treatment.

    It is served with the same no-cache contract, so it pays the same
    re-download on every visit without a validator.
    """
    client = Client()
    first = client.get("/sw-kill.js")
    assert first["ETag"]

    second = client.get("/sw-kill.js", headers={"if-none-match": first["ETag"]})
    assert second.status_code == 304
