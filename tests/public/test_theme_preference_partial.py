"""
tests/public/test_theme_preference_partial.py — the theme preference control.

Covers ``templates/includes/_theme_preference.html`` and its one caller, the
settings page's "This device" group.

The control is the write side of the ``theme`` localStorage key that
``templates/includes/theme_head.html`` reads. That key spent most of the
project's life readable but not writable: the setter shipped as
``window.__setTheme`` for a toggle button nobody built, and SNOW-615 removed
it as dead code. A value stored from a console call could then never be
changed or cleared by anything on the site.

What these tests pin is the part a template edit can silently break — that
all THREE options render. The resolver in theme_head.html has three states
(``dark``, ``light``, and key-absent → follow the OS), and a control offering
only two would strand the third the moment a user touched it: the key would
never be absent again, so "follow the OS" would become unreachable without
devtools. That is the original defect in milder form, and it is the kind of
regression a well-meaning simplification to a light/dark switch would
reintroduce without failing anything else.

The behaviour on top of this markup — which option is checked, what gets
written, and what happens when the OS setting changes — is client-side and
lives in tests/js/test_theme_preference.js. Nothing here needs a browser.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from tests.factories import UserFactory


@pytest.fixture()
def partial_html() -> str:
    """Render the partial on its own.

    No ``request=``, unlike tests/public/test_nav_partial.py: this partial
    takes no context and reads nothing off the request, so passing one would
    only pull in the context processors and their database work.
    """
    return render_to_string("includes/_theme_preference.html", {})


class TestThemePreferencePartial:
    """The partial's own markup contract."""

    @pytest.mark.parametrize("choice", ["system", "light", "dark"])
    def test_offers_every_state_the_resolver_understands(
        self, partial_html: str, choice: str
    ) -> None:
        """All three options render, one radio each.

        Dropping any one of these is the regression described in the module
        docstring — most damagingly "system", whose loss cannot be recovered
        from inside the app.
        """
        assert f'value="{choice}"' in partial_html

    def test_system_is_the_server_rendered_default(self, partial_html: str) -> None:
        """The System option ships checked, and the server cannot do better.

        The real preference is in localStorage, which the server never sees.
        "System" is the truthful default for a browser that has never chosen;
        static/js/theme_preference.js corrects it on load for one that has.
        """
        system_input = partial_html.split('value="system"')[1].split(">")[0]
        assert "checked" in system_input

    def test_options_are_real_radios_in_a_labelled_group(
        self, partial_html: str
    ) -> None:
        """A fieldset of radios, not a scripted control.

        Keyboard support, focus handling and the group label all come from
        the browser; a three-state control rolled out of buttons would have
        to re-implement each of them.
        """
        assert "<fieldset" in partial_html
        assert "<legend" in partial_html
        assert partial_html.count('type="radio"') == 3


@pytest.mark.django_db
class TestThemePreferenceOnSettings:
    """The control's one caller."""

    def test_renders_in_the_device_group(self, db: Any) -> None:
        """It ships on the settings page, inside "This device".

        Grouping is the claim being pinned: the theme is a property of this
        browser (it lives in localStorage beside "Reset local data on this
        device"), not of the account, so it belongs in that cluster and not
        under Account.
        """
        client = Client()
        client.force_login(UserFactory.create())
        html = client.get(reverse("accounts:settings")).content.decode()

        assert "data-theme-preference" in html
        device_group = html.index('data-testid="settings-group-device"')
        privacy_group = html.index('data-testid="settings-group-privacy"')
        control = html.index("data-theme-preference")
        assert device_group < control < privacy_group

    def test_script_is_loaded(self, db: Any) -> None:
        """The write side loads with the page.

        base.html does not carry it — this is the only page with the control
        — so the include and the script tag have to travel together.
        """
        client = Client()
        client.force_login(UserFactory.create())
        html = client.get(reverse("accounts:settings")).content.decode()

        assert "theme_preference.js" in html
