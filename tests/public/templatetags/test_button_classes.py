"""
tests/public/templatetags/test_button_classes.py — the shared button chrome.

Every CTA on the site is rendered through ``includes/_button.html``, which
builds its class string here. The one thing pinned in this file is the
affordance: Tailwind v4's preflight gives ``<button>`` the DEFAULT cursor,
not the pointer, so a native button reads as inert under the mouse unless
the class string says otherwise — and nothing did. The map's icon controls
learned this separately (SNOW-658's ``hover-affordance``, whose own comment
says so); the ordinary button did not, which is how the update toast's
Reload button came to sit under an arrow.

The disabled pair goes with it. A button JS disables mid-action — the same
Reload, while the update applies — has to look unavailable rather than
merely unresponsive.
"""

from __future__ import annotations

import pytest

from apps.public.templatetags.components import button_classes


class TestPointerAffordance:
    """Every variant and size says it is clickable."""

    @pytest.mark.parametrize("variant", ["primary", "secondary", "ghost"])
    def test_variant_takes_the_pointer_cursor(self, variant: str) -> None:
        """The cursor is part of the shared base, not of any one variant."""
        assert "cursor-pointer" in button_classes(variant=variant)

    @pytest.mark.parametrize("size", ["standard", "compact"])
    def test_size_takes_the_pointer_cursor(self, size: str) -> None:
        """Size changes padding and nothing about the affordance."""
        assert "cursor-pointer" in button_classes(size=size)

    def test_anchor_form_keeps_it_too(self) -> None:
        """Harmless on an ``<a>``, which has the pointer already.

        Worth pinning anyway: the class string is shared, and a future
        edit that made the cursor conditional on the element type would be
        solving a problem that does not exist.
        """
        assert "cursor-pointer" in button_classes(is_anchor=True)


class TestDisabledState:
    """A disabled button looks unavailable, not broken."""

    def test_cursor_says_not_allowed(self) -> None:
        """``disabled:cursor-not-allowed`` — matching ``.hover-affordance``."""
        assert "disabled:cursor-not-allowed" in button_classes()

    def test_dimmed_while_disabled(self) -> None:
        """The one visual change that survives at a glance on a small toast."""
        assert "disabled:opacity-60" in button_classes()
