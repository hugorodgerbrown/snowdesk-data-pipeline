"""
tests/map/test_ugc_panels.py — the four UGC map panels share one skeleton.

SNOW-658. Downloads, favourites and field observations answer the same
question on the same map behind the same kind of roundel — "what have I
got here, and how do I add to it or get rid of one?" — and until this
ticket they shared exactly one element (the "Show X on the map" switch)
and diverged at every other point of contact: three sheet-scroll models,
two title sizes, three body wrappers, three row shapes. Nothing enforced
the sameness, so each panel drifted the moment someone edited it.

SNOW-687 made ROUTES the fourth panel and did not add it here, so for two
months the guards below ran over three of the four. That gap had a cost,
and SNOW-765 is it: SNOW-764 put a Share control on every route row while
the strip above them went on promising "Routes are private and not
shared", and ``test_panel_states_where_its_data_lives`` — the check whose
entire purpose is to catch a strip that stopped telling the truth — was
not looking at that panel. Routes is in every dict below now.

The lesson generalises past this file: a guard that enumerates its
subjects by hand silently stops covering the thing it was written for the
moment a fifth one is added. If a UGC panel is ever added, it goes in
PANEL_ICONS and the two dicts under it in the same commit.

That is what these tests exist to stop. They do NOT check that the panels
look nice; they check that the chrome around each one's own content came
out of ``includes/_ugc_panel.html``, so a hand-edit to one of the three
fails here instead of silently going unnoticed until all three are
side by side again.

The skeleton is derived, not transcribed: it is every class string the
shared partial renders minus every class string its rows slot renders, so
a legitimate change to the partial updates the expectation automatically
and a change to ONE panel does not.

Class-string containment cannot see everything, though, and Hugo's "Map
panels — common format" design adds three parts whose whole value is in
their CONTENT rather than their shape: the header icon (which roundel is
this?), the context strip (where does this data live?) and the section
label (what is this a list of?). A panel could render all three with the
right classes and the wrong words — the wrong glyph, a strip that no
longer says whether the contents leave the device — and be exactly as
broken as one that hand-rolled its chrome. So those three are asserted
per panel, by content, below the derived-skeleton checks.
"""

from __future__ import annotations

import re

import pytest
from django.template.loader import render_to_string
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.public.templatetags.components import icon_button_classes
from tests.factories import UserFactory

# The three panel bodies, by the <template> id each surface renders them
# in, each with the glyph its own roundel carries — one distinctive path
# from includes/_icon_*.html. The header icon is the panel's only identity
# mark: it is what confirms which roundel was tapped, so a panel wearing
# another panel's glyph (or none) is a real defect, not a cosmetic one.
PANEL_ICONS = {
    # The viewfinder's top-left bracket.
    "map-downloads-body-template": "M3 7V5a2 2 0 0 1 2-2h2",
    # The star's first two points.
    "favourite-list-template": "12 2 15.09 8.26",
    # The binoculars' left eyepiece (Font Awesome Free, 512 grid).
    "report-list-template": "M128 32l32 0c17.7 0 32 14.3 32 32",
    # The S-bend track between the route glyph's two waypoints.
    #
    # SNOW-765: routes was ABSENT from this file until now. SNOW-658 wrote
    # these guards for three panels; SNOW-687 made routes the fourth and
    # never added it here, so for two months the one panel nobody was
    # checking was free to drift. It did: SNOW-764 put a Share control on
    # every row while the strip above them went on promising "Routes are
    # private and not shared", and the check that exists precisely to catch
    # a strip that stopped telling the truth was not looking at this panel.
    "route-list-template": "M8.5 19h5a3.5 3.5 0 0 0 0-7",
}

PANEL_TEMPLATE_IDS = tuple(PANEL_ICONS)

# The context strip — one line under each title saying where that panel's
# data lives. Written out here rather than derived, because the POINT is
# the sentence: a panel that stopped saying whether its contents leave the
# device would still render a strip, and still pass a shape-only check.
PANEL_CONTEXT_LINES = {
    # SNOW-XXX: the downloads panel has NO strip, and that is the one
    # exemption here. Its sentence said which rows leave the device and
    # which stay; its two list headings — "On this device" / "On your
    # account only" — now say the same thing where it applies, over the
    # rows it applies to, and a sentence repeating them above was the panel
    # saying one thing twice. The headings are rendered by JS, so this
    # server-side check cannot read them: the strings they are built from
    # are asserted instead, in
    # ``test_the_downloads_panel_says_it_in_its_list_headings``.
    "map-downloads-body-template": None,
    "favourite-list-template": "Favourites are private and not shared.",
    "report-list-template": "Reports are shared with the community.",
    # SNOW-765: conditional, and the condition is the point. It said
    # "Routes are private and not shared." until SNOW-764 put a Share
    # control on every row below it — a flat promise sitting a few pixels
    # from the control whose whole purpose is to break it. The favourites
    # line above keeps the unconditional form because a favourite really
    # cannot be shared; this one cannot.
    "route-list-template": "Routes are private unless you share one.",
}

