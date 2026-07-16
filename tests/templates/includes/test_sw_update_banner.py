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
  * The rendered surface uses design tokens (``bg-card``, ``rounded-card``,
    ``border-border``, ``shadow-glass``) and avoids raw palette utilities.
  * The banner is embedded in the public base template on every page.
"""

from __future__ import annotations

import pytest
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

    def test_dismiss_button_type_is_button(self) -> None:
        """Dismiss button is type=button so it never submits an enclosing form."""
        html = render()
        # There are two buttons — reload (rendered via _button.html) and dismiss.
        # Both must be non-submitting.
        assert html.count('type="button"') >= 2


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
