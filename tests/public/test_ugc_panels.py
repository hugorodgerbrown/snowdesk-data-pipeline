"""
tests/public/test_ugc_panels.py — the three UGC map panels share one skeleton.

SNOW-658. Downloads, favourites and field observations answer the same
question on the same map behind the same kind of roundel — "what have I
got here, and how do I add to it or get rid of one?" — and until this
ticket they shared exactly one element (the "Show X on the map" switch)
and diverged at every other point of contact: three sheet-scroll models,
two title sizes, three body wrappers, three row shapes. Nothing enforced
the sameness, so each panel drifted the moment someone edited it.

That is what these tests exist to stop. They do NOT check that the panels
look nice; they check that the chrome around each one's own content came
out of ``includes/_ugc_panel.html``, so a hand-edit to one of the three
fails here instead of silently going unnoticed until all three are
side by side again.

The skeleton is derived, not transcribed: it is every class string the
shared partial renders minus every class string its rows slot renders, so
a legitimate change to the partial updates the expectation automatically
and a change to ONE panel does not.
"""

from __future__ import annotations

import re

import pytest
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

# The three panel bodies, by the <template> id each surface renders them in.
PANEL_TEMPLATE_IDS = (
    "map-downloads-body-template",
    "favourite-list-template",
    "report-list-template",
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
            "rows_template": DEMO_ROWS_TEMPLATE,
            "cta_label": "Add one",
            "toggle_id": "skeleton-toggle",
            "toggle_label": "Show them on the map",
        },
    )
    rows = render_to_string(DEMO_ROWS_TEMPLATE, {})
    return _class_strings(panel) - _class_strings(rows)


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
    def test_panel_runs_header_list_cta_switch_in_that_order(
        self, home_html: str, template_id: str
    ) -> None:
        """The four parts appear in the reading order the design sets.

        Hugo's own instruction on the downloads sheet (SNOW-645 review) put
        the overlay switch LAST, so the map behind the sheet is the last
        thing read rather than the first — and the CTA above it, below the
        list it adds to. Class-string containment alone would not catch a
        panel that shipped those in a different order.
        """
        body = _panel_body(home_html, template_id)
        header = body.index("text-lg font-semibold")
        scroll = body.index("overflow-y-auto")
        cta = body.index("data-panel-add")
        switch = body.index("rounded-tag bg-tag")
        assert header < scroll < cta < switch

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