# The mono uppercase section label heading each panel's list. Downloads
# has none: SNOW-832 groups its rows by BASEMAP, and which basemaps a
# device holds is data, so its headings are written by JS into a cloned
# group template rather than server-rendered here. That template's own
# heading is still the same primitive — see
# ``test_the_downloads_group_heading_is_the_same_label_primitive``.
PANEL_SECTION_LABELS: dict[str, tuple[str, ...]] = {
    "map-downloads-body-template": (),
    "favourite-list-template": ("Places",),
    "report-list-template": ("Reports",),
    "route-list-template": ("Tracks",),
}

# The group `<template>` the downloads panel clones per basemap. A sibling
# of its row template, not nested in its body one — see that template's
# own comment.
DOWNLOADS_GROUP_TEMPLATE_ID = "map-downloads-group-template"

# Each panel's foot carries a "Show on the map" switch for its own layer,
# bound to that layer's layers-menu row (the two-capsule control column,
# restoring what SNOW-904 removed). Its id is how
# static/js/map_panel_overlay_switch.js finds it, so each panel's is named.
PANEL_TOGGLE_IDS = {
    "map-downloads-body-template": "map-downloads-overlay-toggle",
    "favourite-list-template": "map-favourites-overlay-toggle",
    "report-list-template": "map-community-reports-overlay-toggle",
    "route-list-template": "map-routes-overlay-toggle",
}

# The labels earlier rounds used, one per panel and then one for all. The
# switch says "Show on the map" everywhere, fixed in includes/_ugc_panel.html,
# so none of these may come back as a per-panel variant.
RETIRED_TOGGLE_LABELS = (
    "Display on the map",
    "Show areas on the map",
    "Show favourites on the map",
    "Show community reports on the map",
)

# A rows slot that renders something, so the panel's scroll region is not
# empty while the skeleton is being derived. Its own class strings are
# subtracted back out below.
DEMO_ROWS_TEMPLATE = "public/partials/_ugc_panel_demo_rows.html"

_CLASS_RE = re.compile(r"""class=(?:"([^"]*)"|'([^']*)')""")

# SNOW-1479: tokens that say where a part is drawn or how it is trimmed
# rather than what it is. A Saved panel's × carries `app-hidden` (the
# installed phone app's tab bar closes it); its section label is `hidden`
# on every screen since SNOW-1481, as its title and strip are by the
# attribute; and its × sits in the header's action group, which takes the
# ×'s own -12px trim. None makes the chrome a different chrome.
_PLACEMENT_TOKENS = frozenset({"app-hidden", "hidden", "-mt-3", "-mr-3"})

# SNOW-1479: the Saved tabs and the + roundel a Saved panel's header carries
# (on every screen since SNOW-1481). Taken out of a panel's body before it
# is read, so these guards check the skeleton the panels share: its own
# glyph, one add control, the five parts in order.
_APP_ONLY_HEADER_RE = re.compile(
    r"<fieldset[^>]*data-saved-tabs.*?</fieldset>"
    r"|<button(?:(?!</button>).)*?data-testid=\"saved-add-roundel\".*?</button>",
    re.S,
)


def _class_strings(html: str) -> set[str]:
    """Return every ``class`` attribute value in ``html``.

    Both quoting styles: the shared CTA's class attribute is single-quoted
    (it interpolates the ``button_classes`` tag, whose output contains
    double quotes' worth of nothing but is written that way in the
    template), and every other one is double-quoted.

    Args:
        html: Rendered markup to scan.

    Returns:
        The set of class-attribute values found.

    """
    return {
        " ".join(t for t in (double or single).split() if t not in _PLACEMENT_TOKENS)
        for double, single in _CLASS_RE.findall(html)
    }


def _panel_body(html: str, template_id: str) -> str:
    """Return the contents of the ``<template id=…>`` element in ``html``.

    Args:
        html: The rendered home page.
        template_id: The template element's DOM id.

    Returns:
        Everything between that element's open and close tags, less the
        Saved tabs and + roundel and their `app-hidden` and `hidden` class
        marks (SNOW-1479, SNOW-1481).

    """
    opener = f'<template id="{template_id}">'
    start = html.index(opener) + len(opener)
    body = html[start : html.index("</template>", start)]
    body = _APP_ONLY_HEADER_RE.sub("", body)
    body = body.replace("app-hidden ", "").replace(" app-hidden", "")
    return re.sub(r'(class="[^"]*?) hidden\b', r"\1", body)


