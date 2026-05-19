"""
tests/public/test_components_partials.py — Tests for the new component partials.

Covers:
  _button.html     — all three variants × both sizes, full_width, is_anchor,
                     extra_classes, extra_attrs, button_type default.
  _card.html       — chrome classes present, padding forwarded, center flag,
                     extra classes, inner content preserved.
  _status_page.html — rendered via real child templates (manage_sent,
                     manage_saved, link_expired, unsubscribe_done) using the
                     test client; asserts on structural chrome and content.

Follows the pattern in tests/public/test_nav_partial.py: render_to_string
for partials, test client for full-page templates.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import Client, RequestFactory

from public.templatetags.components import (
    _button_chrome_classes,
    _card_chrome_classes,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def rf() -> RequestFactory:
    """Return a Django RequestFactory."""
    return RequestFactory()


@pytest.fixture()
def anon_request(rf: RequestFactory):
    """Return a GET request with an anonymous user attached."""
    request = rf.get("/")
    request.user = AnonymousUser()
    return request


# ---------------------------------------------------------------------------
# Helper function unit tests (pure Python, no DB)
# ---------------------------------------------------------------------------


class TestButtonChromeClasses:
    """Unit tests for the _button_chrome_classes helper."""

    def test_primary_standard_anchor(self) -> None:
        """Primary standard anchor includes bg-text-1, size and inline-block."""
        cls = _button_chrome_classes("primary", "standard", False, True)
        assert "bg-text-1" in cls
        assert "text-card" in cls
        assert "px-5 py-2.5" in cls
        assert "inline-block" in cls
        assert "w-full" not in cls

    def test_primary_compact_button(self) -> None:
        """Primary compact button uses smaller padding, no inline-block."""
        cls = _button_chrome_classes("primary", "compact", False, False)
        assert "px-4 py-2" in cls
        assert "inline-block" not in cls

    def test_secondary_standard(self) -> None:
        """Secondary variant includes border-text-1 and bg-card."""
        cls = _button_chrome_classes("secondary", "standard", False, True)
        assert "bg-card" in cls
        assert "border-text-1" in cls
        assert "text-text-1" in cls

    def test_ghost_standard(self) -> None:
        """Ghost variant includes border-text-3/30 and text-text-2."""
        cls = _button_chrome_classes("ghost", "standard", False, False)
        assert "border-text-3/30" in cls
        assert "text-text-2" in cls

    def test_full_width(self) -> None:
        """full_width=True appends w-full."""
        cls = _button_chrome_classes("primary", "standard", True, False)
        assert "w-full" in cls

    def test_unknown_variant_falls_back_to_primary(self) -> None:
        """An unrecognised variant name falls back to primary styling."""
        cls = _button_chrome_classes("nonexistent", "standard", False, False)
        assert "bg-text-1" in cls


class TestCardChromeClasses:
    """Unit tests for the _card_chrome_classes helper."""

    def test_default_chrome_present(self) -> None:
        """Default call produces all four chrome classes."""
        cls = _card_chrome_classes("p-6", False, "")
        assert "bg-card" in cls
        assert "rounded-card" in cls
        assert "border-text-3/10" in cls
        assert "shadow-sm" in cls
        assert "p-6" in cls

    def test_center_adds_text_center(self) -> None:
        """center=True appends text-center."""
        cls = _card_chrome_classes("p-6", True, "")
        assert "text-center" in cls

    def test_extra_appended(self) -> None:
        """extra string is appended verbatim."""
        cls = _card_chrome_classes("p-4", False, "mb-3 passkey-card")
        assert "mb-3" in cls
        assert "passkey-card" in cls

    def test_no_center_by_default(self) -> None:
        """center=False does not produce text-center."""
        cls = _card_chrome_classes("p-8", False, "")
        assert "text-center" not in cls


# ---------------------------------------------------------------------------
# _button.html partial rendering
# ---------------------------------------------------------------------------


class TestButtonPartial:
    """Tests for the _button.html partial template."""

    def test_primary_anchor_renders_a_tag(self) -> None:
        """href supplied → renders an <a> with primary classes and inline-block."""
        html = render_to_string(
            "includes/_button.html",
            {"label": "Click me", "href": "/foo/", "variant": "primary"},
        )
        assert "<a" in html
        assert 'href="/foo/"' in html
        assert "bg-text-1" in html
        assert "text-card" in html
        assert "inline-block" in html
        assert "<button" not in html

    def test_primary_button_renders_button_tag(self) -> None:
        """No href → renders a <button> with type=submit."""
        html = render_to_string(
            "includes/_button.html",
            {"label": "Submit", "variant": "primary"},
        )
        assert "<button" in html
        assert "submit" in html
        assert "<a" not in html
        assert "inline-block" not in html

    def test_secondary_anchor(self) -> None:
        """secondary variant anchor includes border-text-1."""
        html = render_to_string(
            "includes/_button.html",
            {"label": "Map", "href": "/map/", "variant": "secondary"},
        )
        assert "border-text-1" in html
        assert "bg-card" in html

    def test_ghost_standard(self) -> None:
        """ghost variant button includes border-text-3/30."""
        html = render_to_string(
            "includes/_button.html",
            {"label": "Passkey", "variant": "ghost"},
        )
        assert "border-text-3/30" in html
        assert "text-text-2" in html

    def test_compact_size(self) -> None:
        """size=compact uses px-4 py-2 padding."""
        html = render_to_string(
            "includes/_button.html",
            {"label": "Subscribe", "variant": "primary", "size": "compact"},
        )
        assert "px-4 py-2" in html
        assert "px-5 py-2.5" not in html

    def test_full_width(self) -> None:
        """full_width=True adds w-full."""
        html = render_to_string(
            "includes/_button.html",
            {"label": "Confirm", "variant": "primary", "full_width": True},
        )
        assert "w-full" in html

    def test_extra_classes_appended(self) -> None:
        """extra_classes value is appended to the class attribute."""
        html = render_to_string(
            "includes/_button.html",
            {
                "label": "Subscribe",
                "variant": "primary",
                "size": "compact",
                "extra_classes": "whitespace-nowrap",
            },
        )
        assert "whitespace-nowrap" in html

    def test_extra_attrs_rendered(self) -> None:
        """extra_attrs string is injected verbatim into the element."""
        html = render_to_string(
            "includes/_button.html",
            {
                "label": "Passkey",
                "variant": "ghost",
                "extra_attrs": 'id="btn-passkey" type="button"',
            },
        )
        assert 'id="btn-passkey"' in html
        assert 'type="button"' in html

    def test_button_type_default_is_submit(self) -> None:
        """button_type defaults to submit when not supplied."""
        html = render_to_string(
            "includes/_button.html",
            {"label": "Go", "variant": "primary"},
        )
        assert "submit" in html

    def test_label_present(self) -> None:
        """The label string appears as button text."""
        html = render_to_string(
            "includes/_button.html",
            {"label": "View a sample bulletin →", "href": "#", "variant": "primary"},
        )
        assert "View a sample bulletin" in html


# ---------------------------------------------------------------------------
# _card.html partial rendering
# ---------------------------------------------------------------------------


class TestCardPartial:
    """Tests for the _card.html showcase partial."""

    def test_default_p6_chrome(self) -> None:
        """p-6 card has all four chrome classes."""
        html = render_to_string(
            "includes/_card.html",
            {"title": "My title", "body": "My body", "padding": "p-6"},
        )
        assert "bg-card" in html
        assert "rounded-card" in html
        assert "border-text-3/10" in html
        assert "shadow-sm" in html
        assert "p-6" in html

    def test_padding_forwarded(self) -> None:
        """Custom padding value appears in the rendered class attribute."""
        html = render_to_string(
            "includes/_card.html",
            {"title": "T", "body": "B", "padding": "px-5 py-4"},
        )
        assert "px-5 py-4" in html

    def test_center_adds_text_center(self) -> None:
        """center=True appends text-center to the wrapper div."""
        html = render_to_string(
            "includes/_card.html",
            {"title": "T", "body": "B", "padding": "p-8", "center": True},
        )
        assert "text-center" in html

    def test_no_center_by_default(self) -> None:
        """No center kwarg → text-center absent."""
        html = render_to_string(
            "includes/_card.html",
            {"title": "T", "body": "B", "padding": "p-6"},
        )
        assert "text-center" not in html

    def test_inner_content_preserved(self) -> None:
        """Title and body text appear inside the rendered card."""
        html = render_to_string(
            "includes/_card.html",
            {"title": "Hello card", "body": "This is the body.", "padding": "p-6"},
        )
        assert "Hello card" in html
        assert "This is the body." in html

    def test_extra_classes_forwarded(self) -> None:
        """extra kwarg classes appear on the wrapper div."""
        html = render_to_string(
            "includes/_card.html",
            {"title": "T", "body": "B", "padding": "p-4", "extra": "mb-3 foo-bar"},
        )
        assert "mb-3" in html
        assert "foo-bar" in html


# ---------------------------------------------------------------------------
# Status page children rendered via test client
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStatusPageChildren:
    """Tests for templates that extend _status_page.html.

    Uses the test client so URL resolution and context processors work
    correctly (mirrors the approach in test_nav_partial.py for full-page
    templates rather than partials).
    """

    def test_manage_sent_renders_status_shell(self, client: Client) -> None:
        """manage_sent.html renders the centred flex shell and card chrome."""
        # manage_sent is rendered by manage_view on unauthenticated POST;
        # render directly via render_to_string with a request so context
        # processors fire, then check structural classes.
        rf = RequestFactory()
        request = rf.get("/")
        request.user = AnonymousUser()
        html = render_to_string("subscriptions/manage_sent.html", {}, request=request)
        assert "min-h-[80vh]" in html
        assert "bg-card" in html
        assert "rounded-card" in html
        assert "Check your inbox" in html
        # No CTA block rendered
        assert "inline-block" not in html

    def test_manage_saved_has_cta_button(self, anon_request) -> None:
        """manage_saved.html renders the 'Back to Snowdesk' primary anchor."""
        html = render_to_string(
            "subscriptions/manage_saved.html", {}, request=anon_request
        )
        assert "Regions saved" in html
        assert "Back to Snowdesk" in html
        assert "inline-block" in html  # anchor button

    def test_link_expired_has_cta_button(self, anon_request) -> None:
        """link_expired.html renders the 'Request a new link' primary anchor."""
        html = render_to_string(
            "subscriptions/link_expired.html", {}, request=anon_request
        )
        assert "This link has expired" in html
        assert "Request a new link" in html
        assert "inline-block" in html

    def test_unsubscribe_done_has_cta_button(self, anon_request) -> None:
        """unsubscribe_done.html renders the 'Back to Snowdesk' anchor."""
        html = render_to_string(
            "subscriptions/unsubscribe_done.html", {}, request=anon_request
        )
        assert "You've been unsubscribed" in html
        assert "Back to Snowdesk" in html
        assert "inline-block" in html

    def test_all_status_pages_share_chrome(self, anon_request) -> None:
        """All four status pages share the same flex + card wrapper classes."""
        templates = [
            "subscriptions/manage_sent.html",
            "subscriptions/manage_saved.html",
            "subscriptions/link_expired.html",
            "subscriptions/unsubscribe_done.html",
        ]
        for tpl in templates:
            html = render_to_string(tpl, {}, request=anon_request)
            assert "min-h-[80vh]" in html, f"{tpl}: missing flex shell"
            assert "max-w-md" in html, f"{tpl}: missing max-w-md"
            assert "bg-card" in html, f"{tpl}: missing card chrome"
            assert "rounded-card" in html, f"{tpl}: missing card chrome"
