"""
tests/templates/includes/test_toast.py — Unit tests for the _toast.html partial.

Uses ``django.template.loader.render_to_string`` directly — no test client,
no database. Covers:

  - ARIA mapping: each of the four kinds → correct ``role`` and ``aria-live``.
  - CTA emission: ``cta_label`` + ``cta_href`` together → reload button with
    ``data-action="reload"``; either missing → button absent.
  - Dismiss emission: ``dismissible=True`` (and default) → dismiss button with
    ``data-action="dismiss"``; ``dismissible=False`` → button absent.
  - ``data-action`` attributes in the full SW-banner shape (kind=info, CTA,
    dismissible).
  - Kind token interpolation: ``bg-status-{kind}-bg`` and
    ``text-status-{kind}-text`` present in the rendered output.
  - ``static=True`` → ``inline-flex`` present, ``fixed`` and ``hidden`` absent.
  - ``static=False`` (default) → ``hidden`` and ``fixed`` both present.
  - ``id`` attribute only emitted when supplied.
"""

from __future__ import annotations

import pytest
from django.template.loader import render_to_string

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEMPLATE = "includes/_toast.html"


def render(context: dict) -> str:
    """Render _toast.html with the given context dict and return the HTML string."""
    return render_to_string(TEMPLATE, context)


# ---------------------------------------------------------------------------
# ARIA mapping
# ---------------------------------------------------------------------------


class TestAriaMapping:
    """role and aria-live are keyed off the kind parameter."""

    def test_error_uses_alert_assertive(self) -> None:
        """kind=error → role="alert" aria-live="assertive"."""
        html = render({"kind": "error", "body": "Oops."})
        assert 'role="alert"' in html
        assert 'aria-live="assertive"' in html

    def test_warning_uses_alert_assertive(self) -> None:
        """kind=warning → role="alert" aria-live="assertive"."""
        html = render({"kind": "warning", "body": "Watch out."})
        assert 'role="alert"' in html
        assert 'aria-live="assertive"' in html

    def test_info_uses_status_polite(self) -> None:
        """kind=info → role="status" aria-live="polite"."""
        html = render({"kind": "info", "body": "FYI."})
        assert 'role="status"' in html
        assert 'aria-live="polite"' in html

    def test_success_uses_status_polite(self) -> None:
        """kind=success → role="status" aria-live="polite"."""
        html = render({"kind": "success", "body": "Done."})
        assert 'role="status"' in html
        assert 'aria-live="polite"' in html

    def test_error_does_not_use_polite(self) -> None:
        """kind=error must not carry aria-live="polite"."""
        html = render({"kind": "error", "body": "Oops."})
        assert 'aria-live="polite"' not in html

    def test_info_does_not_use_assertive(self) -> None:
        """kind=info must not carry aria-live="assertive"."""
        html = render({"kind": "info", "body": "Note."})
        assert 'aria-live="assertive"' not in html


# ---------------------------------------------------------------------------
# CTA button emission
# ---------------------------------------------------------------------------


class TestCtaEmission:
    """CTA button is emitted only when both cta_label and cta_href are supplied."""

    def test_both_present_emits_reload_button(self) -> None:
        """cta_label + cta_href together → reload button with data-action="reload"."""
        html = render(
            {
                "kind": "info",
                "body": "Update available.",
                "cta_label": "Reload",
                "cta_href": "#",
            }
        )
        assert 'data-action="reload"' in html
        assert "Reload" in html

    def test_cta_label_missing_suppresses_button(self) -> None:
        """cta_href without cta_label → no reload button."""
        html = render({"kind": "info", "body": "Update.", "cta_href": "#"})
        assert 'data-action="reload"' not in html

    def test_cta_href_missing_suppresses_button(self) -> None:
        """cta_label without cta_href → no reload button."""
        html = render({"kind": "info", "body": "Update.", "cta_label": "Reload"})
        assert 'data-action="reload"' not in html

    def test_neither_cta_field_suppresses_button(self) -> None:
        """Neither cta_label nor cta_href → no reload button."""
        html = render({"kind": "info", "body": "Info."})
        assert 'data-action="reload"' not in html

    def test_reload_button_type_is_button(self) -> None:
        """CTA button carries type="button"."""
        html = render(
            {
                "kind": "info",
                "body": "Update.",
                "cta_label": "Reload",
                "cta_href": "#",
            }
        )
        assert 'type="button"' in html


# ---------------------------------------------------------------------------
# Dismiss button emission
# ---------------------------------------------------------------------------


class TestDismissEmission:
    """Dismiss button is gated by the dismissible parameter."""

    def test_dismissible_true_emits_dismiss_button(self) -> None:
        """dismissible=True → dismiss button with data-action="dismiss"."""
        html = render({"kind": "info", "body": "Note.", "dismissible": True})
        assert 'data-action="dismiss"' in html

    def test_dismissible_default_emits_dismiss_button(self) -> None:
        """Omitting dismissible defaults to True → dismiss button present."""
        html = render({"kind": "info", "body": "Note."})
        assert 'data-action="dismiss"' in html

    def test_dismissible_false_suppresses_dismiss_button(self) -> None:
        """dismissible=False → no dismiss button."""
        html = render({"kind": "error", "body": "Error.", "dismissible": False})
        assert 'data-action="dismiss"' not in html