@pytest.fixture(scope="module")
def skeleton_classes() -> set[str]:
    """Return every class string ``includes/_ugc_panel.html`` itself renders.

    Derived by rendering the shared partial and subtracting the class
    strings contributed by the rows template handed to it — what is left is
    the chrome: the outer flex column, the sheet header, the scroll region,
    the CTA and the map switch.

    Returns:
        The skeleton's class-attribute values.

    """
    panel = render_to_string(
        "includes/_ugc_panel.html",
        {
            "title": "Title",
            "icon_template": "includes/_icon_favourite.html",
            "context_line": "Where this panel's data lives.",
            # No ``section_label``: the downloads panel passes none
            # (SNOW-832 — its headings are per-basemap and JS-written), so
            # that branch's wrapper and label classes are not part of what
            # all four panels share.
            "rows_template": DEMO_ROWS_TEMPLATE,
            "cta_label": "Add one",
            "toggle_id": "skeleton-toggle",
        },
    )
    rows = render_to_string(DEMO_ROWS_TEMPLATE, {})
    return _class_strings(panel) - _class_strings(rows)


@pytest.fixture(scope="module")
def saved_skeleton_classes() -> set[str]:
    """Return every class string a Saved panel's ``_ugc_panel.html`` renders.

    SNOW-1486: a Saved panel's header is the Saved grid (title and × over
    the tabs and +), not the icon-and-title row the others share, so its
    skeleton is derived the same way from a render that passes
    ``saved_tab``, less the tabs and + the page's bodies drop too.

    Returns:
        The Saved skeleton's class-attribute values.

    """
    panel = render_to_string(
        "includes/_ugc_panel.html",
        {
            "title": "Title",
            "icon_template": "includes/_icon_favourite.html",
            "context_line": "Where this panel's data lives.",
            "rows_template": DEMO_ROWS_TEMPLATE,
            "cta_label": "Add one",
            "toggle_id": "skeleton-toggle",
            "saved_tab": "places",
            "header_actions_template": "includes/_saved_add_roundel.html",
        },
    )
    panel = re.sub(
        r'(class="[^"]*?) hidden\b', r"\1", _APP_ONLY_HEADER_RE.sub("", panel)
    )
    rows = render_to_string(DEMO_ROWS_TEMPLATE, {})
    return _class_strings(panel) - _class_strings(rows)


@pytest.fixture(scope="module")
def section_label_classes() -> str:
    """Return the class string a panel's section label renders with.

    Derived by rendering includes/_eyebrow.html exactly as the panels ask
    for it, so a change to that primitive updates the expectation rather
    than failing these tests.

    Returns:
        The label's class-attribute value.

    """
    label = render_to_string(
        "includes/_eyebrow.html",
        {"tag": "p", "text": "Things", "class_extra": "pb-1 font-medium"},
    )
    return next(iter(_class_strings(label)))


@pytest.fixture()
def home_html(db: None) -> str:
    """Return the rendered map home page, which carries all three panels.

    Anonymous: every panel's <template> is server-rendered regardless of
    who is asking — the JS decides at open time what to put inside it.

    Args:
        db: pytest-django's database fixture — the page queries for its
            regions and ratings before it renders anything.

    Returns:
        The decoded response body.

    """
    client = Client(SERVER_NAME="localhost")
    return client.get(reverse("public:map")).content.decode()


