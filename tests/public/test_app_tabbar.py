"""
tests/public/test_app_tabbar.py — the installed phone app's tab bar (SNOW-1476,
three tabs since SNOW-1479).

Covers:
  - The bar renders on pages that extend public/base.html, signed in and
    signed out, with its three tabs: Map, Saved, Help. No Today.
  - On the map the tabs are buttons; elsewhere they are links to
    /map/?panel=….
  - The tab lit on load: Help on the help and legal pages, none on an
    account page (the header's avatar menu leads there), Map everywhere
    else.
  - The header keeps its avatar menu, as on the web (Hugo, 8 Nov 2026).
  - On the map the Help tab's "?" menu carries the Map key and the legal
    line, only in the installed app (`app-only`); the layers menu's
    Downloaded areas row opens the downloads sheet; the Saved tabs
    (four icons) and a + roundel head each of the four Saved sheets,
    `app-only`, and each + carries its tab's hook; the sheets' own titles
    and × are `app-hidden`; the Saved tab carries the alert dot.
  - The CSS gate: the bar and the hidden chrome are drawn only under
    ``(display-mode: standalone) and (max-width: 639.98px)``, and
    `.app-hidden` hides only inside it. No browser here runs standalone, so
    the stylesheets are read as text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.template import Context, Template
from django.test import Client, RequestFactory
from django.urls import reverse
from waffle.testutils import override_flag

from tests.factories import (
    MicroRegionFactory,
    ResortFactory,
    TripFactory,
    UserFactory,
)

GATE = "@media (display-mode: standalone) and (max-width: 639.98px)"
TABS = ("map", "saved", "help")
SAVED_SHEETS = {
    "places": "favourite-list-template",
    "routes": "route-list-template",
    "trips": "trips-sheet-template",
    "observations": "report-list-template",
}


def _template(content: str, template_id: str) -> str:
    """Return the body of the ``<template id=…>`` element in ``content``."""
    opener = f'<template id="{template_id}">'
    start = content.index(opener) + len(opener)
    return content[start : content.index("</template>", start)]


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

    def test_signed_out_all_three_tabs_link_to_the_map(self, client: Client) -> None:
        bar = _bar(client.get(reverse("public:resorts")).content.decode())

        for tab in TABS:
            assert f'data-app-tab="{tab}"' in bar
        assert 'data-app-tab="today"' not in bar
        assert "<button" not in bar
        assert "?panel=today" not in bar
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
        assert len(re.findall(r'<button\s+type="button"\s+class="app-tab"', bar)) == 3
        assert 'data-app-tab="today"' not in bar
        assert _lit(bar) == ["map"]

    def test_the_saved_tab_carries_the_alert_dot(self, client: Client) -> None:
        bar = _bar(client.get(reverse("public:map")).content.decode())

        saved = re.search(r'data-app-tab="saved".*?</button>', bar, re.S)
        assert saved and "data-app-tab-dot" in saved.group(0)
        assert bar.count("data-app-tab-dot") == 1

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

    def test_each_saved_sheet_carries_the_four_tabs_on_its_own(
        self, client: Client
    ) -> None:
        client.force_login(UserFactory.create())

        content = client.get(reverse("public:map")).content.decode()

        for current, template_id in SAVED_SHEETS.items():
            body = _template(content, template_id)
            [row] = re.findall(
                r"<fieldset[^>]*data-saved-tabs.*?</fieldset>", body, re.S
            )
            assert re.search(r'<fieldset class="app-only', row), current
            values = re.findall(r'name="saved-tab"\s+value="(\w+)"', row)
            assert values == ["places", "routes", "trips", "observations"]
            checked = re.findall(r'value="(\w+)"\s+class="peer sr-only"\s+checked', row)
            assert checked == [current]

    def test_only_the_selected_tab_shows_its_name(self, client: Client) -> None:
        """Every option carries its name `sr-only`, shown by `group-has-checked`.

        The label is in each radio's accessible name all the same; which one
        is drawn is CSS (`.group:has(:checked)`), so the markup is what can be
        pinned: every option is a `group` label with one checked radio among
        them, and each name is hidden unless its own label holds the check.
        """
        client.force_login(UserFactory.create())

        content = client.get(reverse("public:map")).content.decode()

        for current, template_id in SAVED_SHEETS.items():
            body = _template(content, template_id)
            [row] = re.findall(
                r"<fieldset[^>]*data-saved-tabs.*?</fieldset>", body, re.S
            )
            options = re.findall(
                r'<label class="group cursor-pointer">.*?</label>', row, re.S
            )
            assert len(options) == 4, current
            for option in options:
                value = re.search(r'value="(\w+)"', option)
                assert value
                name = re.search(
                    r'<span class="([^"]*)">[^<]+</span>\s*</span>', option
                )
                assert name, option
                assert name.group(1).split() == [
                    "sr-only",
                    "group-has-checked:not-sr-only",
                ]
                assert ("checked" in option.split("peer sr-only")[1].split(">")[0]) == (
                    value.group(1) == current
                ), (current, value.group(1))

    def test_a_signed_out_reader_gets_no_trips_tab(self, client: Client) -> None:
        """No #trips-sheet for a visitor, so no Trips option to check."""
        content = client.get(reverse("public:map")).content.decode()

        rows = re.findall(r"<fieldset[^>]*data-saved-tabs.*?</fieldset>", content, re.S)
        assert rows
        for row in rows:
            values = re.findall(r'name="saved-tab"\s+value="(\w+)"', row)
            assert values == ["places", "routes", "observations"]

    def test_the_tabs_and_plus_are_the_whole_header_in_the_app(
        self, client: Client
    ) -> None:
        """Title, ×, context line and section label are `app-hidden`."""
        client.force_login(UserFactory.create())

        content = client.get(reverse("public:map")).content.decode()

        for current, template_id in SAVED_SHEETS.items():
            body = _template(content, template_id)
            assert re.search(
                r'<span class="app-hidden flex min-w-0 items-center gap-2.5">', body
            ), current
            close = re.search(r'<button[^>]*data-action="dismiss"[^>]*>', body, re.S)
            assert close and "app-hidden" in close.group(0), current
            assert re.search(r'<p class="app-hidden -mt-1.5 px-2', body), current
            assert re.search(r'class="[^"]*app-hidden pb-1 font-medium', body), current

    def test_each_tabs_plus_carries_its_hook(self, client: Client) -> None:
        client.force_login(UserFactory.create())

        content = client.get(reverse("public:map")).content.decode()

        def plus(template_id: str) -> str:
            body = _template(content, template_id)
            match = re.search(
                r'<button[^>]*data-testid="saved-add-roundel"[^>]*>', body, re.S
            )
            assert match, template_id
            assert "app-only" in match.group(0)
            return match.group(0)

        assert "data-panel-add" in plus(SAVED_SHEETS["places"])
        assert "data-panel-add" in plus(SAVED_SHEETS["observations"])
        assert 'data-saved-go="routes"' in plus(SAVED_SHEETS["trips"])
        # Routes without route drawing: the + uploads, as the foot's button.
        assert "data-panel-add" in plus(SAVED_SHEETS["routes"])

    def test_routes_plus_is_the_add_menu_with_route_drawing(
        self, client: Client
    ) -> None:
        """The patch's Find / Create / Upload menu, shown everywhere."""
        client.force_login(UserFactory.create())

        with override_flag("route_drawing", active=True):
            content = client.get(reverse("public:map")).content.decode()

        body = _template(content, SAVED_SHEETS["routes"])
        assert 'id="route-add-menu-trigger"' in body
        assert 'data-testid="saved-add-roundel"' not in body

    def test_the_foot_add_button_is_app_hidden(self, client: Client) -> None:
        client.force_login(UserFactory.create())

        content = client.get(reverse("public:map")).content.decode()

        for template_id in (SAVED_SHEETS["places"], SAVED_SHEETS["observations"]):
            body = _template(content, template_id)
            foot = re.search(r"<div\s+data-panel-foot[^>]*>", body, re.S)
            assert foot and "app-hidden" in foot.group(0), template_id

    def test_the_trips_sheet_is_for_a_signed_in_reader(self, client: Client) -> None:
        content = client.get(reverse("public:map")).content.decode()
        assert 'id="trips-sheet-template"' not in content
        client.force_login(UserFactory.create())
        content = client.get(reverse("public:map")).content.decode()
        assert 'id="trips-sheet"' in content
        assert reverse("trips:panel_list") in content

    def test_the_script_is_in_the_map_bundle(self) -> None:
        bundle = Path(
            settings.BASE_DIR, "static/js/bundles/map_surfaces.txt"
        ).read_text()
        assert "app_tabbar.js" in bundle
        assert "trips_panel.js" in bundle
        assert bundle.index("trips_panel.js") < bundle.index("app_tabbar.js")


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

    def test_the_bar_is_hidden_outside_the_gate(self) -> None:
        assert re.search(r"^\.app-tabbar\s*\{\s*display:\s*none", self.main, re.M)
        assert ".app-saved-chips" not in self.main

    def test_app_hidden_hides_only_inside_the_gate(self) -> None:
        """The reverse of `.app-only` (SNOW-1479)."""
        gated = _gated_blocks(self.main)
        assert re.search(r"\.app-hidden\s*\{\s*display:\s*none !important", gated)
        assert self.main.count(".app-hidden {") == 1

    def test_the_gate_draws_the_bar_and_hides_what_it_replaces(self) -> None:
        gated = _gated_blocks(self.main)
        assert ".app-tabbar" in gated
        assert 'footer[data-testid="site-footer"]' in gated
        # The header's avatar menu and Sign in stay in the app.
        assert "[data-subscriber-menu]" not in gated
        assert "#nav-sign-in" not in gated

    def test_a_tab_panels_list_runs_down_to_its_foot(self) -> None:
        """No gap and blank card under a long list in the app (SNOW-1479).

        Most of the body's 1.125rem flex gap before "Show on the map" moves
        inside the scroll area, so the list runs to just above the switch and
        scrolls under its own edge; 0.5rem stays, the gap between region
        cards, so a part-scrolled row clips at a clear edge.
        """
        gated = _gated_blocks(self.map_css)
        rule = re.search(
            r"\[data-panel-scroll\]:has\(~ \[data-panel-overlay-toggle\]\)\s*\{([^}]*)\}",
            gated,
        )
        assert rule
        # 1.125rem gap - 0.625rem pulled back = 0.5rem left above the switch.
        assert "margin-bottom: -0.625rem" in rule.group(1)
        assert "padding-bottom: 0.625rem" in rule.group(1)
        assert "data-panel-scroll" not in self.map_css.replace(gated, "")

    @pytest.mark.django_db
    def test_the_scroll_area_carries_the_hook(self, client: Client) -> None:
        client.force_login(UserFactory.create())

        content = client.get(reverse("public:map")).content.decode()

        for template_id in SAVED_SHEETS.values():
            body = _template(content, template_id)
            assert re.search(
                r"<div data-panel-scroll class=\"[^\"]*overflow-y-auto", body
            )

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
        assert "#trips-sheet" in gated
        assert "#map-controls-collapsible" in gated
        assert "#map-help-toggle" in gated
        assert "#map-legend-toggle" in gated
        assert "#map-help-menu" in gated
        assert ".map-legend > #map-legend-card" in gated
        assert "--app-tabbar-h" in gated
