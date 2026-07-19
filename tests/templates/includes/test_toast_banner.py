"""
tests/templates/includes/test_toast_banner.py — Unit tests for the _toast_banner.html partial.

Uses ``django.template.loader.render_to_string`` directly — no test client,
no database. Covers:

  - Default body copy when ``body`` is omitted; custom body overrides it.
  - ``role="alert"`` / ``aria-live="assertive"`` are always present (unlike
    _toast.html, this partial has only one severity — a permanently-failed
    mutation).
  - ``data-testid="mutation-queue-toast"`` is always emitted.
  - ``id`` defaults to ``"mutation-queue-toast"`` (the id
    static/js/mutation_queue.js looks up) and can be overridden.
  - Status-error design tokens (``bg-status-error-bg`` /
    ``text-status-error-text``) — never raw palette utilities.
  - Hidden-by-default + the reveal-transition utility classes are present
    so JS can toggle them without any custom CSS.
  - The "×" dismiss button carries ``data-action="dismiss"`` and an
    accessible label.
"""

from __future__ import annotations

from django.template.loader import render_to_string

TEMPLATE = "includes/_toast_banner.html"


def render(context: dict | None = None) -> str:
    """Render _toast_banner.html with the given context dict and return the HTML."""
    return render_to_string(TEMPLATE, context or {})


class TestBodyCopy:
    """Body text falls back to a generic message; a supplied body overrides it."""

    def test_default_body_present_when_omitted(self) -> None:
        """Omitting body renders the generic fallback copy."""
        html = render()
        assert "couldn&#x27;t be saved" in html or "couldn't be saved" in html

    def test_custom_body_overrides_default(self) -> None:
        """A supplied body replaces the generic fallback copy."""
        html = render({"body": "Custom failure copy."})
        assert "Custom failure copy." in html
        assert "couldn't be saved" not in html


class TestAria:
    """This partial has a single severity — always alert/assertive."""

    def test_role_alert(self) -> None:
        """role="alert" is always present."""
        html = render()
        assert 'role="alert"' in html

    def test_aria_live_assertive(self) -> None:
        """aria-live="assertive" is always present."""
        html = render()
        assert 'aria-live="assertive"' in html


class TestDataAttributes:
    """data-testid is always emitted on the outer element."""

    def test_data_testid_present(self) -> None:
        """data-testid="mutation-queue-toast" is always on the outer div."""
        html = render()
        assert 'data-testid="mutation-queue-toast"' in html


class TestIdAttribute:
    """id defaults to mutation-queue-toast; callers can override it."""

    def test_id_defaults_to_mutation_queue_toast(self) -> None:
        """Omitting id defaults to the id the JS looks up."""
        html = render()
        assert 'id="mutation-queue-toast"' in html

    def test_id_override(self) -> None:
        """A supplied id overrides the default.

        Uses a quoted prefix to avoid matching ``data-testid="..."``.
        """
        html = render({"id": "mutation-queue-toast--demo"})
        assert 'id="mutation-queue-toast--demo"' in html
        assert ' id="mutation-queue-toast"' not in html


class TestDesignTokens:
    """Status-error design tokens, never raw Tailwind palette utilities."""

    def test_bg_status_error_token(self) -> None:
        """bg-status-error-bg is present."""
        html = render()
        assert "bg-status-error-bg" in html

    def test_text_status_error_token(self) -> None:
        """text-status-error-text is present."""
        html = render()
        assert "text-status-error-text" in html

    def test_no_raw_palette_utility(self) -> None:
        """No raw Tailwind red-* palette utility sneaks in instead of the token."""
        html = render()
        assert "bg-red-" not in html
        assert "text-red-" not in html


class TestRevealClasses:
    """Hidden by default; reveal-transition utility classes are present for JS to toggle."""

    def test_hidden_by_default(self) -> None:
        """hidden is present so the toast starts invisible."""
        html = render()
        assert "hidden" in html

    def test_fixed_top_positioning(self) -> None:
        """Positioned fixed at the top of the viewport, not the bottom."""
        html = render()
        assert "fixed" in html
        assert "top-0" in html

    def test_full_width(self) -> None:
        """Spans the full viewport width (inset-x-0 / w-full), unlike _toast.html's
        max-w-md pill shape.
        """
        html = render()
        assert "inset-x-0" in html
        assert "w-full" in html

    def test_transition_utility_present(self) -> None:
        """A Tailwind transition utility drives the reveal — no custom CSS."""
        html = render()
        assert "transition-all" in html

    def test_initial_transform_and_opacity_present(self) -> None:
        """Starts translated above the viewport and fully transparent."""
        html = render()
        assert "-translate-y-full" in html
        assert "opacity-0" in html


class TestDismissButton:
    """The dismiss button carries data-action="dismiss" and an accessible label."""

    def test_dismiss_action_present(self) -> None:
        """data-action="dismiss" is present on the close button."""
        html = render()
        assert 'data-action="dismiss"' in html

    def test_dismiss_aria_label(self) -> None:
        """The dismiss button carries an aria-label."""
        html = render()
        assert 'aria-label="Dismiss"' in html
