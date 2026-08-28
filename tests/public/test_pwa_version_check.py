"""
Server-side tests for the SNOW-374 client-side version-check contract.

The JavaScript half (``static/js/pwa_version_check.js``) is exercised by
Playwright in a later ticket; here we verify the server sends the client
everything it needs to run the check.

Covered:

* ``apps.public.context_processors.pwa_version`` returns the build string
  and passes it through untouched, alongside the human-readable release
  label the account menu shows (covered in full by
  ``tests/public/test_release_label.py``).
* Every page response bakes the current build into the
  ``<meta name="pwa-app-version">`` tag.
* The ``<meta name="pwa-app-min-version">`` tag is gone (SNOW-609) — there
  is no client-side floor to compare against any more.
* The blocking modal partial ships hidden on every page (revealed by JS).
* The version-check script is loaded on every page.
"""

from __future__ import annotations

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

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
    }


def test_context_processor_defaults_to_empty_string() -> None:
    """A missing setting is exposed as an empty string, not raised.

    Both of them: an unversioned build declares no build to the PWA check
    and shows no release in the menu, rather than either one raising.
    """
    with override_settings(APP_VERSION="", APP_RELEASE=""):
        result = pwa_version(HttpRequest())

    assert result == {"APP_VERSION": "", "APP_RELEASE_LABEL": ""}


@pytest.mark.django_db
@override_settings(APP_VERSION="2026.07.15.testbuild")
def test_meta_tag_present_on_home_page() -> None:
    """Home page bakes the version tag into the shell."""
    response = Client().get("/")
    body = response.content.decode("utf-8")

    assert '<meta name="pwa-app-version" content="2026.07.15.testbuild">' in body


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
def test_version_check_script_loaded_on_home_page() -> None:
    """The version-check JS is referenced from the home page shell."""
    response = Client().get("/")
    body = response.content.decode("utf-8")

    assert "pwa_version_check.js" in body
