"""
tests/public/test_ugc_panels.py — the four UGC map panels share one skeleton.

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
from django.test import Client
from django.urls import reverse

from apps.public.templatetags.components import icon_button_classes

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
    "map-downloads-body-template": (
        # SNOW-749: the areas follow the account now, so the old
        # "Downloads and budget stay on this device." was half false. The
        # half that survived is the load-bearing one — it is what explains
        # how a row can be listed in this panel and still not be available
        # offline here.
        "Your areas follow your account. The map data and the budget stay "
        "on this device."
    ),
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

# The mono uppercase section label heading each panel's list. Downloads has
# two, because it groups its rows by kind.
PANEL_SECTION_LABELS = {
    "map-downloads-body-template": ("Regions", "Custom areas"),
    "favourite-list-template": ("Places",),
    "report-list-template": ("Reports",),
    "route-list-template": ("Tracks",),
}

# The overlay switch's label — ONE sentence for all three panels, fixed
# inside the shared partial. These three are what it replaced.
OVERLAY_TOGGLE_LABEL = "Display on the map"
SUPERSEDED_TOGGLE_LABELS = (
    "Show areas on the map",
    "Show favourites on the map",
    "Show community reports on the map",
)

# A rows slot that renders something, so the panel's scroll region is not
# empty while the skeleton is being derived. Its own class strings are
# subtracted back out below.
DEMO_ROWS_TEMPLATE = "public/partials/_ugc_panel_demo_rows.html"

_CLASS_RE = re.compile(r"""class=(?:"([^"]*)"|'([^']*)')""")


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
    return {double or single for double, single in _CLASS_RE.findall(html)}


def _panel_body(html: str, template_id: str) -> str:
    """Return the contents of the ``<template id=…>`` element in ``html``.

    Args:
        html: The rendered home page.
        template_id: The template element's DOM id.

    Returns:
        Everything between that element's open and close tags.

    """
    opener = f'<template id="{template_id}">'
    start = html.index(opener) + len(opener)
    return html[start : html.index("</template>", start)]


@pytest.fixture(scope="module")
def skeleton_classes() -> set[str]:
    """Return every class string ``includes/_ugc_panel.html`` itself renders.

    Derived by rendering the shared partial and subtracting the class
    strings contributed by the rows template handed to it — what is left is
    the chrome: the outer flex column, the sheet header, the scroll region,
    the CTA and the overlay-toggle panel.

    Returns:
        The skeleton's class-attribute values.

    """
    panel = render_to_string(
        "includes/_ugc_panel.html",
        {
            "title": "Title",
            "icon_template": "includes/_icon_favourite.html",
            "context_line": "Where this panel's data lives.",
            "section_label": "Things",
            "rows_template": DEMO_ROWS_TEMPLATE,
            "cta_label": "Add one",
            "toggle_id": "skeleton-toggle",
        },
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
    return client.get(reverse("public:home")).content.decode()


class TestUgcPanelSkeleton:
    """All three panels' chrome comes from the one shared partial."""

    @pytest.mark.parametrize("template_id", PANEL_TEMPLATE_IDS)
    def test_panel_carries_the_whole_shared_skeleton(
        self, home_html: str, skeleton_classes: set[str], template_id: str
    ) -> None:
        """Every class string the shared partial renders is in this panel.

        A panel that stops including the partial — or that reproduces most
        of it by hand and gets one wrapper subtly different — fails here.
        """
        body = _panel_body(home_html, template_id)
        present = _class_strings(body)
        assert skeleton_classes <= present, skeleton_classes - present

    @pytest.mark.parametrize("template_id", PANEL_TEMPLATE_IDS)
    def test_panel_runs_the_five_parts_in_that_order(
        self, home_html: str, template_id: str
    ) -> None:
        """The five shell parts appear in the reading order the design sets.

        Header, context strip, list, add CTA, map toggle — and the toggle
        LAST is Hugo's own instruction from the downloads sheet (SNOW-645
        review), so the map behind the sheet is the last thing read rather
        than the first. Class-string containment alone would not catch a
        panel that shipped these in a different order.
        """
        body = _panel_body(home_html, template_id)
        header = body.index("text-lg font-semibold")
        strip = body.index(PANEL_CONTEXT_LINES[template_id])
        scroll = body.index("overflow-y-auto")
        cta = body.index("data-panel-add")
        switch = body.index("rounded-tag bg-tag")
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
        assert PANEL_CONTEXT_LINES[template_id] in body

    @pytest.mark.parametrize("template_id", PANEL_TEMPLATE_IDS)
    def test_panel_heads_its_list_with_a_mono_section_label(
        self,
        home_html: str,
        section_label_classes: str,
        template_id: str,
    ) -> None:
        """Every list sits under the shared mono uppercase label.

        Downloads has two (it groups its rows by kind) and renders them
        from inside its own rows template; the other two take the shared
        partial's ``section_label``. Either way the label comes from
        includes/_eyebrow.html, so all four are one shape.
        """
        body = _panel_body(home_html, template_id)
        labels = PANEL_SECTION_LABELS[template_id]
        assert body.count(section_label_classes) == len(labels)
        for label in labels:
            assert f">{label}<" in body

    @pytest.mark.parametrize("template_id", PANEL_TEMPLATE_IDS)
    def test_panel_labels_its_switch_the_same_way_as_the_others(
        self, home_html: str, template_id: str
    ) -> None:
        """One sentence for one control, on all three panels.

        "Show areas on the map" / "Show favourites on the map" / "Show
        community reports on the map" were three sentences for the same
        switch — the divergence this partial exists to end, in copy rather
        than markup. The string is fixed inside the shared partial, so
        there is nothing for a caller to restate.
        """
        body = _panel_body(home_html, template_id)
        assert body.count(OVERLAY_TOGGLE_LABEL) == 1
        for superseded in SUPERSEDED_TOGGLE_LABELS:
            assert superseded not in home_html

    @pytest.mark.parametrize("template_id", PANEL_TEMPLATE_IDS)
    def test_panel_has_exactly_one_add_cta(
        self, home_html: str, template_id: str
    ) -> None:
        """One shared hook, one control — not three differently-named ones.

        ``data-panel-add`` replaced ``data-downloads-add`` /
        ``data-favourites-add`` / ``data-report-add``: three names for one
        control is the same divergence in attribute form. Each owning
        module delegates on its OWN sheet, so one name is unambiguous.
        """
        body = _panel_body(home_html, template_id)
        assert body.count("data-panel-add") == 1

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
        roundels carry (tests/public/test_map_page.py).
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
