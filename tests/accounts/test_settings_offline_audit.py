"""
tests/accounts/test_settings_offline_audit.py — the offline-content audit
panel on /account/settings/ and on the offline fallback page (SNOW-907).

What is worth pinning here is everything the panel's two halves rely on
each other for, none of which any linter checks:

  * The settings page renders the panel AND loads both scripts. The row
    is markup with no behaviour of its own — forget a script tag and the
    button renders, binds to nothing, and reports nothing, silently.
  * The strings ``<template>`` id matches the one the module asks for.
    Whether the KEYS agree is tests/test_js_strings_are_translatable.py's
    job, for every surface at once — SNOW-907 taught its scan to follow a
    named fallback constant so it could cover this one too.
  * ``static/offline.html`` carries the same markup contract, loads the
    two modules by the unhashed paths the worker precaches, and ships the
    panel hidden behind a check that the mechanism arrived.
  * ``sw.js`` precaches exactly those paths, and they exist on disk. A
    typo here costs nothing at build time and everything at 200 metres
    underground.

The report's own arithmetic is tested in tests/js/test_offline_audit_core.js
and the collector in tests/js/test_offline_audit.js — neither belongs in a
Django test, and a browser is not needed for any of it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from tests.factories import AccountFactory

_TOKEN_BACKEND = "django.contrib.auth.backends.ModelBackend"

PARTIAL = "includes/_offline_audit_panel.html"
MODULE = Path(settings.BASE_DIR) / "static" / "js" / "offline_audit.js"
OFFLINE_PAGE = Path(settings.BASE_DIR) / "static" / "offline.html"
SERVICE_WORKER = Path(settings.BASE_DIR) / "static" / "js" / "sw.js"


def _client_for(account: Account) -> Client:
    """Return a test client signed in as the account's User."""
    client = Client()
    client.force_login(account.user, backend=_TOKEN_BACKEND)
    return client


class TestPartial:
    """The panel's markup contract with ``offline_audit.js``."""

    def test_root_carries_the_binding_attribute(self) -> None:
        """``data-offline-audit`` is what the module looks for."""
        assert "data-offline-audit " in render_to_string(PARTIAL, {})

    def test_root_carries_the_deployed_default_basemap(self) -> None:
        """
        SNOW-913: the report names the basemap on screen, and a visitor who
        has never opened the picker has stored no choice — the deployed
        default is the only thing that can stand in for it, and only a
        server can say what it is.
        """
        html = render_to_string(PARTIAL, {"default_basemap_key": "swisstopo_winter"})

        assert 'data-default-basemap-key="swisstopo_winter"' in html

    def test_the_default_basemap_attribute_is_empty_without_one(self) -> None:
        """
        Rendered empty rather than omitted, so the reader (``selectedBasemap``
        in offline_audit.js) gets a falsy value and names no current basemap
        instead of guessing at one.
        """
        assert 'data-default-basemap-key=""' in render_to_string(PARTIAL, {})

    def test_controls_carry_their_binding_attributes(self) -> None:
        """Run, Copy, Save and the status line are each found by attribute."""
        html = render_to_string(PARTIAL, {})
        for attribute in (
            "data-offline-audit-run",
            "data-offline-audit-copy",
            "data-offline-audit-save",
            "data-offline-audit-status",
            "data-offline-audit-output",
        ):
            assert attribute in html, attribute

    def test_copy_and_save_ship_hidden(self) -> None:
        """Neither is offered before there is a report to act on.

        Revealed by the module rather than by CSS: Copy needs a report to
        exist, and Save needs a connection, a controlling worker and a map
        page that is actually missing. All three are readings, not states
        of this markup.
        """
        html = render_to_string(PARTIAL, {})
        assert re.search(r"data-offline-audit-copy\s+hidden", html)
        assert re.search(r"data-offline-audit-save\s+hidden", html)

    def test_output_ships_hidden(self) -> None:
        """Nothing is painted until the user asks for the check."""
        assert re.search(
            r"data-offline-audit-output\s+hidden", render_to_string(PARTIAL, {})
        )

    def test_status_line_is_a_live_region(self) -> None:
        """Checking, copied and every failure are announced."""
        html = render_to_string(PARTIAL, {})
        assert 'role="status"' in html
        assert 'aria-live="polite"' in html


class TestStringsTemplate:
    """Server-translated copy, read back through ``pwaStrings.read``."""

    def test_template_id_matches_the_module(self) -> None:
        """The id the module passes to ``read()``.

        The keys inside it are checked in both directions by
        tests/test_js_strings_are_translatable.py, which does the same for
        every strings template in the tree.
        """
        html = render_to_string(PARTIAL, {})
        assert 'id="offline-audit-strings-template"' in html
        assert "offline-audit-strings-template" in MODULE.read_text()

    def test_placeholders_are_named_not_positional(self) -> None:
        """``%(name)s``, so a locale may reorder them.

        ``{% translate %}`` rather than ``{% blocktranslate %}``: the
        values are runtime JS, so there is nothing in the template context
        to interpolate, and blocktranslate would attempt the substitution
        itself.
        """
        html = render_to_string(PARTIAL, {})
        assert "%(name)s (drop zone)" in html
        assert "%s " not in html.replace("%(", "")