class TestUgcPanelSkeleton:
    """All three panels' chrome comes from the one shared partial."""

    @pytest.mark.parametrize("template_id", PANEL_TEMPLATE_IDS)
    def test_panel_carries_the_whole_shared_skeleton(
        self,
        home_html: str,
        skeleton_classes: set[str],
        saved_skeleton_classes: set[str],
        template_id: str,
    ) -> None:
        """Every class string the shared partial renders is in this panel.

        A panel that stops including the partial — or that reproduces most
        of it by hand and gets one wrapper subtly different — fails here.
        A Saved panel is held to the Saved header's skeleton (SNOW-1486).
        """
        body = _panel_body(home_html, template_id)
        present = _class_strings(body)
        expected = (
            saved_skeleton_classes
            if template_id in SAVED_LIST_TEMPLATES
            else skeleton_classes
        )
        assert expected <= present, expected - present

    @pytest.mark.parametrize("template_id", PANEL_TEMPLATE_IDS)
    def test_panel_runs_the_five_parts_in_that_order(
        self, home_html: str, template_id: str
    ) -> None:
        """The five shell parts appear in the reading order the design sets.

        Header, context strip, list, add CTA, map switch — last, so the map
        behind the sheet is the last thing read (Hugo, SNOW-645 review).
        SNOW-904 removed the switch; the two-capsule control column put it
        back, bound to the layers-menu row. Class-string containment alone
        would not catch a panel that shipped these in a different order.
        """
        body = _panel_body(home_html, template_id)
        header = body.index("text-lg font-semibold")
        scroll = body.index("overflow-y-auto")
        cta = body.index("data-panel-add")
        switch = body.index("data-panel-overlay-toggle")
        line = PANEL_CONTEXT_LINES[template_id]
        if line is None:
            # No strip on this panel — the parts it does have still run in
            # the same order.
            assert header < scroll < cta < switch
            return
        strip = body.index(line)
        assert header < strip < scroll < cta < switch

    @pytest.mark.parametrize("template_id", PANEL_TEMPLATE_IDS)
    def test_panel_header_carries_its_own_roundels_glyph(
        self, home_html: str, template_id: str
    ) -> None:
        """Each panel wears the icon of the roundel that opens it.

        The design makes it the panel's ONLY identity mark — three panels
        with the same chrome and no glyph are three panels a user cannot
        tell apart once one is open. So each carries its own, and none
        carries another's.
        """
        body = _panel_body(home_html, template_id)
        if template_id in SAVED_LIST_TEMPLATES:
            # SNOW-1486: a Saved sheet is titled "Saved items" and its tab
            # names the list, so it wears no roundel's glyph.
            assert "Saved items" in body
            for path in PANEL_ICONS.values():
                assert path not in body
            return
        assert PANEL_ICONS[template_id] in body
        for other_id, path in PANEL_ICONS.items():
            if other_id != template_id:
                assert path not in body

    @pytest.mark.parametrize("template_id", PANEL_TEMPLATE_IDS)
    def test_panel_states_where_its_data_lives(
        self, home_html: str, template_id: str
    ) -> None:
        """One line under the title, saying it in that panel's own words.

        Asserted by CONTENT: the shape of the strip is chrome and is
        covered by the derived skeleton above, but a strip that stopped
        saying whether the contents leave the device would keep its
        classes and lose its whole reason for being there.
        """
        body = _panel_body(home_html, template_id)
        line = PANEL_CONTEXT_LINES[template_id]
        if line is None:
            pytest.skip("this panel says it in its list headings — see the dict")
        assert line in body

    @pytest.mark.parametrize("template_id", PANEL_TEMPLATE_IDS)
    def test_panel_heads_its_list_with_a_mono_section_label(
        self,
        home_html: str,
        section_label_classes: str,
        template_id: str,
    ) -> None:
        """Every fixed list label is the shared mono uppercase one.

        Three panels name their list in the shared partial's
        ``section_label``. Downloads names nothing here (SNOW-832): its
        headings are one per basemap, which is data, so it renders none
        and its group template carries the same primitive instead — the
        next test.
        """
        body = _panel_body(home_html, template_id)
        labels = PANEL_SECTION_LABELS[template_id]
        assert body.count(section_label_classes) == len(labels)
        for label in labels:
            assert f">{label}<" in body

    def test_the_downloads_panel_says_it_in_its_list_headings(
        self, home_html: str
    ) -> None:
        """The one panel with no context strip still says where its data is.

        SNOW-XXX deleted its sentence — "Your areas follow your account.
        The map data and the budget stay on this device." — because its two
        list headings say the same thing over the rows it is true of. That
        makes the headings load-bearing rather than decorative, which is
        what this asserts: they are rendered by JS, so what is checked is
        that the strings it builds them from are on the page for it to
        read.
        """
        assert '<span data-string="group-device">On this device</span>' in home_html
        assert (
            '<span data-string="group-account">Not on this device</span>' in home_html
        )

    def test_the_downloads_group_heading_is_the_same_label_primitive(
        self, home_html: str
    ) -> None:
        """The per-basemap heading comes from includes/_eyebrow.html too.

        SNOW-832 gave the downloads panel a heading per basemap instead of
        two fixed ones. The heading is JS-filled, so it cannot be asserted
        by its text — but it must still be the one label shape every other
        panel uses, or the four diverge again by the back door.
        """
        group = _panel_body(home_html, DOWNLOADS_GROUP_TEMPLATE_ID)
        eyebrow = render_to_string(
            "includes/_eyebrow.html",
            {
                "tag": "p",
                "text": "",
                "class_extra": "min-w-0 truncate font-medium",
                "data_hook": "group-label",
            },
        )
        assert next(iter(_class_strings(eyebrow))) in _class_strings(group)
        # And the hook render() fills it through.
        assert 'data-hook="group-label"' in group

    @pytest.mark.parametrize("template_id", PANEL_TEMPLATE_IDS)
    def test_panel_carries_its_own_show_on_the_map_switch(
        self, home_html: str, template_id: str
    ) -> None:
        """Each panel's foot carries one "Show on the map" switch, by its id.

        The switch is bound to the layers-menu row for the panel's own
        layer by static/js/map_panel_overlay_switch.js, which finds it by
        this id — so the id is the contract, and exactly one strip per
        panel. The label is the one fixed string; the per-panel wordings
        earlier rounds used stay retired, page-wide.
        """
        body = _panel_body(home_html, template_id)
        assert body.count("data-panel-overlay-toggle") == 1
        assert f'id="{PANEL_TOGGLE_IDS[template_id]}"' in body
        assert 'role="switch"' in body
        assert "Show on the map" in body
        for retired in RETIRED_TOGGLE_LABELS:
            assert retired not in home_html

    @pytest.mark.parametrize("template_id", PANEL_TEMPLATE_IDS)
    def test_panel_has_exactly_one_add_cta(
        self, home_html: str, template_id: str
    ) -> None:
        """One shared hook, one control — not three differently-named ones.

        ``data-panel-add`` replaced ``data-downloads-add`` /
        ``data-favourites-add`` / ``data-report-add``: three names for one
        control is the same divergence in attribute form. Each owning
        module delegates on its OWN sheet, so one name is unambiguous.

        SNOW-1414: the Downloads sheet's second way to add, "Download a
        region", is a different action with its own hook
        (``data-panel-add-region``), so it is not counted as this one.
        """
        body = _panel_body(home_html, template_id)
        assert len(re.findall(r"data-panel-add(?![-\w])", body)) == 1

    @pytest.mark.parametrize("template_id", PANEL_TEMPLATE_IDS)
    def test_scroll_region_allows_the_row_labels_bleed(
        self, home_html: str, template_id: str
    ) -> None:
        """The scroll region carries the padding a row label bleeds into.

        includes/_ugc_panel_row.html pulls the label (and the rename
        editor) 6px left of the row, so the label's TEXT lands on the row's
        own left edge rather than 6px inside it. A scroll container clips
        at its PADDING box, and declaring ``overflow-y`` computes
        ``overflow-x`` to ``auto`` as well — so with no padding here, that
        6px fell outside the box and was cut off: measured in Chromium, the
        label's box started at x=11 in a scroll box whose content began at
        x=17. Hugo: "The favourites name element is cut off at the left."

        The fix is 6px of padding on this box, pulled back by an equal
        negative margin so nothing MOVES — the bleed simply lands inside
        the clip. Both halves are asserted: padding alone would shift every
        row right, and the margin alone would shift the panel left.
        """
        body = _panel_body(home_html, template_id)
        scroll = [c for c in _class_strings(body) if "overflow-y-auto" in c]
        assert len(scroll) == 1, scroll
        assert "px-1.5" in scroll[0]
        assert "-mx-1.5" in scroll[0]

    def test_row_label_and_its_editor_share_one_box(self, home_html: str) -> None:
        """Committing a rename must not make the row jump.

        The label and the hidden editor beside it are meant to be the same
        box: same padding, same radius, same bleed. ``w-full`` on the
        editor broke that on the right — width:100% PLUS the -6px margins
        left it 12px narrower than the label (measured in Chromium: label
        11-373, editor 11-361) — so it is gone, and the two now differ only
        in the border colour that says one of them is editable.
        """
        row = render_to_string(
            "includes/_ugc_panel_row.html",
            {"label": "Verbier", "renameable": True, "rename_label": "Name"},
        )
        classes = [c for c in _class_strings(row) if "-mx-1.5" in c]
        assert len(classes) == 2, classes
        for shared in ("-mx-1.5", "px-1.5", "py-0.5", "rounded-pill", "text-label"):
            assert all(shared in c for c in classes), shared
        assert not any("w-full" in c for c in classes)

    @pytest.mark.parametrize("template_id", PANEL_TEMPLATE_IDS)
    def test_every_button_in_a_panel_says_it_is_clickable(
        self, home_html: str, template_id: str
    ) -> None:
        """One hover treatment for every control, on every panel.

        Hugo: "The affordances are inconsistent - for all interactive
        elements (roundels, 'x' closure, 'add' buttons) it should be
        consistent on hover - change the mouse pointer, and add infill."
        The panels had two of the four treatments in play — the close and
        the row trash filled with ``bg-chip-strong`` and gave no cursor at
        all (a native ``<button>`` does not take the pointer from the
        browser), the add CTA shifted opacity. Both now carry the shared
        ``hover-affordance`` class, which is also what the map's own
        roundels carry (tests/map/test_map_page.py).
        """
        body = _panel_body(home_html, template_id)
        buttons = re.findall(r"<button\b[^>]*>", body)
        assert buttons
        for button in buttons:
            assert "hover-affordance" in button, button

    def test_the_icon_control_tag_carries_the_affordance(self) -> None:
        """Both variants of the shared 44x44 icon control, at the source.

        The close, the pencil and the trash all come from
        ``icon_button_classes`` (apps/public/templatetags/components.py) —
        five templates in three apps — so the treatment is asserted on the
        tag rather than on each of them. The destructive variant keeps its
        own hover COLOUR: it is the one control that should name itself as
        destructive under the pointer about to press it.
        """
        neutral = icon_button_classes()
        destructive = icon_button_classes(variant="destructive")

        assert "hover-affordance" in neutral.split()
        assert "hover-affordance" in destructive.split()
        assert "hover:text-status-error-text" in destructive.split()

    def test_every_panel_title_is_the_same_size(self, home_html: str) -> None:
        """One title size across the three, not one per panel.

        This is the drift that started the ticket: downloads at
        ``text-lg font-semibold``, the other two at ``text-sm``. The size
        is fixed inside the shared partial rather than exposed as a
        parameter, so there is nothing for a caller to get wrong.
        """
        for template_id in PANEL_TEMPLATE_IDS:
            body = _panel_body(home_html, template_id)
            assert "text-lg font-semibold text-text-1" in body
            assert "text-sm font-semibold text-text-1" not in body


