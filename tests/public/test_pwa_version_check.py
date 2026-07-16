"""
Server-side tests for the SNOW-374 client-side version-check contract.

The JavaScript half (``static/js/pwa_version_check.js``) is exercised by
Playwright in a later ticket; here we verify the server sends the client
everything it needs to run the check.

Covered:

* ``public.context_processors.pwa_version`` returns the two build strings
  and passes them through untouched.
* Every page response bakes the current build into the
  ``<meta name="pwa-app-version">`` tag.
* Every page response bakes the min-version into the
  ``<meta name="pwa-app-min-version">`` tag (empty content when the
  setting is empty — the client treats "" as "no floor enforced").
* The blocking modal partial ships hidden on every page (revealed by JS).
* The version-check script is loaded on every page.
"""

from __future__ import annotations

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from public.context_processors import pwa_version


def test_context_processor_returns_configured_values() -> None:
    """The context processor exposes both settings verbatim as strings."""
    with override_settings(
        APP_VERSION="2026.07.15.abcdef", APP_MIN_VERSION="2026.07.01"
    ):
        result = pwa_version(HttpRequest())

    assert result == {
        "APP_VERSION": "2026.07.15.abcdef",
        "APP_MIN_VERSION": "2026.07.01",
    }


def test_context_processor_defaults_to_empty_strings() -> None:
    """Missing settings are exposed as empty strings, not raised."""
    with override_settings(APP_VERSION="", APP_MIN_VERSION=""):
        result = pwa_version(HttpRequest())

    assert result == {"APP_VERSION": "", "APP_MIN_VERSION": ""}


@pytest.mark.django_db
@override_settings(
    APP_VERSION="2026.07.15.testbuild", APP_MIN_VERSION="2026.07.01.floor"
)
def test_meta_tags_present_on_home_page() -> None:
    """Home page bakes both version tags into the shell."""
    response = Client().get("/")
    body = response.content.decode("utf-8")

    assert '<meta name="pwa-app-version" content="2026.07.15.testbuild">' in body
    assert '<meta name="pwa-app-min-version" content="2026.07.01.floor">' in body


@pytest.mark.django_db
@override_settings(APP_VERSION="dev", APP_MIN_VERSION="")
def test_empty_min_version_still_ships_the_tag() -> None:
    """Empty min-version renders as an empty ``content=""`` — never omitted.

    The client relies on the tag being present so it can distinguish
    "server declared no floor" from "an ancient server that doesn't know
    about the header". Same rationale as the response-header middleware.
    """
    response = Client().get("/")
    body = response.content.decode("utf-8")

    assert '<meta name="pwa-app-version" content="dev">' in body
    assert '<meta name="pwa-app-min-version" content="">' in body


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