@pytest.mark.django_db
class TestSettingsPage:
    """The proactive entry point — the one to use before a journey."""

    def test_panel_renders_in_the_device_group(self) -> None:
        """The row sits in "This device", above the reset it precedes."""
        html = (
            _client_for(AccountFactory.create())
            .get(reverse("accounts:settings"))
            .content.decode()
        )
        assert 'data-testid="offline-audit-panel"' in html
        assert 'data-testid="settings-group-device"' in html

    def test_both_scripts_are_loaded(self) -> None:
        """The row is inert markup without them, and fails silently."""
        html = (
            _client_for(AccountFactory.create())
            .get(reverse("accounts:settings"))
            .content.decode()
        )
        assert "offline_audit_core" in html
        assert "offline_audit." in html

    def test_the_page_names_the_deployed_default_basemap(self) -> None:
        """
        SNOW-913: the view is the only half that can say what
        ``settings.BASEMAP`` is, and the report needs it for a visitor who
        has never opened the basemap picker.
        """
        html = (
            _client_for(AccountFactory.create())
            .get(reverse("accounts:settings"))
            .content.decode()
        )
        assert f'data-default-basemap-key="{settings.BASEMAP}"' in html

    def test_the_core_is_loaded_before_the_module(self) -> None:
        """``offline_audit.js`` calls into the core at bind time."""
        html = (
            _client_for(AccountFactory.create())
            .get(reverse("accounts:settings"))
            .content.decode()
        )
        assert html.index("offline_audit_core") < html.index("js/offline_audit.")


class TestOfflineFallbackPage:
    """The reactive entry point — the one that still loads with no signal."""

    def test_carries_the_same_markup_contract(self) -> None:
        """One module paints both hosts, so both declare the same attributes."""
        html = OFFLINE_PAGE.read_text()
        for attribute in (
            "data-offline-audit",
            "data-offline-audit-run",
            "data-offline-audit-copy",
            "data-offline-audit-status",
            "data-offline-audit-output",
        ):
            assert attribute in html, attribute

    def test_offers_no_save_control(self) -> None:
        """Saving the map page needs a connection this page proves is absent."""
        assert "data-offline-audit-save" not in OFFLINE_PAGE.read_text()

    def test_loads_the_modules_by_their_unhashed_paths(self) -> None:
        """The paths the worker precaches, which is what makes them resolve.

        Production serves hashed static URLs, so a ``{% static %}`` path
        here would name a file the shell cache never holds.
        """
        html = OFFLINE_PAGE.read_text()
        assert '"/static/js/offline_audit_core.js"' in html
        assert '"/static/js/offline_audit.js"' in html

    def test_panel_ships_hidden_until_the_mechanism_arrives(self) -> None:
        """A control bound to nothing is worse than absent on a recovery page."""
        html = OFFLINE_PAGE.read_text()
        assert 'id="offline-audit-panel"' in html
        assert re.search(r'id="offline-audit-panel"[^>]*\shidden', html)
        assert "window.pwaOfflineAudit" in html


class TestServiceWorkerPrecache:
    """The worker carries the audit onto the offline page, tolerantly."""

    def test_precaches_both_modules(self) -> None:
        """By the same unhashed paths ``offline.html`` requests."""
        source = SERVICE_WORKER.read_text()
        assert "'/static/js/offline_audit_core.js'" in source
        assert "'/static/js/offline_audit.js'" in source

    def test_both_files_exist_on_disk(self) -> None:
        """``cache.add`` on a 404 fails, and a typo costs nothing until it matters."""
        for name in ("offline_audit_core.js", "offline_audit.js"):
            assert (Path(settings.BASE_DIR) / "static" / "js" / name).is_file(), name

    def test_audit_scripts_are_not_in_the_atomic_precache_list(self) -> None:
        """A failed audit warm must never fail the install.

        ``PRECACHE_URLS`` goes through one ``cache.addAll``, where a single
        rejected entry fails ``install`` and leaves the device with no
        worker at all. Without the audit the panel stays hidden and every
        other offline path is untouched, so it is warmed separately.
        """
        source = SERVICE_WORKER.read_text()
        precache_line = next(
            line
            for line in source.splitlines()
            if line.startswith("const PRECACHE_URLS")
        )
        assert "offline_audit" not in precache_line
        assert "Promise.allSettled(AUDIT_SCRIPTS" in source
