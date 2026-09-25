"""
Server-side tests for the SNOW-374 client-side version-check contract.

The JavaScript half (``static/js/pwa_version_check.js``) is exercised by
Playwright in a later ticket; here we verify the server sends the client
everything it needs to run the check.

Covered:

* ``apps.public.context_processors.pwa_version`` returns the build string
  and passes it through untouched, alongside the human-readable release
  label the site footer shows (covered in full by
  ``tests/public/test_release_label.py``).
* Every page response bakes the current build into the
  ``<meta name="pwa-app-version">`` tag.
* The ``<meta name="pwa-app-release">`` tag is gone (SNOW-1026) — SNOW-1025
  removed the banner copy that read it.
* The ``<meta name="pwa-app-min-version">`` tag is gone (SNOW-609) — there
  is no client-side floor to compare against any more.
* The blocking modal partial ships hidden on every page (revealed by JS).
* The version-check script is loaded on every page.
"""

from __future__ import annotations

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from apps.core.sw_shell import cached_cache_version
from apps.public.context_processors import pwa_version


def test_context_processor_returns_configured_values() -> None:
    """The context processor exposes the build setting verbatim as a string.

    ``APP_RELEASE`` is pinned here only to keep the assertion exact — the
    label's own rules live in ``tests/public/test_release_label.py``.
    """
    with override_settings(
        APP_VERSION="2026.07.15.abcdef",
        APP_RELEASE="24",
        SITE_ENVIRONMENT="production",
    ):
        result = pwa_version(HttpRequest())

    assert result == {
        "APP_VERSION": "2026.07.15.abcdef",
        "APP_RELEASE_LABEL": "v24",
        "PWA_SHELL": cached_cache_version(),
    }


def test_context_processor_defaults_to_empty_string() -> None:
    """A missing setting is exposed as an empty string, not raised.

    Both of them: an unversioned build declares no build to the PWA check
    and shows no release in the menu, rather than either one raising.
    """
    with override_settings(APP_VERSION="", APP_RELEASE=""):
        result = pwa_version(HttpRequest())

    assert result == {
        "APP_VERSION": "",
        "APP_RELEASE_LABEL": "",
        "PWA_SHELL": cached_cache_version(),
    }


@pytest.mark.django_db
@override_settings(APP_VERSION="2026.07.15.testbuild")
def test_meta_tag_present_on_home_page() -> None:
    """Home page bakes the version tag into the shell."""
    response = Client().get("/")
    body = response.content.decode("utf-8")

    assert '<meta name="pwa-app-version" content="2026.07.15.testbuild">' in body


@pytest.mark.django_db
def test_shell_meta_tag_matches_the_served_worker() -> None:
    """The page names the shell the worker ``/sw.js`` serves (SNOW-1027).

    ``sw_register.js`` compares this with a waiting worker's own
    ``CACHE_VERSION`` to decide whether the page already IS that worker's
    build, and so whether the worker can take over at once. If the two were
    computed differently the comparison would never match, and every fresh
    tab would fall back to waiting until hidden, silently.
    """
    client = Client()
    page = client.get("/").content.decode("utf-8")
    worker = client.get("/sw.js").content.decode("utf-8")

    shell = cached_cache_version()
    assert f'<meta name="pwa-shell" content="{shell}">' in page
    assert f"const CACHE_VERSION = '{shell}';" in worker


@pytest.mark.django_db
@override_settings(APP_RELEASE="30")
def test_release_meta_tag_absent() -> None:
    """The shell no longer carries ``<meta name="pwa-app-release">`` (SNOW-1026).

    SNOW-1025 removed the update banner's versioned copy, which was the
    tag's only reader. The footer still renders the label, so the page
    names the release once, in the footer, and nowhere in the head.
    """
    response = Client().get("/")
    body = response.content.decode("utf-8")

    assert 'name="pwa-app-release"' not in body


@pytest.mark.django_db
@override_settings(APP_VERSION="dev")
def test_min_version_meta_tag_is_gone() -> None:
    """The shell no longer carries a client-side floor (SNOW-609).

    The tag fed a string-inequality comparison against a git SHA, which
    read every client as below the floor. There is nothing for the client
    to compare any more — ``/api/version`` returns the verdict itself.
    """
    response = Client().get("/")
    body = response.content.decode("utf-8")

    assert '<meta name="pwa-app-version" content="dev">' in body
    assert "pwa-app-min-version" not in body


@pytest.mark.django_db
def test_blocking_modal_ships_hidden_on_home_page() -> None:
    """The modal container renders on every page, gated on the ``hidden`` class."""
    response = Client().get("/")
    body = response.content.decode("utf-8")

    assert 'id="pwa-update-modal"' in body
    # ``hidden`` (Tailwind's ``display: none`` utility) must be on the
    # container by default — the JS strips it to reveal.
    assert "hidden fixed inset-0" in body


@pytest.mark.django_db
def test_blocking_modal_says_nothing_about_local_state() -> None:
    """The modal neither announces a wipe nor reassures against one (SNOW-869).

    Its copy used to say the reload "refreshes the offline copy of the
    app" and then add that downloaded maps and saved data were kept. The
    second half only existed because the first half raised the alarm —
    "clears" on a mostly-offline app reads as "deletes my 500 MB of
    downloaded maps" — so both halves went together. The click clears
    ``snowdesk-shell-*`` / ``map-shell-*`` only, which is code rather
    than anything the user owns.

    Asserted as an absence across the rendered page, matching the banner's
    equivalent test, so reintroducing either half anywhere in the modal
    fails here.
    """
    body = Client().get("/").content.decode("utf-8")
    modal = body[body.index('id="pwa-update-modal"') :]
    modal = modal[: modal.index("</div>", modal.index("Reload now"))]

    assert "are kept" not in modal
    assert "downloaded maps" not in modal
    assert "offline copy" not in modal
    # The half that must stay: what is wrong, and what to do about it.
    assert "no longer supported" in modal
    assert "Reload to continue" in modal


@pytest.mark.django_db
def test_version_check_script_loaded_on_home_page() -> None:
    """The version-check JS is referenced from the home page shell."""
    response = Client().get("/")
    body = response.content.decode("utf-8")

    assert "pwa_version_check.js" in body
