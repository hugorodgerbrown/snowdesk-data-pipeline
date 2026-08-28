"""
tests/templates/includes/test_sw_update_banner.py — Tests for
_sw_update_banner.html.

Covers:

  * The outer container ships hidden with the expected id, role, and
    aria-live attributes so ``sw_register.js`` /
    ``pwa_version_check.js`` can find and reveal it.
  * The reload button carries the id the JS looks up
    (``sw-update-banner-reload``) so a single ``getElementById`` covers
    both reveal paths.
  * The dismiss button carries ``data-action="dismiss"``.
  * Both controls take the pointer cursor — Tailwind v4's preflight gives
    ``<button>`` the default arrow, so a control that does not say
    otherwise reads as inert under the mouse.
  * The build line ships empty and hidden with the id ``sw_register.js``
    fills, and an ``aria-label``, since two git SHAs read aloud are not
    self-explanatory.
  * The strings ``<template>`` carries the runtime-only copy (the busy
    state) with the same keys the admin fallback declares.
  * The rendered surface uses design tokens (``bg-card``, ``rounded-card``,
    ``border-border``, ``shadow-glass``) and avoids raw palette utilities.
  * The banner is embedded in the public base template on every page.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from django.test import Client

TEMPLATE = "includes/_sw_update_banner.html"


def render() -> str:
    """Render the partial standalone."""
    return render_to_string(TEMPLATE, {})


class TestOuterContainer:
    """The outer container is the reveal target for both JS paths."""

    def test_id_is_sw_update_banner(self) -> None:
        """id="sw-update-banner" is the DOM handle the reveal JS uses."""
        assert 'id="sw-update-banner"' in render()

    def test_ships_hidden(self) -> None:
        """The ``hidden`` utility ships on the outer div so the banner starts closed."""
        html = render()
        # The outer container carries the hidden class; JS strips it to reveal.
        assert 'class="hidden fixed' in html

    def test_aria_live_polite(self) -> None:
        """role=status + aria-live=polite — the copy isn't urgent."""
        html = render()
        assert 'role="status"' in html
        assert 'aria-live="polite"' in html

    def test_aria_labelledby_wired(self) -> None:
        """aria-labelledby points at the title node so screen readers announce the heading."""
        html = render()
        assert 'aria-labelledby="sw-update-banner-title"' in html
        assert 'id="sw-update-banner-title"' in html


class TestActions:
    """Reload and dismiss buttons carry the identifiers the JS binds to."""

    def test_reload_button_id(self) -> None:
        """Reload button carries id="sw-update-banner-reload"."""
        assert 'id="sw-update-banner-reload"' in render()

    def test_dismiss_button_data_action(self) -> None:
        """Dismiss button carries data-action="dismiss"."""
        assert 'data-action="dismiss"' in render()

    def test_reload_button_takes_the_pointer_cursor(self) -> None:
        """The CTA says it is clickable.

        Tailwind v4's preflight leaves a ``<button>`` on the default arrow
        cursor, so every CTA rendered through ``_button.html`` read as
        inert under the mouse until ``_BUTTON_BASE`` said otherwise.
        """
        assert "cursor-pointer" in render()

    def test_dismiss_button_takes_the_pointer_cursor(self) -> None:
        """The × is a control too, and carries its own cursor class.

        It is not rendered through ``_button.html``, so the shared base
        class string does not reach it.
        """
        html = render()
        dismiss = html[html.index('data-action="dismiss"') :]
        assert "cursor-pointer" in dismiss[: dismiss.index("</button>")]

    def test_dismiss_button_type_is_button(self) -> None:
        """Dismiss button is type=button so it never submits an enclosing form."""
        html = render()
        # There are two buttons — reload (rendered via _button.html) and dismiss.
        # Both must be non-submitting.
        assert html.count('type="button"') >= 2


class TestBuildLine:
    """The third line naming the build on either side of the reload."""

    def test_slot_ships_empty_and_hidden(self) -> None:
        """Only the client knows both builds, so the server renders neither.

        The shell's build is in its own ``<meta>`` and the server's is in
        ``/api/version``; ``sw_register.js`` composes the line and unhides
        it. A slot that shipped visible would show an empty row under the
        copy on every reveal that could not resolve one.
        """
        html = render()
        slot = html[html.index('id="sw-update-banner-versions"') :]
        slot = slot[: slot.index("</p>")]
        assert "hidden" in slot
        assert slot.rstrip().endswith(">")

    def test_slot_is_labelled(self) -> None:
        """An accessible name, because "a1b2c3d → e4f5g6h" read aloud is not one.

        The label is prose and therefore belongs in the template, where
        ``makemessages`` can see it — not in the JavaScript that fills the
        values in.
        """
        assert 'aria-label="Build version"' in render()


class TestRuntimeStrings:
    """Copy for states that only exist after a click."""

    def test_strings_template_present(self) -> None:
        """The busy-state copy ships as markup so it reaches the catalogue."""
        html = render()
        assert 'id="sw-update-strings-template"' in html
        assert 'data-string="updating"' in html

    def test_keys_match_the_admin_fallback(self) -> None:
        """Both copies of this template feed the same ``pwaStrings.read``.

        ``sw_register.js`` reads whichever template the page carries — this
        one on public pages, the one in ``admin/base_site.html`` on admin
        pages. A key in one and not the other is a string that silently
        ships as English on half the site.
        """
        public_keys = set(re.findall(r'data-string="([^"]+)"', render()))
        admin_keys = set(
            re.findall(
                r'data-string="([^"]+)"',
                (
                    Path(settings.BASE_DIR) / "templates" / "admin" / "base_site.html"
                ).read_text(encoding="utf-8"),
            )
        )

        assert public_keys == admin_keys


class TestDesignTokens:
    """The banner uses design tokens rather than raw palette utilities."""

    def test_uses_card_surface(self) -> None:
        """bg-card / text-text-1 make the surface theme-aware."""
        html = render()
        assert "bg-card" in html
        assert "text-text-1" in html

    def test_uses_rounded_card(self) -> None:
        """rounded-card — no raw radius literals."""
        assert "rounded-card" in render()

    def test_uses_border_border(self) -> None:
        """border-border — no raw palette border."""
        assert "border-border" in render()


@pytest.mark.django_db
class TestBaseTemplateIntegration:
    """The partial is embedded on every public page via base.html."""

    def test_banner_present_on_home(self) -> None:
        """Home page ships the banner so the JS can reveal it."""
        response = Client().get("/")
        body = response.content.decode("utf-8")
        assert 'id="sw-update-banner"' in body
        assert 'id="sw-update-banner-reload"' in body
