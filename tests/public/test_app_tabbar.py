"""
tests/public/test_app_tabbar.py — the installed phone app's tab bar (SNOW-1476).

Covers:
  - The bar renders on pages that extend public/base.html, signed in and
    signed out, with all four tabs.
  - On the map the tabs are buttons; elsewhere they are links to
    /map/?panel=….
  - The tab lit on load: Help on the help and legal pages, none on an
    account page (the header's avatar menu leads there), Map everywhere
    else.
  - The header keeps its avatar menu, as on the web (Hugo, 8 Nov 2026).
  - On the map the Help tab's "?" menu carries the Map key and the legal
    line, only in the installed app (`app-only`); the layers menu's
    Downloaded areas row opens the downloads sheet; and the Saved chips sit under each Saved
    sheet's header.
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

from tests.factories import (
    MicroRegionFactory,
    ResortFactory,
    TripFactory,
    UserFactory,
)

GATE = "@media (display-mode: standalone) and (max-width: 639.98px)"
TABS = ("map", "today", "saved", "help")


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


def _representative_urls(user: object) -> dict[str, str]:
    """Return the pages the bar must reach, one of each kind."""
    region = MicroRegionFactory.create()
    return {
        "home": reverse("public:home"),
        "map": reverse("public:map"),
        "bulletin": region.get_absolute_url(),
        "resort": ResortFactory.create(region=region).get_absolute_url(),
        "trip": reverse(
            "trips:detail", kwargs={"uuid": TripFactory.create(created_by=user).uuid}
        ),
        "settings": reverse("accounts:settings"),
        "help": reverse("public:help"),
        "privacy": reverse("public:privacy"),
        "resorts": reverse("public:resorts"),
    }


EXPECTED_LIT = {
    "home": "map",
    "map": "map",
    "bulletin": "map",
    "resort": "map",
    "trip": "map",
    "settings": "",
    "help": "help",
    "privacy": "help",
    "resorts": "map",
}


@pytest.mark.django_db
@pytest.mark.parametrize("signed_in", [True, False], ids=["signed-in", "signed-out"])
def test_every_kind_of_page_carries_the_bar(client: Client, signed_in: bool) -> None:
    """A page that stops extending public/base.html loses the bar and fails."""
    user = UserFactory.create()
    if signed_in:
        client.force_login(user)
    for kind, url in _representative_urls(user).items():
        response = client.get(url, follow=True)
        if response.status_code != 200:
            # Signed out, the settings and trip pages send a visitor to sign
            # in, which is a page of its own and carries the bar too.
            assert not signed_in and kind in {"settings", "trip"}, (kind, url)
            continue
        bar = _bar(response.content.decode())
        for tab in TABS:
            assert f'data-app-tab="{tab}"' in bar, kind
        if signed_in or kind not in {"settings", "trip"}:
            expected = [EXPECTED_LIT[kind]] if EXPECTED_LIT[kind] else []
            assert _lit(bar) == expected, kind


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
        assert 'href="/map/?panel=help"' in bar
        assert _lit(bar) == ["map"]

    @pytest.mark.parametrize(
        "name", ["public:help", "public:privacy", "public:colophon"]
    )
    def test_help_is_lit_on_help_and_legal_pages(
        self, client: Client, name: str
    ) -> None:
        assert _lit(_bar(client.get(reverse(name)).content.decode())) == ["help"]

    def test_nothing_is_lit_on_an_account_page(self, client: Client) -> None:
        client.force_login(UserFactory.create())

        bar = _bar(client.get(reverse("accounts:settings")).content.decode())

        assert _lit(bar) == []

    def test_the_header_keeps_the_avatar_menu(self, client: Client) -> None:
        client.force_login(UserFactory.create())

        content = client.get(reverse("public:resorts")).content.decode()

        assert "data-subscriber-menu" in content
        assert "account-menu-offline-areas" not in content


@pytest.mark.django_db
class TestOnTheMap:
    """On the map the tabs are buttons, and the panels they open are there."""

    def test_the_tabs_are_buttons(self, client: Client) -> None:
        bar = _bar(client.get(reverse("public:map")).content.decode())

        assert "data-app-tabbar-on-map" in bar
        assert "<a " not in bar
        assert len(re.findall(r'<button\s+type="button"\s+class="app-tab"', bar)) == 4
        assert _lit(bar) == ["map"]

    def test_the_help_menu_gains_the_map_key_and_legal_line_for_the_app(
        self, client: Client
    ) -> None:
        content = client.get(reverse("public:map")).content.decode()

        menu = content[content.index('id="map-help-menu"') :]
        key = re.search(
            r'<li class="app-only">\s*<button[^>]*data-help-menu="key"', menu
        )
        assert key, "no app-only Map key item in the help menu"
        legal = re.search(
            r'<div class="app-only[^"]*"[^>]*data-testid="map-help-menu-legal"', menu
        )
        assert legal, "no app-only legal line in the help menu"
        line = menu[legal.end() :]
        line = line[: line.index("</div>")]
        assert reverse("public:help") not in line
        assert reverse("public:terms_of_service") in line

    def test_downloaded_areas_is_one_row_that_opens_the_sheet(
        self, client: Client
    ) -> None:
        """One row: its left half opens the downloads sheet, its switch shows them."""
        content = client.get(reverse("public:map")).content.decode()

        rows = re.findall(
            r'<li role="none" class="basemap-menu-layer">\s*<button[^>]*'
            r"data-downloads-open.*?</li>",
            content,
            re.S,
        )
        assert rows
        for row in rows:
            assert 'data-overlay-key="downloads"' in row
        assert content.count('data-overlay-key="downloads"') == len(rows)
        assert "Offline areas" not in content

    def test_the_site_footer_keeps_help(self, client: Client) -> None:
        content = client.get(reverse("public:help")).content.decode()

        footer = content[content.index('data-testid="site-footer"') :]
        assert f'href="{reverse("public:help")}"' in footer

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
        # The header's avatar menu and Sign in stay in the app.
        assert "[data-subscriber-menu]" not in gated
        assert "#nav-sign-in" not in gated

    def test_app_only_is_hidden_by_the_negated_gate(self) -> None:
        negated = "@media not (" + GATE.removeprefix("@media ") + ")"
        start = self.main.index(negated)
        block = self.main[start : self.main.index("}", start)]
        assert ".app-only" in block
        assert "display: none !important" in self.main[start : start + 200]

    def test_nothing_outside_the_gate_hides_what_the_bar_replaces(self) -> None:
        for css in (self.main, self.map_css):
            gated = _gated_blocks(css)
            rest = css
            for block in gated.split("\n"):
                if block.strip():
                    rest = rest.replace(block, "")
            for selector in (
                'body > footer[data-testid="site-footer"]',
                "#map-today-panel-toggle,",
            ):
                assert selector not in rest, selector

    def test_the_map_hides_its_roundels_only_under_the_gate(self) -> None:
        gated = _gated_blocks(self.map_css)
        assert "#map-today-panel-toggle" in gated
        assert "#map-controls-collapsible" in gated
        assert "#map-help-toggle" in gated
        assert "#map-legend-toggle" in gated
        assert "#map-help-menu" in gated
        assert ".map-legend > #map-legend-card" in gated
        assert "--app-tabbar-h" in gated