# ---------------------------------------------------------------------------
# Full SW-banner shape (kind=info, CTA, dismissible)
# ---------------------------------------------------------------------------


class TestSwBannerShape:
    """Both data-action attributes are present in the full SW-update banner shape."""

    def test_sw_banner_has_both_actions(self) -> None:
        """SW-update shape → both data-action="reload" and data-action="dismiss"."""
        html = render(
            {
                "kind": "info",
                "body": "An updated version is available.",
                "cta_label": "Reload",
                "cta_href": "#",
                "dismissible": True,
            }
        )
        assert 'data-action="reload"' in html
        assert 'data-action="dismiss"' in html


# ---------------------------------------------------------------------------
# Kind token interpolation
# ---------------------------------------------------------------------------


class TestKindTokenInterpolation:
    """Status palette tokens are interpolated from the kind parameter."""

    @pytest.mark.parametrize("kind", ["error", "warning", "info", "success"])
    def test_bg_token_present(self, kind: str) -> None:
        """bg-status-{kind}-bg appears in the class attribute."""
        html = render({"kind": kind, "body": "Test."})
        assert f"bg-status-{kind}-bg" in html

    @pytest.mark.parametrize("kind", ["error", "warning", "info", "success"])
    def test_text_token_present(self, kind: str) -> None:
        """text-status-{kind}-text appears in the class attribute."""
        html = render({"kind": kind, "body": "Test."})
        assert f"text-status-{kind}-text" in html

    def test_error_bg_token(self) -> None:
        """bg-status-error-bg present for error kind."""
        html = render({"kind": "error", "body": "Oops."})
        assert "bg-status-error-bg" in html

    def test_info_text_token(self) -> None:
        """text-status-info-text present for info kind."""
        html = render({"kind": "info", "body": "Note."})
        assert "text-status-info-text" in html


# ---------------------------------------------------------------------------
# static flag
# ---------------------------------------------------------------------------


class TestStaticFlag:
    """static=True renders inline; static=False (default) renders fixed+hidden."""

    def test_static_true_uses_inline_flex(self) -> None:
        """static=True → inline-flex in class attribute."""
        html = render({"kind": "info", "body": "Note.", "static": True})
        assert "inline-flex" in html

    def test_static_true_omits_fixed(self) -> None:
        """static=True → fixed positioning utility absent."""
        html = render({"kind": "info", "body": "Note.", "static": True})
        assert "fixed" not in html

    def test_static_true_omits_hidden(self) -> None:
        """static=True → hidden utility absent."""
        html = render({"kind": "info", "body": "Note.", "static": True})
        assert "hidden" not in html

    def test_static_false_uses_hidden(self) -> None:
        """static=False (default) → hidden in class attribute."""
        html = render({"kind": "info", "body": "Note.", "static": False})
        assert "hidden" in html

    def test_static_false_uses_fixed(self) -> None:
        """static=False (default) → fixed positioning utility present."""
        html = render({"kind": "info", "body": "Note.", "static": False})
        assert "fixed" in html

    def test_default_static_uses_hidden(self) -> None:
        """Omitting static defaults to False → hidden present."""
        html = render({"kind": "info", "body": "Note."})
        assert "hidden" in html

    def test_default_static_uses_fixed(self) -> None:
        """Omitting static defaults to False → fixed present."""
        html = render({"kind": "info", "body": "Note."})
        assert "fixed" in html

    def test_default_static_omits_inline_flex(self) -> None:
        """Omitting static defaults to False → inline-flex absent."""
        html = render({"kind": "info", "body": "Note."})
        assert "inline-flex" not in html


# ---------------------------------------------------------------------------
# id attribute
# ---------------------------------------------------------------------------


class TestIdAttribute:
    """id is only emitted when supplied."""

    def test_id_emitted_when_supplied(self) -> None:
        """id="sw-update-banner" appears when id is passed."""
        html = render({"kind": "info", "body": "Note.", "id": "sw-update-banner"})
        assert ' id="sw-update-banner"' in html

    def test_id_omitted_when_not_supplied(self) -> None:
        """No id attribute when id is not passed.

        Uses a quoted prefix to avoid matching ``data-testid=``.
        """
        html = render({"kind": "info", "body": "Note."})
        # data-testid="toast" is always present; check no bare id="..." attribute.
        assert ' id="' not in html


# ---------------------------------------------------------------------------
# Static data-testid and data-kind
# ---------------------------------------------------------------------------


class TestDataAttributes:
    """data-testid and data-kind are always emitted."""

    def test_data_testid_always_present(self) -> None:
        """data-testid="toast" is always on the outer div."""
        html = render({"kind": "info", "body": "Note."})
        assert 'data-testid="toast"' in html

    @pytest.mark.parametrize("kind", ["error", "warning", "info", "success"])
    def test_data_kind_matches_kind(self, kind: str) -> None:
        """data-kind equals the kind parameter."""
        html = render({"kind": kind, "body": "Test."})
        assert f'data-kind="{kind}"' in html
