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
  * The strings ``<template>`` carries the runtime-only copy (the busy
    state, and the versioned copy naming both builds) with the same keys
    the admin fallback declares.
  * The roundel draws ``includes/_icon_refresh.html`` — both arrowheads
    and both arcs — rather than the corrupted inline path it replaced.
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


class TestRuntimeStrings:
    """Copy for states that only exist after a click."""

    def test_strings_template_present(self) -> None:
        """The busy-state copy ships as markup so it reaches the catalogue."""
        html = render()
        assert 'id="sw-update-strings-template"' in html
        assert 'data-string="updating"' in html

    def test_versioned_copy_ships_with_named_placeholders(self) -> None:
        """The versioned strings carry ``%(name)s`` holes, never positional.

        ``pwaStrings.interpolate`` substitutes by name because a locale is
        free to reorder the two builds in the sentence; a positional
        substitution would silently swap "from" and "to".
        """
        html = render()
        assert 'data-string="update-title-versioned"' in html
        assert "%(version)s" in html
        assert 'data-string="update-body-versioned"' in html
        assert "%(current)s" in html
        assert "%(next)s" in html

    def test_unnumbered_copy_survives(self) -> None:
        """The original strings are the third state, not dead copy.

        They are what shows when ``/api/version`` is unreachable or when
        nothing distinguishes the two builds.
        """
        html = render()
        assert 'data-string="update-title"' in html
        assert 'data-string="update-body"' in html

    def test_no_copy_reassures_about_what_survives(self) -> None:
        """No string tells the user their downloads are safe (SNOW-869).

        SNOW-609 added "your downloaded maps and saved data are kept" to
        both the unnumbered and, later, the versioned body. It reads as
        care and works as alarm: naming the thing that survives is what
        raises the possibility that it might not, on a banner where
        nothing the user owns is at risk in the first place.

        The rule it was replaced with is a one-way door — warn before
        deleting something, say nothing otherwise — so this asserts the
        absence across the whole rendered partial rather than against the
        two strings that happened to carry it. A third copy site
        reintroducing the sentence fails here too.
        """
        html = render()

        assert "are kept" not in html
        assert "downloaded maps" not in html

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


class TestIcon:
    """The roundel draws the house refresh glyph, not an inline copy."""

    def test_draws_the_two_arc_cycle(self) -> None:
        """Both arrowheads and both arcs reach the page (SNOW-869).

        The inline SVG this replaced kept one arrowhead and one arc, and
        its ``L23 10`` ran as a straight chord back across the circle. All
        four subpaths of ``includes/_icon_refresh.html`` are asserted
        because it is the missing half that made the old mark unreadable.
        """
        html = render()
        assert '<polyline points="23 4 23 10 17 10"' in html
        assert '<polyline points="1 20 1 14 7 14"' in html
        assert 'd="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"' in html
        assert 'd="M20.49 15a9 9 0 0 1-14.85 3.36L1 14"' in html

    def test_the_corrupted_inline_path_is_gone(self) -> None:
        """The single-subpath arc must not come back.

        Named explicitly rather than left to the positive assertions
        above: it drew, it was wrong, and it survived for a year because
        nothing said what it should have been.
        """
        assert "M20.49 15A9 9 0 1 1 5.64 5.64L23 10" not in render()

    def test_icon_sits_in_the_spinnable_hook(self) -> None:
        """``data-overlay-icon`` is what ``sw_register.js`` spins."""
        assert "data-overlay-icon" in render()


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
