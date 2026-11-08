"""
tests/public/test_app_tabbar.py — the installed phone app's tab bar (SNOW-1476).

Covers:
  - The bar renders on pages that extend public/base.html, signed in and
    signed out, with all four tabs.
  - On the map the tabs are buttons; elsewhere they are links to
    /map/?panel=….
  - The tab lit on load: You on account, help and legal pages, Map
    everywhere else.
  - The You tab carries the signed-in reader's initial.
  - The map renders the You panel (account entries signed in, Sign in
    signed out) and the Saved chips under each Saved sheet's header.
  - The CSS gate: the bar, the chips and the hidden chrome are drawn only
    under ``(display-mode: standalone) and (max-width: 639.98px)``. No
    browser here runs standalone, so the stylesheets are read as text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.template import Context, Template
from django.test import Client, RequestFactory
from django.urls import reverse

from tests.factories import UserFactory

GATE = "@media (display-mode: standalone) and (max-width: 639.98px)"
TABS = ("map", "today", "saved", "you")


def _bar(content: str) -> str:
    """Return the tab bar's markup from a page."""
    match = re.search(
        r'<div\s+role="navigation".*?data-testid="app-tabbar".*?</div>', content, re.S
    )
    assert match, "no tab bar on the page"
    return match.group(0)


def _lit(bar: str) -> list[str]:
    """Return the tabs marked aria-current in the bar."""
    tabs = re.findall(r"<(?:a|button)\b[^>]*>", bar)
    return [
        re.search(r'data-app-tab="(\w+)"', tag).group(1)  # type: ignore[union-attr]
        for tag in tabs
        if 'aria-current="page"' in tag
    ]


def _gated_blocks(css: str) -> str:
    """Return the text of every block under the standalone phone gate."""
    out = []
    for match in re.finditer(re.escape(GATE) + r"\s*\{", css):
        depth, i = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        out.append(css[match.end() : i - 1])
    return "\n".join(out)


@pytest.mark.django_db
class TestOnEveryPage:
    """The bar on pages off the map: links, and which tab is lit."""

    def test_signed_out_all_four_tabs_link_to_the_map(self, client: Client) -> None:
        bar = _bar(client.get(reverse("public:resorts")).content.decode())

        for tab in TABS:
            assert f'data-app-tab="{tab}"' in bar
        assert "<button" not in bar
        assert 'href="/map/?panel=today"' in bar
        assert 'href="/map/?panel=saved"' in bar
        assert 'href="/map/?panel=you"' in bar
        assert _lit(bar) == ["map"]

    @pytest.mark.parametrize(
        "name", ["public:help", "public:privacy", "public:colophon"]
    )
    def test_you_is_lit_on_help_and_legal_pages(
        self, client: Client, name: str
    ) -> None:
        assert _lit(_bar(client.get(reverse(name)).content.decode())) == ["you"]

    def test_you_is_lit_on_account_pages_and_carries_the_initial(
        self, client: Client
    ) -> None:
        client.force_login(UserFactory.create(email="dana@example.com"))

        bar = _bar(client.get(reverse("accounts:settings")).content.decode())

        assert _lit(bar) == ["you"]
        assert re.search(r'class="app-tab-avatar"[^>]*>D<', bar)


@pytest.mark.django_db
class TestOnTheMap:
    """On the map the tabs are buttons, and the panels they open are there."""

    def test_the_tabs_are_buttons(self, client: Client) -> None:
        bar = _bar(client.get(reverse("public:map")).content.decode())

        assert "data-app-tabbar-on-map" in bar
        assert "<a " not in bar
        assert len(re.findall(r'<button\s+type="button"\s+class="app-tab"', bar)) == 4
        assert _lit(bar) == ["map"]

    def test_the_you_panel_lists_the_account_signed_in(self, client: Client) -> None:
        client.force_login(UserFactory.create(email="dana@example.com"))

        content = client.get(reverse("public:map")).content.decode()

        assert 'id="you-sheet"' in content
        template = content[content.index('id="you-sheet-template"') :]
        assert "dana@example.com" in template
        assert 'data-testid="account-menu-trips"' in template
        assert 'data-testid="account-menu-settings"' in template
        assert 'data-testid="account-menu-sign-out"' in template
        assert 'data-testid="you-offline-areas"' in template
        assert 'data-testid="you-sheet-footer"' in template

    def test_the_you_panel_offers_sign_in_signed_out(self, client: Client) -> None:
        content = client.get(reverse("public:map")).content.decode()

        template = content[content.index('id="you-sheet-template"') :]
        assert 'id="you-sign-in"' in template
        assert "account-menu-sign-out" not in template

    def test_each_saved_sheet_carries_the_chips_on_its_own(
        self, client: Client
    ) -> None:
        client.force_login(UserFactory.create())

        content = client.get(reverse("public:map")).content.decode()

        rows = re.findall(
            r"<fieldset[^>]*data-saved-chips.*?</fieldset>", content, re.S
        )
        checked = [re.search(r'value="(\w+)"\s[^>]*checked', row, re.S) for row in rows]
        assert sorted(m.group(1) for m in checked if m) == [
            "observations",
            "places",
            "routes",
        ]

    def test_the_script_is_in_the_map_bundle(self) -> None:
        bundle = Path(
            settings.BASE_DIR, "static/js/bundles/map_surfaces.txt"
        ).read_text()
        assert "app_tabbar.js" in bundle


class TestTheStateTag:
    """``{% app_tabbar_state %}`` without a resolved request."""

    def test_a_bare_render_is_map_off_the_map(self) -> None:
        out = Template(
            "{% load app_tabbar %}{% app_tabbar_state as s %}{{ s.active }} {{ s.on_map }}"
        ).render(Context({"request": RequestFactory().get("/anything/")}))
        assert out == "map False"

    def test_no_request_is_map_off_the_map(self) -> None:
        out = Template(
            "{% load app_tabbar %}{% app_tabbar_state as s %}{{ s.active }} {{ s.on_map }}"
        ).render(Context({}))
        assert out == "map False"


class TestTheGate:
    """Drawn only in the installed app on a phone."""

    main = Path(settings.BASE_DIR, "src/css/main.css").read_text()
    map_css = Path(settings.BASE_DIR, "static/css/map.css").read_text()

    def test_the_bar_and_chips_are_hidden_outside_the_gate(self) -> None:
        assert re.search(r"^\.app-tabbar\s*\{\s*display:\s*none", self.main, re.M)
        assert re.search(r"^\.app-saved-chips\s*\{\s*display:\s*none", self.main, re.M)

    def test_the_gate_draws_the_bar_and_hides_what_it_replaces(self) -> None:
        gated = _gated_blocks(self.main)
        assert ".app-tabbar" in gated
        assert ".app-saved-chips" in gated
        assert 'footer[data-testid="site-footer"]' in gated
        assert "[data-subscriber-menu]" in gated

    def test_the_map_hides_its_roundels_only_under_the_gate(self) -> None:
        gated = _gated_blocks(self.map_css)
        assert "#map-today-panel-toggle" in gated
        assert "#map-controls-collapsible" in gated
        assert "--app-tabbar-h" in gated