# SNOW-1481: Saved — the four sheets behind the star and the app's Saved tab.
SAVED_LIST_TEMPLATES = (
    "favourite-list-template",
    "route-list-template",
    "trips-sheet-template",
    "report-list-template",
)
SAVED_SHEET_IDS = ("favourite-sheet", "route-sheet", "trips-sheet", "report-sheet")


@pytest.fixture()
def signed_in_home_html(db: None) -> str:
    """Return the map page for a signed-in reader, who has the trips sheet.

    Args:
        db: pytest-django's database fixture.

    Returns:
        The decoded response body.

    """
    client = Client(SERVER_NAME="localhost")
    client.force_login(UserFactory.create())
    return client.get(reverse("public:map")).content.decode()


class TestSavedOnEveryScreen:
    """SNOW-1481: Saved is one panel on every screen, not the app's alone."""

    @pytest.mark.parametrize("template_id", SAVED_LIST_TEMPLATES)
    def test_the_tabs_and_plus_are_not_app_only(
        self, signed_in_home_html: str, template_id: str
    ) -> None:
        """The tabs and the header's + draw on a desktop and a phone browser."""
        opener = f'<template id="{template_id}">'
        start = signed_in_home_html.index(opener)
        body = signed_in_home_html[
            start : signed_in_home_html.index("</template>", start)
        ]
        assert "data-saved-tabs" in body
        assert "app-only" not in body

    @pytest.mark.parametrize("template_id", SAVED_LIST_TEMPLATES)
    def test_the_close_shows_on_every_screen(
        self, signed_in_home_html: str, template_id: str
    ) -> None:
        """SNOW-1486: nothing is app-hidden; the × closes Saved on a phone too."""
        body = _saved_body(signed_in_home_html, template_id)
        assert "app-hidden" not in body
        close = re.search(r'<button[^>]*data-action="dismiss"[^>]*>', body, re.S)
        assert close, template_id
        assert "sm:hidden" not in close.group(0)

    @pytest.mark.parametrize("sheet_id", SAVED_SHEET_IDS)
    def test_each_sheet_docks_left_and_is_the_phone_card(
        self, signed_in_home_html: str, sheet_id: str
    ) -> None:
        """Under search on a desktop; on a phone the card at half or full.

        SNOW-1486: half and full, opening at half, the card variant, and the
        handle rendered as the sheet's first child (phone-only).
        """
        match = re.search(
            rf'<div[^>]*\bid="{sheet_id}"[^>]*>', signed_in_home_html, re.S
        )
        assert match, sheet_id
        tag = match.group(0)
        assert 'data-sheet-side="left"' in tag
        assert 'data-detents="half,full"' in tag
        assert 'data-detent-initial="half"' in tag
        assert "data-sheet-card" in tag
        after = signed_in_home_html[match.end() : match.end() + 400]
        handle = re.search(r"<button[^>]*data-sheet-handle[^>]*>", after, re.S)
        assert handle, sheet_id
        assert "sm:hidden" in handle.group(0)

    @pytest.mark.parametrize("template_id", SAVED_LIST_TEMPLATES)
    def test_the_header_is_title_and_close_over_the_tabs_and_plus(
        self, signed_in_home_html: str, template_id: str
    ) -> None:
        """SNOW-1486: the phone grid "title close" / "lead actions .".

        From sm the grid is one row, "lead actions close", and the title is
        not drawn, so the desktop header is the row of tabs, + and × it was.
        The row is a drag surface for the sheet.
        """
        body = _saved_body(signed_in_home_html, template_id)
        grid = re.search(r"<div\s+data-sheet-drag\s+class=\"([^\"]*)\"", body)
        assert grid, template_id
        classes = grid.group(1)
        assert "[grid-template-areas:'title_title_close'_'lead_actions_.']" in classes
        assert "sm:[grid-template-areas:'lead_actions_close']" in classes
        title = re.search(
            r'<span\s+class="([^"]*\[grid-area:title\][^"]*)"\s*>\s*Saved items', body
        )
        assert title, template_id
        assert "sm:hidden" in title.group(1)
        lead = body.index("[grid-area:lead]")
        actions = body.index("[grid-area:actions]")
        close = body.index("[grid-area:close]")
        assert lead < body.index("data-saved-tabs") < actions < close
        # The + sits in the actions cell, right of the tabs, not after the ×.
        plus = re.search(
            r"saved-add-roundel|trip-route-picker-trigger|route-add-menu-trigger", body
        )
        assert plus and actions < plus.start() < close

    def test_the_saved_header_has_no_title_icon(self, signed_in_home_html: str) -> None:
        """The Saved grid draws no icon box; the other headers keep theirs."""
        body = _saved_body(signed_in_home_html, "favourite-list-template")
        assert "h-6.5 w-6.5" not in body


def _saved_body(html: str, template_id: str) -> str:
    """Return the raw body of a Saved ``<template>``, tabs and all.

    Args:
        html: The rendered map page.
        template_id: The template element's DOM id.

    Returns:
        Everything between the template's open and close tags.

    """
    opener = f'<template id="{template_id}">'
    start = html.index(opener)
    return html[start : html.index("</template>", start)]


class TestOnePhoneSheet:
    """SNOW-1486: New pin, the report form and Help on the phone card."""

    def test_new_pin_is_titled_and_has_no_cancel(self, home_html: str) -> None:
        """Title "New pin", a sr-only label, a full-width primary Save, no Cancel."""
        form = _saved_body(home_html, "favourite-create-template")
        assert "New pin" in form
        assert "Add favourite" not in form
        assert "Cancel" not in form
        assert re.search(
            r'<label class="sr-only" for="favourite-name-input">\s*Name \(optional\)',
            form,
        )
        assert (
            'placeholder="Name (optional)"' in form
            or "placeholder='Name (optional)'" in form
        )
        submit = re.search(r'<button\s+type="submit"\s+class="([^"]*)"', form)
        assert submit
        assert "w-full" in submit.group(1)
        assert 'data-action="dismiss"' in form  # the header's ×

    def test_tasks_mark_themselves_fit(self, home_html: str) -> None:
        """The New pin form and the saved confirmations fit their content."""
        form = _saved_body(home_html, "favourite-create-template")
        assert re.search(
            r'<form\s+id="favourite-create-form"[^>]*\bdata-sheet-fit\b', form, re.S
        )
        for template_id in (
            "favourite-confirmation-template",
            "route-confirmation-template",
        ):
            assert "data-sheet-fit" in _saved_body(home_html, template_id), template_id

    def test_the_help_menu_has_a_phone_header_with_a_handle(
        self, home_html: str
    ) -> None:
        """A sm:hidden header: handle, "Help" and a × the menu closes on.

        From sm the eyebrow and the corner × are still the menu's own, so a
        desktop draws it as before.
        """
        start = home_html.index('id="map-help-menu"')
        menu = home_html[start : home_html.index("map-tour-offer-options", start)]
        phone = re.search(
            r'<div class="sm:hidden" data-help-menu-drag>(.*?)</button>', menu, re.S
        )
        assert phone
        header = phone.group(1)
        handle = re.search(r"<div\s+data-sheet-handle[^>]*>", header, re.S)
        assert handle
        assert "sm:hidden" in handle.group(0)
        assert "Help" in header
        assert 'data-action="help-menu-close"' in menu
        assert 'class="map-tour-offer-close"' in menu
        assert 'class="map-tour-offer-eyebrow" id="map-help-menu-title"' in menu


class TestOverlaySheetHandle:
    """SNOW-1486: the card path renders the handle with or without detents."""

    def test_a_card_sheet_without_detents_draws_the_handle(self) -> None:
        sheet = render_to_string(
            "includes/_overlay_sheet.html",
            {"id": "s", "aria_label": "S", "card": True},
        )
        assert "data-sheet-card" in sheet
        assert "data-detents" not in sheet
        assert re.search(r"<button[^>]*data-sheet-handle[^>]*sm:hidden", sheet, re.S)

    def test_a_plain_sheet_draws_no_handle(self) -> None:
        """Desktop and every other sheet unchanged: no card, no handle."""
        sheet = render_to_string(
            "includes/_overlay_sheet.html", {"id": "s", "aria_label": "S"}
        )
        assert "data-sheet-card" not in sheet
        assert "data-sheet-handle" not in sheet

    def test_the_report_form_is_fit(self) -> None:
        """The report form, placing state and all, fits its content."""
        form = render_to_string(
            "observations/partials/_report_form.html",
            {"families": [], "submit_url": "/x/", "has_coords": True},
            request=RequestFactory().get("/"),
        )
        assert re.search(r'<form\s+id="report-form"[^>]*\bdata-sheet-fit\b', form, re.S)
        assert "data-report-placing" in form
        confirmation = render_to_string(
            "observations/partials/_report_confirmation.html", {}
        )
        assert "data-sheet-fit" in confirmation


class TestRowDisclosureSlot:
    """The row's optional trailing disclosure (SNOW-711).

    The account page's favourite row expands its own detail card
    underneath itself, which no map panel does — a pin's page is reached
    by tapping the pin. That makes the disclosure a slot rather than a
    fixture of the row, and these tests pin the two things a slot has to
    get right: it renders where the design puts it, and a row that has
    none renders nothing at all in its place.
    """

    def test_a_row_without_one_renders_no_element(self) -> None:
        """No disclosure passed, no element — not an empty one.

        An empty flex child still takes the row's ``gap-2``, so the three
        map panels' rows would each grow 8px of trailing whitespace for a
        control they do not have.
        """
        row = render_to_string(
            "includes/_ugc_panel_row.html",
            {
                "label": "Verbier",
                "actions_template": "includes/_map_downloads_row_actions.html",
            },
        )

        assert "data-row-disclosure" not in row

    def test_the_disclosure_follows_the_action_cluster(self) -> None:
        """It sits after the trash, and outside the actions span.

        "Trash always last" is a rule about the ACTION cluster, and a
        disclosure is not an action — it reveals what the row already is
        rather than changing it. So the rule still holds inside the
        cluster, and the disclosure sits past it at the row's trailing
        edge, where a list-row disclosure belongs.
        """
        row = render_to_string(
            "includes/_ugc_panel_row.html",
            {
                "label": "Verbier",
                "actions_template": "includes/_map_downloads_row_actions.html",
                "disclosure_template": "includes/_row_disclosure.html",
                "disclosure_href": "/favourites/x/",
                "disclosure_panel_id": "panel-x",
                "disclosure_label": "Show details for Verbier",
            },
        )

        trash = row.index("M4 7h16")  # the bin's lid — includes/_icon_trash.html
        disclosure = row.index("data-row-disclosure")
        assert trash < disclosure
        # Outside the actions span, not the last child inside it: the span
        # closes before the disclosure opens.
        assert "</span>" in row[trash:disclosure]

    def test_the_control_is_a_link_before_it_is_a_disclosure(self) -> None:
        """href first, hx-get second — it works with no JavaScript.

        The row it replaced carried a "Details →" link to the same page,
        and losing the plain navigation would be a real regression for a
        control that is now a glyph.
        """
        control = render_to_string(
            "includes/_row_disclosure.html",
            {
                "disclosure_href": "/favourites/abc/",
                "disclosure_hx_get": "/favourites/partials/abc/card/",
                "disclosure_panel_id": "favourite-panel-abc",
                "disclosure_label": "Show details for Verbier",
            },
        )

        assert 'href="/favourites/abc/"' in control
        assert 'hx-get="/favourites/partials/abc/card/"' in control
        assert 'hx-target="#favourite-panel-abc"' in control
        # Closed at rest, and naming the panel it opens.
        assert 'aria-expanded="false"' in control
        assert 'aria-controls="favourite-panel-abc"' in control
        # The same 44x44 target as the pencil and the trash beside it.
        assert "hover-affordance" in control
